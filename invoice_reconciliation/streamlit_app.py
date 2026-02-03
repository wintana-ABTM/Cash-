"""
Streamlit Web Dashboard for Invoice Reconciliation & Estimation

Provides an interactive web interface for:
- Vendor selection
- Monthly charge estimation
- Invoice reconciliation
- Multi-vendor comparison
"""

import streamlit as st
import pandas as pd
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
import tempfile
import os
from io import BytesIO

from .vendor_config import VendorConfigManager, VendorConfig, get_vendor_manager
from .estimation import (
    EstimationEngine, EstimationResult,
    format_estimation_report, export_estimation_to_excel
)
from .vendor_reconciler import (
    VendorReconciler, ReconciliationResult,
    format_reconciliation_report, export_reconciliation_to_excel
)


def load_css():
    """Load custom CSS styles."""
    st.markdown("""
    <style>
    .big-font {
        font-size: 24px !important;
        font-weight: bold;
    }
    .metric-card {
        background-color: #f0f2f6;
        border-radius: 10px;
        padding: 20px;
        margin: 10px 0;
    }
    .match-badge {
        background-color: #28a745;
        color: white;
        padding: 5px 10px;
        border-radius: 5px;
    }
    .warning-badge {
        background-color: #ffc107;
        color: black;
        padding: 5px 10px;
        border-radius: 5px;
    }
    .error-badge {
        background-color: #dc3545;
        color: white;
        padding: 5px 10px;
        border-radius: 5px;
    }
    </style>
    """, unsafe_allow_html=True)


def get_vendor_manager_cached():
    """Get cached vendor manager."""
    if 'vendor_manager' not in st.session_state:
        st.session_state.vendor_manager = get_vendor_manager()
    return st.session_state.vendor_manager


def parse_tracking_data(uploaded_file) -> pd.DataFrame:
    """Parse uploaded tracking data file."""
    if uploaded_file is None:
        return None

    filename = uploaded_file.name.lower()

    try:
        if filename.endswith('.csv'):
            df = pd.read_csv(uploaded_file)
        elif filename.endswith(('.xlsx', '.xls')):
            # Try to find the right sheet
            excel_file = pd.ExcelFile(uploaded_file)
            sheet_names = excel_file.sheet_names

            # Look for pickup recon sheet
            pickup_sheet = None
            for sheet in sheet_names:
                if 'pickup' in sheet.lower() or 'recon' in sheet.lower():
                    pickup_sheet = sheet
                    break

            if pickup_sheet:
                df = pd.read_excel(uploaded_file, sheet_name=pickup_sheet)
            else:
                # Use first sheet
                df = pd.read_excel(uploaded_file, sheet_name=0)
        else:
            st.error(f"Unsupported file format: {filename}")
            return None

        return df

    except Exception as e:
        st.error(f"Error reading file: {str(e)}")
        return None


def render_vendor_selection():
    """Render vendor selection sidebar."""
    st.sidebar.header("Configuration")

    vendor_manager = get_vendor_manager_cached()
    vendors = vendor_manager.list_vendors()

    selected_vendor_name = st.sidebar.selectbox(
        "Select Vendor",
        vendors,
        help="Choose the armored carrier vendor"
    )

    vendor = vendor_manager.get_vendor(selected_vendor_name)

    # Show vendor info
    with st.sidebar.expander("Vendor Details"):
        st.write(f"**Vendor ID:** {vendor.vendor_id}")
        st.write(f"**Pricing Model:** {vendor.pricing_model}")

        st.write("**Rates:**")
        for key, rate in vendor.rates.items():
            st.write(f"  - {key}: ${rate:.2f}")

        if vendor.fuel_surcharge.enabled:
            fuel_rate = vendor.get_fuel_surcharge_rate()
            st.write(f"**Fuel Surcharge:** {fuel_rate*100:.1f}%")

    return vendor


def render_month_selection():
    """Render month selection."""
    # Default to last month
    today = date.today()
    last_month = today - relativedelta(months=1)

    col1, col2 = st.columns(2)

    with col1:
        month = st.selectbox(
            "Service Month",
            range(1, 13),
            index=last_month.month - 1,
            format_func=lambda x: datetime(2000, x, 1).strftime("%B")
        )

    with col2:
        year = st.selectbox(
            "Year",
            range(today.year - 2, today.year + 1),
            index=2  # Current year
        )

    return f"{datetime(year, month, 1).strftime('%B')} {year}"


