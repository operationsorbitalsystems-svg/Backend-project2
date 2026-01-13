# Invoice Processing Refactoring Plan - Simplified Architecture

## Overview

This plan simplifies the invoice processing system by consolidating logic into the main queue worker and creating a generic Ollama LLM service.

**Goal:** Create a maintainable, extensible invoice processing system with proper separation of concerns and support for multi-row XL output (Dr/Cr entries + GST + future TDS).

**Core Architecture:**
- **Main Invoice Queue** (EXISTING - modified) - Orchestrates entire workflow, contains all accounting logic
- **Mistral Parser** (EXISTING - unchanged) - Simple OCR function called directly
- **Generic Ollama Queue** (NEW) - Reusable LLM service for any prompt/response task

**Key Change:** All invoice processing logic now lives in the main queue's worker loop. No separate queues, cleaner code, same API.

---

## Current vs. Desired Architecture

### Current Problems:

1. **XL Output Logic in Wrong Place:** Mistral queue generates ONE row with `ledger_name="PENDING"` - should generate MULTIPLE rows AFTER Ollama calls
2. **Invoice Logic Embedded in Ollama Queue:** `task_queue_ollama.py` contains invoice-specific parsing, COA loading, ledger selection logic - should be generic
3. **No Central Orchestrator:** Invoice processing logic scattered across Mistral and Ollama queues
4. **Hard to Extend:** Adding new LLM tasks (TDS, line-item categorization) requires modifying Ollama queue

### Desired Solution:

**Three Separate Queue Classes:**

1. **Main Invoice Queue** (`services/task_queue.py`) - MODIFY EXISTING
   - Orchestrates complete invoice processing workflow
   - Contains ALL accounting logic (XL output generation, GST calculation, TDS preparation)
   - Calls Mistral parser for OCR (direct function call, not queue)
   - Calls Ollama queue for ledger selections
   - Generates multi-row XL output array
   - Uses round-robin scheduling across batches (fairness)
   - **This is the main entry point - users upload files here**

2. **Mistral OCR Parser** (`services/invoice_parser.py`) - KEEP AS-IS
   - Simple function: Accept PDF → Call Mistral API → Return InvoiceData JSON
   - Already exists and works correctly
   - Called directly by main invoice queue (not a separate queue)
   - Has semaphore-based concurrency control

3. **Generic Ollama Queue** (`services/ollama_queue.py`) - NEW FILE (replace old one)
   - ONLY does: Accept (system_prompt, user_prompt) → Call Ollama → Return response
   - NO invoice-specific logic
   - NO COA loading
   - NO ledger selection parsing
   - Simple, reusable for any LLM task

---

## Step 1: Delete Old Ollama Files

**Files to DELETE:**
1. [services/task_queue_ollama.py](bknd/services/task_queue_ollama.py) - Old Ollama queue with embedded invoice logic
2. [services/ollama_task_service.py](bknd/services/ollama_task_service.py) - Old Ollama task enqueueing service

**Reason:** These files mix invoice-specific logic with Ollama queue management. We'll replace with generic Ollama queue.

---

## Step 2: Update Data Models

**File:** [models.py](bknd/models.py)

**Changes:**

1. **Remove OllamaTask model** - No longer needed with generic Ollama queue

2. **Add new models for generic Ollama:**
```python
class OllamaRequest(BaseModel):
    """Generic Ollama LLM request"""
    task_id: str
    batch_id: str
    system_prompt: str
    user_prompt: str
    enqueued_at: str
    metadata: Optional[dict] = None  # For tracking context

class OllamaResponse(BaseModel):
    """Generic Ollama LLM response"""
    task_id: str
    response_text: str
    success: bool
    error: Optional[str] = None
```

3. **Keep XLOutputRow model** - Already supports multiple rows, no changes needed

4. **Keep ProcessedInvoiceResult model** - Already has `xl_output: List[XLOutputRow]`

5. **Keep TaskItem model** - Used by Mistral queue (will also be used by Invoice queue)

---

## Step 3: Create Generic Ollama Queue

**NEW FILE:** [services/ollama_queue.py](bknd/services/ollama_queue.py)

**Purpose:** Generic LLM queue that ONLY handles prompt → response. NO invoice logic.

**Architecture:**
- Round-robin scheduling across batches (like Mistral queue)
- Redis-backed task queue
- Concurrent workers with semaphore (MAX_OLLAMA_CONCURRENT)
- Simple: enqueue(prompt) → dequeue → call_ollama_api() → return response

