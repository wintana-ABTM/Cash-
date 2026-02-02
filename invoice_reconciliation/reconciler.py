"""Reconciliation engine for matching invoices against pickups."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from .models import Invoice, InvoiceLineItem, Pickup, ReconciliationResult


@dataclass
class ReconciliationSummary:
    """Summary of reconciliation across all locations and months."""
    total_locations: int
    total_months: int
    matched_count: int
    mismatched_count: int
    total_invoice_amount: Decimal
    total_pickup_amount: Optional[Decimal]
    total_difference: Optional[Decimal]
    results: list[ReconciliationResult] = field(default_factory=list)

    @property
    def match_rate(self) -> float:
        """Percentage of location-months that matched."""
        total = self.matched_count + self.mismatched_count
        if total == 0:
            return 0.0
        return (self.matched_count / total) * 100


class InvoiceReconciler:
    """
    Reconciliation engine for comparing invoice line items against pickup records.

    Matches by location ID and month (YYYY-MM format).
    """

    def __init__(
        self,
        match_by_date: bool = False,
        tolerance_amount: Optional[Decimal] = None,
    ):
        """
        Initialize the reconciler.

        Args:
            match_by_date: If True, match by exact date instead of just month
            tolerance_amount: Allow amount differences up to this value to still count as matched
        """
        self.match_by_date = match_by_date
        self.tolerance_amount = tolerance_amount or Decimal("0")

    def reconcile(
        self,
        invoices: list[Invoice],
        pickups: list[Pickup],
        location_ids: Optional[list[str]] = None,
        months: Optional[list[str]] = None,
    ) -> ReconciliationSummary:
        """
        Reconcile invoices against pickup records.

        Args:
            invoices: List of parsed invoices
            pickups: List of parsed pickup records
            location_ids: Optional filter for specific location IDs
            months: Optional filter for specific months (YYYY-MM format)

        Returns:
            ReconciliationSummary with detailed results
        """
        # Index invoice items by location and month
        invoice_index = self._index_invoice_items(invoices)

        # Index pickups by location and month
        pickup_index = self._index_pickups(pickups)

        # Get all unique location-month combinations
        all_keys = set(invoice_index.keys()) | set(pickup_index.keys())

        # Apply filters
        if location_ids:
            all_keys = {k for k in all_keys if k[0] in location_ids}
        if months:
            all_keys = {k for k in all_keys if k[1] in months}

        # Reconcile each location-month
        results = []
        for location_id, month in sorted(all_keys):
            invoice_items = invoice_index.get((location_id, month), [])
            pickup_records = pickup_index.get((location_id, month), [])

            result = self._reconcile_location_month(
                location_id, month, invoice_items, pickup_records
            )
            results.append(result)

        # Build summary
        return self._build_summary(results)

    def reconcile_single_location(
        self,
        invoices: list[Invoice],
        pickups: list[Pickup],
        location_id: str,
        month: str,
    ) -> ReconciliationResult:
        """
        Reconcile a single location for a specific month.

        Args:
            invoices: List of parsed invoices
            pickups: List of parsed pickup records
            location_id: Location ID to reconcile
            month: Month to reconcile (YYYY-MM format)

        Returns:
            ReconciliationResult for the specified location-month
        """
        # Get invoice items for this location-month
        invoice_items = []
        for invoice in invoices:
            invoice_items.extend(invoice.get_items_by_location_month(location_id, month))

        # Get pickups for this location-month
        pickup_records = [
            p for p in pickups
            if p.location_id == location_id and p.month_key == month
        ]

        return self._reconcile_location_month(
            location_id, month, invoice_items, pickup_records
        )

    def _index_invoice_items(
        self, invoices: list[Invoice]
    ) -> dict[tuple[str, str], list[InvoiceLineItem]]:
        """Index invoice items by (location_id, month)."""
        index = defaultdict(list)
        for invoice in invoices:
            for item in invoice.line_items:
                key = (item.location_id, item.month_key)
                index[key].append(item)
        return dict(index)

    def _index_pickups(
        self, pickups: list[Pickup]
    ) -> dict[tuple[str, str], list[Pickup]]:
        """Index pickups by (location_id, month)."""
        index = defaultdict(list)
        for pickup in pickups:
            key = (pickup.location_id, pickup.month_key)
            index[key].append(pickup)
        return dict(index)

    def _reconcile_location_month(
        self,
        location_id: str,
        month: str,
        invoice_items: list[InvoiceLineItem],
        pickups: list[Pickup],
    ) -> ReconciliationResult:
        """Reconcile invoice items against pickups for a single location-month."""
        # Calculate totals
        invoice_total = sum(item.amount for item in invoice_items)
        pickup_total = None
        if pickups and any(p.expected_amount is not None for p in pickups):
            pickup_total = sum(
                p.expected_amount for p in pickups if p.expected_amount is not None
            )

        # Match items
        matched = []
        unmatched_invoices = list(invoice_items)
        unmatched_pickups = list(pickups)

        if self.match_by_date:
            # Match by exact date
            matched, unmatched_invoices, unmatched_pickups = self._match_by_date(
                invoice_items, pickups
            )
        else:
            # Match by count only (since we're already filtered to same month)
            min_count = min(len(invoice_items), len(pickups))
            for i in range(min_count):
                matched.append((invoice_items[i], pickups[i]))
            unmatched_invoices = invoice_items[min_count:]
            unmatched_pickups = pickups[min_count:]

        # Calculate differences
        diff_count = len(invoice_items) - len(pickups)
        diff_amount = None
        if pickup_total is not None:
            diff_amount = invoice_total - pickup_total

        return ReconciliationResult(
            location_id=location_id,
            month=month,
            invoice_count=len(invoice_items),
            invoice_total=invoice_total,
            pickup_count=len(pickups),
            pickup_total=pickup_total,
            difference_count=diff_count,
            difference_amount=diff_amount,
            matched_items=matched,
            unmatched_invoice_items=unmatched_invoices,
            unmatched_pickups=unmatched_pickups,
        )

    def _match_by_date(
        self,
        invoice_items: list[InvoiceLineItem],
        pickups: list[Pickup],
    ) -> tuple[list[tuple], list[InvoiceLineItem], list[Pickup]]:
        """Match invoice items to pickups by exact date."""
        matched = []
        unmatched_invoices = []
        remaining_pickups = list(pickups)

        for item in invoice_items:
            match_found = False
            for i, pickup in enumerate(remaining_pickups):
                if item.service_date == pickup.pickup_date:
                    matched.append((item, pickup))
                    remaining_pickups.pop(i)
                    match_found = True
                    break

            if not match_found:
                unmatched_invoices.append(item)

        return matched, unmatched_invoices, remaining_pickups

    def _build_summary(
        self, results: list[ReconciliationResult]
    ) -> ReconciliationSummary:
        """Build summary from individual results."""
        unique_locations = set(r.location_id for r in results)
        unique_months = set(r.month for r in results)

        matched_count = sum(1 for r in results if r.is_reconciled)
        mismatched_count = len(results) - matched_count

        total_invoice = sum(r.invoice_total for r in results)

        total_pickup = None
        if any(r.pickup_total is not None for r in results):
            total_pickup = sum(
                r.pickup_total for r in results if r.pickup_total is not None
            )

        total_diff = None
        if total_pickup is not None:
            total_diff = total_invoice - total_pickup

        return ReconciliationSummary(
            total_locations=len(unique_locations),
            total_months=len(unique_months),
            matched_count=matched_count,
            mismatched_count=mismatched_count,
            total_invoice_amount=total_invoice,
            total_pickup_amount=total_pickup,
            total_difference=total_diff,
            results=results,
        )


def get_discrepancies(
    summary: ReconciliationSummary,
    min_count_diff: int = 0,
    min_amount_diff: Optional[Decimal] = None,
) -> list[ReconciliationResult]:
    """
    Get only the discrepancy results from a reconciliation summary.

    Args:
        summary: ReconciliationSummary to filter
        min_count_diff: Minimum count difference to include
        min_amount_diff: Minimum amount difference to include

    Returns:
        List of ReconciliationResult with discrepancies
    """
    discrepancies = []

    for result in summary.results:
        if result.is_reconciled:
            continue

        include = False

        if abs(result.difference_count) >= min_count_diff:
            include = True

        if min_amount_diff is not None and result.difference_amount is not None:
            if abs(result.difference_amount) >= min_amount_diff:
                include = True

        if include:
            discrepancies.append(result)

    return discrepancies


def get_location_summary(
    summary: ReconciliationSummary,
    location_id: str,
) -> list[ReconciliationResult]:
    """Get all reconciliation results for a specific location."""
    return [r for r in summary.results if r.location_id == location_id]


def get_month_summary(
    summary: ReconciliationSummary,
    month: str,
) -> list[ReconciliationResult]:
    """Get all reconciliation results for a specific month."""
    return [r for r in summary.results if r.month == month]
