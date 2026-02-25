import ollama
import asyncio
from typing import Tuple, Optional, Type, Dict, Any
from pydantic import BaseModel, Field
from config import OLLAMA_MODEL_NAME, OLLAMA_BASE_URL, ollama_semaphore, langfuse_client
from langfuse import observe
from utils.logger import setup_logger

logger = setup_logger()

# Initialize Ollama client with configurable base URL (singleton pattern)
ollama_client = ollama.AsyncClient(host=OLLAMA_BASE_URL)


class LedgerNameOutputFormat(BaseModel):
    ledger_name: str = Field(description="The exact ledger name from the expense list")


@observe(as_type="generation")
async def call_ollama(
    system_prompt: str,
    user_prompt: str,
    model_name: str = OLLAMA_MODEL_NAME,
    max_retries: int = 3,
    get_pydantic_schema: Optional[Type[BaseModel]] = None,
    pydantic_json_schema: Optional[Dict[str, Any]] = None
) -> Tuple[str, bool]:
    """
    Call Ollama API with retry logic.

    Returns:
        (response_text, success) — normalized (str, bool) for all providers
    """
    global ollama_semaphore

    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                logger.info(f"🔄 Ollama retry {attempt}/{max_retries}")

            json_schema = None
            if get_pydantic_schema or pydantic_json_schema:
                json_schema = pydantic_json_schema if pydantic_json_schema else get_pydantic_schema.model_json_schema()

            chat_kwargs = dict(
                model=model_name,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt}
                ],
                options={'temperature': 0.1, 'top_p': 0.9}
            )
            if json_schema:
                chat_kwargs['format'] = json_schema

            async with ollama_semaphore:
                response = await ollama_client.chat(**chat_kwargs)

            if response and 'message' in response and 'content' in response['message']:
                if langfuse_client:
                    langfuse_client.update_current_generation(model=model_name)
                return response['message']['content'].strip(), True
            else:
                raise ValueError("Invalid response format from Ollama")

        except Exception as e:
            if attempt < max_retries and is_retryable_error(e):
                wait_time = 2 ** attempt
                logger.warning(
                    f"Retryable Ollama error, waiting {wait_time}s before "
                    f"retry {attempt + 1}/{max_retries}: {str(e)}"
                )
                await asyncio.sleep(wait_time)
            else:
                error_msg = str(e)
                if attempt == max_retries:
                    logger.error(f"❌ Ollama failed after {max_retries} retries: {error_msg}")
                else:
                    logger.error(f"❌ Non-retryable Ollama error: {error_msg}")
                return "", False

    return "", False


def _estimate_confidence(ledger_name: str, response: dict) -> float:
    """
    Estimate confidence score based on response quality.

    Note: Ollama doesn't easily expose token probabilities without special configuration.
    This is a heuristic-based approach until we implement proper probability extraction.

    Args:
        ledger_name: Extracted ledger name
        response: Full Ollama response

    Returns:
        Estimated confidence score (0.0 to 1.0)
    """
    # Heuristic: Clean, single-line responses = high confidence
    if not ledger_name or len(ledger_name) == 0:
        return 0.0

    # If response contains extra text beyond the ledger name, lower confidence
    response_text = response.get('message', {}).get('content', '')
    if '\n' in response_text or len(response_text) > len(ledger_name) + 10:
        return 0.7  # Medium confidence

    # Clean single-line response = high confidence
    return 0.95


def is_retryable_error(error: Exception) -> bool:
    """Check if error is retryable (connection, timeout, server errors)"""
    error_str = str(error).lower()
    retryable_indicators = [
        "connection",
        "timeout",
        "server error",
        "503",
        "502",
        "504",
        "unavailable"
    ]
    return any(indicator in error_str for indicator in retryable_indicators)


async def health_check_ollama() -> Tuple[bool, Optional[str]]:
    try:
        models_response = await ollama_client.list()
        
        # Access the 'models' attribute from the response 
        # and use dot notation to get the '.model' attribute from each entry
        model_names = [m.model for m in models_response.models]
        
        
        if OLLAMA_MODEL_NAME not in model_names:
            return False, f"Model '{OLLAMA_MODEL_NAME}' not found. Available: {model_names}"

        logger.info(f"✅ Ollama health check passed. Model '{OLLAMA_MODEL_NAME}' is available.")
        return True, None

    except Exception as e:
        error_msg = f"Ollama health check failed: {str(e)}"
        logger.error(f"❌ {error_msg}")
        return False, error_msg
