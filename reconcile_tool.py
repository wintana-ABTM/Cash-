"""
Invoice Reconciliation & Estimation Tool
A standalone tool for estimating and reconciling armored vendor pickup charges.

Run with: python reconcile_tool.py
"""

import json
import os
import sys
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from collections import defaultdict
import calendar

# Check for required packages
try:
    import pandas as pd
except ImportError:
    print("Installing pandas...")
    os.system(f"{sys.executable} -m pip install pandas openpyxl")
    import pandas as pd

try:
    import streamlit as st
    HAS_STREAMLIT = True
except ImportError:
    HAS_STREAMLIT = False

# ============================================================================
# VENDOR CONFIGURATION
# ============================================================================

VENDORS = {
    "Cash Man Services": {
        "vendor_id": "cashman",
        "rates": {
            "smartsafe_pickup": 46.57,
            "vault_management": 9.22,
            "branch_delivery": 25.70,
            "onetime_pickup": 125.00
        },
        "fuel_surcharge": {
            "enabled": True,
            "type": "variable",
            "base_fuel_price": 2.50,
            "current_fuel_price": 3.20,
            "rate_per_10_cents": 0.02
        },
        "location_mapping": {
            "Game Haven - Sandy - EOW": ["Game Haven Sandy", "Sandy"],
            "Game Haven - West Jordan - Monthly": ["Game Haven West Jordan", "West Jordan"],
            "Game Haven - Bountiful - On Request": ["Game Haven Bountiful", "Bountiful"],
            "Koodegras CBD - Midvale - Monthly": ["Koodegras Midvale", "Midvale"],
            "Newgate Mall": ["Newgate"],
            "Kwick Stop Odgen": ["Kwick Stop", "Ogden"]
        }
    },
    "Loomis": {
        "vendor_id": "loomis",
        "rates": {
            "pickup": 35.00,
            "emergency_pickup": 75.00
        },
        "fuel_surcharge": {
            "enabled": True,
            "type": "fixed",
            "rate": 0.10
        },
        "location_mapping": {}
    },
    "Sectran": {
        "vendor_id": "sectran",
        "rates": {
            "pickup": 42.00
        },
        "fuel_surcharge": {
            "enabled": False
        },
        "location_mapping": {}
    },
    "Brinks": {
        "vendor_id": "brinks",
        "rates": {
            "pickup": 38.50,
            "smart_safe": 45.00
        },
        "fuel_surcharge": {
            "enabled": True,
            "type": "fixed",
            "rate": 0.12
        },
        "location_mapping": {}
    },
    "Garda": {
        "vendor_id": "garda",
        "rates": {
            "pickup": 40.00,
            "vault_processing": 8.50
        },
        "fuel_surcharge": {
            "enabled": True,
            "type": "fixed",
            "rate": 0.08
        },
        "location_mapping": {}
    }
}


def get_fuel_surcharge_rate(vendor_name: str) -> float:
    """Calculate the fuel surcharge rate for a vendor."""
    vendor = VENDORS.get(vendor_name)
    if not vendor:
        return 0.0

    fs = vendor.get("fuel_surcharge", {})
    if not fs.get("enabled"):
        return 0.0

    if fs.get("type") == "fixed":
        return fs.get("rate", 0.0)
    elif fs.get("type") == "variable":
        base = fs.get("base_fuel_price", 2.50)
        current = fs.get("current_fuel_price", 3.20)
        rate_per_10 = fs.get("rate_per_10_cents", 0.02)
        increments = int((current - base) / 0.10)
        return increments * rate_per_10

    return 0.0


def get_schedule_from_location(location_name: str) -> Optional[str]:
    """Extract schedule type from location name."""
    loc_upper = location_name.upper()
    if "EOW" in loc_upper or "EVERY OTHER WEEK" in loc_upper:
        return "EOW"
    elif "WEEKLY" in loc_upper:
        return "Weekly"
    elif "MONTHLY" in loc_upper:
        return "Monthly"
    elif "ON REQUEST" in loc_upper:
        return "On Request"
    return None


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class LocationEstimate:
    """Estimate for a single location."""
    location_name: str
    schedule: Optional[str] = None
    pickup_count: int = 0
    transport_charges: float = 0.0
    vault_charges: float = 0.0
    subtotal: float = 0.0


