import os
import json
from dotenv import load_dotenv
import asyncio
import redis
import redis.asyncio as redis_async


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
REDIS_SSL = os.getenv("REDIS_SSL", "false").lower() == "true"
REDIS_SSL_VERIFY = os.getenv("REDIS_SSL_VERIFY", "true").lower() == "true"

# Initialize Redis client for task queue
redis_client = None
redis_sync_client = None


if REDIS_ENABLED:
    try:
        redis_kwargs = {
            "decode_responses": True
        }

        # Only needed if SSL and you want to disable verification
        if REDIS_URL.startswith("rediss://") and not REDIS_SSL_VERIFY:
            redis_kwargs["ssl_cert_reqs"] = None

        redis_sync_client = redis.from_url(
            REDIS_URL,
            **redis_kwargs
        )

        redis_client = redis_async.from_url(
            REDIS_URL,
            **redis_kwargs
        )

        print(redis_sync_client.ping())

    except Exception as e:
        import logging
        logging.warning(f"Failed to connect to Redis: {e}")
        



SESSION_TIMEOUT_HOURS = int(os.getenv("SESSION_TIMEOUT_HOURS", "4"))
SESSION_TIMEOUT_SECONDS = SESSION_TIMEOUT_HOURS * 3600


        
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


AWS_REGION=os.getenv("AWS_DEFAULT_REGION","ap-south-1")
AWS_ACCESS_KEY_ID     = os.getenv("AWS_ACCESS_KEY", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRETE_KEY", "")
BEDROCK_MODEL_ID=os.getenv("BEDROCK_MODEL_ID","google.gemma-3-12b-it")

LLM_PROVIDER=os.getenv("LLM_PROVIDER","bedrock")

# ── Logging / CloudWatch ──────────────────────────────────────────────────────
ENVIRONMENT            = os.getenv("ENVIRONMENT", "dev")
SERVICE_NAME           = os.getenv("SERVICE_NAME", "invoice-parser")
LOG_CLOUDWATCH_ENABLED = os.getenv("LOG_CLOUDWATCH_ENABLED", "false").lower() == "true"
LOG_FILE_ENABLED       = os.getenv("LOG_FILE_ENABLED", "true").lower() == "true"
CW_LOG_GROUP           = os.getenv("CW_LOG_GROUP", "/invoice-parser/app")


CLEANUP_BATCH_HOURS = float(os.getenv("CLEANUP_BATCH_HOURS", "0.5"))
CLEANUP_AGE = float(os.getenv("CLEANUP_AGE", "0.5"))


LANGFUSE_SECRET_KEY=os.getenv("LANGFUSE_SECRET_KEY")
LANGFUSE_PUBLIC_KEY=os.getenv("LANGFUSE_PUBLIC_KEY")
LANGFUSE_BASE_URL=os.getenv("LANGFUSE_BASE_URL")


