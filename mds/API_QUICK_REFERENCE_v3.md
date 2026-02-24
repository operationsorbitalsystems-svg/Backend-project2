# Invoice Parser API - Quick Reference (v4.0)
**Updated with XL Output for Journal Entry Import + Config Management (Prompts & TDS Rates)**

## 🚀 Quick Start

```bash
# 1. Start backend
python main.py

# 2. Create session
curl -X POST http://localhost:8000/api/sessions

# 3. Upload COA + Invoice files (COA is now REQUIRED)
curl -X POST http://localhost:8000/api/sessions/{batch_id}/upload \
  -F "coa_file=@chart_of_accounts.pdf" \
  -F "files=@invoice1.pdf" \
  -F "files=@invoice2.pdf"

# 4. Check status (includes COA data + XL Output)
curl http://localhost:8000/api/sessions/{batch_id}/status
```

---

## 📡 Endpoints

### 1️⃣ Health Check

```http
GET /health
```

**Response (200):**
```json
{
  "status": "healthy",
  "timestamp": "2026-01-12T10:30:00"
}
```

---

### 2️⃣ Create Session

```http
POST /api/sessions
Content-Type: application/json
```

**Request:** (Empty body)

**Response (201):**
```json
{
  "batch_id": "550e8400-e29b-41d4-a716-446655440000",
  "created_at": "2026-01-12T10:30:00",
  "status": "ready"
}
```

**Errors:**
- `500` - Failed to create session

---

### 3️⃣ Upload Files (COA + Invoices)

```http
POST /api/sessions/{batch_id}/upload
Content-Type: multipart/form-data
```

**Request:**
```
batch_id: (path parameter)
coa_file: chart_of_accounts.pdf (REQUIRED - single PDF file)
files: [invoice1.pdf, invoice2.pdf, ...max 20 files] (REQUIRED)
```

**Response (202):**
```json
{
  "batch_id": "550e8400-e29b-41d4-a716-446655440000",
  "received_files": 2,
  "coa_received": true,
  "status": "processing",
  "message": "Files and COA received. Processing started."
}
```

**Processing:**
- COA is parsed **immediately** (synchronous)
- Invoices are processed **asynchronously** in background
- COA structure available in next status check
- XL Output generated automatically for each invoice

**Errors:**
- `400` - COA file is required
- `400` - COA file must be a PDF
- `400` - No invoice files provided / Invalid batch_id
- `404` - Batch not found or expired
- `413` - File too large (max 50MB)
- `415` - Non-PDF file
- `500` - Upload/parsing error

---

### 4️⃣ Get Status (with COA + Invoice Results + XL Output)

```http
GET /api/sessions/{batch_id}/status
```

**Response (200) - While Processing:**
```json
{
  "batch_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing",
  "total_files": 2,
  "processed": 0,
  "pending": 2,
  "failed": 0,

  "coa_status": {
    "filename": "chart_of_accounts.pdf",
    "status": "parsed",
    "parsed_at": "2026-01-12T10:30:15",
    "error": null,
    "metadata": {
      "total_pages": 5,
      "total_groups": 12,
      "total_ledgers": 145,
      "levels_discovered": 4
    }
  },

  "coa_data": {
    "metadata": {
      "total_pages": 5,
      "total_groups": 12,
      "total_ledgers": 145,
      "levels_discovered": 4
    },
    "hierarchy": {
      "Assets": {
        "Current Assets": {
          "Cash": [],
          "Bank Accounts": [],
          "Accounts Receivable": []
        },
        "Fixed Assets": {
          "Land & Buildings": [],
          "Equipment": []
        }
      },
      "Liabilities": {
        "Current Liabilities": {
          "Accounts Payable": [],
          "Short-term Loans": []
        }
      },
      "Equity": {
        "Share Capital": [],
        "Retained Earnings": []
      }
    },
    "flat_list": [
      "Cash",
      "Bank Accounts",
      "Accounts Receivable",
      "Land & Buildings",
      "Equipment",
      "Accounts Payable",
      "Short-term Loans",
      "Share Capital",
      "Retained Earnings"
    ]
  },

  "file_statuses": [
    {
      "filename": "invoice1.pdf",
      "status": "processing",
      "processed_at": null,
      "started_at": "2026-01-12T10:30:20",
      "error": null
    },
    {
      "filename": "invoice2.pdf",
      "status": "pending",
      "processed_at": null,
      "started_at": null,
      "error": null
    }
  ],

  "data": null,
  "completed_at": null
}
```

