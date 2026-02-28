from typing import Tuple, List, Dict, Any, Optional
from config import NOT_FOUND
from .safe_file_manager import dr_prompt_file, cr_prompt_file, tds_prompt_file, dr_agent_prompt_file


def extract_expense_leaf_nodes(coa_hierarchy: Dict[str, Any]) -> List[str]:
    """
    Recursively extract all leaf nodes from Expenses section.

    Leaf node definition:
    - If value is [] (empty list) → Leaf node
    - If value is a non-empty list → Traverse list recursively
    - If value is a dict → Traverse dict recursively

    Args:
        coa_hierarchy: The "Expenses" sub-tree from COA hierarchy

    Returns:
        List of leaf node names (ledger names)
    """
    leaf_nodes = []

    def traverse(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if value == []:
                    # Leaf node found
                    leaf_nodes.append(key)
                elif isinstance(value, list):
                    # Non-empty list - traverse each element
                    for item in value:
                        traverse(item)
                elif isinstance(value, dict):
                    # Nested dict - traverse
                    traverse(value)
        elif isinstance(node, list):
            for item in node:
                traverse(item)

    traverse(coa_hierarchy)
    return leaf_nodes


def ledger_name_prompt_dr(
    ledger_narration: str,
    expense_leaf_nodes: List[str]
) -> Tuple[str, str]:
    """
    Structured prompt for matching invoice line items to Expense Ledgers.
    """
    ledgers_formatted = "\n".join(
        [f"- {ledger}" for ledger in expense_leaf_nodes]
    )

    template = dr_prompt_file.read()
    system_prompt = template.format(
        NOT_FOUND=NOT_FOUND
    )

    user_prompt = f"""
<input_data>
    <invoice_item_description>
        {ledger_narration}
    </invoice_item_description>

    <available_ledgers>
        {ledgers_formatted}
    </available_ledgers>
</input_data>

Please select the ledger name:"""

    return system_prompt, user_prompt


def ledger_name_prompt_cr(
    vendor_name: str,
    invoice_description: str,
    liability_leaf_nodes: List[str]
) -> Tuple[str, str]:
    """
    Structured prompt for matching vendors to Liability/Sundry Creditor Ledgers.
    """
    ledgers_formatted = "\n".join(
        [f"- {ledger}" for ledger in liability_leaf_nodes]
    )

    template = cr_prompt_file.read()
    system_prompt = template.format(
        NOT_FOUND=NOT_FOUND
    )

    user_prompt = f"""
<input_data>
    <vendor_name_from_invoice>{vendor_name}</vendor_name_from_invoice>
    <invoice_description>{invoice_description}</invoice_description>
    
    <available_liability_ledgers>
        {ledgers_formatted}
    </available_liability_ledgers>
</input_data>

Identify the correct ledger:"""

    return system_prompt, user_prompt

from typing import List, Tuple


def tds_nature_prompt(
    vendor_name: str,
    ledger_narration: str,
    tds_nature_options: List[str],
    not_found_token: str = NOT_FOUND
) -> Tuple[str, str]:
    """
    Structured prompt for matching a vendor and invoice narration to the correct 
    TDS Nature of Transaction for Indian Tax compliance.
    """
    natures_formatted = "\n".join(
        [f"- {nature}" for nature in tds_nature_options]
    )

    template = tds_prompt_file.read()
    system_prompt = template.format(
        not_found_token=not_found_token
    )

    user_prompt = f"""
<input_data>
    <vendor_name>{vendor_name}</vendor_name>
    <invoice_narration>{ledger_narration}</invoice_narration>

    <available_tds_natures>
        {natures_formatted}
    </available_tds_natures>
</input_data>

Select the appropriate Nature of Transaction:"""

    return system_prompt, user_prompt




def build_system_prompt_agent(invoice_description: str, vendor_name: Optional[str]) -> str:
    
    SYSTEM_PROMPT_TEMPLATE = dr_agent_prompt_file.read()
    
    # ── System prompt ─────────────────────────────────────────────────────────────
    tool_info = """\

    ━━━ HOW THE TREE WORKS ━━━
    The tree is a hierarchy rooted at ["Expenses"]. Every node is either:
    FOLDER — has children inside it (type: "folder")
    LEAF   — a final ledger account, no children (type: "leaf")
    You must select a LEAF as your final answer.

    ━━━ HOW TO CALL TOOLS ━━━
    Output EXACTLY ONE tool call per response, as a JSON block like this:

    ```json
    {{"tool": "get_children", "input": {{"path": ["Expenses"]}}}}
    ```

    Wait for the tool result before calling another tool.
    Do NOT call multiple tools in one response.
    Do NOT add any text after the JSON block when making a tool call.

    ━━━ AVAILABLE TOOLS ━━━

    get_children — See the immediate children of any node you are at.
    Input:  {{"path": ["Expenses", "some folder"]}}
    Output: list of children with name, type (leaf/folder), state, leaf_count

    navigate_to — Move into a node OR backtrack to a parent/sibling you have seen before.
    Input:  {{"path": ["Expenses", "some folder"]}}
    Cannot navigate to EXHAUSTED or DISCARDED nodes.

    update_node_states — Mark nodes as DISCARDED (skip by name) or EXHAUSTED (explored, empty).
    Input:  {{"updates": [{{"path": ["Expenses", "X"], "state": "DISCARDED"}}, ...]}}
    Discard irrelevant branches immediately to save turns.

    get_leaf_nodes — Get ALL leaf names under a path in one call.
    Input:  {{"path": ["Expenses", "some folder"]}}
    Use this once you are confident you are in the right subtree.

    get_unexplored_paths — See everything still left to try. Use when unsure what's next.
    Input:  {{}}

    select_leaf — YOUR FINAL ANSWER. Only call when certain.
    Input:  {{"path": ["Expenses", "...", "...", "direct parent folder"], "leaf_name": "Exact Leaf Name"}}
    
    CRITICAL: `path` must be the COMPLETE path from "Expenses" down to the 
    IMMEDIATE parent folder of the leaf. Every intermediate folder must be 
    included. The leaf's direct parent is the last element in the path.
    
    Example — to select "Freight Outward ? General" which lives under:
    Expenses → Indirect Expenses → Other Indirect Expenses → 
        Selling and Distribution Expenses → Distribution Expenses
    
    Correct call:
    {{
        "path": ["Expenses", "Indirect Expenses", "Other Indirect Expenses", 
                "Selling and Distribution Expenses", "Distribution Expenses"],
        "leaf_name": "Freight Outward ? General"
    }}

    ━━━ NODE STATES ━━━
    UNEXPLORED  → Seen but not entered. Should explore.
    IN_PROGRESS → Currently being explored.
    EXHAUSTED   → Entered, nothing suitable found. Do NOT re-enter.
    DISCARDED   → Skipped by name as irrelevant. Do NOT enter.

    ━━━ STRATEGY ━━━
    1. Call get_children on ["Expenses"] to see the top-level options.
    2. Immediately DISCARD obviously irrelevant branches (e.g. Depreciation, Tax Expenses for a travel invoice).
    3. Navigate into the most relevant branch.
    4. Once you believe you are in the right area, call get_leaf_nodes.
    5. If there are ≤15 leaves, pick the best one and call select_leaf.
    6. If you went the wrong way, mark it EXHAUSTED, navigate_to a sibling or parent, and try again.
    7. If lost, call get_unexplored_paths to see what is left.

    BEGIN: Call get_children with path ["Expenses"] now.
    """



    SYSTEM_PROMPT_TEMPLATE += "\n" + tool_info

    return SYSTEM_PROMPT_TEMPLATE.format(
        invoice_description=invoice_description,
        vendor_name=vendor_name or "Unknown",
    )


