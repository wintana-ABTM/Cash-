"""
Invoice Reconciliation Tool for Armored Transport Vendors
Supports: Sectran, Loomis, Cashman

Run with: python -m streamlit run reconcile_tool.py
"""

import os
import sys
import re
import tempfile
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict
import calendar

# Install required packages if missing
try:
    import pandas as pd
except ImportError:
    os.system(f"{sys.executable} -m pip install pandas openpyxl")
    import pandas as pd

try:
    import pdfplumber
except ImportError:
    os.system(f"{sys.executable} -m pip install pdfplumber")
    import pdfplumber

try:
    from rapidfuzz import fuzz, process
except ImportError:
    os.system(f"{sys.executable} -m pip install rapidfuzz")
    from rapidfuzz import fuzz, process

try:
    import streamlit as st
except ImportError:
    os.system(f"{sys.executable} -m pip install streamlit")
    import streamlit as st


# ============================================================================
# VENDOR RATE CONFIGURATIONS
# ============================================================================

VENDOR_RATES = {
    "Sectran": {
        "base_rate_per_stop": 42.00,  # Update with actual rate
        "fuel_surcharge_pct": 0.12,
        "insurance_surcharge_pct": 0.0695,
    },
    "Loomis": {
        "base_rate_per_pickup": 35.00,  # Update with actual rate
        "fuel_fee_pct": 0.125,
        "insurance_fee_pct": 0.09,
    },
    "Cashman": {
        "smartsafe_pickup_rate": 46.57,
        "vault_management_rate": 9.22,
        "branch_delivery_rate": 25.70,
        "fuel_surcharge_pct": 0.14,  # Variable - update as needed
    }
}


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class TrackingRecord:
    """A single pickup record from tracking data."""
    pickup_date: date
    machine_id: str
    location_id: str
    location_name: str
    vendor: str
    armored_transport_branch: str
    anticipated_amount: float
    actual_deposit: float
    overage_shortage: float
    status: str


@dataclass
class InvoiceLineItem:
    """A line item from a vendor invoice."""
    location_id: Optional[str]
    location_name: str
    description: str
    quantity: int
    rate: float
    amount: float
    period: Optional[str] = None


@dataclass
class ParsedInvoice:
    """A fully parsed vendor invoice."""
    vendor: str
    invoice_number: str
    invoice_date: Optional[date]
    service_period: str
    account_number: Optional[str]
    line_items: List[InvoiceLineItem]
    subtotal: float
    fuel_surcharge: float
    insurance_surcharge: float
    total: float
    raw_text: str
    stop_count: Optional[int] = None  # For Sectran


@dataclass
class ReconciliationResult:
    """Result of reconciling one location."""
    location_id: Optional[str]
    location_name: str
    invoice_pickups: int
    tracking_pickups: int
    difference: int
    invoice_amount: float
    tracking_dates: List[date]
    status: str  # MATCH, OVER, UNDER, MISSING, EXTRA
    notes: str = ""


@dataclass
class ReconciliationReport:
    """Complete reconciliation report."""
    vendor: str
    invoice_number: str
    service_period: str
    results: List[ReconciliationResult]
    total_invoice_pickups: int
    total_tracking_pickups: int
    total_difference: int
    invoice_total: float
    estimated_total: float
    variance: float


# ============================================================================
# TRACKING DATA LOADER
# ============================================================================

