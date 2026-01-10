# Invoice Parser API - Quick Reference

## 🚀 Quick Start

```bash
# 1. Start backend
python main.py

# 2. Create session
curl -X POST http://localhost:8000/api/sessions

# 3. Upload files
curl -X POST http://localhost:8000/api/sessions/{batch_id}/upload \
  -F "files=@invoice1.pdf" \
  -F "files=@invoice2.pdf"

# 4. Check status
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
  "timestamp": "2026-01-10T10:30:00"
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
  "created_at": "2026-01-10T10:30:00",
  "status": "ready"
}
```

**Errors:**
- `500` - Failed to create session

---

### 3️⃣ Upload Files

```http
POST /api/sessions/{batch_id}/upload
Content-Type: multipart/form-data
```

**Request:**
```
batch_id: (path parameter)
files: [file1.pdf, file2.pdf, ...max 20 files]
```

**Response (202):**
```json
{
  "batch_id": "550e8400-e29b-41d4-a716-446655440000",
  "received_files": 2,
  "status": "processing",
  "message": "Files received. Processing started."
}
```

**Errors:**
- `400` - No files provided / Invalid batch_id
- `404` - Batch not found or expired
- `413` - File too large (max 50MB)
- `415` - Non-PDF file (only .pdf supported)
- `500` - Upload error

---

### 4️⃣ Get Status (with Results)

```http
GET /api/sessions/{batch_id}/status
```

**Response (200) - While Processing:**
```json
{
  "batch_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing",
  "total_files": 2,
  "processed": 1,
  "pending": 1,
  "failed": 0,
  "file_statuses": [
    {
      "filename": "invoice1.pdf",
      "status": "completed",
      "processed_at": "2026-01-10T10:35:00",
      "started_at": "2026-01-10T10:34:50",
      "error": null
    },
    {
      "filename": "invoice2.pdf",
      "status": "processing",
      "processed_at": null,
      "started_at": "2026-01-10T10:34:55",
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
          "invoice_date": "2026-01-10",
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
      "timestamp": "2026-01-10T10:35:30"
    }
  ],
  "completed_at": "2026-01-10T10:36:00"
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
│    Request: PDFs                                        │
│    Response: 202 Accepted (processing started)          │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│ 3. GET /api/sessions/{batch_id}/status                  │
│    [Repeat every 1s until status == "completed"]        │
│    Response: { status, file_statuses, data: [...] }     │
└─────────────────────────────────────────────────────────┘
```

---

## 📝 Usage Examples

### JavaScript/TypeScript

```typescript
// 1. Create session
const sessionRes = await fetch('http://localhost:8000/api/sessions', {
  method: 'POST'
});
const { batch_id } = await sessionRes.json();

// 2. Upload files
const formData = new FormData();
formData.append('files', file1);
formData.append('files', file2);

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
  
  console.log(`Processed: ${status.processed}/${status.total_files}`);
  
  if (status.status === 'completed') {
    console.log('Results:', status.data);
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

# 2. Upload files
with open("invoice.pdf", "rb") as f:
    files = {"files": f}
    upload_res = requests.post(
        f"{BASE_URL}/api/sessions/{batch_id}/upload",
        files=files
    )

# 3. Poll status
while True:
    status_res = requests.get(
        f"{BASE_URL}/api/sessions/{batch_id}/status"
    )
    status = status_res.json()
    
    print(f"Status: {status['status']}")
    print(f"Processed: {status['processed']}/{status['total_files']}")
    
    if status["status"] == "completed":
        print("Results:", status["data"])
        break
    
    time.sleep(1)
```

### cURL

```bash
#!/bin/bash

# 1. Create session
BATCH=$(curl -s -X POST http://localhost:8000/api/sessions | jq -r '.batch_id')
echo "Batch ID: $BATCH"

# 2. Upload files
curl -X POST http://localhost:8000/api/sessions/$BATCH/upload \
  -F "files=@invoice1.pdf" \
  -F "files=@invoice2.pdf" \
  -w "\nStatus: %{http_code}\n"

# 3. Poll status (10 times)
for i in {1..10}; do
  echo "Poll #$i:"
  curl -s http://localhost:8000/api/sessions/$BATCH/status | jq '.status'
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

# Mistral
MISTRAL_API_KEY=sk_your_key

# CORS
CORS_ORIGINS=["http://localhost:3000"]

# Sessions
REDIS_ENABLED=false
SESSION_TIMEOUT_HOURS=4

# Files
MAX_FILE_SIZE=50000000
MAX_FILES_PER_BATCH=20
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
| `Batch not found or expired` | Session expired | Create new session |
| `No files provided` | Empty upload | Include at least 1 PDF |
| `File too large` | Exceeds 50MB limit | Split or compress files |
| `Only PDF files are supported` | Wrong file type | Use only .pdf files |
| `Invalid PDF` | Corrupted PDF | Verify file integrity |

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
- [ ] File upload returns 202
- [ ] Status polling shows file_statuses
- [ ] Completed status returns data array
- [ ] Results contain invoice_number, vendor_name, total_amount
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
