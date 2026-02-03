"""
Vendor Configuration Manager

Handles loading, validating, and managing vendor-specific pricing configurations.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path


@dataclass
class FuelSurchargeConfig:
    """Fuel surcharge configuration for a vendor."""
    enabled: bool
    surcharge_type: str = "percentage_fixed"  # percentage_fixed, percentage_variable
    rate: float = 0.0  # For fixed percentage
    base_fuel_price: float = 0.0  # For variable calculation
    current_fuel_price: float = 0.0  # For variable calculation
    rate_per_10_cents: float = 0.0  # For variable calculation
    applies_to: List[str] = field(default_factory=list)

    def calculate_rate(self) -> float:
        """Calculate the current fuel surcharge rate."""
        if not self.enabled:
            return 0.0

        if self.surcharge_type == "percentage_fixed":
            return self.rate
        elif self.surcharge_type == "percentage_variable":
            # Calculate based on fuel price difference
            price_diff = self.current_fuel_price - self.base_fuel_price
            increments = int(price_diff / 0.10)  # Per 10 cents
            return increments * self.rate_per_10_cents

        return 0.0

    def calculate_surcharge(self, base_amount: float) -> float:
        """Calculate the fuel surcharge amount."""
        return base_amount * self.calculate_rate()


@dataclass
class BillingComponent:
    """A billing component/line item type."""
    name: str
    rate_key: str
    per: str  # "pickup", "delivery", "order"
    calculated: bool = False


@dataclass
class VendorConfig:
    """Configuration for a single vendor."""
    name: str
    vendor_id: str
    pricing_model: str
    rates: Dict[str, float]
    fuel_surcharge: FuelSurchargeConfig
    billing_components: List[BillingComponent]
    location_tracking_field: str
    location_mapping: Dict[str, List[str]]
    invoice_patterns: Dict[str, str]
    deposit_turnaround_days: int = 3

    def get_rate(self, rate_key: str) -> float:
        """Get the rate for a service type."""
        return self.rates.get(rate_key, 0.0)

    def get_fuel_surcharge_rate(self) -> float:
        """Get the current fuel surcharge rate as a percentage."""
        return self.fuel_surcharge.calculate_rate()

    def calculate_fuel_surcharge(self, base_charges: float, service_types: List[str] = None) -> float:
        """Calculate fuel surcharge for given base charges."""
        if not self.fuel_surcharge.enabled:
            return 0.0

        # If service types specified, only apply to applicable services
        if service_types and self.fuel_surcharge.applies_to:
            # Check if any service type is in applies_to
            if not any(st in self.fuel_surcharge.applies_to for st in service_types):
                return 0.0

        return self.fuel_surcharge.calculate_surcharge(base_charges)

    def match_location(self, invoice_location: str, tracking_location: str) -> bool:
        """Check if an invoice location matches a tracking location using vendor mapping."""
        # Direct match
        if invoice_location.lower().strip() == tracking_location.lower().strip():
            return True

        # Check mapping
        for invoice_loc, tracking_variants in self.location_mapping.items():
            if invoice_location.lower().strip() in invoice_loc.lower():
                for variant in tracking_variants:
                    if variant.lower() in tracking_location.lower():
                        return True
                    if tracking_location.lower() in variant.lower():
                        return True

        # Fuzzy match - check if significant parts match
        invoice_parts = set(invoice_location.lower().replace("-", " ").split())
        tracking_parts = set(tracking_location.lower().replace("-", " ").split())

        # Remove common words
        common_words = {"the", "a", "an", "of", "in", "at", "-", "eow", "monthly", "on", "request"}
        invoice_parts -= common_words
        tracking_parts -= common_words

        # Check overlap
        overlap = invoice_parts & tracking_parts
        if len(overlap) >= 2 or (len(overlap) == 1 and len(invoice_parts) <= 2):
            return True

        return False

    def get_schedule_from_location(self, location_name: str) -> Optional[str]:
        """Extract schedule type from location name (e.g., 'EOW', 'Monthly')."""
        location_upper = location_name.upper()

        if "EOW" in location_upper or "EVERY OTHER WEEK" in location_upper:
            return "EOW"
        elif "WEEKLY" in location_upper:
            return "Weekly"
        elif "MONTHLY" in location_upper:
            return "Monthly"
        elif "ON REQUEST" in location_upper or "ON-REQUEST" in location_upper:
            return "On Request"

        return None


class VendorConfigManager:
    """Manages vendor configurations."""

    def __init__(self, config_path: str = None):
        """Initialize the vendor config manager.

        Args:
            config_path: Path to vendors.json file. If None, uses default location.
        """
        if config_path is None:
            # Default to vendors.json in the same directory as this module
            module_dir = Path(__file__).parent
            config_path = module_dir / "vendors.json"

        self.config_path = Path(config_path)
        self.vendors: Dict[str, VendorConfig] = {}
        self.schedules: Dict[str, Dict] = {}
        self.service_types: Dict[str, List[str]] = {}
        self._rate_history: Dict[str, List[Dict]] = {}

        self._load_config()

    def _load_config(self):
        """Load vendor configurations from JSON file."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Vendor config file not found: {self.config_path}")

        with open(self.config_path, 'r') as f:
            data = json.load(f)

        # Load schedules
        self.schedules = data.get("schedules", {})

        # Load service types
        self.service_types = data.get("service_types", {})

        # Load vendors
        for vendor_name, vendor_data in data.get("vendors", {}).items():
            self.vendors[vendor_name] = self._parse_vendor(vendor_name, vendor_data)

    def _parse_vendor(self, name: str, data: Dict) -> VendorConfig:
        """Parse vendor data into VendorConfig object."""
        # Parse fuel surcharge config
        fs_data = data.get("fuel_surcharge", {})
        fuel_surcharge = FuelSurchargeConfig(
            enabled=fs_data.get("enabled", False),
            surcharge_type=fs_data.get("type", "percentage_fixed"),
            rate=fs_data.get("rate", 0.0),
            base_fuel_price=fs_data.get("base_fuel_price", 0.0),
            current_fuel_price=fs_data.get("current_fuel_price", 0.0),
            rate_per_10_cents=fs_data.get("rate_per_10_cents", 0.0),
            applies_to=fs_data.get("applies_to", [])
        )

        # Parse billing components
        components = []
        for comp_data in data.get("billing_components", []):
            components.append(BillingComponent(
                name=comp_data.get("name", ""),
                rate_key=comp_data.get("rate_key", ""),
                per=comp_data.get("per", "pickup"),
                calculated=comp_data.get("calculated", False)
            ))

        return VendorConfig(
            name=name,
            vendor_id=data.get("vendor_id", ""),
            pricing_model=data.get("pricing_model", "per_service"),
            rates=data.get("rates", {}),
            fuel_surcharge=fuel_surcharge,
            billing_components=components,
            location_tracking_field=data.get("location_tracking_field", ""),
            location_mapping=data.get("location_mapping", {}),
            invoice_patterns=data.get("invoice_patterns", {}),
            deposit_turnaround_days=data.get("deposit_turnaround_days", 3)
        )

    def get_vendor(self, vendor_name: str) -> Optional[VendorConfig]:
        """Get vendor configuration by name."""
        # Exact match
        if vendor_name in self.vendors:
            return self.vendors[vendor_name]

        # Case-insensitive match
        for name, config in self.vendors.items():
            if name.lower() == vendor_name.lower():
                return config
            if config.vendor_id.lower() == vendor_name.lower():
                return config

        return None

    def list_vendors(self) -> List[str]:
        """Get list of available vendor names."""
        return list(self.vendors.keys())

    def get_schedule(self, schedule_name: str) -> Optional[Dict]:
        """Get schedule configuration by name."""
        return self.schedules.get(schedule_name)

    def identify_service_type(self, description: str) -> Optional[str]:
        """Identify service type from a description string."""
        description_lower = description.lower()

        for service_type, keywords in self.service_types.items():
            for keyword in keywords:
                if keyword.lower() in description_lower:
                    return service_type

        return None

    def update_fuel_price(self, vendor_name: str, new_price: float, effective_date: datetime = None):
        """Update the current fuel price for a vendor.

        Args:
            vendor_name: Name of the vendor
            new_price: New fuel price
            effective_date: When this price takes effect (default: now)
        """
        vendor = self.get_vendor(vendor_name)
        if not vendor:
            raise ValueError(f"Vendor not found: {vendor_name}")

        if not vendor.fuel_surcharge.enabled:
            raise ValueError(f"Vendor {vendor_name} does not have fuel surcharge enabled")

        if vendor.fuel_surcharge.surcharge_type != "percentage_variable":
            raise ValueError(f"Vendor {vendor_name} does not use variable fuel surcharge")

        # Store history
        if vendor_name not in self._rate_history:
            self._rate_history[vendor_name] = []

        self._rate_history[vendor_name].append({
            "type": "fuel_price",
            "old_value": vendor.fuel_surcharge.current_fuel_price,
            "new_value": new_price,
            "effective_date": effective_date or datetime.now(),
            "timestamp": datetime.now()
        })

        # Update config
        vendor.fuel_surcharge.current_fuel_price = new_price

    def update_rate(self, vendor_name: str, rate_key: str, new_rate: float,
                    effective_date: datetime = None):
        """Update a rate for a vendor.

        Args:
            vendor_name: Name of the vendor
            rate_key: Key of the rate to update
            new_rate: New rate value
            effective_date: When this rate takes effect (default: now)
        """
        vendor = self.get_vendor(vendor_name)
        if not vendor:
            raise ValueError(f"Vendor not found: {vendor_name}")

        if rate_key not in vendor.rates:
            raise ValueError(f"Rate key {rate_key} not found for vendor {vendor_name}")

        # Store history
        if vendor_name not in self._rate_history:
            self._rate_history[vendor_name] = []

        self._rate_history[vendor_name].append({
            "type": "rate",
            "rate_key": rate_key,
            "old_value": vendor.rates[rate_key],
            "new_value": new_rate,
            "effective_date": effective_date or datetime.now(),
            "timestamp": datetime.now()
        })

        # Update config
        vendor.rates[rate_key] = new_rate

    def add_location_mapping(self, vendor_name: str, invoice_location: str,
                             tracking_variants: List[str]):
        """Add a location mapping for a vendor.

        Args:
            vendor_name: Name of the vendor
            invoice_location: Location name as it appears on invoices
            tracking_variants: List of possible names in tracking data
        """
        vendor = self.get_vendor(vendor_name)
        if not vendor:
            raise ValueError(f"Vendor not found: {vendor_name}")

        vendor.location_mapping[invoice_location] = tracking_variants

    def save_config(self, output_path: str = None):
        """Save the current configuration to a JSON file.

        Args:
            output_path: Path to save to. If None, overwrites original file.
        """
        output_path = output_path or self.config_path

        # Build data structure
        data = {
            "vendors": {},
            "schedules": self.schedules,
            "service_types": self.service_types
        }

        for name, vendor in self.vendors.items():
            data["vendors"][name] = {
                "vendor_id": vendor.vendor_id,
                "pricing_model": vendor.pricing_model,
                "rates": vendor.rates,
                "fuel_surcharge": {
                    "enabled": vendor.fuel_surcharge.enabled,
                    "type": vendor.fuel_surcharge.surcharge_type,
                    "rate": vendor.fuel_surcharge.rate,
                    "base_fuel_price": vendor.fuel_surcharge.base_fuel_price,
                    "current_fuel_price": vendor.fuel_surcharge.current_fuel_price,
                    "rate_per_10_cents": vendor.fuel_surcharge.rate_per_10_cents,
                    "applies_to": vendor.fuel_surcharge.applies_to
                },
                "billing_components": [
                    {
                        "name": comp.name,
                        "rate_key": comp.rate_key,
                        "per": comp.per,
                        "calculated": comp.calculated
                    }
                    for comp in vendor.billing_components
                ],
                "location_tracking_field": vendor.location_tracking_field,
                "location_mapping": vendor.location_mapping,
                "invoice_patterns": vendor.invoice_patterns,
                "deposit_turnaround_days": vendor.deposit_turnaround_days
            }

        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)

    def validate_config(self) -> List[str]:
        """Validate the current configuration.

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        for name, vendor in self.vendors.items():
            # Check required fields
            if not vendor.vendor_id:
                errors.append(f"{name}: Missing vendor_id")

            if not vendor.rates:
                errors.append(f"{name}: No rates defined")

            # Check billing components reference valid rates
            for comp in vendor.billing_components:
                if not comp.calculated and comp.rate_key not in vendor.rates:
                    errors.append(f"{name}: Billing component '{comp.name}' references unknown rate '{comp.rate_key}'")

            # Check fuel surcharge configuration
            if vendor.fuel_surcharge.enabled:
                if vendor.fuel_surcharge.surcharge_type == "percentage_variable":
                    if vendor.fuel_surcharge.base_fuel_price <= 0:
                        errors.append(f"{name}: Variable fuel surcharge requires base_fuel_price > 0")
                elif vendor.fuel_surcharge.surcharge_type == "percentage_fixed":
                    if vendor.fuel_surcharge.rate <= 0:
                        errors.append(f"{name}: Fixed fuel surcharge requires rate > 0")

        return errors


def select_vendor_interactive(config_manager: VendorConfigManager = None) -> VendorConfig:
    """Interactive vendor selection.

    Args:
        config_manager: Optional config manager. If None, creates new one.

    Returns:
        Selected vendor configuration
    """
    if config_manager is None:
        config_manager = VendorConfigManager()

    vendors = config_manager.list_vendors()

    print("\n" + "=" * 50)
    print("VENDOR SELECTION")
    print("=" * 50)
    print("\nAvailable Vendors:")

    for i, vendor_name in enumerate(vendors, 1):
        vendor = config_manager.get_vendor(vendor_name)
        fuel_info = ""
        if vendor.fuel_surcharge.enabled:
            rate = vendor.get_fuel_surcharge_rate()
            fuel_info = f" (Fuel: {rate*100:.1f}%)"
        print(f"  {i}. {vendor_name}{fuel_info}")

    print(f"  {len(vendors) + 1}. Add custom vendor")

    while True:
        try:
            choice = input(f"\nSelect vendor (1-{len(vendors) + 1}): ").strip()
            choice_num = int(choice)

            if 1 <= choice_num <= len(vendors):
                selected = vendors[choice_num - 1]
                vendor = config_manager.get_vendor(selected)
                print(f"\nSelected: {vendor.name}")
                print(f"  Vendor ID: {vendor.vendor_id}")
                print(f"  Pricing Model: {vendor.pricing_model}")
                print(f"  Rates: {', '.join(f'{k}=${v:.2f}' for k, v in vendor.rates.items())}")
                if vendor.fuel_surcharge.enabled:
                    print(f"  Fuel Surcharge: {vendor.get_fuel_surcharge_rate()*100:.1f}%")
                return vendor

            elif choice_num == len(vendors) + 1:
                print("\nCustom vendor configuration not yet implemented.")
                print("Please add vendor to vendors.json manually.")
                continue

            else:
                print("Invalid choice. Please try again.")

        except ValueError:
            print("Please enter a number.")


# Convenience functions
def load_vendor_config(vendor_name: str, config_path: str = None) -> VendorConfig:
    """Load a specific vendor configuration.

    Args:
        vendor_name: Name of the vendor
        config_path: Optional path to vendors.json

    Returns:
        VendorConfig for the specified vendor

    Raises:
        ValueError: If vendor not found
    """
    manager = VendorConfigManager(config_path)
    vendor = manager.get_vendor(vendor_name)

    if not vendor:
        available = ", ".join(manager.list_vendors())
        raise ValueError(f"Vendor '{vendor_name}' not found. Available: {available}")

    return vendor


def get_vendor_manager(config_path: str = None) -> VendorConfigManager:
    """Get a vendor configuration manager instance.

    Args:
        config_path: Optional path to vendors.json

    Returns:
        VendorConfigManager instance
    """
    return VendorConfigManager(config_path)
