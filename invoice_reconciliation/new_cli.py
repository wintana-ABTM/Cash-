"""
Command Line Interface for Invoice Reconciliation System

Provides CLI commands for:
- Monthly charge estimation
- Invoice reconciliation
- Interactive mode
- Vendor management
"""

import click
import pandas as pd
from datetime import datetime, date
from pathlib import Path
import sys

from .vendor_config import (
    VendorConfigManager, VendorConfig,
    get_vendor_manager, select_vendor_interactive
)
from .estimation import (
    EstimationEngine, EstimationResult,
    format_estimation_report, export_estimation_to_excel
)
from .vendor_reconciler import (
    VendorReconciler, ReconciliationResult,
    format_reconciliation_report, export_reconciliation_to_excel
)


def load_tracking_data(file_path: str) -> pd.DataFrame:
    """Load tracking data from CSV or Excel file."""
    path = Path(file_path)

    if not path.exists():
        raise click.ClickException(f"File not found: {file_path}")

    if path.suffix.lower() == '.csv':
        return pd.read_csv(file_path)
    elif path.suffix.lower() in ['.xlsx', '.xls']:
        # Try to find pickup recon sheet
        excel_file = pd.ExcelFile(file_path)
        sheet_names = excel_file.sheet_names

        for sheet in sheet_names:
            if 'pickup' in sheet.lower() or 'recon' in sheet.lower():
                return pd.read_excel(file_path, sheet_name=sheet)

        # Use first sheet
        return pd.read_excel(file_path, sheet_name=0)
    else:
        raise click.ClickException(f"Unsupported file format: {path.suffix}")


@click.group()
@click.version_option(version='2.0.0')
def cli():
    """Invoice Reconciliation & Estimation System

    A dual-purpose tool for estimating monthly charges and reconciling
    invoices against pickup tracking data using vendor-specific pricing rules.
    """
    pass


@cli.command()
@click.argument('tracking_file', type=click.Path(exists=True))
@click.option('-v', '--vendor', required=True, help='Vendor name (e.g., "Cash Man Services")')
@click.option('-m', '--month', required=True, help='Service month (e.g., "October 2025" or "2025-10")')
@click.option('--as-of', type=click.DateTime(formats=['%Y-%m-%d']),
              help='Calculate as-of specific date (default: today)')
@click.option('-o', '--output', type=click.Path(), help='Export results to Excel file')
@click.option('-q', '--quiet', is_flag=True, help='Only show summary line')
def estimate(tracking_file, vendor, month, as_of, output, quiet):
    """Estimate monthly charges based on pickup tracking data.

    TRACKING_FILE: Path to pickup tracking CSV or Excel file

    Examples:

      invoice-recon estimate pickups.csv -v "Cash Man Services" -m "October 2025"

      invoice-recon estimate tracking.xlsx -v Loomis -m 2025-10 --as-of 2025-10-23

      invoice-recon estimate data.csv -v Sectran -m "November 2025" -o estimate.xlsx
    """
    try:
        # Load vendor config
        vendor_manager = get_vendor_manager()
        vendor_config = vendor_manager.get_vendor(vendor)

        if not vendor_config:
            available = ", ".join(vendor_manager.list_vendors())
            raise click.ClickException(f"Vendor '{vendor}' not found. Available: {available}")

        # Load tracking data
        tracking_data = load_tracking_data(tracking_file)
        click.echo(f"Loaded {len(tracking_data)} records from {tracking_file}")

        # Set as-of date
        as_of_date = as_of.date() if as_of else date.today()

        # Generate estimation
        engine = EstimationEngine(vendor_config)
        result = engine.estimate_monthly_charges(tracking_data, month, as_of_date)

        if quiet:
            click.echo(f"{result.vendor_name} | {result.service_month} | "
                      f"Pickups: {result.total_pickups} | "
                      f"Total: ${result.total_estimated:,.2f}")
        else:
            click.echo(format_estimation_report(result))

        # Export if requested
        if output:
            export_estimation_to_excel(result, output)
            click.echo(f"\nExported to: {output}")

        # Return success
        sys.exit(0)

    except Exception as e:
        raise click.ClickException(str(e))


