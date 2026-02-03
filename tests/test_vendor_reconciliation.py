"""
Test Suite for Invoice Reconciliation System

Comprehensive tests for vendor configuration, estimation, and reconciliation.
"""

import pytest
import pandas as pd
from datetime import date, datetime
from pathlib import Path
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invoice_reconciliation.vendor_config import (
    VendorConfigManager, VendorConfig, FuelSurchargeConfig,
    load_vendor_config, get_vendor_manager
)
from invoice_reconciliation.estimation import (
    EstimationEngine, EstimationResult, LocationEstimate,
    estimate_monthly_charges, format_estimation_report
)
from invoice_reconciliation.vendor_reconciler import (
    VendorReconciler, ReconciliationResult,
    format_reconciliation_report
)
from invoice_reconciliation.pattern_analysis import (
    PatternAnalyzer, analyze_pickup_patterns
)


# ============================================================================
# Test Data Fixtures
# ============================================================================

@pytest.fixture
def sample_tracking_data():
    """Create sample tracking data for testing."""
    data = {
        'pickup_date': [
            '2025-10-05', '2025-10-12', '2025-10-19', '2025-10-26',  # Game Haven Sandy (EOW-ish)
            '2025-10-15',  # Game Haven West Jordan (Monthly)
            '2025-10-03', '2025-10-10', '2025-10-17',  # Game Haven Bountiful
            '2025-10-01', '2025-10-08', '2025-10-15', '2025-10-22', '2025-10-29',  # Koodegras Midvale (Weekly)
            '2025-10-07', '2025-10-14', '2025-10-21',  # Newgate Mall
        ],
        'location': [
            'Game Haven - Sandy - EOW', 'Game Haven - Sandy - EOW',
            'Game Haven - Sandy - EOW', 'Game Haven - Sandy - EOW',
            'Game Haven - West Jordan - Monthly',
            'Game Haven - Bountiful - On Request', 'Game Haven - Bountiful - On Request',
            'Game Haven - Bountiful - On Request',
            'Koodegras CBD - Midvale - Monthly', 'Koodegras CBD - Midvale - Monthly',
            'Koodegras CBD - Midvale - Monthly', 'Koodegras CBD - Midvale - Monthly',
            'Koodegras CBD - Midvale - Monthly',
            'Newgate Mall', 'Newgate Mall', 'Newgate Mall',
        ],
        'pickup_type': ['Regular Pickup'] * 16,
        'amount': [1500.00] * 16
    }
    return pd.DataFrame(data)


@pytest.fixture
def vendor_manager():
    """Get vendor manager with test configuration."""
    return get_vendor_manager()


@pytest.fixture
def cashman_config(vendor_manager):
    """Get Cash Man Services configuration."""
    return vendor_manager.get_vendor("Cash Man Services")


@pytest.fixture
def loomis_config(vendor_manager):
    """Get Loomis configuration."""
    return vendor_manager.get_vendor("Loomis")


@pytest.fixture
def sectran_config(vendor_manager):
    """Get Sectran configuration."""
    return vendor_manager.get_vendor("Sectran")


# ============================================================================
# Vendor Configuration Tests
# ============================================================================