@dataclass
class EstimationResult:
    """Complete estimation result."""
    vendor_name: str
    service_month: str
    as_of_date: date
    total_pickups: int = 0
    base_charges: float = 0.0
    fuel_surcharge: float = 0.0
    fuel_surcharge_rate: float = 0.0
    total_estimated: float = 0.0
    location_estimates: Dict[str, LocationEstimate] = field(default_factory=dict)
    charges_by_type: Dict[str, float] = field(default_factory=dict)


# ============================================================================
# ESTIMATION ENGINE
# ============================================================================

def detect_columns(df: pd.DataFrame) -> Dict[str, str]:
    """Auto-detect column names in the dataframe."""
    columns = {}

    # Date column
    for col in df.columns:
        col_str = str(col) if col is not None else ""
        col_lower = col_str.lower().replace("_", " ")
        if any(x in col_lower for x in ["pickup date", "date", "service date"]):
            columns["date"] = col
            break

    # Location column
    for col in df.columns:
        col_str = str(col) if col is not None else ""
        col_lower = col_str.lower().replace("_", " ")
        if any(x in col_lower for x in ["location", "branch", "store", "site"]):
            columns["location"] = col
            break

    # Service type column
    for col in df.columns:
        col_str = str(col) if col is not None else ""
        col_lower = col_str.lower().replace("_", " ")
        if any(x in col_lower for x in ["type", "service"]):
            columns["service_type"] = col
            break

    # Vendor column
    for col in df.columns:
        col_str = str(col) if col is not None else ""
        col_lower = col_str.lower().replace("_", " ")
        if any(x in col_lower for x in ["vendor", "carrier", "provider"]):
            columns["vendor"] = col
            break

    return columns


def parse_month_year(month_str: str) -> tuple:
    """Parse month string into (month, year) tuple."""
    month_str = month_str.strip()

    # Try "October 2025" format
    try:
        dt = datetime.strptime(month_str, "%B %Y")
        return dt.month, dt.year
    except ValueError:
        pass

    # Try "2025-10" format
    try:
        dt = datetime.strptime(month_str, "%Y-%m")
        return dt.month, dt.year
    except ValueError:
        pass

    # Try "10/2025" format
    try:
        dt = datetime.strptime(month_str, "%m/%Y")
        return dt.month, dt.year
    except ValueError:
        pass

    raise ValueError(f"Cannot parse month: {month_str}. Use 'October 2025', '2025-10', or '10/2025'")