@cli.command()
@click.argument('invoice_file', type=click.Path(exists=True))
@click.argument('tracking_file', type=click.Path(exists=True))
@click.option('-v', '--vendor', required=True, help='Vendor name')
@click.option('-m', '--month', help='Service month (optional, extracted from invoice if not provided)')
@click.option('-o', '--output', type=click.Path(), help='Export results to Excel file')
@click.option('-q', '--quiet', is_flag=True, help='Only show summary line')
def reconcile(invoice_file, tracking_file, vendor, month, output, quiet):
    """Reconcile invoice against tracking data.

    INVOICE_FILE: Path to invoice PDF
    TRACKING_FILE: Path to pickup tracking CSV or Excel file

    Examples:

      invoice-recon reconcile invoice.pdf pickups.csv -v "Cash Man Services"

      invoice-recon reconcile inv_16815.pdf tracking.xlsx -v Loomis -m "October 2025"

      invoice-recon reconcile invoice.pdf data.csv -v Sectran -o reconciliation.xlsx
    """
    try:
        # Load vendor config
        vendor_manager = get_vendor_manager()
        vendor_config = vendor_manager.get_vendor(vendor)

        if not vendor_config:
            available = ", ".join(vendor_manager.list_vendors())
            raise click.ClickException(f"Vendor '{vendor}' not found. Available: {available}")

        # Load tracking data
        tracking_data = load_tracking_data(tracking_file)
        click.echo(f"Loaded {len(tracking_data)} tracking records")

        # Reconcile
        reconciler = VendorReconciler(vendor_config)
        result = reconciler.reconcile(invoice_file, tracking_data, month)

        if quiet:
            status_icon = "✓" if result.status == "PERFECT_MATCH" else "⚠" if result.status == "MINOR_VARIANCE" else "✗"
            click.echo(f"{status_icon} Invoice #{result.invoice_number} | "
                      f"Expected: ${result.expected_total:,.2f} | "
                      f"Invoiced: ${result.invoice_total:,.2f} | "
                      f"Variance: ${result.variance:,.2f}")
        else:
            click.echo(format_reconciliation_report(result))

        # Export if requested
        if output:
            export_reconciliation_to_excel(result, output)
            click.echo(f"\nExported to: {output}")

        # Exit code based on status
        if result.status == "PERFECT_MATCH":
            sys.exit(0)
        elif result.status == "MINOR_VARIANCE":
            sys.exit(1)
        else:
            sys.exit(2)

    except Exception as e:
        raise click.ClickException(str(e))


@cli.command()
def interactive():
    """Run in interactive mode with prompts.

    Guides you through vendor selection and mode selection interactively.
    """
    click.echo("\n" + "=" * 60)
    click.echo("INVOICE RECONCILIATION & ESTIMATION SYSTEM")
    click.echo("=" * 60)

    # Select vendor
    vendor_manager = get_vendor_manager()
    vendor = select_vendor_interactive(vendor_manager)

    click.echo("\n" + "-" * 60)
    click.echo("SELECT MODE")
    click.echo("-" * 60)
    click.echo("1. Monthly Estimation")
    click.echo("2. Invoice Reconciliation")
    click.echo("3. Vendor Comparison")
    click.echo("4. Exit")

    while True:
        mode = click.prompt("Select mode (1-4)", type=int)

        if mode == 1:
            _interactive_estimate(vendor)
            break
        elif mode == 2:
            _interactive_reconcile(vendor)
            break
        elif mode == 3:
            _interactive_compare(vendor_manager)
            break
        elif mode == 4:
            click.echo("Goodbye!")
            sys.exit(0)
        else:
            click.echo("Invalid choice. Please try again.")


def _interactive_estimate(vendor: VendorConfig):
    """Interactive estimation mode."""
    click.echo("\n" + "-" * 60)
    click.echo("MONTHLY ESTIMATION")
    click.echo("-" * 60)

    tracking_file = click.prompt("Path to tracking file (CSV/Excel)")
    month = click.prompt("Service month (e.g., October 2025)")

    use_today = click.confirm("Calculate as of today?", default=True)
    if use_today:
        as_of_date = date.today()
    else:
        as_of_str = click.prompt("As-of date (YYYY-MM-DD)")
        as_of_date = datetime.strptime(as_of_str, "%Y-%m-%d").date()

    tracking_data = load_tracking_data(tracking_file)
    click.echo(f"Loaded {len(tracking_data)} records")

    engine = EstimationEngine(vendor)
    result = engine.estimate_monthly_charges(tracking_data, month, as_of_date)

    click.echo("\n" + format_estimation_report(result))

    if click.confirm("Export to Excel?"):
        output = click.prompt("Output file path", default=f"estimate_{month.replace(' ', '_')}.xlsx")
        export_estimation_to_excel(result, output)
        click.echo(f"Exported to: {output}")


