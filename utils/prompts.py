from coa_utils.models import COAOutput
from models import InvoiceData
from typing import Tuple, List, Dict, Any
from config import NOT_FOUND
from .safe_file_manager import dr_prompt_file, cr_prompt_file, tds_prompt_file


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

