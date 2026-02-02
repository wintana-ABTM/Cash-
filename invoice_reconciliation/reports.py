"""Report generation for reconciliation results."""

import csv
from datetime import datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import Optional

from tabulate import tabulate

from .reconciler import ReconciliationSummary, ReconciliationResult


def format_currency(amount: Optional[Decimal]) -> str:
    """Format a decimal as currency."""
    if amount is None:
        return "N/A"
    return f"${amount:,.2f}"


def format_diff(value: int | Decimal | None) -> str:
    """Format a difference value with sign."""
    if value is None:
        return "N/A"
    if isinstance(value, int):
        if value > 0:
            return f"+{value}"
        return str(value)
    else:
        if value > 0:
            return f"+${value:,.2f}"
        elif value < 0:
            return f"-${abs(value):,.2f}"
        return "$0.00"


def generate_summary_report(summary: ReconciliationSummary) -> str:
    """Generate a text summary report."""
    lines = []
    lines.append("=" * 70)
    lines.append("INVOICE RECONCILIATION SUMMARY")
    lines.append("=" * 70)
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append(f"Total Locations:     {summary.total_locations}")
    lines.append(f"Total Months:        {summary.total_months}")
    lines.append(f"Total Comparisons:   {len(summary.results)}")
    lines.append("")
    lines.append(f"Matched:             {summary.matched_count}")
    lines.append(f"Mismatched:          {summary.mismatched_count}")
    lines.append(f"Match Rate:          {summary.match_rate:.1f}%")
    lines.append("")
    lines.append("-" * 70)
    lines.append("FINANCIAL SUMMARY")
    lines.append("-" * 70)
    lines.append(f"Total Invoice Amount:  {format_currency(summary.total_invoice_amount)}")
    lines.append(f"Total Pickup Amount:   {format_currency(summary.total_pickup_amount)}")
    lines.append(f"Total Difference:      {format_diff(summary.total_difference)}")
    lines.append("")

    return "\n".join(lines)


def generate_detail_report(
    summary: ReconciliationSummary,
    show_matched: bool = False,
) -> str:
    """Generate a detailed report with all location-month results."""
    lines = []
    lines.append(generate_summary_report(summary))
    lines.append("-" * 70)
    lines.append("DETAIL BY LOCATION AND MONTH")
    lines.append("-" * 70)
    lines.append("")

    # Prepare table data
    headers = [
        "Location",
        "Month",
        "Inv Count",
        "Pickup Count",
        "Diff",
        "Inv Amount",
        "Pickup Amt",
        "Amt Diff",
        "Status",
    ]

    rows = []
    for result in summary.results:
        if not show_matched and result.is_reconciled:
            continue

        rows.append([
            result.location_id,
            result.month,
            result.invoice_count,
            result.pickup_count,
            format_diff(result.difference_count),
            format_currency(result.invoice_total),
            format_currency(result.pickup_total),
            format_diff(result.difference_amount),
            result.status,
        ])

    if rows:
        lines.append(tabulate(rows, headers=headers, tablefmt="simple"))
    else:
        lines.append("All location-months are reconciled!")

    lines.append("")
    return "\n".join(lines)


