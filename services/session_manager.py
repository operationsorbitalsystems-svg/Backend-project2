import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import json
import logging
from config import SESSION_TIMEOUT_SECONDS, REDIS_ENABLED, REDIS_URL

logger = logging.getLogger("invoice_parser")

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
            "expires_at": (now + timedelta(seconds=SESSION_TIMEOUT_SECONDS)).isoformat()
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
        
        self.sessions[batch_id].update(data)
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


class RedisSessionManager:
    """Redis-based session storage for production"""
    
    def __init__(self):
        try:
            import redis
            self.redis_client = redis.from_url(REDIS_URL, decode_responses=True)
            self.redis_client.ping()
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
            "expires_at": (now + timedelta(seconds=SESSION_TIMEOUT_SECONDS)).isoformat()
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
        
        return session_data
    
    def update_session(self, batch_id: str, data: Dict[str, Any]) -> bool:
        """Update session data"""
        key = f"session:{batch_id}"
        
        if not self.redis_client.exists(key):
            return False
        
        # Convert files list to JSON if present
        if "files" in data:
            data["files"] = json.dumps(data["files"])
        
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
                elif status in ["completed", "failed"]:
                    file_record["processed_at"] = datetime.utcnow().isoformat()
                
                if error:
                    file_record["error"] = error
                
                self.redis_client.hset(key, "files", json.dumps(files))
                return True
        
        return False


# Factory function to get the appropriate session manager
def get_session_manager():
    """Return appropriate session manager based on configuration"""
    if REDIS_ENABLED:
        return RedisSessionManager()
    else:
        return InMemorySessionManager()
