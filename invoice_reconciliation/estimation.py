"""
Monthly Charge Estimation Engine

Calculates expected charges in real-time as pickups occur throughout the month.
"""

import pandas as pd
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict
import calendar

from .vendor_config import VendorConfig, VendorConfigManager, get_vendor_manager


@dataclass
class LocationEstimate:
    """Estimate for a single location."""
    location_name: str
    pickup_count: int = 0
    delivery_count: int = 0
    transport_charges: float = 0.0
    vault_charges: float = 0.0
    delivery_charges: float = 0.0
    onetime_charges: float = 0.0
    subtotal: float = 0.0
    schedule: Optional[str] = None
    pickup_dates: List[date] = field(default_factory=list)

    def calculate_subtotal(self):
        """Calculate the subtotal for this location."""
        self.subtotal = (self.transport_charges + self.vault_charges +
                         self.delivery_charges + self.onetime_charges)
        return self.subtotal


@dataclass
class ChargeBreakdown:
    """Breakdown of charges by type."""
    description: str
    quantity: int
    rate: float
    amount: float
    rate_key: str = ""


@dataclass
class MonthProjection:
    """Projection of remaining charges for the month."""
    days_elapsed: int
    days_remaining: int
    days_total: int
    percent_complete: float
    projected_additional_min: float = 0.0
    projected_additional_max: float = 0.0
    projected_total_min: float = 0.0
    projected_total_max: float = 0.0
    location_projections: Dict[str, Dict] = field(default_factory=dict)


@dataclass
class EstimationAlert:
    """Alert for unusual patterns or issues."""
    alert_type: str  # "no_activity", "excess_pickups", "missing_pickup", "new_location", "rate_change"
    severity: str  # "info", "warning", "error"
    location: Optional[str]
    message: str
    details: Dict = field(default_factory=dict)


@dataclass
class EstimationResult:
    """Complete estimation result."""
    vendor_name: str
    service_month: str
    as_of_date: date
    month_status: str  # "IN_PROGRESS", "COMPLETE"

    # Summary totals
    total_pickups: int = 0
    total_deliveries: int = 0
    base_charges: float = 0.0
    fuel_surcharge: float = 0.0
    total_estimated: float = 0.0

    # Breakdowns
    location_estimates: Dict[str, LocationEstimate] = field(default_factory=dict)
    charge_breakdown: List[ChargeBreakdown] = field(default_factory=list)

    # Projection
    projection: Optional[MonthProjection] = None

    # Alerts
    alerts: List[EstimationAlert] = field(default_factory=list)

    # Special notes
    notes: List[str] = field(default_factory=list)