**Key Functions:**

```python
class GenericOllamaQueue:
    """Generic Ollama LLM task queue - NO invoice-specific logic"""

    async def enqueue_request(
        self,
        batch_id: str,
        system_prompt: str,
        user_prompt: str,
        metadata: Optional[dict] = None
    ) -> str:
        """
        Enqueue generic Ollama request.
        Returns: task_id
        """
        # Create OllamaRequest model
        # Push to Redis queue: ollama_queue:pending:{batch_id}
        # Add batch to active set
        # Return task_id

    async def get_next_task_round_robin(self) -> Optional[OllamaRequest]:
        """Get next task using round-robin across batches"""
        # Same logic as Mistral queue
        # Round-robin index: ollama_queue:round_robin_index

    async def call_ollama_and_respond(self, request: OllamaRequest) -> OllamaResponse:
        """
        Call Ollama API with prompts, return response.
        NO parsing, NO validation, just raw LLM response.
        """
        # Call Ollama API (import from existing ollama_api_call.py)
        # Return OllamaResponse with response_text

    async def worker_loop(self):
        """Worker that processes Ollama requests"""
        # Get next task (round-robin)
        # Call Ollama
        # Save response to Redis: ollama_queue:response:{task_id}
        # Set expiry (1 hour)

    async def wait_for_response(self, task_id: str, timeout: int = 60) -> OllamaResponse:
        """
        Wait for Ollama response (blocking with timeout).
        Used by invoice queue to wait for LLM results.
        """
        # Poll Redis: ollama_queue:response:{task_id}
        # Return OllamaResponse when available
```

**NO invoice logic** - just a dumb queue that calls Ollama.

---

## Step 4: Rewrite Main Invoice Queue Worker

**File:** [services/task_queue.py](bknd/services/task_queue.py)

**Changes to `process_invoice_task()` function (lines 126-260):**

**REPLACE entire function with new workflow:**

```python
async def process_invoice_task(self, task: dict):
    """
    Main invoice processing workflow.
    Mistral OCR → Ollama ledger selections → Multi-row XL generation → Save
    """
    batch_id = task["batch_id"]
    filename = task["filename"]
    pdf_path = task["pdf_path"]

    try:
        # Update status to processing
        await self.session_manager.update_file_status(batch_id, filename, "processing")

        # === STEP 1: Mistral OCR ===
        success, invoice_data, error = await self.invoice_parser.parse_invoice(pdf_path)

        if not success or not invoice_data:
            raise Exception(f"Mistral OCR failed: {error}")

        # === STEP 2a: Ollama - Expense Ledger Selection ===
        expense_ledgers = await self._load_expense_ledgers_from_coa(batch_id)
        narration = XLOutputGenerator.concatenate_line_items(invoice_data.line_items)

        expense_system_prompt = "You are an accounting assistant..."
        expense_user_prompt = f"Select expense ledger from COA for: {narration}. Options: {expense_ledgers}"

        expense_task_id = await self.ollama_queue.enqueue_request(
            batch_id=batch_id,
            system_prompt=expense_system_prompt,
            user_prompt=expense_user_prompt,
            metadata={"type": "expense_selection", "filename": filename}
        )

        expense_response = await self.ollama_queue.wait_for_response(expense_task_id, timeout=60)
        expense_ledger_name, expense_confidence = self._parse_ledger_response(
            expense_response.response_text, expense_ledgers
        )

        if not expense_ledger_name:
            expense_ledger_name = "Suspended AC"
            expense_confidence = 0.0

        # === STEP 2b: Ollama - Vendor Ledger Selection ===
        liability_ledgers = await self._load_liability_ledgers_from_coa(batch_id)
        vendor_name = invoice_data.header.vendor_name

        vendor_system_prompt = "You are an accounting assistant..."
        vendor_user_prompt = f"Select liability ledger from COA for vendor: {vendor_name}. Options: {liability_ledgers}"

        vendor_task_id = await self.ollama_queue.enqueue_request(
            batch_id=batch_id,
            system_prompt=vendor_system_prompt,
            user_prompt=vendor_user_prompt,
            metadata={"type": "vendor_selection", "filename": filename}
        )

        vendor_response = await self.ollama_queue.wait_for_response(vendor_task_id, timeout=60)
        vendor_ledger_name, vendor_confidence = self._parse_ledger_response(
            vendor_response.response_text, liability_ledgers
        )

        if not vendor_ledger_name:
            vendor_ledger_name = "Suspended AC"
            vendor_confidence = 0.0

        # === STEP 3: Generate Multi-Row XL Output ===
        voucher_number = await self._get_next_voucher_number(batch_id)

        xl_rows = XLOutputGenerator.generate_xl_output_rows(
            invoice_data=invoice_data,
            expense_ledger_name=expense_ledger_name,
            vendor_ledger_name=vendor_ledger_name,
            expense_confidence=expense_confidence,
            vendor_confidence=vendor_confidence,
            voucher_number=voucher_number
        )

        # === STEP 4: Save Results ===
        # Save invoice JSON
        json_path = self.file_handler.get_invoice_json_path(batch_id, filename)
        self.file_handler.save_json(json_path, invoice_data.model_dump())

        # Update session with results
        result = ProcessedInvoiceResult(
            filename=filename,
            pdf_path=pdf_path,
            json_path=json_path,
            status="completed",
            invoice_number=invoice_data.header.invoice_number,
            vendor_name=invoice_data.header.vendor_name,
            total_amount=invoice_data.total_amount,
            currency=invoice_data.currency,
            line_items_count=len(invoice_data.line_items),
            xl_output=xl_rows,  # List of XLOutputRow
            data=invoice_data,
            timestamp=datetime.utcnow().isoformat()
        )

        await self.session_manager.add_processed_result(batch_id, result)
        await self.session_manager.update_file_status(batch_id, filename, "completed")

        logger.info(f"✅ Invoice {filename} completed: {len(xl_rows)} XL rows generated")

    except Exception as e:
        logger.error(f"❌ Invoice {filename} failed: {str(e)}")
        await self.session_manager.update_file_status(batch_id, filename, "failed", error=str(e))
```