def render_estimation_mode(vendor: VendorConfig):
    """Render the estimation mode interface."""
    st.header("Monthly Charge Estimation")

    st.markdown("""
    Upload your pickup tracking data to estimate monthly charges in real-time.
    The system will calculate expected charges based on the vendor's pricing structure.
    """)

    # Month selection
    service_month = render_month_selection()

    # As-of date
    as_of_date = st.date_input(
        "As of Date",
        value=date.today(),
        help="Calculate charges as of this date (for mid-month estimates)"
    )

    # File upload
    st.subheader("Upload Tracking Data")
    tracking_file = st.file_uploader(
        "Upload Pickup Tracking Excel/CSV",
        type=['xlsx', 'xls', 'csv'],
        help="Upload your pickup reconciliation tracking file"
    )

    if tracking_file:
        tracking_data = parse_tracking_data(tracking_file)

        if tracking_data is not None:
            st.success(f"Loaded {len(tracking_data)} records")

            # Preview data
            with st.expander("Preview Data"):
                st.dataframe(tracking_data.head(10))

            if st.button("Generate Estimation", type="primary"):
                with st.spinner("Calculating estimates..."):
                    try:
                        engine = EstimationEngine(vendor)
                        result = engine.estimate_monthly_charges(
                            tracking_data,
                            service_month,
                            as_of_date
                        )

                        render_estimation_results(result)

                    except Exception as e:
                        st.error(f"Error generating estimation: {str(e)}")
                        st.exception(e)


