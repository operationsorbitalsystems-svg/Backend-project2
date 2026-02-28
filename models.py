from pydantic import BaseModel, constr, field_validator, create_model, Field
from typing import List, Optional, Dict, Any, Literal, Set
from datetime import datetime
from config import NOT_FOUND
import re
from utils.logger import setup_logger

logger = setup_logger()

# === Session Models ===

class SessionCreateResponse(BaseModel):
    batch_id: str
    created_at: datetime
    status: str


# === File Status Model ===

class FileStatus(BaseModel):
    filename: str
    status: str  # "pending", "processing", "ocr_complete", "ledger_processing", "completed", "failed"
    processed_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    error: Optional[str] = None


# === Invoice Data Models ===


class InvoiceHeader(BaseModel):
    invoice_number: str
    invoice_date: str
    vendor_name: str
    vendor_address: Optional[str] = None
    vendor_gstin: Optional[str] = None
    place_of_supply: Optional[str] = None
    vendor_pin_code: Optional[str] = None

    @field_validator("vendor_pin_code")
    @classmethod
    def validate_pincode(cls, v):
        if v is None:
            return None
        
        cleaned = re.sub(r"[ -]", "", v.strip())

        if cleaned.isdigit() and len(cleaned) == 6:
            return cleaned
        
        # Non-blocking behavior
        logger.warning(
            f"Invalid vendor_pin_code detected ('{v}'). "
            "Setting vendor_pin_code to None."
        )
        return None


class InvoiceLineItem(BaseModel):
    description: str
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: float


from pydantic import BaseModel, model_validator
from typing import List, Optional

class InvoiceData(BaseModel):
    header: InvoiceHeader
    line_items: List[InvoiceLineItem]
    subtotal: Optional[float] = None
    cgst_tax_amount: Optional[float] = None
    sgst_tax_amount: Optional[float] = None
    igst_tax_amount: Optional[float] = None
    total_amount: float
    currency: str
    already_recieved: Optional[float] = None

    @model_validator(mode="after")
    def sync_cgst_sgst(self):
        # Case 1: CGST exists but SGST missing
        if self.cgst_tax_amount is not None and self.sgst_tax_amount is None:
            self.sgst_tax_amount = self.cgst_tax_amount

        # Case 2: SGST exists but CGST missing
        elif self.sgst_tax_amount is not None and self.cgst_tax_amount is None:
            self.cgst_tax_amount = self.sgst_tax_amount

        return self
    
    
class XLOutputRow(BaseModel):
    """Excel output row for journal entry import"""
    voucher_date: str  # ISO date from invoice
    voucher_type_name: str  # Always "Journal"
    voucher_number: int  # Sequential counter
    buyer_supplier_address: str  # Vendor address
    buyer_supplier_pincode: Optional[str] = None  # Extracted 6-digit pincode
    ledger_name: str  # Ledger name from COA (via Ollama)
    ledger_amount: float  # subtotal if GST exists, else total_amount
    ledger_amount_dr_cr: str  # Always "Dr"
    ledger_narration: str  # Concatenated line items
    confidence_score: Optional[float] = None  # LLM confidence (0.0-1.0)


class ProcessedInvoiceResult(BaseModel):
    filename: str
    pdf_path: str
    json_path: str
    status: str
    invoice_number: Optional[str] = None
    vendor_name: Optional[str] = None
    total_amount: Optional[float] = None
    currency: Optional[str] = None
    line_items_count: Optional[int] = None
    xl_output: Optional[List[XLOutputRow]] = None
    data: Optional[InvoiceData] = None
    error: Optional[str] = None
    timestamp: str


# === Batch Status Models ===

class BatchStatusResponse(BaseModel):
    batch_id: str
    status: str  # "processing", "completed", "partial_complete"
    total_files: int
    processed: int
    pending: int
    failed: int
    file_statuses: List[FileStatus]
    coa_status: Optional['COAStatus'] = None
    coa_data: Optional['COAData'] = None
    data: Optional[List[ProcessedInvoiceResult]] = None
    completed_at: Optional[datetime] = None


class UploadResponse(BaseModel):
    batch_id: str
    received_files: int
    coa_received: bool
    status: str
    message: str


# === Error Models ===

class ErrorResponse(BaseModel):
    detail: str
    status_code: int


# === Task Queue Models ===

