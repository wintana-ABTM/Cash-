"""Data models for invoice reconciliation."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass
class InvoiceLineItem:
    """Represents a line item from an armored vendor invoice."""
    location_id: str
    location_name: Optional[str]
    service_date: date
    service_type: str
    amount: Decimal
    description: Optional[str] = None

    @property
    def month_key(self) -> str:
        """Return YYYY-MM format for month matching."""
        return self.service_date.strftime("%Y-%m")


@dataclass
class Invoice:
    """Represents a complete armored vendor invoice."""
    invoice_number: str
    vendor_name: str
    invoice_date: date
    total_amount: Decimal
    line_items: list[InvoiceLineItem] = field(default_factory=list)
    source_file: Optional[str] = None

    def get_items_by_location_month(self, location_id: str, month: str) -> list[InvoiceLineItem]:
        """Get all line items for a specific location and month (YYYY-MM)."""
        return [
            item for item in self.line_items
            if item.location_id == location_id and item.month_key == month
        ]


@dataclass
class Pickup:
    """Represents a pickup record from CSV data."""
    location_id: str
    location_name: Optional[str]
    pickup_date: date
    pickup_type: str
    expected_amount: Optional[Decimal] = None
    reference_number: Optional[str] = None

    @property
    def month_key(self) -> str:
        """Return YYYY-MM format for month matching."""
        return self.pickup_date.strftime("%Y-%m")


@dataclass
class ReconciliationResult:
    """Result of reconciling invoice items against pickup records."""
    location_id: str
    month: str
    invoice_count: int
    invoice_total: Decimal
    pickup_count: int
    pickup_total: Optional[Decimal]
    difference_count: int
    difference_amount: Optional[Decimal]
    matched_items: list[tuple[InvoiceLineItem, Pickup]] = field(default_factory=list)
    unmatched_invoice_items: list[InvoiceLineItem] = field(default_factory=list)
    unmatched_pickups: list[Pickup] = field(default_factory=list)

    @property
    def is_reconciled(self) -> bool:
        """Check if counts match and no unmatched items exist."""
        return (
            self.difference_count == 0
            and len(self.unmatched_invoice_items) == 0
            and len(self.unmatched_pickups) == 0
        )

    @property
    def status(self) -> str:
        """Return reconciliation status string."""
        if self.is_reconciled:
            return "MATCHED"
        elif self.difference_count > 0:
            return "INVOICE_OVER"
        elif self.difference_count < 0:
            return "INVOICE_UNDER"
        else:
            return "MISMATCH"
