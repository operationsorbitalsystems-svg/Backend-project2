import ollama
from ollama import ChatResponse
import asyncio
import logging
from typing import Tuple, Optional, Type
from pydantic import BaseModel, Field
from config import OLLAMA_MODEL_NAME, OLLAMA_BASE_URL, ollama_semaphore

from utils.logger import setup_logger

logger = setup_logger()

# Initialize Ollama client with configurable base URL (singleton pattern)
ollama_client = ollama.AsyncClient(host=OLLAMA_BASE_URL)


class LedgerNameOutputFormat(BaseModel):
    ledger_name: str = Field(description="The exact ledger name from the expense list")


async def call_ollama(
    system_prompt: str,
    user_prompt: str,
    model_name: str = OLLAMA_MODEL_NAME,
    max_retries: int = 3,
    get_pydantic_schema : Optional[Type[BaseModel]] = None
)-> Tuple[ChatResponse, bool]:
    """
    Call Ollama API to select ledger name with retry logic and confidence extraction.

    Args:
        system_prompt: System instructions
        user_prompt: User query with invoice context
        model_name: Ollama model to use (default from config)
        max_retries: Maximum retry attempts (default: 3)

    Returns:
        Tuple of (success, ledger_name, confidence_score, error_message)
    """
    global ollama_semaphore

    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                logger.info(f"🔄 Ollama retry {attempt}/{max_retries}")

            if get_pydantic_schema:

                async with ollama_semaphore:
                    # Call Ollama chat API
                    response = await ollama_client.chat(
                        model=model_name,
                        messages=[
                            {
                                'role': 'system',
                                'content': system_prompt
                            },
                            {
                                'role': 'user',
                                'content': user_prompt
                            }
                        ],
                        format= get_pydantic_schema.model_json_schema(),
                        options={
                            'temperature': 0.1,  # Low temperature for consistent categorization
                            'top_p': 0.9,
                        }
                    )
                    
            else:
                async with ollama_semaphore:
                    # Call Ollama chat API
                    response = await ollama_client.chat(
                        model=model_name,
                        messages=[
                            {
                                'role': 'system',
                                'content': system_prompt
                            },
                            {
                                'role': 'user',
                                'content': user_prompt
                            }
                        ],
                        options={
                            'temperature': 0.1,  # Low temperature for consistent categorization
                            'top_p': 0.9,
                        }
                    )

            # Extract ledger name from response
            if response and 'message' in response and 'content' in response['message']:
                # # ledger_name = response['message']['content'].strip()
                # output = response['message']['content']
                # if get_pydantic_schema:
                    
                #     ledger_dictionary = get_pydantic_schema.model_validate_json(output)
                    
                #     ledger_name = ledger_dictionary.ledger
                    
                # else:
                #     # Clean up response (remove quotes, newlines, extra spaces)
                #     ledger_name = output.replace('"', '').replace("'", "").strip()

                # # Extract confidence score from token probabilities (if available)
                # # Note: Ollama doesn't easily expose token probabilities by default
                # # We use a placeholder approach: assume high confidence if response is clean
                # confidence_score = _estimate_confidence(ledger_name, response)

                # # logger.info(f"✅ Ollama selected ledger: {ledger_name} (confidence: {confidence_score:.2f})")
                # return True, ledger_name, confidence_score, None
                return response, True
            else:
                raise ValueError("Invalid response format from Ollama")

        except Exception as e:
            # Check if retryable error
            if attempt < max_retries and is_retryable_error(e):
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                logger.warning(
                    f"Retryable Ollama error, waiting {wait_time}s before "
                    f"retry {attempt + 1}/{max_retries}: {str(e)}"
                )
                await asyncio.sleep(wait_time)
            else:
                # Final failure or non-retryable error
                error_msg = str(e)
                if attempt == max_retries:
                    logger.error(f"❌ Ollama failed after {max_retries} retries: {error_msg}")
                else:
                    logger.error(f"❌ Non-retryable Ollama error: {error_msg}")
                return response, False


##FIX THIS LATER
    return ChatResponse, False


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
        
        print(model_names)
        
        print(OLLAMA_MODEL_NAME)
        
        if OLLAMA_MODEL_NAME not in model_names:
            return False, f"Model '{OLLAMA_MODEL_NAME}' not found. Available: {model_names}"

        logger.info(f"✅ Ollama health check passed. Model '{OLLAMA_MODEL_NAME}' is available.")
        return True, None

    except Exception as e:
        error_msg = f"Ollama health check failed: {str(e)}"
        logger.error(f"❌ {error_msg}")
        return False, error_msg
