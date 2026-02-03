# Invoice Reconciliation & Estimation System

A dual-purpose tool for managing armored vendor pickup charges:
1. **Real-time Monthly Estimation** - Estimate expected charges as pickups occur throughout the month
2. **Invoice Reconciliation** - Compare actual invoices against tracked pickups using vendor-specific pricing rules

## Features

### Vendor-Aware Pricing
- **Multiple Vendor Support**: Cash Man Services, Loomis, Sectran, Brinks, Garda
- **Configurable Rates**: Per-service pricing with customizable rate structures
- **Fuel Surcharge Handling**: Support for both fixed and variable fuel surcharge calculations
- **Location Mapping**: Flexible matching between invoice and tracking location names

### Monthly Estimation
- **Real-time Calculations**: Track expected charges as the month progresses
- **Location Breakdown**: See charges by location with schedule detection (EOW, Weekly, Monthly)
- **Month-End Projection**: Estimate remaining pickups based on historical patterns
- **Alerts & Notes**: Automatic detection of unusual patterns or missing pickups

### Invoice Reconciliation
- **PDF Parsing**: Extract line items from vendor invoice PDFs
- **Vendor-Specific Validation**: Verify rates against contracted prices
- **Line-by-Line Comparison**: Compare every charge against expectations
- **Discrepancy Detection**: Identify and categorize mismatches with recommended actions

### Pattern Analysis
- **Schedule Detection**: Automatically identify pickup schedules from historical data
- **Anomaly Alerts**: Flag missing pickups, excess charges, or unusual patterns
- **Trend Analysis**: Track changes in pickup frequency over time

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Install the tool
pip install -e .
```

## Quick Start

### Option 1: Streamlit Web Dashboard

```bash
streamlit run invoice_reconciliation/streamlit_app.py
```

Open http://localhost:8501 in your browser.

### Option 2: Command Line Interface

```bash
# Monthly estimation
invoice-recon estimate tracking.xlsx -v "Cash Man Services" -m "October 2025"

# Invoice reconciliation
invoice-recon reconcile invoice.pdf tracking.xlsx -v "Cash Man Services"

# Interactive mode
invoice-recon interactive
```

### Option 3: Python API

```python
from invoice_reconciliation import (
    get_vendor_manager,
    EstimationEngine,
    VendorReconciler
)
import pandas as pd

# Load vendor configuration
vendor_manager = get_vendor_manager()
vendor = vendor_manager.get_vendor("Cash Man Services")

# Load tracking data
tracking_data = pd.read_excel("tracking.xlsx")

# Generate estimation
engine = EstimationEngine(vendor)
result = engine.estimate_monthly_charges(tracking_data, "October 2025")

print(f"Total Estimated: ${result.total_estimated:,.2f}")
print(f"Total Pickups: {result.total_pickups}")
```

## Vendor Configuration

Vendor pricing is configured in `invoice_reconciliation/vendors.json`:

```json
{
  "vendors": {
    "Cash Man Services": {
      "vendor_id": "cashman",
      "pricing_model": "per_service",
      "rates": {
        "smartsafe_pickup": 46.57,
        "vault_management": 9.22,
        "branch_delivery": 25.70,
        "onetime_pickup": 125.00
      },
      "fuel_surcharge": {
        "enabled": true,
        "type": "percentage_variable",
        "base_fuel_price": 2.50,
        "current_fuel_price": 3.20,
        "rate_per_10_cents": 0.02
      }
    }
  }
}
```

### Updating Vendor Rates

```bash
# Update fuel price
invoice-recon update-vendor "Cash Man Services" --fuel-price 3.50

# Update service rate
invoice-recon update-vendor "Cash Man Services" --rate smartsafe_pickup 48.00
```

## CLI Commands

### `estimate` - Monthly Charge Estimation

```bash
invoice-recon estimate TRACKING_FILE -v VENDOR -m MONTH [OPTIONS]

