# Invoice Parser API - Quick Reference (v2.0)
**Updated with Chart of Accounts (COA) Support**

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

# 4. Check status (includes COA data)
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

**Errors:**
- `400` - COA file is required
- `400` - COA file must be a PDF
- `400` - No invoice files provided / Invalid batch_id
- `404` - Batch not found or expired
- `413` - File too large (max 50MB)
- `415` - Non-PDF file
- `500` - Upload/parsing error

---

### 4️⃣ Get Status (with COA + Invoice Results)

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
      "data": {
        "header": {
          "invoice_number": "INV-001",
          "invoice_date": "2026-01-12",
          "vendor_name": "Vendor Inc.",
          "vendor_address": "123 Street, City",
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
        print("\nInvoice Results:", status["data"])
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
    echo "\nCompleted! Full results:"
    echo $STATUS | jq '.'
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
- Original API reference (v1): `API_QUICK_REFERENCE.md`

---

## 🆕 What's New in v2.0

### COA Integration
- COA file upload is now **REQUIRED** alongside invoice files
- COA parsed **immediately** using 5-phase pipeline
- Hierarchical account structure returned in status response
- Metadata includes total_pages, total_groups, total_ledgers, levels_discovered

### Enhanced Status Response
- New `coa_status` field with parsing status and metadata
- New `coa_data` field with complete hierarchy and flat list
- COA data available immediately after upload (synchronous parsing)

### File Storage
- COA files stored in dedicated subdirectory: `/COA/`
- Parsed COA saved as JSON: `/COA/coa.json`
- Session tracks COA paths and parsing status

### Processing Flow
- **COA**: Synchronous (parsed during upload)
- **Invoices**: Asynchronous (background task queue)
- Both statuses tracked independently in unified response

---

**Last Updated:** 2026-01-12
**API Version:** 2.0
**Backend Version:** With COA Support
