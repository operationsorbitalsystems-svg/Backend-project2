"""
Centralized service for managing Ollama task creation and enqueueing.

This service provides a single point of entry for all Ollama-related tasks,
making it easy to add new task types in the future without scattering
enqueueing logic across the codebase.
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime
from uuid import uuid4

from models import OllamaTask

from utils.logger import setup_logger

logger = setup_logger()


class OllamaTaskService:
    """
    Centralized service for Ollama task management.

    Future task types can be added as new methods:
    - enqueue_ledger_selection() [Current]
    - enqueue_line_item_categorization() [Future]
    - enqueue_vendor_classification() [Future]
    """

    @staticmethod
    async def enqueue_ledger_selection(
        ollama_queue_manager,
        batch_id: str,
        filename: str,
        invoice_number: str,
        ledger_narration: str
    ) -> str:
        """
        Enqueue a ledger selection task for an invoice.

        Args:
            ollama_queue_manager: OllamaTaskQueueManager instance
            batch_id: Batch identifier
            filename: Invoice filename
            invoice_number: Invoice number from OCR
            ledger_narration: Concatenated line items

        Returns:
            task_id of the enqueued task
        """
        # Validate inputs
        if not ledger_narration or ledger_narration.strip() == "":
            logger.warning(f"Empty ledger_narration for {filename}, using placeholder")
            ledger_narration = "Miscellaneous Expense"

        # Enqueue task
        task_id = await ollama_queue_manager.enqueue_task(
            batch_id=batch_id,
            filename=filename,
            invoice_number=invoice_number,
            ledger_narration=ledger_narration,
            metadata={"purpose": "ledger_selection"}
        )

        logger.info(f"📝 Enqueued ledger selection task {task_id} for {filename}")
        return task_id

    # Future methods:
    # @staticmethod
    # async def enqueue_line_item_categorization(...):
    #     """Categorize individual line items (multiple calls per invoice)"""
    #     pass

    # @staticmethod
    # async def enqueue_vendor_classification(...):
    #     """Classify vendor type"""
    #     pass
