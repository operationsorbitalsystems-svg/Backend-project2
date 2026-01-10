from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

# === Session Models ===

class SessionCreateResponse(BaseModel):
    batch_id: str
    created_at: datetime
    status: str


# === File Status Model ===

class FileStatus(BaseModel):
    filename: str
    status: str  # "pending", "processing", "completed", "failed"
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


class InvoiceLineItem(BaseModel):
    description: str
    quantity: Optional[int] = None
    unit_price: Optional[float] = None
    amount: float


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
    data: Optional[List[ProcessedInvoiceResult]] = None
    completed_at: Optional[datetime] = None


class UploadResponse(BaseModel):
    batch_id: str
    received_files: int
    status: str
    message: str


# === Error Models ===

class ErrorResponse(BaseModel):
    detail: str
    status_code: int
