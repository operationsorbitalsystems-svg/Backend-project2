import boto3
import asyncio
import json
from typing import Tuple, Optional, Type, Dict, Any
from pydantic import BaseModel

from config import BEDROCK_MODEL_ID, AWS_REGION, bedrock_semaphore, AWS_SECRET_ACCESS_KEY, AWS_ACCESS_KEY_ID
from utils.logger import setup_logger

logger = setup_logger()

# Singleton Bedrock client
bedrock_client = boto3.client(
    service_name="bedrock-runtime",
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY
)


async def call_bedrock(
    system_prompt: str,
    user_prompt: str,
    model_id: str = BEDROCK_MODEL_ID,
    max_retries: int = 3,
    get_pydantic_schema: Optional[Type[BaseModel]] = None,
    pydantic_json_schema: Optional[Dict[str, Any]] = None
) -> Tuple[str, bool]:
    """
    Call AWS Bedrock via the Converse API (works for all models: Gemma, Claude, Llama, etc.)

    Returns:
        (response_text, success) — normalized (str, bool) for all providers
    """
    global bedrock_semaphore

    # Append JSON schema instruction to user prompt if provided
    final_user_prompt = user_prompt
    if get_pydantic_schema or pydantic_json_schema:
        json_schema = (
            pydantic_json_schema
            if pydantic_json_schema
            else get_pydantic_schema.model_json_schema()
        )
        final_user_prompt += f"\n\nReturn strictly valid JSON matching this schema:\n{json.dumps(json_schema)}"

    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                logger.info(f"🔄 Bedrock retry {attempt}/{max_retries}")

            async with bedrock_semaphore:
                raw_response = await asyncio.to_thread(
                    bedrock_client.converse,
                    modelId=model_id,
                    system=[{"text": system_prompt}],
                    messages=[
                        {"role": "user", "content": [{"text": final_user_prompt}]}
                    ],
                    inferenceConfig={
                        "maxTokens": 512,
                        "temperature": 0.1,
                        "topP": 0.9
                    }
                )
                
            input_tokens = raw_response["usage"]["inputTokens"]
            output_tokens = raw_response["usage"]["outputTokens"]
            total_tokens = raw_response["usage"]["totalTokens"]
            
            print(f"{input_tokens}, {output_tokens}, {total_tokens}")

            text = raw_response["output"]["message"]["content"][0]["text"].strip()
            return text, True

        except Exception as e:
            if attempt < max_retries and is_retryable_error(e):
                wait_time = 2 ** attempt
                logger.warning(
                    f"Retryable Bedrock error, waiting {wait_time}s "
                    f"before retry {attempt + 1}/{max_retries}: {str(e)}"
                )
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"❌ Bedrock failed: {str(e)}")
                return "", False

    return "", False


def is_retryable_error(error: Exception) -> bool:
    error_str = str(error).lower()
    retryable_indicators = [
        "throttling",
        "timeout",
        "service unavailable",
        "500",
        "503",
        "502",
        "504"
    ]
    return any(indicator in error_str for indicator in retryable_indicators)


async def health_check_bedrock() -> Tuple[bool, Optional[str]]:
    try:
        await asyncio.to_thread(
            bedrock_client.converse,
            modelId=BEDROCK_MODEL_ID,
            messages=[
                {"role": "user", "content": [{"text": "health check"}]}
            ],
            inferenceConfig={"maxTokens": 10}
        )
        logger.info("✅ Bedrock health check passed.")
        return True, None

    except Exception as e:
        error_msg = f"Bedrock health check failed: {str(e)}"
        logger.error(f"❌ {error_msg}")
        return False, error_msg
