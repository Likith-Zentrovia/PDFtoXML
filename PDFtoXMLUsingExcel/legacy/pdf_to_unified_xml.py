#!/usr/bin/env python3
"""
Master PDF to DocBook Integration Script

Complete workflow:
1. Process PDF text with column detection and reading order
2. Extract media (images, tables, vectors)
3. Merge text and media with overlap removal
4. Generate unified hierarchical XML with page number IDs
5. Auto-derive font roles from unified XML
6. Apply heuristics to create structured DocBook XML
7. Package DocBook XML into deliverable ZIP
8. Run RittDoc validation and create compliant package with validation report

This is the single entry point for the entire PDF → DocBook pipeline.

Usage:
    # Basic (Phase 1 only - unified XML)
    python pdf_to_unified_xml.py document.pdf

    # Full pipeline with validation
    python pdf_to_unified_xml.py document.pdf --full-pipeline

    # Full pipeline without validation
    python pdf_to_unified_xml.py document.pdf --full-pipeline --skip-validation
"""

import os
import sys
import argparse
import re
import json
import subprocess
import xml.etree.ElementTree as ET
import gc
from copy import deepcopy
from typing import Dict, List, Any, Tuple, Optional
from itertools import groupby
from pathlib import Path

# Import our processing modules
from pdf_to_excel_columns import pdf_to_excel_with_columns
from Multipage_Image_Extractor import extract_media_and_tables, extract_table_bboxes_fast
from enhanced_word_split_fixer import fix_word_splits_enhanced

# Import reference mapper for tracking image transformations
try:
    from reference_mapper import get_mapper, reset_mapper
    HAS_REFERENCE_MAPPER = True
except ImportError:
    HAS_REFERENCE_MAPPER = False
    print("Warning: reference_mapper not available - image tracking will be limited")


# Font style detection for semantic markup
def is_bold_font(font_name: str) -> bool:
    """Check if font name indicates bold styling."""
    if not font_name:
        return False
    font_lower = font_name.lower()
    return any(keyword in font_lower for keyword in ['bold', 'heavy', 'black', 'demi'])


def is_italic_font(font_name: str) -> bool:
    """Check if font name indicates italic styling."""
    if not font_name:
        return False
    font_lower = font_name.lower()
    return any(keyword in font_lower for keyword in ['italic', 'oblique', 'slant'])


def get_emphasis_role(font_name: str) -> Optional[str]:
    """
    Determine the emphasis role based on font name.
    
    Returns:
        'bold', 'italic', 'bold-italic', or None for regular text
    """
    if not font_name:
        return None
    
    is_bold = is_bold_font(font_name)
    is_italic = is_italic_font(font_name)
    
    if is_bold and is_italic:
        return 'bold-italic'
    elif is_bold:
        return 'bold'
    elif is_italic:
        return 'italic'
    else:
        return None


# Soft hyphen removal for paragraph merging
def remove_soft_hyphen_unified(prev_text, curr_text):
    """
    Remove soft hyphens between fragments and join hyphenated words.

    Handles cases like:
    - "compu-" (prev) + "tation rest" (curr) → returns ("computation ", "rest", True)
    - "Gauss (an-" (prev) + "other unit" (curr) → returns ("Gauss (another ", "unit", True)
    - "self-" (prev) + "driving" (curr) → keeps hyphen for compound words

    The function joins the hyphenated word parts together in prev_text and removes
    the first word from curr_text to avoid duplication when texts are later joined.

    Args:
        prev_text: Text from previous fragment (may end with hyphen)
        curr_text: Text from current fragment (continuation)

    Returns:
        Tuple of (modified_prev, modified_curr, should_dehyphenate)
    """
    if not prev_text.rstrip().endswith('-'):
        return prev_text, curr_text, False

    prev_stripped = prev_text.rstrip()
    curr_stripped = curr_text.lstrip()

    if not curr_stripped or not curr_stripped[0].islower():
        # Next doesn't start with lowercase - keep hyphen (new sentence or proper noun)
        return prev_text, curr_text, False

    # Check for compound word prefixes that should keep hyphen
    words_before = prev_stripped[:-1].split()
    if words_before:
        last_word_before_hyphen = words_before[-1]

        # Unambiguous compound word prefixes (always keep hyphen)
        always_keep_hyphen = {
            'self', 'non', 'anti', 'co', 'semi', 'quasi', 'pseudo', 'neo', 'proto'
        }

        # Keep hyphen for unambiguous compound words
        if (last_word_before_hyphen.lower() in always_keep_hyphen and
            prev_stripped.endswith(last_word_before_hyphen + '-')):
            return prev_text, curr_text, False

        # Keep hyphen if word before is all uppercase (acronym like "AI-powered")
        if last_word_before_hyphen.isupper() and len(last_word_before_hyphen) >= 2:
            return prev_text, curr_text, False

    # Extract the first word from curr (the continuation of the hyphenated word)
    curr_words = curr_stripped.split(None, 1)  # Split into first word and rest
    first_word = curr_words[0] if curr_words else ""
    rest_of_curr = curr_words[1] if len(curr_words) > 1 else ""

    # Join the hyphenated word: remove hyphen from prev and append first word of curr
    prev_dehyphenated = prev_stripped[:-1] + first_word  # "an-" + "other" = "another"

    # Preserve trailing space from prev if any
    trailing_space = prev_text[len(prev_stripped):]
    if trailing_space:
        prev_dehyphenated += trailing_space
    elif rest_of_curr:  # Add space before rest if there's more text
        prev_dehyphenated += " "

    return prev_dehyphenated, rest_of_curr, True

# Import validation pipeline (for --full-pipeline with validation)
try:
    from rittdoc_compliance_pipeline import RittDocCompliancePipeline
    VALIDATION_AVAILABLE = True
except ImportError:
    VALIDATION_AVAILABLE = False


def is_page_number_text(text: str) -> bool:
    """
    Check if text is a page number (roman or arabic numeral only).

    Args:
        text: Text content to check

    Returns:
        True if text is a standalone page number
    """
    text = text.strip()

    # Arabic numerals (1-9999)
    if re.match(r'^\d{1,4}$', text):
        return True

    # Roman numerals (case insensitive)
    if re.match(r'^[ivxlcdm]+$', text, re.IGNORECASE):
        return True

    return False


def extract_page_number(
    fragments: List[Dict[str, Any]],
    page_height: float,
    margin_threshold: float = 150.0
) -> str:
    """
    Extract page number from text fragments at top or bottom of page.

    Strategy:
    - Look for isolated numbers (arabic or roman) near top or bottom margins
    - Page numbers are typically within 150px of top/bottom edge
    - They are usually short standalone text fragments

    Args:
        fragments: List of text fragments on the page
        page_height: Height of the page
        margin_threshold: Distance from top/bottom to search (default: 150px)

    Returns:
        Page number string if found, empty string otherwise
    """
    # Look for page numbers at bottom of page first (most common)
    bottom_candidates = []
    top_candidates = []

    for f in fragments:
        text = f.get("text", "").strip()

        # Skip if not a potential page number
        if not is_page_number_text(text):
            continue

        # Check if at bottom margin
        fragment_bottom = f["top"] + f["height"]
        if fragment_bottom >= page_height - margin_threshold:
            bottom_candidates.append((fragment_bottom, text))

        # Check if at top margin
        fragment_top = f["top"]
        if fragment_top <= margin_threshold:
            top_candidates.append((fragment_top, text))

    # Prefer bottom page numbers (most common convention)
    if bottom_candidates:
        # Return the lowest (closest to bottom) candidate
        bottom_candidates.sort(reverse=True)
        return bottom_candidates[0][1]

    # Fallback to top page numbers
    if top_candidates:
        # Return the highest (closest to top) candidate
        top_candidates.sort()
        return top_candidates[0][1]

    return ""


def is_index_or_glossary_page(fragments: List[Dict[str, Any]]) -> bool:
    """
    Detect if a page is a reference page (TOC, Index, Glossary, etc.) based on content patterns.

    Reference pages typically have:
    1. Lines ending with page numbers (e.g., "Chapter 1 ..... 5" or "Term 123")
    2. Keywords like "Contents", "Table of Contents", "Index", "Glossary", "References" at the top
    3. High ratio of lines with trailing numbers

    For these pages, we should NOT merge text fragments into paragraphs
    to preserve the proper reading order (each entry should be separate).

    Args:
        fragments: List of text fragments on the page

    Returns:
        True if the page appears to be a reference page (TOC, Index, Glossary, etc.)
    """
    if not fragments:
        return False

    # Check first few lines for reference page keywords
    sorted_frags = sorted(fragments, key=lambda f: (f.get("top", 0), f.get("left", 0)))
    first_lines_text = " ".join(f.get("text", "").strip() for f in sorted_frags[:10]).lower()

    # FIXED: Added "contents" and "table of contents" for TOC detection
    has_index_keyword = any(kw in first_lines_text for kw in [
        "contents", "table of contents", "index", "glossary", "references", "appendix"
    ])

    # Count lines with trailing page numbers (pattern: text followed by number at end)
    # Index entries typically look like: "Term name 123" or "Term name ..... 123"
    lines_with_trailing_numbers = 0
    total_meaningful_lines = 0

    # Pattern: ends with 1-4 digit number, preceded by spaces, dots, or comma
    # Matches: "Chapter 1", "Algorithm 25", "Term ..... 123", "Entry, 45"
    trailing_number_pattern = re.compile(r'[\s\.,]+\d{1,4}\s*$')

    for frag in fragments:
        text = frag.get("text", "").strip()
        if len(text) < 3:  # Skip very short fragments
            continue
        total_meaningful_lines += 1
        if trailing_number_pattern.search(text):
            lines_with_trailing_numbers += 1

    # Calculate ratio of lines with trailing numbers
    # Require at least 3 meaningful lines to calculate ratio (avoid false positives on tiny fragments)
    number_ratio = lines_with_trailing_numbers / total_meaningful_lines if total_meaningful_lines >= 3 else 0

    # Consider it a reference page (TOC/Index/Glossary) if:
    # 1. Has reference keyword AND some trailing numbers (>10% threshold), OR
    # 2. Has very high ratio of lines with trailing numbers (>50%), OR
    # 3. Has strong reference keyword (Contents, Glossary, Appendix) - these are clear signals
    #    even without trailing numbers (Glossary has definitions, not page numbers)
    has_strong_reference_keyword = any(kw in first_lines_text for kw in [
        "contents", "table of contents", "glossary", "appendix"
    ])
    
    is_reference = (
        (has_index_keyword and number_ratio > 0.1) or  # Index/References need some page numbers
        number_ratio > 0.5 or  # High ratio of trailing numbers indicates reference page
        has_strong_reference_keyword  # TOC/Glossary/Appendix are clear signals
    )

    return is_reference


def point_in_rect(x: float, y: float, x1: float, y1: float, x2: float, y2: float) -> bool:
    """Check if point (x, y) is inside rectangle."""
    return x1 <= x <= x2 and y1 <= y <= y2


def rect_fully_contains_rect(
    inner_x1: float, inner_y1: float, inner_x2: float, inner_y2: float,
    outer_x1: float, outer_y1: float, outer_x2: float, outer_y2: float,
) -> bool:
    """Check if inner rectangle is fully contained within outer rectangle."""
    return (
        outer_x1 <= inner_x1 and
        outer_y1 <= inner_y1 and
        inner_x2 <= outer_x2 and
        inner_y2 <= outer_y2
    )


def fragment_fully_within_bbox(fragment: Dict[str, Any], bbox: Tuple[float, float, float, float]) -> bool:
    """
    Check if text fragment is ENTIRELY within a bounding box.

    Args:
        fragment: Text fragment with 'left', 'top', 'width', 'height'
        bbox: (x1, y1, x2, y2) bounding box

    Returns:
        True if entire fragment is inside bbox
    """
    frag_x1 = fragment["left"]
    frag_y1 = fragment["top"]
    frag_x2 = frag_x1 + fragment["width"]
    frag_y2 = frag_y1 + fragment["height"]

    bbox_x1, bbox_y1, bbox_x2, bbox_y2 = bbox
    return rect_fully_contains_rect(frag_x1, frag_y1, frag_x2, frag_y2, bbox_x1, bbox_y1, bbox_x2, bbox_y2)


def transform_fragment_to_media_coords(
    fragment: Dict[str, Any],
    html_page_width: float,
    html_page_height: float,
    media_page_width: float,
    media_page_height: float,
) -> Dict[str, float]:
    """
    Transform fragment coordinates from pdftohtml space to media.xml space.

    pdftohtml.xml uses scaled HTML coordinates (e.g., 823x1161)
    media.xml uses PyMuPDF/fitz coordinates (e.g., 549x774)

    Both use top-left origin, so only scaling is needed (no Y-flip).

    Args:
        fragment: Text fragment with 'left', 'top', 'width', 'height' in HTML coords
        html_page_width: Page width from pdftohtml.xml
        html_page_height: Page height from pdftohtml.xml
        media_page_width: Page width from media.xml
        media_page_height: Page height from media.xml

    Returns:
        Dict with 'left', 'top', 'width', 'height' in media.xml coordinates
    """
    if html_page_width <= 0 or html_page_height <= 0:
        return fragment  # Fallback to original if invalid

    scale_x = media_page_width / html_page_width
    scale_y = media_page_height / html_page_height

    return {
        "left": fragment["left"] * scale_x,
        "top": fragment["top"] * scale_y,
        "width": fragment["width"] * scale_x,
        "height": fragment["height"] * scale_y,
    }


def transform_media_coords_to_html(
    media_elem: ET.Element,
    media_page_width: float,
    media_page_height: float,
    html_page_width: float,
    html_page_height: float,
) -> None:
    """
    Transform media/table element coordinates from PyMuPDF space to HTML space IN-PLACE.
    
    This ensures all coordinates in unified.xml are in the same coordinate system as text.
    
    PyMuPDF (media.xml) uses PDF points (e.g., 595x842)
    pdftohtml uses HTML coordinates (e.g., 823x1161)
    
    Both use top-left origin, so only scaling is needed.
    
    Args:
        media_elem: ET.Element with x1, y1, x2, y2 attributes in PyMuPDF coords
        media_page_width: Page width from media.xml (PyMuPDF)
        media_page_height: Page height from media.xml (PyMuPDF)
        html_page_width: Page width from pdftohtml.xml (HTML)
        html_page_height: Page height from pdftohtml.xml (HTML)
    """
    if media_page_width <= 0 or media_page_height <= 0:
        return  # No transformation if invalid dimensions
    
    scale_x = html_page_width / media_page_width
    scale_y = html_page_height / media_page_height
    
    # Transform x1, y1, x2, y2 coordinates
    for coord_attr in ['x1', 'y1', 'x2', 'y2']:
        if coord_attr in media_elem.attrib:
            original_val = float(media_elem.get(coord_attr, '0'))
            if coord_attr in ['x1', 'x2']:
                transformed_val = original_val * scale_x
            else:  # y1, y2
                transformed_val = original_val * scale_y
            media_elem.set(coord_attr, f"{transformed_val:.2f}")
    
    # Transform table cell coordinates if this is a table
    if media_elem.tag == 'table':
        # Handle OLD format: <rows><row><cell>
        rows_elem = media_elem.find('rows')
        if rows_elem is not None:
            for row_elem in rows_elem.findall('row'):
                for cell_elem in row_elem.findall('cell'):
                    for coord_attr in ['x1', 'y1', 'x2', 'y2']:
                        if coord_attr in cell_elem.attrib:
                            original_val = float(cell_elem.get(coord_attr, '0'))
                            if coord_attr in ['x1', 'x2']:
                                transformed_val = original_val * scale_x
                            else:
                                transformed_val = original_val * scale_y
                            cell_elem.set(coord_attr, f"{transformed_val:.2f}")
        
        # Handle DocBook format: <tgroup><thead/tbody/tfoot><row><entry>
        tgroup = media_elem.find('tgroup')
        if tgroup is not None:
            # Transform cells in thead, tbody, and tfoot sections
            for section in ['thead', 'tbody', 'tfoot']:
                section_elem = tgroup.find(section)
                if section_elem is not None:
                    for row_elem in section_elem.findall('row'):
                        for entry_elem in row_elem.findall('entry'):
                            for coord_attr in ['x1', 'y1', 'x2', 'y2']:
                                if coord_attr in entry_elem.attrib:
                                    original_val = float(entry_elem.get(coord_attr, '0'))
                                    if coord_attr in ['x1', 'x2']:
                                        transformed_val = original_val * scale_x
                                    else:
                                        transformed_val = original_val * scale_y
                                    entry_elem.set(coord_attr, f"{transformed_val:.2f}")


def fragment_overlaps_media(fragment: Dict[str, Any], media_bbox: Tuple[float, float, float, float]) -> bool:
    """
    Check if text fragment's center overlaps with media bounding box.

    NOTE: This is used for non-table media (images, vectors).
    For tables, use fragment_overlaps_table_cells() instead.

    Args:
        fragment: Text fragment with 'left', 'top', 'width', 'height'
        media_bbox: (x1, y1, x2, y2) bounding box

    Returns:
        True if fragment center is inside media bbox
    """
    # Calculate fragment center
    center_x = fragment["left"] + fragment["width"] / 2.0
    center_y = fragment["top"] + fragment["height"] / 2.0

    x1, y1, x2, y2 = media_bbox
    return point_in_rect(center_x, center_y, x1, y1, x2, y2)