**Response (200) - Complete:**
```json
{
  "batch_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "total_files": 2,
  "processed": 2,
  "pending": 0,
  "failed": 0,

  "coa_status": {
    "filename": "chart_of_accounts.pdf",
    "status": "parsed",
    "parsed_at": "2026-01-12T10:30:15",
    "error": null,
    "metadata": {
      "total_pages": 5,
      "total_groups": 12,
      "total_ledgers": 145,
      "levels_discovered": 4
    }
  },

  "coa_data": {
    "metadata": {...},
    "hierarchy": {...},
    "flat_list": [...]
  },

  "file_statuses": [...],

  "data": [
    {
      "filename": "invoice1.pdf",
      "pdf_path": "/tmp/550e8400.../invoice1.pdf",
      "json_path": "/tmp/550e8400.../invoice1.json",
      "status": "success",
      "invoice_number": "INV-001",
      "vendor_name": "Vendor Inc.",
      "total_amount": 2360.0,
      "currency": "INR",
      "line_items_count": 2,
      "xl_output": [
        {
          "voucher_date": "2026-01-12",
          "voucher_type_name": "Journal",
          "voucher_number": 1,
          "buyer_supplier_address": "123 Street, City, 400001",
          "buyer_supplier_pincode": "400001",
          "ledger_name": "ABC",
          "ledger_amount": 2000.0,
          "ledger_amount_dr_cr": "Dr",
          "ledger_narration": "Service A"
        }
      ],
      "data": {
        "header": {
          "invoice_number": "INV-001",
          "invoice_date": "2026-01-12",
          "vendor_name": "Vendor Inc.",
          "vendor_address": "123 Street, City, 400001",
          "vendor_gstin": "GSTIN123",
          "place_of_supply": "State 1"
        },
        "line_items": [
          {
            "description": "Service A",
            "quantity": 1,
            "unit_price": 1000.0,
            "amount": 1000.0
          }
        ],
        "subtotal": 2000.0,
        "cgst_tax_amount": 180.0,
        "sgst_tax_amount": 180.0,
        "igst_tax_amount": 0.0,
        "total_amount": 2360.0,
        "currency": "INR",
        "already_recieved": 0.0
      },
      "error": null,
      "timestamp": "2026-01-12T10:35:30"
    },
    {
      "filename": "invoice2.pdf",
      "pdf_path": "/tmp/550e8400.../invoice2.pdf",
      "json_path": "/tmp/550e8400.../invoice2.json",
      "status": "success",
      "invoice_number": "INV-002",
      "vendor_name": "ABC Corp",
      "total_amount": 11800.0,
      "currency": "INR",
      "line_items_count": 2,
      "xl_output": [
        {
          "voucher_date": "2026-01-12",
          "voucher_type_name": "Journal",
          "voucher_number": 2,
          "buyer_supplier_address": "Plot 5, Mumbai, Maharashtra, 400001",
          "buyer_supplier_pincode": "400001",
          "ledger_name": "ABC",
          "ledger_amount": 10000.0,
          "ledger_amount_dr_cr": "Dr",
          "ledger_narration": "Professional Services; Consulting Fees"
        }
      ],
      "data": {
        "header": {
          "invoice_number": "INV-002",
          "invoice_date": "2026-01-12",
          "vendor_name": "ABC Corp",
          "vendor_address": "Plot 5, Mumbai, Maharashtra, 400001",
          "vendor_gstin": "27AABCU9603R1ZV",
          "place_of_supply": "Maharashtra"
        },
        "line_items": [
          {
            "description": "Professional Services",
            "quantity": 10,
            "unit_price": 500.0,
            "amount": 5000.0
          },
          {
            "description": "Consulting Fees",
            "quantity": 5,
            "unit_price": 1000.0,
            "amount": 5000.0
          }
        ],
        "subtotal": 10000.0,
        "cgst_tax_amount": 900.0,
        "sgst_tax_amount": 900.0,
        "igst_tax_amount": 0.0,
        "total_amount": 11800.0,
        "currency": "INR",
        "already_recieved": 0.0
      },
      "error": null,
      "timestamp": "2026-01-12T10:35:45"
    }
  ],

  "completed_at": "2026-01-12T10:36:00"
}
```

