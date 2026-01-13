# Refactor Task Queue Architecture for Parallel Processing

## Problem Summary

Currently, the invoice processing pipeline is sequential and blocking:
- 5 workers process invoices one-at-a-time through: Mistral OCR → Ollama Expense → Ollama Vendor → Assembly
- Workers block waiting for Ollama responses (60s each), preventing new Mistral OCR tasks from starting
- Two Ollama API calls run sequentially even though they're independent
- With 20 invoices, this causes significant delays due to worker starvation

## Solution Architecture

Convert to a 3-stage pipeline with independent task queues:

```
Main Task Queue → Mistral Queue → Main Task Queue → Ollama Queue (parallel) → Assembly
     (100 workers)     (5 workers)      (100 workers)       (3 workers)      (in main)
```

### Key Benefits:
1. **Mistral never blocks**: Main workers immediately move to next invoice after enqueuing to Mistral
2. **Parallel Ollama calls**: Expense + Vendor ledger selection happen simultaneously
3. **Fair scheduling**: Round-robin across all batches maintained in each queue
4. **Clean separation**: Each queue manages its own concurrency and rate limiting
5. **File-based intermediate storage**: Mistral OCR results saved as JSON files (not Redis), reducing memory pressure

---

## Implementation Plan

### 1. Create New File: `services/mistral_queue.py`

**Purpose**: Dedicated task queue for Mistral OCR processing

**Components**:

#### A. Pydantic Models
```python
class MistralRequest(BaseModel):
    task_id: str           # UUID
    batch_id: str
    filename: str
    pdf_path: str
    enqueued_at: str       # ISO timestamp

class MistralResponse(BaseModel):
    task_id: str
    batch_id: str
    success: bool
    json_path: Optional[str]        # Path to saved JSON file (e.g., /tmp/invoice_uploads/batch-123/invoice.json)
    error_message: Optional[str]
    processed_at: str
```

#### B. MistralQueueManager Class

**Redis Key Structure**:
```
mistral_queue:pending:{batch_id}      # List - pending OCR tasks
mistral_queue:active_batches          # Set - batches with pending tasks
mistral_queue:round_robin_index       # String - RR index
mistral_queue:processing:{task_id}    # String - task in progress (1-hour TTL)
mistral_queue:response:{task_id}      # String - Lightweight metadata (success, json_path, error) (1-hour TTL)
```

**File Structure** (NEW):
```
/tmp/invoice_uploads/batch-123/
├── COA/
│   └── coa.pdf
├── invoice-001.pdf
├── invoice-001.json          # Mistral output (InvoiceData) - intermediate artifact
├── invoice-001_result.json   # Final result with XL rows (unchanged from current)
├── invoice-002.pdf
├── invoice-002.json          # Mistral output
├── invoice-002_result.json
...
```

**Methods**:
- `__init__(redis_client, invoice_parser, file_handler)` - Initialize with Redis, parser, and file handler
- `enqueue_request(batch_id, filename, pdf_path) -> str` - Add to queue, return task_id
- `get_next_task_round_robin() -> Optional[MistralRequest]` - Round-robin scheduling
- `worker_loop(worker_id)` - Main processing loop:
  1. Get next task (round-robin)
  2. Call `invoice_parser.parse_invoice_with_mistral(pdf_path)`
  3. **Save InvoiceData to JSON file** at `{pdf_path.replace('.pdf', '.json')}`
  4. Store lightweight MistralResponse in Redis with json_path
  5. Remove from processing set
  6. Handle errors: store failed response with error message
- `wait_for_response(task_id, timeout=120) -> MistralResponse` - Poll for result (500ms interval)
- `start_workers(num_workers=5)` - Start worker pool
- `stop_workers()` - Graceful shutdown
- `recover_crashed_tasks()` - Startup recovery for processing:* keys

**Key Details**:
- Uses `MAX_MISTRAL_CONCURRENT` from config (5 workers)
- Reuses `parse_invoice_with_mistral()` from [invoice_parser.py](bknd/services/invoice_parser.py) (no changes needed)
- Same round-robin pattern as [task_queue.py](bknd/services/task_queue.py)
- **Stores file path in Redis (not full InvoiceData)** - reduces Redis memory usage
- InvoiceData JSON files are intermediate artifacts (cleaned up with batch directory)