class EstimationEngine:
    """Engine for calculating monthly charge estimates."""

    def __init__(self, vendor_config: VendorConfig):
        """Initialize the estimation engine.

        Args:
            vendor_config: Vendor configuration with pricing rules
        """
        self.vendor = vendor_config

    def estimate_monthly_charges(
        self,
        tracking_data: pd.DataFrame,
        month_year: str,
        as_of_date: date = None
    ) -> EstimationResult:
        """Calculate expected charges for the month based on pickups to-date.

        Args:
            tracking_data: DataFrame from pickup tracking sheet
            month_year: Target month (e.g., "October 2025" or "2025-10")
            as_of_date: Calculate as-of specific date (default: today)

        Returns:
            EstimationResult with complete breakdown
        """
        # Parse month
        target_month, target_year = self._parse_month_year(month_year)
        month_start = date(target_year, target_month, 1)
        month_end = date(target_year, target_month, calendar.monthrange(target_year, target_month)[1])

        # Set as_of_date
        if as_of_date is None:
            as_of_date = date.today()

        # Determine month status
        if as_of_date >= month_end:
            month_status = "COMPLETE"
            effective_end = month_end
        else:
            month_status = "IN_PROGRESS"
            effective_end = as_of_date

        # Filter tracking data for the month
        filtered_data = self._filter_by_month(tracking_data, target_month, target_year, effective_end)

        # Initialize result
        result = EstimationResult(
            vendor_name=self.vendor.name,
            service_month=f"{calendar.month_name[target_month]} {target_year}",
            as_of_date=as_of_date,
            month_status=month_status
        )

        # Calculate by location
        location_field = self._detect_location_field(tracking_data)
        date_field = self._detect_date_field(tracking_data)
        service_type_field = self._detect_service_type_field(tracking_data)

        # Group by location
        location_estimates = {}
        total_pickups = 0
        total_deliveries = 0

        for location in filtered_data[location_field].unique():
            loc_data = filtered_data[filtered_data[location_field] == location]
            estimate = self._calculate_location_estimate(
                location, loc_data, date_field, service_type_field
            )
            location_estimates[location] = estimate
            total_pickups += estimate.pickup_count
            total_deliveries += estimate.delivery_count

        result.location_estimates = location_estimates
        result.total_pickups = total_pickups
        result.total_deliveries = total_deliveries

        # Calculate charge breakdown
        result.charge_breakdown = self._calculate_charge_breakdown(location_estimates)

        # Calculate totals
        result.base_charges = sum(cb.amount for cb in result.charge_breakdown
                                   if cb.rate_key != "fuel_surcharge")

        # Calculate fuel surcharge
        fuel_applicable = self._calculate_fuel_applicable_charges(location_estimates)
        result.fuel_surcharge = self.vendor.calculate_fuel_surcharge(fuel_applicable)

        # Add fuel surcharge to breakdown
        if result.fuel_surcharge > 0:
            fuel_rate = self.vendor.get_fuel_surcharge_rate()
            result.charge_breakdown.append(ChargeBreakdown(
                description="Fuel Surcharge",
                quantity=1,
                rate=fuel_rate,
                amount=result.fuel_surcharge,
                rate_key="fuel_surcharge"
            ))

        result.total_estimated = result.base_charges + result.fuel_surcharge

        # Generate projection if month is in progress
        if month_status == "IN_PROGRESS":
            result.projection = self._project_month_end(
                location_estimates, tracking_data, as_of_date, month_end, location_field
            )
            result.projection.projected_total_min = result.total_estimated + result.projection.projected_additional_min
            result.projection.projected_total_max = result.total_estimated + result.projection.projected_additional_max

        # Generate alerts
        result.alerts = self._generate_alerts(
            location_estimates, tracking_data, as_of_date, month_start, month_end, location_field
        )

        # Generate notes
        result.notes = self._generate_notes(location_estimates, filtered_data, service_type_field)

        return result

    def _parse_month_year(self, month_year: str) -> Tuple[int, int]:
        """Parse month and year from string.

        Supports formats:
        - "October 2025"
        - "2025-10"
        - "10/2025"
        """
        month_year = month_year.strip()

        # Try "October 2025" format
        try:
            dt = datetime.strptime(month_year, "%B %Y")
            return dt.month, dt.year
        except ValueError:
            pass

        # Try "2025-10" format
        try:
            dt = datetime.strptime(month_year, "%Y-%m")
            return dt.month, dt.year
        except ValueError:
            pass

        # Try "10/2025" format
        try:
            dt = datetime.strptime(month_year, "%m/%Y")
            return dt.month, dt.year
        except ValueError:
            pass

        raise ValueError(f"Cannot parse month/year: {month_year}. Use 'October 2025', '2025-10', or '10/2025'")

    def _filter_by_month(
        self,
        data: pd.DataFrame,
        month: int,
        year: int,
        end_date: date
    ) -> pd.DataFrame:
        """Filter tracking data for the specified month."""
        date_field = self._detect_date_field(data)

        # Convert to datetime if needed
        if data[date_field].dtype == 'object':
            data = data.copy()
            data[date_field] = pd.to_datetime(data[date_field], errors='coerce')

        # Filter by date range
        month_start = datetime(year, month, 1)
        end_dt = datetime.combine(end_date, datetime.max.time())

        mask = (data[date_field] >= month_start) & (data[date_field] <= end_dt)

        # Also filter by vendor if vendor field exists
        vendor_field = self._detect_vendor_field(data)
        if vendor_field:
            vendor_mask = data[vendor_field].str.lower().str.contains(
                self.vendor.vendor_id.lower(), na=False
            ) | data[vendor_field].str.lower().str.contains(
                self.vendor.name.lower().split()[0], na=False
            )
            mask = mask & vendor_mask

        return data[mask].copy()

    def _detect_location_field(self, data: pd.DataFrame) -> str:
        """Detect the location field in the data."""
        candidates = [
            "armored transport branch", "location", "location_id", "store",
            "store_id", "site", "site_id", "branch", "location_name"
        ]

        for col in data.columns:
            if col.lower().replace("_", " ").replace("-", " ") in [c.replace("_", " ") for c in candidates]:
                return col

        # Try partial match
        for col in data.columns:
            col_lower = col.lower()
            for candidate in candidates:
                if candidate in col_lower or col_lower in candidate:
                    return col

        raise ValueError(f"Cannot detect location field. Columns: {list(data.columns)}")

    def _detect_date_field(self, data: pd.DataFrame) -> str:
        """Detect the date field in the data."""
        candidates = [
            "pickup_date", "date", "service_date", "pickup date", "transaction_date"
        ]

        for col in data.columns:
            if col.lower().replace("_", " ") in [c.replace("_", " ") for c in candidates]:
                return col

        # Try partial match
        for col in data.columns:
            if "date" in col.lower():
                return col

        raise ValueError(f"Cannot detect date field. Columns: {list(data.columns)}")

    def _detect_service_type_field(self, data: pd.DataFrame) -> Optional[str]:
        """Detect the service type field in the data."""
        candidates = [
            "service_type", "type", "pickup_type", "service", "transaction_type"
        ]

        for col in data.columns:
            if col.lower().replace("_", " ") in [c.replace("_", " ") for c in candidates]:
                return col

        # Try partial match
        for col in data.columns:
            if "type" in col.lower() or "service" in col.lower():
                return col

        return None  # Optional field

    def _detect_vendor_field(self, data: pd.DataFrame) -> Optional[str]:
        """Detect the vendor field in the data."""
        candidates = [
            "vendor", "vendor_name", "armored_vendor", "carrier", "service_provider"
        ]

        for col in data.columns:
            if col.lower().replace("_", " ") in [c.replace("_", " ") for c in candidates]:
                return col

        return None

    def _calculate_location_estimate(
        self,
        location: str,
        loc_data: pd.DataFrame,
        date_field: str,
        service_type_field: Optional[str]
    ) -> LocationEstimate:
        """Calculate estimate for a single location."""
        estimate = LocationEstimate(location_name=location)

        # Extract schedule from location name
        estimate.schedule = self.vendor.get_schedule_from_location(location)

        # Count pickups and deliveries
        for _, row in loc_data.iterrows():
            service_type = "pickup"  # Default

            if service_type_field and pd.notna(row.get(service_type_field)):
                service_desc = str(row[service_type_field]).lower()
                if "delivery" in service_desc:
                    service_type = "delivery"
                elif "one-time" in service_desc or "onetime" in service_desc or "one time" in service_desc:
                    service_type = "onetime"

            if service_type == "delivery":
                estimate.delivery_count += 1
            elif service_type == "onetime":
                estimate.onetime_charges += self.vendor.get_rate("onetime_pickup")
            else:
                estimate.pickup_count += 1

            # Track pickup dates
            if pd.notna(row.get(date_field)):
                pickup_date = row[date_field]
                if isinstance(pickup_date, datetime):
                    pickup_date = pickup_date.date()
                elif isinstance(pickup_date, str):
                    pickup_date = pd.to_datetime(pickup_date).date()
                estimate.pickup_dates.append(pickup_date)

        # Calculate charges based on vendor rates
        if "smartsafe_pickup" in self.vendor.rates:
            # Cash Man style - separate transport and vault
            estimate.transport_charges = estimate.pickup_count * self.vendor.get_rate("smartsafe_pickup")
            estimate.vault_charges = estimate.pickup_count * self.vendor.get_rate("vault_management")
        else:
            # Simple pickup rate
            estimate.transport_charges = estimate.pickup_count * self.vendor.get_rate("pickup")

        estimate.delivery_charges = estimate.delivery_count * self.vendor.get_rate("branch_delivery")

        estimate.calculate_subtotal()
        return estimate

    def _calculate_charge_breakdown(
        self,
        location_estimates: Dict[str, LocationEstimate]
    ) -> List[ChargeBreakdown]:
        """Calculate the charge breakdown by service type."""
        breakdown = []

        # Aggregate counts
        total_pickups = sum(le.pickup_count for le in location_estimates.values())
        total_deliveries = sum(le.delivery_count for le in location_estimates.values())
        total_onetime = sum(1 for le in location_estimates.values() if le.onetime_charges > 0)

        # Add line items based on vendor billing components
        for component in self.vendor.billing_components:
            if component.calculated:
                continue  # Skip calculated items like fuel surcharge

            rate = self.vendor.get_rate(component.rate_key)
            if rate <= 0:
                continue

            if component.per == "pickup":
                if component.rate_key in ["smartsafe_pickup", "pickup"]:
                    quantity = total_pickups
                elif component.rate_key == "vault_management":
                    quantity = total_pickups
                elif component.rate_key == "onetime_pickup":
                    quantity = total_onetime
                else:
                    quantity = total_pickups
            elif component.per == "delivery":
                quantity = total_deliveries
            else:
                quantity = 0

            if quantity > 0:
                breakdown.append(ChargeBreakdown(
                    description=component.name,
                    quantity=quantity,
                    rate=rate,
                    amount=quantity * rate,
                    rate_key=component.rate_key
                ))

        return breakdown

    def _calculate_fuel_applicable_charges(
        self,
        location_estimates: Dict[str, LocationEstimate]
    ) -> float:
        """Calculate charges that fuel surcharge applies to."""
        if not self.vendor.fuel_surcharge.enabled:
            return 0.0

        applies_to = self.vendor.fuel_surcharge.applies_to
        total = 0.0

        for le in location_estimates.values():
            if not applies_to or "smartsafe_pickup" in applies_to or "pickup" in applies_to:
                total += le.transport_charges
            if not applies_to or "vault_management" in applies_to:
                total += le.vault_charges
            if not applies_to or "branch_delivery" in applies_to:
                total += le.delivery_charges

        return total

    def _project_month_end(
        self,
        location_estimates: Dict[str, LocationEstimate],
        full_tracking_data: pd.DataFrame,
        as_of_date: date,
        month_end: date,
        location_field: str
    ) -> MonthProjection:
        """Project remaining charges for the month."""
        days_elapsed = (as_of_date - date(as_of_date.year, as_of_date.month, 1)).days + 1
        days_total = (month_end - date(month_end.year, month_end.month, 1)).days + 1
        days_remaining = days_total - days_elapsed

        projection = MonthProjection(
            days_elapsed=days_elapsed,
            days_remaining=days_remaining,
            days_total=days_total,
            percent_complete=days_elapsed / days_total * 100
        )

        min_additional = 0.0
        max_additional = 0.0

        for location, estimate in location_estimates.items():
            loc_projection = {"expected_remaining": 0, "possible_remaining": 0}

            schedule = estimate.schedule
            if schedule == "EOW":
                # Every other week - check if another pickup is likely
                if estimate.pickup_count < 2 and days_remaining >= 7:
                    loc_projection["expected_remaining"] = 1
                    loc_projection["possible_remaining"] = 1
            elif schedule == "Weekly":
                remaining_weeks = days_remaining // 7
                loc_projection["expected_remaining"] = remaining_weeks
                loc_projection["possible_remaining"] = remaining_weeks + 1
            elif schedule == "Monthly":
                if estimate.pickup_count == 0:
                    loc_projection["expected_remaining"] = 1
                    loc_projection["possible_remaining"] = 1
            elif schedule == "On Request":
                # Use daily rate to estimate
                daily_rate = estimate.pickup_count / days_elapsed if days_elapsed > 0 else 0
                loc_projection["expected_remaining"] = int(daily_rate * days_remaining)
                loc_projection["possible_remaining"] = int(daily_rate * days_remaining) + 1

            projection.location_projections[location] = loc_projection

            # Calculate charges for projected pickups
            pickup_rate = self.vendor.get_rate("smartsafe_pickup") or self.vendor.get_rate("pickup")
            vault_rate = self.vendor.get_rate("vault_management") or 0

            min_additional += loc_projection["expected_remaining"] * (pickup_rate + vault_rate)
            max_additional += loc_projection["possible_remaining"] * (pickup_rate + vault_rate)

        # Add fuel surcharge to projections
        fuel_rate = self.vendor.get_fuel_surcharge_rate()
        projection.projected_additional_min = min_additional * (1 + fuel_rate)
        projection.projected_additional_max = max_additional * (1 + fuel_rate)

        return projection

    def _generate_alerts(
        self,
        location_estimates: Dict[str, LocationEstimate],
        tracking_data: pd.DataFrame,
        as_of_date: date,
        month_start: date,
        month_end: date,
        location_field: str
    ) -> List[EstimationAlert]:
        """Generate alerts for unusual patterns."""
        alerts = []

        # Get all known locations from vendor mapping
        known_locations = set(self.vendor.location_mapping.keys())

        for location, estimate in location_estimates.items():
            # Check for no activity on scheduled locations
            if estimate.schedule in ["EOW", "Weekly", "Monthly"] and estimate.pickup_count == 0:
                days_in = (as_of_date - month_start).days + 1
                if days_in > 14:  # More than 2 weeks in
                    alerts.append(EstimationAlert(
                        alert_type="no_activity",
                        severity="warning",
                        location=location,
                        message=f"No pickups recorded for {location} (schedule: {estimate.schedule})",
                        details={"schedule": estimate.schedule, "days_elapsed": days_in}
                    ))

            # Check for excess pickups
            if estimate.schedule == "Monthly" and estimate.pickup_count > 1:
                alerts.append(EstimationAlert(
                    alert_type="excess_pickups",
                    severity="info",
                    location=location,
                    message=f"Multiple pickups for monthly location: {location} ({estimate.pickup_count} pickups)",
                    details={"expected": 1, "actual": estimate.pickup_count}
                ))

            elif estimate.schedule == "EOW" and estimate.pickup_count > 3:
                alerts.append(EstimationAlert(
                    alert_type="excess_pickups",
                    severity="warning",
                    location=location,
                    message=f"More pickups than expected for EOW location: {location} ({estimate.pickup_count} pickups)",
                    details={"expected_max": 3, "actual": estimate.pickup_count}
                ))

            # Check for new/unknown locations
            if location not in known_locations:
                # Check if it matches any known location
                matched = False
                for known in known_locations:
                    if self.vendor.match_location(known, location):
                        matched = True
                        break

                if not matched and known_locations:
                    alerts.append(EstimationAlert(
                        alert_type="new_location",
                        severity="info",
                        location=location,
                        message=f"New location not in vendor mapping: {location}",
                        details={"pickup_count": estimate.pickup_count}
                    ))

        return alerts

    def _generate_notes(
        self,
        location_estimates: Dict[str, LocationEstimate],
        filtered_data: pd.DataFrame,
        service_type_field: Optional[str]
    ) -> List[str]:
        """Generate special notes about the estimation."""
        notes = []

        # Check for one-time pickups
        for location, estimate in location_estimates.items():
            if estimate.onetime_charges > 0:
                notes.append(f"One-time pickup recorded: {location}")

        # Check for locations with no activity
        no_activity = [loc for loc, est in location_estimates.items()
                       if est.pickup_count == 0 and est.delivery_count == 0]
        if no_activity:
            notes.append(f"{len(no_activity)} location(s) with no activity - verify if active")

        # Count EOW locations
        eow_locations = [loc for loc, est in location_estimates.items()
                         if est.schedule == "EOW"]
        if eow_locations:
            notes.append(f"{len(eow_locations)} location(s) on EOW schedule - verify pickup frequency")

        return notes


