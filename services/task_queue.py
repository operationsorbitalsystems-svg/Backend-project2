import asyncio
import json
import logging
from typing import Optional, List, Tuple, Dict
from datetime import datetime
from uuid import uuid4

from config import redis_client, MAX_MISTRAL_CONCURRENT
from models import TaskItem, ProcessedInvoiceResult, InvoiceData, XLOutputRow
from services.session_manager import get_session_manager
from services.invoice_parser import InvoiceParser
from services.file_handler import FileHandler
from services.xl_output_generator import XLOutputGenerator
from services.ollama_queue import get_ollama_queue_manager
from utils.logger import setup_logger

logger = setup_logger()


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
        self.ollama_queue = get_ollama_queue_manager()

        # Redis key patterns
        self.PENDING_QUEUE_PREFIX = "queue:pending:"
        self.ACTIVE_BATCHES_KEY = "queue:active_batches"
        self.ROUND_ROBIN_INDEX_KEY = "queue:round_robin_index"
        self.PROCESSING_PREFIX = "queue:processing:"
        self.VOUCHER_COUNTER_PREFIX = "voucher_counter:"

        # COA cache per batch
        self.expense_ledgers_cache = {}  # {batch_id: List[str]}
        self.liability_ledgers_cache = {}  # {batch_id: List[str]}

        logger.info("TaskQueueManager initialized")

    async def enqueue_file(self, batch_id: str, filename: str, pdf_path: str, vendor_name:str= ""):
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
            vendor_name=vendor_name,
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
        Main invoice processing workflow.
        Mistral OCR → Ollama ledger selections → Multi-row XL generation → Save

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

                # === STEP 1: Mistral OCR ===
                success, invoice_data, error = await self.invoice_parser.parse_invoice(task.pdf_path)

                if not success or not invoice_data:
                    logger.error(f"Worker {worker_id} Mistral OCR failed for {task.filename}: {error}")
                    self.session_manager.update_file_status(
                        task.batch_id,
                        task.filename,
                        "failed",
                        error=f"Mistral OCR failed: {error}"
                    )
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
                    await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")
                    continue

                logger.info(f"Worker {worker_id} Mistral OCR completed for {task.filename}")

                # === STEP 2a: Ollama - Expense Ledger Selection ===
                expense_ledgers = await self._load_expense_ledgers_from_coa(task.batch_id)
                narration = XLOutputGenerator.concatenate_line_items(invoice_data.line_items)

                expense_system_prompt = """You are an accounting assistant. Select the most appropriate expense ledger from the Chart of Accounts (COA) based on the invoice line items.

Rules:
- Return ONLY the exact ledger name from the provided list
- If unsure, return "Suspended AC"
- Do not add explanations or extra text"""

                expense_user_prompt = f"""Select the best expense ledger for this invoice:

Invoice line items: {narration}

Available expense ledgers:
{chr(10).join(expense_ledgers)}

Return only the ledger name."""

                expense_task_id = await self.ollama_queue.enqueue_request(
                    batch_id=task.batch_id,
                    system_prompt=expense_system_prompt,
                    user_prompt=expense_user_prompt,
                    metadata={"type": "expense_selection", "filename": task.filename}
                )

                try:
                    expense_response = await self.ollama_queue.wait_for_response(expense_task_id, timeout=60)
                    expense_ledger_name, expense_confidence = self._parse_ledger_response(
                        expense_response.response_text, expense_ledgers
                    )
                except TimeoutError:
                    logger.error(f"Worker {worker_id} Ollama timeout for expense ledger: {task.filename}")
                    expense_ledger_name = "Suspended AC"
                    expense_confidence = 0.0

                if not expense_ledger_name or not expense_response.success:
                    expense_ledger_name = "Suspended AC"
                    expense_confidence = 0.0

                logger.info(f"Worker {worker_id} Expense ledger: {expense_ledger_name} (confidence: {expense_confidence:.2f})")

                # === STEP 2b: Ollama - Vendor Ledger Selection ===
                liability_ledgers = await self._load_liability_ledgers_from_coa(task.batch_id)
                vendor_name = invoice_data.header.vendor_name

                vendor_system_prompt = """You are an accounting assistant. Select the most appropriate liability/vendor ledger from the Chart of Accounts (COA) based on the vendor name.

Rules:
- Return ONLY the exact ledger name from the provided list
- Match the vendor name to the closest creditor/liability ledger
- If unsure, return "Suspended AC"
- Do not add explanations or extra text"""

                vendor_user_prompt = f"""Select the best liability ledger for this vendor:

Vendor name: {vendor_name}

Available liability ledgers:
{chr(10).join(liability_ledgers)}

Return only the ledger name."""

                vendor_task_id = await self.ollama_queue.enqueue_request(
                    batch_id=task.batch_id,
                    system_prompt=vendor_system_prompt,
                    user_prompt=vendor_user_prompt,
                    metadata={"type": "vendor_selection", "filename": task.filename}
                )

                try:
                    vendor_response = await self.ollama_queue.wait_for_response(vendor_task_id, timeout=60)
                    vendor_ledger_name, vendor_confidence = self._parse_ledger_response(
                        vendor_response.response_text, liability_ledgers
                    )
                except TimeoutError:
                    logger.error(f"Worker {worker_id} Ollama timeout for vendor ledger: {task.filename}")
                    vendor_ledger_name = "Suspended AC"
                    vendor_confidence = 0.0

                if not vendor_ledger_name or not vendor_response.success:
                    vendor_ledger_name = "Suspended AC"
                    vendor_confidence = 0.0

                logger.info(f"Worker {worker_id} Vendor ledger: {vendor_ledger_name} (confidence: {vendor_confidence:.2f})")

                # === STEP 3: Generate Multi-Row XL Output ===
                voucher_number = await self._get_next_voucher_number(task.batch_id)

                xl_rows = XLOutputGenerator.generate_xl_output_rows(
                    invoice_data=invoice_data,
                    expense_ledger_name=expense_ledger_name,
                    vendor_ledger_name=vendor_ledger_name,
                    expense_confidence=expense_confidence,
                    vendor_confidence=vendor_confidence,
                    voucher_number=voucher_number
                )

                logger.info(f"Worker {worker_id} generated {len(xl_rows)} XL rows for {task.filename} with voucher #{voucher_number}")

                # === STEP 4: Save Results ===
                # Save invoice JSON
                json_path = self.file_handler.get_json_path(task.batch_id, task.filename)
                success, saved_json_path = self.file_handler.save_json_result(
                    task.batch_id,
                    task.filename,
                    invoice_data.model_dump()
                )

                # Update session with results
                result = ProcessedInvoiceResult(
                    filename=task.filename,
                    pdf_path=task.pdf_path,
                    json_path=json_path,
                    status="completed",
                    invoice_number=invoice_data.header.invoice_number,
                    vendor_name=invoice_data.header.vendor_name,
                    total_amount=invoice_data.total_amount,
                    currency=invoice_data.currency,
                    line_items_count=len(invoice_data.line_items),
                    xl_output=[row.model_dump() for row in xl_rows],
                    data=invoice_data,
                    timestamp=datetime.utcnow().isoformat()
                )

                session = self.session_manager.get_session(task.batch_id)
                if session:
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
                    processed_results.append(result.model_dump())
                    self.session_manager.update_session(task.batch_id, {"processed_results": processed_results})

                # Update file status to completed
                self.session_manager.update_file_status(
                    task.batch_id,
                    task.filename,
                    "completed"
                )

                logger.info(f"✅ Worker {worker_id} completed {task.filename}: {len(xl_rows)} XL rows generated")

                # Remove from processing set
                await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")

            except Exception as e:
                logger.error(f"Worker {worker_id} error: {str(e)}")
                await asyncio.sleep(1)

    async def _load_expense_ledgers_from_coa(self, batch_id: str) -> List[str]:
        """
        Load expense leaf nodes from COA (cached per batch).

        Args:
            batch_id: Batch identifier

        Returns:
            List of expense ledger names
        """
        # Check cache first
        if batch_id in self.expense_ledgers_cache:
            return self.expense_ledgers_cache[batch_id]

        # Load COA from session
        session = self.session_manager.get_session(batch_id)
        if not session or "coa_data" not in session:
            logger.warning(f"COA not found for batch {batch_id}, returning fallback")
            return ["Suspended AC"]

        coa_data = session["coa_data"]

        # Parse COA data if it's a string
        if isinstance(coa_data, str):
            try:
                coa_data = json.loads(coa_data)
            except json.JSONDecodeError:
                logger.error(f"Failed to parse COA data for batch {batch_id}")
                return ["Suspended AC"]

        # Extract expense ledgers from flat_list
        flat_list = coa_data.get("flat_list", [])

        # Find expense ledgers (ledgers under "Expense" or "Expenses" groups)
        expense_ledgers = [
            ledger for ledger in flat_list
            if "expense" in ledger.lower() or "cost" in ledger.lower()
        ]

        # If no expense ledgers found, use all leaf nodes as fallback
        if not expense_ledgers:
            expense_ledgers = flat_list[:50]  # Limit to first 50 to avoid token overflow

        # Always include fallback
        if "Suspended AC" not in expense_ledgers:
            expense_ledgers.append("Suspended AC")

        # Cache result
        self.expense_ledgers_cache[batch_id] = expense_ledgers
        logger.info(f"Loaded {len(expense_ledgers)} expense ledgers for batch {batch_id}")

        return expense_ledgers

    async def _load_liability_ledgers_from_coa(self, batch_id: str) -> List[str]:
        """
        Load liability leaf nodes from COA (cached per batch).

        Args:
            batch_id: Batch identifier

        Returns:
            List of liability ledger names
        """
        # Check cache first
        if batch_id in self.liability_ledgers_cache:
            return self.liability_ledgers_cache[batch_id]

        # Load COA from session
        session = self.session_manager.get_session(batch_id)
        if not session or "coa_data" not in session:
            logger.warning(f"COA not found for batch {batch_id}, returning fallback")
            return ["Suspended AC"]

        coa_data = session["coa_data"]

        # Parse COA data if it's a string
        if isinstance(coa_data, str):
            try:
                coa_data = json.loads(coa_data)
            except json.JSONDecodeError:
                logger.error(f"Failed to parse COA data for batch {batch_id}")
                return ["Suspended AC"]

        # Extract liability ledgers from flat_list
        flat_list = coa_data.get("flat_list", [])

        # Find liability ledgers (ledgers under "Liability" or "Creditors" groups)
        liability_ledgers = [
            ledger for ledger in flat_list
            if "liability" in ledger.lower() or "creditor" in ledger.lower() or "payable" in ledger.lower()
        ]

        # If no liability ledgers found, use all leaf nodes as fallback
        if not liability_ledgers:
            liability_ledgers = flat_list[:50]  # Limit to first 50 to avoid token overflow

        # Always include fallback
        if "Suspended AC" not in liability_ledgers:
            liability_ledgers.append("Suspended AC")

        # Cache result
        self.liability_ledgers_cache[batch_id] = liability_ledgers
        logger.info(f"Loaded {len(liability_ledgers)} liability ledgers for batch {batch_id}")

        return liability_ledgers

    def _parse_ledger_response(self, response_text: str, valid_ledgers: List[str]) -> Tuple[str, float]:
        """
        Parse Ollama response to extract ledger name and confidence.

        Args:
            response_text: Raw Ollama response
            valid_ledgers: List of valid ledger names from COA

        Returns:
            Tuple of (ledger_name, confidence_score)
        """
        # Clean response
        response_text = response_text.strip().replace('"', '').replace("'", "")

        # Check if response is valid ledger
        if response_text in valid_ledgers:
            return response_text, 0.95  # High confidence for exact match

        # Try case-insensitive match
        response_lower = response_text.lower()
        for ledger in valid_ledgers:
            if ledger.lower() == response_lower:
                return ledger, 0.90  # Slightly lower confidence for case mismatch

        # Try partial match (response contains ledger name or vice versa)
        for ledger in valid_ledgers:
            if ledger.lower() in response_lower or response_lower in ledger.lower():
                return ledger, 0.75  # Medium confidence for partial match

        # No match found, return fallback
        logger.warning(f"Ollama response '{response_text}' not found in COA, using fallback")
        return "Suspended AC", 0.0

    async def _get_next_voucher_number(self, batch_id: str) -> int:
        """
        Atomic increment of voucher counter per batch.

        Args:
            batch_id: Batch identifier

        Returns:
            Next voucher number (1, 2, 3...)
        """
        counter_key = f"{self.VOUCHER_COUNTER_PREFIX}{batch_id}"
        voucher_number = await self.redis.incr(counter_key)

        # Set expiry (24 hours)
        await self.redis.expire(counter_key, 86400)

        return voucher_number

    async def start_workers(self, num_workers: int):
        """Start worker pool"""
        logger.info(f"Starting {num_workers} invoice workers")

        for worker_id in range(1, num_workers + 1):
            asyncio.create_task(self.worker_loop(worker_id))

        logger.info(f"✅ All {num_workers} invoice workers started")

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