def parse_media_xml(media_xml_path: str) -> Dict[int, Dict[str, Any]]:
    """
    Parse the multimedia XML and organize by page.

    Returns:
        {page_number: {"media": [...], "tables": [...], "page_width": float, "page_height": float,
                       "render_mode": str, "render_reason": str}}
    """
    if not os.path.exists(media_xml_path):
        return {}

    tree = ET.parse(media_xml_path)
    root = tree.getroot()

    pages_media = {}

    for page_elem in root.findall("page"):
        page_num = int(page_elem.get("index", "0"))

        media_elements = page_elem.findall("media")
        table_elements = page_elem.findall("table")

        # Extract page dimensions from media.xml for coordinate transformation
        media_page_width = float(page_elem.get("width", "0"))
        media_page_height = float(page_elem.get("height", "0"))

        # Extract render mode for full-page images (forms, RTL content)
        render_mode = page_elem.get("render_mode", "")
        render_reason = page_elem.get("render_reason", "")

        pages_media[page_num] = {
            "media": media_elements,
            "tables": table_elements,
            "page_width": media_page_width,
            "page_height": media_page_height,
            "render_mode": render_mode,
            "render_reason": render_reason,
        }

    return pages_media


def find_caption_for_image(
    image_bbox: Tuple[float, float, float, float],
    fragments: List[Dict[str, Any]],
    page_height: float,
) -> str:
    """
    Find caption text near an image using spatial proximity and pattern matching.

    Strategy:
    - Look for text fragments near the image (within 100 pixels above/below)
    - Match patterns like "Figure X.Y", "Fig. X.Y", "Fig X.Y", etc.
    - Return the matched caption text or empty string

    Args:
        image_bbox: (x1, y1, x2, y2) bounding box of image
        fragments: List of text fragments on the same page
        page_height: Height of the page for coordinate normalization

    Returns:
        Caption text if found, empty string otherwise
    """
    import re

    img_x1, img_y1, img_x2, img_y2 = image_bbox
    img_center_y = (img_y1 + img_y2) / 2

    # Pattern to match figure captions
    # Matches: "Figure 1.1", "Fig. 1.2", "Fig 1.3", "  Figure 1.4", etc.
    # Also matches text containing "Figure X.Y" anywhere in the string
    caption_pattern = re.compile(r'Fig(?:ure)?\.?\s+\d+\.\d+', re.IGNORECASE)

    # Search for caption within 600 pixels above or below the image center
    # Large radius needed since some images are full-page or far from captions
    search_radius = 600.0
    candidates = []

    for f in fragments:
        frag_y = f["top"] + f["height"] / 2  # Fragment center Y
        distance = abs(frag_y - img_center_y)

        if distance <= search_radius:
            text = f.get("text", "").strip()
            if caption_pattern.search(text):  # Use search instead of match to find pattern anywhere
                candidates.append((distance, text, f))

    # Return the closest matching caption
    if candidates:
        candidates.sort(key=lambda x: x[0])  # Sort by distance
        _, caption_text, _ = candidates[0]
        return caption_text

    return ""


def extract_image_caption_associations(
    media_data: Dict[int, Dict[str, List[ET.Element]]],
    text_data: Dict[str, Any],
) -> Dict[int, List[Dict[str, Any]]]:
    """
    Step 1: Pre-Process and Collect Image Data with Captions.

    For each image in MultiMedia.xml:
    - Extract page number, bounding box, XML tag
    - Find caption text by searching nearby text fragments
    - Store as structured data for later insertion

    Returns:
        {page_num: [{"bbox": (x1,y1,x2,y2), "element": ET.Element, "caption": "..."}]}
    """
    image_data = {}

    for page_num, media_info in media_data.items():
        if page_num not in text_data["pages"]:
            continue

        page_info = text_data["pages"][page_num]
        fragments = page_info["fragments"]
        page_height = page_info["page_height"]

        page_images = []

        for media_elem in media_info.get("media", []):
            bbox = get_element_bbox(media_elem)
            caption = find_caption_for_image(bbox, fragments, page_height)

            page_images.append({
                "bbox": bbox,
                "element": media_elem,
                "caption": caption,
            })

            if caption:
                print(f"  Page {page_num}: Found caption '{caption}' for image {media_elem.get('id', 'unknown')}")

        if page_images:
            image_data[page_num] = page_images

    return image_data


def get_element_bbox(elem: ET.Element) -> Tuple[float, float, float, float]:
    """Extract bounding box from media or table element."""
    x1 = float(elem.get("x1", "0"))
    y1 = float(elem.get("y1", "0"))
    x2 = float(elem.get("x2", "0"))
    y2 = float(elem.get("y2", "0"))
    return (x1, y1, x2, y2)


def get_element_top(elem: ET.Element) -> float:
    """Get the top Y coordinate of an element."""
    return float(elem.get("y1", "0"))


def extract_table_cell_bboxes(table_elem: ET.Element) -> List[Tuple[float, float, float, float]]:
    """
    Extract all cell bounding boxes from a table element.

    Args:
        table_elem: <table> element with DocBook structure:
                    <table><tgroup><tbody><row><entry>

    Returns:
        List of (x1, y1, x2, y2) tuples for each cell
    """
    cell_bboxes = []

    # Handle DocBook table structure: <table><tgroup><tbody><row><entry>
    tgroup = table_elem.find("tgroup")
    if tgroup is None:
        # Fallback: Try old structure for backward compatibility
        rows_elem = table_elem.find("rows")
        if rows_elem is not None:
            for row_elem in rows_elem.findall("row"):
                for cell_elem in row_elem.findall("cell"):
                    x1 = float(cell_elem.get("x1", "0"))
                    y1 = float(cell_elem.get("y1", "0"))
                    x2 = float(cell_elem.get("x2", "0"))
                    y2 = float(cell_elem.get("y2", "0"))
                    cell_bboxes.append((x1, y1, x2, y2))
        return cell_bboxes

    # Extract from thead and tbody sections
    for section in ["thead", "tbody", "tfoot"]:
        section_elem = tgroup.find(section)
        if section_elem is not None:
            for row_elem in section_elem.findall("row"):
                for entry_elem in row_elem.findall("entry"):
                    x1 = float(entry_elem.get("x1", "0"))
                    y1 = float(entry_elem.get("y1", "0"))
                    x2 = float(entry_elem.get("x2", "0"))
                    y2 = float(entry_elem.get("y2", "0"))
                    cell_bboxes.append((x1, y1, x2, y2))

    return cell_bboxes


def fragment_overlaps_table_cells(
    fragment: Dict[str, Any],
    table_cell_bboxes: List[Tuple[float, float, float, float]],
) -> bool:
    """
    Check if a text fragment's CENTER is within any table cell.

    Uses center-point detection (same as media overlap) for consistency:
    - If the fragment's center falls within a cell, mark it as overlapping
    - This is more lenient than requiring full containment

    Args:
        fragment: Text fragment with 'left', 'top', 'width', 'height'
        table_cell_bboxes: List of (x1, y1, x2, y2) cell bounding boxes

    Returns:
        True if fragment center is inside any cell
    """
    # Calculate fragment center
    center_x = fragment["left"] + fragment["width"] / 2.0
    center_y = fragment["top"] + fragment["height"] / 2.0

    for cell_bbox in table_cell_bboxes:
        x1, y1, x2, y2 = cell_bbox
        if point_in_rect(center_x, center_y, x1, y1, x2, y2):
            return True
    return False


def get_fragment_table_cell_id(
    fragment: Dict[str, Any],
    table_cell_info: List[Tuple[str, Tuple[float, float, float, float]]],
) -> str:
    """
    Get the unique cell ID for a fragment if its center is within a table cell.

    Uses center-point detection for consistency with overlap detection.

    Args:
        fragment: Text fragment with 'left', 'top', 'width', 'height'
        table_cell_info: List of (cell_id, (x1, y1, x2, y2)) tuples

    Returns:
        Cell ID if fragment center is within a cell, empty string otherwise
    """
    # Calculate fragment center
    center_x = fragment["left"] + fragment["width"] / 2.0
    center_y = fragment["top"] + fragment["height"] / 2.0

    for cell_id, cell_bbox in table_cell_info:
        x1, y1, x2, y2 = cell_bbox
        if point_in_rect(center_x, center_y, x1, y1, x2, y2):
            return cell_id
    return ""


def assign_reading_order_to_media(
    media_elements: List[ET.Element],
    fragments: List[Dict[str, Any]],
    media_page_width: float = 0.0,
    media_page_height: float = 0.0,
    html_page_width: float = 0.0,
    html_page_height: float = 0.0,
) -> List[Tuple[ET.Element, float, int]]:
    """
    Assign reading order positions to media elements using caption-aware placement.
    
    NEW STRATEGY:
    1. First, try to find caption text near the image (e.g., "Figure 1.1 Caption")
    2. If caption found, place image IMMEDIATELY AFTER caption text
    3. If no caption, fall back to Y-coordinate positioning (old behavior)
    
    This ensures images/figures appear right after their caption text, maintaining
    proper reading flow: "Caption" → [Image] → "Next paragraph"
    
    CRITICAL: Transforms media coordinates from PyMuPDF space to HTML space before comparison.
    This ensures media elements are positioned correctly relative to text.

    Args:
        media_elements: List of media/table elements with x1,y1,x2,y2 in PyMuPDF coords
        fragments: Text fragments with top,left,width,height in HTML coords
        media_page_width: Page width in PyMuPDF coordinates (for transformation)
        media_page_height: Page height in PyMuPDF coordinates (for transformation)
        html_page_width: Page width in HTML coordinates (for transformation)
        html_page_height: Page height in HTML coordinates (for transformation)

    Returns:
        List of (element, baseline_position, reading_block) tuples
        where baseline_position is norm_baseline + 0.5 for proper interleaving
    """
    result = []
    
    # Calculate coordinate transformation scale
    has_valid_coords = (media_page_width > 0 and media_page_height > 0 and 
                       html_page_width > 0 and html_page_height > 0)
    scale_y = html_page_height / media_page_height if has_valid_coords else 1.0
    scale_x = html_page_width / media_page_width if has_valid_coords else 1.0

    # Caption patterns for figures and tables
    figure_pattern = re.compile(r'Fig(?:ure)?\.?\s+\d+[\.\-]?\d*', re.IGNORECASE)
    table_pattern = re.compile(r'Table\.?\s+\d+[\.\-]?\d*', re.IGNORECASE)

    for elem in media_elements:
        elem_id = elem.get('id', 'unknown')
        elem_type = elem.get('type', '')
        
        # Get element bounding box in PyMuPDF coordinates
        x1_pymupdf = float(elem.get('x1', '0'))
        y1_pymupdf = float(elem.get('y1', '0'))
        x2_pymupdf = float(elem.get('x2', '0'))
        y2_pymupdf = float(elem.get('y2', '0'))
        
        # Transform to HTML space to match fragment coordinates
        x1 = x1_pymupdf * scale_x if has_valid_coords else x1_pymupdf
        y1 = y1_pymupdf * scale_y if has_valid_coords else y1_pymupdf
        x2 = x2_pymupdf * scale_x if has_valid_coords else x2_pymupdf
        y2 = y2_pymupdf * scale_y if has_valid_coords else y2_pymupdf
        
        elem_center_y = (y1 + y2) / 2
        elem_center_x = (x1 + x2) / 2
        
        # STEP 1: Try to find caption text near this image/table
        caption_found = False
        caption_fragment = None
        
        # Choose pattern based on element type
        if elem_type == 'table' or 'table' in elem_id.lower():
            caption_pattern = table_pattern
        else:
            caption_pattern = figure_pattern
        
        # Search for caption within 600 pixels above or below the element center
        search_radius = 600.0
        candidates = []
        
        for f in fragments:
            frag_center_y = f["top"] + f["height"] / 2
            distance = abs(frag_center_y - elem_center_y)
            
            if distance <= search_radius:
                text = f.get("text", "").strip()
                if caption_pattern.search(text):
                    # Found a caption - calculate distance and store
                    candidates.append((distance, f, text))
        
        # Use closest matching caption
        if candidates:
            candidates.sort(key=lambda x: x[0])  # Sort by distance
            _, caption_fragment, caption_text = candidates[0]
            caption_found = True

            # Place image IMMEDIATELY AFTER caption using norm_baseline for positioning
            caption_baseline = caption_fragment.get("norm_baseline", caption_fragment.get("baseline", 0))
            reading_block = caption_fragment["reading_order_block"]

            # Use baseline + 0.5 to place after caption but before next text
            reading_order = caption_baseline + 0.5

            # Log successful caption match (for debugging)
            print(f"    {elem_id}: Placed after caption \"{caption_text[:40]}...\" (baseline={caption_baseline})")

        # STEP 2: Fallback to Y-coordinate positioning if no caption found
        if not caption_found:
            elem_top = y1

            # Find fragments before and after this element vertically
            before = [f for f in fragments if f["top"] < elem_top]

            if not before:
                # Media is before all text
                reading_order = 0.5
                reading_block = 1
            else:
                # Find the last fragment before this media (highest baseline = closest to media)
                last_before = max(before, key=lambda f: f.get("norm_baseline", f.get("baseline", 0)))
                last_baseline = last_before.get("norm_baseline", last_before.get("baseline", 0))
                reading_order = last_baseline + 0.5
                reading_block = last_before["reading_order_block"]

            # Log fallback placement
            # print(f"    {elem_id}: No caption found, placed by Y-coordinate (baseline={reading_order - 0.5})")

        result.append((elem, reading_order, reading_block))

    return result


def associate_captions_with_media(
    all_items: List[Tuple[str, float, int, ET.Element]],
) -> None:
    """
    Associate caption paragraphs with their corresponding media elements.

    For each paragraph that matches a figure/table caption pattern (e.g., "Figure 1.2",
    "Fig. 3", "Table A"), find the nearest media/table element and add a `figure_ref`
    attribute linking the caption to the media.

    This creates an explicit association in the XML:
        <para figure_ref="p1_fig1_2">Figure 1.2. System architecture...</para>
        <media id="p1_fig1_2" ... />

    Args:
        all_items: List of (type, sort_key, reading_block, element) tuples
                   where type is "para", "media", or "table"

    Modifies elements in place by adding `figure_ref` attribute to caption paragraphs.
    """
    # Pattern to match caption text
    figure_caption_pattern = re.compile(
        r'^(?:\s*(?:<[^>]+>)?\s*)*'  # Allow leading whitespace and XML-like tags
        r'(?:Figure|Fig\.?|Image|Plate|Diagram|Photo)\s+'  # Caption keyword
        r'(\d+(?:\.\d+)?[A-Za-z]?)',  # Number like 1, 1.2, 1.2a
        re.IGNORECASE
    )
    table_caption_pattern = re.compile(
        r'^(?:\s*(?:<[^>]+>)?\s*)*'  # Allow leading whitespace
        r'(?:Table|Tbl\.?)\s+'  # Table keyword
        r'(\d+(?:\.\d+)?[A-Za-z]?)',  # Number
        re.IGNORECASE
    )

    # Collect media/table elements by their position in the list
    media_elements = []
    para_elements = []

    for idx, (item_type, sort_key, reading_block, elem) in enumerate(all_items):
        if item_type == "media":
            media_elements.append((idx, elem, reading_block))
        elif item_type == "table":
            media_elements.append((idx, elem, reading_block))
        elif item_type == "para":
            para_elements.append((idx, elem, reading_block))

    # For each paragraph, check if it's a caption and find nearby media
    for para_idx, para_elem, para_block in para_elements:
        # Get paragraph text (including any nested emphasis text)
        para_text = get_full_element_text(para_elem)

        # Check if this paragraph is a caption
        figure_match = figure_caption_pattern.match(para_text)
        table_match = table_caption_pattern.match(para_text)

        if not figure_match and not table_match:
            continue

        # This is a caption - find the nearest media element in the same reading block
        best_media = None
        best_distance = float('inf')

        for media_idx, media_elem, media_block in media_elements:
            # Prefer same reading block
            if media_block != para_block:
                continue

            # Calculate distance (position in list)
            distance = abs(media_idx - para_idx)

            # Prefer media that comes immediately after the caption (within 3 positions)
            # or immediately before (within 2 positions)
            if media_idx > para_idx:  # Media after caption
                if distance <= 3 and distance < best_distance:
                    best_distance = distance
                    best_media = media_elem
            else:  # Media before caption (caption below image)
                if distance <= 2 and distance < best_distance:
                    best_distance = distance
                    best_media = media_elem

        if best_media is not None:
            media_id = best_media.get("id", "")
            if media_id:
                para_elem.set("figure_ref", media_id)


def get_full_element_text(elem: ET.Element) -> str:
    """
    Get all text content from an element, including nested children.

    Args:
        elem: XML element

    Returns:
        Combined text from element and all descendants
    """
    text_parts = []
    if elem.text:
        text_parts.append(elem.text)
    for child in elem:
        text_parts.append(get_full_element_text(child))
        if child.tail:
            text_parts.append(child.tail)
    return ''.join(text_parts)


