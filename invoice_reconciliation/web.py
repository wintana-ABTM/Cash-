"""Flask web interface for invoice reconciliation."""

import os
import tempfile
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session
from werkzeug.utils import secure_filename

from .csv_parser import CSVPickupParser, PickupParseError
from .pdf_parser import PDFInvoiceParser, parse_invoice_directory, InvoiceParseError
from .reconciler import InvoiceReconciler
from .reports import export_to_csv, export_unmatched_items


app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB max upload

# Temporary storage for uploaded files and results
UPLOAD_FOLDER = tempfile.mkdtemp(prefix="invoice_reconcile_")
RESULTS_FOLDER = tempfile.mkdtemp(prefix="invoice_results_")

ALLOWED_EXTENSIONS = {"pdf", "csv"}


def allowed_file(filename: str, extensions: set) -> bool:
    """Check if file extension is allowed."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in extensions


def format_currency(amount) -> str:
    """Format decimal as currency for templates."""
    if amount is None:
        return "N/A"
    return f"${amount:,.2f}"


def format_diff(value) -> str:
    """Format difference with sign."""
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


def basename_filter(path) -> str:
    """Get basename from a path."""
    if not path:
        return ""
    return Path(path).name


# Register template filters
app.jinja_env.filters["currency"] = format_currency
app.jinja_env.filters["diff"] = format_diff
app.jinja_env.filters["basename"] = basename_filter


@app.route("/")
def index():
    """Home page with upload form."""
    return render_template("index.html")


@app.route("/reconcile", methods=["POST"])
def reconcile():
    """Handle file upload and run reconciliation."""
    # Check for required files
    if "invoices" not in request.files:
        flash("No invoice files uploaded", "error")
        return redirect(url_for("index"))

    if "pickups" not in request.files:
        flash("No pickup CSV uploaded", "error")
        return redirect(url_for("index"))

    invoice_files = request.files.getlist("invoices")
    pickup_file = request.files["pickups"]

    if not invoice_files or invoice_files[0].filename == "":
        flash("No invoice files selected", "error")
        return redirect(url_for("index"))

    if pickup_file.filename == "":
        flash("No pickup CSV selected", "error")
        return redirect(url_for("index"))

    # Validate pickup CSV
    if not allowed_file(pickup_file.filename, {"csv"}):
        flash("Pickup file must be a CSV", "error")
        return redirect(url_for("index"))

    # Create unique session folder
    session_id = str(uuid.uuid4())[:8]
    session_folder = Path(UPLOAD_FOLDER) / session_id
    session_folder.mkdir(parents=True, exist_ok=True)

    try:
        # Save invoice PDFs
        invoice_paths = []
        for inv_file in invoice_files:
            if inv_file.filename and allowed_file(inv_file.filename, {"pdf"}):
                filename = secure_filename(inv_file.filename)
                filepath = session_folder / filename
                inv_file.save(filepath)
                invoice_paths.append(filepath)

        if not invoice_paths:
            flash("No valid PDF files uploaded", "error")
            return redirect(url_for("index"))

        # Save pickup CSV
        pickup_filename = secure_filename(pickup_file.filename)
        pickup_path = session_folder / pickup_filename
        pickup_file.save(pickup_path)

        # Parse invoices
        parser = PDFInvoiceParser()
        invoices = []
        parse_errors = []

        for pdf_path in invoice_paths:
            try:
                invoice = parser.parse(pdf_path)
                invoices.append(invoice)
            except InvoiceParseError as e:
                parse_errors.append(str(e))

        if not invoices:
            flash(f"Could not parse any invoices. Errors: {'; '.join(parse_errors)}", "error")
            return redirect(url_for("index"))

        # Parse pickups
        csv_parser = CSVPickupParser()
        try:
            pickups = csv_parser.parse(pickup_path)
        except PickupParseError as e:
            flash(f"Could not parse pickup CSV: {e}", "error")
            return redirect(url_for("index"))

        if not pickups:
            flash("No pickup records found in CSV", "error")
            return redirect(url_for("index"))

        # Get filter options
        location_filter = request.form.get("locations", "").strip()
        month_filter = request.form.get("months", "").strip()

        location_ids = [l.strip() for l in location_filter.split(",") if l.strip()] or None
        months = [m.strip() for m in month_filter.split(",") if m.strip()] or None

        # Run reconciliation
        reconciler = InvoiceReconciler()
        summary = reconciler.reconcile(
            invoices=invoices,
            pickups=pickups,
            location_ids=location_ids,
            months=months,
        )

        # Save results for download
        results_folder = Path(RESULTS_FOLDER) / session_id
        results_folder.mkdir(parents=True, exist_ok=True)

        results_csv = results_folder / "reconciliation_results.csv"
        export_to_csv(summary, results_csv)

        unmatched_csv = results_folder / "unmatched_items.csv"
        export_unmatched_items(summary, unmatched_csv)

        # Store session info
        session["session_id"] = session_id
        session["results_ready"] = True

        # Prepare template data
        return render_template(
            "results.html",
            summary=summary,
            invoices=invoices,
            pickups=pickups,
            parse_errors=parse_errors,
            session_id=session_id,
        )

    except Exception as e:
        flash(f"Error during reconciliation: {e}", "error")
        return redirect(url_for("index"))


@app.route("/download/<session_id>/<filename>")
def download(session_id: str, filename: str):
    """Download result files."""
    # Validate filename
    allowed_files = ["reconciliation_results.csv", "unmatched_items.csv"]
    if filename not in allowed_files:
        flash("Invalid file requested", "error")
        return redirect(url_for("index"))

    filepath = Path(RESULTS_FOLDER) / session_id / filename
    if not filepath.exists():
        flash("File not found", "error")
        return redirect(url_for("index"))

    return send_file(
        filepath,
        as_attachment=True,
        download_name=filename,
    )


@app.route("/help")
def help_page():
    """Help and documentation page."""
    return render_template("help.html")


def create_app():
    """Application factory."""
    return app


def run_server(host: str = "0.0.0.0", port: int = 5000, debug: bool = False):
    """Run the Flask development server."""
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_server(debug=True)
