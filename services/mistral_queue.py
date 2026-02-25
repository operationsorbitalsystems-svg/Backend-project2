"""
Mistral Task Queue Manager

Dedicated task queue for Mistral OCR processing with round-robin scheduling.
Processes invoice PDFs through Mistral OCR API and saves results as JSON files.
"""

import asyncio
import json
import uuid
from datetime import datetime, UTC
from typing import Optional
import aiofiles
from redis import Redis
from .invoice_parser import InvoiceParser
from models import MistralRequest, MistralResponse
from utils.logger import setup_logger, batch_id_var
from config import langfuse_client

logger = setup_logger()


class MistralQueueManager:
    """
    Manages Mistral OCR task queue with round-robin scheduling across batches.

    Architecture:
    - Round-robin scheduling ensures fair processing across multiple batches
    - File-based result storage (JSON) reduces Redis memory pressure
    - Crashed task recovery on startup
    - Graceful worker shutdown
    """

    PENDING_PREFIX = "mistral_queue:pending:"
    PROCESSING_PREFIX = "mistral_queue:processing:"
    RESPONSE_PREFIX = "mistral_queue:response:"
    ACTIVE_BATCHES_KEY = "mistral_queue:active_batches"
    ROUND_ROBIN_INDEX_KEY = "mistral_queue:round_robin_index"

    def __init__(self, redis_client: Redis, invoice_parser: InvoiceParser, file_handler):
        """
        Initialize Mistral Queue Manager.

        Args:
            redis_client: Redis async client
            invoice_parser: InvoiceParser instance for OCR processing
            file_handler: FileHandler instance for saving JSON files
        """
        self.redis = redis_client
        self.invoice_parser = invoice_parser
        self.file_handler = file_handler
        self.running = False
        self.workers = []

    async def enqueue_request(self, batch_id: str, filename: str, pdf_path: str, task_id: str) -> str:
        """
        Enqueue a new Mistral OCR request.

        Args:
            batch_id: Batch identifier
            filename: PDF filename
            pdf_path: Full path to PDF file

        Returns:
            task_id: Unique task identifier
        """

        request = MistralRequest(
            task_id=task_id,
            batch_id=batch_id,
            filename=filename,
            pdf_path=pdf_path,
            enqueued_at=datetime.now(UTC).isoformat()
        )

        # Add to batch-specific pending queue
        await self.redis.rpush(
            f"{self.PENDING_PREFIX}{batch_id}",
            request.model_dump_json()
        )

        # Mark batch as active
        await self.redis.sadd(self.ACTIVE_BATCHES_KEY, batch_id)

        logger.info(f"Mistral queue: Enqueued task {task_id} for {filename} (batch: {batch_id})")

        return task_id

    async def get_next_task_round_robin(self) -> Optional[MistralRequest]:
        """
        Get next task using round-robin scheduling across batches.

        Returns:
            MistralRequest object or None if no tasks available
        """
        # Get all active batches
        active_batches_bytes = await self.redis.smembers(self.ACTIVE_BATCHES_KEY)

        if not active_batches_bytes:
            return None

        # Sort batches for consistent ordering
        active_batches = sorted(active_batches_bytes)

        if not active_batches:
            return None

        # Get current round-robin index
        rr_index_str = await self.redis.get(self.ROUND_ROBIN_INDEX_KEY)
        rr_index = int(rr_index_str) if rr_index_str else 0

        # Try each batch in round-robin order
        for _ in range(len(active_batches)):
            batch_id = active_batches[rr_index % len(active_batches)]

            # Try to pop a task from this batch
            task_json = await self.redis.lpop(f"{self.PENDING_PREFIX}{batch_id}")

            if task_json:
                # Parse task
                task_dict = json.loads(task_json)
                task = MistralRequest(**task_dict)

                # Save to processing set with TTL (1 hour)
                await self.redis.setex(
                    f"{self.PROCESSING_PREFIX}{task.task_id}",
                    3600,  # 1 hour
                    task.model_dump_json()
                )

                # Update round-robin index
                await self.redis.set(self.ROUND_ROBIN_INDEX_KEY, (rr_index + 1) % len(active_batches))

                return task
            else:
                # This batch is empty, remove from active set
                await self.redis.srem(self.ACTIVE_BATCHES_KEY, batch_id)

            # Move to next batch
            rr_index += 1

        return None

    async def worker_loop(self, worker_id: int):
        """
        Main worker loop - processes Mistral OCR tasks continuously.

        Workflow:
        1. Get next task (round-robin)
        2. Call Mistral OCR API
        3. Save InvoiceData to JSON file
        4. Store lightweight response in Redis
        5. Remove from processing set

        Args:
            worker_id: Unique worker identifier
        """
        logger.info(f"Mistral worker {worker_id} started")

        while self.running:
            _ctx_token = None
            try:
                # Get next task
                task = await self.get_next_task_round_robin()

                if task is None:
                    # No tasks available, wait briefly
                    await asyncio.sleep(1)
                    continue

                _ctx_token = batch_id_var.set(task.batch_id)

                logger.info(
                    f"Mistral worker {worker_id} processing {task.filename} "
                    f"(batch: {task.batch_id}, task: {task.task_id})"
                )

                # === STEP 1: Call Mistral OCR API ===
                _trace = langfuse_client.trace(id=task.task_id, session_id=task.batch_id) if langfuse_client else None
                _ocr_gen = _trace.generation(
                    name="mistral-ocr",
                    model="mistral-ocr-latest",
                    input={"filename": task.filename},
                ) if _trace else None

                success, invoice_data, error, num_of_pages = await self.invoice_parser.parse_invoice(task.pdf_path)

                if _ocr_gen:
                    _ocr_gen.end(
                        output=invoice_data.model_dump() if invoice_data else None,
                        level="ERROR" if not success else "DEFAULT",
                        metadata={"page_count": num_of_pages, "error": error},
                    )

                if not success or not invoice_data:
                    # Mistral OCR failed
                    logger.error(f"Mistral worker {worker_id} OCR failed for {task.filename}: {error}")

                    response = MistralResponse(
                        task_id=task.task_id,
                        batch_id=task.batch_id,
                        success=False,
                        json_path=None,
                        error_message=error or "OCR failed",
                        processed_at=datetime.now(UTC).isoformat()
                    )

                    # Store failed response in Redis
                    await self.redis.setex(
                        f"{self.RESPONSE_PREFIX}{task.task_id}",
                        3600,  # 1 hour TTL
                        response.model_dump_json()
                    )

                    # Remove from processing set
                    await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")
                    continue

                # === STEP 2: Validate invoice data ===
                is_valid, validation_error = self.invoice_parser.validate_invoice_data(invoice_data)
                if not is_valid:
                    logger.error(f"Mistral worker {worker_id} validation failed for {task.filename}: {validation_error}")

                    response = MistralResponse(
                        task_id=task.task_id,
                        batch_id=task.batch_id,
                        success=False,
                        json_path=None,
                        error_message=validation_error,
                        processed_at=datetime.now(UTC).isoformat()
                    )

                    await self.redis.setex(
                        f"{self.RESPONSE_PREFIX}{task.task_id}",
                        3600,
                        response.model_dump_json()
                    )

                    await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")
                    continue

                # === STEP 3: Save InvoiceData to JSON file ===
                json_path = task.pdf_path.replace('.pdf', '.json')

                try:
                    async with aiofiles.open(json_path, 'w') as f:
                        await f.write(invoice_data.model_dump_json(indent=2))

                    logger.info(f"Mistral worker {worker_id} saved OCR result to {json_path}")
                except Exception as e:
                    logger.error(f"Mistral worker {worker_id} failed to save JSON for {task.filename}: {e}")

                    response = MistralResponse(
                        task_id=task.task_id,
                        batch_id=task.batch_id,
                        success=False,
                        json_path=None,
                        error_message=f"Failed to save JSON: {e}",
                        processed_at=datetime.now(UTC).isoformat()
                    )

                    await self.redis.setex(
                        f"{self.RESPONSE_PREFIX}{task.task_id}",
                        3600,
                        response.model_dump_json()
                    )

                    await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")
                    continue

                # === STEP 4: Store lightweight response in Redis ===
                response = MistralResponse(
                    task_id=task.task_id,
                    batch_id=task.batch_id,
                    success=True,
                    json_path=json_path,
                    error_message=None,
                    processed_at=datetime.now(UTC).isoformat()
                )

                await self.redis.setex(
                    f"{self.RESPONSE_PREFIX}{task.task_id}",
                    3600,  # 1 hour TTL
                    response.model_dump_json()
                )

                # Remove from processing set
                await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")

                logger.info(f"✅ Mistral worker {worker_id} completed {task.filename}")

            except Exception as e:
                import traceback
                logger.error(f"Mistral worker {worker_id} error: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")

                # Store error response if we have task info
                if task:
                    response = MistralResponse(
                        task_id=task.task_id,
                        batch_id=task.batch_id,
                        success=False,
                        json_path=None,
                        error_message=str(e),
                        processed_at=datetime.now(UTC).isoformat()
                    )

                    await self.redis.setex(
                        f"{self.RESPONSE_PREFIX}{task.task_id}",
                        3600,
                        response.model_dump_json()
                    )

                    await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")

                await asyncio.sleep(1)

            finally:
                if _ctx_token is not None:
                    batch_id_var.reset(_ctx_token)

    async def wait_for_response(self, task_id: str, timeout: int = 120) -> MistralResponse:
        """
        Wait for Mistral OCR response with polling.

        Args:
            task_id: Task identifier
            timeout: Timeout in seconds (default: 120s / 2 minutes)

        Returns:
            MistralResponse object

        Raises:
            TimeoutError: If response not received within timeout
        """
        elapsed = 0
        poll_interval = 0.5  # 500ms

        while elapsed < timeout:
            response_json = await self.redis.get(f"{self.RESPONSE_PREFIX}{task_id}")

            if response_json:
                response_dict = json.loads(response_json)
                return MistralResponse(**response_dict)

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise TimeoutError(f"Mistral OCR timeout after {timeout}s for task {task_id}")

    async def start_workers(self, num_workers: int = 5):
        """
        Start Mistral worker pool.

        Args:
            num_workers: Number of workers to start (default: 5, matches MAX_MISTRAL_CONCURRENT)
        """
        self.running = True

        for i in range(num_workers):
            worker_task = asyncio.create_task(self.worker_loop(i))
            self.workers.append(worker_task)

        logger.info(f"Started {num_workers} Mistral queue workers")

    async def stop_workers(self):
        """
        Gracefully stop all Mistral workers.
        """
        logger.info("Stopping Mistral queue workers...")
        self.running = False

        # Wait for workers to finish current tasks
        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)

        logger.info("Mistral queue workers stopped")

    async def recover_crashed_tasks(self):
        """
        Recover tasks that were processing when server crashed.

        Marks them as failed in Redis and cleans up processing keys.
        Called on startup.
        """
        logger.info("Recovering crashed Mistral tasks...")

        recovered_count = 0
        cursor = 0

        while True:
            # Scan for processing keys (non-blocking)
            cursor, keys = await self.redis.scan(
                cursor=cursor,
                match=f"{self.PROCESSING_PREFIX}*",
                count=100
            )

            for key in keys:
                try:
                    task_json = await self.redis.get(key)
                    if task_json:
                        task_dict = json.loads(task_json)
                        task = MistralRequest(**task_dict)

                        # Mark as failed due to crash
                        response = MistralResponse(
                            task_id=task.task_id,
                            batch_id=task.batch_id,
                            success=False,
                            json_path=None,
                            error_message="Server restarted during processing",
                            processed_at=datetime.now(UTC).isoformat()
                        )

                        await self.redis.setex(
                            f"{self.RESPONSE_PREFIX}{task.task_id}",
                            3600,
                            response.model_dump_json()
                        )

                        # Delete processing key
                        await self.redis.delete(key)

                        recovered_count += 1
                        logger.info(f"Recovered crashed Mistral task: {task.filename}")

                except Exception as e:
                    logger.error(f"Error recovering Mistral task from {key}: {e}")

            if cursor == 0:
                break

        logger.info(f"Recovered {recovered_count} crashed Mistral tasks")
