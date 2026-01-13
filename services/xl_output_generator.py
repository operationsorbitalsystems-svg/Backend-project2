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
    def generate_xl_output_rows(
        invoice_data: InvoiceData,
        expense_ledger_name: str,
        vendor_ledger_name: str,
        expense_confidence: float,
        vendor_confidence: float,
        voucher_number: int
    ) -> List[XLOutputRow]:
        """
        Generate multiple XL output rows per invoice (Dr/Cr pairing + GST).

        Returns 2-5+ rows depending on invoice structure:
        - 1 Dr row for expense
        - 0-3 Dr rows for GST (CGST/SGST/IGST)
        - 1 Cr row for vendor

        Args:
            invoice_data: Parsed invoice data from Mistral OCR
            expense_ledger_name: Expense ledger from Ollama (or "Suspended AC")
            vendor_ledger_name: Vendor ledger from Ollama (or "Suspended AC")
            expense_confidence: Confidence score for expense ledger (0.0-1.0)
            vendor_confidence: Confidence score for vendor ledger (0.0-1.0)
            voucher_number: Sequential voucher number (1, 2, 3...)

        Returns:
            List of XLOutputRow objects
        """
        rows = []

        # Common fields for all rows
        voucher_date = invoice_data.header.invoice_date
        vendor_address = invoice_data.header.vendor_address or ""
        pincode = XLOutputGenerator.extract_pincode(vendor_address)
        narration = XLOutputGenerator.concatenate_line_items(invoice_data.line_items)

        # Step 1: Dr entry for expense
        has_gst = XLOutputGenerator.detect_has_gst(invoice_data)
        ledger_amount = invoice_data.subtotal if (has_gst and invoice_data.subtotal) else invoice_data.total_amount

        rows.append(XLOutputRow(
            voucher_date=voucher_date,
            voucher_type_name="Journal",
            voucher_number=voucher_number,
            buyer_supplier_address=vendor_address,
            buyer_supplier_pincode=pincode,
            ledger_name=expense_ledger_name,  # From Ollama
            ledger_amount=ledger_amount,
            ledger_amount_dr_cr="Dr",
            ledger_narration=narration,
            confidence_score=expense_confidence
        ))

        # Step 2: Dr entries for GST (if applicable) - PROGRAMMATIC
        if invoice_data.cgst_tax_amount and invoice_data.cgst_tax_amount > 0:
            rows.append(XLOutputRow(
                voucher_date=voucher_date,
                voucher_type_name="Journal",
                voucher_number=voucher_number,
                buyer_supplier_address=vendor_address,
                buyer_supplier_pincode=pincode,
                ledger_name="CGST",  # Programmatic for now
                ledger_amount=invoice_data.cgst_tax_amount,
                ledger_amount_dr_cr="Dr",
                ledger_narration=narration,
                confidence_score=1.0  # Programmatic = always confident
            ))

        if invoice_data.sgst_tax_amount and invoice_data.sgst_tax_amount > 0:
            rows.append(XLOutputRow(
                voucher_date=voucher_date,
                voucher_type_name="Journal",
                voucher_number=voucher_number,
                buyer_supplier_address=vendor_address,
                buyer_supplier_pincode=pincode,
                ledger_name="SGST",  # Programmatic for now
                ledger_amount=invoice_data.sgst_tax_amount,
                ledger_amount_dr_cr="Dr",
                ledger_narration=narration,
                confidence_score=1.0
            ))

        if invoice_data.igst_tax_amount and invoice_data.igst_tax_amount > 0:
            rows.append(XLOutputRow(
                voucher_date=voucher_date,
                voucher_type_name="Journal",
                voucher_number=voucher_number,
                buyer_supplier_address=vendor_address,
                buyer_supplier_pincode=pincode,
                ledger_name="IGST",  # Programmatic for now
                ledger_amount=invoice_data.igst_tax_amount,
                ledger_amount_dr_cr="Dr",
                ledger_narration=narration,
                confidence_score=1.0
            ))

        # Step 3: Cr entry for vendor/liability
        rows.append(XLOutputRow(
            voucher_date=voucher_date,
            voucher_type_name="Journal",
            voucher_number=voucher_number,
            buyer_supplier_address=vendor_address,
            buyer_supplier_pincode=pincode,
            ledger_name=vendor_ledger_name,  # From Ollama
            ledger_amount=invoice_data.total_amount,  # Total including GST
            ledger_amount_dr_cr="Cr",
            ledger_narration=narration,
            confidence_score=vendor_confidence
        ))

        # Step 4: TDS entries placeholder (Phase 2)
        # tds_rows = add_tds_entries(invoice_data, narration, None, voucher_number)
        # rows.extend(tds_rows)

        return rows