class TaskItem(BaseModel):
    """Represents a task in the processing queue"""
    task_id: str
    batch_id: str
    filename: str
    pdf_path: str
    enqueued_at: str  # ISO timestamp
    vendor_name:str


# === COA Models ===

class COAMetadata(BaseModel):
    """Lightweight COA metadata for session storage"""
    total_pages: int
    total_groups: int
    total_ledgers: int
    levels_discovered: int


class COAStatus(BaseModel):
    """COA processing status"""
    filename: str
    status: str  # "pending", "parsed", "failed"
    parsed_at: Optional[datetime] = None
    error: Optional[str] = None
    metadata: Optional[COAMetadata] = None


class COAData(BaseModel):
    """Full COA structure from coa.json file"""
    metadata: Dict[str, Any]
    hierarchy: Dict[str, Any]
    flat_list: List[str]


# === Generic Ollama Queue Models ====


class OllamaRequest(BaseModel):
    """Generic Ollama LLM request"""
    task_id: str
    batch_id: str
    system_prompt: str
    user_prompt: str
    enqueued_at: str
    metadata: Optional[Dict[str, Any]] = None  # For tracking context


class OllamaResponse(BaseModel):
    """Generic Ollama LLM response"""
    task_id: str
    response_text: str
    success: bool
    error: Optional[str] = None


# === Pydantic model for Ollama ===

class OllamaLedger(BaseModel):
    ledger : str
    
class TDSLedger(BaseModel):
    nature_of_transaction : str
    

def custom_tds(ledger_nature_set: Set[str]):
    try:
        
        ledger_nature_set.add(NOT_FOUND)
        
        # Create a Literal type with the provided list
        ledger_literal = Literal[tuple(ledger_nature_set)]
        
        # Dynamically create the model
        TDSLedger = create_model(
            'TDSLedger',
            nature_of_transaction=(ledger_literal, Field(description="Selected tds nature"))
        )
        
        return TDSLedger
    
    except Exception as e:
        raise e
    

def custom_ledger(leaf_node_list: List[str]):
    """
    Dynamically creates a Pydantic schema with ledger constrained to the provided list.
    
    Args:
        leaf_node_list: List of valid ledger names
    
    Returns:
        Pydantic model class with constrained ledger field
    """
    try:
        
        leaf_node_list.append(NOT_FOUND)
        
        # Create a Literal type with the provided list
        ledger_literal = Literal[tuple(leaf_node_list)]
        
        # Dynamically create the model
        OllamaLedger = create_model(
            'OllamaLedger',
            ledger=(ledger_literal, Field(description="Selected ledger account"))
        )
        
        return OllamaLedger
    
    except Exception as e:
        raise e


# === Config Management Models ===

class TDSRateItem(BaseModel):
    section: str
    nature_of_transaction: str
    threshold_limit: int
    tds_rate: float

class ConfigResponse(BaseModel):
    dr_prompt: str
    cr_prompt: str
    tds_prompt: str
    tds_rates: List[TDSRateItem]

class PromptUpdateRequest(BaseModel):
    content: str

class ConfigUpdateResponse(BaseModel):
    message: str


# === Mistral Queue Models ===

class MistralRequest(BaseModel):
    task_id: str           # UUID
    batch_id: str
    filename: str
    pdf_path: str
    enqueued_at: str       # ISO timestamp

class MistralResponse(BaseModel):
    task_id: str
    batch_id: str
    success: bool
    json_path: Optional[str] = None        # Path to saved JSON file (e.g., /tmp/invoice_uploads/batch-123/invoice.json)
    error_message: Optional[str] = None
    processed_at: str








# ── Data models - AGENT ───────────────────────────────────────────────────────────────

class AgentRequest(BaseModel):
    task_id: str
    batch_id: str
    line_item: str                       # invoice line item description
    expenses_tree: Dict[str, Any]           # COA Expenses subtree (JSON-serialisable)
    vendor_name: Optional[str] = None
    enqueued_at: str
    metadata: Dict[str, Any] = {}
    file_name: str


class AgentResponse(BaseModel):
    task_id: str
    batch_id: str
    line_item: str
    selected_leaf: Optional[str]            # None on failure
    success: bool
    error: Optional[str] = None
    completed_at: Optional[str] = None
    metadata: Dict[str, Any] = {}


