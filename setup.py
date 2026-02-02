"""Setup script for invoice reconciliation tool."""

from setuptools import setup, find_packages

setup(
    name="invoice-reconciliation",
    version="1.0.0",
    description="Invoice Reconciliation Tool for Armored Vendor Invoices",
    author="Cash Team",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "pdfplumber>=0.9.0",
        "pandas>=2.0.0",
        "click>=8.0.0",
        "tabulate>=0.9.0",
        "python-dateutil>=2.8.0",
    ],
    entry_points={
        "console_scripts": [
            "invoice-reconcile=invoice_reconciliation.cli:main",
        ],
    },
)
