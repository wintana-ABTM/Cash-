"""
Vendor-Aware Invoice Reconciliation

Reconciles invoices against tracking data using vendor-specific pricing rules.
"""

import pandas as pd
import pdfplumber
import re
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict
import calendar

from .vendor_config import VendorConfig, VendorConfigManager, get_vendor_manager
from .estimation import EstimationResult, EstimationEngine


@dataclass
class InvoiceLineItem:
    """A line item extracted from an invoice."""
    description: str
    quantity: int
    rate: float
    amount: float
    location: Optional[str] = None
    service_type: Optional[str] = None
    rate_key: Optional[str] = None


@dataclass
class ParsedInvoice:
    """A parsed invoice with extracted data."""
    invoice_number: str
    invoice_date: date
    vendor_name: str
    service_month: str
    total_amount: float
    line_items: List[InvoiceLineItem] = field(default_factory=list)
    subtotal: float = 0.0
    fuel_surcharge: float = 0.0
    raw_text: str = ""


@dataclass
class ReconciliationDiscrepancy:
    """A discrepancy found during reconciliation."""
    discrepancy_type: str  # "count_mismatch", "rate_mismatch", "amount_mismatch", "missing_item", "extra_item"
    location: Optional[str]
    service_type: str
    expected_value: Any
    actual_value: Any
    impact: float
    recommended_action: str


@dataclass
class ReconciliationResult:
    """Complete reconciliation result."""
    vendor_name: str
    invoice_number: str
    invoice_date: date
    service_month: str

    # Totals comparison
    invoice_total: float
    expected_total: float
    variance: float

    # Status
    status: str  # "PERFECT_MATCH", "MINOR_VARIANCE", "DISCREPANCY"

    # Line-by-line comparison
    comparison_table: List[Dict] = field(default_factory=list)

    # Location-level analysis
    location_analysis: List[Dict] = field(default_factory=list)

    # Discrepancies
    discrepancies: List[ReconciliationDiscrepancy] = field(default_factory=list)

    # Rate validation
    rate_validations: List[Dict] = field(default_factory=list)

    # Fuel surcharge validation
    fuel_surcharge_expected: float = 0.0
    fuel_surcharge_actual: float = 0.0
    fuel_surcharge_variance: float = 0.0


