from coa_utils.models import COAOutput
from models import InvoiceData
from typing import Tuple, List, Dict, Any


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


def ledger_name_prompt(
    ledger_narration: str,
    expense_leaf_nodes: List[str]
) -> Tuple[str, str]:
    """
    Generate system and user prompts for Ollama ledger selection.

    Args:
        ledger_narration: Concatenated invoice line item descriptions (e.g., "Item A; Item B")
        expense_leaf_nodes: List of available expense ledger names from COA

    Returns:
        Tuple of (system_prompt, user_prompt)
    """
    # Format expense ledgers as numbered list
    ledgers_formatted = "\n".join([f"{i+1}. {ledger}" for i, ledger in enumerate(expense_leaf_nodes)])

    system_prompt = """You are an accounting assistant specializing in expense categorization for Indian businesses using Tally ERP.

Your task is to match invoice line items to the most appropriate expense ledger from the company's Chart of Accounts (COA).

Rules:
1. Return ONLY the exact ledger name from the provided list (case-sensitive, exact match)
2. Choose the MOST SPECIFIC ledger that matches the expense description
3. If multiple ledgers seem valid, choose the one with the closest semantic match
4. Common mappings for Indian businesses:
   - Salary/wages → "Salary" or "Salary Admin" or "Salary-Direct"
   - Software/SaaS → "Software Subscription" or "Subscriptions"
   - Office supplies → "Office Administrative Exp" or "Office Expenses"
   - Cloud services → "Microsoft Azure and Virtual Machines" or similar
   - Professional services → "Professional Fees-Direct" or "Professional Fees-Indirect"
   - Travel → "Travelling Expenses" or "Travelling and Accomodation"
   - Rent → "Rent" or "Office Rent"
   - Utilities → "Electricity Charges" or "Water Charges"
5. If NO ledger matches at all, return "Other Expenses" if available, else return the first ledger

Response format: Return ONLY the ledger name, nothing else."""

    user_prompt = f"""Invoice line items: {ledger_narration}

Available expense ledgers:
{ledgers_formatted}

Selected ledger name:"""

    return system_prompt, user_prompt
