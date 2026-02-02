"""Command-line interface for invoice reconciliation."""

import sys
from pathlib import Path
from typing import Optional

import click

from .csv_parser import CSVPickupParser, PickupParseError
from .pdf_parser import PDFInvoiceParser, parse_invoice_directory, InvoiceParseError
from .reconciler import InvoiceReconciler
from .reports import (
    generate_summary_report,
    generate_detail_report,
    generate_discrepancy_report,
    export_to_csv,
    export_unmatched_items,
    print_quick_summary,
)


@click.group()
@click.version_option(version="1.0.0")
def cli():
    """Invoice Reconciliation Tool for Armored Vendor Invoices.

    Compare invoice PDFs from armored vendors against pickup CSV records,
    matching by location ID and month.
    """
    pass


@cli.command()
@click.argument("invoice_path", type=click.Path(exists=True))
@click.argument("pickup_csv", type=click.Path(exists=True))
@click.option(
    "--vendor", "-v",
    help="Vendor name (auto-detected if not specified)"
)
@click.option(
    "--location", "-l",
    multiple=True,
    help="Filter to specific location ID(s)"
)
@click.option(
    "--month", "-m",
    multiple=True,
    help="Filter to specific month(s) (YYYY-MM format)"
)
@click.option(
    "--output", "-o",
    type=click.Path(),
    help="Output CSV file for results"
)
@click.option(
    "--report", "-r",
    type=click.Choice(["summary", "detail", "discrepancy"]),
    default="summary",
    help="Report type to generate"
)
@click.option(
    "--match-by-date",
    is_flag=True,
    help="Match by exact date instead of just month"
)
@click.option(
    "--export-unmatched",
    type=click.Path(),
    help="Export unmatched items to separate CSV"
)
@click.option(
    "--quiet", "-q",
    is_flag=True,
    help="Only show summary line"
)
def reconcile(
    invoice_path: str,
    pickup_csv: str,
    vendor: Optional[str],
    location: tuple[str, ...],
    month: tuple[str, ...],
    output: Optional[str],
    report: str,
    match_by_date: bool,
    export_unmatched: Optional[str],
    quiet: bool,
):
    """Reconcile invoice PDFs against pickup CSV.

    INVOICE_PATH: Path to invoice PDF file or directory of PDFs
    PICKUP_CSV: Path to CSV file with pickup records
    """
    try:
        # Parse invoices
        invoice_path_obj = Path(invoice_path)
        if invoice_path_obj.is_dir():
            if not quiet:
                click.echo(f"Parsing invoices from directory: {invoice_path}")
            invoices = parse_invoice_directory(invoice_path, vendor_name=vendor)
        else:
            if not quiet:
                click.echo(f"Parsing invoice: {invoice_path}")
            parser = PDFInvoiceParser(vendor_name=vendor)
            invoices = [parser.parse(invoice_path)]

        if not invoices:
            click.echo("Error: No invoices could be parsed", err=True)
            sys.exit(1)

        if not quiet:
            total_items = sum(len(inv.line_items) for inv in invoices)
            click.echo(f"  Found {len(invoices)} invoice(s) with {total_items} line items")

        # Parse pickup CSV
        if not quiet:
            click.echo(f"Parsing pickup records: {pickup_csv}")
        csv_parser = CSVPickupParser()
        pickups = csv_parser.parse(pickup_csv)

        if not pickups:
            click.echo("Error: No pickup records could be parsed", err=True)
            sys.exit(1)

        if not quiet:
            click.echo(f"  Found {len(pickups)} pickup record(s)")

        # Reconcile
        if not quiet:
            click.echo("Reconciling...")

        reconciler = InvoiceReconciler(match_by_date=match_by_date)
        summary = reconciler.reconcile(
            invoices=invoices,
            pickups=pickups,
            location_ids=list(location) if location else None,
            months=list(month) if month else None,
        )

        # Generate report
        if quiet:
            print_quick_summary(summary)
        else:
            click.echo("")
            if report == "summary":
                click.echo(generate_summary_report(summary))
            elif report == "detail":
                click.echo(generate_detail_report(summary))
            elif report == "discrepancy":
                click.echo(generate_discrepancy_report(summary))

        # Export results
        if output:
            export_to_csv(summary, output)
            if not quiet:
                click.echo(f"Results exported to: {output}")

        if export_unmatched:
            export_unmatched_items(summary, export_unmatched)
            if not quiet:
                click.echo(f"Unmatched items exported to: {export_unmatched}")

        # Exit with error code if there are discrepancies
        if summary.mismatched_count > 0:
            sys.exit(1)

    except (InvoiceParseError, PickupParseError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(3)


@cli.command()
@click.argument("pdf_path", type=click.Path(exists=True))
@click.option("--vendor", "-v", help="Vendor name")
def parse_invoice(pdf_path: str, vendor: Optional[str]):
    """Parse and display contents of an invoice PDF.

    Useful for testing PDF parsing before running reconciliation.
    """
    try:
        parser = PDFInvoiceParser(vendor_name=vendor)
        invoice = parser.parse(pdf_path)

        click.echo(f"Invoice Number: {invoice.invoice_number}")
        click.echo(f"Vendor: {invoice.vendor_name}")
        click.echo(f"Date: {invoice.invoice_date}")
        click.echo(f"Total: ${invoice.total_amount:,.2f}")
        click.echo(f"Line Items: {len(invoice.line_items)}")
        click.echo("")

        if invoice.line_items:
            click.echo("Line Items:")
            click.echo("-" * 60)
            for item in invoice.line_items[:20]:  # Show first 20
                click.echo(
                    f"  {item.location_id:12} | {item.service_date} | "
                    f"{item.service_type:15} | ${item.amount:,.2f}"
                )
            if len(invoice.line_items) > 20:
                click.echo(f"  ... and {len(invoice.line_items) - 20} more items")
        else:
            click.echo("No line items could be extracted from tables.")
            click.echo("You may need to configure custom patterns for this vendor.")

    except InvoiceParseError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("csv_path", type=click.Path(exists=True))
@click.option(
    "--location-col",
    help="Column name for location ID"
)
@click.option(
    "--date-col",
    help="Column name for pickup date"
)
@click.option(
    "--date-format",
    help="Date format string (e.g., '%%Y-%%m-%%d')"
)
def parse_csv(
    csv_path: str,
    location_col: Optional[str],
    date_col: Optional[str],
    date_format: Optional[str],
):
    """Parse and display contents of a pickup CSV.

    Useful for testing CSV parsing before running reconciliation.
    """
    try:
        mappings = {}
        if location_col:
            mappings["location_id"] = location_col
        if date_col:
            mappings["pickup_date"] = date_col

        parser = CSVPickupParser(
            column_mappings=mappings if mappings else None,
            date_format=date_format,
        )
        pickups = parser.parse(csv_path)

        click.echo(f"Total Records: {len(pickups)}")
        click.echo("")

        if pickups:
            # Show unique locations
            locations = set(p.location_id for p in pickups)
            click.echo(f"Unique Locations: {len(locations)}")

            # Show date range
            dates = [p.pickup_date for p in pickups]
            click.echo(f"Date Range: {min(dates)} to {max(dates)}")

            # Show unique months
            months = set(p.month_key for p in pickups)
            click.echo(f"Months: {', '.join(sorted(months))}")
            click.echo("")

            # Show sample records
            click.echo("Sample Records:")
            click.echo("-" * 60)
            for pickup in pickups[:10]:
                click.echo(
                    f"  {pickup.location_id:12} | {pickup.pickup_date} | "
                    f"{pickup.pickup_type:15}"
                )
            if len(pickups) > 10:
                click.echo(f"  ... and {len(pickups) - 10} more records")

    except PickupParseError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("invoice_path", type=click.Path(exists=True))
@click.argument("pickup_csv", type=click.Path(exists=True))
@click.argument("location_id")
@click.argument("month")
@click.option("--vendor", "-v", help="Vendor name")
def check_location(
    invoice_path: str,
    pickup_csv: str,
    location_id: str,
    month: str,
    vendor: Optional[str],
):
    """Check reconciliation for a specific location and month.

    LOCATION_ID: The location ID to check
    MONTH: The month to check (YYYY-MM format)
    """
    try:
        # Parse invoices
        invoice_path_obj = Path(invoice_path)
        if invoice_path_obj.is_dir():
            invoices = parse_invoice_directory(invoice_path, vendor_name=vendor)
        else:
            parser = PDFInvoiceParser(vendor_name=vendor)
            invoices = [parser.parse(invoice_path)]

        # Parse pickups
        csv_parser = CSVPickupParser()
        pickups = csv_parser.parse(pickup_csv)

        # Reconcile single location
        reconciler = InvoiceReconciler()
        result = reconciler.reconcile_single_location(
            invoices, pickups, location_id, month
        )

        click.echo(f"Location: {location_id}")
        click.echo(f"Month: {month}")
        click.echo(f"Status: {result.status}")
        click.echo("")
        click.echo(f"Invoice Items: {result.invoice_count}")
        click.echo(f"Pickup Records: {result.pickup_count}")
        click.echo(f"Count Difference: {result.difference_count:+d}")
        click.echo("")
        click.echo(f"Invoice Total: ${result.invoice_total:,.2f}")
        if result.pickup_total is not None:
            click.echo(f"Pickup Total: ${result.pickup_total:,.2f}")
            if result.difference_amount is not None:
                click.echo(f"Amount Difference: ${result.difference_amount:+,.2f}")

        if result.unmatched_invoice_items:
            click.echo("")
            click.echo("Unmatched Invoice Items:")
            for item in result.unmatched_invoice_items:
                click.echo(f"  - {item.service_date}: ${item.amount:,.2f}")

        if result.unmatched_pickups:
            click.echo("")
            click.echo("Unmatched Pickup Records:")
            for pickup in result.unmatched_pickups:
                click.echo(f"  - {pickup.pickup_date}")

    except (InvoiceParseError, PickupParseError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def main():
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
