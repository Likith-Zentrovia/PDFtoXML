"""
Base Packaging Classes
======================

Abstract base classes for the packaging framework. Extend these classes
to create packagers for different output formats.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
import logging

try:
    from lxml import etree
    LXML_AVAILABLE = True
except ImportError:
    LXML_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class ChapterFragment:
    """Represents a chapter fragment for packaging."""

    entity: str              # Entity name (e.g., "ch0001")
    filename: str            # Output filename (e.g., "ch0001.xml")
    element: Any             # XML element (lxml Element)
    kind: str                # "chapter", "appendix", "preface", etc.
    title: str               # Chapter title
    order: int = 0           # Order in book


@dataclass
class ImageMetadata:
    """Metadata for an image in the package."""

    filename: str                    # Final filename in package
    original_filename: str           # Original source filename
    chapter: str                     # Chapter ID where referenced
    figure_number: str               # Figure number
    caption: str                     # Image caption
    alt_text: str                    # Alt text for accessibility
    referenced_in_text: bool         # Whether referenced in text
    width: Optional[int] = None      # Image width in pixels
    height: Optional[int] = None     # Image height in pixels
    file_size: Optional[int] = None  # File size in bytes
    is_vector: bool = False          # Whether image is vector format
    is_cover: bool = False           # Whether image is cover


@dataclass
class PackageResult:
    """
    Container for packaging results.

    Attributes:
        success: Whether packaging succeeded
        output_path: Path to the created package
        chapters_packaged: Number of chapters packaged
        images_packaged: Number of images packaged
        total_size_bytes: Total package size in bytes
        chapters: List of ChapterFragment objects
        images: List of ImageMetadata objects
        errors: List of error messages
        metadata: Additional packaging metadata
    """
    success: bool = True
    output_path: Optional[Path] = None
    chapters_packaged: int = 0
    images_packaged: int = 0
    total_size_bytes: int = 0
    chapters: List[ChapterFragment] = field(default_factory=list)
    images: List[ImageMetadata] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_error(self, error: str) -> None:
        """Add an error message."""
        self.errors.append(error)
        self.success = False

    def summary(self) -> str:
        """Generate a text summary of packaging results."""
        status = "SUCCESS" if self.success else "FAILED"
        lines = [
            f"Packaging: {status}",
            f"Output: {self.output_path}",
            f"Chapters: {self.chapters_packaged}",
            f"Images: {self.images_packaged}",
        ]

        if self.total_size_bytes > 0:
            size_mb = self.total_size_bytes / (1024 * 1024)
            lines.append(f"Size: {size_mb:.2f} MB")

        if self.errors:
            lines.append(f"\nErrors ({len(self.errors)}):")
            for error in self.errors[:5]:
                lines.append(f"  - {error}")
            if len(self.errors) > 5:
                lines.append(f"  ... and {len(self.errors) - 5} more")

        return "\n".join(lines)


# Type alias for media fetcher callback
MediaFetcher = Callable[[str], Optional[bytes]]


class BasePackager(ABC):
    """
    Abstract base class for document packagers.

    Subclass this to create packagers for different output formats
    (ZIP, directory, custom archive, etc.).

    Example:
        class MyZipPackager(BasePackager):
            def package(self, xml_path: Path, output_path: Path, ...) -> PackageResult:
                result = PackageResult()
                # ... packaging logic ...
                return result
    """

    @abstractmethod
    def package(self,
                xml_path: Path,
                output_path: Path,
                media_fetcher: Optional[MediaFetcher] = None,
                **kwargs) -> PackageResult:
        """
        Create a package from XML input.

        Args:
            xml_path: Path to the source XML
            output_path: Path for the output package
            media_fetcher: Optional callback to fetch media content
            **kwargs: Additional packaging options

        Returns:
            PackageResult with packaging outcome
        """
        pass

    @abstractmethod
    def split_into_chapters(self, root: Any, **kwargs) -> List[ChapterFragment]:
        """
        Split a document into chapters.

        Args:
            root: Root XML element
            **kwargs: Additional options

        Returns:
            List of ChapterFragment objects
        """
        pass

    def extract_bookinfo(self, root: Any) -> Dict[str, Any]:
        """
        Extract book metadata from the document.

        Args:
            root: Root XML element

        Returns:
            Dictionary of book metadata
        """
        return {}

    def generate_toc(self, chapters: List[ChapterFragment]) -> Optional[Any]:
        """
        Generate table of contents structure.

        Args:
            chapters: List of chapter fragments

        Returns:
            TOC structure (XML element, dict, etc.) or None
        """
        return None

    @property
    def package_format(self) -> str:
        """Return the format of packages created (e.g., 'ZIP', 'directory')."""
        return "Unknown"

    @property
    def supported_media_types(self) -> List[str]:
        """Return list of supported media file extensions."""
        return ['.jpg', '.jpeg', '.png', '.gif', '.svg', '.tif', '.tiff']
