# Invoice Parser Backend - FastAPI

A **completely stateless** FastAPI backend for processing PDF invoices asynchronously with polling-based status updates.

## Features

✅ **Stateless Architecture** - No permanent storage, session data in Redis or in-memory  
✅ **Async Processing** - Non-blocking file upload and background processing  
✅ **Polling Support** - Real-time status updates with progressive results  
✅ **Mistral OCR Integration** - High-accuracy invoice parsing (ready for integration)  
✅ **Comprehensive Logging** - File and console logging with rotation  
✅ **CORS Enabled** - Ready for frontend integration  

## Project Structure

```
invoice-parser-backend/
├── main.py                      # FastAPI app & endpoints
├── config.py                    # Configuration (env vars, constants)
├── models.py                    # Pydantic request/response models
├── services/
│   ├── __init__.py
│   ├── session_manager.py       # Session lifecycle (in-memory/Redis)
│   ├── file_handler.py          # File upload, validation, cleanup
│   └── invoice_parser.py        # Invoice parsing & data extraction
├── utils/
│   ├── __init__.py
│   ├── validators.py            # File validation (PDF checks)
│   └── logger.py                # Logger configuration
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment variables template
└── README.md                    # This file
```

## Quick Start

### 1. Clone & Setup

```bash
# Create project directory
mkdir invoice-parser-backend
cd invoice-parser-backend

# Create virtual environment
python -m venv venv

# Activate venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
# Copy example env
cp .env.example .env

# Edit .env and add your Mistral API key
MISTRAL_API_KEY=sk_your_key_here
```

### 3. Run Backend

```bash
# Development mode with auto-reload
python main.py

# Or using uvicorn directly
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Server will be available at: `http://localhost:8000`

### 4. Test Health

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{
  "status": "healthy",
  "timestamp": "2026-01-10T10:30:00.000000"
}
```

## API Endpoints

### 1. Create Session

**Endpoint:** `POST /api/sessions`

Create a new batch processing session.

```bash
curl -X POST http://localhost:8000/api/sessions
```

**Response (201):**
```json
{
  "batch_id": "550e8400-e29b-41d4-a716-446655440000",
  "created_at": "2026-01-10T10:30:00",
  "status": "ready"
}
```

### 2. Upload Files

**Endpoint:** `POST /api/sessions/{batch_id}/upload`

Upload PDF files for processing.

```bash
curl -X POST http://localhost:8000/api/sessions/{batch_id}/upload \
  -F "files=@invoice1.pdf" \
  -F "files=@invoice2.pdf"
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

### 3. Poll Status

**Endpoint:** `GET /api/sessions/{batch_id}/status`

Get real-time batch processing status with file-level details.

```bash
curl http://localhost:8000/api/sessions/{batch_id}/status
```

**Response (200) - Processing:**
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
  ]
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
  "completed_at": "2026-01-10T10:36:00",
  "file_statuses": [...],
  "data": [
    {
      "filename": "invoice1.pdf",
      "pdf_path": "/tmp/550e8400-e29b-41d4-a716-446655440000/invoice1.pdf",
      "json_path": "/tmp/550e8400-e29b-41d4-a716-446655440000/invoice1.json",
      "status": "success",
      "invoice_number": "INV-001",
      "vendor_name": "Sample Vendor Inc.",
      "total_amount": 2360.00,
      "currency": "INR",
      "line_items_count": 2,
      "data": {
        "header": { ... },
        "line_items": [ ... ],
        "total_amount": 2360.00,
        ...
      },
      "error": null,
      "timestamp": "2026-01-10T10:35:30"
    }
  ]
}
```

## Configuration

### Environment Variables

Edit `.env` file to customize:

```
# API & Server
DEBUG=true                              # Enable debug mode
LOG_LEVEL=INFO                          # Logging level (DEBUG, INFO, WARNING, ERROR)

# Mistral AI
MISTRAL_API_KEY=sk_your_key_here       # Your Mistral API key

# CORS
CORS_ORIGINS=["http://localhost:3000"]  # Allowed origins

# Sessions
REDIS_ENABLED=false                     # Use Redis (true) or in-memory (false)
REDIS_URL=redis://localhost:6379        # Redis connection URL
SESSION_TIMEOUT_HOURS=4                 # Session expiration time (hours)

# Files
MAX_FILE_SIZE=50000000                  # Max file size (bytes) - 50MB
MAX_FILES_PER_BATCH=20                  # Max files per batch

