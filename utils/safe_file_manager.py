from pathlib import Path
from typing import Union
from config import redis_sync_client


class RedisConfigStore:
    """
    Redis-backed config store for prompts and TDS rates.

    Redis is the source of truth at runtime. Files are seed sources only —
    read once at startup if the Redis key is absent, never written to after that.

    R/W safety: Redis is single-threaded internally, so GET and SET are
    inherently atomic. No explicit locks needed for simple string replace.
    """

    def __init__(self, redis_client, key: str, seed_file: Union[str, Path]):
        self.redis = redis_client
        self.key = key
        self.seed_file = Path(seed_file)

    def seed_from_file(self):
        """Seed Redis from file if the key is absent. No-op if key already exists."""
        if not self.redis:
            return
        if not self.redis.exists(self.key):
            content = self.seed_file.read_text(encoding="utf-8") if self.seed_file.exists() else ""
            self.redis.set(self.key, content)

    def read(self) -> str:
        if self.redis:
            return self.redis.get(self.key) or ""
        # Fallback: read file directly if Redis is unavailable
        return self.seed_file.read_text(encoding="utf-8") if self.seed_file.exists() else ""

    def write(self, content: str):
        if self.redis:
            self.redis.set(self.key, content)
        else:
            # Fallback: write to file if Redis is unavailable
            self.seed_file.parent.mkdir(parents=True, exist_ok=True)
            self.seed_file.write_text(content, encoding="utf-8")

    def exists(self) -> bool:
        if self.redis:
            return bool(self.redis.exists(self.key))
        return self.seed_file.exists()


# Global instances — same names as before so all callers are unchanged
dr_prompt_file  = RedisConfigStore(redis_sync_client, "config:prompt:dr",  "prompts/dr_prompt.txt")
cr_prompt_file  = RedisConfigStore(redis_sync_client, "config:prompt:cr",  "prompts/cr_prompt.txt")
tds_prompt_file = RedisConfigStore(redis_sync_client, "config:prompt:tds", "prompts/tds_nature_prompt.txt")
tds_file        = RedisConfigStore(redis_sync_client, "config:tds_rates",  "data/tds_rates.json")
dr_agent_prompt_file = RedisConfigStore(redis_sync_client, "config:dr_agent_prompt", "prompts/dr_agent_prompt.txt")

