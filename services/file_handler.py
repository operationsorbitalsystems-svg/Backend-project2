import os
import shutil
from pathlib import Path
from typing import List, Tuple
import logging
from config import TEMP_STORAGE_PATH, MAX_FILES_PER_BATCH
from utils.validators import validate_pdf_file, validate_file_size

logger = logging.getLogger("invoice_parser")


class FileHandler:
    """Handles file uploads, storage, and cleanup"""
    
    def __init__(self):
        self.temp_path = Path(TEMP_STORAGE_PATH)
        self.temp_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"FileHandler initialized with temp path: {self.temp_path}")
    
    def create_batch_directory(self, batch_id: str) -> Path:
        """Create a directory for batch files"""
        batch_dir = self.temp_path / batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created batch directory: {batch_dir}")
        return batch_dir
    
    def get_batch_directory(self, batch_id: str) -> Path:
        """Get the batch directory"""
        return self.temp_path / batch_id
    
    def save_file(self, batch_id: str, filename: str, file_content: bytes) -> Tuple[bool, str, str]:
        """
        Save uploaded file to batch directory
        Returns: (success, message, file_path)
        """
        try:
            # Validate file size
            is_valid, msg = validate_file_size(len(file_content))
            if not is_valid:
                return False, msg, ""
            
            # Create batch directory if needed
            batch_dir = self.create_batch_directory(batch_id)
            
            # Save file
            file_path = batch_dir / filename
            with open(file_path, 'wb') as f:
                f.write(file_content)
            
            # Validate PDF
            is_valid, msg = validate_pdf_file(str(file_path), filename)
            if not is_valid:
                file_path.unlink()  # Delete invalid file
                return False, f"Invalid PDF: {msg}", ""
            
            logger.info(f"File saved: {file_path}")
            return True, "File saved successfully", str(file_path)
        
        except Exception as e:
            logger.error(f"Error saving file {filename}: {str(e)}")
            return False, f"Error saving file: {str(e)}", ""
    
    def get_batch_files(self, batch_id: str) -> List[Path]:
        """Get all PDF files in a batch directory"""
        batch_dir = self.get_batch_directory(batch_id)
        
        if not batch_dir.exists():
            return []
        
        pdf_files = list(batch_dir.glob("*.pdf"))
        logger.info(f"Found {len(pdf_files)} PDF files in batch {batch_id}")
        return pdf_files
    
    def cleanup_batch(self, batch_id: str) -> bool:
        """Delete entire batch directory"""
        try:
            batch_dir = self.get_batch_directory(batch_id)
            
            if batch_dir.exists():
                shutil.rmtree(batch_dir)
                logger.info(f"Cleaned up batch directory: {batch_dir}")
                return True
            
            return True
        
        except Exception as e:
            logger.error(f"Error cleaning up batch {batch_id}: {str(e)}")
            return False
    
    def cleanup_old_batches(self, age_hours: int = 4) -> int:
        """
        Delete batches older than specified hours
        Returns: number of batches deleted
        """
        try:
            import time
            current_time = time.time()
            age_seconds = age_hours * 3600
            deleted_count = 0
            
            if not self.temp_path.exists():
                return 0
            
            for batch_dir in self.temp_path.iterdir():
                if batch_dir.is_dir():
                    dir_age = current_time - batch_dir.stat().st_mtime
                    if dir_age > age_seconds:
                        shutil.rmtree(batch_dir)
                        logger.info(f"Deleted old batch directory: {batch_dir}")
                        deleted_count += 1
            
            return deleted_count
        
        except Exception as e:
            logger.error(f"Error during old batch cleanup: {str(e)}")
            return 0
    
    def get_file_path(self, batch_id: str, filename: str) -> str:
        """Get full path to a file in batch"""
        return str(self.get_batch_directory(batch_id) / filename)
    
    def get_json_path(self, batch_id: str, filename: str) -> str:
        """Get path where JSON result will be stored"""
        json_filename = filename.replace('.pdf', '.json')
        return str(self.get_batch_directory(batch_id) / json_filename)
    
    def save_json_result(self, batch_id: str, filename: str, json_data: dict) -> Tuple[bool, str]:
        """Save JSON result for a processed invoice"""
        try:
            import json
            batch_dir = self.create_batch_directory(batch_id)
            json_filename = filename.replace('.pdf', '.json')
            json_path = batch_dir / json_filename
            
            with open(json_path, 'w') as f:
                json.dump(json_data, f, indent=2)
            
            logger.info(f"JSON result saved: {json_path}")
            return True, str(json_path)
        
        except Exception as e:
            logger.error(f"Error saving JSON result: {str(e)}")
            return False, ""