def estimate_monthly_charges(
    tracking_data: pd.DataFrame,
    vendor_name: str,
    month_year: str,
    as_of_date: date = None
) -> EstimationResult:
    """
    Calculate expected charges for the month based on pickups.

    Args:
        tracking_data: DataFrame with pickup records
        vendor_name: Name of the vendor
        month_year: Target month (e.g., "October 2025")
        as_of_date: Calculate as-of specific date (default: today)

    Returns:
        EstimationResult with complete breakdown
    """
    vendor = VENDORS.get(vendor_name)
    if not vendor:
        raise ValueError(f"Unknown vendor: {vendor_name}. Available: {list(VENDORS.keys())}")

    # Parse month
    target_month, target_year = parse_month_year(month_year)

    if as_of_date is None:
        as_of_date = date.today()

    # Detect columns
    cols = detect_columns(tracking_data)
    if "date" not in cols or "location" not in cols:
        raise ValueError(f"Cannot detect required columns. Found: {list(tracking_data.columns)}")

    date_col = cols["date"]
    location_col = cols["location"]

    # Convert date column
    df = tracking_data.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors='coerce')

    # Filter by month
    month_start = datetime(target_year, target_month, 1)
    month_end = datetime(target_year, target_month, calendar.monthrange(target_year, target_month)[1])

    mask = (df[date_col] >= month_start) & (df[date_col] <= month_end)
    filtered_df = df[mask]

    # Initialize result
    result = EstimationResult(
        vendor_name=vendor_name,
        service_month=f"{calendar.month_name[target_month]} {target_year}",
        as_of_date=as_of_date
    )

    # Get rates
    rates = vendor["rates"]
    pickup_rate = rates.get("smartsafe_pickup", rates.get("pickup", 0))
    vault_rate = rates.get("vault_management", rates.get("vault_processing", 0))

    # Calculate by location
    total_pickups = 0
    total_transport = 0.0
    total_vault = 0.0

    for location in filtered_df[location_col].unique():
        loc_data = filtered_df[filtered_df[location_col] == location]
        pickup_count = len(loc_data)

        transport = pickup_count * pickup_rate
        vault = pickup_count * vault_rate

        estimate = LocationEstimate(
            location_name=location,
            schedule=get_schedule_from_location(location),
            pickup_count=pickup_count,
            transport_charges=transport,
            vault_charges=vault,
            subtotal=transport + vault
        )

        result.location_estimates[location] = estimate
        total_pickups += pickup_count
        total_transport += transport
        total_vault += vault

    result.total_pickups = total_pickups
    result.base_charges = total_transport + total_vault

    # Store charges by type
    result.charges_by_type["Transport/Pickup"] = total_transport
    if vault_rate > 0:
        result.charges_by_type["Vault Management"] = total_vault

    # Calculate fuel surcharge
    fuel_rate = get_fuel_surcharge_rate(vendor_name)
    result.fuel_surcharge_rate = fuel_rate
    result.fuel_surcharge = result.base_charges * fuel_rate

    result.total_estimated = result.base_charges + result.fuel_surcharge

    return result


def format_estimation_report(result: EstimationResult) -> str:
    """Format estimation result as a text report."""
    lines = []
    sep = "=" * 65

    lines.append(sep)
    lines.append("MONTHLY CHARGE ESTIMATION")
    lines.append(sep)
    lines.append(f"Vendor: {result.vendor_name}")
    lines.append(f"Service Month: {result.service_month}")
    lines.append(f"As of Date: {result.as_of_date.strftime('%B %d, %Y')}")

    lines.append("")
    lines.append("-" * 65)
    lines.append("PICKUP SUMMARY BY LOCATION")
    lines.append("-" * 65)
    lines.append(f"{'Location':<40} {'Pickups':>7} {'Subtotal':>12}")
    lines.append("-" * 65)

    for location, est in result.location_estimates.items():
        loc_display = location[:38] + ".." if len(location) > 40 else location
        schedule_str = f" ({est.schedule})" if est.schedule else ""
        lines.append(f"{loc_display}{schedule_str:<40} {est.pickup_count:>7} ${est.subtotal:>10,.2f}")

    lines.append("")
    lines.append("-" * 65)
    lines.append("CHARGE BREAKDOWN")
    lines.append("-" * 65)

    for charge_type, amount in result.charges_by_type.items():
        lines.append(f"{charge_type:<50} ${amount:>10,.2f}")

    lines.append(f"{'Subtotal':<50} ${result.base_charges:>10,.2f}")

    if result.fuel_surcharge > 0:
        lines.append(f"{'Fuel Surcharge':<40} {result.fuel_surcharge_rate*100:.1f}% ${result.fuel_surcharge:>10,.2f}")

    lines.append("-" * 65)
    lines.append(f"{'ESTIMATED TOTAL':<50} ${result.total_estimated:>10,.2f}")
    lines.append(sep)

    return "\n".join(lines)


# ============================================================================
# STREAMLIT WEB APP
# ============================================================================