**Status Values:**
- `ready` - Session ready for files
- `processing` - Files being processed
- `partial_complete` - Some files done
- `completed` - All files processed

**File Status Values:**
- `pending` - Waiting to be processed
- `processing` - Currently processing
- `completed` - Successfully processed
- `failed` - Processing failed

**COA Status Values:**
- `pending` - Waiting to be parsed
- `parsed` - Successfully parsed
- `failed` - Parsing failed

**Errors:**
- `404` - Batch not found or expired
- `500` - Server error

---

### 5️⃣ Config Management (Prompts + TDS Rates)

These endpoints let you read and update the LLM system prompts and TDS rates at runtime.
Changes are stored in Redis and take effect **immediately** for all new invoices — no restart needed.

---

#### GET `/api/config`

Returns all 3 system prompts and the full TDS rates array.

```http
GET /api/config
```

**Response (200):**
```json
{
  "dr_prompt": "<role>You are a Senior Chartered Accountant...</role>",
  "cr_prompt": "<role>You are a Senior Chartered Accountant...</role>",
  "tds_prompt": "<role>You are an Indian Tax Compliance Expert...</role>",
  "tds_rates": [
    {
      "section": "194J(b)",
      "nature_of_transaction": "Fees – All other Professional Services",
      "threshold_limit": 50000,
      "tds_rate": 10
    }
  ]
}
```

---

#### PUT `/api/config/prompts/dr`

Overwrites the **DR (expense ledger)** system prompt. Send as `text/plain` — no JSON encoding needed.

```http
PUT /api/config/prompts/dr
Content-Type: text/plain
```

**Request body:** raw prompt text

**Response (200):**
```json
{ "message": "DR prompt updated" }
```

**cURL:**
```bash
curl -X PUT http://localhost:8000/api/config/prompts/dr \
  -H "Content-Type: text/plain" \
  --data-binary @prompts/dr_prompt.txt
```

---

#### PUT `/api/config/prompts/cr`

Overwrites the **CR (vendor/creditor ledger)** system prompt.

```http
PUT /api/config/prompts/cr
Content-Type: text/plain
```

**Response (200):**
```json
{ "message": "CR prompt updated" }
```

---

#### PUT `/api/config/prompts/tds`

Overwrites the **TDS nature classification** system prompt.

```http
PUT /api/config/prompts/tds
Content-Type: text/plain
```

**Response (200):**
```json
{ "message": "TDS prompt updated" }
```

---

#### PUT `/api/config/tds-rates`

Replaces the full TDS rates list. Validates each entry and hot-reloads in memory.

```http
PUT /api/config/tds-rates
Content-Type: application/json
```

**Request body:** JSON array of TDS rate objects
```json
[
  {
    "section": "194J(b)",
    "nature_of_transaction": "Fees – All other Professional Services",
    "threshold_limit": 50000,
    "tds_rate": 10
  },
  {
    "section": "194I(a)",
    "nature_of_transaction": "Rent for Plant & Machinery",
    "threshold_limit": 50000,
    "tds_rate": 2
  }
]
```

**Response (200):**
```json
{ "message": "TDS rates updated (14 entries)" }
```

**Errors:**
- `422` - Invalid TDS rate schema (missing required fields)

---

#### How Config Persistence Works

- On **first startup**, prompts and TDS rates are seeded from disk files into Redis (no-op if keys already exist)
- After that, all reads come from Redis and all API writes go to Redis
- A `git pull` + server restart does **not** overwrite user customisations — seeding is skipped if Redis keys exist
- To reset to repo defaults: `redis-cli DEL config:prompt:dr` (or whichever key), then restart

