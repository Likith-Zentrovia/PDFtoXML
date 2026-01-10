"""
Format Adapters
===============

Format-specific adapters for document conversion.
Each adapter handles converting a specific format (EPUB, PDF, DOCX, etc.)
to structured XML that can be processed by the core pipeline.

Components:
- BaseAdapter: Abstract base class for format adapters
- AdapterResult: Container for adapter results
"""

from rittdoc_core.adapters.base import (
    BaseAdapter,
    AdapterResult,
)

__all__ = [
    "BaseAdapter",
    "AdapterResult",
]