---

### 2. Update `config.py`

**Add new configuration**:
```python
# Worker Pool Sizes
MAX_MAIN_WORKERS = 100              # Main task queue workers (high limit)
MAX_MISTRAL_CONCURRENT = 5          # Already exists
MAX_OLLAMA_CONCURRENT_CALLS = 3     # Already exists
```

**No changes to**:
- `mistral_semaphore` (stays in invoice_parser.py)
- `ollama_semaphore` (stays in ollama_api_call.py)
- Other existing configs

---

### 3. Refactor `services/task_queue.py`

**Major Changes to `worker_loop()` method**:

#### Current Flow (Lines 176-446):
```python
# Sequential blocking flow
task = get_next_task()
update_status("processing")

# BLOCKS HERE (5-10s)
success, invoice_data, error = await invoice_parser.parse_invoice_with_mistral(pdf_path)

# BLOCKS HERE (60s)
expense_ledger = await ollama_queue.wait_for_response(expense_task_id, 60)

# BLOCKS HERE (60s)
vendor_ledger = await ollama_queue.wait_for_response(vendor_task_id, 60)

generate_xl_output()
save_results()
```

#### New Flow:
```python
async def worker_loop(self, worker_id: int):
    """
    Main invoice processing workflow.
    Mistral OCR → Ollama ledger selections → Multi-row XL generation → Save
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

            # ===== STEP 1: Enqueue to Mistral Queue (NON-BLOCKING) =====
            mistral_task_id = await self.mistral_queue.enqueue_request(
                batch_id=task.batch_id,
                filename=task.filename,
                pdf_path=task.pdf_path
            )

            # ===== STEP 2: Wait for Mistral Result (BLOCKING) =====
            try:
                mistral_response = await self.mistral_queue.wait_for_response(
                    task_id=mistral_task_id,
                    timeout=120  # 2 minutes
                )
            except TimeoutError:
                logger.error(f"Worker {worker_id} Mistral OCR timeout for {task.filename}")
                self.session_manager.update_file_status(
                    task.batch_id,
                    task.filename,
                    "failed",
                    error="Mistral OCR timeout"
                )
                await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")
                continue

            if not mistral_response.success:
                logger.error(f"Worker {worker_id} Mistral OCR failed for {task.filename}: {mistral_response.error_message}")
                self.session_manager.update_file_status(
                    task.batch_id,
                    task.filename,
                    "failed",
                    error=f"Mistral OCR failed: {mistral_response.error_message}"
                )
                await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")
                continue

            # Load InvoiceData from JSON file
            json_path = mistral_response.json_path
            try:
                async with aiofiles.open(json_path, 'r') as f:
                    json_content = await f.read()
                invoice_dict = json.loads(json_content)
                invoice_data = InvoiceData.model_validate(invoice_dict)
            except Exception as e:
                logger.error(f"Worker {worker_id} failed to load invoice data from {json_path}: {e}")
                self.session_manager.update_file_status(
                    task.batch_id,
                    task.filename,
                    "failed",
                    error=f"Failed to load invoice data: {e}"
                )
                await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")
                continue

            logger.info(f"Worker {worker_id} Mistral OCR completed for {task.filename}")

            # ===== STEP 2a: Prepare Expense Ollama Request =====
            expense_pattern = re.compile(r'(?i)\bexpense(s)?\b')
            expense_ledgers = self.get_or_load_expense_leaves(task.batch_id, expense_pattern)
            narration = XLOutputGenerator.concatenate_line_items(invoice_data.line_items)

            custom_schema_expense = custom_ledger(expense_ledgers)
            expense_ledgers.append(NOT_FOUND)

            expense_system_prompt, expense_user_prompt = ledger_name_prompt_dr(
                ledger_narration=narration,
                expense_leaf_nodes=expense_ledgers
            )

            # ===== STEP 2b: Prepare Vendor Ollama Request =====
            liability_pattern = re.compile(r'(?i)\bliabilit(y|ies)\b')
            liability_ledgers = self.get_or_load_expense_leaves(task.batch_id, liability_pattern)
            vendor_name = invoice_data.header.vendor_name

            custom_schema_liability = custom_ledger(liability_ledgers)
            liability_ledgers.append(NOT_FOUND)

            vendor_system_prompt, vendor_user_prompt = ledger_name_prompt_cr(
                vendor_name=vendor_name,
                invoice_description=narration,
                liability_leaf_nodes=liability_ledgers
            )

            # ===== STEP 3: Enqueue BOTH Ollama Tasks (NON-BLOCKING) =====
            expense_task_id = await self.ollama_queue.enqueue_request(
                batch_id=task.batch_id,
                system_prompt=expense_system_prompt,
                user_prompt=expense_user_prompt,
                metadata={
                    "type": "expense_selection",
                    "filename": task.filename,
                    "pydantic_json_schema": custom_schema_expense.model_json_schema()
                }
            )

            vendor_task_id = await self.ollama_queue.enqueue_request(
                batch_id=task.batch_id,
                system_prompt=vendor_system_prompt,
                user_prompt=vendor_user_prompt,
                metadata={
                    "type": "vendor_selection",
                    "filename": task.filename,
                    "pydantic_json_schema": custom_schema_liability.model_json_schema()
                }
            )

            # ===== STEP 4: Wait for BOTH Ollama Results in Parallel =====
            # Use gather with return_exceptions=True to handle individual failures
            results = await asyncio.gather(
                self.ollama_queue.wait_for_response(expense_task_id, timeout=60),
                self.ollama_queue.wait_for_response(vendor_task_id, timeout=60),
                return_exceptions=True
            )

            expense_response = results[0]
            vendor_response = results[1]

            # ===== STEP 5: Parse Expense Ledger (with fallback) =====
            if isinstance(expense_response, Exception) or isinstance(expense_response, TimeoutError):
                logger.error(f"Worker {worker_id} Ollama timeout/error for expense ledger: {task.filename}")
                expense_ledger_name = "Suspended AC"
                expense_confidence = 0.0
            elif not expense_response.success:
                logger.error(f"Worker {worker_id} No Ledger name for Expense through Ollama")
                expense_ledger_name = "Suspended AC"
                expense_confidence = 0.0
            else:
                expense_ledger_name, expense_confidence = self._parse_ledger_response(
                    expense_response.response_text, expense_ledgers,
                    get_pydantic_schema=custom_schema_expense
                )
                if not expense_ledger_name:
                    expense_ledger_name = "Suspended AC"
                    expense_confidence = 0.0

            logger.info(f"Worker {worker_id} Expense ledger: {expense_ledger_name} (confidence: {expense_confidence:.2f})")

            # ===== STEP 6: Parse Vendor Ledger (with fallback) =====
            if isinstance(vendor_response, Exception) or isinstance(vendor_response, TimeoutError):
                logger.error(f"Worker {worker_id} Ollama timeout/error for vendor ledger: {task.filename}")
                vendor_ledger_name = "Suspended AC"
                vendor_confidence = 0.0
            elif not vendor_response.success:
                logger.error(f"Worker {worker_id} No Ledger name for Vendor through Ollama")
                vendor_ledger_name = "Suspended AC"
                vendor_confidence = 0.0
            else:
                vendor_ledger_name, vendor_confidence = self._parse_ledger_response(
                    vendor_response.response_text, liability_ledgers,
                    get_pydantic_schema=custom_schema_liability
                )
                if not vendor_ledger_name:
                    vendor_ledger_name = "Suspended AC"
                    vendor_confidence = 0.0

            logger.info(f"Worker {worker_id} Vendor ledger: {vendor_ledger_name} (confidence: {vendor_confidence:.2f})")

            # ===== STEP 7: Generate Multi-Row XL Output =====
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

            # ===== STEP 8: Save Results =====
            # Note: invoice JSON already saved by mistral_queue (intermediate artifact)
            # This saves the FINAL result with XL rows
            json_path = self.file_handler.get_json_path(task.batch_id, task.filename)

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
            import traceback
            logger.error(f"Worker {worker_id} error: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")

            # Update file status to failed if we have task info
            if task:
                self.session_manager.update_file_status(
                    task.batch_id,
                    task.filename,
                    "failed",
                    error=str(e)
                )

                # Clean up processing task from Redis
                await self.redis.delete(f"{self.PROCESSING_PREFIX}{task.task_id}")

            await asyncio.sleep(1)
```

