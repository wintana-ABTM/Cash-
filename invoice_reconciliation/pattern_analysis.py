"""
Pattern Analysis Module

Analyzes historical pickup patterns to improve projections and detect anomalies.
"""

import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict
import calendar

from .vendor_config import VendorConfig, VendorConfigManager


@dataclass
class LocationPattern:
    """Pattern analysis for a single location."""
    location_name: str
    schedule_detected: str  # EOW, Weekly, Monthly, On Request, Unknown
    confidence: float  # 0-1 confidence in detected schedule

    avg_pickups_per_month: float
    std_pickups_per_month: float
    min_pickups: int
    max_pickups: int

    typical_pickup_days: List[int]  # Days of month (1-31)
    typical_weekdays: List[int]  # 0=Mon, 6=Sun

    avg_days_between_pickups: float
    seasonality_detected: bool
    seasonality_pattern: Optional[Dict] = None

    months_analyzed: int = 0
    total_pickups_analyzed: int = 0


@dataclass
class AnomalyAlert:
    """Alert for detected anomaly."""
    alert_type: str  # missing_pickup, excess_pickup, unusual_timing, amount_spike
    severity: str  # info, warning, critical
    location: str
    date_detected: date
    message: str
    expected_value: Any = None
    actual_value: Any = None
    recommendation: str = ""


@dataclass
class PatternAnalysisResult:
    """Complete pattern analysis result."""
    analysis_date: date
    months_analyzed: int
    locations_analyzed: int

    location_patterns: Dict[str, LocationPattern] = field(default_factory=dict)
    anomalies: List[AnomalyAlert] = field(default_factory=list)

    overall_trends: Dict[str, Any] = field(default_factory=dict)
    predictions: Dict[str, Any] = field(default_factory=dict)