def run_streamlit_app():
    """Run the Streamlit web application."""
    if not HAS_STREAMLIT:
        print("Streamlit not installed. Installing...")
        os.system(f"{sys.executable} -m pip install streamlit")
        print("Please run this script again.")
        return

    st.set_page_config(
        page_title="Invoice Reconciliation Tool",
        page_icon="💰",
        layout="wide"
    )

    st.title("Invoice Reconciliation & Estimation Tool")

    # Sidebar - Vendor Selection
    st.sidebar.header("Configuration")
    vendor_name = st.sidebar.selectbox("Select Vendor", list(VENDORS.keys()))

    vendor = VENDORS[vendor_name]

    # Show vendor info
    with st.sidebar.expander("Vendor Details"):
        st.write(f"**Rates:**")
        for key, rate in vendor["rates"].items():
            st.write(f"  {key}: ${rate:.2f}")

        fuel_rate = get_fuel_surcharge_rate(vendor_name)
        if fuel_rate > 0:
            st.write(f"**Fuel Surcharge:** {fuel_rate*100:.1f}%")

    # Mode selection
    mode = st.sidebar.radio("Mode", ["Monthly Estimation", "Invoice Reconciliation", "Compare Vendors"])

    # Main content
    if mode == "Invoice Reconciliation":
        st.header("Invoice Reconciliation")
        st.markdown("Upload an invoice PDF to extract and analyze charges.")

        # Month selection
        col1, col2 = st.columns(2)
        with col1:
            month = st.selectbox(
                "Service Month",
                range(1, 13),
                index=datetime.now().month - 2 if datetime.now().month > 1 else 11,
                format_func=lambda x: datetime(2000, x, 1).strftime("%B"),
                key="recon_month"
            )
        with col2:
            year = st.selectbox("Year", range(2024, 2027), index=1, key="recon_year")

        service_month = f"{datetime(year, month, 1).strftime('%B')} {year}"

        # PDF Upload
        st.subheader("Upload Invoice PDF")
        pdf_file = st.file_uploader(
            "Upload Invoice PDF",
            type=["pdf"],
            help="Upload the vendor invoice PDF file"
        )

        if pdf_file:
            st.success(f"Uploaded: {pdf_file.name}")

            # Try to parse the PDF
            try:
                import pdfplumber
            except ImportError:
                st.warning("Installing pdfplumber...")
                os.system(f"{sys.executable} -m pip install pdfplumber")
                import pdfplumber

            # Save uploaded file temporarily
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(pdf_file.read())
                tmp_path = tmp.name

            try:
                with pdfplumber.open(tmp_path) as pdf:
                    st.subheader("Invoice Contents")

                    full_text = ""
                    for i, page in enumerate(pdf.pages):
                        text = page.extract_text() or ""
                        full_text += text + "\n"

                        # Show tables if found
                        tables = page.extract_tables()
                        if tables:
                            for j, table in enumerate(tables):
                                if table and len(table) > 1:
                                    st.write(f"**Table {j+1} (Page {i+1}):**")
                                    try:
                                        table_df = pd.DataFrame(table[1:], columns=table[0] if table[0] else None)
                                        st.dataframe(table_df, use_container_width=True)
                                    except:
                                        st.dataframe(pd.DataFrame(table), use_container_width=True)

                    # Show extracted text
                    with st.expander("Raw Text from PDF"):
                        st.text(full_text[:5000] + "..." if len(full_text) > 5000 else full_text)

                    # Extract key info
                    st.subheader("Extracted Information")

                    # Try to find invoice number
                    import re
                    inv_match = re.search(r'(?:Invoice|INV)[#:\s]*(\d+)', full_text, re.IGNORECASE)
                    if inv_match:
                        st.write(f"**Invoice Number:** {inv_match.group(1)}")

                    # Try to find total
                    total_matches = re.findall(r'(?:Total|Amount Due|Grand Total)[:\s]*\$?([\d,]+\.?\d*)', full_text, re.IGNORECASE)
                    if total_matches:
                        st.write(f"**Total Amount:** ${total_matches[-1]}")

                    # Try to find dates
                    date_matches = re.findall(r'\d{1,2}/\d{1,2}/\d{2,4}', full_text)
                    if date_matches:
                        st.write(f"**Dates Found:** {', '.join(date_matches[:5])}")

                    # Show pickup tracking upload for comparison
                    st.subheader("Compare with Tracking Data (Optional)")
                    tracking_file = st.file_uploader(
                        "Upload Tracking Excel/CSV",
                        type=["xlsx", "xls", "csv"],
                        key="recon_tracking"
                    )

                    if tracking_file:
                        try:
                            if tracking_file.name.endswith('.csv'):
                                tracking_df = pd.read_csv(tracking_file)
                            else:
                                excel = pd.ExcelFile(tracking_file)
                                sheet_name = None
                                for s in excel.sheet_names:
                                    if 'pickup' in s.lower() or 'recon' in s.lower():
                                        sheet_name = s
                                        break
                                tracking_df = pd.read_excel(tracking_file, sheet_name=sheet_name or 0)

                            st.success(f"Loaded {len(tracking_df)} tracking records")

                            if st.button("Compare Invoice to Tracking", type="primary"):
                                # Generate estimation from tracking
                                result = estimate_monthly_charges(tracking_df, vendor_name, service_month)

                                st.subheader("Comparison Results")

                                col1, col2 = st.columns(2)
                                with col1:
                                    st.write("**From Tracking Data (Expected):**")
                                    st.metric("Total Pickups", result.total_pickups)
                                    st.metric("Expected Total", f"${result.total_estimated:,.2f}")

                                with col2:
                                    st.write("**From Invoice:**")
                                    if total_matches:
                                        invoice_total = float(total_matches[-1].replace(",", ""))
                                        st.metric("Invoice Total", f"${invoice_total:,.2f}")
                                        variance = invoice_total - result.total_estimated
                                        st.metric("Variance", f"${variance:,.2f}",
                                                delta=f"${variance:,.2f}",
                                                delta_color="inverse" if variance > 0 else "normal")
                                    else:
                                        st.write("Could not extract total from invoice")

                                # Show breakdown
                                st.subheader("Expected Charges by Location")
                                loc_data = []
                                for loc, est in result.location_estimates.items():
                                    loc_data.append({
                                        "Location": loc,
                                        "Pickups": est.pickup_count,
                                        "Subtotal": f"${est.subtotal:,.2f}"
                                    })
                                st.dataframe(pd.DataFrame(loc_data), use_container_width=True)

                        except Exception as e:
                            st.error(f"Error loading tracking file: {str(e)}")

            except Exception as e:
                st.error(f"Error reading PDF: {str(e)}")
            finally:
                # Clean up temp file
                try:
                    os.unlink(tmp_path)
                except:
                    pass

    elif mode == "Monthly Estimation":
        st.header("Monthly Charge Estimation")

        # Month selection
        col1, col2 = st.columns(2)
        with col1:
            month = st.selectbox(
                "Month",
                range(1, 13),
                index=datetime.now().month - 1,
                format_func=lambda x: datetime(2000, x, 1).strftime("%B")
            )
        with col2:
            year = st.selectbox("Year", range(2024, 2027), index=1)

        service_month = f"{datetime(year, month, 1).strftime('%B')} {year}"

        # File upload
        st.subheader("Upload Tracking Data")
        uploaded_file = st.file_uploader(
            "Upload Excel or CSV file",
            type=["xlsx", "xls", "csv"],
            help="Upload your pickup tracking file"
        )

        if uploaded_file:
            # Load data
            try:
                if uploaded_file.name.endswith('.csv'):
                    df = pd.read_csv(uploaded_file)
                else:
                    # Try to find pickup sheet
                    excel = pd.ExcelFile(uploaded_file)
                    sheet_name = None
                    for s in excel.sheet_names:
                        if 'pickup' in s.lower() or 'recon' in s.lower():
                            sheet_name = s
                            break
                    df = pd.read_excel(uploaded_file, sheet_name=sheet_name or 0)

                st.success(f"Loaded {len(df)} records")

                with st.expander("Preview Data"):
                    st.dataframe(df.head(10))

                if st.button("Generate Estimation", type="primary"):
                    try:
                        result = estimate_monthly_charges(df, vendor_name, service_month)

                        # Display results
                        st.subheader("Estimation Results")

                        # Metrics
                        col1, col2, col3, col4 = st.columns(4)
                        col1.metric("Total Pickups", result.total_pickups)
                        col2.metric("Base Charges", f"${result.base_charges:,.2f}")
                        col3.metric("Fuel Surcharge", f"${result.fuel_surcharge:,.2f}")
                        col4.metric("Total Estimated", f"${result.total_estimated:,.2f}")

                        # Location breakdown
                        st.subheader("By Location")
                        loc_data = []
                        for loc, est in result.location_estimates.items():
                            loc_data.append({
                                "Location": loc,
                                "Schedule": est.schedule or "N/A",
                                "Pickups": est.pickup_count,
                                "Transport": f"${est.transport_charges:,.2f}",
                                "Vault": f"${est.vault_charges:,.2f}",
                                "Subtotal": f"${est.subtotal:,.2f}"
                            })
                        st.dataframe(pd.DataFrame(loc_data), use_container_width=True)

                        # Full report
                        with st.expander("Full Text Report"):
                            st.code(format_estimation_report(result))

                    except Exception as e:
                        st.error(f"Error: {str(e)}")

            except Exception as e:
                st.error(f"Error loading file: {str(e)}")

    elif mode == "Compare Vendors":
        st.header("Vendor Cost Comparison")

        # Month selection
        col1, col2 = st.columns(2)
        with col1:
            month = st.selectbox(
                "Month",
                range(1, 13),
                index=datetime.now().month - 1,
                format_func=lambda x: datetime(2000, x, 1).strftime("%B"),
                key="compare_month"
            )
        with col2:
            year = st.selectbox("Year", range(2024, 2027), index=1, key="compare_year")

        service_month = f"{datetime(year, month, 1).strftime('%B')} {year}"

        # Vendor selection
        selected_vendors = st.multiselect(
            "Select Vendors to Compare",
            list(VENDORS.keys()),
            default=list(VENDORS.keys())[:3]
        )

        # File upload
        uploaded_file = st.file_uploader(
            "Upload Tracking Data",
            type=["xlsx", "xls", "csv"],
            key="compare_upload"
        )

        if uploaded_file and selected_vendors:
            try:
                if uploaded_file.name.endswith('.csv'):
                    df = pd.read_csv(uploaded_file)
                else:
                    excel = pd.ExcelFile(uploaded_file)
                    sheet_name = None
                    for s in excel.sheet_names:
                        if 'pickup' in s.lower() or 'recon' in s.lower():
                            sheet_name = s
                            break
                    df = pd.read_excel(uploaded_file, sheet_name=sheet_name or 0)

                if st.button("Compare Costs", type="primary"):
                    results = []
                    for v in selected_vendors:
                        try:
                            r = estimate_monthly_charges(df, v, service_month)
                            results.append({
                                "Vendor": v,
                                "Pickups": r.total_pickups,
                                "Base Charges": r.base_charges,
                                "Fuel Surcharge": r.fuel_surcharge,
                                "Total": r.total_estimated
                            })
                        except Exception as e:
                            st.warning(f"Could not calculate for {v}: {e}")

                    if results:
                        results_df = pd.DataFrame(results)

                        # Find best
                        min_total = results_df["Total"].min()
                        results_df["vs Best"] = results_df["Total"] - min_total

                        # Format
                        for col in ["Base Charges", "Fuel Surcharge", "Total", "vs Best"]:
                            results_df[col] = results_df[col].apply(lambda x: f"${x:,.2f}")

                        st.dataframe(results_df, use_container_width=True)

                        # Best vendor
                        best = [r for r in results if r["Total"] == min_total][0]
                        st.success(f"Best Rate: **{best['Vendor']}** at ${min_total:,.2f}")

            except Exception as e:
                st.error(f"Error: {str(e)}")