**Changes to `__init__` method**:
```python
def __init__(self, redis_client, session_manager, file_handler, mistral_queue, ollama_queue):
    self.redis = redis_client
    self.session_manager = session_manager
    self.file_handler = file_handler
    self.mistral_queue = mistral_queue    # NEW
    self.ollama_queue = ollama_queue
    self.running = False
    self.workers = []
```

**Changes to `start_workers` method**:
```python
async def start_workers(self, num_workers: int = 100):  # Changed from 5 to 100
    """Start main worker pool"""
    self.running = True
    for i in range(num_workers):
        worker_task = asyncio.create_task(self.worker_loop(i))
        self.workers.append(worker_task)
    logger.info(f"Started {num_workers} main task queue workers")
```

**No changes to**:
- `enqueue_file()` - Still adds to main queue
- `get_next_task_round_robin()` - Still does round-robin
- `recover_crashed_tasks()` - Still recovers main queue
- `stop_workers()` - Still graceful shutdown

---

### 4. Update `main.py`

**Changes to imports** (around Line 15):
```python
from services.task_queue import TaskQueueManager
from services.mistral_queue import MistralQueueManager  # NEW
from services.ollama_queue import OllamaQueueManager
from services.invoice_parser import InvoiceParser
```

**Changes to initialization** (around Line 55):
```python
# Initialize invoice parser (unchanged)
invoice_parser = InvoiceParser()

# Initialize Mistral queue (NEW)
mistral_queue = MistralQueueManager(
    redis_client=redis_client,
    invoice_parser=invoice_parser,
    file_handler=file_handler  # NEW: for saving JSON files
)

# Initialize Ollama queue (unchanged)
ollama_queue = OllamaQueueManager(redis_client=redis_client)

# Initialize main task queue (add mistral_queue parameter)
task_queue = TaskQueueManager(
    redis_client=redis_client,
    session_manager=session_manager,
    file_handler=file_handler,
    mistral_queue=mistral_queue,  # NEW
    ollama_queue=ollama_queue
)
```