def _find_paragraph_boundaries(frags: List[Dict[str, Any]]) -> List[float]:
    """
    Find paragraph boundary Y positions within a list of fragments.

    A paragraph boundary is identified by a significant vertical gap between
    consecutive fragments (more than typical line spacing).

    Args:
        frags: List of fragments sorted by Y position

    Returns:
        List of Y positions where paragraph boundaries occur (bottom of last fragment
        in each paragraph). Returns empty list if no clear boundaries found.
    """
    if len(frags) < 2:
        return []

    # Sort by top Y position
    sorted_frags = sorted(frags, key=lambda f: f["top"])

    # Calculate typical line height from fragments
    heights = [f["height"] for f in sorted_frags if f["height"] > 0]
    typical_height = sorted(heights)[len(heights) // 2] if heights else 12.0

    # Threshold for paragraph break: gap > 1.5x typical line height
    # This distinguishes normal line spacing from paragraph breaks
    gap_threshold = typical_height * 1.5

    boundaries = []
    for i in range(len(sorted_frags) - 1):
        curr_frag = sorted_frags[i]
        next_frag = sorted_frags[i + 1]

        curr_bottom = curr_frag["top"] + curr_frag["height"]
        next_top = next_frag["top"]
        gap = next_top - curr_bottom

        if gap > gap_threshold:
            # Paragraph boundary found - record the bottom Y of current fragment
            boundaries.append(curr_bottom)

    return boundaries


def _find_nearest_para_boundary_above(boundaries: List[float], target_y: float) -> float:
    """
    Find the nearest paragraph boundary that is above (less than) the target Y.

    Args:
        boundaries: List of paragraph boundary Y positions
        target_y: The Y position to find a boundary above

    Returns:
        The nearest boundary Y that is above target_y, or target_y if no boundary found
    """
    candidates = [b for b in boundaries if b < target_y]
    if not candidates:
        return target_y
    return max(candidates)  # Closest boundary above target


def split_blocks_by_media(
    fragments: List[Dict[str, Any]],
    media_with_order: List[Tuple[ET.Element, float, int]],
    media_page_width: float = 0.0,
    media_page_height: float = 0.0,
    html_page_width: float = 0.0,
    html_page_height: float = 0.0,
) -> Tuple[List[Dict[str, Any]], List[Tuple[ET.Element, float, int]]]:
    """
    Split reading order blocks when media elements fall within a block's vertical span.

    When an image/table is positioned within a reading_order_block (not at the top or bottom),
    this function increments the reading_order_block for all fragments BELOW the media,
    ensuring the media properly separates the content.

    IMPORTANT: Splits occur at PARAGRAPH BOUNDARIES, not at the exact media position.
    This ensures that paragraphs are not split in the middle, keeping text coherent.

    Args:
        fragments: Text fragments with reading_order_block assigned
        media_with_order: List of (element, reading_order, reading_block) tuples
        media_page_width: Page width in PyMuPDF coordinates (for transformation)
        media_page_height: Page height in PyMuPDF coordinates (for transformation)
        html_page_width: Page width in HTML coordinates (for transformation)
        html_page_height: Page height in HTML coordinates (for transformation)

    Returns:
        Tuple of (updated_fragments, updated_media_with_order)
    """
    if not fragments or not media_with_order:
        return fragments, media_with_order

    # Calculate coordinate transformation scale
    has_valid_coords = (media_page_width > 0 and media_page_height > 0 and
                       html_page_width > 0 and html_page_height > 0)
    scale_y = html_page_height / media_page_height if has_valid_coords else 1.0
    scale_x = html_page_width / media_page_width if has_valid_coords else 1.0

    # Group fragments by reading_order_block to find block vertical spans
    block_fragments = {}
    for f in fragments:
        block = f["reading_order_block"]
        if block not in block_fragments:
            block_fragments[block] = []
        block_fragments[block].append(f)

    # Calculate vertical span (min_y, max_y) for each block
    block_spans = {}
    for block, frags in block_fragments.items():
        if not frags:
            continue
        min_y = min(f["top"] for f in frags)
        max_y = max(f["top"] + f["height"] for f in frags)
        block_spans[block] = (min_y, max_y)

    # Find paragraph boundaries within each block
    block_para_boundaries = {}
    for block, frags in block_fragments.items():
        block_para_boundaries[block] = _find_paragraph_boundaries(frags)

    # Track block increment offsets: block_num -> additional offset to add
    # This accumulates as we find more media that split blocks
    block_increments = {}

    # Process each media element to see if it splits a block
    media_splits = []  # List of (split_y, original_block, media_idx)

    for media_idx, (elem, reading_order, reading_block) in enumerate(media_with_order):
        # Get media Y position in HTML space
        y1_pymupdf = float(elem.get('y1', '0'))
        y2_pymupdf = float(elem.get('y2', '0'))

        # Transform to HTML space
        media_y1 = y1_pymupdf * scale_y if has_valid_coords else y1_pymupdf
        media_y2 = y2_pymupdf * scale_y if has_valid_coords else y2_pymupdf
        media_top_y = media_y1  # Use top of media for finding boundary above

        # Check if this media is WITHIN a block's vertical span (not at edges)
        if reading_block in block_spans:
            block_min_y, block_max_y = block_spans[reading_block]

            # Media is "within" if its top is between the block's first and last fragment
            # Add some tolerance (10px) to avoid edge cases
            tolerance = 10.0

            if media_top_y > block_min_y + tolerance and media_top_y < block_max_y - tolerance:
                # Media is within block - find nearest paragraph boundary ABOVE the media
                para_boundaries = block_para_boundaries.get(reading_block, [])
                split_y = _find_nearest_para_boundary_above(para_boundaries, media_top_y)

                # Only split if we found a valid paragraph boundary above the media
                # If split_y equals media_top_y, no boundary was found - skip split
                if split_y < media_top_y:
                    media_splits.append((split_y, reading_block, media_idx))
                    elem_id = elem.get('id', 'unknown')
                    print(f"    Block split: {elem_id} at para boundary y={split_y:.1f} (media at y={media_top_y:.1f}) splits block {reading_block} (span: {block_min_y:.1f}-{block_max_y:.1f})")
                else:
                    elem_id = elem.get('id', 'unknown')
                    print(f"    Block split skipped: {elem_id} - no paragraph boundary found above media at y={media_top_y:.1f} in block {reading_block}")

    # If no splits, return unchanged
    if not media_splits:
        return fragments, media_with_order

    # Sort splits by block, then by Y position (top to bottom within each block)
    media_splits.sort(key=lambda x: (x[1], x[0]))

    # Calculate how much to increment each block
    # For each block that gets split, we need to increment all fragments below each split point
    # And increment all blocks AFTER the split block

    # Track cumulative block increment for blocks after splits
    cumulative_increment = 0
    processed_blocks = set()

    # Build a list of (block, y_threshold, increment) for fragment updates
    split_points = []  # [(original_block, y_threshold, increment_for_below)]

    current_block = None
    splits_in_block = 0

    for media_y, original_block, media_idx in media_splits:
        if original_block != current_block:
            # New block - reset split counter
            if current_block is not None:
                # Previous block had splits - blocks after it need to be incremented
                cumulative_increment += splits_in_block
            current_block = original_block
            splits_in_block = 0

        splits_in_block += 1

        # Add split point: fragments in this block below media_y get incremented
        # The increment value is cumulative_increment + splits_in_block
        split_points.append((original_block, media_y, cumulative_increment + splits_in_block))

    # Don't forget the last block's contribution
    if current_block is not None:
        cumulative_increment += splits_in_block

    # Now update fragments
    # Each fragment needs to have its reading_order_block incremented based on:
    # 1. Cumulative increment from all blocks before its block that had splits
    # 2. Additional increment if it's below a split point in its own block

    # Calculate base increment for each block (from splits in earlier blocks)
    base_increment_by_block = {}
    running_increment = 0

    # Get sorted unique blocks
    all_blocks = sorted(set(f["reading_order_block"] for f in fragments))

    split_blocks = set(sp[0] for sp in split_points)

    for block in all_blocks:
        base_increment_by_block[block] = running_increment
        if block in split_blocks:
            # Count splits in this block
            splits_in_this_block = sum(1 for sp in split_points if sp[0] == block)
            running_increment += splits_in_this_block

    # Update each fragment's reading_order_block
    for f in fragments:
        orig_block = f["reading_order_block"]
        frag_y = f["top"]

        # Start with base increment (from earlier blocks)
        increment = base_increment_by_block.get(orig_block, 0)

        # Add increment from splits within this fragment's block that are above it
        for split_block, split_y, _ in split_points:
            if split_block == orig_block and frag_y > split_y:
                increment += 1

        if increment > 0:
            f["reading_order_block"] = orig_block + increment

    # Update media reading_block values to account for splits
    # Media that caused a split should be in the NEW block (after the split point)
    # since the split occurs at a paragraph boundary ABOVE the media
    updated_media = []
    for media_idx, (elem, reading_order, reading_block) in enumerate(media_with_order):
        # Start with base increment (from splits in earlier blocks)
        new_reading_block = reading_block + base_increment_by_block.get(reading_block, 0)

        # Get media Y position in HTML space
        media_y1 = float(elem.get('y1', '0'))
        media_y2 = float(elem.get('y2', '0'))
        media_top_y = media_y1 * scale_y if has_valid_coords else media_y1

        # Add increment for splits within this media's block that are ABOVE it
        # (media below a split point moves to new block)
        for split_block, split_y, _ in split_points:
            if split_block == reading_block and media_top_y > split_y:
                new_reading_block += 1

        updated_media.append((elem, reading_order, new_reading_block))

    print(f"    Block splitting complete: {len(media_splits)} splits applied, max block incremented by {cumulative_increment}")

    return fragments, updated_media


def merge_text_and_media_simple(
    text_data: Dict[str, Any],
    media_data: Dict[int, Dict[str, List[ET.Element]]],
) -> Dict[int, Dict[str, Any]]:
    """
    Filter text fragments and prepare for paragraph creation, then position media.

    This function prepares text for paragraph creation by:
    1. Removing text that's INSIDE table cells (tables already contain the text)
    2. Removing text that's INSIDE image/vector bounding boxes (avoid duplication)
    3. Assigning reading order to images and tables based on their bbox position
    4. Processing pages with ONLY media (no text)

    NOTE: This function does NOT create paragraphs or merge content.
    Paragraphs are created later in create_unified_xml(), then media is merged into them.

    This ensures:
    - No duplicate content from text overlapping images/tables
    - Clean text fragments ready for paragraph grouping
    - Images and tables have correct reading order assigned

    Returns:
        {page_num: {"fragments": [...], "tables": [...], "media": [...]}}
    """
    merged_pages = {}

    # CRITICAL FIX: Get all page numbers from BOTH text and media
    # Pages with only images (no text) were being skipped!
    all_page_nums = set(text_data["pages"].keys()) | set(media_data.keys())
    
    for page_num in sorted(all_page_nums):
        # Check if page has text data
        page_info = text_data["pages"].get(page_num)

        # Get media for this page first (needed for both text and image-only pages)
        media_list = []
        table_list = []
        media_page_width = 0.0
        media_page_height = 0.0
        render_mode = ""
        render_reason = ""

        if page_num in media_data:
            media_list = media_data[page_num].get("media", [])
            table_list = media_data[page_num].get("tables", [])
            media_page_width = media_data[page_num].get("page_width", 0.0)
            media_page_height = media_data[page_num].get("page_height", 0.0)
            render_mode = media_data[page_num].get("render_mode", "")
            render_reason = media_data[page_num].get("render_reason", "")

        # SPECIAL HANDLING: Full-page images (forms, RTL content)
        # These pages were rendered as full-page images - skip text extraction
        if render_mode == "fullpage":
            # Calculate page dimensions for HTML space
            if media_page_width > 0 and media_page_height > 0:
                scale_factor = 1.5
                page_width = media_page_width * scale_factor
                page_height = media_page_height * scale_factor
            else:
                page_width = 823.0
                page_height = 1161.0

            print(f"  ✓ Page {page_num}: Full-page image ({render_reason})")

            merged_pages[page_num] = {
                "fragments": [],  # No text extraction for full-page images
                "tables": [],     # No tables for full-page images
                "media": media_list,  # Contains the fullpage media element
                "page_width": page_width,
                "page_height": page_height,
                "render_mode": render_mode,
                "render_reason": render_reason,
            }
            continue

        # Now handle text vs. image-only pages
        if page_info:
            # Page has text - process normally
            fragments = page_info["fragments"]
            page_width = page_info["page_width"]   # HTML/pdftohtml coordinates (e.g., 823)
            page_height = page_info["page_height"]  # HTML/pdftohtml coordinates (e.g., 1161)
        else:
            # Page has NO text (image-only page) - estimate HTML dimensions from media
            fragments = []
            
            # Convert PyMuPDF dimensions to HTML dimensions
            # Typical scale factor is ~1.5 (PDF points to HTML pixels)
            # Common conversions: 549→823, 774→1161, 595→892, 842→1263
            if media_page_width > 0 and media_page_height > 0:
                scale_factor = 1.5  # Approximate scale factor
                page_width = media_page_width * scale_factor
                page_height = media_page_height * scale_factor
                print(f"  ⚠ Page {page_num}: No text (image-only page), using estimated dimensions {page_width:.0f}x{page_height:.0f}")
            else:
                # Fallback to common page size
                page_width = 823.0
                page_height = 1161.0
                print(f"  ⚠ Page {page_num}: No text and no media dimensions, using default 823x1161")

        # ========== STEP A: Build bounding boxes for tables and media ==========

        # Build a list of (cell_id, bbox) for all table cells on this page
        # CRITICAL: Use extract_table_cell_bboxes() to handle BOTH old and DocBook formats
        all_table_cell_info = []
        all_table_cell_bboxes = []
        
        for table_idx, table_elem in enumerate(table_list):
            table_id = table_elem.get("id", f"table_{table_idx}")
            
            # Extract all cell bboxes from this table (handles both formats)
            cell_bboxes = extract_table_cell_bboxes(table_elem)
            
            # Build cell IDs and add to tracking lists
            for cell_idx, cell_bbox in enumerate(cell_bboxes):
                # Create unique cell ID: "table_id:cell_idx"
                cell_id = f"{table_id}:cell{cell_idx}"
                all_table_cell_info.append((cell_id, cell_bbox))
                all_table_cell_bboxes.append(cell_bbox)

        # Build list of media bounding boxes (images, vectors)
        all_media_bboxes = []
        for media_elem in media_list:
            bbox = get_element_bbox(media_elem)
            if bbox:
                all_media_bboxes.append(bbox)

        # ========== STEP B: Filter text inside tables and media ==========
        filtered_fragments = []
        removed_by_tables = 0
        removed_by_media = 0

        # Check if we have valid media dimensions for coordinate transformation
        has_valid_media_coords = media_page_width > 0 and media_page_height > 0

        for f in fragments:
            # CRITICAL: Add page number to fragment for paragraph boundary detection
            f["page_num"] = page_num

            # Transform fragment coordinates to media.xml space for overlap checks
            if has_valid_media_coords:
                f_transformed = transform_fragment_to_media_coords(
                    f, page_width, page_height, media_page_width, media_page_height
                )
            else:
                f_transformed = f  # Fallback to original coords if no media dimensions

            # Assign cell ID if fragment is inside a table cell (using transformed coords)
            cell_id = get_fragment_table_cell_id(f_transformed, all_table_cell_info)
            f["table_cell_id"] = cell_id

            # CRITICAL: Never filter script fragments (superscripts/subscripts merged with parent)
            # Scripts are inline with text, not inside tables/images
            # Check if this fragment has merged scripts OR is itself a script
            has_scripts = f.get("has_merged_scripts", False) or f.get("is_script", False)
            
            if not has_scripts:
                # Remove text if it's ENTIRELY within a table cell (using transformed coords)
                if fragment_overlaps_table_cells(f_transformed, all_table_cell_bboxes):
                    removed_by_tables += 1
                    continue

                # Remove text if it's inside an image/vector bounding box (using transformed coords)
                inside_media = False
                for media_bbox in all_media_bboxes:
                    if fragment_overlaps_media(f_transformed, media_bbox):
                        inside_media = True
                        break

                if inside_media:
                    removed_by_media += 1
                    continue

            filtered_fragments.append(f)

        # Log filtering statistics
        if removed_by_tables > 0 or removed_by_media > 0:
            print(f"  Page {page_num}: Removed {removed_by_tables} fragments inside tables, "
                  f"{removed_by_media} inside images, kept {len(filtered_fragments)}")

        # Assign reading order to media and tables based on bbox
        # Pass dimensions for coordinate transformation (PyMuPDF → HTML space)
        media_with_order = assign_reading_order_to_media(
            media_list, 
            filtered_fragments,
            media_page_width,
            media_page_height,
            page_width,
            page_height
        )
        tables_with_order = assign_reading_order_to_media(
            table_list,
            filtered_fragments,
            media_page_width,
            media_page_height,
            page_width,
            page_height
        )

        # Split reading order blocks when media/tables fall within a block's vertical span
        # This ensures images are properly interleaved between paragraphs, not grouped at end
        # Combine media and tables for block splitting analysis
        all_media_tables = media_with_order + tables_with_order
        if all_media_tables:
            filtered_fragments, updated_all = split_blocks_by_media(
                filtered_fragments,
                all_media_tables,
                media_page_width,
                media_page_height,
                page_width,
                page_height
            )
            # Split updated_all back into media and tables
            media_with_order = updated_all[:len(media_with_order)]
            tables_with_order = updated_all[len(media_with_order):]

        merged_pages[page_num] = {
            "page_width": page_width,
            "page_height": page_height,
            "media_page_width": media_page_width,  # Store for coordinate transformation
            "media_page_height": media_page_height,  # Store for coordinate transformation
            "fragments": filtered_fragments,
            "media": media_with_order,
            "tables": tables_with_order,
            "page_number_fragments": page_info.get("page_number_fragments", []) if page_info else [],  # Handle image-only pages
        }

    return merged_pages


def calculate_column_boundaries(
    fragments: List[Dict[str, Any]],
    page_width: float
) -> Dict[int, Dict[str, float]]:
    """
    Calculate typical left-start and right-end positions for each column.

    Args:
        fragments: All fragments on the page
        page_width: Width of the page

    Returns:
        Dictionary mapping col_id to {left_start, right_end, margin_tolerance}
        where margin_tolerance is used to check if lines are "full width"
    """
    from statistics import median

    # Group fragments by col_id
    col_groups = {}
    for f in fragments:
        col_id = f["col_id"]
        if col_id not in col_groups:
            col_groups[col_id] = []
        col_groups[col_id].append(f)

    # Calculate boundaries for each column
    boundaries = {}
    for col_id, frags in col_groups.items():
        if not frags:
            continue

        # Get all left positions and right positions
        left_positions = [f["left"] for f in frags]
        right_positions = [f["left"] + f["width"] for f in frags]

        # Use median for robustness (handles outliers better than mean)
        typical_left = median(left_positions)
        typical_right = median(right_positions)

        # For col_id 0 (full-width), use page width
        if col_id == 0:
            typical_left = 0
            typical_right = page_width

        # Calculate tolerance based on column width
        col_width = typical_right - typical_left
        # Use 10% of column width or 20 pixels, whichever is smaller
        tolerance = min(col_width * 0.10, 20.0)

        boundaries[col_id] = {
            "left_start": typical_left,
            "right_end": typical_right,
            "tolerance": tolerance
        }

    return boundaries


def is_paragraph_break(
    prev_fragment: Dict[str, Any],
    curr_fragment: Dict[str, Any],
    typical_line_height: float,
    column_boundaries: Dict[int, Dict[str, float]] = None,
) -> bool:
    """
    Determine if there should be a paragraph break between two fragments.

    Paragraph breaks occur when:
    1. Table cell ID changes (different table cells)
    2. Column ID changes (different columns)
    3. Reading block changes (transitions between full-width and columns)
    4. Vertical gap is NOT zero, negative, or very small
       - Combine when gap <= 3 pixels (continuous text flow)
       - Break when gap > 3 pixels (indicates paragraph separation)
    5. Current line doesn't extend to full width AND next line doesn't start at left
       - Only merge if current line is "full width" AND next line starts at "left edge"

    Args:
        prev_fragment: Previous text fragment
        curr_fragment: Current text fragment
        typical_line_height: Median line height for the page
        column_boundaries: Optional dict with column boundary information

    Returns:
        True if a paragraph break should occur
    """
    # Break if table_cell_id changes (different table cells)
    # CRITICAL: Never merge text across cell boundaries, even if same ColID/ReadingBlock
    prev_cell_id = prev_fragment.get("table_cell_id", "")
    curr_cell_id = curr_fragment.get("table_cell_id", "")

    # If either fragment has a cell ID, check if they're in the same cell
    if prev_cell_id or curr_cell_id:
        if prev_cell_id != curr_cell_id:
            return True

    # Break if col_id changes (different columns or full-width vs column)
    if prev_fragment["col_id"] != curr_fragment["col_id"]:
        return True

    # Break if reading_block changes (major content sections)
    # Note: This is redundant when processing within reading blocks, but kept for safety
    if prev_fragment["reading_order_block"] != curr_fragment["reading_order_block"]:
        return True

    # Calculate vertical gap between fragments
    prev_bottom = prev_fragment["top"] + prev_fragment["height"]
    curr_top = curr_fragment["top"]
    vertical_gap = curr_top - prev_bottom

    # Combine fragments when vertical gap is:
    # - Zero or negative (overlapping or touching)
    # - Very small (<= 3 pixels) indicating continuous text flow
    # Break otherwise (gap > 3 pixels indicates paragraph separation)
    SMALL_GAP_THRESHOLD = 3.0  # pixels
    if vertical_gap > SMALL_GAP_THRESHOLD:
        return True

    # Additional check: Only merge if BOTH lines are full-width:
    # - Previous line extends to full width (reaches right edge)
    # - Current line starts at left edge AND extends to right edge
    if column_boundaries:
        col_id = prev_fragment["col_id"]
        if col_id in column_boundaries:
            boundaries = column_boundaries[col_id]
            tolerance = boundaries["tolerance"]

            # Check if previous line extends close to right edge
            prev_right = prev_fragment["left"] + prev_fragment["width"]
            prev_extends_to_right = abs(prev_right - boundaries["right_end"]) <= tolerance

            # Check if current line starts close to left edge
            curr_left = curr_fragment["left"]
            curr_starts_at_left = abs(curr_left - boundaries["left_start"]) <= tolerance

            # Check if current line extends close to right edge
            curr_right = curr_fragment["left"] + curr_fragment["width"]
            curr_extends_to_right = abs(curr_right - boundaries["right_end"]) <= tolerance

            # Only merge if ALL conditions are true:
            # - Previous line extends to full width (reaches right edge)
            # - Current line starts at left edge
            # - Current line extends to full width (reaches right edge)
            if not (prev_extends_to_right and curr_starts_at_left and curr_extends_to_right):
                return True  # Break paragraph if conditions not met

    return False


def get_fragment_font_attrs(fragment: Dict[str, Any], original_texts: Dict[Tuple[int, int], ET.Element]) -> Dict[str, Any]:
    """
    Extract font attributes (font ID, size, bold, italic) from fragment.
    
    Args:
        fragment: Text fragment dictionary
        original_texts: Lookup dictionary for original pdftohtml elements
    
    Returns:
        Dictionary with font, size, bold, italic information
    """
    page_num = fragment.get("page_num", fragment.get("page", None))
    stream_index = fragment.get("stream_index")
    
    # Default values
    attrs = {
        "font": None,
        "size": 12.0,  # Default font size
        "bold": False,
        "italic": False,
    }
    
    # Look up original element
    if page_num is not None and stream_index is not None:
        orig_elem = original_texts.get((page_num, stream_index))
        if orig_elem is not None:
            # Extract font ID
            attrs["font"] = orig_elem.get("font")
            
            # Extract size
            size_str = orig_elem.get("size", "12")
            try:
                attrs["size"] = float(size_str)
            except (ValueError, TypeError):
                attrs["size"] = 12.0
    
    # Check for bold/italic in inner_xml
    inner_xml = fragment.get("inner_xml", "")
    attrs["bold"] = "<b>" in inner_xml or "<strong>" in inner_xml
    attrs["italic"] = "<i>" in inner_xml or "<em>" in inner_xml
    
    return attrs


def is_bullet_text(text: str) -> bool:
    """
    Check if text is a bullet point character or starts with bullet pattern.
    
    Detects:
    - Single bullet characters: •, ●, ○, ■, □, ▪, ▫, ·, -, *, –, —
    - Numbered lists: 1., 2., 3., or (1), (2), (3)
    - Lettered lists: a., b., c., or (a), (b), (c)
    """
    import re
    
    text = text.strip()
    if not text:
        return False
    
    # Single bullet characters
    BULLET_CHARS = {'•', '●', '○', '■', '□', '▪', '▫', '·', '-', '*', '–', '—', '→', '⇒', '▸', '►'}
    if text in BULLET_CHARS:
        return True
    
    # Bullet patterns (at start of text)
    BULLET_PATTERNS = [
        r'^[•●○■□▪▫·\-\*–—→⇒▸►]\s+',  # Bullet + space
        r'^\d+[\.\)]\s+',               # 1. or 1) followed by space
        r'^[a-zA-Z][\.\)]\s+',          # a. or a) followed by space
        r'^\([0-9]+\)\s+',              # (1) followed by space
        r'^\([a-zA-Z]\)\s+',            # (a) followed by space
        r'^[ivxlcdm]+[\.\)]\s+',        # Roman numerals: i., ii., iii.
    ]
    
    for pattern in BULLET_PATTERNS:
        if re.match(pattern, text, re.IGNORECASE):
            return True
    
    return False


def should_merge_fragments(prev_fragment: Dict[str, Any], curr_fragment: Dict[str, Any], baseline_tolerance: float = 3.0) -> bool:
    """
    Determine if two fragments should be merged into the same paragraph.

    Only merge if:
    1. They're on the same baseline (same line) within tolerance
    2. AND there's evidence of word continuation:
       - Previous text ends with space, OR
       - Current text starts with space, OR
       - Previous text ends with hyphen (word break), OR
       - Fragments are horizontally adjacent (left + width ≈ next left)

    This is a simplified approach that avoids complex paragraph detection
    which fails in TOC, Index, and other structured content.

    Args:
        prev_fragment: Previous text fragment
        curr_fragment: Current text fragment
        baseline_tolerance: Max difference in baseline to consider same line

    Returns:
        True if fragments should be merged
    """
    # Check if on same baseline (same line)
    baseline_diff = abs(prev_fragment["baseline"] - curr_fragment["baseline"])
    if baseline_diff > baseline_tolerance:
        return False

    # Must be same column and reading block
    if prev_fragment["col_id"] != curr_fragment["col_id"]:
        return False
    if prev_fragment["reading_order_block"] != curr_fragment["reading_order_block"]:
        return False

    # Check for space/hyphen evidence of word continuation
    prev_text = prev_fragment.get("text", "")
    curr_text = curr_fragment.get("text", "")

    # Merge if previous ends with space
    if prev_text.endswith(" "):
        return True

    # Merge if current starts with space
    if curr_text.startswith(" "):
        return True

    # Merge if previous ends with hyphen (word break)
    if prev_text.endswith("-"):
        return True

    # NEW: Check for horizontal adjacency with content analysis
    # Only merge adjacent fragments if there's clear evidence of continuation
    prev_right = prev_fragment["left"] + prev_fragment["width"]
    curr_left = curr_fragment["left"]
    horizontal_gap = curr_left - prev_right
    
    # If fragments are horizontally adjacent or very close (within 5px)
    if abs(horizontal_gap) <= 5.0:
        # Check if current text starts with punctuation - clear continuation
        # Examples: ", " or ". " or ": " etc.
        if curr_text and curr_text[0] in ',.;:!?)]}':
            return True
        
        # Check if previous text ends with opening punctuation - clear continuation
        # Examples: "(" or "[" or opening quote
        if prev_text and prev_text[-1] in '([{':
            return True
        
        # ENHANCED: Check if previous text ends with continuation words
        # Words like "including", "and", "or", "the", etc. indicate clear continuation
        prev_text_lower = prev_text.lower().rstrip()
        continuation_words = {'including', 'and', 'or', 'the', 'for', 'in', 'of', 'to', 'a', 'an', 
                             'as', 'with', 'from', 'by', 'at', 'on', 'into', 'through', 'during',
                             'such', 'both', 'each', 'all', 'other', 'these', 'those', 'many'}
        
        # Check if previous text ends with a continuation word
        for word in continuation_words:
            if prev_text_lower.endswith(word) or prev_text_lower.endswith(word + ','):
                return True
        
        # If no space evidence and next starts with capital letter,
        # might be intentional styling (e.g., separate words for layout)
        # DON'T merge in this case UNLESS horizontal gap is very small (< 2px)
        # Small gap indicates font style change, not intentional separation
        if curr_text and curr_text[0].isupper() and not prev_text.endswith(' '):
            # If gap is very small, likely a font style change - merge it
            if abs(horizontal_gap) <= 2.0:
                return True
            return False
    
    # Check for larger gaps with continuation evidence
    # If gap is moderate (5-15px) but previous ends with continuation word, still merge
    if 5.0 < abs(horizontal_gap) <= 15.0:
        prev_text_lower = prev_text.lower().rstrip()
        continuation_words = {'including', 'and', 'or', 'the', 'for', 'in', 'of', 'to'}
        
        for word in continuation_words:
            if prev_text_lower.endswith(word) or prev_text_lower.endswith(word + ','):
                return True
    
    # No evidence of continuation - don't merge
    return False


def group_fragments_into_paragraphs(
    fragments: List[Dict[str, Any]],
    typical_line_height: float,
    page_num: int = 0,
    debug: bool = False,
    page_width: float = None,
    original_texts: Dict[Tuple[int, int], ET.Element] = None,
) -> List[List[Dict[str, Any]]]:
    """
    Group consecutive fragments into paragraphs with font/style-aware detection.

    Paragraph breaks occur when:
    1. Font changes (different font ID)
    2. Font size changes significantly (>= 2pt difference)
    3. Style changes (bold/italic transitions in some cases)
    4. Vertical gap exceeds adaptive threshold
    5. Different column/reading block/page
    6. Bullet point detection (list items)
    7. Short line width (< 95% of typical) indicates paragraph end

    Args:
        fragments: List of text fragments sorted by reading order
        typical_line_height: Median line height for the page
        page_num: Page number for debug logging
        debug: Enable debug logging
        page_width: Width of the page (unused)
        original_texts: Lookup dictionary for original pdftohtml elements (for font info)

    Returns:
        List of paragraph groups (each group is a list of fragments)
    """
    if not fragments:
        return []

    paragraphs = []
    current_paragraph = [fragments[0]]

    # Base threshold - will be adaptive based on font size
    base_gap_threshold = typical_line_height * 1.5 if typical_line_height > 0 else 18.0

    # Calculate column widths for width-based list detection
    # Group fragments by col_id and calculate effective column width for each
    col_bounds = {}  # col_id -> (min_left, max_right)
    for frag in fragments:
        col_id = frag.get("col_id", 0)
        frag_left = frag["left"]
        frag_right = frag["left"] + frag["width"]
        if col_id not in col_bounds:
            col_bounds[col_id] = (frag_left, frag_right)
        else:
            min_left, max_right = col_bounds[col_id]
            col_bounds[col_id] = (min(min_left, frag_left), max(max_right, frag_right))

    # Calculate effective column width for each column
    col_widths = {col_id: max_right - min_left for col_id, (min_left, max_right) in col_bounds.items()}

    for i in range(1, len(fragments)):
        prev_fragment = fragments[i - 1]
        curr_fragment = fragments[i]

        # Calculate vertical gap
        prev_bottom = prev_fragment["top"] + prev_fragment["height"]
        curr_top = curr_fragment["top"]
        vertical_gap = curr_top - prev_bottom

        # Get font attributes for both fragments
        prev_attrs = get_fragment_font_attrs(prev_fragment, original_texts or {})
        curr_attrs = get_fragment_font_attrs(curr_fragment, original_texts or {})

        # Check if current fragment is a bullet point
        curr_text = curr_fragment.get("text", "").strip()
        is_bullet = is_bullet_text(curr_text)

        # Get previous fragment text for hyphenation check
        prev_text = prev_fragment.get("text", "")
        prev_ends_with_hyphen = prev_text.rstrip().endswith("-")

        # Check if previous fragment is a short line (potential paragraph ending)
        # Must be at least 100px wide to be considered a "line" - smaller fragments are
        # likely just punctuation or inline elements (e.g., "). " with width=14)
        MIN_LINE_WIDTH = 100
        SHORT_LINE_RATIO = 0.95  # Line < 95% of typical width suggests paragraph end
        prev_col_id = prev_fragment.get("col_id", 0)
        typical_full_width = col_widths.get(prev_col_id, 300)  # Use column width as typical
        prev_width = prev_fragment.get("width", 0)
        prev_is_short_line = (prev_width >= MIN_LINE_WIDTH and
                              prev_width < (typical_full_width * SHORT_LINE_RATIO))

        # Decision logic for starting new paragraph
        should_start_new_para = False
        break_reason = ""

        # 0. CRITICAL: Different page → always new paragraph
        prev_page = prev_fragment.get("page_num", prev_fragment.get("page", None))
        curr_page = curr_fragment.get("page_num", curr_fragment.get("page", None))
        if prev_page is not None and curr_page is not None and prev_page != curr_page:
            should_start_new_para = True
            break_reason = f"page boundary: {prev_page} → {curr_page}"

        # 1. Different column or reading block → check for continuation scenarios
        elif (prev_fragment["col_id"] != curr_fragment["col_id"] or
            prev_fragment["reading_order_block"] != curr_fragment["reading_order_block"]):
            # CROSS-BLOCK CONTINUATION LOGIC:
            # Check if this is a continuation across block boundary
            prev_text = prev_fragment.get("text", "")
            curr_text = curr_fragment.get("text", "")
            prev_text_stripped = prev_text.rstrip()
            curr_text_stripped = curr_text.lstrip()

            # Check if previous ends with sentence terminator (paragraph end)
            prev_ends_with_sentence_term = prev_text_stripped.endswith(('.', '!', '?', '."', '!"', '?"', '.)', '!)', '?)'))

            # Case 1: Hyphenated word continuation (e.g., "elec-" + "tromagnetic" → "electromagnetic")
            if prev_text_stripped.endswith("-") and curr_text_stripped and curr_text_stripped[0].islower():
                prev_dehyph, curr_dehyph, was_dehyph = remove_soft_hyphen_unified(prev_text, curr_text)
                if was_dehyph:
                    prev_fragment["text"] = prev_dehyph
                    curr_fragment["text"] = curr_dehyph
                    if "inner_xml" in prev_fragment:
                        prev_inner = prev_fragment["inner_xml"]
                        if prev_inner.rstrip().endswith('-'):
                            prev_fragment["inner_xml"] = prev_inner.rstrip()[:-1] + prev_inner[len(prev_inner.rstrip()):]
                    curr_fragment["reading_order_block"] = prev_fragment["reading_order_block"]
                    if debug:
                        print(f"      Fragment {i}: CROSS-BLOCK dehyphenation - merged across block boundary")
                    current_paragraph.append(curr_fragment)
                    continue

            # Case 2: Mid-sentence continuation (previous doesn't end with sentence terminator)
            # This handles cases like "frequently referred to " + "as "spins"."
            if not prev_ends_with_sentence_term and curr_text_stripped:
                # Check vertical proximity - should be close (same paragraph spacing)
                prev_bottom = prev_fragment["top"] + prev_fragment["height"]
                curr_top = curr_fragment["top"]
                vertical_gap = curr_top - prev_bottom

                # Allow continuation if gap is reasonable (< 1.5x typical line height)
                if vertical_gap < base_gap_threshold:
                    curr_fragment["reading_order_block"] = prev_fragment["reading_order_block"]
                    if debug:
                        print(f"      Fragment {i}: CROSS-BLOCK mid-sentence continuation (prev doesn't end with sentence terminator)")
                    current_paragraph.append(curr_fragment)
                    continue

            # No continuation pattern found - start new paragraph at block boundary
            should_start_new_para = True
            break_reason = "col/block change"

        # 2. CRITICAL: Same baseline check FIRST (before font/size checks)
        # Fragments on the same line should stay together regardless of font changes
        # Font changes on same line are represented as inline <emphasis>/<phrase> elements
        elif should_merge_fragments(prev_fragment, curr_fragment):
            # Same baseline with space/hyphen → continue paragraph
            # NEW: Apply dehyphenation if previous ends with hyphen
            was_dehyph = False
            prev_text = prev_fragment.get("text", "")
            curr_text = curr_fragment.get("text", "")
            if prev_text.endswith("-") and curr_text:
                prev_dehyph, curr_dehyph, was_dehyph = remove_soft_hyphen_unified(prev_text, curr_text)
                if was_dehyph:
                    prev_fragment["text"] = prev_dehyph
                    curr_fragment["text"] = curr_dehyph
                    # Also update inner_xml if present
                    if "inner_xml" in prev_fragment:
                        prev_inner = prev_fragment["inner_xml"]
                        if prev_inner.rstrip().endswith('-'):
                            prev_fragment["inner_xml"] = prev_inner.rstrip()[:-1] + prev_inner[len(prev_inner.rstrip()):]
            if debug:
                if was_dehyph:
                    print(f"      Fragment {i}: Continue para (same line, dehyphenated)")
                else:
                    print(f"      Fragment {i}: Continue para (same line)")
            current_paragraph.append(curr_fragment)
            continue

        # 3. Font size change detection - significant size change indicates new section/heading
        # A >= 2pt size change typically means header → body or body → header transition
        elif abs(prev_attrs["size"] - curr_attrs["size"]) >= 2.0:
            should_start_new_para = True
            break_reason = f"font size change: {prev_attrs['size']:.1f}pt → {curr_attrs['size']:.1f}pt"

        # 3.5. Font STYLE transition (bold↔regular) - indicates new logical block
        # This catches cases like contributor lists where name (bold) is followed by title (regular)
        elif prev_attrs["bold"] != curr_attrs["bold"]:
            should_start_new_para = True
            prev_style = "bold" if prev_attrs["bold"] else "regular"
            curr_style = "bold" if curr_attrs["bold"] else "regular"
            break_reason = f"font style change: {prev_style} → {curr_style}"

        # 4. Large vertical gap - indicates clear paragraph break
        # If gap is larger than typical line spacing * 1.5, it's a paragraph break
        # EXCEPTION: If previous ends with hyphen, still try to merge (hyphenation takes precedence)
        elif vertical_gap > base_gap_threshold:
            if prev_ends_with_hyphen:
                # Check for hyphenation continuation across large gap
                curr_text = curr_fragment.get("text", "")
                if curr_text.lstrip() and curr_text.lstrip()[0].islower():
                    prev_dehyph, curr_dehyph, was_dehyph = remove_soft_hyphen_unified(prev_text, curr_text)
                    if was_dehyph:
                        prev_fragment["text"] = prev_dehyph
                        curr_fragment["text"] = curr_dehyph
                        if "inner_xml" in prev_fragment:
                            prev_inner = prev_fragment["inner_xml"]
                            if prev_inner.rstrip().endswith('-'):
                                prev_fragment["inner_xml"] = prev_inner.rstrip()[:-1] + prev_inner[len(prev_inner.rstrip()):]
                        if debug:
                            print(f"      Fragment {i}: Continue para (hyphen continuation despite large gap={vertical_gap:.1f}px)")
                        current_paragraph.append(curr_fragment)
                        continue
            should_start_new_para = True
            break_reason = f"large gap={vertical_gap:.1f}px (threshold={base_gap_threshold:.1f})"

        # 5. Medium gap - check if normal line spacing within same paragraph
        elif vertical_gap > 3.0:
            # Check for line wrap: if current fragment starts significantly to the left
            # of where previous ended, it's a new line (not continuation)
            prev_right = prev_fragment["left"] + prev_fragment["width"]
            curr_left = curr_fragment["left"]
            horizontal_jump_back = prev_right - curr_left  # Positive if wrapped to new line

            # CRITICAL: Check for hyphen continuation FIRST before width-based detection
            # Lines ending with hyphens are often shorter but should merge with next line
            prev_text = prev_fragment.get("text", "").rstrip()
            curr_text = curr_fragment.get("text", "").lstrip()
            is_hyphen_continuation = (
                prev_text.endswith("-") and
                curr_text and
                curr_text[0].islower()
            )

            # WIDTH-BASED PARAGRAPH END DETECTION: If previous fragment is narrow (< 95% of column width),
            # it MIGHT indicate the END of a paragraph. But narrow lines can also occur when text
            # wraps around images/figures. To distinguish:
            # - Real paragraph end: narrow + ends with sentence punctuation (. ! ?) + next starts Capital
            # - Text wrap around image: narrow + no punctuation OR next starts lowercase → MERGE
            # EXCEPTION: Skip width check if this is a hyphen continuation
            prev_col_id = prev_fragment.get("col_id", 0)
            prev_col_width = col_widths.get(prev_col_id, page_width or 500)
            prev_width_ratio = prev_fragment["width"] / prev_col_width if prev_col_width > 0 else 1.0

            is_prev_narrow = prev_width_ratio < 0.95

            # Check for sentence-ending punctuation and capital letter start
            prev_text_stripped = prev_text.rstrip()
            curr_text_stripped = curr_text.lstrip()
            ends_with_sentence_punct = prev_text_stripped and prev_text_stripped[-1] in '.!?'
            next_starts_capital = curr_text_stripped and curr_text_stripped[0].isupper()

            # Only break when: narrow + ends sentence + next starts capital (real paragraph break)
            # Do NOT break when: narrow but no punctuation or next is lowercase (text wrap around image)
            is_paragraph_end = (
                is_prev_narrow and
                not is_hyphen_continuation and
                ends_with_sentence_punct and
                next_starts_capital
            )

            # If fragment jumped back more than 50px horizontally, it's a new line/paragraph
            # This handles cases where text wraps from right side to left margin
            if horizontal_jump_back > 50:
                should_start_new_para = True
                break_reason = f"line wrap (horiz jump={horizontal_jump_back:.0f}px, vert gap={vertical_gap:.1f}px)"
            # Previous fragment was narrow = end of paragraph, current is start of new paragraph
            elif is_paragraph_end:
                should_start_new_para = True
                break_reason = f"prev fragment ended paragraph (width={prev_width_ratio*100:.0f}% < 95%)"
            # Allow continuation if gap is less than font size (normal line spacing)
            elif vertical_gap <= curr_attrs["size"]:
                # Normal line spacing for this font size
                # NEW: Apply dehyphenation across line breaks
                was_dehyph = False
                if len(current_paragraph) > 0:
                    last_frag = current_paragraph[-1]
                    prev_text = last_frag.get("text", "")
                    curr_text = curr_fragment.get("text", "")
                    if prev_text.rstrip().endswith("-") and curr_text:
                        prev_dehyph, curr_dehyph, was_dehyph = remove_soft_hyphen_unified(prev_text, curr_text)
                        if was_dehyph:
                            last_frag["text"] = prev_dehyph
                            curr_fragment["text"] = curr_dehyph
                            if "inner_xml" in last_frag:
                                prev_inner = last_frag["inner_xml"]
                                if prev_inner.rstrip().endswith('-'):
                                    last_frag["inner_xml"] = prev_inner.rstrip()[:-1] + prev_inner[len(prev_inner.rstrip()):]
                if debug:
                    if was_dehyph:
                        print(f"      Fragment {i}: Continue para (normal line spacing={vertical_gap:.1f}px, dehyphenated)")
                    else:
                        print(f"      Fragment {i}: Continue para (normal line spacing={vertical_gap:.1f}px for size {curr_attrs['size']:.1f}pt)")
                current_paragraph.append(curr_fragment)
                continue
            else:
                # Larger gap, start new paragraph
                should_start_new_para = True
                break_reason = f"medium gap={vertical_gap:.1f}px"
        else:
            # Very small gap (<= 3px) - BUT still check for narrow fragments (list-like content)
            # CRITICAL: Check for hyphen continuation FIRST before width-based detection
            # Lines ending with hyphens are often shorter but should merge with next line
            prev_text = prev_fragment.get("text", "").rstrip()
            curr_text = curr_fragment.get("text", "").lstrip()
            is_hyphen_continuation = (
                prev_text.endswith("-") and
                curr_text and
                curr_text[0].islower()
            )

            # WIDTH-BASED PARAGRAPH END DETECTION for small gaps too
            # Narrow lines can occur from text wrapping around images. To distinguish real para breaks:
            # - Real paragraph end: narrow + ends with sentence punctuation (. ! ?) + next starts Capital
            # - Text wrap around image: narrow + no punctuation OR next starts lowercase → MERGE
            # EXCEPTION: Skip width check if this is a hyphen continuation
            prev_col_id = prev_fragment.get("col_id", 0)
            prev_col_width = col_widths.get(prev_col_id, page_width or 500)
            prev_width_ratio = prev_fragment["width"] / prev_col_width if prev_col_width > 0 else 1.0

            is_prev_narrow = prev_width_ratio < 0.95

            # Check for sentence-ending punctuation and capital letter start
            ends_with_sentence_punct = prev_text and prev_text[-1] in '.!?'
            next_starts_capital = curr_text and curr_text[0].isupper()

            # Only break when: narrow + ends sentence + next starts capital (real paragraph break)
            is_paragraph_end = (
                is_prev_narrow and
                not is_hyphen_continuation and
                ends_with_sentence_punct and
                next_starts_capital
            )

            if is_paragraph_end:
                # Previous fragment ended a paragraph, current starts new one
                should_start_new_para = True
                break_reason = f"prev fragment ended paragraph (width={prev_width_ratio*100:.0f}% < 95%, ends='{prev_text[-1] if prev_text else ''}', small gap={vertical_gap:.1f}px)"
            else:
                # Normal continuation for full-width fragments
                # NEW: Apply dehyphenation across line breaks
                was_dehyph = False
                if len(current_paragraph) > 0:
                    last_frag = current_paragraph[-1]
                    prev_text = last_frag.get("text", "")
                    curr_text = curr_fragment.get("text", "")
                    if prev_text.endswith("-") and curr_text:
                        prev_dehyph, curr_dehyph, was_dehyph = remove_soft_hyphen_unified(prev_text, curr_text)
                        if was_dehyph:
                            last_frag["text"] = prev_dehyph
                            curr_fragment["text"] = curr_dehyph
                            if "inner_xml" in last_frag:
                                prev_inner = last_frag["inner_xml"]
                                if prev_inner.rstrip().endswith('-'):
                                    last_frag["inner_xml"] = prev_inner.rstrip()[:-1] + prev_inner[len(prev_inner.rstrip()):]
                if debug:
                    if was_dehyph:
                        print(f"      Fragment {i}: Continue para (small gap={vertical_gap:.1f}px, dehyphenated)")
                    else:
                        print(f"      Fragment {i}: Continue para (small gap={vertical_gap:.1f}px)")
                current_paragraph.append(curr_fragment)
                continue
        
        # Start new paragraph if needed
        if should_start_new_para:
            if debug and break_reason:
                print(f"      Fragment {i}: New para ({break_reason})")
            paragraphs.append(current_paragraph)
            current_paragraph = [curr_fragment]
        else:
            current_paragraph.append(curr_fragment)

    # Add the last paragraph
    if current_paragraph:
        paragraphs.append(current_paragraph)

    if debug:
        print(f"  Page {page_num}: Created {len(paragraphs)} paragraphs from {len(fragments)} fragments")

    return paragraphs


def should_merge_cross_page_paragraphs(
    last_para_fragments: List[Dict[str, Any]],
    first_para_fragments: List[Dict[str, Any]],
    original_texts: Dict[Tuple[int, int], ET.Element],
    debug: bool = False,
) -> Tuple[bool, str]:
    """
    Determine if two paragraphs across a page boundary should be merged.

    Paragraphs should merge if:
    1. Same column and reading block context (similar column structure)
    2. Compatible font (same size, same style - bold/italic)
    3. Last paragraph doesn't end with sentence terminator (., !, ?, etc.)
    4. First paragraph starts with lowercase letter (indicates continuation)

    Args:
        last_para_fragments: Fragments from last paragraph on page N
        first_para_fragments: Fragments from first paragraph on page N+1
        original_texts: Font info lookup
        debug: Enable debug logging

    Returns:
        (should_merge: bool, reason: str)
    """
    if not last_para_fragments or not first_para_fragments:
        return False, "empty paragraphs"

    # Get last fragment of previous paragraph
    last_frag = last_para_fragments[-1]
    # Get first fragment of next paragraph
    first_frag = first_para_fragments[0]

    # Check 1: Must be consecutive pages
    last_page = last_frag.get("page_num", last_frag.get("page"))
    first_page = first_frag.get("page_num", first_frag.get("page"))

    if last_page is None or first_page is None:
        return False, "missing page info"

    if first_page != last_page + 1:
        return False, f"non-consecutive pages ({last_page} -> {first_page})"

    # Check 2: Same column and reading block (similar column structure)
    if (last_frag["col_id"] != first_frag["col_id"] or
        last_frag["reading_order_block"] != first_frag["reading_order_block"]):
        return False, "different column/reading block"

    # Check 2.5: Font compatibility - must have same font characteristics for continuation
    last_attrs = get_fragment_font_attrs(last_frag, original_texts)
    first_attrs = get_fragment_font_attrs(first_frag, original_texts)

    # Font size must be similar (within 2pt)
    if abs(last_attrs["size"] - first_attrs["size"]) >= 2.0:
        return False, f"font size mismatch: {last_attrs['size']:.1f}pt → {first_attrs['size']:.1f}pt"

    # Font style (bold) must match for continuation
    if last_attrs["bold"] != first_attrs["bold"]:
        last_style = "bold" if last_attrs["bold"] else "regular"
        first_style = "bold" if first_attrs["bold"] else "regular"
        return False, f"font style mismatch: {last_style} → {first_style}"

    # Check 3: Semantic continuity - does last paragraph end with sentence terminator?
    last_text = last_frag.get("text", "").strip()

    # Sentence terminators indicate paragraph should NOT continue
    # Note: We only check period (.) as the primary terminator for cross-page merging
    # Other terminators like !, ?, etc. are less common for paragraph continuations
    sentence_terminators = {'.', '!', '?', '。', '！', '？'}

    # Also check for list/heading patterns that indicate breaks
    heading_patterns = [
        r'^\d+\.',  # "1. Heading"
        r'^[A-Z][a-z]*:$',  # "Chapter:" or "Section:"
        r'^[IVX]+\.',  # "I. Roman numeral"
    ]

    if last_text:
        # Check if ends with terminator
        if last_text[-1] in sentence_terminators:
            return False, f"sentence terminator: '{last_text[-1]}'"

        # Check if last line looks like a heading/list item
        for pattern in heading_patterns:
            if re.match(pattern, last_text):
                return False, f"heading pattern: {pattern}"

    # Check 4: First paragraph text - must start with lowercase to indicate continuation
    first_text = first_frag.get("text", "").strip()

    if not first_text:
        return False, "empty first paragraph text"

    # Get first alphanumeric character to check case
    first_alpha_char = None
    for char in first_text:
        if char.isalpha():
            first_alpha_char = char
            break

    # If first paragraph starts with uppercase, it's likely a new sentence/paragraph
    # Exception: If it's part of a name or acronym that was split
    if first_alpha_char and first_alpha_char.isupper():
        # Check if last text ends with hyphen (word break) - then we should merge
        if last_text and last_text.endswith('-'):
            # This is a hyphenated word break, should merge
            pass
        else:
            return False, f"next paragraph starts with uppercase: '{first_alpha_char}'"

    # Check 5: First paragraph shouldn't start like a new section
    new_para_patterns = [
        r'^[A-Z][a-z]+\s+\d+',  # "Chapter 1", "Section 2"
        r'^\d+\.\d+',  # "1.1", "2.3" (subsection)
        r'^[•●○■□▪▫·\-\*]',  # Bullet points
        r'^\d+\)',  # "1)", "2)" numbered lists
        r'^\([a-z]\)',  # "(a)", "(b)" lettered lists
        r'^[a-z]\)',  # "a)", "b)" lettered lists
    ]

    for pattern in new_para_patterns:
        if re.match(pattern, first_text):
            return False, f"new section pattern: {pattern}"

    # All checks passed - paragraphs should merge
    merge_reason = f"continuous: no terminator, lowercase start"

    if debug:
        print(f"    Cross-page merge: page {last_page}->{first_page}, {merge_reason}")

    return True, merge_reason


def merge_cross_page_paragraphs(
    all_paragraph_data: List[Tuple[int, List[List[Dict[str, Any]]]]],
    original_texts: Dict[Tuple[int, int], ET.Element],
    debug: bool = False,
) -> List[Tuple[int, List[List[Dict[str, Any]]]]]:
    """
    Post-process paragraphs to merge those that span page boundaries.
    
    This is called after all pages have been processed and paragraphs created.
    It scans for consecutive paragraphs across page boundaries and merges them
    if they represent continuous text flow.
    
    Args:
        all_paragraph_data: List of (page_num, paragraphs) tuples
        original_texts: Font info lookup
        debug: Enable debug logging
    
    Returns:
        Updated list of (page_num, paragraphs) with merged cross-page paragraphs
    """
    if not all_paragraph_data:
        return all_paragraph_data
    
    # Sort by page number to ensure correct order
    sorted_data = sorted(all_paragraph_data, key=lambda x: x[0])
    
    merge_count = 0
    
    # Scan through consecutive pages
    for i in range(len(sorted_data) - 1):
        curr_page_num, curr_paragraphs = sorted_data[i]
        next_page_num, next_paragraphs = sorted_data[i + 1]
        
        # Only check consecutive pages
        if next_page_num != curr_page_num + 1:
            continue
        
        # Skip if either page has no paragraphs
        if not curr_paragraphs or not next_paragraphs:
            continue
        
        # Check if last paragraph of current page should merge with first of next page
        last_para = curr_paragraphs[-1]
        first_para = next_paragraphs[0]
        
        should_merge, reason = should_merge_cross_page_paragraphs(
            last_para,
            first_para,
            original_texts,
            debug=debug
        )
        
        if should_merge:
            # Merge: append first_para fragments to last_para
            curr_paragraphs[-1].extend(first_para)
            
            # Remove first_para from next page
            next_paragraphs.pop(0)
            
            merge_count += 1
            
            if debug:
                print(f"  Merged paragraph across pages {curr_page_num}->{next_page_num}: {reason}")
    
    if merge_count > 0:
        print(f"  Cross-page merge: Combined {merge_count} paragraph(s) spanning page boundaries")
    
    return sorted_data


def create_unified_xml(
    pdf_path: str,
    merged_data: Dict[int, Dict[str, Any]],
    pdftohtml_xml_path: str,
    output_xml_path: str,
) -> None:
    """
    Generate the final unified XML with hierarchical structure.

    XML structure:
    <document>
      <page>
        <texts>
          <!-- Content is INTERLEAVED by reading_order within each reading_block -->
          <!-- Paragraphs, media, and tables appear in their correct reading positions -->
          <para col_id="..." reading_block="...">
            <text reading_order="..." ...>...</text>
            <text reading_order="..." ...>...</text>
          </para>
          <media reading_order="..." reading_block="..." .../>  <!-- Interleaved at correct position -->
          <para col_id="..." reading_block="...">
            <text reading_order="..." ...>...</text>
          </para>
          <table reading_order="..." reading_block="..." ...>...</table>  <!-- Interleaved at correct position -->
        </texts>
        <media/>  <!-- Empty - media is now interleaved in texts section -->
        <tables/>  <!-- Empty - tables are now interleaved in texts section -->
      </page>
    </document>

    Note: Media elements (images, figures) and tables are interleaved with text paragraphs
    based on their reading_order values. This preserves the original document layout where
    figures and tables appear between text paragraphs, not grouped at the end of the page.
    """
    # Parse original pdftohtml XML to get font/size/color attributes
    original_tree = ET.parse(pdftohtml_xml_path)
    original_root = original_tree.getroot()

    # Build a lookup: (page, stream_index) -> original <text> element
    original_texts = {}
    for page_elem in original_root.findall(".//page"):
        page_num = int(page_elem.get("number", "0"))
        stream_idx = 1
        for text_elem in page_elem.findall("text"):
            original_texts[(page_num, stream_idx)] = text_elem
            stream_idx += 1

    # Build fontspec lookup: font_id -> {id, size, family/face, color}
    # This is needed to look up font names for emphasis role detection
    fontspec_lookup = {}
    for fontspec_elem in original_root.findall(".//fontspec"):
        font_id = fontspec_elem.get("id")
        if font_id:
            fontspec_lookup[font_id] = {
                "id": font_id,
                "size": fontspec_elem.get("size", "12"),
                "family": fontspec_elem.get("family", ""),
                "face": fontspec_elem.get("face", ""),
                "color": fontspec_elem.get("color", "#000000"),
            }

    # Create new unified XML
    root = ET.Element("document", {"source": os.path.basename(pdf_path)})

    # Copy <fontspec> elements from original pdftohtml XML to unified XML
    # This is critical for font_roles_auto.py to work correctly
    for fontspec_elem in original_root.findall(".//fontspec"):
        # Clone the fontspec element with all its attributes
        fontspec_copy = ET.SubElement(root, "fontspec", fontspec_elem.attrib)
        print(f"  Added fontspec: id={fontspec_elem.get('id')}, size={fontspec_elem.get('size')}")

    # Copy <outline> element if present (for chapter detection)
    # The outline element contains PDF bookmarks/TOC information
    outline_elem = original_root.find(".//outline")
    if outline_elem is not None:
        print("  Found <outline> element - copying to unified XML for chapter detection")

        def copy_outline_recursive(source_elem, parent_elem):
            """Recursively copy outline structure including nested outlines."""
            for child in source_elem:
                if child.tag == "item":
                    # Copy item with its attributes and text
                    item_copy = ET.SubElement(parent_elem, "item", child.attrib)
                    item_copy.text = child.text
                    item_copy.tail = child.tail
                elif child.tag == "outline":
                    # Recursively copy nested outline
                    nested_outline = ET.SubElement(parent_elem, "outline", child.attrib)
                    copy_outline_recursive(child, nested_outline)

        # Create outline element in unified XML
        outline_copy = ET.SubElement(root, "outline")
        copy_outline_recursive(outline_elem, outline_copy)
        print(f"  Copied outline with {len(list(outline_elem.iter('item')))} items")

    # PARAGRAPH CREATION AND MEDIA MERGING WORKFLOW:
    # Phase 1: Create paragraphs from filtered text fragments
    # Phase 2: Merge cross-page paragraphs
    # Phase 3: Generate XML and interleave media at correct reading positions
    
    # PHASE 1: Collect all paragraphs from all pages (before XML generation)
    # This allows us to merge cross-page paragraphs in a second pass
    print("\nPhase 1: Creating paragraphs from text fragments...")
    all_page_data = []  # List of (page_num, page_data, page_number_id, sorted_fragments, paragraphs)

    for page_num in sorted(merged_data.keys()):
        page_data = merged_data[page_num]

        # Check for full-page image pages (forms, RTL content)
        # These pages skip paragraph processing entirely
        if page_data.get("render_mode") == "fullpage":
            render_reason = page_data.get("render_reason", "")
            print(f"  Page {page_num}: Full-page image - skipping paragraph processing ({render_reason})")
            all_page_data.append((page_num, page_data, None, [], False))  # Not an index page
            continue

        # Extract page number ID from dedicated page_number_fragments (not filtered fragments)
        page_number_id = extract_page_number(
            page_data.get("page_number_fragments", []),
            page_data["page_height"]
        )

        # Sort fragments using Excel metadata: ReadingOrderBlock → ColID → NormBaseline
        # Use norm_baseline (normalized/averaged baseline) for correct ordering within columns
        sorted_fragments = sorted(
            page_data["fragments"],
            key=lambda x: (x["reading_order_block"], x["col_id"], x.get("norm_baseline", x["baseline"]))
        )

        # Calculate typical line height for paragraph break detection
        if sorted_fragments:
            line_heights = [f["height"] for f in sorted_fragments if f["height"] > 0]
            typical_line_height = sorted(line_heights)[len(line_heights) // 2] if line_heights else 12.0
        else:
            typical_line_height = 12.0

        # Check if this is a reference page (TOC/Index/Glossary) - these should NOT have paragraph grouping
        # to preserve proper reading order without merging entries
        is_index_page = is_index_or_glossary_page(sorted_fragments)

        if is_index_page:
            print(f"  Page {page_num}: 🔖 Detected REFERENCE page (TOC/Index/Glossary) - skipping paragraph grouping")
            # For reference pages, keep each fragment as its own "paragraph"
            # This preserves reading order (already sorted by reading_block, col_id, baseline)
            # Each TOC/Index entry remains on its own line
            page_paragraphs = [[frag] for frag in sorted_fragments]
            print(f"  Page {page_num}: Created {len(page_paragraphs)} individual entries (no merging)")
        else:
            # Group fragments by reading order block
            print(f"  Page {page_num}: Grouping {len(sorted_fragments)} fragments into paragraphs by reading order block")

            page_paragraphs = []  # All paragraphs for this page

            for reading_block, block_fragments_iter in groupby(sorted_fragments, key=lambda x: x["reading_order_block"]):
                block_fragments = list(block_fragments_iter)

                print(f"    Reading Block {reading_block}: Processing {len(block_fragments)} fragments")

                # Within this reading order block, group fragments into paragraphs using font-aware logic
                paragraphs = group_fragments_into_paragraphs(
                    block_fragments,
                    typical_line_height,
                    page_num=page_num,
                    debug=False,
                    page_width=page_data["page_width"],
                    original_texts=original_texts  # Pass font info for smart grouping
                )

                print(f"    Reading Block {reading_block}: Created {len(paragraphs)} paragraphs")

                # Collect paragraphs for this page
                page_paragraphs.extend(paragraphs)

        # Store all data for this page (including is_index_page flag)
        all_page_data.append((page_num, page_data, page_number_id, page_paragraphs, is_index_page))
    
    # PHASE 2: Merge cross-page paragraphs (skip for reference pages like TOC/Index/Glossary)
    print("\nPhase 2: Merging paragraphs across page boundaries...")
    # Only include non-reference pages for cross-page merging
    # Reference pages (TOC, Index, Glossary) should never have entries merged across pages
    paragraph_data_for_merge = [(page_num, paragraphs) for page_num, _, _, paragraphs, is_idx in all_page_data if not is_idx]
    merged_paragraph_data = merge_cross_page_paragraphs(
        paragraph_data_for_merge,
        original_texts,
        debug=False
    )

    # Update all_page_data with merged paragraphs (preserve index page flag)
    merged_dict = {page_num: paragraphs for page_num, paragraphs in merged_paragraph_data}
    all_page_data = [(page_num, page_data, page_number_id, merged_dict.get(page_num, paragraphs), is_idx)
                     for page_num, page_data, page_number_id, paragraphs, is_idx in all_page_data]
    
    # PHASE 3: Generate XML from merged paragraphs and interleave media
    print("\nPhase 3: Generating unified XML and interleaving media with paragraphs...")
    for page_num, page_data, page_number_id, page_paragraphs, is_index_page in all_page_data:
        # Build page attributes
        page_attrs = {
            "number": str(page_num),
            "width": str(page_data["page_width"]),
            "height": str(page_data["page_height"]),
        }

        # Add page ID if found
        if page_number_id:
            page_attrs["id"] = f"page_{page_number_id}"

        # Add render mode for full-page images
        if page_data.get("render_mode"):
            page_attrs["render_mode"] = page_data["render_mode"]
        if page_data.get("render_reason"):
            page_attrs["render_reason"] = page_data["render_reason"]

        page_elem = ET.SubElement(root, "page", page_attrs)

        # SPECIAL HANDLING: Full-page image pages
        # These pages only have a single full-page media element, no text
        if page_data.get("render_mode") == "fullpage":
            # Empty texts section
            texts_elem = ET.SubElement(page_elem, "texts")

            # Media section with full-page image
            media_section = ET.SubElement(page_elem, "media")
            for media_elem in page_data.get("media", []):
                # Clone media element with all attributes
                media_copy = ET.SubElement(
                    media_section,
                    "media",
                    media_elem.attrib,
                )
                media_copy.set("reading_order", "1")
                media_copy.set("reading_block", "1")

            # Note: No separate tables section - tables are embedded in media elements or texts
            # Removed <tables> wrapper for DTD compliance

            print(f"  Page {page_num}: Full-page image written to XML")
            continue

        # Texts section with paragraph grouping
        texts_elem = ET.SubElement(page_elem, "texts")

        # Generate XML for each paragraph on this page
        # NOTE: Paragraphs are already in correct order (sorted by reading_block, col_id, baseline)
        # Downstream code should trust document order, NOT re-sort by reading_order attribute
        for para_fragments in page_paragraphs:
            if not para_fragments:
                continue

            # Create <para> element with col_id and reading_block from first fragment
            first_fragment = para_fragments[0]
            para_attrs = {
                "col_id": str(first_fragment["col_id"]),
                "reading_block": str(first_fragment["reading_order_block"]),
            }
            para_elem = ET.SubElement(texts_elem, "para", para_attrs)

            # Collect all inline phrases for this paragraph (flattened structure)
            # This allows text to flow and reflow naturally on screen resize
            # Store fragment info with each phrase for position tracking (needed for TOC lookahead in heuristics)
            all_phrases = []

            # Track previous fragment position to detect gaps that need spaces
            prev_frag_right = None  # right edge of previous fragment (left + width)
            prev_frag_text = None   # text of previous fragment

            for f in para_fragments:
                # Check if we need to add a space between this fragment and the previous one
                # This handles cases like "Chapter 1" and "Basic MRI Physics" being separate text elements
                # with a visible gap between them that should be preserved as a space
                if prev_frag_right is not None and prev_frag_text is not None:
                    curr_left = f.get("left", 0)
                    horizontal_gap = curr_left - prev_frag_right

                    # If there's a significant gap (> 5 pixels) AND neither text ends/starts with space
                    # add a space to preserve word separation
                    GAP_THRESHOLD = 5.0  # pixels - gaps larger than this suggest intentional word separation
                    if horizontal_gap > GAP_THRESHOLD:
                        curr_text = f.get("text", "")
                        # Check if space is already present
                        if not prev_frag_text.endswith(" ") and not curr_text.startswith(" "):
                            # Add a space phrase to maintain word separation
                            # Use the same attributes as the previous phrase for consistency
                            if all_phrases:
                                last_elem_name, last_attrs, _ = all_phrases[-1]
                                all_phrases.append((last_elem_name, last_attrs.copy(), " "))
                            else:
                                # No previous phrase, add a simple space
                                all_phrases.append(("phrase", {}, " "))

                # Update tracking for next iteration
                prev_frag_right = f.get("left", 0) + f.get("width", 0)
                prev_frag_text = f.get("text", "")
                # Get original attributes from pdftohtml XML
                orig_elem = original_texts.get((page_num, f["stream_index"]))

                # CRITICAL FIX: Only reconstruct from original fragments for superscript/subscript merging
                # For regular text merging, use the merged text directly to preserve Excel merging
                # This fixes the issue where "MORIEL NESSAIVER, PH.D." was being split back into separate fragments
                if f.get("has_merged_scripts"):
                    # Fragment contains merged superscript/subscript - reconstruct with inline elements
                    for orig_frag in f["original_fragments"]:
                        # Get font info from pdftohtml XML
                        orig_stream_idx = orig_frag.get("stream_index")
                        orig_pdftohtml = original_texts.get((page_num, orig_stream_idx))

                        # Determine element type based on fragment properties
                        if orig_frag.get("is_script"):
                            if orig_frag["script_type"] == "subscript":
                                elem_name = "subscript"
                            else:
                                elem_name = "superscript"
                        else:
                            elem_name = "phrase"

                        # Build inline attributes (including position for TOC lookahead)
                        inline_attrs = {}
                        if orig_pdftohtml is not None:
                            for attr in ["font", "size", "color"]:
                                if attr in orig_pdftohtml.attrib:
                                    inline_attrs[attr] = orig_pdftohtml.get(attr)
                        
                        # Add position attributes for heuristics (TOC lookahead needs these)
                        inline_attrs["top"] = str(f["top"])
                        inline_attrs["left"] = str(f["left"])

                        text_content = orig_frag.get("text", "")
                        if text_content:
                            all_phrases.append((elem_name, inline_attrs, text_content))
                else:
                    # Single fragment - check for inner XML formatting
                    inner_xml = f.get("inner_xml", f["text"])
                    if inner_xml and inner_xml != f["text"]:
                        # Parse inner XML to extract formatted segments
                        try:
                            wrapped = f"<root>{inner_xml}</root>"
                            temp_root = ET.fromstring(wrapped)

                            # Get font attributes from original element
                            base_attrs = {}
                            if orig_elem is not None:
                                for attr in ["font", "size", "color"]:
                                    if attr in orig_elem.attrib:
                                        base_attrs[attr] = orig_elem.get(attr)
                            
                            # Add position attributes for heuristics (TOC lookahead needs these)
                            base_attrs["top"] = str(f["top"])
                            base_attrs["left"] = str(f["left"])

                            # Extract text and formatted children
                            # Handle nested tags like <i><b>Index</b></i> by recursively processing
                            def extract_formatted_text(elem, parent_attrs, parent_tags=None, script_type=None):
                                """Recursively extract text from nested formatting tags.

                                Args:
                                    elem: The XML element to process
                                    parent_attrs: Attributes inherited from parent
                                    parent_tags: Set of formatting tags from ancestors (bold, italic)
                                    script_type: 'superscript' or 'subscript' if inside such an element
                                """
                                if parent_tags is None:
                                    parent_tags = set()

                                results = []
                                current_tags = parent_tags.copy()
                                current_script = script_type

                                # Track formatting from this element's tag
                                if elem.tag in ("b", "strong"):
                                    current_tags.add("bold")
                                elif elem.tag in ("i", "em"):
                                    current_tags.add("italic")
                                elif elem.tag == "sup":
                                    current_script = "superscript"
                                elif elem.tag == "sub":
                                    current_script = "subscript"

                                # Determine role based on accumulated tags
                                if "bold" in current_tags and "italic" in current_tags:
                                    role = "bold-italic"
                                elif "bold" in current_tags:
                                    role = "bold"
                                elif "italic" in current_tags:
                                    role = "italic"
                                else:
                                    role = None

                                # Build attrs for this element
                                elem_attrs = parent_attrs.copy()
                                if role:
                                    elem_attrs["role"] = role

                                # Get direct text content of this element
                                if elem.text:
                                    # Superscript/subscript takes precedence over emphasis
                                    if current_script:
                                        results.append((current_script, elem_attrs.copy(), elem.text))
                                    elif role:
                                        results.append(("emphasis", elem_attrs.copy(), elem.text))
                                    else:
                                        results.append(("phrase", elem_attrs.copy(), elem.text))

                                # Process children recursively
                                for child in elem:
                                    child_results = extract_formatted_text(child, parent_attrs, current_tags, current_script)
                                    results.extend(child_results)

                                    # Handle tail text (text after the child element)
                                    if child.tail:
                                        # Tail text inherits parent's formatting, not child's
                                        # but stays within same script context
                                        if current_script:
                                            results.append((current_script, elem_attrs.copy(), child.tail))
                                        elif role:
                                            results.append(("emphasis", elem_attrs.copy(), child.tail))
                                        else:
                                            results.append(("phrase", parent_attrs.copy(), child.tail))

                                return results

                            if temp_root.text:
                                all_phrases.append(("phrase", base_attrs.copy(), temp_root.text))
                            for child in temp_root:
                                # Use recursive extraction to handle nested tags like <i><b>text</b></i>
                                child_results = extract_formatted_text(child, base_attrs.copy())
                                all_phrases.extend(child_results)

                                # Handle tail text (text after the child element)
                                if child.tail:
                                    all_phrases.append(("phrase", base_attrs.copy(), child.tail))
                        except ET.ParseError:
                            # Fallback to plain text
                            base_attrs = {}
                            if orig_elem is not None:
                                for attr in ["font", "size", "color"]:
                                    if attr in orig_elem.attrib:
                                        base_attrs[attr] = orig_elem.get(attr)
                            # Add position attributes for heuristics
                            base_attrs["top"] = str(f["top"])
                            base_attrs["left"] = str(f["left"])
                            if f["text"]:
                                all_phrases.append(("phrase", base_attrs, f["text"]))
                    else:
                        # Plain text without formatting
                        base_attrs = {}
                        if orig_elem is not None:
                            for attr in ["font", "size", "color"]:
                                if attr in orig_elem.attrib:
                                    base_attrs[attr] = orig_elem.get(attr)
                        # Add position attributes for heuristics
                        base_attrs["top"] = str(f["top"])
                        base_attrs["left"] = str(f["left"])
                        if f["text"]:
                            all_phrases.append(("phrase", base_attrs, f["text"]))

            # Merge adjacent phrases with same element type and attributes
            # ALSO merge standalone punctuation into previous element
            merged_phrases = []
            for phrase in all_phrases:
                elem_name, attrs, text = phrase
                if merged_phrases:
                    prev_elem_name, prev_attrs, prev_text = merged_phrases[-1]
                    
                    # SPECIAL CASE: Merge standalone punctuation into previous element
                    # Punctuation should ALWAYS attach to preceding text, regardless of element type
                    PUNCTUATION_CHARS = {'.', ',', ';', ':', '!', '?', ')', ']', '}', '"', "'", '…', '»', '›', '."', ',"', ';"'}
                    text_stripped = text.strip()
                    if text_stripped and (text_stripped in PUNCTUATION_CHARS or 
                                         (len(text_stripped) <= 3 and all(c in PUNCTUATION_CHARS or c.isspace() for c in text_stripped))):
                        # Merge punctuation into previous element (keep previous element type)
                        merged_phrases[-1] = (prev_elem_name, prev_attrs, prev_text + text)
                        continue
                    
                    # Merge if same element type and same attributes
                    if elem_name == prev_elem_name and attrs == prev_attrs:
                        # Apply dehyphenation when merging phrases
                        if prev_text.endswith("-") and text:
                            dehyph_prev, dehyph_curr, was_dehyph = remove_soft_hyphen_unified(prev_text, text)
                            if was_dehyph:
                                merged_phrases[-1] = (prev_elem_name, prev_attrs, dehyph_prev + dehyph_curr)
                            else:
                                merged_phrases[-1] = (prev_elem_name, prev_attrs, prev_text + text)
                        else:
                            merged_phrases[-1] = (prev_elem_name, prev_attrs, prev_text + text)
                        continue
                merged_phrases.append(phrase)

            # Create flowing text with semantic formatting
            # Convert font names to emphasis elements and combine adjacent plain text
            # NOTE: We preserve font ID to map back to fontspec definitions
            formatted_groups = []
            for elem_name, attrs, text in merged_phrases:
                # Determine formatting based on font name and element type
                font_id = attrs.get('font', '')

                if elem_name in ('subscript', 'superscript'):
                    # Keep scripts as-is
                    group_type = elem_name
                    group_role = None
                elif elem_name == 'emphasis':
                    # Already has emphasis, preserve it
                    group_type = 'emphasis'
                    group_role = attrs.get('role')
                else:
                    # Detect emphasis from font name (look up font family from fontspec)
                    # font_id is a numeric ID like "0", "1", etc. - need to look up actual font name
                    font_name = ""
                    if font_id and font_id in fontspec_lookup:
                        font_spec = fontspec_lookup[font_id]
                        # Try 'family' first (common), then 'face' as fallback
                        font_name = font_spec.get("family") or font_spec.get("face") or ""

                    role = get_emphasis_role(font_name)
                    if role:
                        group_type = 'emphasis'
                        group_role = role
                    else:
                        group_type = 'text'
                        group_role = None

                # Try to merge with previous group if same type/role AND same font
                if formatted_groups:
                    prev_type, prev_role, prev_text, prev_font = formatted_groups[-1]
                    if group_type == prev_type and group_role == prev_role and font_id == prev_font:
                        # Merge text into previous group (same font)
                        formatted_groups[-1] = (prev_type, prev_role, prev_text + text, prev_font)
                        continue

                formatted_groups.append((group_type, group_role, text, font_id))
            
            # Build the para content with mixed inline elements and text
            if formatted_groups:
                # Determine dominant font for the paragraph (most common or first)
                para_font_id = None
                if formatted_groups:
                    # Use first non-empty font ID as paragraph font
                    for _, _, _, fid in formatted_groups:
                        if fid:
                            para_font_id = fid
                            break

                # Set font attribute on para element if available
                if para_font_id:
                    para_elem.set('font', para_font_id)

                # First group - might be plain text or inline element
                first_type, first_role, first_text, first_font = formatted_groups[0]
                if first_type == 'text':
                    # Plain text goes directly in para.text
                    # Apply word split fixer to clean up kerning artifacts like "Com pany" → "Company"
                    para_elem.text = fix_word_splits_enhanced(first_text)
                    start_idx = 1
                else:
                    # First element is inline
                    para_elem.text = ''
                    start_idx = 0

                # Add remaining groups
                prev_elem = None
                for group_type, group_role, group_text, group_font in formatted_groups[start_idx:]:
                    if group_type == 'text':
                        # Plain text - add as tail of previous element or to para.text
                        # Apply word split fixer to clean up kerning artifacts
                        fixed_text = fix_word_splits_enhanced(group_text)
                        if prev_elem is not None:
                            prev_elem.tail = (prev_elem.tail or '') + fixed_text
                        else:
                            para_elem.text = (para_elem.text or '') + fixed_text
                    else:
                        # Create inline element
                        if group_type == 'emphasis':
                            elem = ET.SubElement(para_elem, 'emphasis')
                            if group_role:
                                elem.set('role', group_role)
                        elif group_type in ('subscript', 'superscript'):
                            elem = ET.SubElement(para_elem, group_type)

                        # Add font attribute to inline element if it differs from para font
                        if group_font and group_font != para_font_id:
                            elem.set('font', group_font)

                        # Apply word split fixer to inline element text
                        elem.text = fix_word_splits_enhanced(group_text)
                        elem.tail = ''
                        prev_elem = elem

        # Get page dimensions for coordinate transformation
        html_page_width = page_data.get("page_width", 0)
        html_page_height = page_data.get("page_height", 0)
        media_page_width = page_data.get("media_page_width", 0)
        media_page_height = page_data.get("media_page_height", 0)

        # ============================================================
        # INTERLEAVED MEDIA AND TABLES WITH PARAGRAPHS
        # ============================================================
        # Media and tables are inserted at their correct reading positions
        # within the texts section, not grouped at the end.
        # This preserves the original document layout where figures/tables
        # appear between text paragraphs.
        # ============================================================

        # Build list of media elements with their reading positions
        media_items = []
        for elem, reading_order, reading_block in page_data["media"]:
            # Deep clone the element to preserve entire structure without modifications
            new_elem = deepcopy(elem)
            new_elem.set("reading_order", str(reading_order))
            new_elem.set("reading_block", str(reading_block))

            # Transform coordinates to HTML space to match text coordinates
            if media_page_width > 0 and media_page_height > 0:
                transform_media_coords_to_html(
                    new_elem,
                    media_page_width,
                    media_page_height,
                    html_page_width,
                    html_page_height
                )

            media_items.append(("media", reading_order, reading_block, new_elem))

        # Build list of table elements with their reading positions
        table_items = []
        for elem, reading_order, reading_block in page_data["tables"]:
            # Deep clone the element to preserve entire table structure without modifications
            # CRITICAL: Use deepcopy to ensure all nested rows/columns/cells are preserved AS IS
            new_elem = deepcopy(elem)
            new_elem.set("reading_order", str(reading_order))
            new_elem.set("reading_block", str(reading_block))

            # Transform coordinates to HTML space to match text coordinates
            if media_page_width > 0 and media_page_height > 0:
                transform_media_coords_to_html(
                    new_elem,
                    media_page_width,
                    media_page_height,
                    html_page_width,
                    html_page_height
                )

            table_items.append(("table", reading_order, reading_block, new_elem))

        # Combine media and tables for interleaving
        all_media_tables = media_items + table_items
        # Sort by reading_block, then reading_order
        all_media_tables.sort(key=lambda x: (x[2], x[1]))

        # Find existing para elements in texts_elem and interleave media/tables
        para_elements = list(texts_elem)  # All <para> elements

        # Build list of (type, sort_key, reading_block, element) for paragraphs
        # CRITICAL FIX: Use paragraph's POSITION in the list (idx) as sort key, NOT reading_order attribute
        # Paragraphs are already in correct baseline order from earlier processing.
        # Using reading_order_index caused wrong ordering because it wasn't assigned in baseline order.
        para_items = []
        for idx, para_elem in enumerate(para_elements):
            para_reading_block = int(para_elem.get("reading_block", "1"))
            # Use (reading_block * 10000 + idx) to maintain order within each block
            # This preserves the original paragraph order while allowing media interleaving
            sort_key = para_reading_block * 10000 + idx
            para_items.append(("para", sort_key, para_reading_block, para_elem))

        # Combine paragraphs with media/tables
        all_items = para_items + all_media_tables
        # Sort by reading_block, then sort_key (position for paras, reading_order for media)
        all_items.sort(key=lambda x: (x[2], x[1]))

        # Associate caption paragraphs with their corresponding media elements
        # This adds figure_ref attribute to caption paragraphs for explicit linking
        associate_captions_with_media(all_items)

        # Clear texts_elem and rebuild with interleaved content
        texts_elem.clear()

        for item_type, reading_order, reading_block, elem in all_items:
            texts_elem.append(elem)

        # Note: Media and tables are now interleaved in texts section
        # Removed empty <media> and <tables> wrapper elements for DTD compliance
        # All content is in the <texts> section with proper reading order

    # Write XML
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(output_xml_path, encoding="utf-8", xml_declaration=True)

    # Count tables and media written
    # Note: Media and tables are now interleaved in the texts section, not in separate sections
    total_tables_written = len(root.findall('.//texts/table'))
    total_media_written = len(root.findall('.//texts/media'))
    total_pages = len(root.findall('.//page'))
    pages_with_tables = len([p for p in root.findall('.//page') if len(p.findall('.//texts/table')) > 0])
    pages_with_media = len([p for p in root.findall('.//page') if len(p.findall('.//texts/media')) > 0])
    
    print(f"Unified XML saved to: {output_xml_path}")
    print(f"  Pages: {total_pages}")
    print(f"  Tables: {total_tables_written} (across {pages_with_tables} pages)")
    print(f"  Media: {total_media_written} (across {pages_with_media} pages)")
    print(f"  ✓ All coordinates normalized to HTML space (matching text elements)")


def run_font_roles_auto(unified_xml_path: str) -> str:
    """
    Run font_roles_auto.py to derive font roles from unified XML.

    Args:
        unified_xml_path: Path to unified XML

    Returns:
        Path to font roles JSON file
    """
    base_name = Path(unified_xml_path).stem.replace("_unified", "")
    base_dir = Path(unified_xml_path).parent
    font_roles_path = base_dir / f"{base_name}_font_roles.json"

    print("  Running font_roles_auto.py...")
    cmd = [
        sys.executable,
        "font_roles_auto.py",
        str(unified_xml_path),
        "--out", str(font_roles_path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"  ⚠ Warning: font_roles_auto.py failed:")
        print(result.stderr)
        return ""

    print(f"  ✓ Font roles: {font_roles_path}")
    return str(font_roles_path)


def run_heuristics(unified_xml_path: str, font_roles_path: str) -> str:
    """
    Run heuristics_Nov3.py to create structured DocBook XML.

    Args:
        unified_xml_path: Path to unified XML
        font_roles_path: Path to font roles JSON

    Returns:
        Path to structured DocBook XML file
    """
    base_name = Path(unified_xml_path).stem.replace("_unified", "")
    base_dir = Path(unified_xml_path).parent
    structured_xml_path = base_dir / f"{base_name}_structured.xml"

    print("  Running heuristics_Nov3.py...")
    cmd = [
        sys.executable,
        "heuristics_Nov3.py",
        str(unified_xml_path),
        "--font-roles", str(font_roles_path),
        "--out", str(structured_xml_path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"  ⚠ Warning: heuristics_Nov3.py failed:")
        print(result.stderr)
        return ""

    print(f"  ✓ Structured XML: {structured_xml_path}")
    return str(structured_xml_path)


def run_docbook_packaging(structured_xml_path: str, metadata_dir: Optional[str] = None) -> str:
    """
    Run create_book_package.py to package DocBook XML into ZIP.

    Args:
        structured_xml_path: Path to structured DocBook XML
        metadata_dir: Optional directory containing metadata.csv or metadata.xls/xlsx

    Returns:
        Path to output ZIP file
    """
    base_name = Path(structured_xml_path).stem.replace("_structured", "")
    base_dir = Path(structured_xml_path).parent
    output_dir = base_dir / f"{base_name}_package"

    print("  Running create_book_package.py...")
    cmd = [
        sys.executable,
        "create_book_package.py",
        "--input", str(structured_xml_path),
        "--out", str(output_dir)
    ]
    
    # Add metadata directory if provided
    if metadata_dir:
        cmd.extend(["--metadata-dir", str(metadata_dir)])

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"  ⚠ Warning: create_book_package.py failed:")
        print(result.stderr)
        return ""

    # Find the generated ZIP file
    zip_files = list(output_dir.glob("*.zip"))
    if zip_files:
        print(f"  ✓ Package: {zip_files[0]}")
        return str(zip_files[0])

    return ""


def process_pdf_to_unified_xml(
    pdf_path: str,
    output_dir: str = None,
    dpi: int = 200,
    require_table_caption: bool = True,
    max_caption_distance: float = 100.0,
) -> str:
    """
    Main orchestration function.

    Args:
        pdf_path: Path to input PDF
        output_dir: Optional output directory (default: same as PDF)
        dpi: DPI for image rendering
        require_table_caption: If True, filter out tables without "Table X" captions
        max_caption_distance: Maximum distance between table and caption in points

    Returns:
        Path to unified XML file
    """
    pdf_path = os.path.abspath(pdf_path)
    base_dir = os.path.dirname(pdf_path)
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]

    print(f"\n{'='*60}")
    print(f"Processing: {os.path.basename(pdf_path)}")
    print(f"{'='*60}\n")
    
    # CRITICAL: Reset reference mapper for this conversion
    # This ensures clean state and enables image tracking throughout the pipeline
    if HAS_REFERENCE_MAPPER:
        reset_mapper()
        print("✓ Reference mapper initialized for image tracking\n")

    # ============================================================
    # OPTIMIZED PIPELINE ORDER:
    # 1. Extract table bboxes FIRST (before text processing)
    # 2. Process text with table exclusions (prevents column distortion)
    # 3. Extract full media (images, tables with snapshots, vectors)
    # 4. Filter and merge
    # ============================================================

    # Step 1: Extract table bboxes (fast pass for exclusion regions)
    print("Step 1: Detecting table regions...")
    table_bboxes, pymupdf_page_dims = extract_table_bboxes_fast(pdf_path)
    tables_found = sum(len(bboxes) for bboxes in table_bboxes.values())
    pages_with_tables_step1 = len(table_bboxes)
    print(f"  ✓ Found {tables_found} table regions across {pages_with_tables_step1} pages")
    print(f"  (Table text will be excluded from column detection)\n")
    gc.collect()

    # Step 2: Process text with reading order (excluding table regions)
    # Pass PyMuPDF page dimensions for coordinate scale conversion
    print("Step 2: Processing text and reading order...")
    text_data = pdf_to_excel_with_columns(
        pdf_path,
        exclusion_rects=table_bboxes,
        pymupdf_page_dims=pymupdf_page_dims,
    )
    print(f"  ✓ Excel output: {text_data['excel_path']}")
    print(f"  ✓ Processed {len(text_data['pages'])} pages\n")
    gc.collect()  # Free memory after text processing

    # Step 3: Extract full media (images, tables with snapshots, vectors)
    print("Step 3: Extracting media (images, tables with snapshots, vectors)...")
    media_xml_path = extract_media_and_tables(
        pdf_path,
        dpi=dpi,
        require_table_caption=require_table_caption,
        max_caption_distance=max_caption_distance,
    )
    print(f"  ✓ Media XML: {media_xml_path}\n")
    gc.collect()  # Free memory after media extraction

    # Step 4: Parse media XML
    print("Step 4: Parsing media data...")
    media_data = parse_media_xml(media_xml_path)
    
    # Count total tables found
    total_tables_in_media = sum(len(page_data.get("tables", [])) for page_data in media_data.values())
    pages_with_tables = sum(1 for page_data in media_data.values() if len(page_data.get("tables", [])) > 0)
    
    print(f"  ✓ Found media on {len(media_data)} pages")
    print(f"  ✓ Found {total_tables_in_media} tables across {pages_with_tables} pages\n")
    gc.collect()  # Free memory after parsing

    # Step 5: Filter text fragments (remove text inside tables/media to avoid duplicates)
    # This prepares the text for paragraph creation
    print("Step 5: Filtering text fragments (removing overlaps with tables/media)...")
    merged_data = merge_text_and_media_simple(text_data, media_data)
    print(f"  ✓ Filtered text for {len(merged_data)} pages\n")
    gc.collect()  # Free memory after filtering

    # Step 6: Create paragraphs from text, then merge media into document structure
    print("Step 6: Creating paragraphs and merging media into unified XML...")
    output_xml_path = os.path.join(base_dir, f"{base_name}_unified.xml")
    create_unified_xml(
        pdf_path,
        merged_data,
        text_data["pdftohtml_xml_path"],
        output_xml_path,
    )
    print(f"  ✓ Done! Paragraphs created and media merged.\n")

    # Export reference mapping for debugging
    if HAS_REFERENCE_MAPPER:
        try:
            mapper = get_mapper()
            mapping_path = os.path.join(base_dir, f"{base_name}_reference_mapping_phase1.json")
            mapper.export_to_json(Path(mapping_path))
            print(f"\n  ✓ Reference mapping exported: {mapping_path}")
            
            # Print summary
            report = mapper.generate_report()
            print(f"\n{report}")
        except Exception as e:
            print(f"\n  ⚠ Warning: Could not export reference mapping: {e}")

    print(f"{'='*60}")
    print("PHASE 1 COMPLETE: Unified XML with Page Numbers")
    print(f"{'='*60}")
    print(f"  - Excel (debug): {text_data['excel_path']}")
    print(f"  - Media XML: {media_xml_path}")
    print(f"  - Unified XML: {output_xml_path}")
    print(f"  - Media folder: {base_name}_MultiMedia/")
    print(f"{'='*60}\n")

    return output_xml_path


def process_pdf_to_docbook_package(
    pdf_path: str,
    output_dir: str = None,
    dpi: int = 200,
    skip_packaging: bool = False,
    metadata_dir: str = None,
    dtd_path: str = "RITTDOCdtd/v1.1/RittDocBook.dtd",
    skip_validation: bool = False,
    require_table_caption: bool = True,
    max_caption_distance: float = 100.0,
) -> Dict[str, str]:
    """
    Complete PDF to DocBook pipeline - master integration function.

    This function orchestrates the entire pipeline:
    1. Create unified XML with page numbers
    2. Auto-derive font roles
    3. Apply heuristics for DocBook structure
    4. Package into deliverable ZIP
    5. Run RittDoc validation and create compliant package

    Args:
        pdf_path: Path to input PDF
        output_dir: Optional output directory (default: same as PDF)
        dpi: DPI for image rendering
        skip_packaging: If True, skip the final packaging step
        metadata_dir: Optional directory containing metadata.csv or metadata.xls/xlsx
                     (defaults to PDF directory)
        dtd_path: Path to DTD file for validation
        skip_validation: If True, skip the validation step
        require_table_caption: If True, filter out tables without "Table X" captions
        max_caption_distance: Maximum distance between table and caption in points

    Returns:
        Dictionary with paths to all outputs
    """
    # Phase 1: Create unified XML
    unified_xml_path = process_pdf_to_unified_xml(
        pdf_path,
        output_dir,
        dpi,
        require_table_caption=require_table_caption,
        max_caption_distance=max_caption_distance,
    )
    
    # Default metadata directory to PDF's directory
    if metadata_dir is None:
        metadata_dir = str(Path(pdf_path).parent)

    outputs = {
        "unified_xml": unified_xml_path,
        "font_roles": "",
        "structured_xml": "",
        "package_zip": "",
        "validated_zip": "",
        "validation_report": "",
    }

    # Phase 2: DocBook processing (optional - only if heuristics files exist)
    if not os.path.exists("font_roles_auto.py"):
        print("\n⚠ Skipping DocBook processing: font_roles_auto.py not found")
        print("Phase 1 complete - unified XML with page numbers created.\n")
        return outputs

    print(f"\n{'='*60}")
    print("PHASE 2: DocBook Processing")
    print(f"{'='*60}\n")

    # Step 6: Auto-derive font roles
    print("Step 6: Auto-deriving font roles...")
    font_roles_path = run_font_roles_auto(unified_xml_path)
    outputs["font_roles"] = font_roles_path
    gc.collect()  # Free memory after font roles

    if not font_roles_path:
        print("\n⚠ Stopping: Font roles derivation failed")
        return outputs

    # Step 7: Apply heuristics
    print("\nStep 7: Applying heuristics to create structured DocBook XML...")
    structured_xml_path = run_heuristics(unified_xml_path, font_roles_path)
    outputs["structured_xml"] = structured_xml_path
    gc.collect()  # Free memory after heuristics

    if not structured_xml_path:
        print("\n⚠ Stopping: Heuristics processing failed")
        return outputs

    # Step 8: Package DocBook (optional)
    if not skip_packaging and os.path.exists("create_book_package.py"):
        print("\nStep 8: Packaging DocBook XML...")
        package_zip_path = run_docbook_packaging(structured_xml_path, metadata_dir)
        outputs["package_zip"] = package_zip_path

    # Step 9: RittDoc Validation (optional)
    if outputs["package_zip"] and not skip_validation and VALIDATION_AVAILABLE:
        print(f"\n{'='*60}")
        print("PHASE 3: RittDoc Validation & Compliance")
        print(f"{'='*60}\n")

        print("Step 9: Running RittDoc validation and compliance fixes...")

        # Check if DTD exists
        dtd_file = Path(dtd_path)
        if not dtd_file.exists():
            print(f"  ⚠ Warning: DTD file not found at {dtd_path}")
            print("  Skipping validation step.")
        else:
            try:
                # Determine output paths
                package_path = Path(outputs["package_zip"])
                base_name = package_path.stem.replace("pre_fixes_", "").replace("_structured", "")
                # Use just the ISBN/base name for final package (no _rittdoc suffix per user request)
                validated_zip_path = package_path.parent / f"{base_name}.zip"
                validation_report_path = package_path.parent / f"{base_name}_validation_report.xlsx"

                # Run the compliance pipeline
                pipeline = RittDocCompliancePipeline(dtd_file)
                success = pipeline.run(
                    input_zip=package_path,
                    output_zip=validated_zip_path,
                    max_iterations=3
                )

                outputs["validated_zip"] = str(validated_zip_path)

                # Check for validation report
                if validation_report_path.exists():
                    outputs["validation_report"] = str(validation_report_path)

                if success:
                    print(f"\n  ✓ Validation passed! Compliant package: {validated_zip_path}")
                else:
                    print(f"\n  ⚠ Validation completed with some errors remaining.")
                    print(f"    Review: {validation_report_path}")

            except Exception as e:
                print(f"  ✗ Validation failed: {e}")
                import traceback
                traceback.print_exc()
    elif outputs["package_zip"] and not skip_validation and not VALIDATION_AVAILABLE:
        print("\n⚠ Skipping validation: rittdoc_compliance_pipeline not available")
    elif skip_validation:
        print("\n⚠ Skipping validation: --skip-validation flag set")

    print(f"\n{'='*60}")
    print("✓ COMPLETE: Full PDF to DocBook Pipeline")
    print(f"{'='*60}")
    print("Final Outputs:")
    print(f"  - Unified XML: {outputs['unified_xml']}")
    if outputs["font_roles"]:
        print(f"  - Font Roles: {outputs['font_roles']}")
    if outputs["structured_xml"]:
        print(f"  - Structured XML: {outputs['structured_xml']}")
    if outputs["package_zip"]:
        print(f"  - Package ZIP (pre-validation): {outputs['package_zip']}")
    if outputs["validated_zip"]:
        print(f"  - Validated ZIP (RittDoc compliant): {outputs['validated_zip']}")
    if outputs["validation_report"]:
        print(f"  - Validation Report: {outputs['validation_report']}")
    print(f"{'='*60}\n")

    return outputs


def main():
    parser = argparse.ArgumentParser(
        description="""
        Master PDF to DocBook Integration Script

        This script orchestrates the complete PDF → DocBook pipeline:
        1. Extract text with reading order and column detection
        2. Extract media (images, tables, vectors)
        3. Merge and create unified XML with page number IDs
        4. Auto-derive font roles (with --full-pipeline)
        5. Apply heuristics for DocBook structure (with --full-pipeline)
        6. Package into deliverable ZIP (with --full-pipeline)
        7. Run RittDoc validation and create compliant package (with --full-pipeline)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("pdf_path", help="Path to input PDF file")
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="DPI for image rendering (default: 200)",
    )
    parser.add_argument(
        "--out",
        dest="output_dir",
        help="Optional output directory (default: same as PDF)",
    )
    parser.add_argument(
        "--full-pipeline",
        action="store_true",
        help="Run full DocBook processing pipeline (font roles, heuristics, packaging)",
    )
    parser.add_argument(
        "--skip-packaging",
        action="store_true",
        help="Skip final ZIP packaging step (only applies with --full-pipeline)",
    )
    parser.add_argument(
        "--metadata-dir",
        help="Directory containing metadata.csv or metadata.xls/xlsx (default: PDF directory)",
    )
    parser.add_argument(
        "--dtd",
        default="RITTDOCdtd/v1.1/RittDocBook.dtd",
        help="Path to DTD file for validation (default: RITTDOCdtd/v1.1/RittDocBook.dtd)",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip RittDoc validation step (only applies with --full-pipeline)",
    )
    parser.add_argument(
        "--no-caption-filter",
        action="store_true",
        help="Include all detected tables, even without 'Table X' captions. "
             "May include false positives but ensures no tables are missed.",
    )
    parser.add_argument(
        "--caption-distance",
        type=float,
        default=100.0,
        help="Maximum distance (in points) between table and caption for matching. "
             "Default: 100.0. Increase to capture tables with distant captions.",
    )
    parser.add_argument(
        "--edit-mode",
        action="store_true",
        help="Launch web-based UI editor for manual editing after creating unified XML"
    )
    parser.add_argument(
        "--editor-port",
        type=int,
        default=5555,
        help="Port for editor server (default: 5555)"
    )

    args = parser.parse_args()

    # Validate input file
    input_path = Path(args.pdf_path)
    if not input_path.exists():
        print(f"\n❌ ERROR: Input file not found: {args.pdf_path}")
        sys.exit(1)
    
    # Check if input is actually a PDF file
    if input_path.suffix.lower() not in ['.pdf']:
        print(f"\n❌ ERROR: Input file must be a PDF, but got: {input_path.suffix}")
        print(f"   File provided: {args.pdf_path}")
        print(f"\n   This script processes PDF files to create DocBook XML.")
        
        # Check if user accidentally provided an XML file
        if input_path.suffix.lower() in ['.xml']:
            print(f"\n   It looks like you provided an XML file instead of a PDF.")
            print(f"   If you already have a unified XML file, you can:")
            print(f"   1. Skip this step and proceed with the XML file directly")
            print(f"   2. Or provide the original PDF file to reprocess")
            
            # Try to find the PDF file with similar name
            pdf_path_guess = input_path.parent / input_path.name.replace('_unified.xml', '.pdf')
            if pdf_path_guess.exists():
                print(f"\n   Found possible PDF file: {pdf_path_guess}")
                print(f"   Try running: python pdf_to_unified_xml.py {pdf_path_guess} --full-pipeline --edit-mode")
            else:
                # Look for any PDF in the same directory
                pdf_files = list(input_path.parent.glob('*.pdf'))
                if pdf_files:
                    print(f"\n   PDF files found in the same directory:")
                    for pdf in pdf_files[:5]:  # Show up to 5
                        print(f"     - {pdf.name}")
        
        sys.exit(1)

    # Run appropriate pipeline based on flags
    if args.full_pipeline:
        # Run complete pipeline
        outputs = process_pdf_to_docbook_package(
            pdf_path=args.pdf_path,
            output_dir=args.output_dir,
            dpi=args.dpi,
            skip_packaging=args.skip_packaging,
            metadata_dir=args.metadata_dir,
            dtd_path=args.dtd,
            skip_validation=args.skip_validation,
            require_table_caption=not args.no_caption_filter,
            max_caption_distance=args.caption_distance,
        )
        unified_xml_path = outputs.get("unified_xml", "")
    else:
        # Just create unified XML with page numbers
        unified_xml_path = process_pdf_to_unified_xml(
            pdf_path=args.pdf_path,
            output_dir=args.output_dir,
            dpi=args.dpi,
            require_table_caption=not args.no_caption_filter,
            max_caption_distance=args.caption_distance,
        )
    
    # Launch editor if edit mode is enabled
    if args.edit_mode and unified_xml_path:
        print("\n" + "=" * 80)
        print("LAUNCHING WEB-BASED EDITOR")
        print("=" * 80)
        print("Opening editor for manual review and editing...")
        print("After editing, save changes and close the browser tab.")
        print("=" * 80)
        
        try:
            from editor_server import start_editor
            
            pdf_path = Path(args.pdf_path)
            unified_xml = Path(unified_xml_path)
            
            # Determine multimedia folder with fallback strategies
            base = unified_xml.stem
            if base.endswith("_unified"):
                base = base[:-8]
            multimedia_folder = unified_xml.parent / f"{base}_MultiMedia"

            if not multimedia_folder.exists():
                # Fallback 1: Look for any folder ending with _MultiMedia
                multimedia_folders = list(unified_xml.parent.glob("*_MultiMedia"))
                if multimedia_folders:
                    multimedia_folder = multimedia_folders[0]
                    print(f"Found alternative MultiMedia folder: {multimedia_folder}")
                else:
                    # Fallback 2: Look for plain "MultiMedia" folder
                    plain_multimedia = unified_xml.parent / "MultiMedia"
                    if plain_multimedia.exists():
                        multimedia_folder = plain_multimedia
                        print(f"Found plain MultiMedia folder: {multimedia_folder}")
                    else:
                        print(f"Warning: Multimedia folder not found")
                        multimedia_folder = None
            
            # Start editor (this will block until user is done)
            start_editor(
                pdf_path=pdf_path,
                xml_path=unified_xml,
                multimedia_folder=multimedia_folder,
                dtd_path=Path(args.dtd),
                port=args.editor_port
            )
            
            print("\n" + "=" * 80)
            print("EDITOR CLOSED")
            print("=" * 80)
        except ImportError as e:
            print(f"\n⚠ Error importing editor_server: {e}")
            print("   Possible causes:")
            print("   - Missing Flask: pip install flask")
            print("   - Missing Flask-CORS: pip install flask-cors")
            print("   - Missing Pillow: pip install Pillow")
            print("   - editor_server.py not in the same directory")
            import traceback
            traceback.print_exc()
        except Exception as e:
            print(f"\n⚠ Error launching editor: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