**ADD new helper methods to TaskQueueManager class:**

```python
async def _load_expense_ledgers_from_coa(self, batch_id: str) -> List[str]:
    """Load expense leaf nodes from COA (cached per batch)"""
    # Load COA JSON from disk
    # Find leaf nodes under "Expense" groups (regex)
    # Cache in memory per batch
    # Return list of ledger names

async def _load_liability_ledgers_from_coa(self, batch_id: str) -> List[str]:
    """Load liability leaf nodes from COA (cached per batch)"""
    # Load COA JSON from disk
    # Find leaf nodes under "Liability" groups (regex)
    # Cache in memory per batch
    # Return list of ledger names

def _parse_ledger_response(self, response_text: str, valid_ledgers: List[str]) -> Tuple[str, float]:
    """Parse Ollama response to extract ledger name and confidence"""
    # Parse response (expect JSON or structured text)
    # Extract ledger_name and confidence
    # Validate ledger_name exists in valid_ledgers list
    # Return (ledger_name, confidence)

async def _get_next_voucher_number(self, batch_id: str) -> int:
    """Atomic increment of voucher counter per batch"""
    # Increment Redis counter: voucher_counter:{batch_id}
    # Return new voucher number
```

**UPDATE imports:**
```python
# REMOVE:
from services.ollama_task_service import OllamaTaskService
from services.task_queue_ollama import get_ollama_queue_manager

# ADD:
from services.ollama_queue import get_ollama_queue_manager
```

**UPDATE __init__ method:**
```python
def __init__(self, redis_client):
    # ... existing code ...
    self.ollama_queue = get_ollama_queue_manager()  # Add this

    # Add COA cache
    self.expense_ledgers_cache = {}  # {batch_id: List[str]}
    self.liability_ledgers_cache = {}  # {batch_id: List[str]}
```

**KEEP everything else:**
- Round-robin queue scheduling
- Crash recovery
- Semaphore-based concurrency (MAX_MISTRAL_CONCURRENT)
- enqueue_file() method
- get_next_task_round_robin() method

**Result:** Main queue now orchestrates entire invoice workflow in one place

---

## Step 5: Rewrite XL Output Generator (Multi-Row)

**File:** [services/xl_output_generator.py](bknd/services/xl_output_generator.py)

**DELETE existing `generate_xl_output_row()` function** (lines 105-148)

**ADD NEW function:**