**Changes to startup event** (Lines 481-510):
```python
@app.on_event("startup")
async def startup_event():
    logger.info("Starting up application...")

    # Health check Ollama
    await health_check_ollama()

    # Recover crashed tasks from all queues
    await task_queue.recover_crashed_tasks()
    await mistral_queue.recover_crashed_tasks()  # NEW
    # Note: Ollama queue doesn't have crashed task recovery

    # Start worker pools
    await mistral_queue.start_workers(num_workers=MAX_MISTRAL_CONCURRENT)  # NEW: 5 workers
    await task_queue.start_workers(num_workers=MAX_MAIN_WORKERS)            # NEW: 100 workers (was 5)

    # Start Ollama workers (unchanged)
    for i in range(MAX_OLLAMA_CONCURRENT_CALLS):
        asyncio.create_task(ollama_queue.worker_loop(i))

    # Start background cleanup (unchanged)
    asyncio.create_task(cleanup_old_batches())

    logger.info("Application startup complete")
```

**Changes to shutdown event** (Lines 512-520):
```python
@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down application...")

    # Stop all worker pools
    await task_queue.stop_workers()
    await mistral_queue.stop_workers()  # NEW
    await ollama_queue.stop_workers()

    logger.info("Application shutdown complete")
```

**Import new config** (around Line 8):
```python
from config import (
    MAX_MISTRAL_CONCURRENT,
    MAX_OLLAMA_CONCURRENT_CALLS,
    MAX_MAIN_WORKERS,  # NEW
    REDIS_ENABLED,
    redis_client,
    # ... other imports
)
```

---

### 5. No Changes Required

