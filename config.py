import os
import json
from dotenv import load_dotenv

load_dotenv()

# API & Server
DEBUG = os.getenv("DEBUG", "True").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Mistral AI
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")

# CORS Configuration
cors_origins_str = os.getenv("CORS_ORIGINS", '["http://localhost:3000","http://localhost:5173"]')
try:
    CORS_ORIGINS = json.loads(cors_origins_str)
except json.JSONDecodeError:
    CORS_ORIGINS = ["http://localhost:3000", "http://localhost:5173"]

# Session Management
REDIS_ENABLED = os.getenv("REDIS_ENABLED", "false").lower() == "true"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
SESSION_TIMEOUT_HOURS = int(os.getenv("SESSION_TIMEOUT_HOURS", "4"))
SESSION_TIMEOUT_SECONDS = SESSION_TIMEOUT_HOURS * 3600

# File Handling
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", "50000000"))  # 50MB per file
MAX_FILES_PER_BATCH = int(os.getenv("MAX_FILES_PER_BATCH", "20"))

# Server Configuration
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# Temp storage
TEMP_STORAGE_PATH = "/tmp/invoice_uploads"