```python
@staticmethod
def generate_xl_output_rows(
    invoice_data: InvoiceData,
    expense_ledger_name: str,
    vendor_ledger_name: str,
    expense_confidence: float,
    vendor_confidence: float,
    voucher_number: int
) -> List[XLOutputRow]:
    """
    Generate multiple XL output rows per invoice (Dr/Cr pairing + GST).

    Returns 2-5+ rows depending on invoice structure:
    - 1 Dr row for expense
    - 0-3 Dr rows for GST (CGST/SGST/IGST)
    - 1 Cr row for vendor
    """
    rows = []

    # Common fields for all rows
    voucher_date = invoice_data.header.invoice_date
    vendor_address = invoice_data.header.vendor_address or ""
    pincode = XLOutputGenerator.extract_pincode(vendor_address)
    narration = XLOutputGenerator.concatenate_line_items(invoice_data.line_items)

    # Step 1: Dr entry for expense
    has_gst = XLOutputGenerator.detect_has_gst(invoice_data)
    ledger_amount = invoice_data.subtotal if (has_gst and invoice_data.subtotal) else invoice_data.total_amount

    rows.append(XLOutputRow(
        voucher_date=voucher_date,
        voucher_type_name="Journal",
        voucher_number=voucher_number,
        buyer_supplier_address=vendor_address,
        buyer_supplier_pincode=pincode,
        ledger_name=expense_ledger_name,  # From Ollama
        ledger_amount=ledger_amount,
        ledger_amount_dr_cr="Dr",
        ledger_narration=narration,
        confidence_score=expense_confidence
    ))

    # Step 2: Dr entries for GST (if applicable) - PROGRAMMATIC
    if invoice_data.cgst_tax_amount and invoice_data.cgst_tax_amount > 0:
        rows.append(XLOutputRow(
            voucher_date=voucher_date,
            voucher_type_name="Journal",
            voucher_number=voucher_number,
            buyer_supplier_address=vendor_address,
            buyer_supplier_pincode=pincode,
            ledger_name="CGST",  # Programmatic for now
            ledger_amount=invoice_data.cgst_tax_amount,
            ledger_amount_dr_cr="Dr",
            ledger_narration=narration,
            confidence_score=1.0  # Programmatic = always confident
        ))

    if invoice_data.sgst_tax_amount and invoice_data.sgst_tax_amount > 0:
        rows.append(XLOutputRow(
            voucher_date=voucher_date,
            voucher_type_name="Journal",
            voucher_number=voucher_number,
            buyer_supplier_address=vendor_address,
            buyer_supplier_pincode=pincode,
            ledger_name="SGST",  # Programmatic for now
            ledger_amount=invoice_data.sgst_tax_amount,
            ledger_amount_dr_cr="Dr",
            ledger_narration=narration,
            confidence_score=1.0
        ))

    if invoice_data.igst_tax_amount and invoice_data.igst_tax_amount > 0:
        rows.append(XLOutputRow(
            voucher_date=voucher_date,
            voucher_type_name="Journal",
            voucher_number=voucher_number,
            buyer_supplier_address=vendor_address,
            buyer_supplier_pincode=pincode,
            ledger_name="IGST",  # Programmatic for now
            ledger_amount=invoice_data.igst_tax_amount,
            ledger_amount_dr_cr="Dr",
            ledger_narration=narration,
            confidence_score=1.0
        ))

    # Step 3: Cr entry for vendor/liability
    rows.append(XLOutputRow(
        voucher_date=voucher_date,
        voucher_type_name="Journal",
        voucher_number=voucher_number,
        buyer_supplier_address=vendor_address,
        buyer_supplier_pincode=pincode,
        ledger_name=vendor_ledger_name,  # From Ollama
        ledger_amount=invoice_data.total_amount,  # Total including GST
        ledger_amount_dr_cr="Cr",
        ledger_narration=f"{invoice_data.header.invoice_number} - {invoice_data.header.vendor_name}",
        confidence_score=vendor_confidence
    ))

    # Step 4: TDS entries placeholder (Phase 2)
    # tds_rows = add_tds_entries(invoice_data, narration, None, voucher_number)
    # rows.extend(tds_rows)

    return rows
```

**KEEP existing helper functions:**
- `extract_pincode()` - Already works correctly
- `detect_has_gst()` - Already works correctly
- `concatenate_line_items()` - Already works correctly

---

## Step 6: Update Main API Endpoints

**File:** [main.py](bknd/main.py)

**Changes:**

1. **Update imports:**
   ```python
   # REMOVE (if exists):
   from services.task_queue_ollama import get_ollama_queue_manager as get_old_ollama_queue

   # ADD:
   from services.ollama_queue import get_ollama_queue_manager
   ```