def _interactive_reconcile(vendor: VendorConfig):
    """Interactive reconciliation mode."""
    click.echo("\n" + "-" * 60)
    click.echo("INVOICE RECONCILIATION")
    click.echo("-" * 60)

    invoice_file = click.prompt("Path to invoice PDF")
    tracking_file = click.prompt("Path to tracking file (CSV/Excel)")

    override_month = click.confirm("Override service month from invoice?", default=False)
    month = None
    if override_month:
        month = click.prompt("Service month (e.g., October 2025)")

    tracking_data = load_tracking_data(tracking_file)
    click.echo(f"Loaded {len(tracking_data)} records")

    reconciler = VendorReconciler(vendor)
    result = reconciler.reconcile(invoice_file, tracking_data, month)

    click.echo("\n" + format_reconciliation_report(result))

    if click.confirm("Export to Excel?"):
        output = click.prompt("Output file path", default=f"reconciliation_{result.invoice_number}.xlsx")
        export_reconciliation_to_excel(result, output)
        click.echo(f"Exported to: {output}")


def _interactive_compare(vendor_manager: VendorConfigManager):
    """Interactive vendor comparison mode."""
    click.echo("\n" + "-" * 60)
    click.echo("VENDOR COMPARISON")
    click.echo("-" * 60)

    tracking_file = click.prompt("Path to tracking file (CSV/Excel)")
    month = click.prompt("Service month (e.g., October 2025)")

    tracking_data = load_tracking_data(tracking_file)
    click.echo(f"Loaded {len(tracking_data)} records")

    vendors = vendor_manager.list_vendors()
    click.echo("\nAvailable vendors:")
    for i, v in enumerate(vendors, 1):
        click.echo(f"  {i}. {v}")

    selected_str = click.prompt("Select vendors to compare (comma-separated numbers)")
    selected_indices = [int(x.strip()) for x in selected_str.split(",")]
    selected_vendors = [vendors[i-1] for i in selected_indices if 1 <= i <= len(vendors)]

    click.echo(f"\nComparing: {', '.join(selected_vendors)}")
    click.echo("-" * 60)

    results = []
    for vendor_name in selected_vendors:
        vendor_config = vendor_manager.get_vendor(vendor_name)
        engine = EstimationEngine(vendor_config)

        try:
            result = engine.estimate_monthly_charges(tracking_data, month)
            results.append({
                "Vendor": vendor_name,
                "Pickups": result.total_pickups,
                "Base": result.base_charges,
                "Fuel": result.fuel_surcharge,
                "Total": result.total_estimated
            })
        except Exception as e:
            click.echo(f"  Error for {vendor_name}: {str(e)}")

    if results:
        # Print comparison table
        click.echo(f"\n{'Vendor':<25} {'Pickups':>8} {'Base':>12} {'Fuel':>10} {'Total':>12}")
        click.echo("-" * 70)

        min_total = min(r["Total"] for r in results)

        for r in results:
            marker = " *BEST*" if r["Total"] == min_total else ""
            click.echo(f"{r['Vendor']:<25} {r['Pickups']:>8} ${r['Base']:>10,.2f} "
                      f"${r['Fuel']:>8,.2f} ${r['Total']:>10,.2f}{marker}")

        # Savings calculation
        if len(results) >= 2:
            sorted_results = sorted(results, key=lambda x: x["Total"])
            savings = sorted_results[-1]["Total"] - sorted_results[0]["Total"]
            click.echo(f"\nPotential monthly savings: ${savings:,.2f}")
            click.echo(f"  (switching from {sorted_results[-1]['Vendor']} to {sorted_results[0]['Vendor']})")