def generate_discrepancy_report(summary: ReconciliationSummary) -> str:
    """Generate a report focusing only on discrepancies."""
    discrepancies = [r for r in summary.results if not r.is_reconciled]

    lines = []
    lines.append("=" * 70)
    lines.append("DISCREPANCY REPORT")
    lines.append("=" * 70)
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Total Discrepancies: {len(discrepancies)}")
    lines.append("")

    if not discrepancies:
        lines.append("No discrepancies found - all invoices reconciled!")
        return "\n".join(lines)

    # Group by status
    invoice_over = [r for r in discrepancies if r.status == "INVOICE_OVER"]
    invoice_under = [r for r in discrepancies if r.status == "INVOICE_UNDER"]
    mismatched = [r for r in discrepancies if r.status == "MISMATCH"]

    if invoice_over:
        lines.append("-" * 70)
        lines.append(f"INVOICE OVER (more invoices than pickups): {len(invoice_over)}")
        lines.append("-" * 70)
        for r in invoice_over:
            lines.append(f"  Location {r.location_id} / {r.month}:")
            lines.append(f"    Invoice count: {r.invoice_count}, Pickup count: {r.pickup_count}")
            lines.append(f"    Difference: {format_diff(r.difference_count)} items")
            if r.difference_amount is not None:
                lines.append(f"    Amount diff: {format_diff(r.difference_amount)}")
            lines.append("")

    if invoice_under:
        lines.append("-" * 70)
        lines.append(f"INVOICE UNDER (fewer invoices than pickups): {len(invoice_under)}")
        lines.append("-" * 70)
        for r in invoice_under:
            lines.append(f"  Location {r.location_id} / {r.month}:")
            lines.append(f"    Invoice count: {r.invoice_count}, Pickup count: {r.pickup_count}")
            lines.append(f"    Difference: {format_diff(r.difference_count)} items")
            if r.difference_amount is not None:
                lines.append(f"    Amount diff: {format_diff(r.difference_amount)}")
            lines.append("")

    if mismatched:
        lines.append("-" * 70)
        lines.append(f"OTHER MISMATCHES: {len(mismatched)}")
        lines.append("-" * 70)
        for r in mismatched:
            lines.append(f"  Location {r.location_id} / {r.month}:")
            lines.append(f"    Invoice count: {r.invoice_count}, Pickup count: {r.pickup_count}")
            lines.append("")

    return "\n".join(lines)


def export_to_csv(
    summary: ReconciliationSummary,
    output_path: str | Path,
    include_matched: bool = True,
) -> None:
    """Export reconciliation results to CSV."""
    output_path = Path(output_path)

    rows = []
    for result in summary.results:
        if not include_matched and result.is_reconciled:
            continue

        rows.append({
            "location_id": result.location_id,
            "month": result.month,
            "invoice_count": result.invoice_count,
            "pickup_count": result.pickup_count,
            "count_difference": result.difference_count,
            "invoice_total": str(result.invoice_total),
            "pickup_total": str(result.pickup_total) if result.pickup_total else "",
            "amount_difference": str(result.difference_amount) if result.difference_amount else "",
            "status": result.status,
            "is_reconciled": result.is_reconciled,
        })

    if not rows:
        rows = [{"message": "No results to export"}]

    fieldnames = list(rows[0].keys())

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def export_unmatched_items(
    summary: ReconciliationSummary,
    output_path: str | Path,
) -> None:
    """Export unmatched invoice items and pickups to CSV."""
    output_path = Path(output_path)

    # Collect all unmatched items
    unmatched_invoices = []
    unmatched_pickups = []

    for result in summary.results:
        for item in result.unmatched_invoice_items:
            unmatched_invoices.append({
                "type": "invoice",
                "location_id": item.location_id,
                "location_name": item.location_name or "",
                "date": str(item.service_date),
                "month": item.month_key,
                "service_type": item.service_type,
                "amount": str(item.amount),
                "description": item.description or "",
            })

        for pickup in result.unmatched_pickups:
            unmatched_pickups.append({
                "type": "pickup",
                "location_id": pickup.location_id,
                "location_name": pickup.location_name or "",
                "date": str(pickup.pickup_date),
                "month": pickup.month_key,
                "service_type": pickup.pickup_type,
                "amount": str(pickup.expected_amount) if pickup.expected_amount else "",
                "description": pickup.reference_number or "",
            })

    all_unmatched = unmatched_invoices + unmatched_pickups

    if not all_unmatched:
        all_unmatched = [{"message": "No unmatched items"}]

    fieldnames = list(all_unmatched[0].keys())

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_unmatched)


def print_quick_summary(summary: ReconciliationSummary) -> None:
    """Print a quick one-line summary to console."""
    total = len(summary.results)
    matched = summary.matched_count
    rate = summary.match_rate

    print(f"Reconciliation: {matched}/{total} matched ({rate:.1f}%)")

    if summary.mismatched_count > 0:
        print(f"  -> {summary.mismatched_count} discrepancies found")
        if summary.total_difference:
            print(f"  -> Total amount difference: {format_diff(summary.total_difference)}")
