"""
Generic Ollama LLM Queue

Handles generic prompt/response tasks using Ollama LLM.
NO invoice-specific logic - just a reusable LLM service.
"""

import asyncio
import logging
from typing import Optional
from datetime import datetime
from uuid import uuid4

from config import redis_client, MAX_OLLAMA_CONCURRENT_CALLS, ollama_semaphore
from models import OllamaRequest, OllamaResponse
from services.ollama_api_call import ollama_client
from config import OLLAMA_MODEL_NAME
from utils.logger import setup_logger

logger = setup_logger()


class GenericOllamaQueue:
    """
    Generic Ollama LLM task queue - NO invoice-specific logic.
    Handles prompt → LLM → response workflow.
    """

    # Redis key prefixes
    PENDING_QUEUE_PREFIX = "ollama_queue:pending:"
    RESPONSE_PREFIX = "ollama_queue:response:"
    ACTIVE_BATCHES_KEY = "ollama_queue:active_batches"
    ROUND_ROBIN_INDEX_KEY = "ollama_queue:round_robin_index"
    PROCESSING_PREFIX = "ollama_queue:processing:"

    def __init__(self, redis_client):
        if redis_client is None:
            raise ValueError("Redis client is required for Ollama queue")

        self.redis = redis_client
        self.ollama_client = ollama_client
        logger.info("GenericOllamaQueue initialized")

    async def enqueue_request(
        self,
        batch_id: str,
        system_prompt: str,
        user_prompt: str,
        metadata: Optional[dict] = None
    ) -> str:
        """
        Enqueue a generic Ollama LLM request.

        Args:
            batch_id: Batch identifier
            system_prompt: System instructions for LLM
            user_prompt: User query for LLM
            metadata: Optional metadata for tracking

        Returns:
            task_id of the enqueued request
        """
        # Create request model
        request = OllamaRequest(
            task_id=str(uuid4()),
            batch_id=batch_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            enqueued_at=datetime.utcnow().isoformat(),
            metadata=metadata or {}
        )

        # Serialize to JSON
        request_json = request.model_dump_json()

        # Add to batch-specific queue
        queue_key = f"{self.PENDING_QUEUE_PREFIX}{batch_id}"
        await self.redis.rpush(queue_key, request_json)

        # Add batch to active set
        await self.redis.sadd(self.ACTIVE_BATCHES_KEY, batch_id)

        logger.info(f"📝 Enqueued Ollama request {request.task_id} for batch {batch_id}")
        return request.task_id

    async def get_next_task_round_robin(self) -> Optional[OllamaRequest]:
        """
        Get next Ollama task using round-robin scheduling across batches.

        Returns:
            OllamaRequest if available, None if queue is empty
        """
        # Get all active batches
        active_batches = await self.redis.smembers(self.ACTIVE_BATCHES_KEY)

        if not active_batches or len(active_batches) == 0:
            return None  # No tasks available

        # Convert to sorted list for consistent ordering
        batches = sorted(list(active_batches))

        # Get current round-robin index
        index_str = await self.redis.get(self.ROUND_ROBIN_INDEX_KEY)
        index = int(index_str) if index_str else 0

        # Try each batch in round-robin order
        for offset in range(len(batches)):
            selected_index = (index + offset) % len(batches)
            selected_batch = batches[selected_index]

            # Pop task from selected batch's queue (FIFO)
            queue_key = f"{self.PENDING_QUEUE_PREFIX}{selected_batch}"
            request_json = await self.redis.lpop(queue_key)

            if request_json:
                # Update round-robin index for next iteration
                next_index = (selected_index + 1) % len(batches)
                await self.redis.set(self.ROUND_ROBIN_INDEX_KEY, next_index)

                # Parse request
                request = OllamaRequest.model_validate_json(request_json)

                # Check if batch queue is now empty, remove from active set
                queue_len = await self.redis.llen(queue_key)
                if queue_len == 0:
                    await self.redis.srem(self.ACTIVE_BATCHES_KEY, selected_batch)

                logger.info(f"⚡ Dequeued Ollama task {request.task_id} from batch {selected_batch}")
                return request

        # No tasks available in any batch
        return None

    async def call_ollama_and_respond(self, request: OllamaRequest) -> OllamaResponse:
        """
        Call Ollama API with prompts and return response.
        NO parsing, NO validation - just raw LLM response.

        Args:
            request: OllamaRequest with prompts

        Returns:
            OllamaResponse with LLM output
        """
        try:
            async with ollama_semaphore:
                # Call Ollama chat API
                response = await self.ollama_client.chat(
                    model=OLLAMA_MODEL_NAME,
                    messages=[
                        {
                            'role': 'system',
                            'content': request.system_prompt
                        },
                        {
                            'role': 'user',
                            'content': request.user_prompt
                        }
                    ],
                    options={
                        'temperature': 0.1,  # Low temperature for consistent output
                        'top_p': 0.9,
                    }
                )

            # Extract response text
            if response and 'message' in response and 'content' in response['message']:
                response_text = response['message']['content'].strip()

                return OllamaResponse(
                    task_id=request.task_id,
                    response_text=response_text,
                    success=True,
                    error=None
                )
            else:
                raise Exception("Invalid response format from Ollama")

        except Exception as e:
            logger.error(f"❌ Ollama API error for task {request.task_id}: {str(e)}")
            return OllamaResponse(
                task_id=request.task_id,
                response_text="",
                success=False,
                error=str(e)
            )

    async def worker_loop(self):
        """
        Worker that processes Ollama requests continuously.
        Uses round-robin scheduling across batches.
        """
        logger.info("🚀 Ollama worker started")

        while True:
            try:
                # Get next task (round-robin)
                request = await self.get_next_task_round_robin()

                if request is None:
                    # No tasks available, wait before checking again
                    await asyncio.sleep(1)
                    continue

                # Mark as processing (for recovery)
                processing_key = f"{self.PROCESSING_PREFIX}{request.task_id}"
                await self.redis.setex(processing_key, 3600, request.model_dump_json())

                # Call Ollama
                logger.info(f"🤖 Processing Ollama task {request.task_id}")
                response = await self.call_ollama_and_respond(request)

                # Save response to Redis (1-hour expiry)
                response_key = f"{self.RESPONSE_PREFIX}{request.task_id}"
                await self.redis.setex(response_key, 3600, response.model_dump_json())

                # Remove processing marker
                await self.redis.delete(processing_key)

                logger.info(f"✅ Ollama task {request.task_id} completed (success={response.success})")

            except Exception as e:
                logger.error(f"❌ Ollama worker error: {str(e)}")
                await asyncio.sleep(5)  # Wait before retrying

    async def wait_for_response(self, task_id: str, timeout: int = 60) -> OllamaResponse:
        """
        Wait for Ollama response (blocking with timeout).
        Used by invoice queue to wait for LLM results.

        Args:
            task_id: ID of the Ollama task
            timeout: Maximum wait time in seconds (default: 60)

        Returns:
            OllamaResponse when available

        Raises:
            TimeoutError if response not available within timeout
        """
        response_key = f"{self.RESPONSE_PREFIX}{task_id}"
        start_time = asyncio.get_event_loop().time()

        while True:
            # Check if response is available
            response_json = await self.redis.get(response_key)

            if response_json:
                # Parse and return response
                response = OllamaResponse.model_validate_json(response_json)
                logger.info(f"📬 Retrieved Ollama response for task {task_id}")
                return response

            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                raise TimeoutError(f"Ollama task {task_id} timed out after {timeout}s")

            # Wait before checking again
            await asyncio.sleep(0.5)


# Singleton instance
_ollama_queue_manager = None


def get_ollama_queue_manager():
    """Get singleton instance of GenericOllamaQueue"""
    global _ollama_queue_manager
    if _ollama_queue_manager is None:
        _ollama_queue_manager = GenericOllamaQueue(redis_client)
    return _ollama_queue_manager
