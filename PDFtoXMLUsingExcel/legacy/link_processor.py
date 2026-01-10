"""
Link Processing Module for PDFtoXMLUsingExcel

Handles:
1. Figure cross-reference detection and linking
2. TOC/Index page number anchor link generation
3. Internal vs external link classification
4. Link preservation and transformation
"""

import re
import logging
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass
from lxml import etree
from reference_mapper import ReferenceMapper

logger = logging.getLogger(__name__)

# Patterns for detecting figure references in text
FIGURE_REF_PATTERNS = [
    # "Figure 1.1", "Fig. 1.1", "Fig 1.1"
    re.compile(r'\b(Figure|Fig\.?)\s+(\d+(?:\.\d+)?)\b', re.IGNORECASE),
    # "figure 1.1", "fig. 1.1"
    re.compile(r'\bfig(?:ure)?\.?\s+(\d+(?:\.\d+)?)\b', re.IGNORECASE),
    # "(Figure 1.1)", "(Fig. 1.1)"
    re.compile(r'\(\s*(Figure|Fig\.?)\s+(\d+(?:\.\d+)?)\s*\)', re.IGNORECASE),
    # "see Figure 1.1", "see Fig. 1.1"
    re.compile(r'\bsee\s+(Figure|Fig\.?)\s+(\d+(?:\.\d+)?)\b', re.IGNORECASE),
]

# Pattern for detecting table references
TABLE_REF_PATTERNS = [
    re.compile(r'\b(Table|Tbl\.?)\s+(\d+(?:\.\d+)?)\b', re.IGNORECASE),
    re.compile(r'\(Table\s+(\d+(?:\.\d+)?)\)', re.IGNORECASE),
]

# Pattern for page numbers (used in TOC/Index)
PAGE_NUMBER_PATTERN = re.compile(r'\b(\d+)\s*$')  # Page number at end of line

# Pattern for TOC entries with page numbers
TOC_ENTRY_PATTERN = re.compile(r'^(.*?)[\s.…]+(\d+)\s*$')


@dataclass
class FigureReference:
    """Represents a figure reference found in text"""
    full_text: str          # Full matched text (e.g., "Figure 1.1")
    figure_label: str       # Normalized label (e.g., "Figure 1.1")
    start_pos: int          # Start position in text
    end_pos: int            # End position in text
    target_image: Optional[str] = None  # Target image filename


@dataclass
class PageReference:
    """Represents a page number reference in TOC/Index"""
    page_number: str        # Page number as string
    page_id: Optional[str] = None  # Corresponding page_id in XML
    start_pos: int = 0      # Start position in text
    end_pos: int = 0        # End position in text


