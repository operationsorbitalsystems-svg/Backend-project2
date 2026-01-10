from pathlib import Path
from config import MAX_FILE_SIZE

def is_valid_pdf(file_path: str) -> bool:
    """Check if file is a valid PDF"""
    try:
        # Check file magic number (PDF signature)
        with open(file_path, 'rb') as f:
            header = f.read(4)
            return header == b'%PDF'
    except Exception:
        return False


def validate_file_size(file_size: int) -> tuple[bool, str]:
    """Validate if file size is within limits"""
    if file_size > MAX_FILE_SIZE:
        return False, f"File size ({file_size} bytes) exceeds maximum allowed ({MAX_FILE_SIZE} bytes)"
    return True, ""


def validate_file_extension(filename: str) -> tuple[bool, str]:
    """Validate if file has PDF extension"""
    if not filename.lower().endswith('.pdf'):
        return False, f"Invalid file extension. Only PDF files are supported. Got: {Path(filename).suffix}"
    return True, ""


def validate_pdf_file(file_path: str, filename: str) -> tuple[bool, str]:
    """Comprehensive PDF validation"""
    # Check extension
    is_valid, msg = validate_file_extension(filename)
    if not is_valid:
        return False, msg
    
    # Check if actual PDF
    if not is_valid_pdf(file_path):
        return False, "File is not a valid PDF (invalid PDF header)"
    
    return True, ""
