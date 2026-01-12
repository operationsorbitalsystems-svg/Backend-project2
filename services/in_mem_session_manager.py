import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import json
import logging
from config import SESSION_TIMEOUT_SECONDS, REDIS_ENABLED, REDIS_URL

from utils.logger import setup_logger

logger = setup_logger()

class InMemorySessionManager:
    """In-memory session storage for development/MVP"""
    
    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}
    
    def create_session(self) -> str:
        """Create a new session and return batch_id"""
        batch_id = str(uuid.uuid4())
        now = datetime.utcnow()

        self.sessions[batch_id] = {
            "batch_id": batch_id,
            "created_at": now.isoformat(),
            "status": "ready",
            "files": [],
            "file_count": 0,
            "expires_at": (now + timedelta(seconds=SESSION_TIMEOUT_SECONDS)).isoformat(),
            "voucher_counter": 0,  # Sequential counter for voucher numbers
            # COA fields
            "coa_filename": None,
            "coa_pdf_path": None,
            "coa_json_path": None,
            "coa_status": "pending",
            "coa_parsed_at": None,
            "coa_error": None,
            "coa_metadata": None
        }

        logger.info(f"Session created: {batch_id}")
        return batch_id
    
    def get_session(self, batch_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve session by batch_id"""
        if batch_id not in self.sessions:
            logger.warning(f"Session not found: {batch_id}")
            return None
        
        session = self.sessions[batch_id]
        
        # Check if session expired
        expires_at = datetime.fromisoformat(session["expires_at"])
        if datetime.utcnow() > expires_at:
            logger.warning(f"Session expired: {batch_id}")
            del self.sessions[batch_id]
            return None
        
        return session
    
    def update_session(self, batch_id: str, data: Dict[str, Any]) -> bool:
        """Update session data"""
        if batch_id not in self.sessions:
            return False
        
        # Ensure lists are stored as lists (not JSON strings)
        processed_data = data.copy()
        if "processed_results" in processed_data and isinstance(processed_data["processed_results"], str):
            try:
                processed_data["processed_results"] = json.loads(processed_data["processed_results"])
            except (json.JSONDecodeError, TypeError):
                pass
        
        if "files" in processed_data and isinstance(processed_data["files"], str):
            try:
                processed_data["files"] = json.loads(processed_data["files"])
            except (json.JSONDecodeError, TypeError):
                pass
        
        self.sessions[batch_id].update(processed_data)
        return True
    
    def add_file_to_session(self, batch_id: str, filename: str) -> bool:
        """Add file to session"""
        session = self.get_session(batch_id)
        if not session:
            return False
        
        self.sessions[batch_id]["files"].append({
            "filename": filename,
            "status": "pending",
            "started_at": None,
            "processed_at": None,
            "error": None
        })
        self.sessions[batch_id]["file_count"] = len(self.sessions[batch_id]["files"])
        return True
    
    def update_file_status(self, batch_id: str, filename: str, status: str, 
                          error: Optional[str] = None) -> bool:
        """Update file status in session"""
        session = self.get_session(batch_id)
        if not session:
            return False
        
        for file_record in self.sessions[batch_id]["files"]:
            if file_record["filename"] == filename:
                file_record["status"] = status
                
                if status == "processing":
                    file_record["started_at"] = datetime.utcnow().isoformat()
                elif status in ["completed", "failed"]:
                    file_record["processed_at"] = datetime.utcnow().isoformat()
                
                if error:
                    file_record["error"] = error
                
                return True
        
        return False
    
    def cleanup_expired_sessions(self):
        """Remove expired sessions"""
        now = datetime.utcnow()
        expired = []

        for batch_id, session in self.sessions.items():
            expires_at = datetime.fromisoformat(session["expires_at"])
            if now > expires_at:
                expired.append(batch_id)

        for batch_id in expired:
            del self.sessions[batch_id]
            logger.info(f"Cleaned up expired session: {batch_id}")

        return len(expired)

    def set_coa_paths(self, batch_id: str, filename: str, pdf_path: str, json_path: str) -> bool:
        """Store COA file paths in session"""
        return self.update_session(batch_id, {
            "coa_filename": filename,
            "coa_pdf_path": pdf_path,
            "coa_json_path": json_path
        })

    def update_coa_status(self, batch_id: str, status: str,
                          coa_metadata: Optional[Dict[str, Any]] = None,
                          error: Optional[str] = None) -> bool:
        """Update COA processing status in session"""
        update_data = {
            "coa_status": status,
            "coa_parsed_at": datetime.utcnow().isoformat() if status == "parsed" else None
        }

        if coa_metadata:
            update_data["coa_metadata"] = coa_metadata

        if error:
            update_data["coa_error"] = error

        return self.update_session(batch_id, update_data)

    def get_next_voucher_number(self, batch_id: str) -> int:
        """
        Get next voucher number and increment counter

        Args:
            batch_id: Session batch ID

        Returns:
            Next voucher number (1, 2, 3...)
        """
        if batch_id not in self.sessions:
            return 1

        if "voucher_counter" not in self.sessions[batch_id]:
            self.sessions[batch_id]["voucher_counter"] = 0

        self.sessions[batch_id]["voucher_counter"] += 1
        return self.sessions[batch_id]["voucher_counter"]

