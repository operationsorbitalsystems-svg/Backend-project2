"""
agent.py
────────
Main entry point for the COA invoice classification agent.

Key functions:
  run_agent(invoice_description, vendor_name)  → selected ledger name (str)
  classify_batch(invoices)                      → dict of id → ledger name

NOTE ON GEMMA TOOL CALLING:
  Gemma 3 on Bedrock does not use Bedrock's native toolUse/toolResult protocol.
  It outputs tool calls as plain JSON inside ```json ... ``` blocks in its text.
  We use a ReAct-style text parsing loop instead of relying on stop_reason="tool_use".

  Each turn we:
    1. Call bedrock converse() (no toolConfig — Gemma ignores it anyway)
    2. Extract the full text response
    3. Parse any ```json { "tool": ..., "input": ... } ``` blocks
    4. Execute them, format results as text, inject back as next user message
    5. Repeat until select_leaf is called or MAX_TURNS exceeded
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError
from langfuse import get_client

from config import BEDROCK_MODEL_ID as MODEL_ID
from config import bedrock_semaphore
from .memory import initialize_memory, is_done
from .tools import execute_tool
from utils.logger import setup_logger
from utils.prompts import build_system_prompt_agent
from services.bedrock import bedrock_client

# BASE_DIR = Path(__file__).resolve().parent.parent
# PROMPT_PATH = BASE_DIR / "prompts" / ".txt"

logger = setup_logger()

# ── Safety cap ────────────────────────────────────────────────────────────────
MAX_TURNS = 30  # max LLM round-trips per invoice

# ── Regex to extract JSON tool calls from model text output ───────────────────
# Matches: ```json ... ```,  ``` ... ```, or ```tool_code ... ``` blocks
_TOOL_CALL_RE = re.compile(
    r"```(?:json|tool_code)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)




# ── Tool call parser ──────────────────────────────────────────────────────────

def _parse_tool_calls(text: str) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Extract all tool calls from model text output.

    Expected format:
        ```json
        {"tool": "get_children", "input": {"path": ["Expenses"]}}
        ```

    Returns list of (tool_name, tool_input) tuples.
    Returns [] if no valid tool calls found.
    """
    calls = []
    for match in _TOOL_CALL_RE.finditer(text):
        raw = match.group(1).strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue

        tool_name  = parsed.get("tool", "").strip()
        tool_input = parsed.get("input", {})

        if tool_name and isinstance(tool_input, dict):
            calls.append((tool_name, tool_input))

    return calls


def _format_tool_result(tool_name: str, result: Dict[str, Any]) -> str:
    """Format a tool result as clean text to inject back into the conversation."""
    return (
        f"Tool result for `{tool_name}`:\n"
        f"```json\n{json.dumps(result, indent=2)}\n```\n"
        f"Now decide your next action based on this result."
    )


# ── Agentic loop ──────────────────────────────────────────────────────────────

