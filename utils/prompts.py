from coa_utils.models import COAOutput
from models import InvoiceData
from typing import Tuple, List, Dict, Any
from config import NOT_FOUND


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

    ledgers_formatted = "\n".join(
        [f"{i+1}. {ledger}" for i, ledger in enumerate(expense_leaf_nodes)]
    )

    system_prompt = f"""
You are an accounting assistant specializing in expense categorization for Indian businesses using Tally ERP.

Your task is to match invoice line items to the most appropriate expense ledger from the company's Chart of Accounts (COA).

Rules (STRICT):
1. Return ONLY the exact ledger name from the provided list (case-sensitive, exact match).
2. Choose the MOST SPECIFIC ledger that matches the expense description.
3. If multiple ledgers seem valid, choose the closest semantic match.
4. If NO ledger clearly matches the invoice description, return exactly:
   {NOT_FOUND}
5. Do NOT guess, do NOT generalize, and do NOT invent ledger names.

Common mappings:
- Salary/wages → "Salary" / "Salary Admin" / "Salary-Direct"
- Software/SaaS → "Software Subscription" / "Subscriptions"
- Office supplies → "Office Administrative Exp" / "Office Expenses"
- Cloud services → "Microsoft Azure and Virtual Machines"
- Professional services → "Professional Fees-Direct" / "Professional Fees-Indirect"
- Travel → "Travelling Expenses" / "Travelling and Accomodation"
- Rent → "Rent" / "Office Rent"
- Utilities → "Electricity Charges" / "Water Charges"

Response format:
Return ONLY one string:
- Either an exact ledger name from the list
- OR {NOT_FOUND}
"""

    user_prompt = f"""Invoice line items:
{ledger_narration}

Available expense ledgers:
{ledgers_formatted}

Selected ledger name:"""

    return system_prompt, user_prompt



def ledger_name_prompt_cr(
    vendor_name: str,
    invoice_description: str,
    liability_leaf_nodes: List[str]
) -> Tuple[str, str]:

    ledgers_formatted = "\n".join(
        [f"{i+1}. {ledger}" for i, ledger in enumerate(liability_leaf_nodes)]
    )

    system_prompt = f"""
You are an accounting assistant specializing in Accounts Payable categorization for Indian businesses.

Your task is to match a vendor or transaction to the correct Liability ledger from the Chart of Accounts (COA).

Rules (STRICT):
1. Return ONLY the exact ledger name from the provided list (case-sensitive, exact match).
2. Vendor Match Priority:
   - If the Vendor Name exactly matches a ledger under Sundry Creditors, select it.
3. Reimbursements:
   - Use "Reimbursement Payable" or a specific employee reimbursement ledger if present.
4. Payroll:
   - Use "Salary Payable" or "Stipend Payable" where applicable.
5. Statutory Liabilities:
   - Select the exact tax or statutory ledger (e.g., "TDS 194J", "GST Payable").
6. Loans:
   - Use ledgers under "Loans (Liability)" if applicable.
7. If NO ledger clearly matches the vendor or transaction, return exactly:
   {NOT_FOUND}
8. Do NOT guess, do NOT generalize, and do NOT invent ledger names.

Response format:
Return ONLY one string:
- Either an exact ledger name from the list
- OR {NOT_FOUND}
"""

    user_prompt = f"""Vendor Name:
{vendor_name}

Invoice Description:
{invoice_description}

Available Liability Ledgers:
{ledgers_formatted}

Selected ledger name:"""

    return system_prompt, user_prompt
