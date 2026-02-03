"""Setup script for invoice reconciliation tool."""

from setuptools import setup, find_packages

setup(
    name="invoice-reconciliation",
    version="2.0.0",
    description="Invoice Reconciliation & Estimation System for Armored Vendor Invoices",
    author="Cash Team",
    packages=find_packages(),
    include_package_data=True,
    package_data={
        "invoice_reconciliation": ["vendors.json", "templates/*.html"],
    },
    python_requires=">=3.10",
    install_requires=[
        "pdfplumber>=0.9.0",
        "pandas>=2.0.0",
        "click>=8.0.0",
        "tabulate>=0.9.0",
        "python-dateutil>=2.8.0",
        "numpy>=1.24.0",
        "openpyxl>=3.1.0",
        "flask>=3.0.0",
        "werkzeug>=3.0.0",
        "streamlit>=1.28.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-cov>=4.0.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "invoice-reconcile=invoice_reconciliation.cli:main",
            "invoice-recon=invoice_reconciliation.new_cli:main",
        ],
    },
)
