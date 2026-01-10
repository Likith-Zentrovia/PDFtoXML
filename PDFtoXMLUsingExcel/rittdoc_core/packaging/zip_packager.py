"""
ZIP Packager
============

ZIP-based document packaging utilities for creating structured
document packages with chapters, media, and metadata.
"""

import io
import os
import shutil
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Iterator
import logging

try:
    from lxml import etree
    LXML_AVAILABLE = True
except ImportError:
    LXML_AVAILABLE = False

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from rittdoc_core.packaging.base import (
    BasePackager,
    PackageResult,
    ChapterFragment,
    ImageMetadata,
    MediaFetcher,
)
from rittdoc_core.xml.utils import local_name, extract_title_text

logger = logging.getLogger(__name__)


class ZipPackager(BasePackager):
    """
    ZIP-based document packager.

    Creates structured ZIP packages with:
    - Chapter XML files
    - Multimedia directory with images
    - Book.XML master file with entity declarations
    - Optional DTD files

    Example:
        packager = ZipPackager()
        result = packager.package(
            xml_path=Path("structured.xml"),
            output_path=Path("output.zip"),
            dtd_dir=Path("dtd/"),
            book_id="978-1-234-56789-0"
        )
    """

    def __init__(self,
                 media_dir_name: str = "multimedia",
                 chapter_prefix: str = "ch",
                 chapter_padding: int = 4):
        """
        Initialize ZIP packager.

        Args:
            media_dir_name: Name of the media directory in package
            chapter_prefix: Prefix for chapter filenames
            chapter_padding: Number of digits for chapter numbers
        """
        self.media_dir_name = media_dir_name
        self.chapter_prefix = chapter_prefix
        self.chapter_padding = chapter_padding

    @property
    def package_format(self) -> str:
        return "ZIP"

    def package(self,
                xml_path: Path,
                output_path: Path,
                media_fetcher: Optional[MediaFetcher] = None,
                dtd_dir: Optional[Path] = None,
                book_id: Optional[str] = None,
                **kwargs) -> PackageResult:
        """
        Create a ZIP package from structured XML.

        Args:
            xml_path: Path to the structured XML file
            output_path: Path for the output ZIP
            media_fetcher: Callback to fetch media content by filename
            dtd_dir: Optional directory containing DTD files to include
            book_id: Optional book identifier for naming
            **kwargs: Additional options

        Returns:
            PackageResult with packaging outcome
        """
        result = PackageResult(output_path=output_path)

        if not LXML_AVAILABLE:
            result.add_error("lxml is required for XML packaging")
            return result

        try:
            # Parse source XML
            tree = etree.parse(str(xml_path))
            root = tree.getroot()

            # Extract book metadata
            bookinfo = self.extract_bookinfo(root)
            result.metadata['bookinfo'] = bookinfo

            # Split into chapters
            chapters = self.split_into_chapters(root)
            result.chapters = chapters
            result.chapters_packaged = len(chapters)

            # Create ZIP package
            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                # Write DTD files
                if dtd_dir and dtd_dir.exists():
                    self._write_dtd_files(zf, dtd_dir)

                # Write chapter files
                for chapter in chapters:
                    chapter_xml = self._serialize_chapter(chapter)
                    zf.writestr(chapter.filename, chapter_xml)

                # Write media files
                if media_fetcher:
                    images = self._package_media(zf, root, media_fetcher)
                    result.images = images
                    result.images_packaged = len(images)

                # Write Book.XML master file
                book_xml = self._generate_book_xml(
                    chapters=chapters,
                    bookinfo=bookinfo,
                    book_id=book_id or bookinfo.get('isbn', 'BOOK')
                )
                zf.writestr("Book.XML", book_xml)

            # Calculate package size
            result.total_size_bytes = output_path.stat().st_size
            logger.info(f"Created package: {output_path} ({result.total_size_bytes} bytes)")

        except Exception as e:
            result.add_error(f"Packaging failed: {str(e)}")
            logger.error(f"Packaging error: {e}", exc_info=True)

        return result

    def split_into_chapters(self, root: Any, **kwargs) -> List[ChapterFragment]:
        """
        Split document into chapter fragments.

        Args:
            root: Root XML element
            **kwargs: Additional options

        Returns:
            List of ChapterFragment objects
        """
        chapters = []
        chapter_num = 0

        # Find chapter-level elements
        chapter_tags = {'chapter', 'appendix', 'preface', 'glossary', 'bibliography', 'index'}

        for child in root:
            tag = local_name(child)

            if tag in chapter_tags:
                chapter_num += 1
                entity_name = f"{self.chapter_prefix}{chapter_num:0{self.chapter_padding}d}"
                filename = f"{entity_name}.xml"
                title = extract_title_text(child) or f"Chapter {chapter_num}"

                chapters.append(ChapterFragment(
                    entity=entity_name,
                    filename=filename,
                    element=child,
                    kind=tag,
                    title=title,
                    order=chapter_num
                ))

        return chapters

    def extract_bookinfo(self, root: Any) -> Dict[str, Any]:
        """
        Extract book metadata from document.

        Args:
            root: Root XML element

        Returns:
            Dictionary of book metadata
        """
        info = {}

        # Try different info element locations
        for info_tag in ['bookinfo', 'info']:
            info_elem = root.find(f'.//{info_tag}')
            if info_elem is not None:
                break
        else:
            return info

        # Extract common fields
        field_mapping = {
            'title': ['title'],
            'subtitle': ['subtitle'],
            'author': ['author/firstname', 'author/surname', 'author/personname'],
            'isbn': ['biblioid[@class="isbn"]', 'isbn', 'productnumber'],
            'publisher': ['publisher/publishername', 'publisher'],
            'copyright': ['copyright/year'],
            'edition': ['edition'],
            'pubdate': ['pubdate'],
        }

        for field_name, xpaths in field_mapping.items():
            for xpath in xpaths:
                try:
                    elem = info_elem.find(xpath)
                    if elem is not None:
                        text = ''.join(elem.itertext()).strip()
                        if text:
                            info[field_name] = text
                            break
                except Exception:
                    continue

        return info

    def generate_toc(self, chapters: List[ChapterFragment]) -> Optional[Any]:
        """Generate TOC element from chapters."""
        if not LXML_AVAILABLE:
            return None

        toc = etree.Element('toc')
        title = etree.SubElement(toc, 'title')
        title.text = "Table of Contents"

        for chapter in chapters:
            tocentry = etree.SubElement(toc, 'tocentry')
            tocentry.set('linkend', chapter.entity)
            tocentry.text = chapter.title

        return toc

    def _serialize_chapter(self, chapter: ChapterFragment) -> bytes:
        """Serialize chapter element to XML bytes."""
        return etree.tostring(
            chapter.element,
            encoding='utf-8',
            xml_declaration=True,
            pretty_print=True
        )

    def _write_dtd_files(self, zf: zipfile.ZipFile, dtd_dir: Path) -> None:
        """Write DTD files to ZIP package."""
        for dtd_file in dtd_dir.rglob('*'):
            if dtd_file.is_file():
                arcname = dtd_file.relative_to(dtd_dir.parent)
                zf.write(dtd_file, arcname)
                logger.debug(f"Added DTD file: {arcname}")

    def _package_media(self,
                       zf: zipfile.ZipFile,
                       root: Any,
                       media_fetcher: MediaFetcher) -> List[ImageMetadata]:
        """
        Package media files into the ZIP.

        Args:
            zf: ZipFile object
            root: XML root element
            media_fetcher: Callback to fetch media content

        Returns:
            List of ImageMetadata for packaged images
        """
        images = []
        image_count = 0

        # Find all imagedata elements
        for imagedata in root.iter():
            if local_name(imagedata) != 'imagedata':
                continue

            fileref = imagedata.get('fileref')
            if not fileref:
                continue

            # Fetch image content
            content = media_fetcher(fileref)
            if content is None:
                logger.warning(f"Could not fetch image: {fileref}")
                continue

            image_count += 1

            # Determine output filename
            ext = Path(fileref).suffix.lower() or '.jpg'
            output_name = f"img_{image_count:04d}{ext}"
            arcname = f"{self.media_dir_name}/{output_name}"

            # Write to ZIP
            zf.writestr(arcname, content)

            # Update fileref in XML and ensure width/scalefit attributes are set
            imagedata.set('fileref', f"{self.media_dir_name}/{output_name}")
            imagedata.set('width', '100%')
            imagedata.set('scalefit', '1')

            # Collect metadata
            width, height = self._get_image_dimensions(content)
            images.append(ImageMetadata(
                filename=output_name,
                original_filename=fileref,
                chapter="",  # Would need context to determine
                figure_number=str(image_count),
                caption="",
                alt_text=imagedata.get('alt', ''),
                referenced_in_text=True,
                width=width,
                height=height,
                file_size=len(content),
                is_vector=ext in ['.svg', '.eps', '.pdf'],
            ))

            logger.debug(f"Packaged image: {fileref} -> {arcname}")

        return images

    def _get_image_dimensions(self, content: bytes) -> tuple:
        """Get image dimensions from content."""
        if not PIL_AVAILABLE:
            return (None, None)

        try:
            with Image.open(io.BytesIO(content)) as img:
                return img.size
        except Exception:
            return (None, None)

    def _generate_book_xml(self,
                          chapters: List[ChapterFragment],
                          bookinfo: Dict[str, Any],
                          book_id: str) -> bytes:
        """
        Generate Book.XML master file with entity declarations.

        Args:
            chapters: List of chapter fragments
            bookinfo: Book metadata dictionary
            book_id: Book identifier

        Returns:
            Book.XML content as bytes
        """
        # Build entity declarations
        entities = []
        for chapter in chapters:
            entities.append(f'<!ENTITY {chapter.entity} SYSTEM "{chapter.filename}">')

        entity_block = "\n  ".join(entities)

        # Build chapter references
        chapter_refs = []
        for chapter in chapters:
            chapter_refs.append(f"&{chapter.entity};")

        chapters_block = "\n".join(chapter_refs)

        # Generate XML content
        title = bookinfo.get('title', 'Untitled')

        book_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE book PUBLIC "-//OASIS//DTD DocBook XML V4.5//EN"
  "RITTDOCdtd/v1.1/RittDocBook.dtd" [
  {entity_block}
]>
<book id="{book_id}">
  <title>{title}</title>
{chapters_block}
</book>
'''

        return book_xml.encode('utf-8')


def make_file_fetcher(base_dir: Path) -> MediaFetcher:
    """
    Create a media fetcher that reads from a directory.

    Args:
        base_dir: Base directory to read files from

    Returns:
        MediaFetcher callback function
    """
    def fetcher(filename: str) -> Optional[bytes]:
        file_path = base_dir / filename
        if file_path.exists():
            return file_path.read_bytes()
        # Try without path components
        file_path = base_dir / Path(filename).name
        if file_path.exists():
            return file_path.read_bytes()
        return None

    return fetcher


def make_zip_fetcher(zip_path: Path) -> MediaFetcher:
    """
    Create a media fetcher that reads from a ZIP file.

    Args:
        zip_path: Path to the ZIP file

    Returns:
        MediaFetcher callback function
    """
    def fetcher(filename: str) -> Optional[bytes]:
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # Try exact match
                try:
                    return zf.read(filename)
                except KeyError:
                    pass

                # Try basename match
                basename = Path(filename).name
                for name in zf.namelist():
                    if Path(name).name == basename:
                        return zf.read(name)

                return None
        except Exception:
            return None

    return fetcher