class LinkProcessor:
    """
    Processes links and cross-references in the document
    """

    def __init__(self, reference_mapper: ReferenceMapper):
        self.mapper = reference_mapper
        self.page_number_to_id: Dict[str, str] = {}  # Map printed page numbers to page_id

    def build_page_number_map(self, xml_root: etree._Element) -> None:
        """
        Build mapping from printed page numbers to page_id attributes.

        Args:
            xml_root: Root element of unified XML
        """
        self.page_number_to_id.clear()

        # Extract page_id from all <page> elements
        for page_elem in xml_root.findall('.//page'):
            page_id = page_elem.get('id', '')
            page_number_attr = page_elem.get('number', '')

            # page_id format is typically "page_1", "page_ii", etc.
            # Extract the actual printed page number from the page_id
            if page_id.startswith('page_'):
                printed_page = page_id.replace('page_', '')
                self.page_number_to_id[printed_page] = page_id
                logger.debug(f"Mapped page number '{printed_page}' to page_id '{page_id}'")

            # Also map from the number attribute if available
            if page_number_attr:
                self.page_number_to_id[page_number_attr] = page_id

        logger.info(f"Built page number mapping with {len(self.page_number_to_id)} entries")

    def find_figure_references(self, text: str) -> List[FigureReference]:
        """
        Find all figure references in the given text.

        Args:
            text: Text to search for figure references

        Returns:
            List of FigureReference objects
        """
        references = []

        for pattern in FIGURE_REF_PATTERNS:
            for match in pattern.finditer(text):
                # Extract the figure number (last group is usually the number)
                groups = match.groups()
                figure_num = groups[-1] if groups else None

                if figure_num:
                    # Build normalized label
                    figure_label = f"Figure {figure_num}"

                    # Try to find the resource
                    resource = self.mapper.get_resource_by_figure_label(figure_label)
                    target_image = None
                    if resource:
                        target_image = resource.final_name or resource.intermediate_name

                    ref = FigureReference(
                        full_text=match.group(0),
                        figure_label=figure_label,
                        start_pos=match.start(),
                        end_pos=match.end(),
                        target_image=target_image
                    )
                    references.append(ref)
                    logger.debug(f"Found figure reference: '{ref.full_text}' → {target_image}")

        return references

    def find_page_references(self, text: str, is_toc_or_index: bool = False) -> List[PageReference]:
        """
        Find page number references in TOC/Index entries.

        Args:
            text: Text to search for page references
            is_toc_or_index: Whether this text is from TOC or Index

        Returns:
            List of PageReference objects
        """
        if not is_toc_or_index:
            return []

        references = []

        # Try to match TOC entry pattern (title ... page_number)
        match = TOC_ENTRY_PATTERN.match(text.strip())
        if match:
            page_num = match.group(2)
            page_id = self.page_number_to_id.get(page_num)

            if page_id:
                ref = PageReference(
                    page_number=page_num,
                    page_id=page_id,
                    start_pos=match.start(2),
                    end_pos=match.end(2)
                )
                references.append(ref)
                logger.debug(f"Found page reference: {page_num} → {page_id}")

        return references

    def add_figure_links_to_text(self, text_element: etree._Element, text: str) -> None:
        """
        Add link elements for figure references in text.

        Modifies the text_element to include <link> elements for figure references.

        Args:
            text_element: XML element containing text
            text: The text content
        """
        references = self.find_figure_references(text)

        if not references:
            return

        # Sort by position (reverse) so we can replace from end to start
        references.sort(key=lambda r: r.start_pos, reverse=True)

        # Build new text with links
        # For simplicity, we'll add an attribute to mark figure references
        # The actual linking will be done during DocBook generation
        for ref in references:
            if ref.target_image:
                # Store reference info as attribute
                text_element.set(f'figref_{ref.start_pos}', f'{ref.figure_label}|{ref.target_image}')

    def add_page_links_to_toc(self, text_element: etree._Element, text: str) -> None:
        """
        Add internal page links to TOC/Index entries.

        Args:
            text_element: XML element containing TOC/Index entry
            text: The text content
        """
        references = self.find_page_references(text, is_toc_or_index=True)

        if not references:
            return

        # Add page_id as attribute for linking
        for ref in references:
            if ref.page_id:
                text_element.set('page_link', ref.page_id)
                text_element.set('page_number', ref.page_number)

    def classify_link(self, href: str) -> str:
        """
        Classify a link as external or internal.

        Args:
            href: The link URL/reference

        Returns:
            'external' or 'internal'
        """
        # External links start with http://, https://, ftp://, mailto:, etc.
        if re.match(r'^(https?|ftp|mailto):', href, re.IGNORECASE):
            return 'external'

        # Links starting with # are internal anchors
        if href.startswith('#'):
            return 'internal'

        # Relative paths are internal
        if not href.startswith('/') and '://' not in href:
            return 'internal'

        return 'internal'

    def process_links_in_element(self, element: etree._Element) -> Dict[str, int]:
        """
        Process all links in an element and its children.

        Args:
            element: XML element to process

        Returns:
            Statistics dict with counts of different link types
        """
        stats = {
            'external_links': 0,
            'internal_links': 0,
            'figure_links': 0,
            'page_links': 0
        }

        # Process <link> elements
        for link_elem in element.findall('.//link'):
            href = link_elem.get('href', '')
            if href:
                link_type = self.classify_link(href)
                link_elem.set('link_type', link_type)

                if link_type == 'external':
                    stats['external_links'] += 1
                else:
                    stats['internal_links'] += 1

        return stats


def extract_figure_label_from_caption(caption_text: str) -> Optional[str]:
    """
    Extract figure label (e.g., "Figure 1.1") from caption text.

    Args:
        caption_text: Full caption text

    Returns:
        Extracted figure label or None
    """
    if not caption_text:
        return None

    # Try to match "Figure X.Y" or "Fig. X.Y" at the start
    match = re.match(r'^(Figure|Fig\.?)\s+(\d+(?:\.\d+)?)', caption_text, re.IGNORECASE)
    if match:
        # Normalize to "Figure X.Y" format
        return f"Figure {match.group(2)}"

    return None


def generate_chapter_based_image_name(chapter_id: str, image_number: int, extension: str) -> str:
    """
    Generate chapter-based image name.

    Args:
        chapter_id: Chapter ID (e.g., "Ch0001")
        image_number: Sequential image number within chapter
        extension: File extension (e.g., "png", "jpg")

    Returns:
        Generated filename (e.g., "Ch0001_img001.png")
    """
    # Remove leading dot from extension if present
    extension = extension.lstrip('.')

    return f"{chapter_id}_img{image_number:03d}.{extension}"


def normalize_figure_label(label: str) -> str:
    """
    Normalize figure label for consistent matching.

    Args:
        label: Figure label (e.g., "Fig. 1.1", "figure 1.1")

    Returns:
        Normalized label (e.g., "Figure 1.1")
    """
    # Convert "Fig." or "fig" to "Figure"
    label = re.sub(r'^(Fig\.?|figure)', 'Figure', label, flags=re.IGNORECASE)

    # Remove extra whitespace
    label = ' '.join(label.split())

    return label