@cli.command()
def list_vendors():
    """List all configured vendors."""
    vendor_manager = get_vendor_manager()

    click.echo("\n" + "=" * 60)
    click.echo("CONFIGURED VENDORS")
    click.echo("=" * 60)

    for vendor_name in vendor_manager.list_vendors():
        vendor = vendor_manager.get_vendor(vendor_name)

        click.echo(f"\n{vendor_name}")
        click.echo("-" * 40)
        click.echo(f"  ID: {vendor.vendor_id}")
        click.echo(f"  Pricing Model: {vendor.pricing_model}")

        click.echo("  Rates:")
        for key, rate in vendor.rates.items():
            click.echo(f"    - {key}: ${rate:.2f}")

        if vendor.fuel_surcharge.enabled:
            fuel_rate = vendor.get_fuel_surcharge_rate()
            click.echo(f"  Fuel Surcharge: {fuel_rate*100:.1f}%")

            if vendor.fuel_surcharge.surcharge_type == "percentage_variable":
                click.echo(f"    Base Price: ${vendor.fuel_surcharge.base_fuel_price:.2f}")
                click.echo(f"    Current Price: ${vendor.fuel_surcharge.current_fuel_price:.2f}")


@cli.command()
@click.argument('vendor_name')
@click.option('--fuel-price', type=float, help='Update current fuel price')
@click.option('--rate', nargs=2, multiple=True, help='Update rate: --rate <key> <value>')
def update_vendor(vendor_name, fuel_price, rate):
    """Update vendor configuration.

    VENDOR_NAME: Name of the vendor to update

    Examples:

      invoice-recon update-vendor "Cash Man Services" --fuel-price 3.50

      invoice-recon update-vendor Loomis --rate pickup 37.50

      invoice-recon update-vendor Sectran --rate pickup 45.00 --rate emergency_pickup 85.00
    """
    vendor_manager = get_vendor_manager()
    vendor = vendor_manager.get_vendor(vendor_name)

    if not vendor:
        available = ", ".join(vendor_manager.list_vendors())
        raise click.ClickException(f"Vendor '{vendor_name}' not found. Available: {available}")

    updated = False

    if fuel_price is not None:
        try:
            old_price = vendor.fuel_surcharge.current_fuel_price
            vendor_manager.update_fuel_price(vendor_name, fuel_price)
            click.echo(f"Updated fuel price: ${old_price:.2f} -> ${fuel_price:.2f}")
            updated = True
        except ValueError as e:
            click.echo(f"Cannot update fuel price: {str(e)}")

    for rate_key, rate_value in rate:
        try:
            rate_value = float(rate_value)
            old_rate = vendor.rates.get(rate_key, 0)
            vendor_manager.update_rate(vendor_name, rate_key, rate_value)
            click.echo(f"Updated {rate_key}: ${old_rate:.2f} -> ${rate_value:.2f}")
            updated = True
        except ValueError as e:
            click.echo(f"Cannot update {rate_key}: {str(e)}")

    if updated:
        if click.confirm("Save changes to config file?"):
            vendor_manager.save_config()
            click.echo("Configuration saved.")
    else:
        click.echo("No updates made.")


@cli.command()
@click.argument('tracking_file', type=click.Path(exists=True))
@click.option('-v', '--vendor', help='Filter by vendor name')
@click.option('-m', '--month', help='Filter by month')
@click.option('--show-dates', is_flag=True, help='Show pickup dates')
def analyze(tracking_file, vendor, month, show_dates):
    """Analyze tracking data for patterns and issues.

    TRACKING_FILE: Path to pickup tracking CSV or Excel file
    """
    tracking_data = load_tracking_data(tracking_file)
    click.echo(f"Loaded {len(tracking_data)} records from {tracking_file}")

    # Detect columns
    columns = tracking_data.columns.tolist()
    click.echo(f"\nDetected columns: {', '.join(columns)}")

    # Show data summary
    click.echo(f"\nDate range: {tracking_data.iloc[:, 0].min()} to {tracking_data.iloc[:, 0].max()}")

    # Group by location if possible
    location_col = None
    for col in columns:
        if 'location' in col.lower() or 'branch' in col.lower():
            location_col = col
            break

    if location_col:
        click.echo(f"\nPickups by location ({location_col}):")
        location_counts = tracking_data[location_col].value_counts()
        for loc, count in location_counts.items():
            click.echo(f"  {loc}: {count}")


def main():
    """Entry point for CLI."""
    cli()


if __name__ == '__main__':
    main()