# ============================================================================
# COMMAND LINE INTERFACE
# ============================================================================

def run_cli():
    """Run the command-line interface."""
    print("\n" + "=" * 60)
    print("INVOICE RECONCILIATION & ESTIMATION TOOL")
    print("=" * 60)

    # Select mode
    print("\nSelect Mode:")
    print("1. Monthly Estimation")
    print("2. Compare Vendors")
    print("3. Launch Web Dashboard")
    print("4. Exit")

    choice = input("\nChoice (1-4): ").strip()

    if choice == "1":
        run_estimation_cli()
    elif choice == "2":
        run_comparison_cli()
    elif choice == "3":
        print("\nLaunching web dashboard...")
        print("Open http://localhost:8501 in your browser")
        os.system(f"{sys.executable} -m streamlit run {__file__}")
    elif choice == "4":
        print("Goodbye!")
        sys.exit(0)
    else:
        print("Invalid choice")
        run_cli()


def run_estimation_cli():
    """Run estimation from CLI."""
    print("\n" + "-" * 60)
    print("MONTHLY ESTIMATION")
    print("-" * 60)

    # Select vendor
    print("\nAvailable Vendors:")
    vendors = list(VENDORS.keys())
    for i, v in enumerate(vendors, 1):
        fuel = get_fuel_surcharge_rate(v)
        fuel_str = f" (Fuel: {fuel*100:.1f}%)" if fuel > 0 else ""
        print(f"  {i}. {v}{fuel_str}")

    vendor_choice = int(input("\nSelect vendor (number): ").strip()) - 1
    vendor_name = vendors[vendor_choice]

    # Get month
    month_str = input("Service month (e.g., October 2025): ").strip()

    # Get file path
    file_path = input("Path to tracking file (Excel/CSV): ").strip()

    # Load data
    try:
        if file_path.endswith('.csv'):
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)

        print(f"\nLoaded {len(df)} records")

        # Generate estimation
        result = estimate_monthly_charges(df, vendor_name, month_str)

        print("\n" + format_estimation_report(result))

    except Exception as e:
        print(f"\nError: {e}")

    input("\nPress Enter to continue...")
    run_cli()


