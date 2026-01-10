"""
Conversion Tracking Module
==========================

Track conversion metadata and generate Excel dashboards with:
- ISBN, Publisher, Conversion Date/Time
- Status (Progress, Success, Failure)
- Type (PDF, ePub, DOCX, etc.)
- Template (Single Column, Double Column)
- Image counts (vector, raster)
- Table counts
"""

from rittdoc_core.tracking.conversion_tracker import (
    ConversionTracker,
    ConversionMetadata,
    ConversionStatus,
    ConversionType,
    TemplateType,
    track_conversion,
)

__all__ = [
    "ConversionTracker",
    "ConversionMetadata",
    "ConversionStatus",
    "ConversionType",
    "TemplateType",
    "track_conversion",
]