class TestVendorConfig:
    """Tests for vendor configuration loading and management."""

    def test_load_vendor_config(self, vendor_manager):
        """Test loading vendor configuration."""
        vendors = vendor_manager.list_vendors()
        assert len(vendors) >= 3
        assert "Cash Man Services" in vendors
        assert "Loomis" in vendors
        assert "Sectran" in vendors

    def test_cashman_config(self, cashman_config):
        """Test Cash Man Services configuration."""
        assert cashman_config.vendor_id == "cashman"
        assert cashman_config.pricing_model == "per_service"

        # Check rates
        assert cashman_config.rates["smartsafe_pickup"] == 46.57
        assert cashman_config.rates["vault_management"] == 9.22
        assert cashman_config.rates["branch_delivery"] == 25.70
        assert cashman_config.rates["onetime_pickup"] == 125.00

        # Check fuel surcharge
        assert cashman_config.fuel_surcharge.enabled == True
        assert cashman_config.fuel_surcharge.surcharge_type == "percentage_variable"

    def test_loomis_config(self, loomis_config):
        """Test Loomis configuration."""
        assert loomis_config.vendor_id == "loomis"
        assert loomis_config.rates["pickup"] == 35.00

        # Check fixed fuel surcharge
        assert loomis_config.fuel_surcharge.enabled == True
        assert loomis_config.fuel_surcharge.surcharge_type == "percentage_fixed"
        assert loomis_config.fuel_surcharge.rate == 0.10

    def test_sectran_config(self, sectran_config):
        """Test Sectran configuration."""
        assert sectran_config.vendor_id == "sectran"
        assert sectran_config.rates["pickup"] == 42.00

        # No fuel surcharge
        assert sectran_config.fuel_surcharge.enabled == False

    def test_fuel_surcharge_calculation_variable(self, cashman_config):
        """Test variable fuel surcharge calculation."""
        # Cash Man: base $2.50, current $3.20, rate_per_10_cents = 0.02
        # Difference: $0.70, increments: 7, rate: 7 * 0.02 = 0.14 (14%)
        rate = cashman_config.get_fuel_surcharge_rate()
        assert abs(rate - 0.14) < 0.001

    def test_fuel_surcharge_calculation_fixed(self, loomis_config):
        """Test fixed fuel surcharge calculation."""
        rate = loomis_config.get_fuel_surcharge_rate()
        assert rate == 0.10

    def test_location_matching(self, cashman_config):
        """Test location matching with vendor mapping."""
        # Direct match
        assert cashman_config.match_location("Game Haven - Sandy - EOW", "Game Haven Sandy")

        # Mapped match
        assert cashman_config.match_location("Game Haven - Sandy - EOW", "Sandy")

        # Fuzzy match
        assert cashman_config.match_location("Game Haven Sandy", "Game Haven - Sandy")

    def test_schedule_detection(self, cashman_config):
        """Test schedule detection from location name."""
        assert cashman_config.get_schedule_from_location("Game Haven - Sandy - EOW") == "EOW"
        assert cashman_config.get_schedule_from_location("Game Haven - West Jordan - Monthly") == "Monthly"
        assert cashman_config.get_schedule_from_location("Game Haven - Bountiful - On Request") == "On Request"
        assert cashman_config.get_schedule_from_location("Newgate Mall") is None


# ============================================================================
# Estimation Tests
# ============================================================================

