import json
import logging
from typing import Dict, Any, Tuple, Optional
from pathlib import Path
from config import MISTRAL_API_KEY
from models import InvoiceData, InvoiceHeader, InvoiceLineItem

logger = logging.getLogger("invoice_parser")


class InvoiceParser:
    """Handles invoice parsing using Mistral AI"""
    
    def __init__(self):
        self.api_key = MISTRAL_API_KEY
        if not self.api_key:
            logger.warning("MISTRAL_API_KEY not set. Parsing will use mock data.")
    
    async def parse_invoice(self, pdf_path: str) -> Tuple[bool, Optional[InvoiceData], Optional[str]]:
        """
        Parse a single invoice PDF using Mistral OCR
        Returns: (success, invoice_data, error_message)
        """
        try:
            # For MVP: Return mock data
            # In production, integrate with Mistral API
            invoice_data = self._generate_mock_invoice()
            logger.info(f"Parsed invoice: {pdf_path}")
            return True, invoice_data, None
        
        except Exception as e:
            error_msg = f"Failed to parse invoice: {str(e)}"
            logger.error(error_msg)
            return False, None, error_msg
    
    async def parse_invoice_with_mistral(self, pdf_path: str) -> Tuple[bool, Optional[InvoiceData], Optional[str]]:
        """
        Parse invoice using actual Mistral API
        This is a placeholder for future implementation
        """
        try:
            # This would be the real Mistral integration
            # For now, keeping it as fallback/reference
            logger.info("Mistral API parsing would go here")
            return False, None, "Mistral API integration pending"
        
        except Exception as e:
            return False, None, str(e)
    
    def _generate_mock_invoice(self) -> InvoiceData:
        """Generate mock invoice data for development/testing"""
        header = InvoiceHeader(
            invoice_number="INV-001",
            invoice_date="2026-01-10",
            vendor_name="Sample Vendor Inc.",
            vendor_address="123 Business Street, City, State 12345",
            vendor_gstin="GSTIN123456",
            place_of_supply="State 1"
        )
        
        line_items = [
            InvoiceLineItem(
                description="Service A",
                quantity=1,
                unit_price=1000.00,
                amount=1000.00
            ),
            InvoiceLineItem(
                description="Service B",
                quantity=2,
                unit_price=500.00,
                amount=1000.00
            )
        ]
        
        invoice_data = InvoiceData(
            header=header,
            line_items=line_items,
            subtotal=2000.00,
            cgst_tax_amount=180.00,
            sgst_tax_amount=180.00,
            igst_tax_amount=0.00,
            total_amount=2360.00,
            currency="INR",
            already_recieved=0.00
        )
        
        return invoice_data
    
    def extract_metadata(self, invoice_data: InvoiceData) -> Dict[str, Any]:
        """
        Extract key metadata from parsed invoice
        Returns summary information for quick display
        """
        return {
            "invoice_number": invoice_data.header.invoice_number,
            "vendor_name": invoice_data.header.vendor_name,
            "total_amount": invoice_data.total_amount,
            "currency": invoice_data.currency,
            "line_items_count": len(invoice_data.line_items),
            "invoice_date": invoice_data.header.invoice_date,
        }
    
    def validate_invoice_data(self, invoice_data: InvoiceData) -> Tuple[bool, str]:
        """
        Validate extracted invoice data
        Returns: (is_valid, error_message)
        """
        # Check required fields
        if not invoice_data.header.invoice_number:
            return False, "Missing invoice number"
        
        if not invoice_data.header.vendor_name:
            return False, "Missing vendor name"
        
        if invoice_data.total_amount <= 0:
            return False, "Invalid total amount"
        
        if not invoice_data.currency:
            return False, "Missing currency"
        
        if not invoice_data.line_items:
            return False, "No line items found"
        
        # Validate line items
        for item in invoice_data.line_items:
            if item.amount <= 0:
                return False, f"Invalid amount for item: {item.description}"
        
        return True, ""
    
    def format_invoice_response(self, 
                               filename: str,
                               pdf_path: str,
                               json_path: str,
                               invoice_data: Optional[InvoiceData],
                               error: Optional[str] = None) -> Dict[str, Any]:
        """
        Format invoice data into response structure
        """
        from datetime import datetime
        
        if error or not invoice_data:
            return {
                "filename": filename,
                "pdf_path": pdf_path,
                "json_path": json_path,
                "status": "error",
                "error": error,
                "timestamp": datetime.utcnow().isoformat()
            }
        
        metadata = self.extract_metadata(invoice_data)
        
        return {
            "filename": filename,
            "pdf_path": pdf_path,
            "json_path": json_path,
            "status": "success",
            "invoice_number": metadata["invoice_number"],
            "vendor_name": metadata["vendor_name"],
            "total_amount": metadata["total_amount"],
            "currency": metadata["currency"],
            "line_items_count": metadata["line_items_count"],
            "data": invoice_data.model_dump(),
            "error": None,
            "timestamp": datetime.utcnow().isoformat()
        }