2. **Update startup event to start Ollama workers:**
   ```python
   # Startup event:
   @app.on_event("startup")
   async def startup_event():
       # Start main invoice queue workers
       task_queue = get_task_queue_manager()
       for _ in range(MAX_MISTRAL_CONCURRENT):  # Still using same config variable
           asyncio.create_task(task_queue.worker_loop())

       # Start Ollama workers (NEW)
       ollama_queue = get_ollama_queue_manager()
       for _ in range(MAX_OLLAMA_CONCURRENT):
           asyncio.create_task(ollama_queue.worker_loop())
   ```

3. **NO changes to upload endpoint** - Still uses `task_queue.enqueue_file()` (API stays the same!)

4. **NO other changes** - Status endpoint stays the same (reads from session)

---

## Step 7: Update Configuration

**File:** [config.py](bknd/config.py)

**Changes:**
- **Rename if exists:** `MAX_OLLAMA_CONCURRENT_CALLS` → `MAX_OLLAMA_CONCURRENT`
- **KEEP existing:** `MAX_MISTRAL_CONCURRENT` (used by main invoice queue)
- **KEEP:** All Ollama connection settings (OLLAMA_BASE_URL, OLLAMA_MODEL_NAME)
- **KEEP:** All Mistral settings

**NO new config variables needed** - Reusing existing ones

---

## Step 8: Create TDS Placeholder (Phase 2 Prep)

**NEW FILE:** [services/tds_processor.py](bknd/services/tds_processor.py)

```python
"""
TDS (Tax Deducted at Source) processing module.

Phase 2 implementation - currently returns empty list.
"""

from typing import List, Optional, Dict
from models import InvoiceData, XLOutputRow

def calculate_tds_entries(
    invoice_data: InvoiceData,
    narration: str,
    tds_table: Optional[Dict],
    voucher_number: int
) -> List[XLOutputRow]:
    """
    Calculate TDS entries for invoice.

    Phase 2 Implementation Plan:
    1. Concatenate narration from line items
    2. Call Ollama with: narration + TDS table "nature of tds" column
    3. Ollama returns: applicable TDS row OR null if not applicable
    4. If applicable: Calculate TDS amount, create Dr/Cr entries
    5. Adjust vendor Cr amount to account for TDS deduction
    6. Return TDS rows

    Args:
        invoice_data: Parsed invoice data
        narration: Concatenated line item descriptions
        tds_table: TDS rate table (section code, nature, threshold, rate)
        voucher_number: Current voucher number

    Returns:
        List of XLOutputRow for TDS entries (empty for now)
    """
    # Phase 2 implementation
    return []
```

**NEW FILE:** [data/tds_rates.json](bknd/data/tds_rates.json)

```json
{
  "_comment": "TDS Rate Table - Phase 2 Implementation",
  "_structure": {
    "section_code": "TDS section code (e.g., 194C, 194J)",
    "nature_of_tds": "Description of TDS nature (used for Ollama matching)",
    "threshold_amount": "Threshold amount for TDS applicability",
    "tds_rate_percent": "TDS rate percentage"
  },
  "rates": []
}
```

---

## Summary of Files

### DELETE (2 files):
- [services/task_queue_ollama.py](bknd/services/task_queue_ollama.py)
- [services/ollama_task_service.py](bknd/services/ollama_task_service.py)

### CREATE (3 files):
- [services/ollama_queue.py](bknd/services/ollama_queue.py) - Generic Ollama LLM queue
- [services/tds_processor.py](bknd/services/tds_processor.py) - Stub for Phase 2
- [data/tds_rates.json](bknd/data/tds_rates.json) - Stub for Phase 2

### MODIFY (4 files):
1. [models.py](bknd/models.py) - Remove OllamaTask, add OllamaRequest/Response
2. [services/task_queue.py](bknd/services/task_queue.py) - **Complete rewrite of worker to orchestrate full invoice workflow**
3. [services/xl_output_generator.py](bknd/services/xl_output_generator.py) - Rewrite for multi-row
4. [main.py](bknd/main.py) - Update imports, start Ollama workers

### KEEP UNCHANGED:
- [coa_parser.py](bknd/coa_parser.py)
- [services/invoice_parser.py](bknd/services/invoice_parser.py)
- [services/session_manager.py](bknd/services/session_manager.py)
- [services/file_handler.py](bknd/services/file_handler.py)
- [services/ollama_api_call.py](bknd/services/ollama_api_call.py) - Keep for low-level Ollama API calls

---

## Expected Results

### Example: Invoice with GST

