# ORBTL Backend — Task Queue Deep Dive

## "I ran `uvicorn main:app`. Now what?"

---

## 1. Startup Sequence (`main.py` → `startup_event`)

Before the server even accepts requests, these happen **in order**:

```
Module load (before startup_event):
  session_manager  = get_session_manager()      # RedisSessionManager or InMemSessionManager
  file_handler     = FileHandler()
  invoice_parser   = InvoiceParser()
  mistral_queue    = MistralQueueManager(redis_client, invoice_parser, file_handler)
  llm_queue        = get_llm_queue()            # LLMQueue singleton
  task_queue       = TaskQueueManager(redis_client, mistral_queue, llm_queue,
                                      session_manager, file_handler)

startup_event():
  1. health_check_bedrock()                  # Converse API test call
  2. dr_prompt_file.seed_from_file()         # SET config:prompt:dr  if absent (from prompts/dr_prompt.txt)
     cr_prompt_file.seed_from_file()         # SET config:prompt:cr  if absent
     tds_prompt_file.seed_from_file()        # SET config:prompt:tds if absent
     tds_file.seed_from_file()              # SET config:tds_rates  if absent (from data/tds_rates.json)
  3. MANAGER.load_data()                     # reads config:tds_rates from Redis → self.data in memory
  4. task_queue.recover_crashed_tasks()      # SCAN queue:processing:* → mark failed
  5. mistral_queue.recover_crashed_tasks()   # SCAN mistral_queue:processing:* → mark failed
  6. mistral_queue.start_workers(5)          # → 5 asyncio tasks
  7. task_queue.start_workers(100)           # → 100 asyncio tasks
  8. for i in range(3): llm_queue.worker_loop()  # → 3 asyncio tasks
  9. cleanup_old_batches()                   # → 1 asyncio task (runs every 1h)
```

---

## 2. Workers Initialized

| Class | Count | Method | Polls Redis Key |
|-------|------:|-------|----------------|
| `TaskQueueManager` | **100** | `worker_loop(worker_id)` | `queue:pending:{batch_id}` |
| `MistralQueueManager` | **5** | `worker_loop(worker_id)` | `mistral_queue:pending:{batch_id}` |
| `LLMQueue` | **3** | `worker_loop()` | `llm_queue:pending:{batch_id}` |
| cleanup | 1 | `cleanup_old_batches()` | — |

**Total: 109 long-running asyncio tasks**, all started at startup. They loop forever (poll → sleep(1) when empty).

The 100 main workers and 3 LLM workers aren't tracked (fire-and-forget `asyncio.create_task`).
The 5 Mistral workers are tracked in `self.workers` list and stopped gracefully on shutdown.

### Rate-Limiting (semaphores in config.py)
```python
mistral_semaphore = asyncio.Semaphore(5)   # Max 5 concurrent Mistral API calls
bedrock_semaphore = asyncio.Semaphore(5)   # Max 5 concurrent Bedrock API calls
ollama_semaphore  = asyncio.Semaphore(3)   # Max 3 concurrent Ollama HTTP calls (if used)
```
These gate the actual external API calls inside the provider modules, independent of worker counts.

---

## 3. LLM Provider System

The LLM queue is provider-agnostic. The active provider is controlled by a single env var:

```
LLM_PROVIDER=bedrock    # default
LLM_PROVIDER=ollama
LLM_PROVIDER=openai     # add to llm_client.py to enable
```

```
services/
  llm_client.py        ← dispatcher: call_llm(system, user, json_schema) → (str, bool)
  bedrock.py           ← AWS Bedrock via Converse API → (str, bool)
  ollama_api_call.py   ← local Ollama → (str, bool)
  llm_queue.py         ← queue, calls call_llm(), knows nothing about provider
```

Every provider normalizes its return to `(str, bool)` — response text + success flag. No dicts.

To add a new provider: create `services/<name>.py` returning `(str, bool)`, add one `elif` in `llm_client.py`.

---

## 4. Request Flow: `POST /api/sessions`

```
Client → FastAPI → session_manager.create_session()
       ← batch_id (UUID)
```

Session stored in Redis as a hash with 4h TTL. No workers involved.

---

## 5. Request Flow: `POST /api/sessions/{batch_id}/upload`

Receives: 1 COA PDF + N invoice PDFs (max 20).

```
① COA Processing (SYNCHRONOUS — blocks the request until done):
   coa_parsing(batch_id, coa_content, coa_filename)
     → file_handler.save_coa_file()          # write to /tmp/invoice_uploads/{batch_id}/COA/
     → parse_coa(coa_pdf_path)               # pdfplumber + PyMuPDF → COAOutput
     → open(coa_json_path).write(json)       # save coa.json to disk
     → session_manager.update_coa_status()   # mark "parsed" in Redis

② For each invoice PDF:
   file_handler.save_file()                  # write PDF to /tmp/invoice_uploads/{batch_id}/
   session_manager.add_file_to_session()     # add file record (status="pending")
   await task_queue.enqueue_file()
     → redis.rpush("queue:pending:{batch_id}", TaskItem JSON)
     → redis.sadd("queue:active_batches", batch_id)

③ session_manager.update_session(status="processing")
← HTTP 202 response (processing started)
```