Options:
  -v, --vendor TEXT    Vendor name (required)
  -m, --month TEXT     Service month, e.g., "October 2025" or "2025-10" (required)
  --as-of DATE         Calculate as-of specific date (default: today)
  -o, --output PATH    Export results to Excel file
  -q, --quiet          Only show summary line
```

**Example:**
```bash
invoice-recon estimate tracking.xlsx -v "Cash Man Services" -m "October 2025" -o estimate.xlsx
```

### `reconcile` - Invoice Reconciliation

```bash
invoice-recon reconcile INVOICE_PDF TRACKING_FILE -v VENDOR [OPTIONS]

Options:
  -v, --vendor TEXT    Vendor name (required)
  -m, --month TEXT     Override service month (optional)
  -o, --output PATH    Export results to Excel file
  -q, --quiet          Only show summary line
```

**Example:**
```bash
invoice-recon reconcile invoice_16815.pdf tracking.xlsx -v "Cash Man Services"
```

### `interactive` - Interactive Mode

```bash
invoice-recon interactive
```

Guides you through vendor selection and provides menu options for:
1. Monthly Estimation
2. Invoice Reconciliation
3. Vendor Comparison

### `list-vendors` - Show Configured Vendors

```bash
invoice-recon list-vendors
```

### `analyze` - Analyze Tracking Data

```bash
invoice-recon analyze TRACKING_FILE [OPTIONS]

Options:
  -v, --vendor TEXT    Filter by vendor
  -m, --month TEXT     Filter by month
  --show-dates         Show pickup dates
```

## Tracking Data Format

Your tracking Excel/CSV should contain these columns:

| Column | Required | Description |
|--------|----------|-------------|
| `pickup_date` or `date` | Yes | Date of pickup |
| `location` or `armored transport branch` | Yes | Location identifier |
| `pickup_type` or `service_type` | No | Type of service |
| `vendor` | No | Vendor name (for filtering) |
| `amount` | No | Pickup amount |

### Column Auto-Detection

The system auto-detects common column naming conventions:
- **Date**: `pickup_date`, `date`, `service_date`, `transaction_date`
- **Location**: `location`, `armored transport branch`, `branch`, `store`
- **Service Type**: `pickup_type`, `service_type`, `type`

## Estimation Report Format

```
=================================================================
MONTHLY CHARGE ESTIMATION
=================================================================
Vendor: Cash Man Services
Service Month: October 2025
As of Date: October 23, 2025
Status: MONTH IN PROGRESS (26% remaining)

-----------------------------------------------------------------
PICKUP SUMMARY BY LOCATION
-----------------------------------------------------------------
Location                              Pickups  Transport  Vault   Subtotal
Game Haven - Sandy - EOW                  3    $139.71   $27.66   $167.37
Game Haven - West Jordan - Monthly        1     $46.57    $9.22    $55.79
Koodegras CBD - Midvale - Monthly         5    $232.85   $46.10   $278.95

-----------------------------------------------------------------
CHARGE BREAKDOWN
-----------------------------------------------------------------
Secure Transportation (Smartsafe)     15 × $46.57      $698.55
Vault & Cash Management               15 × $9.22       $138.30
Fuel Surcharge                        14%              $117.16
                                           ESTIMATED TOTAL: $953.01

-----------------------------------------------------------------
PROJECTION TO MONTH END
-----------------------------------------------------------------
Days elapsed: 23 / 31 (74%)
Remaining days: 8
ESTIMATED MONTH-END TOTAL: $1,036.38 - $1,119.75
=================================================================
```

## Reconciliation Report Format

```
=================================================================
INVOICE RECONCILIATION REPORT
=================================================================
Vendor: Cash Man Services
Invoice #: 16815
Service Month: October 2025

-----------------------------------------------------------------
ESTIMATE vs INVOICE COMPARISON
-----------------------------------------------------------------
                                  Estimated    Invoiced    Variance