class TrackingDataLoader:
    """Loads and processes tracking CSV data."""

    # Column name mappings (lowercase -> standard name)
    COLUMN_MAPPINGS = {
        # Date columns
        "expected pickup date": "pickup_date",
        "pickup date": "pickup_date",
        "date": "pickup_date",
        # Machine ID
        "machine id": "machine_id",
        "machineid": "machine_id",
        "machine": "machine_id",
        # Location ID
        "location id": "location_id",
        "locationid": "location_id",
        "loc id": "location_id",
        # Location name
        "location": "location_name",
        "location name": "location_name",
        "store": "location_name",
        # Vendor (pickup contact)
        "vendor": "vendor",
        # Armored transport branch (billing vendor)
        "armored transport branch": "armored_transport_branch",
        "transport branch": "armored_transport_branch",
        "armored branch": "armored_transport_branch",
        "billing vendor": "armored_transport_branch",
        # Amounts
        "anticipated deposit amt": "anticipated_amount",
        "anticipated deposit": "anticipated_amount",
        "anticipated amount": "anticipated_amount",
        "expected amount": "anticipated_amount",
        "actual deposit": "actual_deposit",
        "actual": "actual_deposit",
        "deposit": "actual_deposit",
        "overage/shortage": "overage_shortage",
        "overage shortage": "overage_shortage",
        "variance": "overage_shortage",
        # Status
        "status": "status",
    }

    def __init__(self):
        self.data: Optional[pd.DataFrame] = None
        self.column_map: Dict[str, str] = {}

    def load_csv(self, file_path_or_buffer) -> pd.DataFrame:
        """Load tracking data from CSV file."""
        df = pd.read_csv(file_path_or_buffer)
        return self._process_dataframe(df)

    def load_excel(self, file_path_or_buffer, sheet_name=None) -> pd.DataFrame:
        """Load tracking data from Excel file."""
        if sheet_name is None:
            # Try to find the right sheet
            excel = pd.ExcelFile(file_path_or_buffer)
            for s in excel.sheet_names:
                s_lower = s.lower()
                if 'pickup' in s_lower or 'recon' in s_lower or 'tracking' in s_lower:
                    sheet_name = s
                    break
            if sheet_name is None:
                sheet_name = 0

        df = pd.read_excel(file_path_or_buffer, sheet_name=sheet_name)
        return self._process_dataframe(df)

    def _process_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Process and standardize the dataframe."""
        # Build column mapping
        self.column_map = {}
        for col in df.columns:
            col_lower = str(col).lower().strip()
            for pattern, standard_name in self.COLUMN_MAPPINGS.items():
                if pattern in col_lower or col_lower in pattern:
                    self.column_map[col] = standard_name
                    break

        # Rename columns
        df = df.rename(columns=self.column_map)

        # Parse date column
        if 'pickup_date' in df.columns:
            df['pickup_date'] = pd.to_datetime(df['pickup_date'], errors='coerce')

        # Parse currency columns
        for col in ['anticipated_amount', 'actual_deposit', 'overage_shortage']:
            if col in df.columns:
                df[col] = df[col].apply(self._parse_currency)

        # Clean string columns
        for col in ['location_id', 'location_name', 'armored_transport_branch', 'status']:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()

        self.data = df
        return df

    def _parse_currency(self, value) -> float:
        """Parse currency string to float."""
        if pd.isna(value):
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        # Remove $, commas, parentheses (for negatives)
        value = str(value).replace('$', '').replace(',', '').strip()
        if value.startswith('(') and value.endswith(')'):
            value = '-' + value[1:-1]
        try:
            return float(value)
        except:
            return 0.0

    def get_records_for_vendor_month(
        self,
        vendor: str,
        year: int,
        month: int
    ) -> pd.DataFrame:
        """Get tracking records for a specific vendor and month."""
        if self.data is None:
            return pd.DataFrame()

        df = self.data.copy()

        # Filter by vendor (armored transport branch)
        vendor_lower = vendor.lower()
        df = df[df['armored_transport_branch'].str.lower().str.contains(vendor_lower, na=False)]

        # Filter by month
        if 'pickup_date' in df.columns:
            df = df[
                (df['pickup_date'].dt.year == year) &
                (df['pickup_date'].dt.month == month)
            ]

        return df

    def get_pickups_by_location(
        self,
        vendor: str,
        year: int,
        month: int
    ) -> Dict[str, Dict]:
        """Get pickup counts grouped by location for a vendor/month."""
        df = self.get_records_for_vendor_month(vendor, year, month)

        result = {}
        if 'location_id' in df.columns:
            for loc_id in df['location_id'].unique():
                loc_df = df[df['location_id'] == loc_id]
                loc_name = loc_df['location_name'].iloc[0] if 'location_name' in loc_df.columns else loc_id

                result[loc_id] = {
                    'location_id': loc_id,
                    'location_name': loc_name,
                    'pickup_count': len(loc_df),
                    'total_anticipated': loc_df['anticipated_amount'].sum() if 'anticipated_amount' in loc_df.columns else 0,
                    'total_actual': loc_df['actual_deposit'].sum() if 'actual_deposit' in loc_df.columns else 0,
                    'pickup_dates': loc_df['pickup_date'].dt.date.tolist() if 'pickup_date' in loc_df.columns else []
                }
        elif 'location_name' in df.columns:
            for loc_name in df['location_name'].unique():
                loc_df = df[df['location_name'] == loc_name]

                result[loc_name] = {
                    'location_id': None,
                    'location_name': loc_name,
                    'pickup_count': len(loc_df),
                    'total_anticipated': loc_df['anticipated_amount'].sum() if 'anticipated_amount' in loc_df.columns else 0,
                    'total_actual': loc_df['actual_deposit'].sum() if 'actual_deposit' in loc_df.columns else 0,
                    'pickup_dates': loc_df['pickup_date'].dt.date.tolist() if 'pickup_date' in loc_df.columns else []
                }

        return result


# ============================================================================
# INVOICE PARSERS
# ============================================================================

class InvoiceParser:
    """Base class for invoice parsers."""

    def detect_vendor(self, text: str) -> Optional[str]:
        """Auto-detect vendor from PDF text."""
        text_upper = text.upper()

        if 'SECTRAN' in text_upper:
            return 'Sectran'
        elif 'LOOMIS' in text_upper:
            return 'Loomis'
        elif 'CASH MAN' in text_upper or 'CASHMAN' in text_upper:
            return 'Cashman'

        return None

    def parse_pdf(self, pdf_path: str) -> ParsedInvoice:
        """Parse a PDF invoice. Auto-detects vendor."""
        with pdfplumber.open(pdf_path) as pdf:
            full_text = ""
            all_tables = []

            for page in pdf.pages:
                text = page.extract_text() or ""
                full_text += text + "\n"

                tables = page.extract_tables()
                if tables:
                    all_tables.extend(tables)

        vendor = self.detect_vendor(full_text)

        if vendor == 'Sectran':
            return self._parse_sectran(full_text, all_tables)
        elif vendor == 'Loomis':
            return self._parse_loomis(full_text, all_tables)
        elif vendor == 'Cashman':
            return self._parse_cashman(full_text, all_tables)
        else:
            # Return generic parsed invoice
            return self._parse_generic(full_text, all_tables)

    def _parse_sectran(self, text: str, tables: List) -> ParsedInvoice:
        """Parse Sectran invoice."""
        # Extract invoice number
        inv_match = re.search(r'Invoice\s*No[.:]?\s*(\d+)', text, re.IGNORECASE)
        invoice_number = inv_match.group(1) if inv_match else "Unknown"

        # Extract invoice date
        date_match = re.search(r'Invoice\s*Date[.:]?\s*(\d{1,2}/\d{1,2}/\d{2,4})', text, re.IGNORECASE)
        invoice_date = None
        if date_match:
            try:
                invoice_date = datetime.strptime(date_match.group(1), "%m/%d/%Y").date()
            except:
                pass

        # Extract service period
        period_match = re.search(r'SERVICE\s+FOR\s+(\w+\s+\d{4})', text, re.IGNORECASE)
        service_period = period_match.group(1) if period_match else "Unknown"

        # Extract account number
        acct_match = re.search(r'Account\s*No[.:]?\s*(\w+)', text, re.IGNORECASE)
        account_number = acct_match.group(1) if acct_match else None

        # Count stops from daily activity log
        # Look for patterns like "1 2 3 4 5..." in stop charges section
        stop_count = 0
        stop_matches = re.findall(r'STOP\s+CHARGES.*?(\d+)', text, re.IGNORECASE | re.DOTALL)
        if stop_matches:
            stop_count = len(stop_matches)
        else:
            # Alternative: count days with activity
            day_pattern = re.findall(r'\b(\d{1,2})\s+\d+\s+\d+', text)
            stop_count = len(set(day_pattern))

        # Extract line items
        line_items = []

        # Look for armored truck service
        truck_match = re.search(r'ARMORED\s+TRUCK\s+SVC.*?(\d+)\s+Day.*?\$?([\d,]+\.?\d*)', text, re.IGNORECASE)
        if truck_match:
            qty = int(truck_match.group(1))
            amount = float(truck_match.group(2).replace(',', ''))
            line_items.append(InvoiceLineItem(
                location_id=None,
                location_name="All Locations",
                description="Armored Truck Service",
                quantity=qty,
                rate=amount / qty if qty > 0 else 0,
                amount=amount
            ))
            stop_count = qty  # Use this as stop count if found

        # Extract totals
        total_match = re.search(r'(?:Invoice\s+)?Total[:\s]*\$?([\d,]+\.?\d*)', text, re.IGNORECASE)
        total = float(total_match.group(1).replace(',', '')) if total_match else 0

        # Fuel surcharge
        fuel_match = re.search(r'Fuel\s+Surcharge.*?\$?([\d,]+\.?\d*)', text, re.IGNORECASE)
        fuel_surcharge = float(fuel_match.group(1).replace(',', '')) if fuel_match else 0

        # Insurance surcharge
        ins_match = re.search(r'Insurance\s+Surcharge.*?\$?([\d,]+\.?\d*)', text, re.IGNORECASE)
        insurance_surcharge = float(ins_match.group(1).replace(',', '')) if ins_match else 0

        subtotal = total - fuel_surcharge - insurance_surcharge

        return ParsedInvoice(
            vendor="Sectran",
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            service_period=service_period,
            account_number=account_number,
            line_items=line_items,
            subtotal=subtotal,
            fuel_surcharge=fuel_surcharge,
            insurance_surcharge=insurance_surcharge,
            total=total,
            raw_text=text,
            stop_count=stop_count
        )

    def _parse_loomis(self, text: str, tables: List) -> ParsedInvoice:
        """Parse Loomis invoice."""
        # Extract invoice number
        inv_match = re.search(r'Invoice\s*(?:Number|#|No)?[.:]?\s*(\d+)', text, re.IGNORECASE)
        invoice_number = inv_match.group(1) if inv_match else "Unknown"

        # Extract invoice date
        date_match = re.search(r'Invoice\s*Date[.:]?\s*(\d{1,2}/\d{1,2}/\d{2,4})', text, re.IGNORECASE)
        invoice_date = None
        if date_match:
            try:
                invoice_date = datetime.strptime(date_match.group(1), "%m/%d/%Y").date()
            except:
                pass

        # Extract service period from line items (MM/YY format)
        period_match = re.search(r'\b(\d{2}/\d{2})\b', text)
        service_period = period_match.group(1) if period_match else "Unknown"

        # Convert MM/YY to readable format
        if service_period != "Unknown":
            try:
                mm, yy = service_period.split('/')
                month_name = calendar.month_name[int(mm)]
                year = 2000 + int(yy) if int(yy) < 50 else 1900 + int(yy)
                service_period = f"{month_name} {year}"
            except:
                pass

        # Parse line items from tables
        line_items = []
        for table in tables:
            if not table or len(table) < 2:
                continue

            for row in table[1:]:  # Skip header
                if not row or len(row) < 4:
                    continue

                # Try to extract Location ID and details
                loc_id = None
                description = ""
                amount = 0

                for cell in row:
                    if cell is None:
                        continue
                    cell_str = str(cell).strip()

                    # Location ID pattern (e.g., AR020005, MN020002)
                    loc_match = re.match(r'^([A-Z]{2}\d{6})$', cell_str)
                    if loc_match:
                        loc_id = loc_match.group(1)
                        continue

                    # Amount pattern
                    amt_match = re.match(r'^\$?([\d,]+\.?\d*)$', cell_str.replace(',', ''))
                    if amt_match and len(cell_str) > 0:
                        try:
                            amount = float(cell_str.replace('$', '').replace(',', ''))
                        except:
                            pass
                        continue

                    # Description
                    if len(cell_str) > 5 and not cell_str.replace('.', '').isdigit():
                        description = cell_str

                if loc_id or description:
                    line_items.append(InvoiceLineItem(
                        location_id=loc_id,
                        location_name=description,
                        description=description,
                        quantity=1,
                        rate=amount,
                        amount=amount
                    ))

        # Also try text-based extraction for Location IDs
        loc_pattern = re.findall(r'([A-Z]{2}\d{6})\s+(?:ATM\s+DEPOSIT\s+PULL|.*?)\s+.*?\$?([\d,]+\.?\d*)', text)
        for loc_id, amount in loc_pattern:
            # Check if already in line items
            if not any(li.location_id == loc_id for li in line_items):
                line_items.append(InvoiceLineItem(
                    location_id=loc_id,
                    location_name=f"Location {loc_id}",
                    description="ATM Deposit Pull",
                    quantity=1,
                    rate=float(amount.replace(',', '')) if amount else 0,
                    amount=float(amount.replace(',', '')) if amount else 0
                ))

        # Extract totals
        total_match = re.search(r'(?:Invoice\s+)?Total[:\s]*\$?([\d,]+\.?\d*)', text, re.IGNORECASE)
        total = float(total_match.group(1).replace(',', '')) if total_match else 0

        # Fuel fee
        fuel_match = re.search(r'FUEL\s+FEE.*?\$?([\d,]+\.?\d*)', text, re.IGNORECASE)
        fuel_surcharge = float(fuel_match.group(1).replace(',', '')) if fuel_match else 0

        # Insurance fee
        ins_match = re.search(r'INSURANCE\s+FEE.*?\$?([\d,]+\.?\d*)', text, re.IGNORECASE)
        insurance_surcharge = float(ins_match.group(1).replace(',', '')) if ins_match else 0

        subtotal = total - fuel_surcharge - insurance_surcharge

        return ParsedInvoice(
            vendor="Loomis",
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            service_period=service_period,
            account_number=None,
            line_items=line_items,
            subtotal=subtotal,
            fuel_surcharge=fuel_surcharge,
            insurance_surcharge=insurance_surcharge,
            total=total,
            raw_text=text
        )

    def _parse_cashman(self, text: str, tables: List) -> ParsedInvoice:
        """Parse Cashman (Cash Man Services) invoice."""
        # Extract invoice number
        inv_match = re.search(r'(?:Invoice|INV)[#:\s]*(\d+)', text, re.IGNORECASE)
        invoice_number = inv_match.group(1) if inv_match else "Unknown"

        # Extract invoice date
        date_match = re.search(r'Date[.:]?\s*(\d{1,2}/\d{1,2}/\d{2,4})', text, re.IGNORECASE)
        invoice_date = None
        if date_match:
            try:
                invoice_date = datetime.strptime(date_match.group(1), "%m/%d/%Y").date()
            except:
                pass

        # Extract service month
        period_match = re.search(r'(?:Service\s+Month|For\s+Services?)[:\s]*(\w+\s+\d{4})', text, re.IGNORECASE)
        service_period = period_match.group(1) if period_match else "Unknown"

        # Parse line items
        line_items = []

        # Pattern for Smartsafe Pickups: "Secure Transportation: Smartsafe Pickups - [Location] QTY RATE AMOUNT"
        smartsafe_pattern = re.findall(
            r'(?:Secure\s+Transportation[:\s]*)?Smartsafe\s+Pickups?\s*[-–]\s*([^0-9]+?)\s+(\d+)\s+\$?([\d.]+)\s+\$?([\d,.]+)',
            text, re.IGNORECASE
        )
        for match in smartsafe_pattern:
            loc_name, qty, rate, amount = match
            line_items.append(InvoiceLineItem(
                location_id=None,
                location_name=loc_name.strip(),
                description="Smartsafe Pickup",
                quantity=int(qty),
                rate=float(rate),
                amount=float(amount.replace(',', ''))
            ))

        # Pattern for Vault Management
        vault_pattern = re.findall(
            r'Vault\s*(?:&|and)?\s*Cash\s+Management\s*[-–]\s*([^0-9]+?)\s+(\d+)\s+\$?([\d.]+)\s+\$?([\d,.]+)',
            text, re.IGNORECASE
        )
        for match in vault_pattern:
            loc_name, qty, rate, amount = match
            line_items.append(InvoiceLineItem(
                location_id=None,
                location_name=loc_name.strip(),
                description="Vault & Cash Management",
                quantity=int(qty),
                rate=float(rate),
                amount=float(amount.replace(',', ''))
            ))

        # Also try parsing from tables
        for table in tables:
            if not table or len(table) < 2:
                continue

            for row in table:
                if not row or len(row) < 3:
                    continue

                row_text = ' '.join(str(c) for c in row if c)

                # Look for QTY RATE AMOUNT pattern
                qty_match = re.search(r'(\d+)\s+\$?([\d.]+)\s+\$?([\d,.]+)', row_text)
                if qty_match:
                    # Extract location from beginning of row
                    loc_match = re.match(r'^(.+?)(?=\d)', row_text)
                    loc_name = loc_match.group(1).strip() if loc_match else "Unknown"

                    qty = int(qty_match.group(1))
                    rate = float(qty_match.group(2))
                    amount = float(qty_match.group(3).replace(',', ''))

                    # Determine service type
                    if 'smartsafe' in row_text.lower() or 'pickup' in row_text.lower():
                        desc = "Smartsafe Pickup"
                    elif 'vault' in row_text.lower():
                        desc = "Vault & Cash Management"
                    elif 'delivery' in row_text.lower():
                        desc = "Branch Delivery"
                    else:
                        desc = "Service"

                    # Avoid duplicates
                    if not any(li.location_name == loc_name and li.description == desc for li in line_items):
                        line_items.append(InvoiceLineItem(
                            location_id=None,
                            location_name=loc_name,
                            description=desc,
                            quantity=qty,
                            rate=rate,
                            amount=amount
                        ))

        # Extract totals
        total_match = re.search(r'(?:Invoice\s+)?Total[:\s]*\$?([\d,]+\.?\d*)', text, re.IGNORECASE)
        total = float(total_match.group(1).replace(',', '')) if total_match else 0

        # Fuel surcharge
        fuel_match = re.search(r'Fuel\s+Surcharge.*?\$?([\d,]+\.?\d*)', text, re.IGNORECASE)
        fuel_surcharge = float(fuel_match.group(1).replace(',', '')) if fuel_match else 0

        subtotal = sum(li.amount for li in line_items)

        return ParsedInvoice(
            vendor="Cashman",
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            service_period=service_period,
            account_number=None,
            line_items=line_items,
            subtotal=subtotal,
            fuel_surcharge=fuel_surcharge,
            insurance_surcharge=0,
            total=total if total > 0 else subtotal + fuel_surcharge,
            raw_text=text
        )

    def _parse_generic(self, text: str, tables: List) -> ParsedInvoice:
        """Parse unknown invoice format."""
        # Extract any invoice number
        inv_match = re.search(r'(?:Invoice|INV)[#:\s]*(\d+)', text, re.IGNORECASE)
        invoice_number = inv_match.group(1) if inv_match else "Unknown"

        # Extract any total
        total_match = re.search(r'(?:Total|Amount\s+Due)[:\s]*\$?([\d,]+\.?\d*)', text, re.IGNORECASE)
        total = float(total_match.group(1).replace(',', '')) if total_match else 0

        return ParsedInvoice(
            vendor="Unknown",
            invoice_number=invoice_number,
            invoice_date=None,
            service_period="Unknown",
            account_number=None,
            line_items=[],
            subtotal=total,
            fuel_surcharge=0,
            insurance_surcharge=0,
            total=total,
            raw_text=text
        )


# ============================================================================
# RECONCILIATION ENGINE
# ============================================================================

class ReconciliationEngine:
    """Matches invoice data against tracking data."""

    def __init__(self, tracking_loader: TrackingDataLoader):
        self.tracking = tracking_loader

    def reconcile(
        self,
        invoice: ParsedInvoice,
        year: int,
        month: int
    ) -> ReconciliationReport:
        """Reconcile an invoice against tracking data."""
        # Get tracking data for this vendor/month
        tracking_by_location = self.tracking.get_pickups_by_location(
            invoice.vendor, year, month
        )

        results = []

        if invoice.vendor == "Sectran":
            # Sectran: Compare total stop count
            results = self._reconcile_sectran(invoice, tracking_by_location)

        elif invoice.vendor == "Loomis":
            # Loomis: Match by Location ID
            results = self._reconcile_loomis(invoice, tracking_by_location)

        elif invoice.vendor == "Cashman":
            # Cashman: Match by location name (fuzzy)
            results = self._reconcile_cashman(invoice, tracking_by_location)

        else:
            # Generic reconciliation
            results = self._reconcile_generic(invoice, tracking_by_location)

        # Calculate totals
        total_invoice_pickups = sum(r.invoice_pickups for r in results)
        total_tracking_pickups = sum(r.tracking_pickups for r in results)
        total_difference = total_invoice_pickups - total_tracking_pickups

        # Estimate what the invoice should be
        estimated_total = self._estimate_invoice_amount(
            invoice.vendor, total_tracking_pickups
        )

        return ReconciliationReport(
            vendor=invoice.vendor,
            invoice_number=invoice.invoice_number,
            service_period=invoice.service_period,
            results=results,
            total_invoice_pickups=total_invoice_pickups,
            total_tracking_pickups=total_tracking_pickups,
            total_difference=total_difference,
            invoice_total=invoice.total,
            estimated_total=estimated_total,
            variance=invoice.total - estimated_total
        )

    def _reconcile_sectran(
        self,
        invoice: ParsedInvoice,
        tracking_by_location: Dict
    ) -> List[ReconciliationResult]:
        """Reconcile Sectran invoice (total stops)."""
        total_tracking = sum(loc['pickup_count'] for loc in tracking_by_location.values())
        invoice_stops = invoice.stop_count or 0

        # For Sectran, we compare total stops
        all_dates = []
        for loc in tracking_by_location.values():
            all_dates.extend(loc.get('pickup_dates', []))

        difference = invoice_stops - total_tracking
        if difference == 0:
            status = "MATCH"
        elif difference > 0:
            status = "OVER"
        else:
            status = "UNDER"

        return [ReconciliationResult(
            location_id=None,
            location_name="All Locations (Sectran)",
            invoice_pickups=invoice_stops,
            tracking_pickups=total_tracking,
            difference=difference,
            invoice_amount=invoice.total,
            tracking_dates=sorted(all_dates),
            status=status,
            notes=f"Sectran invoices by total stop count"
        )]

    def _reconcile_loomis(
        self,
        invoice: ParsedInvoice,
        tracking_by_location: Dict
    ) -> List[ReconciliationResult]:
        """Reconcile Loomis invoice (by Location ID)."""
        results = []

        # Group invoice items by Location ID
        invoice_by_loc = defaultdict(int)
        invoice_amounts = defaultdict(float)
        for item in invoice.line_items:
            if item.location_id:
                invoice_by_loc[item.location_id] += item.quantity
                invoice_amounts[item.location_id] += item.amount

        # All location IDs from both sources
        all_loc_ids = set(invoice_by_loc.keys()) | set(tracking_by_location.keys())

        for loc_id in all_loc_ids:
            invoice_count = invoice_by_loc.get(loc_id, 0)
            tracking_data = tracking_by_location.get(loc_id, {})
            tracking_count = tracking_data.get('pickup_count', 0)
            tracking_dates = tracking_data.get('pickup_dates', [])
            loc_name = tracking_data.get('location_name', loc_id)

            difference = invoice_count - tracking_count

            if invoice_count == 0 and tracking_count > 0:
                status = "MISSING"  # In tracking but not invoiced
                notes = "Pickups in tracking not on invoice"
            elif invoice_count > 0 and tracking_count == 0:
                status = "EXTRA"  # Invoiced but not in tracking
                notes = "Invoiced but no tracking records"
            elif difference == 0:
                status = "MATCH"
                notes = ""
            elif difference > 0:
                status = "OVER"
                notes = f"Invoice has {difference} more than tracking"
            else:
                status = "UNDER"
                notes = f"Invoice has {-difference} fewer than tracking"

            results.append(ReconciliationResult(
                location_id=loc_id,
                location_name=loc_name,
                invoice_pickups=invoice_count,
                tracking_pickups=tracking_count,
                difference=difference,
                invoice_amount=invoice_amounts.get(loc_id, 0),
                tracking_dates=tracking_dates,
                status=status,
                notes=notes
            ))

        return sorted(results, key=lambda x: x.location_id or "")

    def _reconcile_cashman(
        self,
        invoice: ParsedInvoice,
        tracking_by_location: Dict
    ) -> List[ReconciliationResult]:
        """Reconcile Cashman invoice (total stop count comparison)."""
        # Count total pickups from invoice (Smartsafe Pickups only)
        invoice_pickups = 0
        for item in invoice.line_items:
            if item.description == "Smartsafe Pickup":
                invoice_pickups += item.quantity

        # Count total pickups from tracking
        total_tracking = sum(loc['pickup_count'] for loc in tracking_by_location.values())

        # Collect all pickup dates
        all_dates = []
        for loc in tracking_by_location.values():
            all_dates.extend(loc.get('pickup_dates', []))

        difference = invoice_pickups - total_tracking

        if difference == 0:
            status = "MATCH"
        elif difference > 0:
            status = "OVER"
        else:
            status = "UNDER"

        return [ReconciliationResult(
            location_id=None,
            location_name="All Locations (Cashman)",
            invoice_pickups=invoice_pickups,
            tracking_pickups=total_tracking,
            difference=difference,
            invoice_amount=invoice.total,
            tracking_dates=sorted(all_dates),
            status=status,
            notes="Cashman invoices compared by total stop count"
        )]

    def _reconcile_generic(
        self,
        invoice: ParsedInvoice,
        tracking_by_location: Dict
    ) -> List[ReconciliationResult]:
        """Generic reconciliation."""
        total_tracking = sum(loc['pickup_count'] for loc in tracking_by_location.values())

        return [ReconciliationResult(
            location_id=None,
            location_name="All Locations",
            invoice_pickups=len(invoice.line_items),
            tracking_pickups=total_tracking,
            difference=len(invoice.line_items) - total_tracking,
            invoice_amount=invoice.total,
            tracking_dates=[],
            status="UNKNOWN",
            notes="Could not auto-detect vendor format"
        )]

    def _estimate_invoice_amount(self, vendor: str, pickup_count: int) -> float:
        """Estimate invoice amount based on vendor rates."""
        rates = VENDOR_RATES.get(vendor, {})

        if vendor == "Sectran":
            base = pickup_count * rates.get('base_rate_per_stop', 42)
            fuel = base * rates.get('fuel_surcharge_pct', 0.12)
            insurance = base * rates.get('insurance_surcharge_pct', 0.0695)
            return base + fuel + insurance

        elif vendor == "Loomis":
            base = pickup_count * rates.get('base_rate_per_pickup', 35)
            fuel = base * rates.get('fuel_fee_pct', 0.125)
            insurance = base * rates.get('insurance_fee_pct', 0.09)
            return base + fuel + insurance

        elif vendor == "Cashman":
            pickup_charge = pickup_count * rates.get('smartsafe_pickup_rate', 46.57)
            vault_charge = pickup_count * rates.get('vault_management_rate', 9.22)
            base = pickup_charge + vault_charge
            fuel = base * rates.get('fuel_surcharge_pct', 0.14)
            return base + fuel

        return 0


# ============================================================================
# MONTHLY ESTIMATION
# ============================================================================

class MonthlyEstimator:
    """Estimates upcoming bills based on tracking data."""

    def __init__(self, tracking_loader: TrackingDataLoader):
        self.tracking = tracking_loader

    def estimate_month(
        self,
        vendor: str,
        year: int,
        month: int
    ) -> Dict:
        """Estimate charges for a vendor/month."""
        df = self.tracking.get_records_for_vendor_month(vendor, year, month)

        if df.empty:
            return {
                'vendor': vendor,
                'period': f"{calendar.month_name[month]} {year}",
                'pickup_count': 0,
                'locations': [],
                'estimated_base': 0,
                'estimated_fuel': 0,
                'estimated_insurance': 0,
                'estimated_total': 0
            }

        pickup_count = len(df)
        rates = VENDOR_RATES.get(vendor, {})

        # Calculate by location
        locations = []
        if 'location_name' in df.columns:
            for loc_name in df['location_name'].unique():
                loc_df = df[df['location_name'] == loc_name]
                locations.append({
                    'name': loc_name,
                    'pickups': len(loc_df),
                    'total_deposit': loc_df['actual_deposit'].sum() if 'actual_deposit' in loc_df.columns else 0
                })

        # Calculate estimates
        if vendor == "Sectran":
            base = pickup_count * rates.get('base_rate_per_stop', 42)
            fuel = base * rates.get('fuel_surcharge_pct', 0.12)
            insurance = base * rates.get('insurance_surcharge_pct', 0.0695)

        elif vendor == "Loomis":
            base = pickup_count * rates.get('base_rate_per_pickup', 35)
            fuel = base * rates.get('fuel_fee_pct', 0.125)
            insurance = base * rates.get('insurance_fee_pct', 0.09)

        elif vendor == "Cashman":
            pickup_charge = pickup_count * rates.get('smartsafe_pickup_rate', 46.57)
            vault_charge = pickup_count * rates.get('vault_management_rate', 9.22)
            base = pickup_charge + vault_charge
            fuel = base * rates.get('fuel_surcharge_pct', 0.14)
            insurance = 0

        else:
            base = pickup_count * 40  # Default rate
            fuel = base * 0.12
            insurance = base * 0.07

        return {
            'vendor': vendor,
            'period': f"{calendar.month_name[month]} {year}",
            'pickup_count': pickup_count,
            'locations': sorted(locations, key=lambda x: x['pickups'], reverse=True),
            'estimated_base': base,
            'estimated_fuel': fuel,
            'estimated_insurance': insurance,
            'estimated_total': base + fuel + insurance
        }

    def get_historical_summary(self, vendor: str, months: int = 6) -> List[Dict]:
        """Get historical summary for a vendor."""
        results = []
        today = date.today()

        for i in range(months):
            # Go back i months
            target_date = today.replace(day=1)
            for _ in range(i):
                target_date = (target_date - pd.Timedelta(days=1)).replace(day=1)

            estimate = self.estimate_month(vendor, target_date.year, target_date.month)
            results.append(estimate)

        return list(reversed(results))


# ============================================================================
# STREAMLIT APP
# ============================================================================

def run_app():
    """Run the Streamlit application."""
    st.set_page_config(
        page_title="Invoice Reconciliation Tool",
        page_icon="📊",
        layout="wide"
    )

    st.title("📊 Invoice Reconciliation Tool")
    st.markdown("Reconcile armored transport invoices against pickup tracking data")

    # Initialize session state
    if 'tracking_loader' not in st.session_state:
        st.session_state.tracking_loader = TrackingDataLoader()
    if 'tracking_loaded' not in st.session_state:
        st.session_state.tracking_loaded = False

    # Sidebar
    st.sidebar.header("📁 Data Sources")

    # Tracking data upload
    st.sidebar.subheader("1. Upload Tracking Data")
    tracking_file = st.sidebar.file_uploader(
        "Pickup Tracking CSV/Excel",
        type=['csv', 'xlsx', 'xls'],
        help="Upload Cash_Reconcilation_-_Pickup_Recon.csv or similar"
    )

    if tracking_file:
        try:
            if tracking_file.name.endswith('.csv'):
                df = st.session_state.tracking_loader.load_csv(tracking_file)
            else:
                df = st.session_state.tracking_loader.load_excel(tracking_file)

            st.session_state.tracking_loaded = True
            st.sidebar.success(f"✅ Loaded {len(df)} records")

            # Show column mapping
            with st.sidebar.expander("Column Mapping"):
                for orig, mapped in st.session_state.tracking_loader.column_map.items():
                    st.write(f"{orig} → {mapped}")

        except Exception as e:
            st.sidebar.error(f"Error loading tracking data: {e}")

    # Mode selection
    st.sidebar.markdown("---")
    mode = st.sidebar.radio(
        "Mode",
        ["Invoice Reconciliation", "Monthly Estimation", "Data Explorer"]
    )

    # Main content
    if mode == "Invoice Reconciliation":
        render_reconciliation_mode()

    elif mode == "Monthly Estimation":
        render_estimation_mode()

    elif mode == "Data Explorer":
        render_explorer_mode()


def render_reconciliation_mode():
    """Render invoice reconciliation mode."""
    st.header("Invoice Reconciliation")

    if not st.session_state.tracking_loaded:
        st.warning("⚠️ Please upload tracking data first (in sidebar)")
        return

    # Invoice upload
    col1, col2 = st.columns([2, 1])

    with col1:
        invoice_file = st.file_uploader(
            "Upload Invoice PDF",
            type=['pdf'],
            help="Upload a Sectran, Loomis, or Cashman invoice"
        )

    with col2:
        # Month/Year selection
        st.subheader("Service Period")
        month = st.selectbox(
            "Month",
            range(1, 13),
            index=datetime.now().month - 2 if datetime.now().month > 1 else 11,
            format_func=lambda x: calendar.month_name[x]
        )
        year = st.selectbox("Year", range(2024, 2027), index=1)

    if invoice_file:
        # Save to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            tmp.write(invoice_file.read())
            tmp_path = tmp.name

        try:
            # Parse invoice
            parser = InvoiceParser()
            invoice = parser.parse_pdf(tmp_path)

            st.success(f"✅ Detected Vendor: **{invoice.vendor}**")

            # Show parsed invoice info
            col1, col2, col3 = st.columns(3)
            col1.metric("Invoice #", invoice.invoice_number)
            col2.metric("Service Period", invoice.service_period)
            col3.metric("Invoice Total", f"${invoice.total:,.2f}")

            # Show line items
            with st.expander("📋 Invoice Line Items"):
                if invoice.line_items:
                    items_data = []
                    for item in invoice.line_items:
                        items_data.append({
                            "Location ID": item.location_id or "-",
                            "Location": item.location_name,
                            "Description": item.description,
                            "Qty": item.quantity,
                            "Rate": f"${item.rate:.2f}",
                            "Amount": f"${item.amount:.2f}"
                        })
                    st.dataframe(pd.DataFrame(items_data), use_container_width=True)
                else:
                    st.info("No line items extracted (may be summary invoice)")

                if invoice.stop_count:
                    st.write(f"**Total Stops:** {invoice.stop_count}")

            # Show raw text
            with st.expander("📄 Raw PDF Text"):
                st.text(invoice.raw_text[:5000] + "..." if len(invoice.raw_text) > 5000 else invoice.raw_text)

            # Run reconciliation
            st.subheader("🔍 Reconciliation Results")

            engine = ReconciliationEngine(st.session_state.tracking_loader)
            report = engine.reconcile(invoice, year, month)

            # Summary metrics
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Invoice Pickups", report.total_invoice_pickups)
            col2.metric("Tracking Pickups", report.total_tracking_pickups)

            diff_color = "normal" if report.total_difference == 0 else "inverse"
            col3.metric("Difference", report.total_difference,
                       delta=report.total_difference if report.total_difference != 0 else None,
                       delta_color=diff_color)

            variance_color = "normal" if abs(report.variance) < 10 else "inverse"
            col4.metric("$ Variance", f"${report.variance:,.2f}",
                       delta=f"${report.variance:,.2f}" if report.variance != 0 else None,
                       delta_color=variance_color)

            # Results table
            if report.results:
                results_data = []
                for r in report.results:
                    status_emoji = {
                        "MATCH": "✅",
                        "OVER": "⚠️",
                        "UNDER": "⚠️",
                        "MISSING": "❌",
                        "EXTRA": "❓"
                    }.get(r.status, "❔")

                    results_data.append({
                        "Status": f"{status_emoji} {r.status}",
                        "Location ID": r.location_id or "-",
                        "Location": r.location_name,
                        "Invoice": r.invoice_pickups,
                        "Tracking": r.tracking_pickups,
                        "Diff": r.difference,
                        "Inv Amount": f"${r.invoice_amount:,.2f}",
                        "Notes": r.notes
                    })

                results_df = pd.DataFrame(results_data)
                st.dataframe(results_df, use_container_width=True)

                # Summary
                matches = sum(1 for r in report.results if r.status == "MATCH")
                issues = len(report.results) - matches

                if issues == 0:
                    st.success(f"✅ All {matches} locations match!")
                else:
                    st.warning(f"⚠️ {issues} location(s) have discrepancies")

            # Export
            st.download_button(
                "📥 Export Results to CSV",
                results_df.to_csv(index=False),
                f"reconciliation_{invoice.vendor}_{invoice.invoice_number}.csv",
                "text/csv"
            )

        except Exception as e:
            st.error(f"Error processing invoice: {e}")
            import traceback
            st.code(traceback.format_exc())

        finally:
            # Clean up temp file
            try:
                os.unlink(tmp_path)
            except:
                pass


def render_estimation_mode():
    """Render monthly estimation mode."""
    st.header("Monthly Estimation")

    if not st.session_state.tracking_loaded:
        st.warning("⚠️ Please upload tracking data first (in sidebar)")
        return

    # Vendor and period selection
    col1, col2, col3 = st.columns(3)

    with col1:
        vendor = st.selectbox("Vendor", ["Sectran", "Loomis", "Cashman"])

    with col2:
        month = st.selectbox(
            "Month",
            range(1, 13),
            index=datetime.now().month - 1,
            format_func=lambda x: calendar.month_name[x],
            key="est_month"
        )

    with col3:
        year = st.selectbox("Year", range(2024, 2027), index=1, key="est_year")

    # Generate estimation
    estimator = MonthlyEstimator(st.session_state.tracking_loader)
    estimate = estimator.estimate_month(vendor, year, month)

    # Display results
    st.subheader(f"Estimate for {estimate['period']}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Pickups", estimate['pickup_count'])
    col2.metric("Base Charges", f"${estimate['estimated_base']:,.2f}")
    col3.metric("Fuel + Insurance", f"${estimate['estimated_fuel'] + estimate['estimated_insurance']:,.2f}")
    col4.metric("Estimated Total", f"${estimate['estimated_total']:,.2f}")

    # Location breakdown
    if estimate['locations']:
        st.subheader("By Location")
        loc_df = pd.DataFrame(estimate['locations'])
        loc_df.columns = ['Location', 'Pickups', 'Total Deposit']
        loc_df['Total Deposit'] = loc_df['Total Deposit'].apply(lambda x: f"${x:,.2f}")
        st.dataframe(loc_df, use_container_width=True)

    # Historical trend
    st.subheader("Historical Trend (Last 6 Months)")
    history = estimator.get_historical_summary(vendor, 6)

    if history:
        trend_data = []
        for h in history:
            trend_data.append({
                'Period': h['period'],
                'Pickups': h['pickup_count'],
                'Estimated $': h['estimated_total']
            })

        trend_df = pd.DataFrame(trend_data)
        st.dataframe(trend_df, use_container_width=True)

        # Chart
        if len(trend_df) > 1:
            st.line_chart(trend_df.set_index('Period')['Estimated $'])


def render_explorer_mode():
    """Render data explorer mode."""
    st.header("Data Explorer")

    if not st.session_state.tracking_loaded:
        st.warning("⚠️ Please upload tracking data first (in sidebar)")
        return

    df = st.session_state.tracking_loader.data

    # Filters
    col1, col2, col3 = st.columns(3)

    with col1:
        if 'armored_transport_branch' in df.columns:
            vendors = ['All'] + sorted(df['armored_transport_branch'].dropna().unique().tolist())
            selected_vendor = st.selectbox("Filter by Vendor", vendors)

    with col2:
        if 'pickup_date' in df.columns:
            months = df['pickup_date'].dt.to_period('M').unique()
            month_options = ['All'] + sorted([str(m) for m in months], reverse=True)
            selected_month = st.selectbox("Filter by Month", month_options)

    with col3:
        if 'location_name' in df.columns:
            locations = ['All'] + sorted(df['location_name'].dropna().unique().tolist())
            selected_location = st.selectbox("Filter by Location", locations)

    # Apply filters
    filtered_df = df.copy()

    if selected_vendor != 'All' and 'armored_transport_branch' in filtered_df.columns:
        filtered_df = filtered_df[filtered_df['armored_transport_branch'] == selected_vendor]

    if selected_month != 'All' and 'pickup_date' in filtered_df.columns:
        filtered_df = filtered_df[filtered_df['pickup_date'].dt.to_period('M').astype(str) == selected_month]

    if selected_location != 'All' and 'location_name' in filtered_df.columns:
        filtered_df = filtered_df[filtered_df['location_name'] == selected_location]

    # Display
    st.subheader(f"Showing {len(filtered_df)} records")
    st.dataframe(filtered_df, use_container_width=True)

    # Summary stats
    if 'armored_transport_branch' in filtered_df.columns:
        st.subheader("Summary by Vendor")
        vendor_summary = filtered_df.groupby('armored_transport_branch').size().reset_index(name='Pickups')
        st.dataframe(vendor_summary, use_container_width=True)


# ============================================================================
# MAIN
# ============================================================================

def is_running_in_streamlit():
    """Check if running in Streamlit context."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except:
        return False


if is_running_in_streamlit():
    run_app()
elif __name__ == "__main__":
    print("\nInvoice Reconciliation Tool")
    print("=" * 40)
    print("\nTo run the web interface:")
    print("  python -m streamlit run reconcile_tool.py")
    print("\nOr:")
    print("  streamlit run reconcile_tool.py")
