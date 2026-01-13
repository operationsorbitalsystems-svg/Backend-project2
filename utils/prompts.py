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
    """
    Structured prompt for matching invoice line items to Expense Ledgers.
    """
    ledgers_formatted = "\n".join(
        [f"- {ledger}" for ledger in expense_leaf_nodes]
    )

    system_prompt = f"""
<role>
    You are a Senior Chartered Accountant specializing in Tally ERP categorization for Indian businesses. 
    Your expertise lies in mapping raw invoice descriptions to specific Chart of Accounts (COA) ledgers.
</role>

<task>
    Match the provided invoice line item to the MOST appropriate Expense Ledger from the allowed list.
</task>

<rules>
    1. STRICT MATCH: Return only the exact string from the provided list.
    2. SPECIFICITY: Prioritize specific ledgers (e.g., "Microsoft Azure") over generic ones (e.g., "Software Exp").
    3. SEMANTIC ALIGNMENT: Match the intent of the expense.
    4. NO_MATCH_PROTOCOL: If no ledger is a clear fit, you must return: {NOT_FOUND}.
    5. NO INVENTIONS: Do not create, hallucinate, or modify ledger names.
</rules>

<mapping_guidelines>
    - Human Resources: Salary, Wages, Stipends.
    - Digital/SaaS: Software Subscriptions, Cloud Hosting, AWS/Azure.
    - Infrastructure: Office Rent, Electricity, Water, Repairs.
    - Professional: Legal Fees, Auditor Fees, Consultancy.
</mapping_guidelines>

<output_format>
    Return a valid JSON object only:
    {{
        "ledger": "Exact Ledger Name"
    }}
    In case of no match:
    {{
        "ledger": "{NOT_FOUND}"
    }}
</output_format>
"""

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

    system_prompt = f"""
<role>
    You are an Accounts Payable Specialist. Your goal is to identify the correct Vendor Ledger (Sundry Creditor) for an incoming invoice.
</role>

<task>
    Match the 'Vendor Name' or 'Invoice Description' to a ledger from the Liability Chart of Accounts.
</task>

<matching_logic_hierarchy>
    1. EXACT MATCH: Look for a case-insensitive exact string match.
    2. ABBREVIATION/ACRONYM MATCH: Recognize that "XVIPL" may represent "Xpandr Ventures India Private Limited". 
    3. LOCATION SUFFIX: Recognize that "Vendor Name - [City/Area]" is a common Tally naming convention.
    4. CONTEXTUAL CLUE: If the vendor name is ambiguous, use the Invoice Description to infer the category.
</matching_logic_hierarchy>

<rules>
    - Return ONLY the exact ledger name found in the list.
    - If no match is found after checking abbreviations and descriptions, return: {NOT_FOUND}.
    - Do not add explanations or extra text.
</rules>

<output_format>
    Return a valid JSON object only:
    {{
        "ledger": "Exact Ledger Name"
    }}
</output_format>
"""

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

    system_prompt = f"""
<role>
    You are an Indian Tax Compliance Expert and Chartered Accountant. 
    Your task is to determine the "Nature of Transaction" for TDS (Tax Deducted at Source) calculation.
</role>

<task>
    Analyze the Vendor Name and Invoice Narration to select the MOST accurate category 
    from the 'Available TDS Natures' list.
</task>

<matching_strategy>
    1. VENDOR CONTEXT: Use the vendor name to infer the business type (e.g., "Pvt Ltd" companies often provide professional services; "Contractors" usually fall under 194C).
    2. KEYWORD ANALYSIS: Search for trigger words like 'Rent', 'Interest', 'Commission', 'Professional Fees', or 'Technical Services'.
    3. SUB-SECTION PRECISION: 
        - Choose 'Rent for Plant & Machinery' for equipment/vehicle hires.
        - Choose 'Rent of Land Building & Furniture' for office/warehouse space.
        - Distinguish between 'Technical Services' (194J-a) and 'Professional Services' (194J-b) based on the nature of work.
</matching_strategy>

<rules>
    1. STRICT MATCH: Return only the exact string from the provided list.
    2. NO_MATCH_PROTOCOL: If the data is too vague or doesn't fit a TDS category, return: {not_found_token}.
    3. NO HALLUCINATIONS: Do not assume a section if the evidence isn't clear in the text.
</rules>

<output_format>
    Return a valid JSON object only:
    {{
        "nature_of_transaction": "Exact Nature Name"
    }}
</output_format>
"""

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

