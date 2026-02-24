import asyncio
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from typing import List
from datetime import datetime
import logging

# ── Logging must be configured FIRST, before any service module imports ───────
from config import DEBUG, LOG_LEVEL, CORS_ORIGINS, HOST, PORT, MAX_FILES_PER_BATCH, MAX_MISTRAL_CONCURRENT, MAX_MAIN_WORKERS, redis_client, CLEANUP_BATCH_HOURS, CLEANUP_AGE
from utils.logger import configure_logging, setup_logger, batch_id_var

configure_logging(debug=DEBUG)

from utils.tds import MANAGER
from utils.safe_file_manager import dr_prompt_file, cr_prompt_file, tds_prompt_file, tds_file
from models import (
    SessionCreateResponse, BatchStatusResponse, UploadResponse,
    FileStatus, ProcessedInvoiceResult,
    COAStatus, COAMetadata, COAData,
    ConfigResponse, ConfigUpdateResponse, TDSRateItem,
)
from services.session_manager import get_session_manager
from services.file_handler import FileHandler
from services.invoice_parser import InvoiceParser
from services.task_queue import TaskQueueManager
from services.mistral_queue import MistralQueueManager
from services.llm_queue import get_llm_queue
from services.ollama_api_call import health_check_ollama
from coa_parser import parse_coa
import json
logger = setup_logger()

# FastAPI app
app = FastAPI(
    title="Invoice Parser API",
    description="High-performance invoice parsing platform",
    version="1.0.0"
)

# # CORS configuration
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=CORS_ORIGINS,
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,   # IMPORTANT
    allow_methods=["*"],
    allow_headers=["*"],
)


class BatchIdMiddleware(BaseHTTPMiddleware):
    """Sets batch_id_var for the duration of any request that has a batch_id path param."""
    async def dispatch(self, request: Request, call_next):
        batch_id = request.path_params.get("batch_id")
        if batch_id:
            token = batch_id_var.set(batch_id)
            try:
                return await call_next(request)
            finally:
                batch_id_var.reset(token)
        return await call_next(request)

app.add_middleware(BatchIdMiddleware)

# Initialize services
session_manager = get_session_manager()
file_handler = FileHandler()
invoice_parser = InvoiceParser()

# Initialize Mistral queue
mistral_queue = MistralQueueManager(
    redis_client=redis_client,
    invoice_parser=invoice_parser,
    file_handler=file_handler
)

# Initialize LLM queue
llm_queue = get_llm_queue()

# Initialize main task queue
task_queue = TaskQueueManager(
    redis_client=redis_client,
    mistral_queue=mistral_queue,
    llm_queue=llm_queue,
    session_manager=session_manager,
    file_handler=file_handler
)

logger.info(f"Invoice Parser Backend Started - Debug: {DEBUG}, Log Level: {LOG_LEVEL}")

# ============================================================================
# UTIL FUNCTIONS
# ============================================================================