async def run_agent(
    invoice_description: str,
    expense_tree: Dict[str, Any],
    batch_id: str,
    task_id: str,
    vendor_name: Optional[str] = None,

) -> str:
    """
    Classify a single invoice. Returns the selected ledger name.
    Raises RuntimeError if classification fails or MAX_TURNS exceeded.
    """
    langfuse = get_client()
    memory   = initialize_memory(expense_tree, invoice_description, vendor_name)
    system_prompt = build_system_prompt_agent(invoice_description, vendor_name)

    with langfuse.start_as_current_observation(
        trace_context= {
            "trace_id" : task_id,
        },
        as_type="span",
        name=f"invoice-classification-{task_id}",
        input={"invoice_description": invoice_description, "vendor_name": vendor_name},
    ) as root_span:
        
        root_span.update_trace(
            session_id= batch_id
        )

        root_span.update_trace(
            input={"invoice": invoice_description, "vendor": vendor_name},
            metadata={"max_turns": MAX_TURNS, "model": MODEL_ID},
            tags=["coa-agent"],
        )

        # Conversation history — plain text turns, no toolConfig
        messages: List[Dict[str, Any]] = [
            {
                "role": "user",
                "content": [{"text": (
                    f"Classify this invoice and find the correct ledger account.\n\n"
                    f"Invoice: {invoice_description}"
                    + (f"\nVendor: {vendor_name}" if vendor_name else "")
                    + "\n\nStart by calling get_children on [\"Expenses\"] now."
                )}],
            }
        ]

        turn = 0

        while turn < MAX_TURNS:
            turn += 1

            # ── LLM call (no toolConfig — Gemma uses text-based tool calls) ──
            with langfuse.start_as_current_observation(
                as_type="generation",
                name=f"bedrock-turn-{turn}",
                model=MODEL_ID,
                input=messages,
            ) as gen_span:
                
                async with bedrock_semaphore:
                    try:
                        response = bedrock_client.converse(
                            modelId=MODEL_ID,
                            system=[{"text": system_prompt}],
                            messages=messages,
                            inferenceConfig={"maxTokens": 1024, "temperature": 0.0},
                        )
                    except ClientError as e:
                        raise RuntimeError(f"Bedrock API error on turn {turn}: {e}") from e

                output_message = response["output"]["message"]

                # Extract full text from response
                model_text = " ".join(
                    block.get("text", "")
                    for block in output_message.get("content", [])
                    if "text" in block
                ).strip()

                usage = response.get("usage", {})
                gen_span.update(
                    output=model_text,
                    usage_details={
                        "input":  usage.get("inputTokens", 0),
                        "output": usage.get("outputTokens", 0),
                    },
                )
                

            logger.info(f"\n[Turn {turn}] Model:\n{model_text[:500]}")

            # Add assistant turn to history
            messages.append({
                "role":    "assistant",
                "content": [{"text": model_text}],
            })

            # ── Parse tool calls from text ─────────────────────────────────────
            tool_calls = _parse_tool_calls(model_text)

            if not tool_calls:
                # No tool call found — check if we're done or truly stuck
                if is_done(memory):
                    root_span.update_trace(output={"selected_leaf": memory.selected_leaf})
                    return memory.selected_leaf  # type: ignore[return-value]

                # Prod the model to keep going
                messages.append({
                    "role": "user",
                    "content": [{"text": (
                        "No tool call detected in your response. "
                        "You must call a tool now. Output a single JSON block like:\n"
                        "```json\n"
                        "{\"tool\": \"get_children\", \"input\": {\"path\": [\"Expenses\"]}}\n"
                        "```"
                    )}],
                })
                continue

            # ── Execute tool calls (Gemma should only send one at a time) ──────
            tool_result_texts: List[str] = []

            for tool_name, tool_input in tool_calls:
                logger.info(f"  → Tool call: {tool_name}({json.dumps(tool_input)})")

                with langfuse.start_as_current_observation(
                    as_type="span",
                    name=f"tool:{tool_name}",
                    input={"tool": tool_name, "input": tool_input},
                ) as tool_span:
                    result = execute_tool(tool_name, tool_input, memory, expense_tree)
                    tool_span.update(output=result)

                logger.info(f"  ← Result: {json.dumps(result)[:200]}")
                tool_result_texts.append(_format_tool_result(tool_name, result))

                # Terminal — select_leaf was called successfully
                if tool_name == "select_leaf" and is_done(memory):
                    root_span.update_trace(
                        output={"selected_leaf": memory.selected_leaf},
                        metadata={
                            "turns": turn,
                            "full_path": memory.selected_path,
                            "log_entries": len(memory.log),
                        },
                    )
                    logger.info(f"\n✅ Selected: {memory.selected_leaf}")
                    return memory.selected_leaf  # type: ignore[return-value]

            # Inject all tool results back as next user message
            messages.append({
                "role": "user",
                "content": [{"text": "\n\n".join(tool_result_texts)}],
            })

        # MAX_TURNS exceeded
        root_span.update_trace(
            output={"error": f"MAX_TURNS ({MAX_TURNS}) exceeded without selection"},
            level="ERROR",
        )
        raise RuntimeError(
            f"Agent exceeded MAX_TURNS ({MAX_TURNS}) without calling select_leaf. "
            f"Exploration log: {len(memory.log)} entries."
        )


# ── Batch runner ──────────────────────────────────────────────────────────────

def classify_batch(
    invoices: List[Dict[str, Any]],
    max_workers: int = 10,
) -> Dict[str, Any]:
    """
    Classify a list of invoices in parallel.

    Each invoice dict should have:
      - id: str              — unique identifier
      - description: str     — invoice text
      - vendor: str (optional)

    Returns:
      {
        invoice_id: {
          "selected_leaf": str,
          "error": str | None
        }
      }
    """
    results: Dict[str, Any] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_id = {
            executor.submit(
                run_agent,
                inv["description"],
                inv.get("vendor"),
            ): inv["id"]
            for inv in invoices
        }

        for future in as_completed(future_to_id):
            inv_id = future_to_id[future]
            try:
                leaf = future.result()
                results[inv_id] = {"selected_leaf": leaf, "error": None}
            except Exception as e:
                results[inv_id] = {"selected_leaf": None, "error": str(e)}

    return results