**Redis keys:**
```
config:prompt:dr     → DR system prompt text
config:prompt:cr     → CR system prompt text
config:prompt:tds    → TDS nature system prompt text
config:tds_rates     → TDS rates JSON array (string)
```

---

## 📊 XL Output Fields

Each processed invoice includes an `xl_output` array with journal entry data:

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `voucher_date` | string | Invoice date in ISO format | `"2026-01-12"` |
| `voucher_type_name` | string | Always "Journal" | `"Journal"` |
| `voucher_number` | integer | Sequential number (1, 2, 3...) | `1` |
| `buyer_supplier_address` | string | Vendor address | `"123 Street, City, 400001"` |
| `buyer_supplier_pincode` | string/null | Extracted 6-digit pincode | `"400001"` |
| `ledger_name` | string | Ledger account (currently "ABC") | `"ABC"` |
| `ledger_amount` | float | Subtotal if GST exists, else total | `2000.0` |
| `ledger_amount_dr_cr` | string | Always "Dr" | `"Dr"` |
| `ledger_narration` | string | Line items concatenated with "; " | `"Service A; Product B"` |

### XL Output Logic

**Voucher Number:**
- Sequential counter per batch (1, 2, 3...)
- Atomic increment (thread-safe)

**Pincode Extraction:**
- Regex: `\b\d{6}\b` (6 consecutive digits)
- Handles spaces: "400 001" → "400001"
- Returns `null` if not found

**Ledger Amount Calculation:**
- **If GST exists** (CGST/SGST/IGST > 0): Use `subtotal`
- **If no GST**: Use `total_amount`
- **If GST but no subtotal**: Fall back to `total_amount`

**Narration:**
- Concatenates all line item descriptions
- Separator: `"; "`
- Example: `["Item A", "Item B"]` → `"Item A; Item B"`

**Future Enhancement:**
- `ledger_name` will be mapped from COA using LLM (Ollama gemma2:2b)
- Currently uses placeholder "ABC"

---

## 🔄 Flow Diagram

```
┌─────────────────────────────────────────────────────────┐
│ 1. POST /api/sessions                                   │
│    Response: { batch_id, created_at, status: "ready" }  │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│ 2. POST /api/sessions/{batch_id}/upload                 │
│    Request: COA PDF + Invoice PDFs (both required)      │
│    - COA parsed immediately (5-phase pipeline)          │
│    - Invoices queued for background processing          │
│    Response: 202 Accepted (processing started)          │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│ 3. GET /api/sessions/{batch_id}/status                  │
│    [Repeat every 1s until status == "completed"]        │
│    Response:                                            │
│    - coa_status { parsed_at, metadata }                 │
│    - coa_data { hierarchy, flat_list }                  │
│    - file_statuses, data: [...invoice results]         │
│    - xl_output: [journal entry rows] (NEW in v3)        │
└─────────────────────────────────────────────────────────┘
```

---

## 🗂️ COA (Chart of Accounts) Details

### What is COA?

The Chart of Accounts (COA) is a hierarchical structure of all accounting ledgers and groups used by an organization. It provides context for invoice processing and ledger mapping.

### COA Processing Pipeline

**5-Phase Parsing Process:**

1. **PDF Word Extraction** - Extracts all words with (x, y) coordinates
2. **Create Sentences** - Groups words by Y-coordinate into lines
3. **Build Levels Array** - Clusters X-coordinates into hierarchy levels
4. **Assign Levels** - Maps each sentence to its indentation level
5. **Build Hierarchy** - Creates parent-child relationships

### COA Data Structure

**Metadata:**
```json
{
  "total_pages": 5,
  "total_groups": 12,
  "total_ledgers": 145,
  "levels_discovered": 4
}
```

**Hierarchy:** Nested dictionary structure
```json
{
  "Assets": {
    "Current Assets": {
      "Cash": [],
      "Bank Accounts": []
    }
  }
}
```

