import asyncio
import json
from typing import Optional, List, Tuple
from datetime import datetime
from uuid import uuid4

from config import redis_client
from models import OllamaTask
from services.session_manager import get_session_manager
from services.file_handler import FileHandler
from services.ollama_api_call import call_ollama_for_ledger
from utils.prompts import ledger_name_prompt_dr, extract_expense_leaf_nodes, ledger_name_prompt_cr
import re
from utils.logger import setup_logger
from models import custom_ledger

logger = setup_logger()



class OllamaTaskQueueManager:
    """
    Manages Ollama task queue for ledger name selection.
    Uses same round-robin pattern as Mistral queue.
    """

    # Redis key prefixes
    PENDING_QUEUE_PREFIX = "ollama_queue:pending:"
    PROCESSING_PREFIX = "ollama_queue:processing:"
    ACTIVE_BATCHES_KEY = "ollama_queue:active_batches"
    ROUND_ROBIN_INDEX_KEY = "ollama_queue:round_robin_index"

    def __init__(self, ):
        self.redis = redis_client
        self.session_manager = get_session_manager()
        self.file_handler = FileHandler()

        # In-memory cache for expense leaf nodes per batch
        self.dr_ledger_name_cache = {}  # {batch_id: [leaf_node_names]}
        self.cr_ledger_name_cache = {}
        
    

    async def enqueue_task(
        self,
        batch_id: str,
        vendor_name: str,
        filename: str,
        invoice_number: str,
        ledger_narration: str,
        metadata: Optional[dict] = None
    ) -> str:
        """
        Enqueue an Ollama ledger selection task.

        Returns:
            task_id of the enqueued task
        """
        task = OllamaTask(
            task_id=str(uuid4()),
            batch_id=batch_id,
            vendor_name=vendor_name,
            filename=filename,
            invoice_number=invoice_number,
            ledger_narration=ledger_narration,
            enqueued_at=datetime.utcnow().isoformat(),
            metadata=metadata or {}
        )

        task_json = task.model_dump_json()

        # Add to batch queue (FIFO)
        queue_key = f"{self.PENDING_QUEUE_PREFIX}{batch_id}"
        await self.redis.rpush(queue_key, task_json)

        # Add batch to active set
        await self.redis.sadd(self.ACTIVE_BATCHES_KEY, batch_id)

        logger.info(f"📝 Enqueued Ollama task {task.task_id} for {filename} (batch: {batch_id})")
        return task.task_id

    async def get_next_task_round_robin(self) -> Optional[OllamaTask]:
        """Get next task using round-robin scheduling across batches"""
        active_batches = await self.redis.smembers(self.ACTIVE_BATCHES_KEY)

        if not active_batches:
            return None

        batches = sorted(list(active_batches))

        # Get current index
        index_str = await self.redis.get(self.ROUND_ROBIN_INDEX_KEY)
        index = int(index_str) if index_str else 0

        # Select batch using modulo wrap-around
        selected_batch = batches[index % len(batches)]

        # Pop task from selected batch (FIFO)
        queue_key = f"{self.PENDING_QUEUE_PREFIX}{selected_batch}"
        task_json = await self.redis.lpop(queue_key)

        if task_json:
            task = OllamaTask.model_validate_json(task_json)

            # Mark as processing with 1-hour expiry
            processing_key = f"{self.PROCESSING_PREFIX}{task.task_id}"
            await self.redis.setex(processing_key, 3600, task_json)

            # Increment index for next round
            await self.redis.incr(self.ROUND_ROBIN_INDEX_KEY)

            return task
        else:
            # Queue empty - remove batch from active set
            await self.redis.srem(self.ACTIVE_BATCHES_KEY, selected_batch)
            # Retry recursively
            return await self.get_next_task_round_robin()

    def get_or_load_expense_leaves(self, batch_id: str, expense_pattern_dr : re.compile, expense_pattern_cr: re.compile) -> Tuple[Optional[list], Optional[List]]:
        """
        Get expense leaf nodes from cache or load from COA file.
        Cache is in-memory per batch. On server restart, reloads on-demand.
        """
        
        # Check cache first
        if batch_id in self.cr_ledger_name_cache:
            return self.cr_ledger_name_cache[batch_id]        
        # Check cache first
        if batch_id in self.dr_ledger_name_cache:
            return self.dr_ledger_name_cache[batch_id]

        # Load from COA file
        try:
            coa_data = self.file_handler.read_coa_json(batch_id)
            if not coa_data:
                logger.error(f"No COA data found for batch {batch_id}")
                return None, None
        

            hierarchy = coa_data.get("hierarchy", {})
            

            expenses_cr = None
            for key, value in hierarchy.items():
                if isinstance(key, str) and expense_pattern_cr.fullmatch(key.strip()):
                    expenses_cr = value
                    break

            if not expenses_cr:
                logger.error(f"No re section in COA for batch {batch_id}")
                return None, None       

            expenses_dr = None
            for key, value in hierarchy.items():
                if isinstance(key, str) and expense_pattern_dr.fullmatch(key.strip()):
                    expenses_dr = value
                    break

            if not expenses_dr:
                logger.error(f"No re section in COA for batch {batch_id}")
                return None, None

            leaf_nodes_cr = extract_expense_leaf_nodes(expenses_cr)

            # Cache in memory
            self.cr_ledger_name_cache[batch_id] = leaf_nodes_cr

            logger.info(f"📋 Loaded {len(leaf_nodes_dr)} : CR : expense ledgers for batch {batch_id}")


            # Extract leaf nodes
            leaf_nodes_dr = extract_expense_leaf_nodes(expenses_dr)

            # Cache in memory
            self.dr_ledger_name_cache[batch_id] = leaf_nodes_dr

            logger.info(f"📋 Loaded {len(leaf_nodes_dr)} : DR : expense ledgers for batch {batch_id}")
            return leaf_nodes_dr, leaf_nodes_cr

        except Exception as e:
            logger.error(f"Error loading expense leaves for batch {batch_id}: {str(e)}")
            return None, None

    def clear_dr_ledger_name_cache(self, batch_id: str):
        """Clear expense cache for a completed batch"""
        if batch_id in self.dr_ledger_name_cache:
            del self.dr_ledger_name_cache[batch_id]
            logger.info(f"🧹 Cleared dr cache for batch {batch_id}")
            
    def clear_cr_ledger_name_cache(self, batch_id: str):
        if batch_id in self.cr_ledger_name_cache:
            del self.cr_ledger_name_cache[batch_id]
            logger.info(f"🧹 Cleared cr cache for batch {batch_id}")

    async def worker_loop(self, worker_id: int):
        """Worker loop for processing Ollama tasks"""
        logger.info(f"Ollama Worker {worker_id} started")

        while True:
            try:
                # Get next task
                task = await self.get_next_task_round_robin()

                if task is None:
                    await asyncio.sleep(1)  # Wait if no tasks
                    continue

                logger.info(f"🤖 Ollama Worker {worker_id} processing: {task.filename}")

                # Update file status to ledger_processing
                self.session_manager.update_file_status(
                    task.batch_id,
                    task.filename,
                    "ledger_processing"
                )

                expense_pattern_dr = re.compile(r'(?i)\bexpense(s)?\b')
                # Specifically targets 'Liability' or 'Liabilities' (case-insensitive)
                expense_pattern_cr = re.compile(r'(?i)\bliabilit(y|ies)\b')
                
                # Get expense leaf nodes
                expense_leaves_dr, expense_leaves_cr = self.get_or_load_expense_leaves(task.batch_id, expense_pattern_dr, expense_leaves_cr)

                if not expense_leaves_dr or not expense_leaves_cr:
                    error_msg = "Failed to load expense ledgers from COA"
                    logger.error(f"❌ {error_msg} for {task.filename}")
                    self._handle_ollama_failure(task, error_msg)
                    await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")
                    continue

                # Generate prompts
                system_prompt_dr, user_prompt_dr = ledger_name_prompt_dr(
                    task.ledger_narration,
                    expense_leaves_dr
                )
                
                system_prompt_cr, user_prompt_cr = ledger_name_prompt_cr(
                    task.vendor_name,
                    task.ledger_narration,
                    expense_leaves_cr
                )

                # Call Ollama with retry
                success, ledger_name, confidence_score, error = await call_ollama_for_ledger(
                    system_prompt_dr,
                    user_prompt_dr,
                    get_pydantic_schema= custom_ledger(expense_leaves_dr)
                )
                

                if not success:
                    logger.error(f"❌ Ollama failed for {task.filename}: {error}")
                    self._handle_ollama_failure(task, error)
                    await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")
                    continue

                # Update XLOutputRow with selected ledger and confidence
                self._update_ledger_in_results(task, ledger_name, confidence_score)

                # Update file status to completed
                self.session_manager.update_file_status(
                    task.batch_id,
                    task.filename,
                    "completed"
                )

                logger.info(
                    f"✅ Ollama Worker {worker_id} completed: {task.filename} → "
                    f"{ledger_name} (confidence: {confidence_score:.2f})"
                )

                # Check if batch is complete
                self._check_batch_completion(task.batch_id)

                # Cleanup
                await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")

            except Exception as e:
                logger.error(f"Ollama Worker {worker_id} error: {str(e)}")
                await asyncio.sleep(1)

    def _handle_ollama_failure(self, task: OllamaTask, error_msg: str):
        """Handle Ollama task failure - set ledger_name to FAILED and confidence to 0.0"""
        session = self.session_manager.get_session(task.batch_id)
        if not session:
            return

        # Get processed results
        processed_results = session.get("processed_results", [])
        if isinstance(processed_results, str):
            processed_results = json.loads(processed_results)

        # Find and update the result for this file
        for result in processed_results:
            if result["filename"] == task.filename:
                # Update XL output ledger_name to FAILED
                if result.get("xl_output"):
                    for xl_row in result["xl_output"]:
                        xl_row["ledger_name"] = "FAILED"
                        xl_row["confidence_score"] = 0.0

                # Update session
                self.session_manager.update_session(
                    task.batch_id,
                    {"processed_results": processed_results}
                )
                break

        # Mark file as completed (with FAILED ledger)
        self.session_manager.update_file_status(
            task.batch_id,
            task.filename,
            "completed"
        )

    def _update_ledger_in_results(self, task: OllamaTask, ledger_name: str, confidence_score: float):
        """Update the ledger_name and confidence_score in XLOutputRow for the processed invoice"""
        session = self.session_manager.get_session(task.batch_id)
        if not session:
            return

        # Get processed results
        processed_results = session.get("processed_results", [])
        if isinstance(processed_results, str):
            processed_results = json.loads(processed_results)

        # Find and update the result for this file
        for result in processed_results:
            if result["filename"] == task.filename:
                # Update XL output ledger_name and confidence_score
                if result.get("xl_output"):
                    for xl_row in result["xl_output"]:
                        xl_row["ledger_name"] = ledger_name
                        xl_row["confidence_score"] = confidence_score

                # Update session
                self.session_manager.update_session(
                    task.batch_id,
                    {"processed_results": processed_results}
                )

                logger.info(f"✅ Updated ledger_name to '{ledger_name}' (confidence: {confidence_score:.2f}) for {task.filename}")
                break

    def _check_batch_completion(self, batch_id: str):
        """Check if all files in batch are completed and clear cache"""
        session = self.session_manager.get_session(batch_id)
        if not session:
            return

        files = session.get("files", [])
        if isinstance(files, str):
            files = json.loads(files)

        all_completed = all(f["status"] in ["completed", "failed"] for f in files)

        if all_completed:
            logger.info(f"🎉 Batch {batch_id} fully completed (Mistral + Ollama)")
            self.session_manager.update_session(batch_id, {
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat()
            })

            # Clear expense cache
            self.clear_dr_ledger_name_cache(batch_id)
            self.clear_cr_ledger_name_cache(batch_id)

    async def start_workers(self, num_workers: int):
        """Start worker pool"""
        logger.info(f"Starting {num_workers} Ollama workers")

        for worker_id in range(1, num_workers + 1):
            asyncio.create_task(self.worker_loop(worker_id))

        logger.info(f"✅ All {num_workers} Ollama workers started")

    async def recover_crashed_tasks(self):
        """Recover tasks that were processing when server crashed"""
        processing_pattern = f"{self.PROCESSING_PREFIX}*"
        cursor = 0
        processing_keys = []

        while True:
            cursor, keys = await self.redis.scan(cursor, match=processing_pattern, count=100)
            processing_keys.extend(keys)
            if cursor == 0:
                break

        for key in processing_keys:
            task_json = await self.redis.get(key)
            if task_json:
                task = OllamaTask.model_validate_json(task_json)

                # Mark as failed
                self.session_manager.update_file_status(
                    task.batch_id,
                    task.filename,
                    "failed",
                    error="Server restarted during Ollama processing"
                )

                # Cleanup
                await self.redis.delete(key)

        if processing_keys:
            logger.warning(f"Recovered {len(processing_keys)} crashed Ollama tasks")


# Singleton instance
_ollama_queue_manager = None


def get_ollama_queue_manager() -> OllamaTaskQueueManager:
    """Get or create OllamaTaskQueueManager singleton"""
    global _ollama_queue_manager
    if _ollama_queue_manager is None:
        _ollama_queue_manager = OllamaTaskQueueManager()
    return _ollama_queue_manager
