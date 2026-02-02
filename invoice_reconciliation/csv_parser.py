"""CSV parser for pickup records."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import pandas as pd

from .models import Pickup


class PickupParseError(Exception):
    """Raised when pickup data cannot be parsed."""
    pass


class CSVPickupParser:
    """
    Parser for pickup CSV files.

    Flexible column mapping to handle various CSV formats.
    """

    # Default column name mappings (lowercase)
    DEFAULT_COLUMN_MAPPINGS = {
        "location_id": [
            "location_id", "location id", "locationid",
            "store_id", "store id", "storeid",
            "site_id", "site id", "siteid",
            "store #", "store#", "location #", "location#",
            "loc_id", "loc id", "locid",
        ],
        "location_name": [
            "location_name", "location name", "locationname",
            "store_name", "store name", "storename",
            "site_name", "site name", "sitename",
            "name",
        ],
        "pickup_date": [
            "pickup_date", "pickup date", "pickupdate",
            "date", "service_date", "service date", "servicedate",
            "transaction_date", "transaction date",
        ],
        "pickup_type": [
            "pickup_type", "pickup type", "pickuptype",
            "type", "service_type", "service type", "servicetype",
            "transaction_type", "transaction type",
        ],
        "amount": [
            "amount", "expected_amount", "expected amount",
            "pickup_amount", "pickup amount",
            "value", "total", "cash",
        ],
        "reference": [
            "reference", "reference_number", "reference number",
            "ref", "ref_number", "ref number",
            "confirmation", "confirmation_number",
            "ticket", "ticket_number",
        ],
    }

    DATE_FORMATS = [
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%m/%d/%y",
        "%m-%d-%y",
        "%d/%m/%Y",
        "%Y/%m/%d",
    ]

    def __init__(
        self,
        column_mappings: Optional[dict[str, str]] = None,
        date_format: Optional[str] = None,
    ):
        """
        Initialize the CSV parser.

        Args:
            column_mappings: Override default column mappings.
                            Keys: 'location_id', 'location_name', 'pickup_date', etc.
                            Values: Actual column name in CSV
            date_format: Specific date format to use (e.g., '%Y-%m-%d')
        """
        self.column_mappings = column_mappings or {}
        self.date_format = date_format

    def parse(self, csv_path: str | Path) -> list[Pickup]:
        """
        Parse a CSV file and extract pickup records.

        Args:
            csv_path: Path to the CSV file

        Returns:
            List of Pickup objects

        Raises:
            PickupParseError: If the CSV cannot be parsed
        """
        csv_path = Path(csv_path)
        if not csv_path.exists():
            raise PickupParseError(f"CSV file not found: {csv_path}")

        try:
            # Read CSV with pandas
            df = pd.read_csv(csv_path, dtype=str)
            df.columns = df.columns.str.strip()

            # Map columns
            col_map = self._resolve_columns(df.columns.tolist())

            if "location_id" not in col_map:
                raise PickupParseError(
                    f"Could not identify location ID column in {csv_path}. "
                    f"Columns found: {', '.join(df.columns)}"
                )

            if "pickup_date" not in col_map:
                raise PickupParseError(
                    f"Could not identify date column in {csv_path}. "
                    f"Columns found: {', '.join(df.columns)}"
                )

            # Parse rows
            pickups = []
            errors = 0

            for idx, row in df.iterrows():
                try:
                    pickup = self._parse_row(row, col_map)
                    if pickup:
                        pickups.append(pickup)
                except Exception:
                    errors += 1

            if errors > 0:
                print(f"Warning: {errors} row(s) could not be parsed in {csv_path}")

            return pickups

        except Exception as e:
            if isinstance(e, PickupParseError):
                raise
            raise PickupParseError(f"Failed to parse CSV {csv_path}: {e}") from e

    def _resolve_columns(self, columns: list[str]) -> dict[str, str]:
        """
        Resolve actual column names from CSV headers.

        Returns mapping of field name -> actual column name
        """
        col_map = {}
        columns_lower = {c.lower().strip(): c for c in columns}

        for field, explicit_col in self.column_mappings.items():
            if explicit_col in columns or explicit_col.lower() in columns_lower:
                col_map[field] = explicit_col if explicit_col in columns else columns_lower[explicit_col.lower()]

        # Auto-detect remaining columns
        for field, candidates in self.DEFAULT_COLUMN_MAPPINGS.items():
            if field in col_map:
                continue

            for candidate in candidates:
                if candidate in columns_lower:
                    col_map[field] = columns_lower[candidate]
                    break

        return col_map

    def _parse_row(self, row: pd.Series, col_map: dict[str, str]) -> Optional[Pickup]:
        """Parse a single CSV row into a Pickup object."""
        # Get location ID (required)
        location_id = self._get_value(row, col_map, "location_id")
        if not location_id:
            return None

        # Get pickup date (required)
        date_str = self._get_value(row, col_map, "pickup_date")
        if not date_str:
            return None

        pickup_date = self._parse_date(date_str)
        if not pickup_date:
            return None

        # Get optional fields
        location_name = self._get_value(row, col_map, "location_name")
        pickup_type = self._get_value(row, col_map, "pickup_type") or "Pickup"
        reference = self._get_value(row, col_map, "reference")

        # Get amount if available
        amount = None
        amount_str = self._get_value(row, col_map, "amount")
        if amount_str:
            try:
                cleaned = amount_str.replace(",", "").replace("$", "").strip()
                amount = Decimal(cleaned)
            except (InvalidOperation, ValueError):
                pass

        return Pickup(
            location_id=location_id,
            location_name=location_name,
            pickup_date=pickup_date,
            pickup_type=pickup_type,
            expected_amount=amount,
            reference_number=reference,
        )

    def _get_value(
        self, row: pd.Series, col_map: dict[str, str], field: str
    ) -> Optional[str]:
        """Get a value from a row using the column mapping."""
        col_name = col_map.get(field)
        if not col_name:
            return None

        value = row.get(col_name)
        if pd.isna(value):
            return None

        return str(value).strip() or None

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse a date string."""
        if not date_str:
            return None

        # If specific format provided, use it first
        if self.date_format:
            try:
                return datetime.strptime(date_str.strip(), self.date_format).date()
            except ValueError:
                pass

        # Try common formats
        for fmt in self.DATE_FORMATS:
            try:
                return datetime.strptime(date_str.strip(), fmt).date()
            except ValueError:
                continue

        return None


def parse_pickup_csv(
    csv_path: str | Path,
    column_mappings: Optional[dict[str, str]] = None,
    date_format: Optional[str] = None,
) -> list[Pickup]:
    """
    Convenience function to parse a pickup CSV file.

    Args:
        csv_path: Path to the CSV file
        column_mappings: Optional column name overrides
        date_format: Optional date format string

    Returns:
        List of Pickup objects
    """
    parser = CSVPickupParser(
        column_mappings=column_mappings,
        date_format=date_format,
    )
    return parser.parse(csv_path)


def parse_multiple_csvs(
    csv_paths: list[str | Path],
    column_mappings: Optional[dict[str, str]] = None,
) -> list[Pickup]:
    """
    Parse multiple CSV files and combine results.

    Args:
        csv_paths: List of paths to CSV files
        column_mappings: Optional column name overrides

    Returns:
        Combined list of Pickup objects from all files
    """
    parser = CSVPickupParser(column_mappings=column_mappings)
    all_pickups = []

    for csv_path in csv_paths:
        try:
            pickups = parser.parse(csv_path)
            all_pickups.extend(pickups)
        except PickupParseError as e:
            print(f"Error: {e}")

    return all_pickups
