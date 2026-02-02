"""PDF parser for armored vendor invoices."""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import pdfplumber

from .models import Invoice, InvoiceLineItem


class InvoiceParseError(Exception):
    """Raised when an invoice cannot be parsed."""
    pass


class PDFInvoiceParser:
    """
    Parser for armored vendor invoice PDFs.

    Supports common armored carrier formats with configurable patterns.
    Default patterns work with standard invoice formats from major carriers.
    """

    # Common patterns for armored vendor invoices
    DEFAULT_PATTERNS = {
        "invoice_number": [
            r"Invoice\s*#?\s*:?\s*(\w+[-/]?\w+)",
            r"Invoice\s+Number\s*:?\s*(\w+[-/]?\w+)",
            r"Inv\s*#\s*:?\s*(\w+[-/]?\w+)",
        ],
        "invoice_date": [
            r"Invoice\s+Date\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
            r"Date\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
            r"Billed\s+On\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        ],
        "total_amount": [
            r"Total\s+(?:Amount\s+)?(?:Due\s*)?:?\s*\$?([\d,]+\.?\d*)",
            r"Amount\s+Due\s*:?\s*\$?([\d,]+\.?\d*)",
            r"Grand\s+Total\s*:?\s*\$?([\d,]+\.?\d*)",
        ],
        "location_id": [
            r"(?:Store|Location|Site)\s*#?\s*:?\s*(\w+)",
            r"(?:Store|Location|Site)\s+(?:ID|Number)\s*:?\s*(\w+)",
            r"Loc\s*#?\s*:?\s*(\w+)",
        ],
    }

    DATE_FORMATS = [
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%m/%d/%y",
        "%m-%d-%y",
        "%Y-%m-%d",
        "%d/%m/%Y",
    ]

    def __init__(
        self,
        vendor_name: Optional[str] = None,
        custom_patterns: Optional[dict] = None,
        location_id_column: Optional[str] = None,
    ):
        """
        Initialize the PDF parser.

        Args:
            vendor_name: Name of the armored vendor (auto-detected if not provided)
            custom_patterns: Override default regex patterns
            location_id_column: Column name/header for location ID in tables
        """
        self.vendor_name = vendor_name
        self.patterns = {**self.DEFAULT_PATTERNS, **(custom_patterns or {})}
        self.location_id_column = location_id_column

    def parse(self, pdf_path: str | Path) -> Invoice:
        """
        Parse an invoice PDF and extract structured data.

        Args:
            pdf_path: Path to the PDF file

        Returns:
            Invoice object with extracted data

        Raises:
            InvoiceParseError: If the PDF cannot be parsed
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise InvoiceParseError(f"PDF file not found: {pdf_path}")

        try:
            with pdfplumber.open(pdf_path) as pdf:
                full_text = ""
                tables = []

                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    full_text += page_text + "\n"

                    page_tables = page.extract_tables()
                    if page_tables:
                        tables.extend(page_tables)

                # Extract header information
                invoice_number = self._extract_pattern(full_text, "invoice_number")
                invoice_date = self._extract_date(full_text)
                total_amount = self._extract_amount(full_text, "total_amount")
                vendor = self.vendor_name or self._detect_vendor(full_text)

                if not invoice_number:
                    invoice_number = f"UNKNOWN-{pdf_path.stem}"

                if not invoice_date:
                    raise InvoiceParseError(f"Could not extract invoice date from {pdf_path}")

                # Extract line items from tables
                line_items = self._extract_line_items(tables, full_text)

                return Invoice(
                    invoice_number=invoice_number,
                    vendor_name=vendor,
                    invoice_date=invoice_date,
                    total_amount=total_amount or Decimal("0"),
                    line_items=line_items,
                    source_file=str(pdf_path),
                )

        except Exception as e:
            if isinstance(e, InvoiceParseError):
                raise
            raise InvoiceParseError(f"Failed to parse PDF {pdf_path}: {e}") from e

    def _extract_pattern(self, text: str, pattern_key: str) -> Optional[str]:
        """Extract value using configured patterns."""
        patterns = self.patterns.get(pattern_key, [])
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_date(self, text: str) -> Optional[datetime]:
        """Extract and parse invoice date."""
        date_str = self._extract_pattern(text, "invoice_date")
        if not date_str:
            return None

        for fmt in self.DATE_FORMATS:
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        return None

    def _extract_amount(self, text: str, pattern_key: str) -> Optional[Decimal]:
        """Extract monetary amount."""
        amount_str = self._extract_pattern(text, pattern_key)
        if not amount_str:
            return None

        try:
            cleaned = amount_str.replace(",", "").replace("$", "")
            return Decimal(cleaned)
        except InvalidOperation:
            return None

    def _detect_vendor(self, text: str) -> str:
        """Auto-detect vendor from invoice text."""
        vendors = {
            "Loomis": ["loomis"],
            "Brinks": ["brink's", "brinks"],
            "Garda": ["garda"],
            "Dunbar": ["dunbar"],
            "Rochester": ["rochester armored"],
        }

        text_lower = text.lower()
        for vendor, keywords in vendors.items():
            if any(kw in text_lower for kw in keywords):
                return vendor
        return "Unknown Vendor"

    def _extract_line_items(
        self, tables: list, full_text: str
    ) -> list[InvoiceLineItem]:
        """Extract line items from tables."""
        line_items = []

        for table in tables:
            if not table or len(table) < 2:
                continue

            # Try to identify header row
            header_row = self._find_header_row(table)
            if header_row is None:
                continue

            headers = [str(h).lower().strip() if h else "" for h in table[header_row]]

            # Find relevant columns
            col_indices = self._identify_columns(headers)
            if not col_indices.get("location"):
                continue

            # Process data rows
            for row in table[header_row + 1:]:
                if not row or all(cell is None or str(cell).strip() == "" for cell in row):
                    continue

                item = self._parse_line_item(row, col_indices, headers)
                if item:
                    line_items.append(item)

        return line_items

    def _find_header_row(self, table: list) -> Optional[int]:
        """Find the header row in a table."""
        header_keywords = [
            "location", "store", "site", "date", "service",
            "amount", "charge", "description", "type"
        ]

        for i, row in enumerate(table[:5]):  # Check first 5 rows
            if not row:
                continue
            row_text = " ".join(str(cell).lower() for cell in row if cell)
            matches = sum(1 for kw in header_keywords if kw in row_text)
            if matches >= 2:
                return i
        return None

    def _identify_columns(self, headers: list[str]) -> dict[str, int]:
        """Identify column indices based on headers."""
        col_map = {}

        patterns = {
            "location": ["location", "store", "site", "loc #", "store #"],
            "date": ["date", "service date", "pickup date"],
            "amount": ["amount", "charge", "total", "fee"],
            "type": ["type", "service", "description"],
            "name": ["name", "location name", "store name"],
        }

        for col_name, keywords in patterns.items():
            for i, header in enumerate(headers):
                if any(kw in header for kw in keywords):
                    col_map[col_name] = i
                    break

        return col_map

    def _parse_line_item(
        self, row: list, col_indices: dict, headers: list
    ) -> Optional[InvoiceLineItem]:
        """Parse a single line item from a table row."""
        try:
            # Get location ID (required)
            loc_idx = col_indices.get("location")
            if loc_idx is None or loc_idx >= len(row):
                return None
            location_id = str(row[loc_idx]).strip() if row[loc_idx] else None
            if not location_id:
                return None

            # Get service date
            date_idx = col_indices.get("date")
            service_date = None
            if date_idx is not None and date_idx < len(row) and row[date_idx]:
                date_str = str(row[date_idx]).strip()
                for fmt in self.DATE_FORMATS:
                    try:
                        service_date = datetime.strptime(date_str, fmt).date()
                        break
                    except ValueError:
                        continue

            if not service_date:
                return None

            # Get amount
            amount = Decimal("0")
            amt_idx = col_indices.get("amount")
            if amt_idx is not None and amt_idx < len(row) and row[amt_idx]:
                try:
                    amt_str = str(row[amt_idx]).replace(",", "").replace("$", "").strip()
                    amount = Decimal(amt_str)
                except (InvalidOperation, ValueError):
                    pass

            # Get service type
            service_type = "Pickup"
            type_idx = col_indices.get("type")
            if type_idx is not None and type_idx < len(row) and row[type_idx]:
                service_type = str(row[type_idx]).strip()

            # Get location name
            location_name = None
            name_idx = col_indices.get("name")
            if name_idx is not None and name_idx < len(row) and row[name_idx]:
                location_name = str(row[name_idx]).strip()

            return InvoiceLineItem(
                location_id=location_id,
                location_name=location_name,
                service_date=service_date,
                service_type=service_type,
                amount=amount,
            )

        except Exception:
            return None


def parse_invoice_directory(
    directory: str | Path,
    vendor_name: Optional[str] = None,
) -> list[Invoice]:
    """
    Parse all PDF invoices in a directory.

    Args:
        directory: Path to directory containing PDF files
        vendor_name: Optional vendor name to use for all invoices

    Returns:
        List of parsed Invoice objects
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError(f"Not a directory: {directory}")

    parser = PDFInvoiceParser(vendor_name=vendor_name)
    invoices = []
    errors = []

    for pdf_file in directory.glob("*.pdf"):
        try:
            invoice = parser.parse(pdf_file)
            invoices.append(invoice)
        except InvoiceParseError as e:
            errors.append(str(e))

    if errors:
        print(f"Warning: {len(errors)} PDF(s) could not be parsed:")
        for error in errors[:5]:
            print(f"  - {error}")
        if len(errors) > 5:
            print(f"  ... and {len(errors) - 5} more")

    return invoices
