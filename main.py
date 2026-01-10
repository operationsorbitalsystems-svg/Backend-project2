import asyncio
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from typing import List
from datetime import datetime
import logging

from config import DEBUG, LOG_LEVEL, CORS_ORIGINS, HOST, PORT, MAX_FILES_PER_BATCH
from utils.logger import setup_logger
from models import (
    SessionCreateResponse, BatchStatusResponse, UploadResponse, 
    FileStatus, ProcessedInvoiceResult
)
from services.session_manager import get_session_manager
from services.file_handler import FileHandler
from services.invoice_parser import InvoiceParser

# Setup logger
logger = setup_logger(debug=DEBUG)

# FastAPI app
app = FastAPI(
    title="Invoice Parser API",
    description="High-performance invoice parsing platform",
    version="1.0.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize services
session_manager = get_session_manager()
file_handler = FileHandler()
invoice_parser = InvoiceParser()

logger.info(f"Invoice Parser Backend Started - Debug: {DEBUG}, Log Level: {LOG_LEVEL}")


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
async def upload_files(batch_id: str, files: List[UploadFile] = File(...), background_tasks: BackgroundTasks = None):
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
                    
                    # Start background processing task
                    if background_tasks:
                        background_tasks.add_task(
                            process_invoice_background,
                            batch_id,
                            file.filename,
                            pdf_path
                        )
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
        processed = sum(1 for f in files if f["status"] in ["completed", "failed"])
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
            data=results_data if results_data else None,
            completed_at=datetime.fromisoformat(session.get("completed_at")) if session.get("completed_at") else None
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting batch status: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve batch status")


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
        
        # Update session with results
        session = session_manager.get_session(batch_id)
        if session:
            processed_results = session.get("processed_results", [])
            processed_results.append(result)
            session_manager.update_session(batch_id, {"processed_results": processed_results})
        
        # Update file status to completed
        session_manager.update_file_status(batch_id, filename, "completed")
        
        # Check if all files are processed
        session = session_manager.get_session(batch_id)
        if session:
            files = session.get("files", [])
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
# STARTUP/SHUTDOWN EVENTS
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """Startup event - initialize services"""
    logger.info("Application startup")
    # Could add periodic cleanup task here if needed


@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown event - cleanup"""
    logger.info("Application shutdown")


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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=HOST,
        port=PORT,
        reload=DEBUG,
        log_level=LOG_LEVEL.lower()
    )
