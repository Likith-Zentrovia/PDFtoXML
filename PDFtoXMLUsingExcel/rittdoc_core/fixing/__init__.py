"""
Fixing Framework
================

Provides abstract fixer interfaces and concrete implementations
for automatically fixing validation errors in XML documents.

Components:
- BaseFixer: Abstract base class for all fixers
- FixResult: Container for fix results
- DTDFixer: DTD-specific fix implementation
"""

from rittdoc_core.fixing.base import (
    BaseFixer,
    FixResult,
)

from rittdoc_core.fixing.dtd_fixer import (
    DTDFixer,
    ComprehensiveDTDFixer,
)

__all__ = [
    # Base classes
    "BaseFixer",
    "FixResult",
    # DTD fixing
    "DTDFixer",
    "ComprehensiveDTDFixer",
]