def estimate_monthly_charges(
    tracking_data: pd.DataFrame,
    vendor_config: VendorConfig,
    month_year: str,
    as_of_date: date = None
) -> EstimationResult:
    """Convenience function to estimate monthly charges.

    Args:
        tracking_data: DataFrame from pickup tracking sheet
        vendor_config: Vendor configuration with pricing rules
        month_year: Target month (e.g., "October 2025")
        as_of_date: Calculate as-of specific date (default: today)

    Returns:
        EstimationResult with complete breakdown
    """
    engine = EstimationEngine(vendor_config)
    return engine.estimate_monthly_charges(tracking_data, month_year, as_of_date)


def format_estimation_report(result: EstimationResult) -> str:
    """Format estimation result as a text report.

    Args:
        result: EstimationResult to format

    Returns:
        Formatted text report
    """
    lines = []
    sep = "=" * 65

    lines.append(sep)
    lines.append("MONTHLY CHARGE ESTIMATION")
    lines.append(sep)
    lines.append(f"Vendor: {result.vendor_name}")
    lines.append(f"Service Month: {result.service_month}")
    lines.append(f"As of Date: {result.as_of_date.strftime('%B %d, %Y')}")

    if result.projection:
        days_info = f"({result.projection.days_elapsed} days into month)"
        pct_remaining = 100 - result.projection.percent_complete
        lines.append(f"Status: MONTH {result.month_status} ({pct_remaining:.0f}% remaining)")
    else:
        lines.append(f"Status: MONTH {result.month_status}")

    lines.append("")
    lines.append("-" * 65)
    lines.append("PICKUP SUMMARY BY LOCATION")
    lines.append("-" * 65)

    # Header
    lines.append(f"{'Location':<40} {'Pickups':>7} {'Transport':>10} {'Vault':>8} {'Subtotal':>10}")

    for location, estimate in result.location_estimates.items():
        loc_display = location[:38] + ".." if len(location) > 40 else location
        lines.append(
            f"{loc_display:<40} {estimate.pickup_count:>7} "
            f"${estimate.transport_charges:>8,.2f} ${estimate.vault_charges:>6,.2f} "
            f"${estimate.subtotal:>8,.2f}"
        )

    lines.append("")
    lines.append("-" * 65)
    lines.append("CHARGE BREAKDOWN")
    lines.append("-" * 65)

    for charge in result.charge_breakdown:
        if charge.rate_key == "fuel_surcharge":
            lines.append(f"{charge.description:<40} {charge.rate*100:.0f}%           ${charge.amount:>10,.2f}")
        else:
            lines.append(
                f"{charge.description:<25} {charge.quantity:>3} × ${charge.rate:<8,.2f} ${charge.amount:>10,.2f}"
            )

    lines.append(f"{'':>56} {'─' * 8}")
    lines.append(f"{'ESTIMATED TOTAL':>56} ${result.total_estimated:>10,.2f}")

    # Projection section
    if result.projection:
        lines.append("")
        lines.append("-" * 65)
        lines.append("PROJECTION TO MONTH END")
        lines.append("-" * 65)
        lines.append(
            f"Days elapsed: {result.projection.days_elapsed} / {result.projection.days_total} "
            f"({result.projection.percent_complete:.0f}%)"
        )
        lines.append(f"Remaining days: {result.projection.days_remaining}")
        lines.append("")
        lines.append("Based on pickup patterns:")

        for location, proj in result.projection.location_projections.items():
            if proj["expected_remaining"] > 0 or proj["possible_remaining"] > 0:
                estimate = result.location_estimates.get(location)
                schedule = estimate.schedule if estimate else "Unknown"
                lines.append(
                    f"- {location} ({schedule}): May have {proj['expected_remaining']}-{proj['possible_remaining']} more pickups"
                )

        lines.append("")
        lines.append(
            f"Projected Additional Charges: ${result.projection.projected_additional_min:,.2f} - "
            f"${result.projection.projected_additional_max:,.2f}"
        )
        lines.append(
            f"ESTIMATED MONTH-END TOTAL: ${result.projection.projected_total_min:,.2f} - "
            f"${result.projection.projected_total_max:,.2f}"
        )

    # Alerts section
    if result.alerts:
        lines.append("")
        lines.append("-" * 65)
        lines.append("ALERTS")
        lines.append("-" * 65)
        for alert in result.alerts:
            icon = "⚠" if alert.severity == "warning" else "ℹ" if alert.severity == "info" else "❌"
            lines.append(f"{icon} {alert.message}")

    # Notes section
    if result.notes:
        lines.append("")
        lines.append("-" * 65)
        lines.append("SPECIAL NOTES")
        lines.append("-" * 65)
        for note in result.notes:
            lines.append(f"- {note}")

    lines.append(sep)

    return "\n".join(lines)


