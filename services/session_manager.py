from config import REDIS_ENABLED
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import json
import logging
from config import SESSION_TIMEOUT_SECONDS, REDIS_ENABLED, REDIS_URL, redis_sync_client

from utils.logger import setup_logger

logger = setup_logger()


class RedisSessionManager:
    """Redis-based session storage for production"""
    
    def __init__(self):
        try:
            self.redis_client = redis_sync_client
            # self.redis_client.ping()
            logger.info("Redis connection established")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise
    
    def create_session(self) -> str:
        """Create a new session and return batch_id"""
        batch_id = str(uuid.uuid4())
        now = datetime.utcnow()

        session_data = {
            "batch_id": batch_id,
            "created_at": now.isoformat(),
            "status": "ready",
            "files": json.dumps([]),
            "file_count": 0,
            "expires_at": (now + timedelta(seconds=SESSION_TIMEOUT_SECONDS)).isoformat(),
            "voucher_counter": 0,  # Sequential counter for voucher numbers
            # COA fields
            "coa_filename": "",
            "coa_pdf_path": "",
            "coa_json_path": "",
            "coa_status": "pending",
            "coa_parsed_at": "",
            "coa_error": "",
            "coa_metadata": ""
        }

        key = f"session:{batch_id}"
        self.redis_client.hset(key, mapping=session_data)
        self.redis_client.expire(key, SESSION_TIMEOUT_SECONDS)

        logger.info(f"Session created: {batch_id}")
        return batch_id
    
    def get_session(self, batch_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve session by batch_id"""
        key = f"session:{batch_id}"
        session_data = self.redis_client.hgetall(key)

        if not session_data:
            logger.warning(f"Session not found: {batch_id}")
            return None

        # Parse files list
        session_data["files"] = json.loads(session_data.get("files", "[]"))
        session_data["file_count"] = int(session_data.get("file_count", 0))

        # Parse processed_results list
        processed_results_json = session_data.get("processed_results", "[]")
        if processed_results_json:
            try:
                session_data["processed_results"] = json.loads(processed_results_json)
            except (json.JSONDecodeError, TypeError):
                session_data["processed_results"] = []
        else:
            session_data["processed_results"] = []

        # Parse coa_metadata if present
        coa_metadata_json = session_data.get("coa_metadata", "")
        if coa_metadata_json:
            try:
                session_data["coa_metadata"] = json.loads(coa_metadata_json)
            except (json.JSONDecodeError, TypeError):
                session_data["coa_metadata"] = None
        else:
            session_data["coa_metadata"] = None

        # Convert empty strings to None for COA fields
        for field in ["coa_filename", "coa_pdf_path", "coa_json_path", "coa_parsed_at", "coa_error"]:
            if session_data.get(field) == "":
                session_data[field] = None

        return session_data
    
    def update_session(self, batch_id: str, data: Dict[str, Any]) -> bool:
        """Update session data"""
        key = f"session:{batch_id}"

        if not self.redis_client.exists(key):
            return False

        # Convert lists to JSON if present
        if "files" in data and isinstance(data["files"], list):
            data["files"] = json.dumps(data["files"])

        if "processed_results" in data and isinstance(data["processed_results"], list):
            data["processed_results"] = json.dumps(data["processed_results"])

        # Convert coa_metadata to JSON if present
        if "coa_metadata" in data and isinstance(data["coa_metadata"], dict):
            data["coa_metadata"] = json.dumps(data["coa_metadata"])

        # Convert None to empty string for Redis storage
        for key_name, value in data.items():
            if value is None:
                data[key_name] = ""

        self.redis_client.hset(key, mapping=data)
        return True
    
    def add_file_to_session(self, batch_id: str, filename: str) -> bool:
        """Add file to session"""
        key = f"session:{batch_id}"
        
        if not self.redis_client.exists(key):
            return False
        
        files_json = self.redis_client.hget(key, "files")
        files = json.loads(files_json or "[]")
        
        files.append({
            "filename": filename,
            "status": "pending",
            "started_at": None,
            "processed_at": None,
            "error": None
        })
        
        self.redis_client.hset(key, mapping={
            "files": json.dumps(files),
            "file_count": len(files)
        })
        
        return True
    
    def update_file_status(self, batch_id: str, filename: str, status: str,
                          error: Optional[str] = None) -> bool:
        """Update file status in session"""
        key = f"session:{batch_id}"
        
        if not self.redis_client.exists(key):
            return False
        
        files_json = self.redis_client.hget(key, "files")
        files = json.loads(files_json or "[]")
        
        for file_record in files:
            if file_record["filename"] == filename:
                file_record["status"] = status
                
                if status == "processing":
                    file_record["started_at"] = datetime.utcnow().isoformat()
                elif status in ["completed", "failed", "success"]:
                    file_record["processed_at"] = datetime.utcnow().isoformat()
                
                if error:
                    file_record["error"] = error
                
                self.redis_client.hset(key, "files", json.dumps(files))
                return True

        return False

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
        Atomically increment and return next voucher number

        Uses Redis HINCRBY for atomic increment to ensure thread-safety
        in multi-worker environments.

        Args:
            batch_id: Session batch ID

        Returns:
            Next voucher number (1, 2, 3...)
        """
        key = f"session:{batch_id}"

        # HINCRBY is atomic in Redis - increments and returns new value
        new_value = self.redis_client.hincrby(key, "voucher_counter", 1)

        return new_value  # Returns 1, 2, 3, etc.



# Factory function to get the appropriate session manager
def get_session_manager():
    """Return appropriate session manager based on configuration"""
    return RedisSessionManager()