def coa_parsing(batch_id: str, coa_file_content, coa_file_name):
    try:
        # Save COA PDF
        success, msg, coa_pdf_path = file_handler.save_coa_file(
            batch_id, coa_file_name, coa_file_content
        )

        if not success:
            raise HTTPException(status_code=400, detail=f"Failed to save COA file: {msg}")

        # Get COA JSON path
        coa_json_path = file_handler.get_coa_json_path(batch_id)

        # Store COA paths in session
        session_manager.set_coa_paths(batch_id, coa_file_name, coa_pdf_path, coa_json_path)

        # Parse COA immediately (synchronous)
        logger.info(f"Parsing COA file: {coa_pdf_path}")
        coa_output = parse_coa(coa_pdf_path, debug=False)

        # Save COA output as JSON
        import json
        with open(coa_json_path, 'w') as f:
            json.dump(coa_output.model_dump(mode='json'), f, indent=2, default=str)

        # Extract metadata for session
        coa_metadata = {
            "total_pages": coa_output.metadata.total_pages,
            "total_groups": coa_output.metadata.total_groups,
            "total_ledgers": coa_output.metadata.total_ledgers,
            "levels_discovered": coa_output.metadata.levels_discovered
        }

        # Update COA status to "parsed"
        session_manager.update_coa_status(batch_id, "parsed", coa_metadata=coa_metadata)
        logger.info(f"COA parsed successfully for batch {batch_id}")
    except Exception as e:
        logger.error(f"Error processing COA file: {str(e)}")
        session_manager.update_coa_status(batch_id, "failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to process COA file: {str(e)}")


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.post("/api/sessions", response_model=SessionCreateResponse, status_code=201, tags=["Sessions"])
async def create_session():
    """
    Create a new session for batch processing
    
    Returns:
        - batch_id: Unique identifier for the batch
        - created_at: Session creation timestamp
        - status: Initial status (always "ready")
    """
    try:
        batch_id = session_manager.create_session()
        
        session = session_manager.get_session(batch_id)
        if not session:
            raise HTTPException(status_code=500, detail="Failed to create session")
        
        return SessionCreateResponse(
            batch_id=batch_id,
            created_at=datetime.fromisoformat(session["created_at"]),
            status=session["status"]
        )
    
    except Exception as e:
        logger.error(f"Error creating session: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create session")


