import asyncio
import json
import logging
from typing import Optional
from datetime import datetime
from uuid import uuid4

from config import redis_client, MAX_MISTRAL_CONCURRENT
from models import TaskItem
from services.session_manager import get_session_manager
from services.invoice_parser import InvoiceParser
from services.file_handler import FileHandler
from services.xl_output_generator import XLOutputGenerator

logger = logging.getLogger("task_queue")


class TaskQueueManager:
    """
    Manages a Redis-backed task queue with round-robin batch scheduling.

    Ensures fair processing across multiple users by cycling through batches
    in round-robin fashion, preventing any single user from monopolizing workers.
    """

    def __init__(self, redis_client):
        if redis_client is None:
            raise ValueError("Redis client is required for task queue. Set REDIS_ENABLED=true")

        self.redis = redis_client
        self.session_manager = get_session_manager()
        self.invoice_parser = InvoiceParser()
        self.file_handler = FileHandler()

        # Redis key patterns
        self.PENDING_QUEUE_PREFIX = "queue:pending:"
        self.ACTIVE_BATCHES_KEY = "queue:active_batches"
        self.ROUND_ROBIN_INDEX_KEY = "queue:round_robin_index"
        self.PROCESSING_PREFIX = "queue:processing:"

        logger.info("TaskQueueManager initialized")

    async def enqueue_file(self, batch_id: str, filename: str, pdf_path: str):
        """
        Add a file to the task queue for processing.

        Args:
            batch_id: Batch identifier
            filename: Name of the PDF file
            pdf_path: Full path to the PDF file
        """
        # Create task item
        task = TaskItem(
            task_id=str(uuid4()),
            batch_id=batch_id,
            filename=filename,
            pdf_path=pdf_path,
            enqueued_at=datetime.utcnow().isoformat()
        )

        # Serialize task to JSON
        task_json = task.model_dump_json()

        # Add to batch-specific queue
        queue_key = f"{self.PENDING_QUEUE_PREFIX}{batch_id}"
        await self.redis.rpush(queue_key, task_json)

        # Add batch to active batches set
        await self.redis.sadd(self.ACTIVE_BATCHES_KEY, batch_id)

        logger.info(f"Enqueued task {task.task_id}: {filename} (batch: {batch_id})")

    async def get_next_task_round_robin(self) -> Optional[TaskItem]:
        """
        Pull the next task using round-robin scheduling across batches.

        Returns:
            TaskItem if available, None if queue is empty
        """
        # Get all active batches
        active_batches = await self.redis.smembers(self.ACTIVE_BATCHES_KEY)

        if not active_batches or len(active_batches) == 0:
            return None  # No tasks available

        # Convert to sorted list for consistent ordering
        batches = sorted(list(active_batches))

        # Get current round-robin index
        index_str = await self.redis.get(self.ROUND_ROBIN_INDEX_KEY)
        index = int(index_str) if index_str else 0

        # Select batch using modulo for wrap-around
        selected_batch = batches[index % len(batches)]

        # Pop task from selected batch's queue (left pop for FIFO)
        queue_key = f"{self.PENDING_QUEUE_PREFIX}{selected_batch}"
        task_json = await self.redis.lpop(queue_key)

        if task_json:
            # Parse task
            task_dict = json.loads(task_json)
            task = TaskItem(**task_dict)

            # Mark as processing (with 1-hour expiry for safety)
            processing_key = f"{self.PROCESSING_PREFIX}{task.task_id}"
            await self.redis.setex(processing_key, 3600, task_json)

            # Increment round-robin index
            await self.redis.incr(self.ROUND_ROBIN_INDEX_KEY)

            logger.debug(f"Pulled task {task.task_id} from batch {selected_batch} (index: {index})")
            return task
        else:
            # Batch queue empty, remove from active set
            await self.redis.srem(self.ACTIVE_BATCHES_KEY, selected_batch)
            logger.debug(f"Batch {selected_batch} exhausted, removed from active batches")

            # Retry with remaining batches (recursive)
            return await self.get_next_task_round_robin()

    async def worker_loop(self, worker_id: int):
        """
        Worker that continuously pulls and processes tasks.

        Args:
            worker_id: Unique identifier for this worker
        """
        logger.info(f"Worker {worker_id} started")

        while True:
            try:
                # Get next task (round-robin)
                task = await self.get_next_task_round_robin()

                if task is None:
                    # No tasks available, wait briefly
                    await asyncio.sleep(1)
                    continue

                logger.info(
                    f"Worker {worker_id} processing {task.filename} "
                    f"(batch: {task.batch_id}, task: {task.task_id})"
                )

                # Update file status to "processing"
                self.session_manager.update_file_status(
                    task.batch_id,
                    task.filename,
                    "processing"
                )

                # Process invoice (already has semaphore inside InvoiceParser)
                success, invoice_data, error = await self.invoice_parser.parse_invoice(task.pdf_path)

                if not success:
                    logger.error(f"Worker {worker_id} failed to parse {task.filename}: {error}")
                    self.session_manager.update_file_status(
                        task.batch_id,
                        task.filename,
                        "failed",
                        error=error
                    )
                    # Remove from processing set
                    await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")
                    continue

                # Validate invoice data
                is_valid, validation_error = self.invoice_parser.validate_invoice_data(invoice_data)
                if not is_valid:
                    logger.error(f"Worker {worker_id} validation failed for {task.filename}: {validation_error}")
                    self.session_manager.update_file_status(
                        task.batch_id,
                        task.filename,
                        "failed",
                        error=validation_error
                    )
                    # Remove from processing set
                    await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")
                    continue

                # Generate XL Output
                voucher_number = self.session_manager.get_next_voucher_number(task.batch_id)
                xl_output_row = XLOutputGenerator.generate_xl_output_row(
                    invoice_data,
                    voucher_number
                )
                logger.info(f"Worker {worker_id} generated XL output for {task.filename} with voucher #{voucher_number}")

                # Format and save result
                json_path = self.file_handler.get_json_path(task.batch_id, task.filename)
                result = self.invoice_parser.format_invoice_response(
                    task.filename,
                    task.pdf_path,
                    json_path,
                    invoice_data,
                    xl_output=[xl_output_row.model_dump()]
                )

                # Save JSON result
                success, saved_json_path = self.file_handler.save_json_result(
                    task.batch_id,
                    task.filename,
                    result
                )

                # Update file status to completed
                self.session_manager.update_file_status(
                    task.batch_id,
                    task.filename,
                    "completed"
                )

                # Update session with results
                session = self.session_manager.get_session(task.batch_id)
                if session:
                    # Get processed_results - could be list or JSON string depending on storage backend
                    processed_results_raw = session.get("processed_results", [])

                    # Parse if it's a JSON string (Redis case)
                    if isinstance(processed_results_raw, str):
                        try:
                            processed_results = json.loads(processed_results_raw)
                        except (json.JSONDecodeError, TypeError):
                            processed_results = []
                    else:
                        processed_results = processed_results_raw if isinstance(processed_results_raw, list) else []

                    # Append result and update session
                    processed_results.append(result)
                    self.session_manager.update_session(task.batch_id, {"processed_results": processed_results})

                # Check if all files in batch are processed
                session = self.session_manager.get_session(task.batch_id)
                if session:
                    # Get files - could be list or JSON string depending on storage backend
                    files_raw = session.get("files", [])

                    # Parse if it's a JSON string (Redis case)
                    if isinstance(files_raw, str):
                        try:
                            files = json.loads(files_raw)
                        except (json.JSONDecodeError, TypeError):
                            files = []
                    else:
                        files = files_raw if isinstance(files_raw, list) else []

                    all_processed = all(f["status"] in ["completed", "failed"] for f in files)
                    if all_processed:
                        logger.info(f"Batch {task.batch_id} processing completed")
                        self.session_manager.update_session(task.batch_id, {
                            "status": "completed",
                            "completed_at": datetime.utcnow().isoformat()
                        })

                # Remove from processing set
                await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")

                logger.info(f"Worker {worker_id} completed {task.filename} (batch: {task.batch_id})")

            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}", exc_info=True)
                # Try to mark task as failed if we have task context
                if 'task' in locals() and task:
                    try:
                        self.session_manager.update_file_status(
                            task.batch_id,
                            task.filename,
                            "failed",
                            error=str(e)
                        )
                        await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")
                    except Exception as cleanup_error:
                        logger.error(f"Worker {worker_id} failed to cleanup after error: {cleanup_error}")

    async def start_workers(self, num_workers: int):
        """
        Start N worker coroutines.

        Args:
            num_workers: Number of workers to start
        """
        logger.info(f"Starting {num_workers} workers")

        for worker_id in range(1, num_workers + 1):
            asyncio.create_task(self.worker_loop(worker_id))

        logger.info(f"All {num_workers} workers started")

    async def recover_crashed_tasks(self):
        """
        Mark any tasks that were being processed when server crashed as failed.

        This is called on startup to clean up orphaned tasks.
        """
        logger.info("Recovering crashed tasks...")

        # Get all processing task keys
        processing_pattern = f"{self.PROCESSING_PREFIX}*"
        cursor = 0
        processing_keys = []

        # Use SCAN to avoid blocking Redis (better than KEYS *)
        while True:
            cursor, keys = await self.redis.scan(cursor, match=processing_pattern, count=100)
            processing_keys.extend(keys)
            if cursor == 0:
                break

        if not processing_keys:
            logger.info("No crashed tasks found")
            return

        recovered_count = 0
        for key in processing_keys:
            try:
                task_json = await self.redis.get(key)
                if task_json:
                    task_dict = json.loads(task_json)
                    task = TaskItem(**task_dict)

                    # Mark as failed
                    self.session_manager.update_file_status(
                        task.batch_id,
                        task.filename,
                        "failed",
                        error="Server restarted during processing"
                    )

                    # Clean up processing key
                    await self.redis.delete(key)
                    recovered_count += 1

                    logger.info(f"Recovered crashed task: {task.filename} (batch: {task.batch_id})")
            except Exception as e:
                logger.error(f"Error recovering task from key {key}: {e}")

        logger.info(f"Recovered {recovered_count} crashed tasks")


# Singleton instance
_task_queue_manager = None


def get_task_queue_manager() -> TaskQueueManager:
    """
    Get or create the singleton TaskQueueManager instance.

    Returns:
        TaskQueueManager instance
    """
    global _task_queue_manager

    if _task_queue_manager is None:
        _task_queue_manager = TaskQueueManager(redis_client)

    return _task_queue_manager
