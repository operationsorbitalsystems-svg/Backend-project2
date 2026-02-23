import boto3
import asyncio
import json
import logging
from typing import Tuple, Optional, Type, Dict, Any
from pydantic import BaseModel

from config import BEDROCK_MODEL_ID, AWS_REGION, bedrock_semaphore
from utils.logger import setup_logger

logger = setup_logger()

# Singleton Bedrock client
bedrock_client = boto3.client(
    service_name="bedrock-runtime",
    region_name=AWS_REGION
)


class LedgerNameOutputFormat(BaseModel):
    ledger_name: str


async def call_bedrock(
    system_prompt: str,
    user_prompt: str,
    model_id: str = BEDROCK_MODEL_ID,
    max_retries: int = 3,
    get_pydantic_schema: Optional[Type[BaseModel]] = None,
    pydantic_json_schema: Optional[Dict[str, Any]] = None
) -> Tuple[Optional[dict], bool]:

    global bedrock_semaphore
    response = None

    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                logger.info(f"🔄 Bedrock retry {attempt}/{max_retries}")

            # Construct final prompt
            full_prompt = f"{system_prompt}\n\nUser:\n{user_prompt}"

            if get_pydantic_schema or pydantic_json_schema:
                json_schema = (
                    pydantic_json_schema
                    if pydantic_json_schema
                    else get_pydantic_schema.model_json_schema()
                )

                full_prompt += f"\n\nReturn strictly valid JSON matching this schema:\n{json.dumps(json_schema)}"

            request_body = {
                "inputText": full_prompt,
                "textGenerationConfig": {
                    "temperature": 0.1,
                    "topP": 0.9,
                    "maxTokenCount": 512
                }
            }

            async with bedrock_semaphore:
                raw_response = await asyncio.to_thread(
                    bedrock_client.invoke_model,
                    modelId=model_id,
                    body=json.dumps(request_body),
                    contentType="application/json",
                    accept="application/json"
                )

            body = json.loads(raw_response["body"].read())

            # Titan-style response parsing
            if "results" in body and len(body["results"]) > 0:
                text_output = body["results"][0]["outputText"].strip()
                response = {"content": text_output}
                return response, True

            raise ValueError("Invalid Bedrock response format")

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
                return response, False

    return None, False


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
        # Simple test invoke with minimal prompt
        test_body = {
            "inputText": "health check",
            "textGenerationConfig": {
                "maxTokenCount": 10
            }
        }

        response = await asyncio.to_thread(
            bedrock_client.invoke_model,
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps(test_body),
            contentType="application/json",
            accept="application/json"
        )

        logger.info("✅ Bedrock health check passed.")
        return True, None

    except Exception as e:
        error_msg = f"Bedrock health check failed: {str(e)}"
        logger.error(f"❌ {error_msg}")
        return False, error_msg