**Flat List:** All leaf nodes (ledger accounts)
```json
["Cash", "Bank Accounts", "Accounts Receivable", ...]
```

### COA File Requirements

- Format: PDF only
- Max size: 50MB (default)
- Required: Yes (must be uploaded with invoices)
- Processing: Synchronous (parsed immediately)
- Storage: Saved to `/tmp/invoice_uploads/{batch_id}/COA/`

---

## 📝 Usage Examples

### JavaScript/TypeScript

```typescript
// 1. Create session
const sessionRes = await fetch('http://localhost:8000/api/sessions', {
  method: 'POST'
});
const { batch_id } = await sessionRes.json();

// 2. Upload COA + Invoice files
const formData = new FormData();
formData.append('coa_file', coaFile);  // REQUIRED
formData.append('files', invoiceFile1);
formData.append('files', invoiceFile2);

const uploadRes = await fetch(
  `http://localhost:8000/api/sessions/${batch_id}/upload`,
  { method: 'POST', body: formData }
);

// 3. Poll status
const pollStatus = async () => {
  const statusRes = await fetch(
    `http://localhost:8000/api/sessions/${batch_id}/status`
  );
  const status = await statusRes.json();

  // COA data available immediately after upload
  if (status.coa_status?.status === 'parsed') {
    console.log('COA Metadata:', status.coa_status.metadata);
    console.log('COA Hierarchy:', status.coa_data.hierarchy);
    console.log('Total Ledgers:', status.coa_data.flat_list.length);
  }

  console.log(`Invoices: ${status.processed}/${status.total_files}`);

  if (status.status === 'completed') {
    console.log('Invoice Results:', status.data);

    // Process XL Output for journal entries
    status.data.forEach(invoice => {
      console.log(`\nInvoice: ${invoice.invoice_number}`);
      invoice.xl_output.forEach(row => {
        console.log(`  Voucher #${row.voucher_number}`);
        console.log(`  Amount: ${row.ledger_amount} ${row.ledger_amount_dr_cr}`);
        console.log(`  Narration: ${row.ledger_narration}`);
      });
    });
  } else {
    setTimeout(pollStatus, 1000);
  }
};

pollStatus();
```

### Python

```python
import requests
import time

BASE_URL = "http://localhost:8000"

# 1. Create session
session_res = requests.post(f"{BASE_URL}/api/sessions")
batch_id = session_res.json()["batch_id"]

# 2. Upload COA + Invoice files
with open("chart_of_accounts.pdf", "rb") as coa_file, \
     open("invoice1.pdf", "rb") as inv1, \
     open("invoice2.pdf", "rb") as inv2:

    files = {
        "coa_file": coa_file,
        "files": [inv1, inv2]
    }
    upload_res = requests.post(
        f"{BASE_URL}/api/sessions/{batch_id}/upload",
        files=files
    )
    print(f"Upload status: {upload_res.json()}")

# 3. Poll status
while True:
    status_res = requests.get(
        f"{BASE_URL}/api/sessions/{batch_id}/status"
    )
    status = status_res.json()

    # Check COA status
    if status.get("coa_status"):
        coa_status = status["coa_status"]
        print(f"COA Status: {coa_status['status']}")
        if coa_status["status"] == "parsed":
            metadata = coa_status["metadata"]
            print(f"  Total Ledgers: {metadata['total_ledgers']}")
            print(f"  Hierarchy Levels: {metadata['levels_discovered']}")

    # Check invoice processing
    print(f"Invoices: {status['status']}")
    print(f"Processed: {status['processed']}/{status['total_files']}")

    if status["status"] == "completed":
        print("\nCOA Hierarchy:", status["coa_data"]["hierarchy"])

        # Process XL Output
        print("\n=== XL Output for Journal Entries ===")
        for invoice in status["data"]:
            print(f"\nInvoice: {invoice['filename']}")
            for xl_row in invoice["xl_output"]:
                print(f"  Voucher #{xl_row['voucher_number']}")
                print(f"  Date: {xl_row['voucher_date']}")
                print(f"  Type: {xl_row['voucher_type_name']}")
                print(f"  Amount: {xl_row['ledger_amount']} {xl_row['ledger_amount_dr_cr']}")
                print(f"  Ledger: {xl_row['ledger_name']}")
                print(f"  Narration: {xl_row['ledger_narration']}")
                print(f"  Pincode: {xl_row['buyer_supplier_pincode']}")

        break

    time.sleep(1)
