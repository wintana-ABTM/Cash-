# Invoice Reconciliation Tool

A command-line tool for reconciling armored vendor invoice PDFs against pickup CSV records by matching on location ID and month.

## Features

- **PDF Invoice Parsing**: Automatically extracts line items from armored vendor invoices (supports Loomis, Brinks, Garda, and other major carriers)
- **CSV Pickup Parsing**: Flexible CSV parsing with auto-detection of common column formats
- **Reconciliation Engine**: Matches invoices to pickups by location ID and month
- **Multiple Report Types**: Summary, detail, and discrepancy-focused reports
- **CSV Export**: Export results and unmatched items to CSV for further analysis

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Install the tool
pip install -e .
```

## Quick Start

### Basic Reconciliation

```bash
# Reconcile a directory of invoice PDFs against a pickup CSV
invoice-reconcile reconcile ./invoices/ ./pickups.csv

# Reconcile a single invoice PDF
invoice-reconcile reconcile ./invoice.pdf ./pickups.csv
```

### Filter by Location or Month

```bash
# Filter to specific locations
invoice-reconcile reconcile ./invoices/ ./pickups.csv -l 1001 -l 1002

# Filter to specific months
invoice-reconcile reconcile ./invoices/ ./pickups.csv -m 2024-01 -m 2024-02
```

### Generate Reports

```bash
# Summary report (default)
invoice-reconcile reconcile ./invoices/ ./pickups.csv -r summary

# Detailed report with all location-months
invoice-reconcile reconcile ./invoices/ ./pickups.csv -r detail

# Discrepancy-only report
invoice-reconcile reconcile ./invoices/ ./pickups.csv -r discrepancy
```

### Export Results

```bash
# Export to CSV
invoice-reconcile reconcile ./invoices/ ./pickups.csv -o results.csv

# Export unmatched items
invoice-reconcile reconcile ./invoices/ ./pickups.csv --export-unmatched unmatched.csv
```

## Commands

### `reconcile`

Main command for reconciling invoices against pickups.

```bash
invoice-reconcile reconcile INVOICE_PATH PICKUP_CSV [OPTIONS]
```

Options:
- `-v, --vendor TEXT`: Vendor name (auto-detected if not specified)
- `-l, --location TEXT`: Filter to specific location ID(s) (can be repeated)
- `-m, --month TEXT`: Filter to specific month(s) in YYYY-MM format (can be repeated)
- `-o, --output PATH`: Output CSV file for results
- `-r, --report [summary|detail|discrepancy]`: Report type (default: summary)
- `--match-by-date`: Match by exact date instead of just month
- `--export-unmatched PATH`: Export unmatched items to separate CSV
- `-q, --quiet`: Only show summary line

### `parse-invoice`

Test PDF parsing for a single invoice.

```bash
invoice-reconcile parse-invoice PDF_PATH [OPTIONS]
```

Options:
- `-v, --vendor TEXT`: Vendor name

### `parse-csv`

Test CSV parsing for pickup records.

```bash
invoice-reconcile parse-csv CSV_PATH [OPTIONS]
```

Options:
- `--location-col TEXT`: Column name for location ID
- `--date-col TEXT`: Column name for pickup date
- `--date-format TEXT`: Date format string (e.g., '%Y-%m-%d')

### `check-location`

Check reconciliation for a specific location and month.

```bash
invoice-reconcile check-location INVOICE_PATH PICKUP_CSV LOCATION_ID MONTH
```

## CSV Format

The pickup CSV should contain these columns (column names are auto-detected):

| Column | Required | Description |
|--------|----------|-------------|
| location_id | Yes | Store/location identifier |
| pickup_date | Yes | Date of pickup (various formats supported) |
| location_name | No | Human-readable location name |
| pickup_type | No | Type of pickup service |
| amount | No | Expected pickup amount |
| reference | No | Reference/confirmation number |

### Supported Column Names

The parser auto-detects common column naming conventions:

- **Location ID**: `location_id`, `store_id`, `site_id`, `store #`, `location #`, etc.
- **Date**: `pickup_date`, `date`, `service_date`, `transaction_date`, etc.
- **Amount**: `amount`, `expected_amount`, `pickup_amount`, `value`, `total`, etc.

### Sample CSV

```csv
location_id,location_name,pickup_date,pickup_type,expected_amount,reference_number
1001,Downtown Store,2024-01-05,Regular Pickup,1500.00,REF-001
1001,Downtown Store,2024-01-12,Regular Pickup,1750.50,REF-002
1002,Mall Location,2024-01-03,Regular Pickup,2200.00,REF-003
```

## PDF Invoice Requirements

The tool parses invoice PDFs by extracting:
1. Header information (invoice number, date, vendor, total)
2. Line item tables (location, date, service type, amount)

### Supported Vendors

Auto-detection works for:
- Loomis
- Brinks
- Garda
- Dunbar
- Rochester Armored

### Custom Patterns

For non-standard invoice formats, you can customize the PDF parser programmatically:

```python
from invoice_reconciliation.pdf_parser import PDFInvoiceParser

parser = PDFInvoiceParser(
    vendor_name="Custom Vendor",
    custom_patterns={
        "invoice_number": [r"Inv:\s*(\w+)"],
        "location_id": [r"Store:\s*(\d+)"],
    }
)
invoice = parser.parse("invoice.pdf")
```

## Reconciliation Logic

1. **Indexing**: Invoice line items and pickups are indexed by (location_id, month)
2. **Matching**: For each location-month combination:
   - Count of invoice items vs pickup records
   - Total amounts (if available in both)
3. **Status Assignment**:
   - `MATCHED`: Counts match, no unmatched items
   - `INVOICE_OVER`: More invoice items than pickups
   - `INVOICE_UNDER`: Fewer invoice items than pickups
   - `MISMATCH`: Other discrepancy

## Exit Codes

- `0`: All location-months reconciled
- `1`: Discrepancies found
- `2`: Parse error (invalid PDF or CSV)
- `3`: Unexpected error

## Programmatic Usage

```python
from invoice_reconciliation.pdf_parser import parse_invoice_directory
from invoice_reconciliation.csv_parser import parse_pickup_csv
from invoice_reconciliation.reconciler import InvoiceReconciler
from invoice_reconciliation.reports import generate_discrepancy_report

# Parse data
invoices = parse_invoice_directory("./invoices/")
pickups = parse_pickup_csv("./pickups.csv")

# Reconcile
reconciler = InvoiceReconciler()
summary = reconciler.reconcile(invoices, pickups)

# Generate report
print(generate_discrepancy_report(summary))

# Access results programmatically
for result in summary.results:
    if not result.is_reconciled:
        print(f"Discrepancy at {result.location_id}/{result.month}")
```

## License

Internal use only.