-----------------------------------------------------------------
Smartsafe Pickups (15)             $698.55     $698.55      $0.00 ✓
Vault & Cash Management (15)       $138.30     $138.30      $0.00 ✓
Fuel Surcharge                     $117.16     $117.16      $0.00 ✓
-----------------------------------------------------------------
TOTALS                             $953.01     $953.01      $0.00 ✓

RESULT: PERFECT MATCH - No discrepancies found
=================================================================
```

## Fuel Surcharge Calculations

### Variable Rate (Cash Man Services)
```
Rate = (Current Fuel Price - Base Fuel Price) / $0.10 × Rate per 10 cents
Example: ($3.20 - $2.50) / $0.10 × 0.02 = 14%
```

### Fixed Rate (Loomis)
```
Rate = Fixed percentage (e.g., 10%)
```

## Supported Vendors

| Vendor | Pickup Rate | Fuel Surcharge | Notes |
|--------|-------------|----------------|-------|
| Cash Man Services | $46.57 + $9.22 vault | 14% variable | Separate transport & vault |
| Loomis | $35.00 | 10% fixed | Simple structure |
| Sectran | $42.00 | None | No fuel surcharge |
| Brinks | $38.50 | 12% fixed | Also supports smart safe |
| Garda | $40.00 + $8.50 vault | 8% fixed | Similar to Cash Man |

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_vendor_reconciliation.py -v

# Run with coverage
pytest tests/ --cov=invoice_reconciliation
```

## Project Structure

```
invoice_reconciliation/
├── __init__.py           # Package exports
├── vendors.json          # Vendor pricing configuration
├── vendor_config.py      # Vendor configuration management
├── estimation.py         # Monthly estimation engine
├── vendor_reconciler.py  # Invoice reconciliation with vendor rules
├── pattern_analysis.py   # Historical pattern detection
├── streamlit_app.py      # Web dashboard
├── new_cli.py            # Command line interface
├── pdf_parser.py         # PDF invoice parsing
├── csv_parser.py         # CSV tracking data parsing
└── templates/            # Flask web templates (legacy)
```

## Programmatic Usage

### Complete Estimation Workflow

```python
from invoice_reconciliation import (
    get_vendor_manager,
    EstimationEngine,
    format_estimation_report,
    export_estimation_to_excel
)
import pandas as pd
from datetime import date

# Load vendor
manager = get_vendor_manager()
vendor = manager.get_vendor("Cash Man Services")

# Load tracking data
tracking = pd.read_excel("tracking.xlsx", sheet_name="Pickup Recon")

# Generate estimation
engine = EstimationEngine(vendor)
result = engine.estimate_monthly_charges(
    tracking,
    "October 2025",
    as_of_date=date(2025, 10, 23)
)

# Print report
print(format_estimation_report(result))

# Export to Excel
export_estimation_to_excel(result, "october_estimate.xlsx")
```

### Compare Estimate to Invoice

```python
from invoice_reconciliation import (
    VendorReconciler,
    format_reconciliation_report
)

reconciler = VendorReconciler(vendor)
result = reconciler.reconcile(
    "invoice.pdf",
    tracking,
    "October 2025"
)

print(f"Status: {result.status}")
print(f"Variance: ${result.variance:,.2f}")

if result.discrepancies:
    for disc in result.discrepancies:
        print(f"- {disc.message}: ${disc.impact:,.2f}")
```

### Analyze Patterns

```python
from invoice_reconciliation import (
    analyze_pickup_patterns,
    format_pattern_report
)

result = analyze_pickup_patterns(tracking, months_back=6)

print(format_pattern_report(result))

for location, pattern in result.location_patterns.items():
    print(f"{location}: {pattern.schedule_detected} "
          f"(avg {pattern.avg_pickups_per_month:.1f}/month)")
```

## License

Internal use only.