At this point the HTTP request is done. Everything else is async background.

---

## 6. Task Flow: `TaskQueueManager.worker_loop` (100 workers)

One of the 100 idle workers wakes and picks up the task:

```
STEP 0: get_next_task_round_robin()
  → redis.smembers("queue:active_batches")     # get all active batch IDs
  → redis.get("queue:round_robin_index")       # current position
  → redis.lpop("queue:pending:{batch_id}")     # FIFO pop
  → redis.setex("queue:processing:{task_id}", 3600, task_json)
  → redis.incr("queue:round_robin_index")

STEP 1: session_manager.update_file_status(status="processing")

STEP 2: await mistral_queue.enqueue_request(batch_id, filename, pdf_path)
  → redis.rpush("mistral_queue:pending:{batch_id}", MistralRequest JSON)
  → redis.sadd("mistral_queue:active_batches", batch_id)
  ← mistral_task_id (UUID)
  [** main worker DOES NOT call Mistral directly — it hands off and waits **]

STEP 3: await mistral_queue.wait_for_response(mistral_task_id, timeout=120)
  → polls redis.get("mistral_queue:response:{mistral_task_id}") every 500ms
  [BLOCKING — up to 120 seconds]
  ← MistralResponse (success=True, json_path=".../filename.json")

STEP 4: aiofiles.open(json_path) → InvoiceData.model_validate(json)
  [Read parsed invoice from disk — NO API call]

STEP 5: Load COA data (synchronous, from disk)
  get_or_load_expense_leaves()
    → file_handler.read_coa_json(batch_id)     # read coa.json from disk
    → regex match for "expenses?" in hierarchy keys
    → extract_expense_leaf_nodes()             # all leaf names under Expenses

  get_sundry_creditor_ledgers()
    → file_handler.read_coa_json(batch_id)     # read coa.json again
    → regex match "liabilit(y|ies)" → then "creditor(s)?"
    → extract_leaf_nodes()

  MANAGER.get_all_transaction_natures()        # from in-memory self.data (seeded from Redis at startup)

STEP 6: Prepare 3 prompts
  ledger_name_prompt_dr(narration, expense_ledgers)              # Dr: expense selection
  ledger_name_prompt_cr(vendor_name, narration, creditor_ledgers) # Cr: vendor selection
  tds_nature_prompt(vendor_name, narration, tds_options)          # TDS nature

STEP 7: Enqueue all 3 to LLM queue (NON-BLOCKING)
  llm_queue.enqueue_request(expense_...)  → redis.rpush("llm_queue:pending:{batch_id}", ...)
  llm_queue.enqueue_request(vendor_...)   → redis.rpush("llm_queue:pending:{batch_id}", ...)
  llm_queue.enqueue_request(tds_...)      → redis.rpush("llm_queue:pending:{batch_id}", ...)

STEP 8: asyncio.gather(wait_for_response × 3, timeout=60 each)
  → polls redis.get("llm_queue:response:{task_id}") every 500ms for each
  [BLOCKING on all 3 simultaneously — up to 60 seconds]
  ← (expense_response, vendor_response, tds_response)

STEP 9: _parse_ledger_response() × 3
  try: Pydantic model_validate_json(response_text)    # strict parse
  fallback: json.loads() → extract "ledger" key
  fallback: fuzzy match against valid_ledgers list
    exact            → 0.95 confidence
    case-insensitive → 0.90 confidence
    partial          → 0.75 confidence
    NOT_FOUND        → 0.0  confidence

STEP 10: _get_next_voucher_number(batch_id)
  → redis.incr("voucher_counter:{batch_id}")   # atomic
  ← voucher_number (1, 2, 3...)

STEP 11: XLOutputGenerator.generate_xl_output_rows(...)
  [pure in-memory, no I/O]
  generates 4–6 XLOutputRow objects (Dr expense, Dr CGST/SGST/IGST, Cr TDS?, Cr vendor)

STEP 12: file_handler.save_json_result()
  → write invoice_data dict to {batch_id}/{filename}.json

STEP 13: session_manager.update_session(processed_results += result)
  → redis HGET "processed_results" → parse JSON → append → HSET

STEP 14: session_manager.update_file_status(status="success")

STEP 15: redis.delete("queue:processing:{task_id}")
```

---

## 7. Task Flow: `MistralQueueManager.worker_loop` (5 workers)

Triggered by Step 2 above. One of 5 Mistral workers picks it up:

```
get_next_task_round_robin()
  → redis.smembers("mistral_queue:active_batches")
  → redis.lpop("mistral_queue:pending:{batch_id}")
  → redis.setex("mistral_queue:processing:{task_id}", 3600, ...)

invoice_parser.parse_invoice(pdf_path)
  [ACTUAL MISTRAL API CALL — 1 external HTTP call]
  → base64 encode PDF
  → mistral_semaphore (max 5 concurrent)
  → HTTP POST to Mistral AI (vision OCR)
  → Retry up to 4 times (1s, 2s, 4s, 8s backoff)
  ← InvoiceData (structured JSON output)

invoice_parser.validate_invoice_data(invoice_data)
  [check required fields: invoice_number, vendor_name, total_amount, etc.]

aiofiles.open(json_path, 'w').write(invoice_data.model_dump_json())
  → save to {pdf_path}.json (e.g., invoice1.json)

redis.setex("mistral_queue:response:{task_id}", 3600, MistralResponse JSON)
  [this is what the main worker is polling for]

redis.delete("mistral_queue:processing:{task_id}")
```

---

## 8. Task Flow: `LLMQueue.worker_loop` (3 workers)

Triggered by Step 7 above. One of 3 LLM workers picks up each task:

```
get_next_task_round_robin()
  → redis.smembers("llm_queue:active_batches")
  → redis.lpop("llm_queue:pending:{batch_id}")
  → redis.setex("llm_queue:processing:{task_id}", 3600, ...)

call_llm_and_respond(request)
  → llm_client.call_llm(system_prompt, user_prompt, pydantic_json_schema)
    [dispatches based on LLM_PROVIDER env var]

    LLM_PROVIDER=bedrock:
      → bedrock_semaphore (max 5 concurrent)
      → bedrock_client.converse() — AWS Bedrock Converse API
      → model: BEDROCK_MODEL_ID (default: google.gemma-3-12b-it)
      → Retry up to 3 times (backoff)
      ← str (response text)

    LLM_PROVIDER=ollama:
      → ollama_semaphore (max 3 concurrent)
      → HTTP POST to http://localhost:11434/api/chat
      → model: OLLAMA_MODEL_NAME (default: gemma2:2b)
      → Retry up to 3 times (backoff)
      ← str (response text)

  → Extract JSON from response string (strip markdown fences, preamble)
  → Fix malformed JSON if needed
  ← OllamaResponse(response_text='{"ledger": "..."}', success=True)

redis.setex("llm_queue:response:{task_id}", 3600, OllamaResponse JSON)
  [this is what the main worker is polling for × 3]

redis.delete("llm_queue:processing:{task_id}")
```

---

## 9. API Calls Per Invoice (Summary)

| # | Service | Who calls it | Notes |
|---|---------|-------------|-------|
| 1 | **Mistral AI** (remote) | `MistralQueueManager.worker_loop` | OCR/extraction, up to 4 retries |
| 2 | **LLM provider** (configurable) | `LLMQueue.worker_loop` | Expense ledger selection |
| 3 | **LLM provider** (configurable) | `LLMQueue.worker_loop` | Vendor/creditor selection |
| 4 | **LLM provider** (configurable) | `LLMQueue.worker_loop` | TDS nature of transaction |

**Total: 4 API calls per invoice** (1 Mistral + 3 LLM)

LLM provider is set via `LLM_PROVIDER` env var (`bedrock` by default).

---

## 10. Redis Keys Reference

```
# Config (permanent — no TTL)
config:prompt:dr                           → DR system prompt text (from prompts/dr_prompt.txt)
config:prompt:cr                           → CR system prompt text (from prompts/cr_prompt.txt)
config:prompt:tds                          → TDS nature system prompt text
config:tds_rates                           → TDS rates JSON array string (from data/tds_rates.json)

# Session data
session:{batch_id}                         → hash (all session fields)

# Main task queue
queue:pending:{batch_id}                   → list of TaskItem JSON (FIFO)
queue:active_batches                       → set of active batch IDs
queue:round_robin_index                    → integer (cycles through batches)
queue:processing:{task_id}                 → TaskItem JSON (1h TTL, crash detection)
voucher_counter:{batch_id}                 → integer (atomic INCR)

# Mistral queue
mistral_queue:pending:{batch_id}           → list of MistralRequest JSON
mistral_queue:active_batches              → set
mistral_queue:round_robin_index           → integer
mistral_queue:processing:{task_id}        → MistralRequest JSON (1h TTL)
mistral_queue:response:{task_id}          → MistralResponse JSON (1h TTL)

# LLM queue
llm_queue:pending:{batch_id}              → list of OllamaRequest JSON
llm_queue:active_batches                  → set
llm_queue:round_robin_index               → integer
llm_queue:processing:{task_id}            → OllamaRequest JSON (1h TTL)
llm_queue:response:{task_id}              → OllamaResponse JSON (1h TTL)
```