class TestEstimation:
    """Tests for the estimation engine."""

    def test_cashman_estimation(self, sample_tracking_data, cashman_config):
        """Test Cash Man Services monthly estimation."""
        engine = EstimationEngine(cashman_config)
        result = engine.estimate_monthly_charges(
            sample_tracking_data,
            "October 2025"
        )

        assert result.vendor_name == "Cash Man Services"
        assert result.service_month == "October 2025"

        # Total pickups: 4 + 1 + 3 + 5 + 3 = 16
        assert result.total_pickups == 16

        # Transport: 16 * $46.57 = $745.12
        expected_transport = 16 * 46.57
        transport_charge = sum(cb.amount for cb in result.charge_breakdown
                              if cb.rate_key == "smartsafe_pickup")
        assert abs(transport_charge - expected_transport) < 0.01

        # Vault: 16 * $9.22 = $147.52
        expected_vault = 16 * 9.22
        vault_charge = sum(cb.amount for cb in result.charge_breakdown
                          if cb.rate_key == "vault_management")
        assert abs(vault_charge - expected_vault) < 0.01

        # Base charges
        expected_base = expected_transport + expected_vault
        assert abs(result.base_charges - expected_base) < 0.01

        # Fuel surcharge: 14% of base
        expected_fuel = expected_base * 0.14
        assert abs(result.fuel_surcharge - expected_fuel) < 0.01

        # Total
        expected_total = expected_base + expected_fuel
        assert abs(result.total_estimated - expected_total) < 0.01

    def test_loomis_estimation(self, sample_tracking_data, loomis_config):
        """Test Loomis monthly estimation."""
        engine = EstimationEngine(loomis_config)
        result = engine.estimate_monthly_charges(
            sample_tracking_data,
            "October 2025"
        )

        # Total pickups: 16
        assert result.total_pickups == 16

        # Base: 16 * $35.00 = $560.00
        expected_base = 16 * 35.00
        assert abs(result.base_charges - expected_base) < 0.01

        # Fuel: 10% of base = $56.00
        expected_fuel = expected_base * 0.10
        assert abs(result.fuel_surcharge - expected_fuel) < 0.01

        # Total: $616.00
        expected_total = expected_base + expected_fuel
        assert abs(result.total_estimated - expected_total) < 0.01

    def test_sectran_estimation(self, sample_tracking_data, sectran_config):
        """Test Sectran monthly estimation (no fuel surcharge)."""
        engine = EstimationEngine(sectran_config)
        result = engine.estimate_monthly_charges(
            sample_tracking_data,
            "October 2025"
        )

        # Total pickups: 16
        assert result.total_pickups == 16

        # Base: 16 * $42.00 = $672.00
        expected_base = 16 * 42.00
        assert abs(result.base_charges - expected_base) < 0.01

        # No fuel surcharge
        assert result.fuel_surcharge == 0

        # Total equals base
        assert abs(result.total_estimated - expected_base) < 0.01

    def test_location_breakdown(self, sample_tracking_data, cashman_config):
        """Test estimation includes correct location breakdown."""
        engine = EstimationEngine(cashman_config)
        result = engine.estimate_monthly_charges(
            sample_tracking_data,
            "October 2025"
        )

        # Check each location
        assert "Game Haven - Sandy - EOW" in result.location_estimates
        sandy = result.location_estimates["Game Haven - Sandy - EOW"]
        assert sandy.pickup_count == 4
        assert sandy.schedule == "EOW"

        assert "Koodegras CBD - Midvale - Monthly" in result.location_estimates
        midvale = result.location_estimates["Koodegras CBD - Midvale - Monthly"]
        assert midvale.pickup_count == 5

    def test_estimation_with_as_of_date(self, sample_tracking_data, cashman_config):
        """Test estimation with specific as-of date (mid-month)."""
        engine = EstimationEngine(cashman_config)
        result = engine.estimate_monthly_charges(
            sample_tracking_data,
            "October 2025",
            as_of_date=date(2025, 10, 15)
        )

        # Should have projection since month is in progress
        assert result.projection is not None
        assert result.month_status == "IN_PROGRESS"
        assert result.projection.days_elapsed == 15
        assert result.projection.days_remaining == 16

    def test_estimation_report_format(self, sample_tracking_data, cashman_config):
        """Test estimation report formatting."""
        engine = EstimationEngine(cashman_config)
        result = engine.estimate_monthly_charges(
            sample_tracking_data,
            "October 2025"
        )

        report = format_estimation_report(result)

        assert "MONTHLY CHARGE ESTIMATION" in report
        assert "Cash Man Services" in report
        assert "October 2025" in report
        assert "PICKUP SUMMARY BY LOCATION" in report
        assert "CHARGE BREAKDOWN" in report


# ============================================================================
# Vendor Comparison Tests
# ============================================================================

class TestVendorComparison:
    """Tests for comparing costs across vendors."""

    def test_vendor_cost_comparison(self, sample_tracking_data, vendor_manager):
        """Test comparing costs across all vendors."""
        results = {}

        for vendor_name in ["Cash Man Services", "Loomis", "Sectran"]:
            vendor_config = vendor_manager.get_vendor(vendor_name)
            engine = EstimationEngine(vendor_config)
            results[vendor_name] = engine.estimate_monthly_charges(
                sample_tracking_data,
                "October 2025"
            )

        # All should have same number of pickups
        assert results["Cash Man Services"].total_pickups == 16
        assert results["Loomis"].total_pickups == 16
        assert results["Sectran"].total_pickups == 16

        # Cash Man should be most expensive (higher rates + fuel)
        # Loomis should be cheapest (lower base rate)
        assert results["Cash Man Services"].total_estimated > results["Sectran"].total_estimated
        assert results["Loomis"].total_estimated < results["Cash Man Services"].total_estimated


# ============================================================================
# Fuel Surcharge Tests
# ============================================================================