```

### cURL

```bash
#!/bin/bash

# 1. Create session
BATCH=$(curl -s -X POST http://localhost:8000/api/sessions | jq -r '.batch_id')
echo "Batch ID: $BATCH"

# 2. Upload COA + Invoices
curl -X POST http://localhost:8000/api/sessions/$BATCH/upload \
  -F "coa_file=@chart_of_accounts.pdf" \
  -F "files=@invoice1.pdf" \
  -F "files=@invoice2.pdf" \
  -w "\nStatus: %{http_code}\n"

# 3. Check COA parsing immediately
echo "Checking COA status:"
curl -s http://localhost:8000/api/sessions/$BATCH/status | jq '.coa_status'

# 4. Poll for completion
for i in {1..30}; do
  echo "\nPoll #$i:"
  STATUS=$(curl -s http://localhost:8000/api/sessions/$BATCH/status)

  # Show COA metadata
  echo "COA Ledgers:" $(echo $STATUS | jq -r '.coa_status.metadata.total_ledgers')

  # Show invoice progress
  echo "Status:" $(echo $STATUS | jq -r '.status')
  echo "Processed:" $(echo $STATUS | jq -r '.processed')/$(echo $STATUS | jq -r '.total_files')

  if [ "$(echo $STATUS | jq -r '.status')" == "completed" ]; then
    echo "\nCompleted! XL Output for Journal Entries:"
    echo $STATUS | jq '.data[].xl_output'
    break
  fi

  sleep 1
done
```

---

## ⚙️ Configuration

### Environment Variables

```env
# Server
DEBUG=true
LOG_LEVEL=INFO
HOST=0.0.0.0
PORT=8000

# Mistral AI
MISTRAL_API_KEY=sk_your_key

# CORS
CORS_ORIGINS=["http://localhost:3000"]

# Sessions
REDIS_ENABLED=false
SESSION_TIMEOUT_HOURS=4

# Files
MAX_FILE_SIZE=50000000
MAX_FILES_PER_BATCH=20

# COA Parser
X_VARIATION=0.8    # X-coordinate clustering tolerance
Y_VARIATION=0.3    # Y-coordinate grouping tolerance
```

---

## 🚨 Error Responses

All errors follow this format:

```json
{
  "detail": "Error message here",
  "status_code": 400
}
```

### Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `COA file is required` | No COA file in upload | Include coa_file parameter |
| `COA file must be a PDF` | Wrong COA file type | Use only .pdf for COA |
| `Batch not found or expired` | Session expired | Create new session |
| `No files provided` | Empty invoice upload | Include at least 1 invoice PDF |
| `File too large` | Exceeds 50MB limit | Split or compress files |
| `Only PDF files are supported` | Wrong file type | Use only .pdf files |
| `Invalid PDF` | Corrupted PDF | Verify file integrity |
| `COA parsing failed` | Invalid COA structure | Check COA format |

---

## 📊 Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success (GET) |
| 201 | Created (POST) |
| 202 | Accepted (processing started) |
| 400 | Bad request |
| 404 | Not found |
| 413 | Payload too large |
| 415 | Unsupported media type |
| 500 | Server error |

---

## ✅ Testing Checklist

- [ ] Backend running on port 8000
- [ ] Health check returns 200
- [ ] Session creation returns batch_id
- [ ] COA + invoice upload returns 202
- [ ] COA status shows "parsed" immediately
- [ ] COA metadata shows total_ledgers, levels_discovered
- [ ] COA hierarchy structure returned
- [ ] COA flat_list contains all ledger accounts
- [ ] Status polling shows file_statuses
- [ ] Completed status returns data array
- [ ] Invoice results contain invoice_number, vendor_name, total_amount
- [ ] **XL Output present in each invoice result** ✨ NEW
- [ ] **Voucher numbers are sequential (1, 2, 3...)** ✨ NEW
- [ ] **Pincodes extracted correctly** ✨ NEW
- [ ] **Ledger amount uses subtotal when GST exists** ✨ NEW
- [ ] **Line items concatenated in narration** ✨ NEW
- [ ] Logs visible in logs/app.log

---

## 🔗 Integration

**Frontend URL:** `http://localhost:3000`
**Backend URL:** `http://localhost:8000`
**API Base:** `http://localhost:8000/api`

Update frontend `.env`:
```env
VITE_BACKEND_URL=http://localhost:8000
```

---

## 📖 More Info

- Full API docs: `README.md`
- Setup guide: `BACKEND_SETUP_GUIDE.md`
- Architecture: `Project_Plan`
- Previous versions:
  - v1: `API_QUICK_REFERENCE.md`
  - v2: `API_QUICK_REFERENCE_v2.md` (COA Support)

---

## 🆕 What's New in v4.0

### Config Management APIs ✨

- **`GET /api/config`** — read all 3 LLM system prompts + full TDS rates in one call
- **`PUT /api/config/prompts/dr|cr|tds`** — update any prompt at runtime (`text/plain` body, no JSON escaping)
- **`PUT /api/config/tds-rates`** — replace full TDS rates JSON, validated + hot-reloaded instantly
- **Redis-backed**: prompts and TDS rates stored in Redis at runtime; disk files are seed-only
- **CI/CD safe**: `git pull` + restart never overwrites user customisations already in Redis
- **Zero-restart updates**: prompt/rate changes are live immediately for all new invoices

---

## 🆕 What's New in v3.0

### XL Output for Journal Entries ✨
- **Automatic generation**: Each processed invoice includes `xl_output` array
- **Sequential voucher numbers**: Atomic counter ensures 1, 2, 3... sequence
- **Smart pincode extraction**: Regex-based extraction from vendor address
- **GST-aware ledger amounts**: Uses subtotal if GST exists, else total_amount
- **Concatenated narrations**: All line items joined with "; " separator

### XL Output Fields (9 total):
1. `voucher_date` - Invoice date
2. `voucher_type_name` - Always "Journal"
3. `voucher_number` - Sequential (1, 2, 3...)
4. `buyer_supplier_address` - Vendor address
5. `buyer_supplier_pincode` - Extracted 6-digit pincode (or null)
6. `ledger_name` - "ABC" placeholder (future: LLM-based COA mapping)
7. `ledger_amount` - Subtotal or total based on GST
8. `ledger_amount_dr_cr` - Always "Dr"
9. `ledger_narration` - Line items concatenated

### Technical Implementation:
- **Session-level voucher counter**: Atomic increments (thread-safe with Redis)
- **Pincode regex**: `\b\d{6}\b` with space normalization
- **GST detection**: Checks CGST/SGST/IGST > 0
- **Backward compatible**: `xl_output` is optional field
- **Zero performance impact**: ~0.2ms per invoice

### Future Roadmap:
- **Phase 2**: Replace `ledger_name: "ABC"` with Ollama gemma2:2b
- **Phase 3**: Multiple rows per invoice for ledger mapping
- **Phase 4**: Excel file export endpoint

---

### Changelog: v2.0 → v3.0

**Added:**
- ✅ `xl_output` array in ProcessedInvoiceResult
- ✅ Sequential voucher numbering per batch
- ✅ Pincode extraction from addresses
- ✅ GST-aware ledger amount calculation
- ✅ Line item narration aggregation
- ✅ XLOutputRow Pydantic model
- ✅ XLOutputGenerator service
- ✅ Session voucher counter (both In-Memory and Redis)

**Maintained:**
- ✅ Full backward compatibility
- ✅ All v2.0 features (COA parsing, status tracking)
- ✅ Same API endpoints and request/response formats
- ✅ No breaking changes

---

**Last Updated:** 2026-02-24
**API Version:** 4.0
**Backend Version:** With COA Support + XL Output + Config Management (Redis-backed)
