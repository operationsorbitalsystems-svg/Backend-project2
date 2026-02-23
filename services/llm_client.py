"""
LLM Client Dispatcher

Single entry point for all LLM calls. Dispatches to the correct provider
based on the LLM_PROVIDER env var. All providers return (str, bool).

To add a new provider:
  1. Create services/<provider>.py with call_<provider>(...) -> (str, bool)
  2. Add an elif branch below
  3. Set LLM_PROVIDER=<provider> in .env
"""

from typing import Optional, Dict, Any, Tuple
from config import LLM_PROVIDER


async def call_llm(
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