class TestFuelSurcharge:
    """Detailed tests for fuel surcharge calculations."""

    def test_cashman_fuel_surcharge(self, cashman_config):
        """Test Cash Man's variable fuel surcharge calculation."""
        # Given: $965.35 in base charges
        # Given: Fuel at $3.20 (base $2.50)
        # Expected: $135.15 surcharge (14%)

        base_charges = 965.35
        expected_rate = 0.14  # 14%
        expected_surcharge = 135.15

        actual_rate = cashman_config.get_fuel_surcharge_rate()
        actual_surcharge = cashman_config.calculate_fuel_surcharge(base_charges)

        assert abs(actual_rate - expected_rate) < 0.001
        assert abs(actual_surcharge - expected_surcharge) < 0.01

    def test_loomis_fuel_surcharge(self, loomis_config):
        """Test Loomis's fixed fuel surcharge calculation."""
        # Given: $525.00 in base charges
        # Expected: $52.50 surcharge (10% flat)

        base_charges = 525.00
        expected_surcharge = 52.50

        actual_surcharge = loomis_config.calculate_fuel_surcharge(base_charges)

        assert abs(actual_surcharge - expected_surcharge) < 0.01

    def test_fuel_price_update(self, vendor_manager):
        """Test updating fuel price."""
        vendor_name = "Cash Man Services"
        vendor = vendor_manager.get_vendor(vendor_name)

        old_price = vendor.fuel_surcharge.current_fuel_price
        old_rate = vendor.get_fuel_surcharge_rate()

        # Update to higher fuel price
        new_price = 3.50
        vendor_manager.update_fuel_price(vendor_name, new_price)

        new_rate = vendor.get_fuel_surcharge_rate()

        assert vendor.fuel_surcharge.current_fuel_price == new_price
        assert new_rate > old_rate

        # Reset
        vendor_manager.update_fuel_price(vendor_name, old_price)


# ============================================================================
# Pattern Analysis Tests
# ============================================================================

class TestPatternAnalysis:
    """Tests for pattern analysis."""

    def test_pattern_detection(self, sample_tracking_data):
        """Test detecting pickup patterns."""
        analyzer = PatternAnalyzer()
        result = analyzer.analyze_patterns(sample_tracking_data, months_back=1)

        assert result.locations_analyzed > 0
        assert len(result.location_patterns) > 0

    def test_schedule_detection(self, sample_tracking_data):
        """Test schedule detection from patterns."""
        analyzer = PatternAnalyzer()

        # Create data with clear weekly pattern
        weekly_data = pd.DataFrame({
            'pickup_date': pd.date_range('2025-10-01', periods=8, freq='W-TUE'),
            'location': ['Weekly Location'] * 8,
            'pickup_type': ['Regular'] * 8
        })

        result = analyzer.analyze_patterns(weekly_data, months_back=2)

        if 'Weekly Location' in result.location_patterns:
            pattern = result.location_patterns['Weekly Location']
            assert pattern.schedule_detected in ['Weekly', 'Unknown']


# ============================================================================
# Integration Tests
# ============================================================================

class TestIntegration:
    """Integration tests for the full workflow."""

    def test_full_estimation_workflow(self, sample_tracking_data, cashman_config):
        """Test complete estimation workflow."""
        # 1. Generate estimation
        engine = EstimationEngine(cashman_config)
        result = engine.estimate_monthly_charges(
            sample_tracking_data,
            "October 2025"
        )

        # 2. Verify result structure
        assert result.vendor_name == "Cash Man Services"
        assert result.total_pickups > 0
        assert result.total_estimated > 0
        assert len(result.location_estimates) > 0
        assert len(result.charge_breakdown) > 0

        # 3. Generate report
        report = format_estimation_report(result)
        assert len(report) > 0
        assert "October 2025" in report

    def test_month_parsing(self, sample_tracking_data, cashman_config):
        """Test different month format parsing."""
        engine = EstimationEngine(cashman_config)

        # Test various formats
        formats = [
            "October 2025",
            "2025-10",
            "10/2025"
        ]

        results = []
        for fmt in formats:
            result = engine.estimate_monthly_charges(
                sample_tracking_data,
                fmt
            )
            results.append(result.total_estimated)

        # All formats should produce same result
        assert all(abs(r - results[0]) < 0.01 for r in results)


# ============================================================================
# Edge Cases
# ============================================================================

class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_data(self, cashman_config):
        """Test handling of empty tracking data."""
        empty_data = pd.DataFrame({
            'pickup_date': [],
            'location': [],
            'pickup_type': []
        })

        engine = EstimationEngine(cashman_config)

        # This should not raise an error
        result = engine.estimate_monthly_charges(empty_data, "October 2025")

        assert result.total_pickups == 0
        assert result.total_estimated == 0

    def test_unknown_vendor(self, vendor_manager):
        """Test handling of unknown vendor."""
        vendor = vendor_manager.get_vendor("NonExistent Vendor")
        assert vendor is None

    def test_invalid_month_format(self, sample_tracking_data, cashman_config):
        """Test handling of invalid month format."""
        engine = EstimationEngine(cashman_config)

        with pytest.raises(ValueError):
            engine.estimate_monthly_charges(
                sample_tracking_data,
                "Invalid Month"
            )


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
