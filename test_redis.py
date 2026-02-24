import os
from dotenv import load_dotenv
import redis
import redis.asyncio as redis_async

load_dotenv()

REDIS_ENABLED = os.getenv("REDIS_ENABLED", "true").lower() == "true"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_SSL_VERIFY = os.getenv("REDIS_SSL_VERIFY", "true").lower() == "true"

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