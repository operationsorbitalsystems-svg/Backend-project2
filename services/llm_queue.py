"""
Generic LLM Task Queue

Provider-agnostic queue for LLM prompt/response tasks.
Dispatches via llm_client.call_llm() — swap providers via LLM_PROVIDER env var.
"""

import asyncio
import re
import json
from typing import Optional
from datetime import datetime
from uuid import uuid4

from config import redis_client
from models import OllamaRequest, OllamaResponse
from utils.logger import setup_logger, batch_id_var

from typing import Optional, Dict, Any, Tuple
from config import LLM_PROVIDER


logger = setup_logger()




class LLMQueue:
    """
    Generic LLM task queue — NO provider-specific logic.
    Handles enqueue → LLM call → response workflow via Redis.
    """

    PENDING_QUEUE_PREFIX = "llm_queue:pending:"
    RESPONSE_PREFIX = "llm_queue:response:"
    ACTIVE_BATCHES_KEY = "llm_queue:active_batches"
    ROUND_ROBIN_INDEX_KEY = "llm_queue:round_robin_index"
    PROCESSING_PREFIX = "llm_queue:processing:"

    def __init__(self, redis_client):
        if redis_client is None:
            raise ValueError("Redis client is required for LLM queue")
        self.redis = redis_client
        logger.info("LLMQueue initialized")
        

    async def call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        pydantic_json_schema: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, bool]:
        """
        Dispatch an LLM call to the configured provider.

        Args:
            system_prompt: System/role instructions for the model
            user_prompt: The user query
            pydantic_json_schema: Optional JSON schema to constrain output

        Returns:
            (response_text, success) — normalized across all providers
        """
        if LLM_PROVIDER == "bedrock":
            from services.bedrock import call_bedrock
            return await call_bedrock(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                pydantic_json_schema=pydantic_json_schema
            )

        elif LLM_PROVIDER == "ollama":
            from services.ollama_api_call import call_ollama
            return await call_ollama(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                pydantic_json_schema=pydantic_json_schema
            )

        else:
            raise ValueError(f"Unknown LLM_PROVIDER: '{LLM_PROVIDER}'. Set LLM_PROVIDER to 'bedrock' or 'ollama'.")


    async def enqueue_request(
        self,
        batch_id: str,
        system_prompt: str,
        user_prompt: str,
        metadata: Optional[dict] = None
    ) -> str:
        request = OllamaRequest(
            task_id=str(uuid4()),
            batch_id=batch_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            enqueued_at=datetime.utcnow().isoformat(),
            metadata=metadata or {}
        )

        queue_key = f"{self.PENDING_QUEUE_PREFIX}{batch_id}"
        await self.redis.rpush(queue_key, request.model_dump_json())
        await self.redis.sadd(self.ACTIVE_BATCHES_KEY, batch_id)

        logger.info(f"📝 Enqueued LLM request {request.task_id} for batch {batch_id}")
        return request.task_id

    async def get_next_task_round_robin(self) -> Optional[OllamaRequest]:
        active_batches = await self.redis.smembers(self.ACTIVE_BATCHES_KEY)

        if not active_batches:
            return None

        batches = sorted(list(active_batches))

        index_str = await self.redis.get(self.ROUND_ROBIN_INDEX_KEY)
        index = int(index_str) if index_str else 0

        for offset in range(len(batches)):
            selected_index = (index + offset) % len(batches)
            selected_batch = batches[selected_index]

            queue_key = f"{self.PENDING_QUEUE_PREFIX}{selected_batch}"
            request_json = await self.redis.lpop(queue_key)

            if request_json:
                next_index = (selected_index + 1) % len(batches)
                await self.redis.set(self.ROUND_ROBIN_INDEX_KEY, next_index)

                request = OllamaRequest.model_validate_json(request_json)

                queue_len = await self.redis.llen(queue_key)
                if queue_len == 0:
                    await self.redis.srem(self.ACTIVE_BATCHES_KEY, selected_batch)

                logger.info(f"⚡ Dequeued LLM task {request.task_id} from batch {selected_batch}")
                return request

        return None

    async def call_llm_and_respond(self, request: OllamaRequest) -> OllamaResponse:
        """
        Call the LLM via llm_client dispatcher and return a normalized OllamaResponse.
        Provider-agnostic: bedrock, ollama, or any future provider all go through call_llm().
        """
        try:
            pydantic_json_schema = request.metadata.get('pydantic_json_schema') if request.metadata else None

            response_text, success = await self.call_llm(
                system_prompt=request.system_prompt,
                user_prompt=request.user_prompt,
                pydantic_json_schema=pydantic_json_schema
            )

            if not success:
                raise Exception("LLM call failed")

            # Extract JSON object from response (handles markdown fences, preamble text, etc.)
            first_brace = response_text.find('{')
            last_brace = response_text.rfind('}')
            if first_brace != -1 and last_brace > first_brace:
                response_text = response_text[first_brace:last_brace + 1]

            # Fix common JSON formatting issues (unquoted keys/values)
            try:
                json.loads(response_text)
            except json.JSONDecodeError:
                response_text = re.sub(
                    r'\{\s*(\w+):\s*([^}]+)\s*\}',
                    lambda m: f'{{ "{m.group(1)}": "{m.group(2).strip()}" }}',
                    response_text
                )

            return OllamaResponse(
                task_id=request.task_id,
                response_text=response_text,
                success=True,
                error=None
            )

        except Exception as e:
            logger.error(f"❌ LLM error for task {request.task_id}: {str(e)}")
            return OllamaResponse(
                task_id=request.task_id,
                response_text="",
                success=False,
                error=str(e)
            )

    async def worker_loop(self):
        logger.info("🚀 LLM queue worker started")

        while True:
            _ctx_token = None
            try:
                request = await self.get_next_task_round_robin()

                if request is None:
                    await asyncio.sleep(1)
                    continue

                _ctx_token = batch_id_var.set(request.batch_id)

                processing_key = f"{self.PROCESSING_PREFIX}{request.task_id}"
                await self.redis.setex(processing_key, 3600, request.model_dump_json())

                logger.info(f"🤖 Processing LLM task {request.task_id}")
                response = await self.call_llm_and_respond(request)

                response_key = f"{self.RESPONSE_PREFIX}{request.task_id}"
                await self.redis.setex(response_key, 3600, response.model_dump_json())

                await self.redis.delete(processing_key)

                logger.info(f"✅ LLM task {request.task_id} completed (success={response.success})")

            except Exception as e:
                logger.error(f"❌ LLM worker error: {str(e)}")
                await asyncio.sleep(5)

            finally:
                if _ctx_token is not None:
                    batch_id_var.reset(_ctx_token)

    async def wait_for_response(self, task_id: str, timeout: int = 60) -> OllamaResponse:
        response_key = f"{self.RESPONSE_PREFIX}{task_id}"
        start_time = asyncio.get_event_loop().time()

        while True:
            response_json = await self.redis.get(response_key)

            if response_json:
                response = OllamaResponse.model_validate_json(response_json)
                logger.info(f"📬 Retrieved LLM response for task {task_id}")
                return response

            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                raise TimeoutError(f"LLM task {task_id} timed out after {timeout}s")

            await asyncio.sleep(0.5)


# Singleton
_llm_queue = None


def get_llm_queue() -> LLMQueue:
    global _llm_queue
    if _llm_queue is None:
        _llm_queue = LLMQueue(redis_client)
    return _llm_queue