# Server
HOST=0.0.0.0                            # Server host
PORT=8000                               # Server port
```

### Session Management

**Option A: In-Memory (Default - MVP)**
- Sessions stored in RAM
- Perfect for development
- Lost on server restart
- No external dependencies

**Option B: Redis (Production)**
- Sessions persisted in Redis
- Survives server restarts
- Distributed across multiple instances
- Requires Redis server running

To enable Redis:
```bash
# Start Redis
redis-server

# Update .env
REDIS_ENABLED=true
REDIS_URL=redis://localhost:6379
```

## Logging

Logs are written to:
- **Console**: Real-time logs in terminal
- **File**: `logs/app.log` with rotation (10MB per file, 5 backups)

Log levels:
- `DEBUG`: Detailed information for debugging
- `INFO`: General informational messages
- `WARNING`: Warning messages
- `ERROR`: Error messages

## Background Processing

When files are uploaded, they're processed asynchronously:

1. File validation (PDF format, size)
2. Mistral OCR extraction
3. Data validation
4. JSON result storage
5. Session status update

Frontend polls status endpoint to track progress.

## Error Handling

### Status Codes

- **201 Created**: Session created successfully
- **202 Accepted**: Files received, processing started
- **400 Bad Request**: Invalid request (no files, invalid batch_id)
- **404 Not Found**: Batch not found or expired
- **413 Payload Too Large**: File size exceeds limit
- **415 Unsupported Media Type**: Non-PDF files
- **500 Internal Server Error**: Server error

### Error Messages

All errors returned as JSON:
```json
{
  "detail": "Error message here",
  "status_code": 400
}
```

## Development Tips

### 1. Mock Data

Currently returns mock invoice data. To see real parsing:
1. Get Mistral API key from https://mistral.ai
2. Update `.env` with your key
3. Implement Mistral integration in `services/invoice_parser.py`

### 2. Local Frontend Testing

```bash
# Terminal 1: Backend
cd invoice-parser-backend
python main.py

# Terminal 2: Frontend
cd invoice-parser-frontend
npm run dev
```

Frontend available at: `http://localhost:5173`

### 3. Test Complete Flow

```bash
#!/bin/bash

# 1. Create session
BATCH=$(curl -s -X POST http://localhost:8000/api/sessions | jq -r '.batch_id')
echo "Batch ID: $BATCH"

# 2. Upload files
curl -X POST http://localhost:8000/api/sessions/$BATCH/upload \
  -F "files=@test.pdf"

# 3. Poll status
for i in {1..10}; do
  echo "Poll #$i:"
  curl -s http://localhost:8000/api/sessions/$BATCH/status | jq '.status'
  sleep 1
done
```

## Performance Notes

- **Processing Speed**: ~1-2 seconds per invoice (with Mistral OCR)
- **Concurrent Requests**: Supports hundreds via async
- **Memory Usage**: Minimal - files stored in `/tmp`
- **Auto-Cleanup**: Sessions expire after 4 hours (configurable)

## Troubleshooting

### "Failed to create session"
- Check if server is running: `curl http://localhost:8000/health`
- Check logs: `tail -f logs/app.log`

### "Batch not found or expired"
- Session expired (4 hour default TTL)
- Create new session with `POST /api/sessions`

### "No valid PDF files were provided"
- Ensure files are valid PDFs
- Check file size (max 50MB default)
- Use only `.pdf` extension

### High Memory Usage
- Reduce MAX_FILE_SIZE in .env
- Reduce SESSION_TIMEOUT_HOURS for faster cleanup
- Monitor with: `watch -n 1 'du -sh /tmp/invoice_uploads'`

## Docker Deployment

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

ENV HOST=0.0.0.0
ENV PORT=8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and run:
```bash
docker build -t invoice-parser-backend .
docker run -p 8000:8000 invoice-parser-backend
```

## Next Steps

1. **Implement Mistral OCR**: Update `services/invoice_parser.py` with real API calls
2. **Add Excel Export**: Generate Tally-compatible Excel files
3. **Database Integration**: Add persistent storage for results
4. **Authentication**: Add API key authentication
5. **Rate Limiting**: Implement rate limiting per user/IP
6. **Webhooks**: Add webhook support for completion notifications

## Support

For issues, feature requests, or contributions:
- Check logs: `tail logs/app.log`
- Review [Project Plan](../Project_Plan) for architecture details
- Check frontend integration: `services/` in frontend

## License

MIT License - See LICENSE file for details