**Input Invoice:**
- Invoice: INV-001
- Vendor: ABC Corp
- Subtotal: 10,000
- CGST: 900
- SGST: 900
- Total: 11,800

**Generated XL Output (5 rows):**

| Row | Voucher # | Ledger Name | Amount | Dr/Cr | Confidence |
|-----|-----------|-------------|--------|-------|------------|
| 1 | 1 | Professional Services (Ollama) | 10,000 | Dr | 0.95 |
| 2 | 1 | CGST | 900 | Dr | 1.0 |
| 3 | 1 | SGST | 900 | Dr | 1.0 |
| 4 | 1 | Sundry Creditors - ABC (Ollama) | 11,800 | Cr | 0.90 |

**Verification:** Dr total (11,800) = Cr total (11,800) ✓

### Example: Invoice without GST

**Input Invoice:**
- Invoice: INV-002
- Vendor: XYZ Ltd
- Total: 5,000
- No GST

**Generated XL Output (2 rows):**

| Row | Voucher # | Ledger Name | Amount | Dr/Cr | Confidence |
|-----|-----------|-------------|--------|-------|------------|
| 1 | 2 | Office Supplies (Ollama) | 5,000 | Dr | 0.88 |
| 2 | 2 | Sundry Creditors - XYZ (Ollama) | 5,000 | Cr | 0.92 |

**Verification:** Dr total (5,000) = Cr total (5,000) ✓

---

## Verification & Testing

### Test Scenarios

1. **Invoice with GST:**
   - Upload COA + invoice with CGST/SGST
   - Verify 5 rows generated (Dr expense + Dr CGST + Dr SGST + Cr vendor)
   - Verify Dr total = Cr total
   - Verify expense ledger selected from COA
   - Verify vendor ledger selected from COA

2. **Invoice without GST:**
   - Upload COA + invoice without GST
   - Verify 2 rows generated (Dr expense + Cr vendor)
   - Verify amounts match

3. **Multiple invoices in batch:**
   - Upload 3 invoices
   - Verify voucher numbers are sequential (1, 2, 3)
   - Verify each invoice has all rows with same voucher number

4. **Ollama failure handling:**
   - Simulate Ollama service down
   - Verify fallback to "Suspended AC"
   - Verify confidence = 0.0
   - Verify processing continues (doesn't crash)

5. **End-to-end flow:**
   - POST /create-session → Get batch_id
   - POST /upload-files → Upload COA + 3 invoices
   - GET /status/{batch_id} → Poll until complete
   - Verify all XL outputs present
   - Verify all fields populated correctly

### Manual Verification Steps

1. Start backend server
2. Create session via API
3. Upload COA PDF + sample invoices
4. Poll status endpoint
5. Verify generated XL output:
   - Check row count per invoice
   - Check Dr/Cr balance
   - Check ledger names selected from COA
   - Check confidence scores

---

## Critical Success Factors

✅ **Clean Separation of Concerns**
- Invoice queue orchestrates (accounting logic)
- Mistral queue extracts (OCR only)
- Ollama queue responds (LLM only)

✅ **Maintainability**
- Invoice logic in ONE place (invoice_task_queue.py)
- Easy to add new LLM tasks (TDS, categorization, etc.)
- Generic Ollama queue reusable for anything

✅ **Correctness**
- Multi-row XL output with proper Dr/Cr balancing
- GST rows added programmatically
- Fallback handling for LLM failures

✅ **Extensibility**
- TDS-ready architecture (Phase 2)
- Generic Ollama queue supports any future LLM task
- Clear insertion points for new features

✅ **Fairness**
- Round-robin scheduling at ALL queue levels
- No batch monopolization

✅ **API Compatibility**
- **NO changes to API endpoints** - Frontend stays the same
- Upload endpoint still calls `task_queue.enqueue_file()`
- Status endpoint still returns same data structure
- Session data structure unchanged

---

## Implementation Notes

**Important:**
- Main queue (task_queue.py) now contains ALL invoice processing logic
- No separate invoice_task_queue.py file needed
- API surface stays identical - no frontend changes required
- ProcessedInvoiceResult model already supports List[XLOutputRow]

**Order of Implementation:**
1. Create generic Ollama queue first (services/ollama_queue.py)
2. Update models.py (add OllamaRequest/Response)
3. Rewrite XL generator for multi-row
4. Update main queue worker (task_queue.py) - this is the big one
5. Update main.py to start Ollama workers
6. Delete old Ollama files
7. Test end-to-end

---

*Status: Ready for implementation*
