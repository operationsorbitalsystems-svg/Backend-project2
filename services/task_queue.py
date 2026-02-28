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

    def __init__(
        self,
        redis_client: Redis,
        mistral_queue: MistralQueueManager,
        llm_queue: LLMQueue = None,
        session_manager: RedisSessionManager = None,
        file_handler: FileHandler = None,
        agent_queue: AgentQueue = None,
    ):
        if redis_client is None:
            raise ValueError("Redis client is required for task queue. Set REDIS_ENABLED=true")

        self.redis = redis_client
        self.mistral_queue = mistral_queue
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
        self.expense_ledgers_cache = {}
        self.liability_ledgers_cache = {}

        logger.info("TaskQueueManager initialized")

    # ─────────────────────────────────────────────────────────────────────────
    # Queue management
    # ─────────────────────────────────────────────────────────────────────────

    async def enqueue_file(self, batch_id: str, filename: str, pdf_path: str, vendor_name: str = ""):
        task = TaskItem(
            task_id=uuid4().hex,
            batch_id=batch_id,
            vendor_name=vendor_name,
            filename=filename,
            pdf_path=pdf_path,
            enqueued_at=datetime.utcnow().isoformat(),
        )

        task_json = task.model_dump_json()
        queue_key = f"{self.PENDING_QUEUE_PREFIX}{batch_id}"
        await self.redis.rpush(queue_key, task_json)
        await self.redis.sadd(self.ACTIVE_BATCHES_KEY, batch_id)
        logger.info(f"Enqueued task {task.task_id}: {filename} (batch: {batch_id})")

    async def get_next_task_round_robin(self) -> Optional[TaskItem]:
        active_batches = await self.redis.smembers(self.ACTIVE_BATCHES_KEY)
        if not active_batches:
            return None

        batches = sorted(list(active_batches))
        index_str = await self.redis.get(self.ROUND_ROBIN_INDEX_KEY)
        index = int(index_str) if index_str else 0
        selected_batch = batches[index % len(batches)]

        queue_key = f"{self.PENDING_QUEUE_PREFIX}{selected_batch}"
        task_json = await self.redis.lpop(queue_key)

        if task_json:
            task_dict = json.loads(task_json)
            task = TaskItem(**task_dict)

            processing_key = f"{self.PROCESSING_PREFIX}{task.task_id}"
            await self.redis.setex(processing_key, 3600, task_json)
            await self.redis.incr(self.ROUND_ROBIN_INDEX_KEY)

            logger.debug(f"Pulled task {task.task_id} from batch {selected_batch} (index: {index})")
            return task
        else:
            await self.redis.srem(self.ACTIVE_BATCHES_KEY, selected_batch)
            logger.debug(f"Batch {selected_batch} exhausted, removed from active batches")
            return await self.get_next_task_round_robin()

    # ─────────────────────────────────────────────────────────────────────────
    # COA helpers
    # ─────────────────────────────────────────────────────────────────────────

    def get_or_load_expense_leaves(self, batch_id: str, pattern_report: re.compile = None) -> Optional[list]:
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

            leaf_nodes = extract_expense_leaf_nodes(expenses)
            logger.info(f"📋 Loaded {len(leaf_nodes)} expense ledgers for batch {batch_id}")
            return leaf_nodes

        except Exception as e:
            logger.info(f"Couldn't extract because {e}")
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

            creditor_pattern = re.compile(r'(?i)\bcreditor(s)?\b')
            match = find_matching_non_leaf_node(liabilities, creditor_pattern)
            if not match:
                logger.error("No Sundry Creditors-like node found under Liabilities")
                return None

            creditor_key, creditor_tree = match
            logger.info(f"✅ Found creditor node: {creditor_key}")
            leaf_nodes = extract_leaf_nodes(creditor_tree)
            logger.info(f"📋 Loaded {len(leaf_nodes)} creditor ledgers from '{creditor_key}'")
            return leaf_nodes

        except Exception as e:
            logger.exception("Failed to extract Sundry Creditors ledgers")
            raise e

    # ─────────────────────────────────────────────────────────────────────────
    # LLM response parsing
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_ledger_response(
        self,
        response_text: str,
        valid_ledgers: List[str],
        worker_id: str = None,
        get_pydantic_schema: BaseModel = None,
    ) -> Tuple[str, float]:
        response_text = response_text.strip()

        if get_pydantic_schema:
            try:
                validated_model = get_pydantic_schema.model_validate_json(response_text)
                if hasattr(validated_model, 'ledger'):
                    ledger_name = validated_model.ledger
                elif hasattr(validated_model, 'nature_of_transaction'):
                    ledger_name = validated_model.nature_of_transaction
                else:
                    ledger_name = next(iter(validated_model.model_dump().values()))
            except Exception as e:
                logger.warning(f"Pydantic validation failed, using fuzzy matching: {e}")
                try:
                    parsed = json.loads(response_text)
                    ledger_name = (
                        parsed.get('ledger')
                        or parsed.get('nature_of_transaction')
                        or next(iter(parsed.values()))
                    )
                except json.JSONDecodeError:
                    ledger_name = response_text.replace('"', '').replace("'", "").strip()
        else:
            ledger_name = response_text.replace('"', '').replace("'", "").strip()

        response_text = ledger_name
        logger.info(f"Worker {worker_id} Response was: {response_text}")

        if response_text in valid_ledgers:
            return response_text, 0.95
        response_lower = response_text.lower()
        for ledger in valid_ledgers:
            if ledger.lower() == response_lower:
                return ledger, 0.90
        for ledger in valid_ledgers:
            if ledger.lower() in response_lower or response_lower in ledger.lower():
                return ledger, 0.75

        logger.warning(f"Response '{response_text}' not found in COA, using fallback")
        return "Suspended AC", 0.0

    def _safe_parse_ledger(self, response: OllamaResponse, ledger_options, schema, worker_id: int, label: str):
        """
        Parse an LLM response into (ledger_name, confidence).
        Returns (NOT_FOUND, 0.0) on timeout, error, empty parse, or response.success=False.
        """
        if isinstance(response, (Exception, TimeoutError)):
            logger.error(f"Worker {worker_id} LLM timeout/error for {label}: {response}")
            return NOT_FOUND, 0.0
        if not response.success:
            logger.error(f"Worker {worker_id} LLM failed for {label} — defaulting to Suspended AC")
            return NOT_FOUND, 0.0

        name, confidence = self._parse_ledger_response(
            response.response_text, ledger_options,
            get_pydantic_schema=schema, worker_id=worker_id,
        )
        return (name, confidence) if name else (NOT_FOUND, 0.0)

    # ─────────────────────────────────────────────────────────────────────────
    # DR ledger resolution — two strategies, toggled by USE_DR_AGENT
    # ─────────────────────────────────────────────────────────────────────────

    async def _get_dr_ledger_via_agent(
        self,
        worker_id: int,
        task: TaskItem,
        invoice_data: InvoiceData,
        expense_ledgers: list,
    ) -> Tuple[str, float]:
        """
        Resolve the DR (expense) ledger by routing through the AgentQueue.
        Falls back to (NOT_FOUND, 0.0) if the agent fails or times out.
        """
        if self.agent_queue is None:
            logger.error(f"Worker {worker_id} USE_DR_AGENT=True but agent_queue is None — falling back to NOT_FOUND")
            return NOT_FOUND, 0.0

        narration = XLOutputGenerator.concatenate_line_items(invoice_data.line_items)
        expenses_tree = self.file_handler.read_coa_json(task.batch_id)  # full tree for agent

        try:
            enqueued_id = await self.agent_queue.enqueue_request(
                task_id=task.task_id,
                batch_id=task.batch_id,
                line_item=narration,
                file_name=task.filename,
                expenses_tree=expenses_tree,
                vendor_name=task.vendor_name
            )
            response = await self.agent_queue.wait_for_response(enqueued_id, timeout=600)
        except TimeoutError:
            logger.error(f"Worker {worker_id} AgentQueue timeout for DR ledger ({task.filename})")
            return NOT_FOUND, 0.0
        except Exception as e:
            logger.error(f"Worker {worker_id} AgentQueue error for DR ledger ({task.filename}): {e}")
            return NOT_FOUND, 0.0

        # Treat response.success=False the same as a failed LLM call
        if not response.success:
            logger.error(
                f"Worker {worker_id} AgentQueue returned success=False for {task.filename} "
                f"— defaulting to Suspended AC"
            )
            return NOT_FOUND, 0.0

        selected = response.selected_leaf
        if not selected:
            logger.warning(f"Worker {worker_id} AgentQueue returned empty leaf for {task.filename}")
            return NOT_FOUND, 0.0

        # Validate against known expense ledgers (same fuzzy logic as LLM path)
        expense_ledgers_set = set(expense_ledgers) | {NOT_FOUND}
        name, confidence = self._parse_ledger_response(
            selected,
            list(expense_ledgers_set),
            worker_id=worker_id,
            get_pydantic_schema=None,  # agent returns plain string
        )
        return (name, confidence) if name else (NOT_FOUND, 0.0)

    async def _get_dr_ledger_via_llm(
        self,
        worker_id: int,
        task: TaskItem,
        invoice_data: InvoiceData,
        expense_ledgers: list,
    ) -> Tuple[str, float]:
        """
        Resolve the DR (expense) ledger using the standard LLMQueue path.
        """
        narration = XLOutputGenerator.concatenate_line_items(invoice_data.line_items)
        expense_ledgers_set = set(expense_ledgers) | {NOT_FOUND}
        custom_schema_expense = custom_ledger(expense_ledgers)
        expense_sys, expense_usr = ledger_name_prompt_dr(
            ledger_narration=narration, expense_leaf_nodes=expense_ledgers_set
        )

        expense_task_id = await self.llm_queue.enqueue_request(
            batch_id=task.batch_id,
            system_prompt=expense_sys,
            user_prompt=expense_usr,
            metadata={
                "type": "expense_selection",
                "filename": task.filename,
                "pydantic_json_schema": custom_schema_expense.model_json_schema(),
            },
        )
        expense_response = await self.llm_queue.wait_for_response(expense_task_id, timeout=60)

        return self._safe_parse_ledger(
            expense_response, expense_ledgers_set, custom_schema_expense, worker_id, "expense"
        )

    async def _resolve_dr_ledger(
        self,
        worker_id: int,
        task: TaskItem,
        invoice_data: InvoiceData,
        expense_ledgers: list,
    ) -> Tuple[str, float]:
        """
        Single entry point for DR ledger resolution.
        Routes to agent or LLM based on the USE_DR_AGENT config flag.
        """
        if USE_DR_AGENT:
            logger.info(f"Worker {worker_id} using AgentQueue for DR ledger ({task.filename})")
            return await self._get_dr_ledger_via_agent(worker_id, task, invoice_data, expense_ledgers)
        else:
            logger.info(f"Worker {worker_id} using LLMQueue for DR ledger ({task.filename})")
            return await self._get_dr_ledger_via_llm(worker_id, task, invoice_data, expense_ledgers)

    # ─────────────────────────────────────────────────────────────────────────
    # Voucher helpers
    # ─────────────────────────────────────────────────────────────────────────

    async def _get_next_voucher_number(self, batch_id: str) -> int:
        counter_key = f"{self.VOUCHER_COUNTER_PREFIX}{batch_id}"
        voucher_number = await self.redis.incr(counter_key)
        await self.redis.expire(counter_key, 86400)
        return voucher_number

    # ─────────────────────────────────────────────────────────────────────────
    # Worker lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    async def start_workers(self, num_workers: int = 100):
        logger.info(f"Starting {num_workers} main task queue workers")
        for worker_id in range(1, num_workers + 1):
            asyncio.create_task(self.worker_loop(worker_id))
        logger.info(f"✅ All {num_workers} main task queue workers started")

    async def recover_crashed_tasks(self):
        logger.info("Recovering crashed tasks...")
        processing_pattern = f"{self.PROCESSING_PREFIX}*"
        cursor = 0
        processing_keys = []

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
                    self.session_manager.update_file_status(
                        task.batch_id, task.filename, "failed",
                        error="Server restarted during processing",
                    )
                    await self.redis.delete(key)
                    recovered_count += 1
                    logger.info(f"Recovered crashed task: {task.filename} (batch: {task.batch_id})")
            except Exception as e:
                logger.error(f"Error recovering task from key {key}: {e}")

        logger.info(f"Recovered {recovered_count} crashed tasks")

    # ─────────────────────────────────────────────────────────────────────────
    # Main worker loop
    # ─────────────────────────────────────────────────────────────────────────

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
                logger.info(
                    f"Worker {worker_id} processing {task.filename} "
                    f"(batch: {task.batch_id}, task: {task.task_id})"
                )

                invoice_data = await self._run_ocr_step(worker_id, task)
                if invoice_data is None:
                    continue

                (
                    expense_ledger_name, expense_confidence,
                    vendor_ledger_name, vendor_confidence,
                    tds_ledger_name, tds_confidence,
                ) = await self._run_llm_step(worker_id, task, invoice_data)

                await self._finalize_and_save(
                    worker_id, task, invoice_data,
                    expense_ledger_name, expense_confidence,
                    vendor_ledger_name, vendor_confidence,
                    tds_ledger_name, tds_confidence,
                )

            except Exception as e:
                import traceback
                logger.error(f"Worker {worker_id} error: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                if task:
                    self.session_manager.update_file_status(
                        task.batch_id, task.filename, "failed", error=str(e)
                    )
                    await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")
                await asyncio.sleep(1)

            finally:
                if _ctx_token is not None:
                    batch_id_var.reset(_ctx_token)

    # ─────────────────────────────────────────────────────────────────────────
    # Step implementations
    # ─────────────────────────────────────────────────────────────────────────

    async def _run_ocr_step(self, worker_id: int, task: TaskItem) -> InvoiceData | None:
        """Step 1 & 2: Send PDF to Mistral OCR queue and return parsed InvoiceData."""
        self.session_manager.update_file_status(task.batch_id, task.filename, "processing")

        mistral_task_id = await self.mistral_queue.enqueue_request(
            batch_id=task.batch_id,
            filename=task.filename,
            pdf_path=task.pdf_path,
        )

        try:
            mistral_response = await self.mistral_queue.wait_for_response(
                task_id=mistral_task_id, timeout=120
            )
        except TimeoutError:
            logger.error(f"Worker {worker_id} Mistral OCR timeout for {task.filename}")
            await self._fail_task(task, "Mistral OCR timeout")
            return None

        if not mistral_response.success:
            logger.error(
                f"Worker {worker_id} Mistral OCR failed for {task.filename}: "
                f"{mistral_response.error_message}"
            )
            await self._fail_task(task, f"Mistral OCR failed: {mistral_response.error_message}")
            return None

        try:
            async with aiofiles.open(mistral_response.json_path, 'r') as f:
                json_content = await f.read()
            invoice_data = InvoiceData.model_validate(json.loads(json_content))
            logger.info(f"Worker {worker_id} Mistral OCR completed for {task.filename}")
            return invoice_data
        except Exception as e:
            logger.error(
                f"Worker {worker_id} failed to load invoice data from "
                f"{mistral_response.json_path}: {e}"
            )
            await self._fail_task(task, f"Failed to load invoice data: {e}")
            return None

    async def _run_llm_step(self, worker_id: int, task: TaskItem, invoice_data: InvoiceData):
        """
        Steps 2a–4: Resolve expense (DR), vendor (CR), and TDS ledger names.

        DR resolution is routed through _resolve_dr_ledger which switches between
        AgentQueue and LLMQueue based on the USE_DR_AGENT flag.

        CR and TDS always use the LLMQueue.

        Returns a 6-tuple:
            (expense_name, expense_conf, vendor_name, vendor_conf, tds_name, tds_conf)
        """
        narration = XLOutputGenerator.concatenate_line_items(invoice_data.line_items)
        vendor_name = invoice_data.header.vendor_name

        # ── Load ledger lists ──────────────────────────────────────────────
        expense_ledgers = self.get_or_load_expense_leaves(task.batch_id)
        liability_ledgers = self.get_sundry_creditor_ledgers(task.batch_id)
        tds_set = MANAGER.get_all_transaction_natures()

        logger.debug(f"Worker {worker_id} Expenses: {expense_ledgers}")
        logger.debug(f"Worker {worker_id} Liabilities: {liability_ledgers}")
        logger.debug(f"Worker {worker_id} TDS: {tds_set}")

        # ── Build CR + TDS prompts & schemas ─────────────────────────────
        custom_schema_liability = custom_ledger(liability_ledgers)
        liability_ledgers_set = set(liability_ledgers) | {NOT_FOUND}
        vendor_sys, vendor_usr = ledger_name_prompt_cr(
            vendor_name=vendor_name,
            invoice_description=narration,
            liability_leaf_nodes=liability_ledgers_set,
        )

        custom_schema_tds = custom_tds(tds_set)
        tds_sys, tds_usr = tds_nature_prompt(
            vendor_name=vendor_name,
            ledger_narration=narration,
            tds_nature_options=tds_set,
        )

        # ── Enqueue CR + TDS to LLM queue ────────────────────────────────
        vendor_task_id = await self.llm_queue.enqueue_request(
            batch_id=task.batch_id,
            system_prompt=vendor_sys,
            user_prompt=vendor_usr,
            metadata={
                "type": "vendor_selection",
                "filename": task.filename,
                "pydantic_json_schema": custom_schema_liability.model_json_schema(),
            },
        )
        tds_task_id = await self.llm_queue.enqueue_request(
            batch_id=task.batch_id,
            system_prompt=tds_sys,
            user_prompt=tds_usr,
            metadata={
                "type": "tds_selection",
                "filename": task.filename,
                "pydantic_json_schema": custom_schema_tds.model_json_schema(),
            },
        )

        # ── Resolve DR + CR + TDS concurrently ───────────────────────────
        # DR uses the toggled strategy; CR+TDS are plain LLM awaits.
        dr_coro = self._resolve_dr_ledger(worker_id, task, invoice_data, expense_ledgers)
        cr_coro = self.llm_queue.wait_for_response(vendor_task_id, timeout=60)
        tds_coro = self.llm_queue.wait_for_response(tds_task_id, timeout=60)

        (expense_name, expense_conf), vendor_response, tds_response = await asyncio.gather(
            dr_coro, cr_coro, tds_coro, return_exceptions=True
        )

        # If DR gather itself raised (shouldn't happen but guard anyway)
        if isinstance((expense_name, expense_conf), Exception):
            logger.error(f"Worker {worker_id} DR resolution raised: {expense_name}")
            expense_name, expense_conf = NOT_FOUND, 0.0

        # Parse CR + TDS
        vendor_name_result, vendor_conf = self._safe_parse_ledger(
            vendor_response, liability_ledgers_set, custom_schema_liability, worker_id, "vendor"
        )
        tds_name, tds_conf = self._safe_parse_ledger(
            tds_response, list(tds_set), custom_schema_tds, worker_id, "tds"
        )

        logger.info(f"Worker {worker_id} Expense ledger : {expense_name} ({expense_conf:.2f})")
        logger.info(f"Worker {worker_id} Vendor ledger  : {vendor_name_result} ({vendor_conf:.2f})")
        logger.info(f"Worker {worker_id} TDS ledger     : {tds_name} ({tds_conf:.2f})")

        # Normalize NOT_FOUND for TDS
        if tds_name == NOT_FOUND:
            tds_name, tds_conf = None, 0

        return expense_name, expense_conf, vendor_name_result, vendor_conf, tds_name, tds_conf

    async def _finalize_and_save(
        self,
        worker_id: int,
        task: TaskItem,
        invoice_data: InvoiceData,
        expense_ledger_name, expense_confidence,
        vendor_ledger_name, vendor_confidence,
        tds_ledger_name, tds_confidence,
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
            tds_confidence=tds_confidence,
        )
        logger.info(
            f"Worker {worker_id} generated {len(xl_rows)} XL rows for "
            f"{task.filename} with voucher #{voucher_number}"
        )

        json_path = self.file_handler.get_json_path(task.batch_id, task.filename)
        self.file_handler.save_json_result(task.batch_id, task.filename, invoice_data.model_dump())

        result = ProcessedInvoiceResult(
            filename=task.filename,
            pdf_path=task.pdf_path,
            json_path=json_path,
            status="success",
            invoice_number=invoice_data.header.invoice_number,
            vendor_name=invoice_data.header.vendor_name,
            total_amount=invoice_data.total_amount,
            currency=invoice_data.currency,
            line_items_count=len(invoice_data.line_items),
            xl_output=[row.model_dump() for row in xl_rows],
            data=invoice_data,
            timestamp=datetime.utcnow().isoformat(),
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

    async def _fail_task(self, task: TaskItem, error: str):
        """Mark a task as failed and remove it from the Redis processing set."""
        self.session_manager.update_file_status(task.batch_id, task.filename, "failed", error=error)
        await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_task_queue_manager = None


def get_task_queue_manager() -> TaskQueueManager:
    global _task_queue_manager
    if _task_queue_manager is None:
        _task_queue_manager = TaskQueueManager(redis_client)
    return _task_queue_manager