class VendorAwareInvoiceParser:
    """Parses invoices using vendor-specific patterns."""

    def __init__(self, vendor_config: VendorConfig):
        """Initialize the parser.

        Args:
            vendor_config: Vendor configuration with invoice patterns
        """
        self.vendor = vendor_config

    def parse_invoice(self, pdf_path: str) -> ParsedInvoice:
        """Parse an invoice PDF.

        Args:
            pdf_path: Path to the PDF file

        Returns:
            ParsedInvoice with extracted data
        """
        with pdfplumber.open(pdf_path) as pdf:
            # Extract all text
            full_text = ""
            all_tables = []

            for page in pdf.pages:
                text = page.extract_text() or ""
                full_text += text + "\n"

                # Extract tables
                tables = page.extract_tables()
                all_tables.extend(tables)

        # Parse invoice metadata
        invoice_number = self._extract_invoice_number(full_text)
        invoice_date = self._extract_invoice_date(full_text)
        service_month = self._extract_service_month(full_text, invoice_date)
        total_amount = self._extract_total_amount(full_text)

        # Parse line items
        line_items = self._extract_line_items(full_text, all_tables)

        # Calculate subtotal and fuel surcharge
        subtotal = sum(item.amount for item in line_items if item.rate_key != "fuel_surcharge")
        fuel_surcharge = sum(item.amount for item in line_items if item.rate_key == "fuel_surcharge")

        return ParsedInvoice(
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            vendor_name=self.vendor.name,
            service_month=service_month,
            total_amount=total_amount,
            line_items=line_items,
            subtotal=subtotal,
            fuel_surcharge=fuel_surcharge,
            raw_text=full_text
        )

    def _extract_invoice_number(self, text: str) -> str:
        """Extract invoice number from text."""
        pattern = self.vendor.invoice_patterns.get("invoice_number", r"Invoice\s*#?\s*:?\s*(\d+)")

        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)

        # Fallback patterns
        fallback_patterns = [
            r"Invoice\s*(?:Number|#|No\.?)?\s*:?\s*(\d+)",
            r"INV[#-]?(\d+)",
            r"#\s*(\d+)"
        ]

        for pattern in fallback_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)

        return "UNKNOWN"

    def _extract_invoice_date(self, text: str) -> date:
        """Extract invoice date from text."""
        # Common date patterns
        date_patterns = [
            (r"Invoice\s*Date\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", "%m/%d/%Y"),
            (r"Date\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", "%m/%d/%Y"),
            (r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})", "%m/%d/%Y"),
            (r"(\w+\s+\d{1,2},?\s+\d{4})", "%B %d, %Y"),
        ]

        for pattern, date_format in date_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                date_str = match.group(1)
                try:
                    # Try different formats
                    for fmt in [date_format, "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%B %d, %Y", "%B %d %Y"]:
                        try:
                            return datetime.strptime(date_str.replace(",", ""), fmt).date()
                        except ValueError:
                            continue
                except Exception:
                    pass

        return date.today()

    def _extract_service_month(self, text: str, invoice_date: date) -> str:
        """Extract service month from text."""
        # Look for service period
        patterns = [
            r"Service\s*(?:Period|Month)\s*:?\s*(\w+\s+\d{4})",
            r"For\s*(?:Services|Period)\s*:?\s*(\w+\s+\d{4})",
            r"(\w+)\s+\d{4}\s+Services",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)

        # Default to month before invoice date
        prev_month = invoice_date.replace(day=1) - pd.Timedelta(days=1)
        return f"{calendar.month_name[prev_month.month]} {prev_month.year}"

    def _extract_total_amount(self, text: str) -> float:
        """Extract total amount from text."""
        patterns = [
            r"(?:Total|Amount\s*Due|Grand\s*Total)\s*:?\s*\$?([\d,]+\.?\d*)",
            r"\$\s*([\d,]+\.\d{2})\s*$",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
            if matches:
                # Take the last/largest match as total
                amounts = [float(m.replace(",", "")) for m in matches]
                return max(amounts)

        return 0.0

    def _extract_line_items(self, text: str, tables: List) -> List[InvoiceLineItem]:
        """Extract line items from invoice."""
        line_items = []

        # Try to extract from tables first
        for table in tables:
            if not table:
                continue

            # Look for table with QTY, RATE, AMOUNT columns
            header_row = None
            for i, row in enumerate(table):
                if row and any("QTY" in str(cell).upper() or "QUANTITY" in str(cell).upper()
                              for cell in row if cell):
                    header_row = i
                    break

            if header_row is not None:
                # Parse table rows
                for row in table[header_row + 1:]:
                    if not row or not any(row):
                        continue

                    item = self._parse_table_row(row)
                    if item:
                        line_items.append(item)

        # If no table items found, try text-based extraction
        if not line_items:
            line_items = self._extract_line_items_from_text(text)

        return line_items

    def _parse_table_row(self, row: List) -> Optional[InvoiceLineItem]:
        """Parse a table row into a line item."""
        if not row or len(row) < 3:
            return None

        # Clean up row values
        cleaned = [str(cell).strip() if cell else "" for cell in row]

        # Try to identify columns
        description = ""
        quantity = 0
        rate = 0.0
        amount = 0.0

        for i, val in enumerate(cleaned):
            if not val:
                continue

            # Check if it's a number
            try:
                num = float(val.replace(",", "").replace("$", ""))

                # Heuristic: small integers are quantity, others are rate/amount
                if num == int(num) and num < 100:
                    quantity = int(num)
                elif rate == 0:
                    rate = num
                else:
                    amount = num
            except ValueError:
                # It's text - likely description
                if len(val) > 3 and not description:
                    description = val

        if description and (quantity > 0 or amount > 0):
            # Try to identify service type
            service_type = None
            rate_key = None

            for comp in self.vendor.billing_components:
                if comp.name.lower() in description.lower():
                    service_type = comp.name
                    rate_key = comp.rate_key
                    break

            return InvoiceLineItem(
                description=description,
                quantity=quantity,
                rate=rate,
                amount=amount if amount else quantity * rate,
                service_type=service_type,
                rate_key=rate_key
            )

        return None

    def _extract_line_items_from_text(self, text: str) -> List[InvoiceLineItem]:
        """Extract line items from raw text."""
        line_items = []

        # Pattern for line items with quantity, rate, amount
        pattern = r"(.+?)\s+(\d+)\s+\$?([\d,]+\.?\d*)\s+\$?([\d,]+\.?\d*)"

        for match in re.finditer(pattern, text):
            description = match.group(1).strip()
            quantity = int(match.group(2))
            rate = float(match.group(3).replace(",", ""))
            amount = float(match.group(4).replace(",", ""))

            # Identify service type
            service_type = None
            rate_key = None

            for comp in self.vendor.billing_components:
                if comp.name.lower() in description.lower():
                    service_type = comp.name
                    rate_key = comp.rate_key
                    break

            line_items.append(InvoiceLineItem(
                description=description,
                quantity=quantity,
                rate=rate,
                amount=amount,
                service_type=service_type,
                rate_key=rate_key
            ))

        return line_items


class VendorReconciler:
    """Reconciles invoices against tracking data using vendor rules."""

    def __init__(self, vendor_config: VendorConfig):
        """Initialize the reconciler.

        Args:
            vendor_config: Vendor configuration with pricing rules
        """
        self.vendor = vendor_config
        self.parser = VendorAwareInvoiceParser(vendor_config)
        self.estimation_engine = EstimationEngine(vendor_config)

    def reconcile(
        self,
        invoice_path: str,
        tracking_data: pd.DataFrame,
        service_month: str = None
    ) -> ReconciliationResult:
        """Reconcile an invoice against tracking data.

        Args:
            invoice_path: Path to invoice PDF
            tracking_data: DataFrame from pickup tracking sheet
            service_month: Override service month (optional)

        Returns:
            ReconciliationResult with complete analysis
        """
        # Parse invoice
        invoice = self.parser.parse_invoice(invoice_path)

        if service_month:
            invoice.service_month = service_month

        # Generate estimation for the same period
        estimation = self.estimation_engine.estimate_monthly_charges(
            tracking_data,
            invoice.service_month
        )

        # Compare invoice to estimation
        result = self._compare_invoice_to_estimation(invoice, estimation)

        return result

    def reconcile_with_estimation(
        self,
        invoice_path: str,
        estimation: EstimationResult
    ) -> ReconciliationResult:
        """Reconcile an invoice against a pre-computed estimation.

        Args:
            invoice_path: Path to invoice PDF
            estimation: Pre-computed EstimationResult

        Returns:
            ReconciliationResult with complete analysis
        """
        # Parse invoice
        invoice = self.parser.parse_invoice(invoice_path)

        # Compare invoice to estimation
        result = self._compare_invoice_to_estimation(invoice, estimation)

        return result

    def _compare_invoice_to_estimation(
        self,
        invoice: ParsedInvoice,
        estimation: EstimationResult
    ) -> ReconciliationResult:
        """Compare parsed invoice to estimation."""
        # Calculate variance
        variance = invoice.total_amount - estimation.total_estimated

        # Determine status
        if abs(variance) < 0.01:
            status = "PERFECT_MATCH"
        elif abs(variance) < estimation.total_estimated * 0.01:  # Within 1%
            status = "MINOR_VARIANCE"
        else:
            status = "DISCREPANCY"

        result = ReconciliationResult(
            vendor_name=self.vendor.name,
            invoice_number=invoice.invoice_number,
            invoice_date=invoice.invoice_date,
            service_month=invoice.service_month,
            invoice_total=invoice.total_amount,
            expected_total=estimation.total_estimated,
            variance=variance,
            status=status
        )

        # Build comparison table
        result.comparison_table = self._build_comparison_table(invoice, estimation)

        # Build location analysis
        result.location_analysis = self._build_location_analysis(invoice, estimation)

        # Find discrepancies
        result.discrepancies = self._find_discrepancies(invoice, estimation)

        # Validate rates
        result.rate_validations = self._validate_rates(invoice)

        # Validate fuel surcharge
        result.fuel_surcharge_expected = estimation.fuel_surcharge
        result.fuel_surcharge_actual = invoice.fuel_surcharge
        result.fuel_surcharge_variance = invoice.fuel_surcharge - estimation.fuel_surcharge

        return result

    def _build_comparison_table(
        self,
        invoice: ParsedInvoice,
        estimation: EstimationResult
    ) -> List[Dict]:
        """Build line-by-line comparison table."""
        comparison = []

        # Map estimation charges to comparison rows
        estimation_by_key = {}
        for charge in estimation.charge_breakdown:
            estimation_by_key[charge.rate_key] = {
                "description": charge.description,
                "quantity": charge.quantity,
                "rate": charge.rate,
                "amount": charge.amount
            }

        # Map invoice items to comparison rows
        invoice_by_key = {}
        for item in invoice.line_items:
            key = item.rate_key or item.service_type or item.description
            if key not in invoice_by_key:
                invoice_by_key[key] = {
                    "description": item.description,
                    "quantity": item.quantity,
                    "rate": item.rate,
                    "amount": item.amount
                }
            else:
                # Aggregate
                invoice_by_key[key]["quantity"] += item.quantity
                invoice_by_key[key]["amount"] += item.amount

        # Build comparison rows
        all_keys = set(estimation_by_key.keys()) | set(invoice_by_key.keys())

        for key in all_keys:
            est = estimation_by_key.get(key, {})
            inv = invoice_by_key.get(key, {})

            est_qty = est.get("quantity", 0)
            inv_qty = inv.get("quantity", 0)
            est_amt = est.get("amount", 0)
            inv_amt = inv.get("amount", 0)

            variance = inv_amt - est_amt
            match = "✓" if abs(variance) < 0.01 else "✗"

            comparison.append({
                "description": est.get("description") or inv.get("description") or key,
                "estimated_qty": est_qty,
                "invoiced_qty": inv_qty,
                "qty_variance": inv_qty - est_qty,
                "estimated_amount": est_amt,
                "invoiced_amount": inv_amt,
                "amount_variance": variance,
                "status": match
            })

        return comparison

    def _build_location_analysis(
        self,
        invoice: ParsedInvoice,
        estimation: EstimationResult
    ) -> List[Dict]:
        """Build location-level analysis."""
        analysis = []

        for location, estimate in estimation.location_estimates.items():
            # Find matching invoice items for this location
            inv_qty = 0
            for item in invoice.line_items:
                if item.location and self.vendor.match_location(item.location, location):
                    inv_qty += item.quantity

            est_qty = estimate.pickup_count + estimate.delivery_count
            variance = inv_qty - est_qty if inv_qty > 0 else 0

            status = "✓ MATCH" if variance == 0 else f"✗ VARIANCE ({variance:+d})"

            analysis.append({
                "location": location,
                "estimated_qty": est_qty,
                "invoiced_qty": inv_qty if inv_qty > 0 else est_qty,  # Assume match if can't find
                "variance": variance,
                "status": status
            })

        return analysis

    def _find_discrepancies(
        self,
        invoice: ParsedInvoice,
        estimation: EstimationResult
    ) -> List[ReconciliationDiscrepancy]:
        """Find and categorize discrepancies."""
        discrepancies = []

        # Check total variance
        total_variance = invoice.total_amount - estimation.total_estimated
        if abs(total_variance) > 0.01:
            discrepancies.append(ReconciliationDiscrepancy(
                discrepancy_type="amount_mismatch",
                location=None,
                service_type="Total",
                expected_value=estimation.total_estimated,
                actual_value=invoice.total_amount,
                impact=total_variance,
                recommended_action="Review line items for discrepancy source"
            ))

        # Check fuel surcharge
        fuel_variance = invoice.fuel_surcharge - estimation.fuel_surcharge
        if abs(fuel_variance) > 0.01:
            discrepancies.append(ReconciliationDiscrepancy(
                discrepancy_type="amount_mismatch",
                location=None,
                service_type="Fuel Surcharge",
                expected_value=estimation.fuel_surcharge,
                actual_value=invoice.fuel_surcharge,
                impact=fuel_variance,
                recommended_action="Verify fuel surcharge calculation and current fuel price"
            ))

        # Check rates
        for item in invoice.line_items:
            if item.rate_key and item.rate_key in self.vendor.rates:
                expected_rate = self.vendor.rates[item.rate_key]
                if abs(item.rate - expected_rate) > 0.01:
                    discrepancies.append(ReconciliationDiscrepancy(
                        discrepancy_type="rate_mismatch",
                        location=None,
                        service_type=item.description,
                        expected_value=expected_rate,
                        actual_value=item.rate,
                        impact=(item.rate - expected_rate) * item.quantity,
                        recommended_action="Verify rate with vendor contract"
                    ))

        return discrepancies

    def _validate_rates(self, invoice: ParsedInvoice) -> List[Dict]:
        """Validate invoice rates against vendor configuration."""
        validations = []

        for item in invoice.line_items:
            if item.rate_key and item.rate_key in self.vendor.rates:
                expected_rate = self.vendor.rates[item.rate_key]
                is_valid = abs(item.rate - expected_rate) < 0.01

                validations.append({
                    "service": item.description,
                    "rate_key": item.rate_key,
                    "expected_rate": expected_rate,
                    "actual_rate": item.rate,
                    "valid": "✓" if is_valid else "✗",
                    "variance": item.rate - expected_rate
                })

        return validations


def reconcile_with_vendor_rules(
    invoice_path: str,
    tracking_data: pd.DataFrame,
    vendor_config: VendorConfig,
    service_month: str = None
) -> ReconciliationResult:
    """Convenience function to reconcile invoice with vendor rules.

    Args:
        invoice_path: Path to invoice PDF
        tracking_data: DataFrame from pickup tracking sheet
        vendor_config: Vendor configuration
        service_month: Override service month (optional)

    Returns:
        ReconciliationResult with complete analysis
    """
    reconciler = VendorReconciler(vendor_config)
    return reconciler.reconcile(invoice_path, tracking_data, service_month)


def compare_estimate_to_invoice(
    estimation: EstimationResult,
    invoice_path: str,
    vendor_config: VendorConfig
) -> ReconciliationResult:
    """Compare a pre-computed estimate to an invoice.

    Args:
        estimation: Pre-computed EstimationResult
        invoice_path: Path to invoice PDF
        vendor_config: Vendor configuration

    Returns:
        ReconciliationResult with comparison
    """
    reconciler = VendorReconciler(vendor_config)
    return reconciler.reconcile_with_estimation(invoice_path, estimation)


def format_reconciliation_report(result: ReconciliationResult) -> str:
    """Format reconciliation result as a text report.

    Args:
        result: ReconciliationResult to format

    Returns:
        Formatted text report
    """
    lines = []
    sep = "=" * 65

    lines.append(sep)
    lines.append("INVOICE RECONCILIATION REPORT")
    lines.append(sep)
    lines.append(f"Vendor: {result.vendor_name}")
    lines.append(f"Invoice #: {result.invoice_number}")
    lines.append(f"Invoice Date: {result.invoice_date.strftime('%B %d, %Y')}")
    lines.append(f"Service Month: {result.service_month}")

    lines.append("")
    lines.append("-" * 65)
    lines.append("ESTIMATE vs INVOICE COMPARISON")
    lines.append("-" * 65)

    # Header
    lines.append(f"{'':40} {'Estimated':>10} {'Invoiced':>10} {'Variance':>10}")
    lines.append("-" * 65)

    for row in result.comparison_table:
        desc = row["description"][:38] + ".." if len(row["description"]) > 40 else row["description"]
        qty_info = f"({row['estimated_qty']})" if row['estimated_qty'] else ""
        status = row["status"]

        lines.append(
            f"{desc} {qty_info:<5} ${row['estimated_amount']:>8,.2f} "
            f"${row['invoiced_amount']:>8,.2f} ${row['amount_variance']:>8,.2f} {status}"
        )

    lines.append("-" * 65)
    variance_status = "✓" if abs(result.variance) < 0.01 else "✗"
    lines.append(
        f"{'TOTALS':40} ${result.expected_total:>10,.2f} "
        f"${result.invoice_total:>10,.2f} ${result.variance:>10,.2f} {variance_status}"
    )

    # Result
    lines.append("")
    if result.status == "PERFECT_MATCH":
        lines.append("RESULT: PERFECT MATCH - No discrepancies found")
    elif result.status == "MINOR_VARIANCE":
        lines.append(f"RESULT: MINOR VARIANCE - ${abs(result.variance):.2f} difference")
    else:
        lines.append(f"RESULT: DISCREPANCY - ${abs(result.variance):.2f} difference requires review")

    # Location analysis
    lines.append("")
    lines.append("-" * 65)
    lines.append("LOCATION-LEVEL ANALYSIS")
    lines.append("-" * 65)
    lines.append(f"{'Location':<35} {'Est Qty':>8} {'Inv Qty':>8} {'Variance':>8} {'Status':>10}")

    for loc in result.location_analysis:
        loc_name = loc["location"][:33] + ".." if len(loc["location"]) > 35 else loc["location"]
        lines.append(
            f"{loc_name:<35} {loc['estimated_qty']:>8} {loc['invoiced_qty']:>8} "
            f"{loc['variance']:>8} {loc['status']:>10}"
        )

    # Discrepancies
    if result.discrepancies:
        lines.append("")
        lines.append("-" * 65)
        lines.append("DISCREPANCIES REQUIRING REVIEW")
        lines.append("-" * 65)

        for disc in result.discrepancies:
            lines.append(f"")
            lines.append(f"Type: {disc.discrepancy_type}")
            if disc.location:
                lines.append(f"Location: {disc.location}")
            lines.append(f"Service: {disc.service_type}")
            lines.append(f"Expected: {disc.expected_value}")
            lines.append(f"Actual: {disc.actual_value}")
            lines.append(f"Impact: ${disc.impact:,.2f}")
            lines.append(f"Action: {disc.recommended_action}")

    # Rate validations
    if result.rate_validations:
        lines.append("")
        lines.append("-" * 65)
        lines.append("RATE VALIDATION")
        lines.append("-" * 65)

        for val in result.rate_validations:
            lines.append(
                f"{val['service']}: Expected ${val['expected_rate']:.2f}, "
                f"Actual ${val['actual_rate']:.2f} {val['valid']}"
            )

    lines.append(sep)

    return "\n".join(lines)


def export_reconciliation_to_excel(result: ReconciliationResult, output_path: str):
    """Export reconciliation result to Excel.

    Args:
        result: ReconciliationResult to export
        output_path: Path to output Excel file
    """
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        # Summary sheet
        summary_data = {
            "Field": [
                "Vendor", "Invoice #", "Invoice Date", "Service Month",
                "Invoice Total", "Expected Total", "Variance", "Status"
            ],
            "Value": [
                result.vendor_name, result.invoice_number,
                result.invoice_date.strftime("%Y-%m-%d"), result.service_month,
                f"${result.invoice_total:,.2f}",
                f"${result.expected_total:,.2f}",
                f"${result.variance:,.2f}",
                result.status
            ]
        }
        pd.DataFrame(summary_data).to_excel(writer, sheet_name="Summary", index=False)

        # Comparison table sheet
        pd.DataFrame(result.comparison_table).to_excel(
            writer, sheet_name="Line Comparison", index=False
        )

        # Location analysis sheet
        pd.DataFrame(result.location_analysis).to_excel(
            writer, sheet_name="Location Analysis", index=False
        )

        # Discrepancies sheet
        if result.discrepancies:
            disc_data = []
            for disc in result.discrepancies:
                disc_data.append({
                    "Type": disc.discrepancy_type,
                    "Location": disc.location or "",
                    "Service": disc.service_type,
                    "Expected": disc.expected_value,
                    "Actual": disc.actual_value,
                    "Impact": f"${disc.impact:,.2f}",
                    "Recommended Action": disc.recommended_action
                })
            pd.DataFrame(disc_data).to_excel(writer, sheet_name="Discrepancies", index=False)

        # Rate validations sheet
        if result.rate_validations:
            pd.DataFrame(result.rate_validations).to_excel(
                writer, sheet_name="Rate Validation", index=False
            )
