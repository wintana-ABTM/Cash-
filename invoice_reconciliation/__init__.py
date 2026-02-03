"""
Invoice Reconciliation & Estimation System

A dual-purpose tool for:
- Estimating monthly charges in real-time as pickups occur
- Reconciling actual invoices against tracked pickups using vendor-specific pricing rules

Supports multiple vendors including Cash Man Services, Loomis, Sectran, Brinks, and Garda.
"""

__version__ = "2.0.0"

# Vendor Configuration
from .vendor_config import (
    VendorConfig,
    VendorConfigManager,
    FuelSurchargeConfig,
    BillingComponent,
    load_vendor_config,
    get_vendor_manager,
    select_vendor_interactive
)

# Estimation Engine
from .estimation import (
    EstimationEngine,
    EstimationResult,
    LocationEstimate,
    ChargeBreakdown,
    MonthProjection,
    EstimationAlert,
    estimate_monthly_charges,
    format_estimation_report,
    export_estimation_to_excel
)

# Vendor-Aware Reconciliation
from .vendor_reconciler import (
    VendorReconciler,
    VendorAwareInvoiceParser,
    ReconciliationResult,
    ReconciliationDiscrepancy,
    ParsedInvoice,
    InvoiceLineItem,
    reconcile_with_vendor_rules,
    compare_estimate_to_invoice,
    format_reconciliation_report,
    export_reconciliation_to_excel
)

# Pattern Analysis
from .pattern_analysis import (
    PatternAnalyzer,
    PatternAnalysisResult,
    LocationPattern,
    AnomalyAlert,
    analyze_pickup_patterns,
    format_pattern_report
)

__all__ = [
    # Version
    "__version__",

    # Vendor Configuration
    "VendorConfig",
    "VendorConfigManager",
    "FuelSurchargeConfig",
    "BillingComponent",
    "load_vendor_config",
    "get_vendor_manager",
    "select_vendor_interactive",

    # Estimation
    "EstimationEngine",
    "EstimationResult",
    "LocationEstimate",
    "ChargeBreakdown",
    "MonthProjection",
    "EstimationAlert",
    "estimate_monthly_charges",
    "format_estimation_report",
    "export_estimation_to_excel",

    # Reconciliation
    "VendorReconciler",
    "VendorAwareInvoiceParser",
    "ReconciliationResult",
    "ReconciliationDiscrepancy",
    "ParsedInvoice",
    "InvoiceLineItem",
    "reconcile_with_vendor_rules",
    "compare_estimate_to_invoice",
    "format_reconciliation_report",
    "export_reconciliation_to_excel",

    # Pattern Analysis
    "PatternAnalyzer",
    "PatternAnalysisResult",
    "LocationPattern",
    "AnomalyAlert",
    "analyze_pickup_patterns",
    "format_pattern_report",
]