def export_estimation_to_excel(result: EstimationResult, output_path: str):
    """Export estimation result to Excel.

    Args:
        result: EstimationResult to export
        output_path: Path to output Excel file
    """
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        # Summary sheet
        summary_data = {
            "Field": [
                "Vendor", "Service Month", "As of Date", "Status",
                "Total Pickups", "Total Deliveries",
                "Base Charges", "Fuel Surcharge", "Total Estimated"
            ],
            "Value": [
                result.vendor_name, result.service_month,
                result.as_of_date.strftime("%Y-%m-%d"), result.month_status,
                result.total_pickups, result.total_deliveries,
                f"${result.base_charges:,.2f}",
                f"${result.fuel_surcharge:,.2f}",
                f"${result.total_estimated:,.2f}"
            ]
        }
        pd.DataFrame(summary_data).to_excel(writer, sheet_name="Summary", index=False)

        # Location breakdown sheet
        loc_data = []
        for location, estimate in result.location_estimates.items():
            loc_data.append({
                "Location": location,
                "Schedule": estimate.schedule or "Unknown",
                "Pickups": estimate.pickup_count,
                "Deliveries": estimate.delivery_count,
                "Transport Charges": estimate.transport_charges,
                "Vault Charges": estimate.vault_charges,
                "Delivery Charges": estimate.delivery_charges,
                "One-time Charges": estimate.onetime_charges,
                "Subtotal": estimate.subtotal
            })
        pd.DataFrame(loc_data).to_excel(writer, sheet_name="By Location", index=False)

        # Charge breakdown sheet
        charge_data = []
        for charge in result.charge_breakdown:
            charge_data.append({
                "Description": charge.description,
                "Quantity": charge.quantity,
                "Rate": charge.rate,
                "Amount": charge.amount
            })
        pd.DataFrame(charge_data).to_excel(writer, sheet_name="Charges", index=False)

        # Projection sheet (if available)
        if result.projection:
            proj_data = {
                "Field": [
                    "Days Elapsed", "Days Remaining", "Days Total", "Percent Complete",
                    "Projected Additional (Min)", "Projected Additional (Max)",
                    "Projected Total (Min)", "Projected Total (Max)"
                ],
                "Value": [
                    result.projection.days_elapsed,
                    result.projection.days_remaining,
                    result.projection.days_total,
                    f"{result.projection.percent_complete:.1f}%",
                    f"${result.projection.projected_additional_min:,.2f}",
                    f"${result.projection.projected_additional_max:,.2f}",
                    f"${result.projection.projected_total_min:,.2f}",
                    f"${result.projection.projected_total_max:,.2f}"
                ]
            }
            pd.DataFrame(proj_data).to_excel(writer, sheet_name="Projection", index=False)

        # Alerts sheet
        if result.alerts:
            alert_data = []
            for alert in result.alerts:
                alert_data.append({
                    "Type": alert.alert_type,
                    "Severity": alert.severity,
                    "Location": alert.location or "",
                    "Message": alert.message
                })
            pd.DataFrame(alert_data).to_excel(writer, sheet_name="Alerts", index=False)