def run_comparison_cli():
    """Run vendor comparison from CLI."""
    print("\n" + "-" * 60)
    print("VENDOR COMPARISON")
    print("-" * 60)

    # Get month
    month_str = input("Service month (e.g., October 2025): ").strip()

    # Get file path
    file_path = input("Path to tracking file (Excel/CSV): ").strip()

    try:
        if file_path.endswith('.csv'):
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)

        print(f"\nLoaded {len(df)} records")
        print("\nCalculating costs for all vendors...\n")

        print(f"{'Vendor':<25} {'Pickups':>8} {'Base':>12} {'Fuel':>10} {'Total':>12}")
        print("-" * 70)

        results = []
        for vendor_name in VENDORS.keys():
            try:
                result = estimate_monthly_charges(df, vendor_name, month_str)
                results.append((vendor_name, result.total_estimated))
                print(f"{vendor_name:<25} {result.total_pickups:>8} "
                      f"${result.base_charges:>10,.2f} ${result.fuel_surcharge:>8,.2f} "
                      f"${result.total_estimated:>10,.2f}")
            except Exception as e:
                print(f"{vendor_name:<25} Error: {e}")

        if results:
            results.sort(key=lambda x: x[1])
            print(f"\nBest Rate: {results[0][0]} at ${results[0][1]:,.2f}")
            if len(results) > 1:
                savings = results[-1][1] - results[0][1]
                print(f"Potential Savings: ${savings:,.2f}/month")

    except Exception as e:
        print(f"\nError: {e}")

    input("\nPress Enter to continue...")
    run_cli()


# ============================================================================
# MAIN
# ============================================================================

# Check if running under Streamlit
def is_running_in_streamlit():
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except:
        return False

# Auto-run Streamlit app if we're in Streamlit context
if is_running_in_streamlit():
    run_streamlit_app()
elif __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "web":
        run_streamlit_app()
    elif len(sys.argv) > 1 and sys.argv[1] == "cli":
        run_cli()
    else:
        print("\nInvoice Reconciliation & Estimation Tool")
        print("-" * 40)
        print("\nUsage:")
        print("  python reconcile_tool.py web   - Launch web dashboard")
        print("  python reconcile_tool.py cli   - Run command-line interface")
        print("\nOr run with Streamlit directly:")
        print("  streamlit run reconcile_tool.py")
        print()

        # Default to CLI
        choice = input("Launch web dashboard? (y/n): ").strip().lower()
        if choice == 'y':
            print("\nLaunching web dashboard...")
            os.system(f"{sys.executable} -m streamlit run {__file__}")
        else:
            run_cli()
