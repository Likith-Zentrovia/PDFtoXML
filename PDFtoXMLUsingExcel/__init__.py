"""
PDF to XML Conversion Pipeline (RittDoc)

A comprehensive PDF-to-DocBook XML conversion pipeline using Claude Vision AI.
This module provides both CLI and API interfaces for converting PDF documents
to validated RittDoc DTD-compliant DocBook XML packages.

Main Components:
- pdf_orchestrator: Main pipeline orchestration
- ai_pdf_conversion_service: Claude Vision AI integration
- editor_server: Web-based XML editor
- rittdoc_core: Core validation, fixing, and packaging library

Example Usage:
    # CLI usage
    python -m pdftoxml.pdf_orchestrator input.pdf --out ./output

    # API usage
    from pdftoxml import PDFConversionAPI
    api = PDFConversionAPI()
    result = api.convert("input.pdf", output_dir="./output")
"""

__version__ = "2.0.0"
__author__ = "RittDoc Team"

# Core exports for programmatic usage
from pathlib import Path
from typing import Optional, Dict, Any

# Import from rittdoc_core for public API
try:
    from rittdoc_core import (
        ConversionTracker,
        ConversionStatus,
        ConversionType,
        TemplateType,
        ConversionMetadata,
    )
    _TRACKING_AVAILABLE = True
except ImportError:
    _TRACKING_AVAILABLE = False


def get_version() -> str:
    """Return the package version."""
    return __version__


def get_pipeline_info() -> Dict[str, Any]:
    """Return information about the pipeline configuration."""
    return {
        "version": __version__,
        "tracking_available": _TRACKING_AVAILABLE,
        "default_model": "claude-sonnet-4-20250514",
        "default_dpi": 300,
        "default_temperature": 0.0,
    }


# Lazy imports for heavy modules
def get_orchestrator():
    """Get the PDF orchestrator module (lazy import)."""
    from . import pdf_orchestrator
    return pdf_orchestrator


def get_ai_service():
    """Get the AI PDF conversion service (lazy import)."""
    from . import ai_pdf_conversion_service
    return ai_pdf_conversion_service


def get_editor():
    """Get the editor server module (lazy import)."""
    from . import editor_server
    return editor_server


__all__ = [
    "__version__",
    "get_version",
    "get_pipeline_info",
    "get_orchestrator",
    "get_ai_service",
    "get_editor",
]

# Only export tracking classes if available
if _TRACKING_AVAILABLE:
    __all__.extend([
        "ConversionTracker",
        "ConversionStatus",
        "ConversionType",
        "TemplateType",
        "ConversionMetadata",
    ])