**Files that remain unchanged**:
- [invoice_parser.py](bknd/services/invoice_parser.py) - `parse_invoice_with_mistral()` used as-is by mistral_queue
- [ollama_queue.py](bknd/services/ollama_queue.py) - Already has `enqueue_request()` and `wait_for_response()`
- [ollama_api_call.py](bknd/services/ollama_api_call.py) - Ollama API wrapper unchanged
- [xl_output_generator.py](bknd/services/xl_output_generator.py) - XL generation logic unchanged
- [models.py](bknd/models.py) - Add new models (MistralRequest/Response) but existing models unchanged
- [session_manager.py](bknd/services/session_manager.py) - Session management unchanged
- [file_handler.py](bknd/services/file_handler.py) - File I/O unchanged

---

## Critical Files to Modify

1. **NEW**: [services/mistral_queue.py](bknd/services/mistral_queue.py) - Complete new file (~400 lines)
2. **MODIFY**: [services/task_queue.py](bknd/services/task_queue.py) - Refactor `worker_loop()` method (Lines 176-446)
3. **MODIFY**: [config.py](bknd/config.py) - Add `MAX_MAIN_WORKERS = 100`
4. **MODIFY**: [models.py](bknd/models.py) - Add `MistralRequest` and `MistralResponse` models
5. **MODIFY**: [main.py](bknd/main.py) - Update initialization and startup/shutdown events

---

## Data Flow Comparison

### Before (Sequential):
```
Client uploads 20 PDFs
    ↓
Main Queue enqueues 20 tasks
    ↓
5 Workers process sequentially:
  Worker 1: Task A → Mistral (10s) → Ollama-Expense (60s) → Ollama-Vendor (60s) → Assembly (1s)
  Worker 2: Task B → [same 131s]
  Worker 3: Task C → [same 131s]
  Worker 4: Task D → [same 131s]
  Worker 5: Task E → [same 131s]

Tasks F-T: Wait for worker availability
Total Time: ~525 seconds (8.75 minutes)
```

### After (Parallel):
```
Client uploads 20 PDFs
    ↓
Main Queue enqueues 20 tasks (100 workers available)
    ↓
100 Main Workers immediately:
  - Enqueue all 20 tasks to Mistral Queue (non-blocking)
  - Wait for Mistral responses
    ↓
5 Mistral Workers process:
  - Task A (10s) → Store result → Done
  - Task B (10s) → Store result → Done
  - [Process all 20 in batches of 5]
  - Total Mistral time: 40s (4 batches × 10s)
    ↓
Main Workers receive Mistral results:
  - Enqueue 40 Ollama tasks (20 expense + 20 vendor) in parallel
    ↓
3 Ollama Workers process:
  - Tasks processed in batches of 3
  - Each task: 60s
  - Total Ollama time: 400s (40 tasks ÷ 3 workers × 60s)
    ↓
Main Workers receive Ollama results:
  - Generate XL output
  - Save results
  - Total Assembly time: 20s

Total Time: ~460 seconds (7.6 minutes)
Plus: All Mistral calls complete in 40s instead of being spread out
Plus: Ollama calls run in parallel per invoice (2 × 60s = 60s, not 120s)
```

**Actual improvement**: With parallel Ollama calls per invoice, we save 60s per invoice:
- Old: 20 invoices × 131s = 2,620s (43.6 min)
- New: 40s (Mistral) + 400s (Ollama) + 20s (Assembly) = 460s (7.6 min)
- **Speedup: ~5.7x faster**

---

## Verification Steps

### 1. Unit Tests (Manual)
```bash
# Test Mistral queue in isolation
cd /home/soham/Documents/orbtl/bknd
python -c "
from services.mistral_queue import MistralQueueManager
from config import redis_client
from services.invoice_parser import InvoiceParser

parser = InvoiceParser()
mq = MistralQueueManager(redis_client, parser)

# Test enqueue
task_id = await mq.enqueue_request('batch-123', 'test.pdf', '/path/to/test.pdf')
print(f'Enqueued: {task_id}')

# Test round-robin
task = await mq.get_next_task_round_robin()
print(f'Got task: {task}')
"
```

### 2. Integration Test
```bash
# Use existing test.sh script
cd /home/soham/Documents/orbtl/bknd
bash test.sh

# Monitor logs
tail -f logs/app.log | grep -E "(Mistral|Ollama|Worker)"
```

