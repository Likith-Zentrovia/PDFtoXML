"""
Packaging Framework
===================

Provides abstract packaging interfaces and utilities for creating
document packages (ZIP, etc.).

Components:
- BasePackager: Abstract base class for packagers
- PackageResult: Container for packaging results
- ZipPackager: ZIP-based packaging utilities
"""

from rittdoc_core.packaging.base import (
    BasePackager,
    PackageResult,
    ChapterFragment,
    ImageMetadata,
)

from rittdoc_core.packaging.zip_packager import (
    ZipPackager,
)

__all__ = [
    # Base classes
    "BasePackager",
    "PackageResult",
    "ChapterFragment",
    "ImageMetadata",
    # ZIP packaging
    "ZipPackager",
]