@app.post("/api/sessions/{batch_id}/upload", response_model=UploadResponse, status_code=202, tags=["Files"])
async def upload_files(batch_id: str, coa_file: UploadFile = File(...), files: List[UploadFile] = File(...)):
    """
    Upload PDF files for batch processing
    
    Args:
        batch_id: Session batch ID
        files: List of PDF files to process
    
    Returns:
        - batch_id: Confirmed batch ID
        - received_files: Number of files received
        - status: Processing status
        - message: Operation message
    """
    try:
        # Verify session exists
        session = session_manager.get_session(batch_id)
        if not session:
            logger.warning(f"Session not found: {batch_id}")
            raise HTTPException(status_code=404, detail="Batch not found or expired")

        # === Process COA File ===
        if not coa_file or not coa_file.filename:
            raise HTTPException(status_code=400, detail="COA file is required")

        if not coa_file.filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="COA file must be a PDF")

        try:
            # Read COA file content
            coa_content = await coa_file.read()
            coa_filename = coa_file.filename
            
            coa_parsing(batch_id, coa_content, coa_filename)

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error processing COA file: {str(e)}")
            session_manager.update_coa_status(batch_id, "failed", error=str(e))
            raise HTTPException(status_code=500, detail=f"Failed to process COA file: {str(e)}")
        
        # Validate file count
        if not files or len(files) == 0:
            raise HTTPException(status_code=400, detail="No files provided")
        
        if len(files) > MAX_FILES_PER_BATCH:
            raise HTTPException(
                status_code=413, 
                detail=f"Too many files. Maximum {MAX_FILES_PER_BATCH} allowed"
            )
        
        
        # Create batch directory
        file_handler.create_batch_directory(batch_id)
        
        # Save files and add to session
        saved_count = 0
        for file in files:
            try:
                # Validate filename
                if not file.filename:
                    continue
                
                if not file.filename.lower().endswith('.pdf'):
                    logger.warning(f"Non-PDF file rejected: {file.filename}")
                    continue
                
                # Read file content
                content = await file.read()
                
                # Save file
                success, msg, pdf_path = file_handler.save_file(batch_id, file.filename, content)
                
                if success:
                    session_manager.add_file_to_session(batch_id, file.filename)
                    saved_count += 1

                    # Add to task queue for fair processing
                    await task_queue.enqueue_file(batch_id, file.filename, pdf_path)
                else:
                    logger.warning(f"Failed to save file {file.filename}: {msg}")
            
            except Exception as e:
                logger.error(f"Error processing file {file.filename}: {str(e)}")
                continue
        
        if saved_count == 0:
            raise HTTPException(status_code=415, detail="No valid PDF files were provided")
        
        # Update session status
        session_manager.update_session(batch_id, {"status": "processing"})
        
        return UploadResponse(
            batch_id=batch_id,
            received_files=saved_count,
            coa_received=True,
            status="processing",
            message=f"Files received. Processing started."
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading files: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to process upload")


@app.get("/api/sessions/{batch_id}/status", response_model=BatchStatusResponse, tags=["Status"])
async def get_batch_status(batch_id: str):
    """
    Get batch processing status with live file status updates and results
    
    Args:
        batch_id: Session batch ID
    
    Returns:
        - batch_id: Batch ID
        - status: Current status (processing, completed, partial_complete)
        - total_files: Total files in batch
        - processed: Number of processed files
        - pending: Number of pending files
        - failed: Number of failed files
        - file_statuses: List of individual file statuses
        - data: Array of processed invoices (when complete)
        - completed_at: Completion timestamp (if done)
    """
    try:
        # Get session
        session = session_manager.get_session(batch_id)
        if not session:
            logger.warning(f"Status requested for non-existent session: {batch_id}")
            raise HTTPException(status_code=404, detail="Batch not found or expired")
        
        # Count file statuses
        files = session.get("files", [])
        processed = sum(1 for f in files if f["status"] in ["completed", "failed", "success"])
        pending = sum(1 for f in files if f["status"] == "pending")
        failed = sum(1 for f in files if f["status"] == "failed")
        total = len(files)
        
        # Determine overall status
        if total == 0:
            overall_status = "ready"
        elif processed == total:
            overall_status = "completed"
        elif processed > 0:
            overall_status = "partial_complete"
        else:
            overall_status = "processing"
        
        # Build file statuses
        file_statuses = []
        for file_record in files:
            file_status = FileStatus(
                filename=file_record["filename"],
                status=file_record["status"],
                processed_at=datetime.fromisoformat(file_record["processed_at"]) if file_record["processed_at"] else None,
                started_at=datetime.fromisoformat(file_record["started_at"]) if file_record["started_at"] else None,
                error=file_record.get("error")
            )
            file_statuses.append(file_status)

        # === Build COA Status ===
        coa_status_obj = None
        coa_data_obj = None

        if session.get("coa_filename"):
            # Build COA status
            coa_metadata_dict = session.get("coa_metadata")
            coa_metadata_obj = None
            if coa_metadata_dict:
                coa_metadata_obj = COAMetadata(**coa_metadata_dict)

            coa_status_obj = COAStatus(
                filename=session.get("coa_filename"),
                status=session.get("coa_status", "pending"),
                parsed_at=datetime.fromisoformat(session["coa_parsed_at"]) if session.get("coa_parsed_at") else None,
                error=session.get("coa_error"),
                metadata=coa_metadata_obj
            )

            # Load full COA data from JSON file if parsed successfully
            if session.get("coa_status") == "parsed":
                coa_json_data = file_handler.read_coa_json(batch_id)
                if coa_json_data:
                    coa_data_obj = COAData(
                        metadata=coa_json_data.get("metadata", {}),
                        hierarchy=coa_json_data.get("hierarchy", {}),
                        flat_list=coa_json_data.get("flat_list", [])
                    )
        
        # Get processed results from session data
        processed_results = session.get("processed_results", [])
        
        # Convert to response models
        results_data = []
        if processed_results:
            for result in processed_results:
                # Parse data if it's a string
                if isinstance(result.get("data"), str):
                    import json
                    result["data"] = json.loads(result["data"])
                results_data.append(ProcessedInvoiceResult(**result))
        
        return BatchStatusResponse(
            batch_id=batch_id,
            status=overall_status,
            total_files=total,
            processed=processed,
            pending=pending,
            failed=failed,
            file_statuses=file_statuses,
            coa_status=coa_status_obj,
            coa_data=coa_data_obj,
            data=results_data if results_data else None,
            completed_at=datetime.fromisoformat(session.get("completed_at")) if session.get("completed_at") else None
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting batch status: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve batch status")


# ============================================================================
# CONFIG MANAGEMENT ENDPOINTS
# ============================================================================

@app.get("/api/config", response_model=ConfigResponse, tags=["Config"])
async def get_config():
    """Return all 3 prompt files and the TDS rates JSON."""
    return ConfigResponse(
        dr_prompt=dr_prompt_file.read(),
        cr_prompt=cr_prompt_file.read(),
        tds_prompt=tds_prompt_file.read(),
        tds_rates=MANAGER.data,
    )


@app.put("/api/config/prompts/dr", response_model=ConfigUpdateResponse, tags=["Config"])
async def update_dr_prompt(request: Request):
    """Overwrite the DR (expense ledger) system prompt. Send as text/plain."""
    content = (await request.body()).decode("utf-8")
    dr_prompt_file.write(content)
    return ConfigUpdateResponse(message="DR prompt updated")


@app.put("/api/config/prompts/cr", response_model=ConfigUpdateResponse, tags=["Config"])
async def update_cr_prompt(request: Request):
    """Overwrite the CR (vendor/creditor ledger) system prompt. Send as text/plain."""
    content = (await request.body()).decode("utf-8")
    cr_prompt_file.write(content)
    return ConfigUpdateResponse(message="CR prompt updated")


@app.put("/api/config/prompts/tds", response_model=ConfigUpdateResponse, tags=["Config"])
async def update_tds_prompt(request: Request):
    """Overwrite the TDS nature classification system prompt. Send as text/plain."""
    content = (await request.body()).decode("utf-8")
    tds_prompt_file.write(content)
    return ConfigUpdateResponse(message="TDS prompt updated")


@app.put("/api/config/tds-rates", response_model=ConfigUpdateResponse, tags=["Config"])
async def update_tds_rates(rates: List[TDSRateItem]):
    """Replace the full TDS rates JSON and hot-reload in memory."""
    MANAGER.save_data([r.model_dump() for r in rates])
    return ConfigUpdateResponse(message=f"TDS rates updated ({len(rates)} entries)")


# ============================================================================
# BACKGROUND TASKS
# ============================================================================

async def process_invoice_background(batch_id: str, filename: str, pdf_path: str):
    """
    Background task to process an invoice
    """
    try:
        logger.info(f"Starting background processing for {filename} (batch: {batch_id})")
        
        # Update file status to processing
        session_manager.update_file_status(batch_id, filename, "processing")
        
        # Parse invoice
        success, invoice_data, error = await invoice_parser.parse_invoice(pdf_path)
        
        if not success:
            logger.error(f"Failed to parse {filename}: {error}")
            session_manager.update_file_status(batch_id, filename, "failed", error=error)
            return
        
        # Validate invoice data
        is_valid, validation_error = invoice_parser.validate_invoice_data(invoice_data)
        if not is_valid:
            logger.error(f"Invoice validation failed for {filename}: {validation_error}")
            session_manager.update_file_status(batch_id, filename, "failed", error=validation_error)
            return
        
        # Format and save result
        json_path = file_handler.get_json_path(batch_id, filename)
        result = invoice_parser.format_invoice_response(filename, pdf_path, json_path, invoice_data)
        
        # Save JSON
        success, saved_json_path = file_handler.save_json_result(batch_id, filename, result)
        
        # Update file status to completed
        session_manager.update_file_status(batch_id, filename, "completed")
        
        # Update session with results (handle both in-memory dict and Redis string formats)
        session = session_manager.get_session(batch_id)
        if session:
            # Get processed_results - could be list or JSON string depending on storage backend
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
            processed_results.append(result)
            session_manager.update_session(batch_id, {"processed_results": processed_results})
        
        # Check if all files are processed
        session = session_manager.get_session(batch_id)
        if session:
            # Get files - could be list or JSON string depending on storage backend
            files_raw = session.get("files", [])
            
            # Parse if it's a JSON string (Redis case)
            if isinstance(files_raw, str):
                try:
                    files = json.loads(files_raw)
                except (json.JSONDecodeError, TypeError):
                    files = []
            else:
                files = files_raw if isinstance(files_raw, list) else []
            
            all_processed = all(f["status"] in ["completed", "failed"] for f in files)
            if all_processed:
                logger.info(f"Batch {batch_id} processing completed")
                session_manager.update_session(batch_id, {
                    "status": "completed",
                    "completed_at": datetime.utcnow().isoformat()
                })
        
        logger.info(f"Successfully processed {filename} (batch: {batch_id})")
    
    except Exception as e:
        logger.error(f"Background processing error for {filename}: {str(e)}")
        session_manager.update_file_status(batch_id, filename, "failed", error=str(e))


# ============================================================================
# BACKGROUND CLEANUP TASK
# ============================================================================

async def cleanup_old_batches():
    """
    Periodic task to clean up old batch directories
    Runs every 1 hour
    """
    while True:
        try:
            await asyncio.sleep(CLEANUP_BATCH_HOURS*3600)  # Run every 1 hour
            logger.info("🧹 Starting cleanup of old batches...")
            
            deleted_count = file_handler.cleanup_old_batches(age_hours=CLEANUP_AGE)
            
            if deleted_count > 0:
                logger.info(f"🧹 Cleaned up {deleted_count} old batch directories")
            else:
                logger.info("🧹 No old batches to clean up")
        
        except Exception as e:
            logger.error(f"Error in cleanup task: {str(e)}")


# ============================================================================
# STARTUP/SHUTDOWN EVENTS
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """Startup event - initialize services and start cleanup task"""
    logger.info("Application startup...")

    # Health check for Ollama service
    ollama_healthy, ollama_error = await health_check_ollama()
    if not ollama_healthy:
        logger.error(f"⚠️ Ollama health check failed: {ollama_error}")
        logger.warning("⚠️ Continuing without Ollama - ledger selection will fail!")

    # Seed config into Redis from files (no-op if keys already exist)
    dr_prompt_file.seed_from_file()
    cr_prompt_file.seed_from_file()
    tds_prompt_file.seed_from_file()
    tds_file.seed_from_file()

    # Load TDS data into memory (reads from Redis via tds_file.read())
    MANAGER.load_data()

    # Recover crashed tasks from all queues
    await task_queue.recover_crashed_tasks()
    await mistral_queue.recover_crashed_tasks()
    logger.info("✅ Recovered crashed tasks from all queues")

    # Start Mistral worker pool
    await mistral_queue.start_workers(num_workers=MAX_MISTRAL_CONCURRENT)
    logger.info(f"✅ Started {MAX_MISTRAL_CONCURRENT} Mistral OCR workers")

    # Start main task queue worker pool
    await task_queue.start_workers(num_workers=MAX_MAIN_WORKERS)
    logger.info(f"✅ Started {MAX_MAIN_WORKERS} main task queue workers")

    # Start LLM worker pool
    from config import MAX_OLLAMA_CONCURRENT_CALLS
    for i in range(MAX_OLLAMA_CONCURRENT_CALLS):
        asyncio.create_task(llm_queue.worker_loop())
    logger.info(f"✅ Started {MAX_OLLAMA_CONCURRENT_CALLS} LLM workers")

    # Start background cleanup task
    asyncio.create_task(cleanup_old_batches())
    logger.info("🧹 Background cleanup task started (runs every 1 hour)")

    logger.info("Application startup complete")


@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown event - cleanup"""
    logger.info("Application shutting down...")

    # Stop Mistral workers
    await mistral_queue.stop_workers()
    logger.info("✅ Mistral workers stopped")

    logger.info("Application shutdown complete")


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return {
        "detail": exc.detail,
        "status_code": exc.status_code
    }


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {str(exc)}")
    return {
        "detail": "Internal server error",
        "status_code": 500
    }


# ============================================================================
# RUN APPLICATION
# ============================================================================

# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(
#         "main:app",
#         host=HOST,
#         port=PORT,
#         reload=DEBUG,
#         log_level=LOG_LEVEL.lower()
#     )