class PatternAnalyzer:
    """Analyzes historical pickup patterns."""

    def __init__(self, vendor_config: VendorConfig = None):
        """Initialize the analyzer.

        Args:
            vendor_config: Optional vendor configuration for location mapping
        """
        self.vendor = vendor_config

    def analyze_patterns(
        self,
        tracking_data: pd.DataFrame,
        months_back: int = 6
    ) -> PatternAnalysisResult:
        """Analyze pickup patterns over historical data.

        Args:
            tracking_data: DataFrame with pickup history
            months_back: Number of months to analyze

        Returns:
            PatternAnalysisResult with detected patterns
        """
        # Detect columns
        date_col = self._detect_date_column(tracking_data)
        location_col = self._detect_location_column(tracking_data)

        # Prepare data
        df = tracking_data.copy()
        df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
        df = df.dropna(subset=[date_col])

        # Filter to analysis period
        end_date = df[date_col].max()
        start_date = end_date - relativedelta(months=months_back)
        df = df[(df[date_col] >= start_date) & (df[date_col] <= end_date)]

        result = PatternAnalysisResult(
            analysis_date=date.today(),
            months_analyzed=months_back,
            locations_analyzed=df[location_col].nunique()
        )

        # Analyze each location
        for location in df[location_col].unique():
            loc_data = df[df[location_col] == location]
            pattern = self._analyze_location_pattern(location, loc_data, date_col, months_back)
            result.location_patterns[location] = pattern

        # Detect anomalies
        result.anomalies = self._detect_anomalies(df, date_col, location_col, result.location_patterns)

        # Calculate overall trends
        result.overall_trends = self._calculate_trends(df, date_col, location_col)

        # Generate predictions
        result.predictions = self._generate_predictions(result.location_patterns, end_date)

        return result

    def _detect_date_column(self, data: pd.DataFrame) -> str:
        """Detect the date column."""
        candidates = ['pickup_date', 'date', 'service_date', 'transaction_date']

        for col in data.columns:
            if col.lower().replace('_', ' ') in [c.replace('_', ' ') for c in candidates]:
                return col

        for col in data.columns:
            if 'date' in col.lower():
                return col

        raise ValueError(f"Cannot detect date column. Columns: {list(data.columns)}")

    def _detect_location_column(self, data: pd.DataFrame) -> str:
        """Detect the location column."""
        candidates = ['location', 'armored transport branch', 'location_id', 'branch', 'store']

        for col in data.columns:
            if col.lower().replace('_', ' ') in [c.replace('_', ' ') for c in candidates]:
                return col

        for col in data.columns:
            if 'location' in col.lower() or 'branch' in col.lower():
                return col

        raise ValueError(f"Cannot detect location column. Columns: {list(data.columns)}")

    def _analyze_location_pattern(
        self,
        location: str,
        loc_data: pd.DataFrame,
        date_col: str,
        months_back: int
    ) -> LocationPattern:
        """Analyze pattern for a single location."""
        dates = loc_data[date_col].sort_values()

        # Calculate monthly pickup counts
        monthly_counts = loc_data.groupby(loc_data[date_col].dt.to_period('M')).size()

        # Basic statistics
        avg_monthly = monthly_counts.mean() if len(monthly_counts) > 0 else 0
        std_monthly = monthly_counts.std() if len(monthly_counts) > 1 else 0

        # Days between pickups
        if len(dates) > 1:
            date_diffs = dates.diff().dropna()
            avg_days_between = date_diffs.mean().days
        else:
            avg_days_between = 0

        # Detect schedule
        schedule, confidence = self._detect_schedule(avg_monthly, avg_days_between, std_monthly)

        # Typical pickup days
        days_of_month = loc_data[date_col].dt.day.tolist()
        weekdays = loc_data[date_col].dt.dayofweek.tolist()

        # Find most common days
        typical_days = self._find_typical_values(days_of_month, top_n=3)
        typical_weekdays = self._find_typical_values(weekdays, top_n=2)

        # Check for seasonality
        seasonality, seasonality_pattern = self._detect_seasonality(monthly_counts)

        return LocationPattern(
            location_name=location,
            schedule_detected=schedule,
            confidence=confidence,
            avg_pickups_per_month=avg_monthly,
            std_pickups_per_month=std_monthly,
            min_pickups=int(monthly_counts.min()) if len(monthly_counts) > 0 else 0,
            max_pickups=int(monthly_counts.max()) if len(monthly_counts) > 0 else 0,
            typical_pickup_days=typical_days,
            typical_weekdays=typical_weekdays,
            avg_days_between_pickups=avg_days_between,
            seasonality_detected=seasonality,
            seasonality_pattern=seasonality_pattern,
            months_analyzed=len(monthly_counts),
            total_pickups_analyzed=len(loc_data)
        )

    def _detect_schedule(
        self,
        avg_monthly: float,
        avg_days_between: float,
        std_monthly: float
    ) -> Tuple[str, float]:
        """Detect the pickup schedule type.

        Returns:
            Tuple of (schedule_type, confidence)
        """
        if avg_monthly < 0.5:
            return "Inactive", 0.9

        # Weekly: ~4 pickups/month, ~7 days between
        if 3.5 <= avg_monthly <= 5 and 5 <= avg_days_between <= 9:
            confidence = 1 - (abs(avg_monthly - 4.33) / 4.33) * 0.5
            return "Weekly", min(confidence, 0.95)

        # Every Other Week: ~2 pickups/month, ~14 days between
        if 1.5 <= avg_monthly <= 3 and 12 <= avg_days_between <= 18:
            confidence = 1 - (abs(avg_monthly - 2.17) / 2.17) * 0.5
            return "EOW", min(confidence, 0.95)

        # Monthly: ~1 pickup/month, ~30 days between
        if 0.8 <= avg_monthly <= 1.5 and avg_days_between >= 25:
            confidence = 1 - (abs(avg_monthly - 1) / 1) * 0.5
            return "Monthly", min(confidence, 0.95)

        # On Request: irregular pattern
        if std_monthly > avg_monthly * 0.5:
            return "On Request", 0.7

        return "Unknown", 0.5

    def _find_typical_values(self, values: List[int], top_n: int = 3) -> List[int]:
        """Find the most common values."""
        if not values:
            return []

        from collections import Counter
        counter = Counter(values)
        return [v for v, _ in counter.most_common(top_n)]

    def _detect_seasonality(
        self,
        monthly_counts: pd.Series
    ) -> Tuple[bool, Optional[Dict]]:
        """Detect seasonal patterns in pickup frequency."""
        if len(monthly_counts) < 6:
            return False, None

        # Convert to monthly averages
        monthly_avg = {}
        for period in monthly_counts.index:
            month = period.month
            if month not in monthly_avg:
                monthly_avg[month] = []
            monthly_avg[month].append(monthly_counts[period])

        # Check for significant variation by month
        avg_by_month = {m: np.mean(v) for m, v in monthly_avg.items() if v}

        if len(avg_by_month) < 4:
            return False, None

        overall_avg = np.mean(list(avg_by_month.values()))
        max_deviation = max(abs(v - overall_avg) / overall_avg for v in avg_by_month.values())

        # If more than 30% variation, consider seasonal
        if max_deviation > 0.3:
            return True, {
                "monthly_averages": avg_by_month,
                "peak_months": [m for m, v in avg_by_month.items() if v > overall_avg * 1.2],
                "low_months": [m for m, v in avg_by_month.items() if v < overall_avg * 0.8]
            }

        return False, None

    def _detect_anomalies(
        self,
        data: pd.DataFrame,
        date_col: str,
        location_col: str,
        patterns: Dict[str, LocationPattern]
    ) -> List[AnomalyAlert]:
        """Detect anomalies in the data."""
        anomalies = []

        # Check each location
        for location, pattern in patterns.items():
            loc_data = data[data[location_col] == location]

            # Check for missing pickups (gaps longer than expected)
            if pattern.avg_days_between_pickups > 0:
                expected_gap = pattern.avg_days_between_pickups
                dates = loc_data[date_col].sort_values()

                if len(dates) > 1:
                    for i in range(1, len(dates)):
                        gap = (dates.iloc[i] - dates.iloc[i-1]).days

                        # Alert if gap is more than 50% longer than expected
                        if gap > expected_gap * 1.5:
                            anomalies.append(AnomalyAlert(
                                alert_type="missing_pickup",
                                severity="warning",
                                location=location,
                                date_detected=dates.iloc[i].date(),
                                message=f"Unusual gap of {gap} days between pickups",
                                expected_value=f"{expected_gap:.0f} days",
                                actual_value=f"{gap} days",
                                recommendation="Verify if a pickup was missed or cancelled"
                            ))

            # Check for excess pickups in a month
            monthly_counts = loc_data.groupby(loc_data[date_col].dt.to_period('M')).size()
            for period, count in monthly_counts.items():
                if count > pattern.avg_pickups_per_month + 2 * pattern.std_pickups_per_month:
                    anomalies.append(AnomalyAlert(
                        alert_type="excess_pickup",
                        severity="info",
                        location=location,
                        date_detected=period.start_time.date(),
                        message=f"Higher than usual pickup count in {period}",
                        expected_value=f"{pattern.avg_pickups_per_month:.1f} pickups",
                        actual_value=f"{count} pickups",
                        recommendation="Verify if additional pickups were authorized"
                    ))

            # Check for inactive locations
            if pattern.schedule_detected in ["Weekly", "EOW", "Monthly"]:
                latest_pickup = loc_data[date_col].max()
                days_since = (datetime.now() - latest_pickup).days

                expected_max_gap = {
                    "Weekly": 14,
                    "EOW": 21,
                    "Monthly": 45
                }.get(pattern.schedule_detected, 30)

                if days_since > expected_max_gap:
                    anomalies.append(AnomalyAlert(
                        alert_type="missing_pickup",
                        severity="warning",
                        location=location,
                        date_detected=date.today(),
                        message=f"No pickups in {days_since} days (schedule: {pattern.schedule_detected})",
                        expected_value=f"Pickup within {expected_max_gap} days",
                        actual_value=f"Last pickup {days_since} days ago",
                        recommendation="Verify location is still active"
                    ))

        return anomalies

    def _calculate_trends(
        self,
        data: pd.DataFrame,
        date_col: str,
        location_col: str
    ) -> Dict[str, Any]:
        """Calculate overall trends."""
        trends = {}

        # Monthly totals
        monthly_totals = data.groupby(data[date_col].dt.to_period('M')).size()

        if len(monthly_totals) > 1:
            # Trend direction
            first_half = monthly_totals.head(len(monthly_totals) // 2).mean()
            second_half = monthly_totals.tail(len(monthly_totals) // 2).mean()

            if second_half > first_half * 1.1:
                trends["direction"] = "increasing"
                trends["change_pct"] = ((second_half - first_half) / first_half) * 100
            elif second_half < first_half * 0.9:
                trends["direction"] = "decreasing"
                trends["change_pct"] = ((first_half - second_half) / first_half) * -100
            else:
                trends["direction"] = "stable"
                trends["change_pct"] = 0

        # Location activity
        location_counts = data[location_col].value_counts()
        trends["most_active_locations"] = location_counts.head(5).to_dict()
        trends["least_active_locations"] = location_counts.tail(5).to_dict()

        # Day of week distribution
        dow_counts = data[date_col].dt.dayofweek.value_counts()
        day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        trends["day_of_week_distribution"] = {day_names[i]: int(dow_counts.get(i, 0)) for i in range(7)}

        return trends

    def _generate_predictions(
        self,
        patterns: Dict[str, LocationPattern],
        last_date: pd.Timestamp
    ) -> Dict[str, Any]:
        """Generate predictions for the next month."""
        predictions = {}

        next_month_start = (last_date + relativedelta(months=1)).replace(day=1)
        next_month_days = calendar.monthrange(next_month_start.year, next_month_start.month)[1]

        total_predicted = 0
        location_predictions = {}

        for location, pattern in patterns.items():
            if pattern.schedule_detected == "Inactive":
                predicted = 0
            elif pattern.schedule_detected == "Weekly":
                predicted = 4
            elif pattern.schedule_detected == "EOW":
                predicted = 2
            elif pattern.schedule_detected == "Monthly":
                predicted = 1
            else:
                predicted = round(pattern.avg_pickups_per_month)

            location_predictions[location] = {
                "predicted_pickups": predicted,
                "confidence": pattern.confidence,
                "range": (
                    max(0, predicted - 1),
                    predicted + 1
                )
            }
            total_predicted += predicted

        predictions["next_month"] = next_month_start.strftime("%B %Y")
        predictions["total_predicted_pickups"] = total_predicted
        predictions["by_location"] = location_predictions

        return predictions


def analyze_pickup_patterns(
    tracking_data: pd.DataFrame,
    vendor_config: VendorConfig = None,
    months_back: int = 6
) -> PatternAnalysisResult:
    """Convenience function to analyze pickup patterns.

    Args:
        tracking_data: DataFrame with pickup history
        vendor_config: Optional vendor configuration
        months_back: Number of months to analyze

    Returns:
        PatternAnalysisResult
    """
    analyzer = PatternAnalyzer(vendor_config)
    return analyzer.analyze_patterns(tracking_data, months_back)


def format_pattern_report(result: PatternAnalysisResult) -> str:
    """Format pattern analysis as a text report.

    Args:
        result: PatternAnalysisResult to format

    Returns:
        Formatted text report
    """
    lines = []
    sep = "=" * 65

    lines.append(sep)
    lines.append("PICKUP PATTERN ANALYSIS")
    lines.append(sep)
    lines.append(f"Analysis Date: {result.analysis_date}")
    lines.append(f"Months Analyzed: {result.months_analyzed}")
    lines.append(f"Locations Analyzed: {result.locations_analyzed}")

    lines.append("")
    lines.append("-" * 65)
    lines.append("DETECTED SCHEDULES BY LOCATION")
    lines.append("-" * 65)

    for location, pattern in result.location_patterns.items():
        lines.append(f"\n{location}")
        lines.append(f"  Schedule: {pattern.schedule_detected} (confidence: {pattern.confidence:.0%})")
        lines.append(f"  Avg pickups/month: {pattern.avg_pickups_per_month:.1f} (±{pattern.std_pickups_per_month:.1f})")
        lines.append(f"  Avg days between: {pattern.avg_days_between_pickups:.0f}")

        if pattern.typical_weekdays:
            day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
            typical_days = [day_names[d] for d in pattern.typical_weekdays]
            lines.append(f"  Typical days: {', '.join(typical_days)}")

        if pattern.seasonality_detected:
            lines.append(f"  Seasonality: Detected")

    # Anomalies
    if result.anomalies:
        lines.append("")
        lines.append("-" * 65)
        lines.append("DETECTED ANOMALIES")
        lines.append("-" * 65)

        for anomaly in result.anomalies:
            icon = "⚠" if anomaly.severity == "warning" else "ℹ" if anomaly.severity == "info" else "❌"
            lines.append(f"\n{icon} {anomaly.message}")
            lines.append(f"   Location: {anomaly.location}")
            lines.append(f"   Expected: {anomaly.expected_value}")
            lines.append(f"   Actual: {anomaly.actual_value}")
            lines.append(f"   Action: {anomaly.recommendation}")

    # Trends
    if result.overall_trends:
        lines.append("")
        lines.append("-" * 65)
        lines.append("OVERALL TRENDS")
        lines.append("-" * 65)

        if "direction" in result.overall_trends:
            lines.append(f"Trend: {result.overall_trends['direction']} ({result.overall_trends.get('change_pct', 0):.1f}%)")

        if "day_of_week_distribution" in result.overall_trends:
            lines.append("Day of Week Distribution:")
            for day, count in result.overall_trends["day_of_week_distribution"].items():
                lines.append(f"  {day}: {count}")

    # Predictions
    if result.predictions:
        lines.append("")
        lines.append("-" * 65)
        lines.append("PREDICTIONS FOR NEXT MONTH")
        lines.append("-" * 65)

        lines.append(f"Month: {result.predictions.get('next_month', 'N/A')}")
        lines.append(f"Total Predicted Pickups: {result.predictions.get('total_predicted_pickups', 0)}")

    lines.append(sep)

    return "\n".join(lines)
