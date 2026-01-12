"""
XL Output Generator Service

Generates Excel-compatible output rows for journal entry import from invoice data.
"""

import re
from typing import Optional, List
from models import InvoiceData, InvoiceLineItem, XLOutputRow


class XLOutputGenerator:
    """Service for generating XL output rows from invoice data"""

    @staticmethod
    def extract_pincode(address: Optional[str]) -> Optional[str]:
        """
        Extract 6-digit Indian pincode from address using regex

        Args:
            address: Vendor address string

        Returns:
            6-digit pincode string or None if not found

        Examples:
            "123 Street, Mumbai, 400001" → "400001"
            "123 Street, 400 001, India" → "400001"
            "123 Street, Mumbai, India" → None
        """
        if not address:
            return None

        # Match 6 consecutive digits (with optional spaces: "400 001" → "400001")
        # \b ensures word boundaries to avoid matching longer numbers
        pattern = r'\b(\d{6}|\d{3}\s?\d{3})\b'
        match = re.search(pattern, address)

        if match:
            # Remove spaces and return normalized pincode
            return match.group(0).replace(' ', '').replace('-', '')

        return None

    @staticmethod
    def detect_has_gst(invoice_data: InvoiceData) -> bool:
        """
        Check if invoice has any GST component

        Args:
            invoice_data: Parsed invoice data

        Returns:
            True if any GST amount (CGST/SGST/IGST) is greater than 0
        """
        cgst = invoice_data.cgst_tax_amount or 0
        sgst = invoice_data.sgst_tax_amount or 0
        igst = invoice_data.igst_tax_amount or 0

        return (cgst > 0) or (sgst > 0) or (igst > 0)

    @staticmethod
    def calculate_ledger_amount(invoice_data: InvoiceData) -> float:
        """
        Calculate ledger amount based on GST presence

        Logic:
        - If invoice has GST (CGST/SGST/IGST > 0) → return subtotal
        - If no GST → return total_amount
        - If GST exists but subtotal is None → fall back to total_amount

        Args:
            invoice_data: Parsed invoice data

        Returns:
            Ledger amount (float)
        """
        has_gst = XLOutputGenerator.detect_has_gst(invoice_data)

        if has_gst and invoice_data.subtotal is not None:
            return invoice_data.subtotal

        return invoice_data.total_amount

    @staticmethod
    def concatenate_line_items(line_items: List[InvoiceLineItem]) -> str:
        """
        Concatenate all line item descriptions with '; ' separator

        Args:
            line_items: List of invoice line items

        Returns:
            Concatenated string of descriptions

        Examples:
            [Item(desc="A"), Item(desc="B")] → "A; B"
            [Item(desc="A")] → "A"
            [] → ""
        """
        descriptions = [item.description for item in line_items if item.description]
        return "; ".join(descriptions)

    @staticmethod
    def generate_xl_output_row(invoice_data: InvoiceData, voucher_number: int) -> XLOutputRow:
        """
        Generate complete XL_Output row from invoice data

        Args:
            invoice_data: Parsed invoice data from Mistral OCR
            voucher_number: Sequential voucher number (1, 2, 3...)

        Returns:
            XLOutputRow with all fields populated

        Field Mapping:
        - voucher_date: invoice_date from header
        - voucher_type_name: Always "Journal"
        - voucher_number: Sequential counter
        - buyer_supplier_address: vendor_address from header
        - buyer_supplier_pincode: Extracted from vendor_address
        - ledger_name: "ABC" (placeholder for future LLM mapping)
        - ledger_amount: subtotal if GST exists, else total_amount
        - ledger_amount_dr_cr: Always "Dr"
        - ledger_narration: Concatenated line item descriptions
        """
        # Extract pincode from vendor address
        pincode = XLOutputGenerator.extract_pincode(invoice_data.header.vendor_address)

        # Calculate ledger amount
        ledger_amount = XLOutputGenerator.calculate_ledger_amount(invoice_data)

        # Concatenate line item descriptions for narration
        narration = XLOutputGenerator.concatenate_line_items(invoice_data.line_items)

        return XLOutputRow(
            voucher_date=invoice_data.header.invoice_date,
            voucher_type_name="Journal",
            voucher_number=voucher_number,
            buyer_supplier_address=invoice_data.header.vendor_address or "",
            buyer_supplier_pincode=pincode,
            ledger_name="PENDING",  # Will be updated by Ollama worker
            ledger_amount=ledger_amount,
            ledger_amount_dr_cr="Dr",
            ledger_narration=narration,
            confidence_score=None  # Will be set by Ollama worker
        )