def render_estimation_results(result: EstimationResult):
    """Render estimation results."""
    st.success("Estimation Complete!")

    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Pickups", result.total_pickups)

    with col2:
        st.metric("Total Deliveries", result.total_deliveries)

    with col3:
        st.metric("Base Charges", f"${result.base_charges:,.2f}")

    with col4:
        st.metric("Estimated Total", f"${result.total_estimated:,.2f}")

    # Month status
    if result.projection:
        pct = result.projection.percent_complete
        st.progress(pct / 100, text=f"Month {pct:.0f}% complete ({result.projection.days_elapsed}/{result.projection.days_total} days)")

    # Tabs for different views
    tab1, tab2, tab3, tab4 = st.tabs(["Summary", "By Location", "Charges", "Projection"])

    with tab1:
        st.subheader("Charge Summary")

        # Charge breakdown table
        charge_data = []
        for charge in result.charge_breakdown:
            charge_data.append({
                "Description": charge.description,
                "Quantity": charge.quantity,
                "Rate": f"${charge.rate:.2f}" if charge.rate_key != "fuel_surcharge" else f"{charge.rate*100:.0f}%",
                "Amount": f"${charge.amount:,.2f}"
            })

        st.table(pd.DataFrame(charge_data))

        st.markdown(f"**ESTIMATED TOTAL: ${result.total_estimated:,.2f}**")

    with tab2:
        st.subheader("By Location")

        loc_data = []
        for location, estimate in result.location_estimates.items():
            loc_data.append({
                "Location": location,
                "Schedule": estimate.schedule or "Unknown",
                "Pickups": estimate.pickup_count,
                "Transport": f"${estimate.transport_charges:,.2f}",
                "Vault": f"${estimate.vault_charges:,.2f}",
                "Subtotal": f"${estimate.subtotal:,.2f}"
            })

        st.dataframe(pd.DataFrame(loc_data), use_container_width=True)

    with tab3:
        st.subheader("Detailed Charges")

        # Fuel surcharge details
        if result.fuel_surcharge > 0:
            st.info(f"Fuel Surcharge: ${result.fuel_surcharge:,.2f}")

        # Full text report
        with st.expander("Full Text Report"):
            st.code(format_estimation_report(result))

    with tab4:
        st.subheader("Month-End Projection")

        if result.projection:
            col1, col2 = st.columns(2)

            with col1:
                st.metric("Days Remaining", result.projection.days_remaining)
                st.metric(
                    "Projected Additional (Min)",
                    f"${result.projection.projected_additional_min:,.2f}"
                )

            with col2:
                st.metric(
                    "Projected Total (Min)",
                    f"${result.projection.projected_total_min:,.2f}"
                )
                st.metric(
                    "Projected Total (Max)",
                    f"${result.projection.projected_total_max:,.2f}"
                )

            st.subheader("Location Projections")
            for location, proj in result.projection.location_projections.items():
                if proj["expected_remaining"] > 0 or proj["possible_remaining"] > 0:
                    estimate = result.location_estimates.get(location)
                    schedule = estimate.schedule if estimate else "Unknown"
                    st.write(f"- **{location}** ({schedule}): {proj['expected_remaining']}-{proj['possible_remaining']} more pickups expected")

        else:
            st.info("Month is complete - no projection available")

    # Alerts
    if result.alerts:
        st.subheader("Alerts")
        for alert in result.alerts:
            if alert.severity == "warning":
                st.warning(f"{alert.message}")
            elif alert.severity == "error":
                st.error(f"{alert.message}")
            else:
                st.info(f"{alert.message}")

    # Download buttons
    st.subheader("Export")
    col1, col2 = st.columns(2)

    with col1:
        # Text report download
        report_text = format_estimation_report(result)
        st.download_button(
            "Download Text Report",
            report_text,
            file_name=f"estimation_{result.service_month.replace(' ', '_')}.txt",
            mime="text/plain"
        )

    with col2:
        # Excel download
        excel_buffer = BytesIO()
        export_estimation_to_excel(result, excel_buffer)
        excel_buffer.seek(0)

        st.download_button(
            "Download Excel Report",
            excel_buffer,
            file_name=f"estimation_{result.service_month.replace(' ', '_')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    # Store result in session for reconciliation
    st.session_state.last_estimation = result


def render_reconciliation_mode(vendor: VendorConfig):
    """Render the reconciliation mode interface."""
    st.header("Invoice Reconciliation")

    st.markdown("""
    Upload an invoice PDF and tracking data to reconcile charges against vendor pricing rules.
    The system will compare invoice line items to expected charges based on pickup records.
    """)

    # Month selection
    service_month = render_month_selection()

    # File uploads
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Invoice PDF")
        invoice_file = st.file_uploader(
            "Upload Invoice",
            type=['pdf'],
            help="Upload the vendor invoice PDF"
        )

    with col2:
        st.subheader("Tracking Data")
        tracking_file = st.file_uploader(
            "Upload Tracking Excel/CSV",
            type=['xlsx', 'xls', 'csv'],
            key="recon_tracking",
            help="Upload your pickup reconciliation tracking file"
        )

    # Check for previous estimation
    use_previous = False
    if 'last_estimation' in st.session_state:
        prev_est = st.session_state.last_estimation
        if prev_est.vendor_name == vendor.name:
            use_previous = st.checkbox(
                f"Use previous estimation ({prev_est.service_month})",
                value=True
            )

    if invoice_file and (tracking_file or use_previous):
        if st.button("Reconcile Invoice", type="primary"):
            with st.spinner("Processing..."):
                try:
                    # Save PDF to temp file
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                        tmp.write(invoice_file.read())
                        pdf_path = tmp.name

                    try:
                        reconciler = VendorReconciler(vendor)

                        if use_previous and 'last_estimation' in st.session_state:
                            result = reconciler.reconcile_with_estimation(
                                pdf_path,
                                st.session_state.last_estimation
                            )
                        else:
                            tracking_data = parse_tracking_data(tracking_file)
                            if tracking_data is None:
                                st.error("Failed to parse tracking data")
                                return

                            result = reconciler.reconcile(
                                pdf_path,
                                tracking_data,
                                service_month
                            )

                        render_reconciliation_results(result)

                    finally:
                        # Clean up temp file
                        os.unlink(pdf_path)

                except Exception as e:
                    st.error(f"Error during reconciliation: {str(e)}")
                    st.exception(e)


def render_reconciliation_results(result: ReconciliationResult):
    """Render reconciliation results."""
    # Status banner
    if result.status == "PERFECT_MATCH":
        st.success("PERFECT MATCH - Invoice reconciled successfully!")
    elif result.status == "MINOR_VARIANCE":
        st.warning(f"MINOR VARIANCE - ${abs(result.variance):.2f} difference")
    else:
        st.error(f"DISCREPANCY - ${abs(result.variance):.2f} requires review")

    # Summary metrics
    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("Invoice Total", f"${result.invoice_total:,.2f}")

    with col2:
        st.metric("Expected Total", f"${result.expected_total:,.2f}")

    with col3:
        delta_color = "normal" if result.variance >= 0 else "inverse"
        st.metric(
            "Variance",
            f"${result.variance:,.2f}",
            delta=f"${result.variance:,.2f}",
            delta_color=delta_color
        )

    # Tabs
    tab1, tab2, tab3, tab4 = st.tabs(["Comparison", "By Location", "Discrepancies", "Rate Validation"])

    with tab1:
        st.subheader("Line-by-Line Comparison")

        comp_df = pd.DataFrame(result.comparison_table)
        if not comp_df.empty:
            # Format currency columns
            for col in ['estimated_amount', 'invoiced_amount', 'amount_variance']:
                if col in comp_df.columns:
                    comp_df[col] = comp_df[col].apply(lambda x: f"${x:,.2f}" if pd.notna(x) else "")

            st.dataframe(comp_df, use_container_width=True)

    with tab2:
        st.subheader("Location Analysis")

        loc_df = pd.DataFrame(result.location_analysis)
        if not loc_df.empty:
            st.dataframe(loc_df, use_container_width=True)

            # Summary
            matched = sum(1 for loc in result.location_analysis if "MATCH" in loc.get("status", ""))
            total = len(result.location_analysis)
            st.info(f"{matched}/{total} locations matched")

    with tab3:
        st.subheader("Discrepancies")

        if result.discrepancies:
            for disc in result.discrepancies:
                with st.expander(f"{disc.service_type} - {disc.discrepancy_type}"):
                    st.write(f"**Location:** {disc.location or 'N/A'}")
                    st.write(f"**Expected:** {disc.expected_value}")
                    st.write(f"**Actual:** {disc.actual_value}")
                    st.write(f"**Impact:** ${disc.impact:,.2f}")
                    st.warning(f"**Recommended Action:** {disc.recommended_action}")
        else:
            st.success("No discrepancies found!")

    with tab4:
        st.subheader("Rate Validation")

        if result.rate_validations:
            rate_df = pd.DataFrame(result.rate_validations)
            st.dataframe(rate_df, use_container_width=True)

            valid_count = sum(1 for v in result.rate_validations if v.get("valid") == "✓")
            total_count = len(result.rate_validations)

            if valid_count == total_count:
                st.success(f"All {total_count} rates validated successfully!")
            else:
                st.warning(f"{valid_count}/{total_count} rates valid")

        # Fuel surcharge validation
        st.subheader("Fuel Surcharge")
        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric("Expected", f"${result.fuel_surcharge_expected:,.2f}")
        with col2:
            st.metric("Actual", f"${result.fuel_surcharge_actual:,.2f}")
        with col3:
            st.metric("Variance", f"${result.fuel_surcharge_variance:,.2f}")

    # Export
    st.subheader("Export")
    col1, col2 = st.columns(2)

    with col1:
        report_text = format_reconciliation_report(result)
        st.download_button(
            "Download Text Report",
            report_text,
            file_name=f"reconciliation_{result.invoice_number}.txt",
            mime="text/plain"
        )

    with col2:
        excel_buffer = BytesIO()
        export_reconciliation_to_excel(result, excel_buffer)
        excel_buffer.seek(0)

        st.download_button(
            "Download Excel Report",
            excel_buffer,
            file_name=f"reconciliation_{result.invoice_number}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )


def render_comparison_mode(vendor: VendorConfig):
    """Render multi-vendor comparison mode."""
    st.header("Multi-Vendor Cost Comparison")

    st.markdown("""
    Compare what different vendors would charge for the same pickup services.
    Upload your tracking data to see cost differences across vendors.
    """)

    # Month selection
    service_month = render_month_selection()

    # File upload
    tracking_file = st.file_uploader(
        "Upload Tracking Data",
        type=['xlsx', 'xls', 'csv'],
        key="compare_tracking"
    )

    vendor_manager = get_vendor_manager_cached()

    # Vendor selection for comparison
    all_vendors = vendor_manager.list_vendors()
    selected_vendors = st.multiselect(
        "Select Vendors to Compare",
        all_vendors,
        default=all_vendors[:3]
    )

    if tracking_file and selected_vendors and st.button("Compare Costs", type="primary"):
        tracking_data = parse_tracking_data(tracking_file)

        if tracking_data is not None:
            with st.spinner("Calculating costs for each vendor..."):
                results = {}

                for vendor_name in selected_vendors:
                    vendor_config = vendor_manager.get_vendor(vendor_name)
                    engine = EstimationEngine(vendor_config)

                    try:
                        result = engine.estimate_monthly_charges(
                            tracking_data,
                            service_month
                        )
                        results[vendor_name] = result
                    except Exception as e:
                        st.warning(f"Could not calculate for {vendor_name}: {str(e)}")

                if results:
                    render_comparison_results(results, service_month)


def render_comparison_results(results: Dict[str, EstimationResult], service_month: str):
    """Render vendor comparison results."""
    st.subheader(f"Cost Comparison - {service_month}")

    # Summary table
    summary_data = []
    for vendor_name, result in results.items():
        summary_data.append({
            "Vendor": vendor_name,
            "Total Pickups": result.total_pickups,
            "Base Charges": result.base_charges,
            "Fuel Surcharge": result.fuel_surcharge,
            "Total": result.total_estimated
        })

    summary_df = pd.DataFrame(summary_data)

    # Find best rate
    min_total = summary_df["Total"].min()
    summary_df["vs Best"] = summary_df["Total"] - min_total
    summary_df["Best Rate"] = summary_df["Total"] == min_total

    # Format columns
    for col in ["Base Charges", "Fuel Surcharge", "Total", "vs Best"]:
        summary_df[col] = summary_df[col].apply(lambda x: f"${x:,.2f}")

    st.dataframe(summary_df, use_container_width=True)

    # Chart
    chart_data = pd.DataFrame({
        "Vendor": [r["Vendor"] for r in summary_data],
        "Total Cost": [r["Total"] for r in summary_data]
    })

    st.bar_chart(chart_data.set_index("Vendor"))

    # Savings calculation
    totals = [r["Total"] for r in summary_data]
    if len(totals) >= 2:
        sorted_totals = sorted(totals)
        potential_savings = sorted_totals[-1] - sorted_totals[0]

        best_vendor = [r["Vendor"] for r in summary_data if r["Total"] == sorted_totals[0]][0]
        worst_vendor = [r["Vendor"] for r in summary_data if r["Total"] == sorted_totals[-1]][0]

        st.info(f"Potential monthly savings: **${potential_savings:,.2f}** by switching from {worst_vendor} to {best_vendor}")


def render_settings():
    """Render settings page."""
    st.header("Settings")

    vendor_manager = get_vendor_manager_cached()

    st.subheader("Update Fuel Prices")

    vendor_name = st.selectbox(
        "Select Vendor",
        vendor_manager.list_vendors(),
        key="settings_vendor"
    )

    vendor = vendor_manager.get_vendor(vendor_name)

    if vendor.fuel_surcharge.enabled and vendor.fuel_surcharge.surcharge_type == "percentage_variable":
        col1, col2 = st.columns(2)

        with col1:
            st.write(f"**Base Fuel Price:** ${vendor.fuel_surcharge.base_fuel_price:.2f}")
            st.write(f"**Current Fuel Price:** ${vendor.fuel_surcharge.current_fuel_price:.2f}")
            st.write(f"**Current Surcharge Rate:** {vendor.get_fuel_surcharge_rate()*100:.1f}%")

        with col2:
            new_price = st.number_input(
                "New Fuel Price",
                value=vendor.fuel_surcharge.current_fuel_price,
                min_value=0.0,
                step=0.10
            )

            if st.button("Update Fuel Price"):
                vendor_manager.update_fuel_price(vendor_name, new_price)
                st.success(f"Updated fuel price to ${new_price:.2f}")
                st.rerun()

    else:
        st.info(f"{vendor_name} uses a fixed fuel surcharge rate of {vendor.fuel_surcharge.rate*100:.1f}%")

    # Update rates
    st.subheader("Update Service Rates")

    rate_key = st.selectbox(
        "Select Rate",
        list(vendor.rates.keys()),
        key="settings_rate"
    )

    current_rate = vendor.rates[rate_key]
    new_rate = st.number_input(
        f"New Rate for {rate_key}",
        value=current_rate,
        min_value=0.0,
        step=1.0
    )

    if st.button("Update Rate"):
        vendor_manager.update_rate(vendor_name, rate_key, new_rate)
        st.success(f"Updated {rate_key} to ${new_rate:.2f}")
        st.rerun()


def main():
    """Main Streamlit application."""
    st.set_page_config(
        page_title="Invoice Reconciliation System",
        page_icon="💰",
        layout="wide"
    )

    load_css()

    st.title("Invoice Reconciliation & Estimation System")

    # Sidebar
    vendor = render_vendor_selection()

    mode = st.sidebar.radio(
        "Select Mode",
        ["Monthly Estimation", "Invoice Reconciliation", "Vendor Comparison", "Settings"]
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Quick Links")
    st.sidebar.markdown("- [Help Documentation](#)")
    st.sidebar.markdown("- [Vendor Configuration](#)")

    # Main content
    if mode == "Monthly Estimation":
        render_estimation_mode(vendor)

    elif mode == "Invoice Reconciliation":
        render_reconciliation_mode(vendor)

    elif mode == "Vendor Comparison":
        render_comparison_mode(vendor)

    elif mode == "Settings":
        render_settings()


if __name__ == "__main__":
    main()
