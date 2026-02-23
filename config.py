import os
import json
from dotenv import load_dotenv
import asyncio
import redis.asyncio as redis


load_dotenv()




# API & Server
DEBUG = os.getenv("DEBUG", "True").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

MAX_MAIN_WORKERS = int(os.getenv("MAX_MAIN_WORKERS", "100"))
MAX_MISTRAL_CONCURRENT = int(os.getenv("MAX_MISTRAL_CONCURRENT", "5"))
MAX_OLLAMA_CONCURRENT_CALLS = int(os.getenv("MAX_OLLAMA_CONCURRENT_CALLS", "3"))
MAX_BEDROCK_CONCURRENT_CALLS = int(os.getenv("MAX_BEDROCK_CONCURRENT_CALLS", "5"))

mistral_semaphore = asyncio.Semaphore(MAX_MISTRAL_CONCURRENT)
ollama_semaphore = asyncio.Semaphore(MAX_OLLAMA_CONCURRENT_CALLS)
bedrock_semaphore = asyncio.Semaphore(MAX_BEDROCK_CONCURRENT_CALLS)

# Mistral AI
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")

# CORS Configuration
cors_origins_str = os.getenv("CORS_ORIGINS", '["http://localhost:3000","http://localhost:5173"]')
try:
    CORS_ORIGINS = json.loads(cors_origins_str)
except json.JSONDecodeError:
    CORS_ORIGINS = ["http://localhost:3000", "http://localhost:5173"]

# Session Management
REDIS_ENABLED = os.getenv("REDIS_ENABLED", "true").lower() == "true"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
SESSION_TIMEOUT_HOURS = int(os.getenv("SESSION_TIMEOUT_HOURS", "4"))
SESSION_TIMEOUT_SECONDS = SESSION_TIMEOUT_HOURS * 3600

# Initialize Redis client for task queue
redis_client = None
if REDIS_ENABLED:
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    except Exception as e:
        import logging
        logging.warning(f"Failed to connect to Redis: {e}")

# File Handling
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", "50000000"))  # 50MB per file
MAX_FILES_PER_BATCH = int(os.getenv("MAX_FILES_PER_BATCH", "20"))

OLLAMA_MODEL_NAME = os.getenv("OLLAMA_MODEL_NAME", "gemma2:2b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

NOT_FOUND = os.getenv("NOT_FOUND", "Suspense A/C")

# Server Configuration
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# Temp storage
TEMP_STORAGE_PATH = "/tmp/invoice_uploads"

TDS_FILE_PATH = os.getenv("TDS_FILE_PATH", "./data/tds_rates.json")


AWS_REGION=os.getenv("AWS_REGION","ap-south-1")

BEDROCK_MODEL_ID=os.getenv("BEDROCK_MODEL_ID","google.gemma-3-12b-it")

