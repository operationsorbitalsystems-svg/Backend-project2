import asyncio
import json
import logging
from typing import Optional, List, Tuple, Dict
from datetime import datetime
from uuid import uuid4
from pydantic import BaseModel
import aiofiles
from models import OllamaResponse
from config import redis_client, NOT_FOUND, USE_DR_AGENT
from models import TaskItem, ProcessedInvoiceResult, custom_ledger, InvoiceData, custom_tds
from services.session_manager import get_session_manager
from services.file_handler import FileHandler
from services.xl_output_generator import XLOutputGenerator
from services.llm_queue import get_llm_queue
from utils.logger import setup_logger, batch_id_var
from utils.prompts import ledger_name_prompt_cr, ledger_name_prompt_dr, extract_expense_leaf_nodes, tds_nature_prompt                                  
import re
from utils.coa_tree_traversal import find_matching_non_leaf_node, extract_leaf_nodes
from utils.tds import MANAGER
from redis import Redis
from .session_manager import RedisSessionManager
from .mistral_queue import MistralQueueManager
from .llm_queue import LLMQueue
from .file_handler import FileHandler
from agent.agent_qeue import AgentQueue

logger = setup_logger()



class TaskQueueManager:
    """
    Manages a Redis-backed task queue with round-robin batch scheduling.

    Ensures fair processing across multiple users by cycling through batches
    in round-robin fashion, preventing any single user from monopolizing workers.
    """

    def __init__(self, redis_client: Redis, mistral_queue: MistralQueueManager, llm_queue: LLMQueue=None, session_manager : RedisSessionManager =None, file_handler: FileHandler =None, agent_queue: AgentQueue = None):
        if redis_client is None:
            raise ValueError("Redis client is required for task queue. Set REDIS_ENABLED=true")

        self.redis = redis_client
        self.mistral_queue = mistral_queue  # NEW: Mistral task queue
        self.session_manager = session_manager if session_manager is not None else get_session_manager()
        self.file_handler = file_handler if file_handler is not None else FileHandler()
        self.llm_queue = llm_queue if llm_queue is not None else get_llm_queue()
        self.agent_queue = agent_queue
        
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
            task_id=uuid4().hex,
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

    def get_or_load_expense_leaves(self, batch_id: str, pattern_report : re.compile = None) -> Optional[list]:
        """
        Get expense leaf nodes from cache or load from COA file.
        Cache is in-memory per batch. On server restart, reloads on-demand.
        """
        # # Check cache first
        # if batch_id in self.expense_leaf_cache:
        #     return self.expense_leaf_cache[batch_id]

        # Load from COA file
        try:
            if not pattern_report:
                pattern_report = re.compile(r'(?i)\bexpense(s)?\b')
            
            coa_data = self.file_handler.read_coa_json(batch_id)
            if not coa_data:
                logger.error(f"No COA data found for batch {batch_id}")
                return None
            
            hierarchy = coa_data.get("hierarchy", {})

            expenses = None
            for key, value in hierarchy.items():
                if isinstance(key, str) and pattern_report.fullmatch(key.strip()):
                    expenses = value
                    break

            if not expenses:
                logger.error(f"No Expenses section in COA for batch {batch_id}")
                return None

            # Extract leaf nodes
            leaf_nodes = extract_expense_leaf_nodes(expenses)

            # # Cache in memory
            # self.expense_leaf_cache[batch_id] = leaf_nodes

            logger.info(f"📋 Loaded {len(leaf_nodes)} expense ledgers for batch {batch_id}")
            return leaf_nodes
        
        except Exception as e:
            logger.info(f"Couldnt extract because e")
            raise e
        
    def get_sundry_creditor_ledgers(self, batch_id: str) -> Optional[list]:
        try:
            coa_data = self.file_handler.read_coa_json(batch_id)
            if not coa_data:
                logger.error(f"No COA data found for batch {batch_id}")
                return None

            liability_pattern = re.compile(r'(?i)\bliabilit(y|ies)\b')
            
            hierarchy = coa_data.get("hierarchy", {})

            liabilities = None
            for key, value in hierarchy.items():
                if isinstance(key, str) and liability_pattern.fullmatch(key.strip()):
                    liabilities = value
                    break

            if not isinstance(liabilities, dict):
                logger.error(f"No Liabilities section in COA for batch {batch_id}")
                return None

            # Match: Sundry Creditors, Creditors, Creditor, etc.
            creditor_pattern = re.compile(r'(?i)\bcreditor(s)?\b')

            match = find_matching_non_leaf_node(liabilities, creditor_pattern)
            if not match:
                logger.error("No Sundry Creditors-like node found under Liabilities")
                return None

            creditor_key, creditor_tree = match
            logger.info(f"✅ Found creditor node: {creditor_key}")

            leaf_nodes = extract_leaf_nodes(creditor_tree)

            logger.info(
                f"📋 Loaded {len(leaf_nodes)} creditor ledgers from '{creditor_key}'"
            )
            return leaf_nodes

        except Exception as e:
            logger.exception("Failed to extract Sundry Creditors ledgers")
            raise e        

    def _parse_ledger_response(self, response_text: str, valid_ledgers: List[str], worker_id: str = None, get_pydantic_schema: BaseModel = None) -> Tuple[str, float]:
        """
        Parse Ollama response to extract ledger name and confidence.

        Args:
            response_text: Raw Ollama response
            valid_ledgers: List of valid ledger names from COA

        Returns:
            Tuple of (ledger_name, confidence_score)
        """

        # Clean response
        response_text = response_text.strip()

        if get_pydantic_schema:
            try:
                # Try strict Pydantic validation first
                validated_model = get_pydantic_schema.model_validate_json(response_text)

                if hasattr(validated_model, 'ledger'):
                    ledger_name = validated_model.ledger
                elif hasattr(validated_model, 'nature_of_transaction'):
                    ledger_name = validated_model.nature_of_transaction
                else:
                    ledger_name = next(iter(validated_model.model_dump().values()))
            except Exception as e:
                # Pydantic validation failed - fall back to manual JSON parsing
                # Let fuzzy matching below handle validation
                logger.warning(f"Pydantic validation failed, using fuzzy matching: {e}")
                try:
                    parsed = json.loads(response_text)
                    ledger_name = parsed.get('ledger') or parsed.get('nature_of_transaction') or next(iter(parsed.values()))
                except json.JSONDecodeError:
                    ledger_name = response_text.replace('"', '').replace("'", "").strip()
        else:
            # Clean up response (remove quotes, newlines, extra spaces)
            ledger_name = response_text.replace('"', '').replace("'", "").strip()

        response_text = ledger_name
        
        
        #REMOVE THIS BEFORE PROD
        logger.info(f"Worker {worker_id} Response was : {response_text}")

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

    async def start_workers(self, num_workers: int = 100):
        """Start worker pool"""
        logger.info(f"Starting {num_workers} main task queue workers")

        for worker_id in range(1, num_workers + 1):
            asyncio.create_task(self.worker_loop(worker_id))

        logger.info(f"✅ All {num_workers} main task queue workers started")

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




    async def worker_loop(self, worker_id: int):
        """Main invoice processing workflow orchestrator."""
        logger.info(f"Worker {worker_id} started")

        while True:
            _ctx_token = None
            task = None
            try:
                task = await self.get_next_task_round_robin()
                if task is None:
                    await asyncio.sleep(1)
                    continue

                _ctx_token = batch_id_var.set(task.batch_id)
                logger.info(f"Worker {worker_id} processing {task.filename} (batch: {task.batch_id}, task: {task.task_id})")

                invoice_data = await self._run_ocr_step(worker_id, task)
                if invoice_data is None:
                    continue

                expense_ledger_name, expense_confidence, \
                vendor_ledger_name, vendor_confidence, \
                tds_ledger_name, tds_confidence = await self._run_llm_step(worker_id, task, invoice_data)

                await self._finalize_and_save(
                    worker_id, task, invoice_data,
                    expense_ledger_name, expense_confidence,
                    vendor_ledger_name, vendor_confidence,
                    tds_ledger_name, tds_confidence
                )

            except Exception as e:
                import traceback
                logger.error(f"Worker {worker_id} error: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                if task:
                    self.session_manager.update_file_status(task.batch_id, task.filename, "failed", error=str(e))
                    await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")
                await asyncio.sleep(1)

            finally:
                if _ctx_token is not None:
                    batch_id_var.reset(_ctx_token)


    async def _run_ocr_step(self, worker_id: int, task: TaskItem) -> InvoiceData | None:
        """
        Step 1 & 2: Send PDF to Mistral OCR queue and return parsed InvoiceData.
        Returns None and marks task failed if OCR fails.
        """
        self.session_manager.update_file_status(task.batch_id, task.filename, "processing")

        mistral_task_id = await self.mistral_queue.enqueue_request(
            batch_id=task.batch_id,
            filename=task.filename,
            pdf_path=task.pdf_path
        )

        try:
            mistral_response = await self.mistral_queue.wait_for_response(task_id=mistral_task_id, timeout=120)
        except TimeoutError:
            logger.error(f"Worker {worker_id} Mistral OCR timeout for {task.filename}")
            await self._fail_task(task, "Mistral OCR timeout")
            return None

        if not mistral_response.success:
            logger.error(f"Worker {worker_id} Mistral OCR failed for {task.filename}: {mistral_response.error_message}")
            await self._fail_task(task, f"Mistral OCR failed: {mistral_response.error_message}")
            return None

        try:
            async with aiofiles.open(mistral_response.json_path, 'r') as f:
                json_content = await f.read()
            invoice_data = InvoiceData.model_validate(json.loads(json_content))
            logger.info(f"Worker {worker_id} Mistral OCR completed for {task.filename}")
            return invoice_data
        except Exception as e:
            logger.error(f"Worker {worker_id} failed to load invoice data from {mistral_response.json_path}: {e}")
            await self._fail_task(task, f"Failed to load invoice data: {e}")
            return None


    async def _run_llm_step(self, worker_id: int, task: TaskItem, invoice_data: InvoiceData):
        """
        Steps 2a–4: Build prompts for expense/vendor/TDS, enqueue to LLM queue,
        await all three in parallel, and parse results.
        Returns a 6-tuple: (expense_name, expense_conf, vendor_name, vendor_conf, tds_name, tds_conf)
        """
        narration = XLOutputGenerator.concatenate_line_items(invoice_data.line_items)
        vendor_name = invoice_data.header.vendor_name

        # Prepare schemas and prompts
        expense_ledgers = self.get_or_load_expense_leaves(task.batch_id)
        custom_schema_expense = custom_ledger(expense_ledgers)
        expense_ledgers_set = set(expense_ledgers) | {NOT_FOUND}
        expense_sys, expense_usr = ledger_name_prompt_dr(ledger_narration=narration, expense_leaf_nodes=expense_ledgers_set)

        liability_ledgers = self.get_sundry_creditor_ledgers(task.batch_id)
        custom_schema_liability = custom_ledger(liability_ledgers)
        liability_ledgers_set = set(liability_ledgers) | {NOT_FOUND}
        vendor_sys, vendor_usr = ledger_name_prompt_cr(vendor_name=vendor_name, invoice_description=narration, liability_leaf_nodes=liability_ledgers_set)

        tds_set = MANAGER.get_all_transaction_natures()
        custom_schema_tds = custom_tds(tds_set)
        tds_sys, tds_usr = tds_nature_prompt(vendor_name=vendor_name, ledger_narration=narration, tds_nature_options=tds_set)

        logger.debug(f"Worker {worker_id} Expenses: {expense_ledgers_set}")
        logger.debug(f"Worker {worker_id} Liabilities: {liability_ledgers_set}")
        logger.debug(f"Worker {worker_id} TDS: {tds_set}")

        # Enqueue all three concurrently
        expense_task_id = await self.llm_queue.enqueue_request(
            batch_id=task.batch_id, system_prompt=expense_sys, user_prompt=expense_usr,
            metadata={"type": "expense_selection", "filename": task.filename, "pydantic_json_schema": custom_schema_expense.model_json_schema()}
        )
        vendor_task_id = await self.llm_queue.enqueue_request(
            batch_id=task.batch_id, system_prompt=vendor_sys, user_prompt=vendor_usr,
            metadata={"type": "vendor_selection", "filename": task.filename, "pydantic_json_schema": custom_schema_liability.model_json_schema()}
        )
        tds_task_id = await self.llm_queue.enqueue_request(
            batch_id=task.batch_id, system_prompt=tds_sys, user_prompt=tds_usr,
            metadata={"type": "expense_selection", "filename": task.filename, "pydantic_json_schema": custom_schema_tds.model_json_schema()}
        )

        # Await all in parallel
        results = await asyncio.gather(
            self.llm_queue.wait_for_response(expense_task_id, timeout=60),
            self.llm_queue.wait_for_response(vendor_task_id, timeout=60),
            self.llm_queue.wait_for_response(tds_task_id, timeout=60),
            return_exceptions=True
        )
        expense_response, vendor_response, tds_response = results
        logger.info(f"Worker {worker_id} TDS raw response: {tds_response}")

        # Parse results
        expense_name, expense_conf = self._safe_parse_ledger(expense_response, expense_ledgers_set, custom_schema_expense, worker_id, "expense")
        vendor_name_result, vendor_conf = self._safe_parse_ledger(vendor_response, liability_ledgers_set, custom_schema_liability, worker_id, "vendor")
        tds_name, tds_conf = self._safe_parse_ledger(tds_response, list(tds_set), custom_schema_tds, worker_id, "tds")

        logger.info(f"Worker {worker_id} Expense ledger: {expense_name} ({expense_conf:.2f})")
        logger.info(f"Worker {worker_id} Vendor ledger: {vendor_name_result} ({vendor_conf:.2f})")
        logger.info(f"Worker {worker_id} TDS ledger: {tds_name} ({tds_conf:.2f})")

        # Normalize NOT_FOUND for TDS
        if tds_name == NOT_FOUND:
            tds_name, tds_conf = None, 0

        return expense_name, expense_conf, vendor_name_result, vendor_conf, tds_name, tds_conf


    async def _finalize_and_save(
        self, worker_id: int, task: TaskItem, invoice_data: InvoiceData,
        expense_ledger_name, expense_confidence,
        vendor_ledger_name, vendor_confidence,
        tds_ledger_name, tds_confidence
    ):
        """Step 8: Generate XL rows, persist results to session, and mark task complete."""
        voucher_number = await self._get_next_voucher_number(task.batch_id)

        xl_rows = XLOutputGenerator.generate_xl_output_rows(
            invoice_data=invoice_data,
            expense_ledger_name=expense_ledger_name,
            vendor_ledger_name=vendor_ledger_name,
            expense_confidence=expense_confidence,
            vendor_confidence=vendor_confidence,
            voucher_number=voucher_number,
            tds_section=tds_ledger_name,
            tds_confidence=tds_confidence
        )
        logger.info(f"Worker {worker_id} generated {len(xl_rows)} XL rows for {task.filename} with voucher #{voucher_number}")

        json_path = self.file_handler.get_json_path(task.batch_id, task.filename)
        self.file_handler.save_json_result(task.batch_id, task.filename, invoice_data.model_dump())

        result = ProcessedInvoiceResult(
            filename=task.filename, pdf_path=task.pdf_path, json_path=json_path,
            status="success", invoice_number=invoice_data.header.invoice_number,
            vendor_name=invoice_data.header.vendor_name, total_amount=invoice_data.total_amount,
            currency=invoice_data.currency, line_items_count=len(invoice_data.line_items),
            xl_output=[row.model_dump() for row in xl_rows],
            data=invoice_data, timestamp=datetime.utcnow().isoformat()
        )

        session = self.session_manager.get_session(task.batch_id)
        if session:
            processed_results_raw = session.get("processed_results", [])
            if isinstance(processed_results_raw, str):
                try:
                    processed_results = json.loads(processed_results_raw)
                except (json.JSONDecodeError, TypeError):
                    processed_results = []
            else:
                processed_results = processed_results_raw if isinstance(processed_results_raw, list) else []

            processed_results.append(result.model_dump())
            self.session_manager.update_session(task.batch_id, {"processed_results": processed_results})

        self.session_manager.update_file_status(task.batch_id, task.filename, "success")
        await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")
        logger.info(f"✅ Worker {worker_id} completed {task.filename}: {len(xl_rows)} XL rows generated")


    def _safe_parse_ledger(self, response: OllamaResponse, ledger_options, schema, worker_id: int, label: str):
        """
        Parse an LLM response into (ledger_name, confidence).
        Returns (NOT_FOUND, 0.0) on timeout, error, or empty parse.
        """
        if isinstance(response, (Exception, TimeoutError)):
            logger.error(f"Worker {worker_id} LLM timeout/error for {label}: {response}")
            return NOT_FOUND, 0.0
        if not response.success:
            logger.error(f"Worker {worker_id} LLM failed for {label}")
            return NOT_FOUND, 0.0

        name, confidence = self._parse_ledger_response(
            response.response_text, ledger_options,
            get_pydantic_schema=schema, worker_id=worker_id
        )
        return (name, confidence) if name else (NOT_FOUND, 0.0)


    async def _fail_task(self, task: TaskItem, error: str):
        """Mark a task as failed and remove it from the Redis processing set."""
        self.session_manager.update_file_status(task.batch_id, task.filename, "failed", error=error)
        await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")
        # Note: Redis delete is async — caller must await separately if needed outside async context.
        # For use inside async methods, prefer: await self.redis.delete(...)



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
