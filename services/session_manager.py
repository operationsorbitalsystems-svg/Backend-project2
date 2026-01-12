from redis_session_manager import RedisSessionManager, REDIS_ENABLED
from in_mem_session_manager import InMemorySessionManager

# Factory function to get the appropriate session manager
def get_session_manager():
    """Return appropriate session manager based on configuration"""
    if REDIS_ENABLED:
        return RedisSessionManager()
    else:
        return InMemorySessionManager()