---

## 11. Polling: How Workers Wake Up

None of the workers use Redis pub/sub or blocking pops (`BLPOP`). They all use busy-wait polling:

```python
while True:
    task = await get_next_task_round_robin()  # LPOP attempt
    if task is None:
        await asyncio.sleep(1)               # sleep 1s then retry
        continue
    # ... process task
```

`wait_for_response` uses 500ms polling:
```python
while elapsed < timeout:
    response = await redis.get(f"response:{task_id}")
    if response: return parse(response)
    await asyncio.sleep(0.5)
    elapsed += 0.5
```

---

## 12. Full Timeline for 1 Invoice (happy path)

```
t=0s    Upload received
        COA parsed (synchronous, ~2-5s)
        TaskItem pushed to queue:pending:{batch_id}
        HTTP 202 returned

t≈0s    A main worker (of 100) picks up the TaskItem
        MistralRequest pushed to mistral_queue:pending:{batch_id}
        Main worker starts polling mistral_queue:response:{task_id} every 500ms

t≈1s    A Mistral worker (of 5) picks up the MistralRequest
        Calls Mistral AI API (~10-30s typical)

t≈30s   Mistral worker saves invoice.json to disk
        Stores MistralResponse in mistral_queue:response:{task_id}

t≈30s   Main worker sees response, reads invoice.json from disk
        Loads COA json from disk (expense leaves, creditor leaves)
        Pushes 3 LLM requests to llm_queue:pending:{batch_id}
        asyncio.gather → polling 3 response keys every 500ms

t≈31s   LLM workers pick up the 3 tasks (all 3 in parallel if workers free)
        Each calls LLM provider via llm_client.call_llm() (~2-10s per call)

t≈45s   All 3 LLM responses stored in Redis
        Main worker receives all 3, parses ledger names with fuzzy match
        Generates voucher number (Redis INCR)
        XLOutputGenerator builds 4-6 rows in memory
        Saves result to session in Redis
        File status → "success"

t≈45s   Client polling GET /api/sessions/{batch_id}/status
        sees file status "success", gets xl_output rows in response
```

---

## 13. Round-Robin Fair Scheduling

All 3 queues use the same pattern to prevent any one batch from monopolizing workers:

```
active_batches = sorted([batch_A, batch_B, batch_C])  # stable order
index = redis.get("round_robin_index") % len(active_batches)
try: lpop("queue:pending:{active_batches[index]}")
if empty: remove from active_batches, try next
increment index
```

If 3 users each upload 10 invoices simultaneously, their invoices are interleaved (A1, B1, C1, A2, B2, C2...) rather than processing one user's full batch before the next.

---

## 14. Config Store: `RedisConfigStore` (`utils/safe_file_manager.py`)

Replaces the old `SafeFileManager` (filelock + atomic file writes). Same interface — callers unchanged.

```
utils/safe_file_manager.py
  RedisConfigStore(redis_client, key, seed_file)
    .seed_from_file()   → SET key content NX  (only if absent)
    .read()             → redis.get(key)       (fallback: read seed_file directly)
    .write(content)     → redis.set(key, content)
    .exists()           → redis.exists(key)

Global instances:
  dr_prompt_file  → key="config:prompt:dr",  seed="prompts/dr_prompt.txt"
  cr_prompt_file  → key="config:prompt:cr",  seed="prompts/cr_prompt.txt"
  tds_prompt_file → key="config:prompt:tds", seed="prompts/tds_nature_prompt.txt"
  tds_file        → key="config:tds_rates",  seed="data/tds_rates.json"
```

**Why Redis instead of files:**
- Disk files are git-tracked → `git pull` on server would overwrite user-edited prompts
- Redis keys have no TTL → survive restarts, invisible to git
- `seed_from_file()` is a no-op if the key exists → deploy + restart never stomps user changes
- Redis `GET`/`SET` are atomic (single-threaded command loop) → no OS file locks needed

**How prompts reach the LLM:**

```
prompts/dr_prompt.txt  ──(seed once)──▶  Redis: config:prompt:dr
                                               │
PUT /api/config/prompts/dr  ──────────────────▶│ (overwrite)
                                               │
                                               ▼
utils/prompts.py: ledger_name_prompt_dr()
  template = dr_prompt_file.read()   ← redis.get("config:prompt:dr")
  system_prompt = template.format(NOT_FOUND=NOT_FOUND)
  return system_prompt, user_prompt
                                               │
                                               ▼
LLMQueue worker → call_llm(system_prompt, user_prompt, schema)
```

**To reset a prompt to the repo default:**
```bash
redis-cli DEL config:prompt:dr   # delete the key
# restart server → seed_from_file() re-reads the file
```