### 3. End-to-End Test with 20 PDFs
```bash
# Create test session
SESSION_ID=$(curl -X POST http://localhost:8000/api/sessions | jq -r '.batch_id')

# Upload COA + 20 invoices
curl -X POST "http://localhost:8000/api/sessions/$SESSION_ID/upload" \
  -F "coa=@path/to/coa.pdf" \
  -F "files=@invoice1.pdf" \
  -F "files=@invoice2.pdf" \
  # ... (add 20 invoices)

# Poll status and measure time
START_TIME=$(date +%s)
while true; do
  STATUS=$(curl -s "http://localhost:8000/api/sessions/$SESSION_ID/status")
  COMPLETED=$(echo $STATUS | jq '.files | map(select(.status=="completed")) | length')
  echo "Completed: $COMPLETED/20"

  if [ "$COMPLETED" -eq 20 ]; then
    END_TIME=$(date +%s)
    ELAPSED=$((END_TIME - START_TIME))
    echo "All files processed in $ELAPSED seconds"
    break
  fi

  sleep 5
done
```

### 4. Redis Monitoring
```bash
# Monitor queue sizes
redis-cli
> LLEN mistral_queue:pending:batch-123
> LLEN queue:pending:batch-123
> SMEMBERS mistral_queue:active_batches
> SMEMBERS queue:active_batches
> KEYS mistral_queue:response:*
> KEYS ollama_queue:response:*
```

### 5. Performance Metrics to Track
- Time from upload to first Mistral completion
- Time from first Mistral completion to first Ollama completion
- Total time for batch completion
- Redis memory usage
- Worker CPU usage
- Number of concurrent Mistral/Ollama calls

---

## Risk Mitigation

### Risk 1: Redis Memory Pressure
**Issue**: Storing 40 Ollama responses + metadata in Redis

**Mitigation**:
- All response keys have 1-hour TTL (auto-cleanup)
- **File-based storage**: Mistral responses (InvoiceData) saved to JSON files instead of Redis
- Only lightweight metadata stored in Redis (~200 bytes per response: `{success, json_path, error}`)
- Significantly reduces Redis memory footprint (from ~10KB to ~200B per Mistral result)
- Monitor Redis memory usage in production

### Risk 2: Worker Pool Overhead
**Issue**: 100 main workers might consume too much memory

**Mitigation**:
- Each worker is lightweight (async/await, not threads)
- Most workers will be idle waiting for queue results
- Can tune MAX_MAIN_WORKERS down if needed (start with 100, reduce if issues)

### Risk 3: Mistral Rate Limiting
**Issue**: Concurrent API calls might hit rate limits

**Mitigation**:
- Semaphore already limits to 5 concurrent calls
- Existing retry logic with exponential backoff handles transient errors
- No change to Mistral concurrency (still 5)

### Risk 4: Ollama Queue Backlog
**Issue**: 40 Ollama tasks might overwhelm the queue

**Mitigation**:
- Ollama queue already handles round-robin fairly
- 3 workers process continuously
- If backlog grows, consider increasing MAX_OLLAMA_CONCURRENT_CALLS

### Risk 5: Main Worker Starvation
**Issue**: All 100 workers waiting on Mistral/Ollama

**Mitigation**:
- This is expected behavior (workers are async, not blocking CPU)
- Async/await allows thousands of concurrent waiters efficiently
- Memory footprint is minimal per waiting task

---

## Rollback Plan

If issues arise:

1. **Quick rollback**:
   - Set `MAX_MAIN_WORKERS = 5` in config.py
   - Comment out mistral_queue initialization in main.py
   - Revert task_queue.py worker_loop to call invoice_parser directly

2. **Gradual rollout**:
   - Start with `MAX_MAIN_WORKERS = 10`
   - Monitor for 24 hours
   - Increase to 50, then 100 if stable

3. **Feature flag**:
   - Add `USE_MISTRAL_QUEUE = True/False` to config.py
   - Keep both code paths in task_queue.py
   - Switch based on flag

---

## Success Metrics

- **Processing time**: 20 invoices complete in <8 minutes (vs current ~45 minutes)
- **Resource usage**: Redis memory < 100MB for 20-invoice batch
- **Error rate**: <5% failed tasks (same as current)
- **Fairness**: Multiple batches process fairly via round-robin
- **Recovery**: Crashed tasks recover on restart (all queues)
