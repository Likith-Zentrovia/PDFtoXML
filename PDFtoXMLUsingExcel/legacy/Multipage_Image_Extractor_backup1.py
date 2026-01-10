import os
import sys
import argparse
import gc
import fitz          # PyMuPDF
import camelot       # Camelot-py for table detection
import pandas as pd  # For table data cleaning
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Tuple, Optional
import re

# Import figure label extraction for semantic media IDs
try:
    from link_processor import extract_figure_label_from_caption
    HAS_FIGURE_LABEL_EXTRACTOR = True
except ImportError:
    HAS_FIGURE_LABEL_EXTRACTOR = False
    def extract_figure_label_from_caption(text):
        """Fallback if link_processor not available."""
        return None

# Import reference mapper for tracking resource transformations
try:
    from reference_mapper import get_mapper
    HAS_REFERENCE_MAPPER = True
except ImportError:
    HAS_REFERENCE_MAPPER = False
    print("Warning: reference_mapper not available, resource tracking disabled")

# Import multimedia validation for post-processing table boundaries
try:
    from update_multimedia_with_validation import update_multimedia_xml
    HAS_MULTIMEDIA_VALIDATION = True
except ImportError:
    HAS_MULTIMEDIA_VALIDATION = False
    print("Warning: update_multimedia_with_validation not available, table boundary validation disabled")


# ----------------------------
# Utility helpers
# ----------------------------

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def sanitize_xml_text(text: str) -> str:
    """
    Sanitize text for XML by removing invalid characters.

    XML 1.0 allows only:
    - Tab (0x09)
    - Newline (0x0A)
    - Carriage Return (0x0D)
    - Characters >= 0x20

    This removes other control characters that cause XML parsing errors.
    """
    if not text:
        return ""

    # Filter out invalid XML characters
    valid_chars = []
    for char in text:
        code = ord(char)
        # Allow tab, newline, carriage return, and characters >= 0x20
        if code == 0x09 or code == 0x0A or code == 0x0D or code >= 0x20:
            valid_chars.append(char)

    return ''.join(valid_chars)


def generate_semantic_media_id(
    page_no: int,
    counter: int,
    caption: str,
    media_type: str = "img",
    used_ids: Optional[set] = None,
) -> str:
    """
    Generate a semantic media ID from figure caption if available.

    Strategy:
    1. Try to extract figure label from caption (e.g., "Figure 1.2A" -> "fig1_2a")
    2. If successful and unique, use semantic ID: p{page}_fig{label}
    3. Otherwise fall back to sequential ID: p{page}_{type}{counter}

    Args:
        page_no: Page number (1-indexed)
        counter: Sequential counter for this media type on the page
        caption: Caption text (may be empty)
        media_type: Type prefix ("img" for raster, "vector" for vectors)
        used_ids: Set of already-used IDs (to ensure uniqueness)

    Returns:
        Unique media ID string
    """
    if used_ids is None:
        used_ids = set()

    # Try to extract figure label from caption
    figure_label = extract_figure_label_from_caption(caption) if caption else None

    if figure_label:
        # Convert "Figure 1.2" -> "fig1_2", "Figure 1A" -> "fig1a"
        # Remove "Figure " prefix, replace dots/spaces with underscores, lowercase
        label_part = figure_label.replace("Figure ", "").replace(".", "_").replace(" ", "").lower()
        # Clean up any double underscores and trailing underscores
        label_part = re.sub(r'_+', '_', label_part).strip('_')

        semantic_id = f"p{page_no}_fig{label_part}"

        # Check for uniqueness
        if semantic_id not in used_ids:
            used_ids.add(semantic_id)
            return semantic_id

        # If duplicate (e.g., Figure 1A and 1B both on same page), append suffix
        suffix = 2
        while f"{semantic_id}_{suffix}" in used_ids:
            suffix += 1
        unique_id = f"{semantic_id}_{suffix}"
        used_ids.add(unique_id)
        return unique_id

    # Fallback to sequential ID
    fallback_id = f"p{page_no}_{media_type}{counter}"
    used_ids.add(fallback_id)
    return fallback_id


def rect_iou(r1: fitz.Rect, r2: fitz.Rect) -> float:
    """Intersection-over-union of two PyMuPDF rects."""
    x0 = max(r1.x0, r2.x0)
    y0 = max(r1.y0, r2.y0)
    x1 = min(r1.x1, r2.x1)
    y1 = min(r1.y1, r2.y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area1 = r1.width * r1.height
    area2 = r2.width * r2.height
    denom = area1 + area2 - inter
    return inter / denom if denom > 0 else 0.0


def get_content_area_rect(
    page_rect: fitz.Rect,
    header_margin_pct: float = 0.08,
    footer_margin_pct: float = 0.08,
    left_margin_pct: float = 0.05,
    right_margin_pct: float = 0.05,
) -> fitz.Rect:
    """
    Calculate the content area of a page, excluding headers, footers, and margins.

    Args:
        page_rect: The full page rectangle
        header_margin_pct: Percentage of page height to exclude from top (default: 8%)
        footer_margin_pct: Percentage of page height to exclude from bottom (default: 8%)
        left_margin_pct: Percentage of page width to exclude from left (default: 5%)
        right_margin_pct: Percentage of page width to exclude from right (default: 5%)

    Returns:
        fitz.Rect representing the content area
    """
    page_width = page_rect.width
    page_height = page_rect.height

    content_x0 = page_rect.x0 + (page_width * left_margin_pct)
    content_y0 = page_rect.y0 + (page_height * header_margin_pct)
    content_x1 = page_rect.x1 - (page_width * right_margin_pct)
    content_y1 = page_rect.y1 - (page_height * footer_margin_pct)

    return fitz.Rect(content_x0, content_y0, content_x1, content_y1)


def is_in_content_area(
    rect: fitz.Rect,
    content_area: fitz.Rect,
    min_overlap_pct: float = 0.5,
) -> bool:
    """
    Check if a rectangle is primarily within the content area.

    An element is considered "in content area" if at least min_overlap_pct
    of its area is within the content area bounds.

    Args:
        rect: The rectangle to check (image, vector, table bounds)
        content_area: The content area rectangle
        min_overlap_pct: Minimum percentage of rect that must be in content area (default: 50%)

    Returns:
        True if the rect is primarily within the content area
    """
    if not rect.intersects(content_area):
        return False

    # Calculate intersection
    x0 = max(rect.x0, content_area.x0)
    y0 = max(rect.y0, content_area.y0)
    x1 = min(rect.x1, content_area.x1)
    y1 = min(rect.y1, content_area.y1)

    if x1 <= x0 or y1 <= y0:
        return False

    intersection_area = (x1 - x0) * (y1 - y0)
    rect_area = rect.width * rect.height

    if rect_area <= 0:
        return False

    overlap_pct = intersection_area / rect_area
    return overlap_pct >= min_overlap_pct


def get_text_blocks(page: fitz.Page) -> List[Dict[str, Any]]:
    """
    Get page text as blocks from PyMuPDF.
    Each block: {"bbox": Rect, "text": str}
    """
    blocks = []
    for b in page.get_text("blocks"):
        x0, y0, x1, y1, text, *_ = b
        rect = fitz.Rect(x0, y0, x1, y1)
        blocks.append({"bbox": rect, "text": text.strip()})
    return blocks


def get_page_spans(page: fitz.Page) -> List[Dict[str, Any]]:
    """
    Get detailed text spans for a page, including bbox, font, size, color.
    Each span: {"text": str, "bbox": (x0, y0, x1, y1), "font": str, "size": float, "color": str_hex}
    """
    spans: List[Dict[str, Any]] = []
    text_dict = page.get_text("dict")
    for b in text_dict.get("blocks", []):
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                text = s.get("text", "")
                if not text.strip():
                    continue
                x0, y0, x1, y1 = s.get("bbox", (0, 0, 0, 0))
                rect = (x0, y0, x1, y1)
                font = s.get("font", "")
                size = float(s.get("size", 0.0))
                color_int = s.get("color", 0)
                # color_int is usually 0xRRGGBB
                color_hex = f"#{color_int:06x}"
                spans.append(
                    {
                        "text": text,
                        "bbox": rect,
                        "font": font,
                        "size": size,
                        "color": color_hex,
                    }
                )
    return spans


def spans_in_rect(rect: fitz.Rect, spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return all text spans whose bbox intersects the given rect."""
    inside: List[Dict[str, Any]] = []
    for s in spans:
        x0, y0, x1, y1 = s["bbox"]
        s_rect = fitz.Rect(x0, y0, x1, y1)
        if s_rect.intersects(rect):
            inside.append(s)
    return inside


def is_text_heavy_region(
    rect: fitz.Rect,
    spans: List[Dict[str, Any]],
    char_threshold: int = 150,
    coverage_threshold: float = 0.5,
) -> bool:
    """
    Heuristic to decide if a region is mostly 'text panel' rather than diagram.
    """
    region_area = rect.width * rect.height
    if region_area <= 0:
        return False

    spans_here = spans_in_rect(rect, spans)
    if not spans_here:
        return False

    total_chars = 0
    span_area_sum = 0.0

    for s in spans_here:
        text = s["text"]
        total_chars += len(text)
        x0, y0, x1, y1 = s["bbox"]
        span_area_sum += max(0.0, (x1 - x0)) * max(0.0, (y1 - y0))

    coverage = span_area_sum / region_area

    if total_chars > char_threshold:
        return True
    if coverage > coverage_threshold:
        return True
    return False


def has_complex_drawing_shapes(
    rect: fitz.Rect,
    drawings: List[Dict[str, Any]],
    min_curves: int = 1,
    min_complex_lines: int = 3,
) -> bool:
    """
    Detect if a region contains complex drawing shapes that indicate it's a diagram
    rather than just simple text boxes or underlines.

    Complex shapes include:
    - Curves (bezier curves for circles, ovals, arcs)
    - Multiple non-rectangular lines (arrows, complex shapes)
    - Quads that form non-rectangular shapes

    Args:
        rect: The bounding box region to check
        drawings: List of drawing dictionaries from page.get_drawings()
        min_curves: Minimum number of curves to consider it complex (default: 1)
        min_complex_lines: Minimum number of lines that suggest complex shapes (default: 3)

    Returns:
        True if the region contains complex drawing shapes
    """
    if not drawings:
        return False

    # Count different types of drawing primitives in this region
    curve_count = 0
    line_count = 0
    quad_count = 0
    rect_count = 0

    for drawing in drawings:
        # Check if this drawing intersects with our region
        draw_rect = fitz.Rect(drawing.get("rect", (0, 0, 0, 0)))
        if not draw_rect.intersects(rect):
            continue

        # Analyze the drawing items
        items = drawing.get("items", [])

        for item in items:
            # Each item is a tuple: (operator, *points)
            # operator can be: 'l' (line), 'c' (curve), 're' (rectangle), 'qu' (quad)
            if not item:
                continue

            operator = item[0]

            if operator == "c":  # Bezier curve - often used for circles, ovals, arcs
                curve_count += 1
            elif operator == "l":  # Line
                line_count += 1
            elif operator == "qu":  # Quad
                quad_count += 1
            elif operator == "re":  # Rectangle
                rect_count += 1

    # Decision logic:
    # 1. If we have any curves, it's likely a diagram (circles, ovals, arcs)
    if curve_count >= min_curves:
        return True

    # 2. If we have multiple lines but few/no rectangles, it's likely a complex shape
    #    (arrows, diagrams with connectors, etc.)
    if line_count >= min_complex_lines and rect_count <= 2:
        return True

    # 3. If we have multiple quads (which aren't simple rectangles when used in drawings)
    if quad_count >= 2:
        return True

    # 4. Complex combination: some curves + some lines (labeled diagrams)
    if curve_count > 0 and line_count >= 2:
        return True

    return False


def is_table_like_drawing_region(
    rect: fitz.Rect,
    drawings: List[Dict[str, Any]],
    min_parallel_lines: int = 4,
    grid_tolerance: float = 5.0,
) -> bool:
    """
    Detect if a region contains table-like drawing patterns.

    Tables are characterized by:
    - Multiple parallel horizontal lines (row separators)
    - Multiple parallel vertical lines (column separators)
    - Lines arranged in a grid pattern
    - Few or no curves (unlike diagrams)

    Args:
        rect: The bounding box region to check
        drawings: List of drawing dictionaries from page.get_drawings()
        min_parallel_lines: Minimum number of parallel lines to consider table-like (default: 4)
        grid_tolerance: Tolerance for grouping parallel lines (default: 5.0 pixels)

    Returns:
        True if the region appears to be a table based on drawing patterns
    """
    if not drawings:
        return False

    # Collect line endpoints within the region
    horizontal_lines = []  # [(y, x0, x1), ...]
    vertical_lines = []    # [(x, y0, y1), ...]
    curve_count = 0

    for drawing in drawings:
        draw_rect = fitz.Rect(drawing.get("rect", (0, 0, 0, 0)))
        if not draw_rect.intersects(rect):
            continue

        items = drawing.get("items", [])
        for item in items:
            if not item or len(item) < 2:
                continue

            operator = item[0]

            if operator == "c":  # Curve - tables don't have curves
                curve_count += 1
            elif operator == "l":  # Line
                # Line format: ('l', Point(x0, y0), Point(x1, y1))
                if len(item) >= 3:
                    try:
                        p0 = item[1]
                        p1 = item[2]
                        x0, y0 = float(p0.x), float(p0.y)
                        x1, y1 = float(p1.x), float(p1.y)

                        # Check if line is within our region
                        if not (rect.x0 <= x0 <= rect.x1 and rect.y0 <= y0 <= rect.y1):
                            continue
                        if not (rect.x0 <= x1 <= rect.x1 and rect.y0 <= y1 <= rect.y1):
                            continue

                        # Determine if horizontal or vertical
                        dx = abs(x1 - x0)
                        dy = abs(y1 - y0)

                        if dy < grid_tolerance and dx > 10:  # Horizontal line
                            y_avg = (y0 + y1) / 2
                            horizontal_lines.append((y_avg, min(x0, x1), max(x0, x1)))
                        elif dx < grid_tolerance and dy > 10:  # Vertical line
                            x_avg = (x0 + x1) / 2
                            vertical_lines.append((x_avg, min(y0, y1), max(y0, y1)))
                    except (AttributeError, TypeError):
                        continue
            elif operator == "re":  # Rectangle - tables often have cell borders
                # Rectangle adds both horizontal and vertical segments
                if len(item) >= 2:
                    try:
                        r = item[1]
                        if hasattr(r, 'x0'):
                            # Add rectangle edges as lines
                            horizontal_lines.append((r.y0, r.x0, r.x1))
                            horizontal_lines.append((r.y1, r.x0, r.x1))
                            vertical_lines.append((r.x0, r.y0, r.y1))
                            vertical_lines.append((r.x1, r.y0, r.y1))
                    except (AttributeError, TypeError):
                        continue

    # If many curves, it's likely a diagram not a table
    if curve_count >= 3:
        return False

    # Group horizontal lines by Y position
    def group_parallel_lines(lines, pos_idx=0, tolerance=grid_tolerance):
        """Group lines that are parallel (same position within tolerance)."""
        if not lines:
            return []
        lines_sorted = sorted(lines, key=lambda l: l[pos_idx])
        groups = []
        current_group = [lines_sorted[0]]
        current_pos = lines_sorted[0][pos_idx]

        for line in lines_sorted[1:]:
            if abs(line[pos_idx] - current_pos) <= tolerance:
                current_group.append(line)
            else:
                groups.append(current_group)
                current_group = [line]
                current_pos = line[pos_idx]
        groups.append(current_group)
        return groups

    h_groups = group_parallel_lines(horizontal_lines, pos_idx=0)
    v_groups = group_parallel_lines(vertical_lines, pos_idx=0)

    # Count distinct parallel line positions (rows/columns)
    h_line_count = len(h_groups)  # Number of distinct horizontal line positions (rows)
    v_line_count = len(v_groups)  # Number of distinct vertical line positions (columns)

    # Table detection: multiple parallel horizontal AND vertical lines
    # Tables typically have 2+ rows and 2+ columns
    if h_line_count >= min_parallel_lines and v_line_count >= 2:
        return True
    if v_line_count >= min_parallel_lines and h_line_count >= 2:
        return True

    # Strong table indicator: many evenly-spaced parallel lines
    if h_line_count >= 3 and v_line_count >= 3:
        return True

    return False


def is_form_page(
    page: fitz.Page,
    drawings: List[Dict[str, Any]],
    min_small_rects: int = 10,
    min_grid_density: float = 0.3,
    checkbox_size_max: float = 25.0,
    min_text_chars_to_skip: int = 500,
) -> Tuple[bool, str]:
    """
    Detect if a page contains a complex form that should be rendered as an image.

    Form characteristics detected:
    - Many small rectangles (checkboxes, radio buttons, form fields)
    - Grid-like layouts with many cells
    - Warning/caution symbols (triangles)
    - Human body diagrams (many curves in specific regions)
    - High density of drawing elements relative to page size

    IMPORTANT: Pages with significant text content (>500 chars) are NOT considered
    forms, even if they have tables/figures. This prevents text-heavy pages with
    simple tables from being rendered as full-page images.

    Args:
        page: PyMuPDF page object
        drawings: List of drawing dictionaries from page.get_drawings()
        min_small_rects: Minimum number of small rectangles to indicate form checkboxes
        min_grid_density: Minimum ratio of drawing elements to page area
        checkbox_size_max: Maximum size (width/height) for a checkbox/radio button
        min_text_chars_to_skip: If page has >= this many text characters, skip form detection

    Returns:
        Tuple of (is_form: bool, reason: str)
    """
    if not drawings:
        return False, ""

    # EARLY EXIT: Pages with substantial text content should NOT be rendered as forms
    # This prevents text-heavy pages with simple tables/figures from being full-page images
    page_text = page.get_text("text")
    text_char_count = len(page_text.strip()) if page_text else 0
    if text_char_count >= min_text_chars_to_skip:
        # Page has significant readable text - not a form
        return False, ""

    page_rect = page.rect
    page_area = page_rect.width * page_rect.height

    # Count different element types
    small_rect_count = 0  # Checkboxes, radio buttons
    curve_count = 0       # Circles, ovals (radio buttons, body diagrams)
    line_count = 0        # Table lines, form borders
    total_drawing_area = 0

    # Track checkbox-like elements (small squares)
    checkbox_rects = []

    for drawing in drawings:
        draw_rect = fitz.Rect(drawing.get("rect", (0, 0, 0, 0)))
        items = drawing.get("items", [])

        for item in items:
            if not item:
                continue
            operator = item[0]

            if operator == "c":  # Bezier curve
                curve_count += 1
            elif operator == "l":  # Line
                line_count += 1
            elif operator == "re":  # Rectangle
                if len(item) >= 2:
                    try:
                        r = item[1]
                        if hasattr(r, 'width') and hasattr(r, 'height'):
                            rect_w = r.width
                            rect_h = r.height
                        else:
                            rect_w = draw_rect.width
                            rect_h = draw_rect.height

                        # Count small rectangles (likely checkboxes/form fields)
                        if rect_w <= checkbox_size_max and rect_h <= checkbox_size_max:
                            if rect_w > 5 and rect_h > 5:  # Not too tiny
                                small_rect_count += 1
                                checkbox_rects.append(draw_rect)

                        total_drawing_area += rect_w * rect_h
                    except (AttributeError, TypeError):
                        pass
            elif operator == "qu":  # Quad
                total_drawing_area += draw_rect.width * draw_rect.height

    # Decision logic for form detection
    reasons = []

    # 1. Many small rectangles (checkboxes/form fields)
    if small_rect_count >= min_small_rects:
        reasons.append(f"checkboxes/form_fields({small_rect_count})")

    # 2. Many curves combined with rectangles (body diagrams with checkboxes)
    if curve_count >= 20 and small_rect_count >= 5:
        reasons.append(f"diagram_with_form({curve_count}_curves,{small_rect_count}_fields)")

    # 3. Very high line count with rectangles (complex form with borders)
    if line_count >= 50 and small_rect_count >= 5:
        reasons.append(f"bordered_form({line_count}_lines,{small_rect_count}_fields)")

    # 4. Grid-like pattern (many parallel lines with checkboxes suggesting form structure)
    # Require significant number of checkbox-like rectangles to distinguish forms from simple tables
    # Simple data tables have lines but not many small checkbox rectangles
    if line_count >= 50 and small_rect_count >= 8:
        reasons.append(f"grid_form({line_count}_lines,{small_rect_count}_fields)")

    # 5. High drawing density (complex visual layout)
    drawing_density = total_drawing_area / page_area if page_area > 0 else 0
    if drawing_density >= min_grid_density and (curve_count >= 10 or small_rect_count >= 8):
        reasons.append(f"dense_layout(density={drawing_density:.2f})")

    if reasons:
        return True, "; ".join(reasons)

    return False, ""


def has_rtl_or_complex_script(
    page: fitz.Page,
    min_rtl_ratio: float = 0.15,
    min_rtl_chars: int = 50,
) -> Tuple[bool, str]:
    """
    Detect if a page contains significant RTL (Right-to-Left) text like Arabic, Urdu, Hebrew,
    or other complex scripts that may not render correctly when extracted as text.

    RTL scripts include:
    - Arabic (U+0600-U+06FF, U+0750-U+077F, U+08A0-U+08FF)
    - Urdu uses Arabic script with extensions
    - Hebrew (U+0590-U+05FF)
    - Persian/Farsi (uses Arabic script)
    - Syriac (U+0700-U+074F)

    Complex scripts that may have rendering issues:
    - Thai (U+0E00-U+0E7F)
    - Devanagari (U+0900-U+097F)
    - Bengali (U+0980-U+09FF)

    Args:
        page: PyMuPDF page object
        min_rtl_ratio: Minimum ratio of RTL characters to total characters
        min_rtl_chars: Minimum absolute count of RTL characters

    Returns:
        Tuple of (has_rtl: bool, detected_scripts: str)
    """
    text = page.get_text("text")
    if not text:
        return False, ""

    # Count characters by script type
    total_chars = 0
    rtl_chars = 0
    arabic_chars = 0
    hebrew_chars = 0
    complex_script_chars = 0

    detected_scripts = set()

    for char in text:
        code = ord(char)

        # Skip whitespace and control characters
        if char.isspace() or code < 32:
            continue

        total_chars += 1

        # Arabic script (includes Urdu, Persian, Pashto)
        # Main Arabic: U+0600-U+06FF
        # Arabic Supplement: U+0750-U+077F
        # Arabic Extended-A: U+08A0-U+08FF
        # Arabic Presentation Forms-A: U+FB50-U+FDFF
        # Arabic Presentation Forms-B: U+FE70-U+FEFF
        if (0x0600 <= code <= 0x06FF or
            0x0750 <= code <= 0x077F or
            0x08A0 <= code <= 0x08FF or
            0xFB50 <= code <= 0xFDFF or
            0xFE70 <= code <= 0xFEFF):
            rtl_chars += 1
            arabic_chars += 1
            detected_scripts.add("Arabic/Urdu")

        # Hebrew: U+0590-U+05FF
        # Hebrew Presentation Forms: U+FB00-U+FB4F (partial)
        elif 0x0590 <= code <= 0x05FF or 0xFB1D <= code <= 0xFB4F:
            rtl_chars += 1
            hebrew_chars += 1
            detected_scripts.add("Hebrew")

        # Syriac: U+0700-U+074F
        elif 0x0700 <= code <= 0x074F:
            rtl_chars += 1
            detected_scripts.add("Syriac")

        # Thaana (Maldivian): U+0780-U+07BF
        elif 0x0780 <= code <= 0x07BF:
            rtl_chars += 1
            detected_scripts.add("Thaana")

        # Complex scripts that may have rendering issues
        # Devanagari: U+0900-U+097F
        elif 0x0900 <= code <= 0x097F:
            complex_script_chars += 1
            detected_scripts.add("Devanagari")

        # Bengali: U+0980-U+09FF
        elif 0x0980 <= code <= 0x09FF:
            complex_script_chars += 1
            detected_scripts.add("Bengali")

        # Thai: U+0E00-U+0E7F
        elif 0x0E00 <= code <= 0x0E7F:
            complex_script_chars += 1
            detected_scripts.add("Thai")

        # Tamil: U+0B80-U+0BFF
        elif 0x0B80 <= code <= 0x0BFF:
            complex_script_chars += 1
            detected_scripts.add("Tamil")

    if total_chars == 0:
        return False, ""

    rtl_ratio = rtl_chars / total_chars

    # Decision: significant RTL content
    if rtl_chars >= min_rtl_chars and rtl_ratio >= min_rtl_ratio:
        scripts_str = ", ".join(sorted(detected_scripts))
        return True, f"{scripts_str} ({rtl_chars} chars, {rtl_ratio:.1%} of page)"

    # Also flag pages with high complex script content (may have rendering issues)
    complex_ratio = complex_script_chars / total_chars
    if complex_script_chars >= min_rtl_chars and complex_ratio >= min_rtl_ratio:
        scripts_str = ", ".join(sorted(detected_scripts))
        return True, f"{scripts_str} ({complex_script_chars} chars, {complex_ratio:.1%} of page)"

    return False, ""


def render_full_page_as_image(
    page: fitz.Page,
    page_no: int,
    media_dir: str,
    page_el: ET.Element,
    dpi: int = 200,
    reason: str = "",
    crop_margins: bool = True,
    header_margin_pct: float = 0.08,
    footer_margin_pct: float = 0.08,
    left_margin_pct: float = 0.05,
    right_margin_pct: float = 0.05,
) -> None:
    """
    Render an entire page as a single image.

    This is used for:
    - Complex form pages that shouldn't have text extracted
    - Pages with RTL/Arabic/Urdu text that may not render correctly
    - Any page where visual fidelity is more important than text extraction

    Args:
        page: PyMuPDF page object
        page_no: Page number (1-indexed)
        media_dir: Directory to save the image
        page_el: XML element for this page
        dpi: DPI for rendering (default: 200)
        reason: Reason for full-page rendering (for logging/metadata)
        crop_margins: If True, crop out headers, footers, margins, and crop marks (default: True)
        header_margin_pct: Percentage of page height to exclude from top (default: 8%)
        footer_margin_pct: Percentage of page height to exclude from bottom (default: 8%)
        left_margin_pct: Percentage of page width to exclude from left (default: 5%)
        right_margin_pct: Percentage of page width to exclude from right (default: 5%)
    """
    page_rect = page.rect

    # Calculate content area (excludes headers, footers, margins, crop marks)
    if crop_margins:
        content_rect = get_content_area_rect(
            page_rect=page_rect,
            header_margin_pct=header_margin_pct,
            footer_margin_pct=footer_margin_pct,
            left_margin_pct=left_margin_pct,
            right_margin_pct=right_margin_pct,
        )
    else:
        content_rect = page_rect

    # Render the page (cropped to content area if crop_margins=True)
    filename = f"page{page_no}_fullpage.png"
    out_path = os.path.join(media_dir, filename)

    # Use high-quality rendering with clip to render only the content area
    # This crops out headers, footers, margins, and crop marks
    pix = page.get_pixmap(dpi=dpi, clip=content_rect)
    pix.save(out_path)

    # Register in reference mapper (use content_rect dimensions)
    if HAS_REFERENCE_MAPPER:
        try:
            mapper = get_mapper()
            mapper.add_resource(
                original_path=filename,
                intermediate_name=filename,
                resource_type="image",
                first_seen_in=f"page_{page_no}",
                width=int(content_rect.width),
                height=int(content_rect.height),
                is_raster=True,
            )
        except Exception as e:
            print(f"Warning: Failed to register full-page image in mapper: {e}")

    # Create media element for the full-page image (use content_rect coordinates)
    media_el = ET.SubElement(
        page_el,
        "media",
        {
            "id": f"p{page_no}_fullpage",
            "type": "fullpage",
            "file": filename,
            "x1": str(content_rect.x0),
            "y1": str(content_rect.y0),
            "x2": str(content_rect.x1),
            "y2": str(content_rect.y1),
            "alt": "",
            "title": f"Full page image (page {page_no})",
            "render_reason": reason,
            "cropped": "true" if crop_margins else "false",
        },
    )

    crop_info = " (cropped)" if crop_margins else " (uncropped)"
    print(f"    Page {page_no}: Rendered as full-page image{crop_info} - {reason}")


def merge_nearby_rects(
    rects: List[fitz.Rect],
    merge_distance: float = 20.0,
    max_iterations: int = 10,
) -> List[fitz.Rect]:
    """
    Merge rectangles that are close to each other to reduce fragmentation.

    This helps combine fragmented vector drawings that should be one image.
    Rectangles within merge_distance pixels of each other will be combined.
    """
    if not rects:
        return []

    merged = [r for r in rects]  # Copy list

    for _ in range(max_iterations):
        changed = False
        new_merged = []
        used = set()

        for i, r1 in enumerate(merged):
            if i in used:
                continue

            # Try to find rectangles to merge with r1
            current = fitz.Rect(r1)

            for j, r2 in enumerate(merged):
                if j <= i or j in used:
                    continue

                # Check if rectangles are close enough to merge
                # Expand r1 by merge_distance in all directions
                expanded = fitz.Rect(
                    current.x0 - merge_distance,
                    current.y0 - merge_distance,
                    current.x1 + merge_distance,
                    current.y1 + merge_distance,
                )

                if expanded.intersects(r2):
                    # Merge r2 into current
                    current.include_rect(r2)
                    used.add(j)
                    changed = True

            new_merged.append(current)
            used.add(i)

        merged = new_merged

        if not changed:
            break

    return merged


def expand_rect_for_nearby_text(
    rect: fitz.Rect,
    spans: List[Dict[str, Any]],
    max_distance: float = 15.0,
) -> fitz.Rect:
    """
    Expand a rectangle to include nearby text fragments that are part of the drawing.

    This fixes the issue where flowchart labels and diagram text get cropped
    because they're not included in the drawing shape bounds.

    Args:
        rect: The original bounding box from drawing shapes
        spans: List of text spans on the page
        max_distance: Maximum distance (pixels) to search for associated text

    Returns:
        Expanded rectangle that includes nearby text fragments
    """
    if not spans:
        return rect

    expanded = fitz.Rect(rect)  # Copy the original rect

    # Expand search area slightly beyond the drawing bounds
    search_rect = fitz.Rect(
        rect.x0 - max_distance,
        rect.y0 - max_distance,
        rect.x1 + max_distance,
        rect.y1 + max_distance,
    )

    # Find all text spans near or within the drawing boundary
    for span in spans:
        x0, y0, x1, y1 = span["bbox"]
        span_rect = fitz.Rect(x0, y0, x1, y1)

        # Check if span is within search distance
        if not span_rect.intersects(search_rect):
            continue

        # Calculate distance from span to drawing boundary
        # Use center point of span for distance calculation
        span_center_x = (x0 + x1) / 2.0
        span_center_y = (y0 + y1) / 2.0

        # Distance to nearest edge of the drawing rect
        dx = 0.0
        dy = 0.0

        if span_center_x < rect.x0:
            dx = rect.x0 - span_center_x
        elif span_center_x > rect.x1:
            dx = span_center_x - rect.x1

        if span_center_y < rect.y0:
            dy = rect.y0 - span_center_y
        elif span_center_y > rect.y1:
            dy = span_center_y - rect.y1

        distance = (dx * dx + dy * dy) ** 0.5

        # If text is close enough, include it in the expanded bbox
        if distance <= max_distance:
            expanded.include_rect(span_rect)

    return expanded


def is_valid_figure_caption(text: str) -> bool:
    """
    Check if text is a valid figure caption.

    A valid figure caption must:
    - Start with "Figure", "Fig.", "Fig ", "IMAGE", or similar pattern
    - Be followed by a number (e.g., "Figure 1", "Figure 2.3", "Fig. 1-2")
    - Have a delimiter after the number: ".", ":", "-", or space followed by text

    Examples of valid captions:
    - "Figure 1. System Architecture"
    - "Figure 1: Overview"
    - "Figure 1 - Components"
    - "Fig. 2.3: Details"

    Args:
        text: The text to check

    Returns:
        True if text appears to be a valid figure caption
    """
    import re

    if not text or len(text.strip()) < 5:  # "Fig 1" minimum
        return False

    text_stripped = text.strip()
    text_lower = text_stripped.lower()

    # Pattern structure:
    # 1. Keyword (figure, fig, image, etc.)
    # 2. Optional space/dot
    # 3. Number (with optional decimal/letter suffix like 1.2, 1a, 1-2)
    # 4. REQUIRED delimiter: ".", ":", "-", ")", or space followed by more text
    #
    # The delimiter is key - "Figure 1" alone in running text is not a caption,
    # but "Figure 1." or "Figure 1:" is a proper caption label.

    # Number pattern: digits, optionally followed by .digit, -digit, or letter
    num_pattern = r'\d+(?:[\.\-]\d+)?[a-z]?'

    # Delimiter pattern: must have ., :, -, ), or be followed by space+text
    # This ensures "Figure 1." or "Figure 1: description" matches
    # but "see Figure 1 for details" doesn't match (no delimiter after number)
    delim_pattern = r'(?:[\.\:\-\;\)\]]|\s+\w)'

    figure_keywords = [
        r'figure',           # Figure 1.
        r'fig\.',            # Fig. 1.
        r'fig',              # Fig 1.
        r'image',            # Image 1.
        r'img\.',            # Img. 1.
        r'plate',            # Plate 1.
        r'diagram',          # Diagram 1.
        r'illustration',     # Illustration 1.
        r'photo',            # Photo 1.
        r'photograph',       # Photograph 1.
        r'exhibit',          # Exhibit 1.
        r'chart',            # Chart 1.
        r'graph',            # Graph 1.
        r'map',              # Map 1.
        r'drawing',          # Drawing 1.
        r'sketch',           # Sketch 1.
    ]

    for keyword in figure_keywords:
        # Pattern: keyword + optional space + number + delimiter
        pattern = rf'^{keyword}\s*{num_pattern}\s*{delim_pattern}'
        if re.match(pattern, text_lower):
            return True

    return False


# ----------------------------
# Table Header Cleaning
# ----------------------------

class TableHeaderCleaner:
    """
    Removes extra rows that aren't part of the actual table.
    
    Common issues addressed:
    - Table captions ("Table 1. Description...") that Camelot includes in the table
    - Paragraph text above the table
    - Page headers/footers
    - Long single-line text that's not part of the table structure
    
    This cleaner identifies the REAL header row and removes everything above it.
    """
    
    @staticmethod
    def find_real_header_row(df: pd.DataFrame, verbose: bool = False) -> int:
        """
        Find the row that's the REAL table header.
        
        Real headers are typically:
        - Short text (< 80 chars per cell)
        - At least 70% filled (not sparse)
        - Look like column names (not long sentences)
        - Multiple non-empty cells
        
        Args:
            df: Table dataframe from Camelot
            verbose: Print debugging info
            
        Returns:
            Index of the real header row (0 if no extra rows found)
        """
        rows, cols = df.shape
        
        if rows == 0 or cols == 0:
            return 0
        
        # Check first 5 rows for the real header
        for row_idx in range(min(5, rows)):
            row_cells = [str(df.iloc[row_idx, c]).strip() for c in range(cols)]
            
            # Skip empty rows
            if all(cell in ['', 'nan', 'NaN'] for cell in row_cells):
                continue
            
            # Count non-empty cells
            non_empty_cells = [cell for cell in row_cells if cell not in ['', 'nan', 'NaN']]
            non_empty_count = len(non_empty_cells)
            
            # Calculate cell lengths
            cell_lengths = [len(cell) for cell in non_empty_cells]
            
            # Real headers have:
            # 1. At least 70% of cells filled (not sparse)
            # 2. Short text (< 80 chars per cell) - not long paragraphs
            # 3. Multiple non-empty cells (not just one cell spanning)
            
            fill_ratio = non_empty_count / cols if cols > 0 else 0
            max_cell_length = max(cell_lengths) if cell_lengths else 0
            
            # Check if this looks like a header row
            is_well_filled = fill_ratio >= 0.7
            is_short_text = max_cell_length < 80
            has_multiple_cells = non_empty_count >= max(2, cols * 0.5)
            
            if is_well_filled and is_short_text and has_multiple_cells:
                # This looks like a real header!
                if verbose and row_idx > 0:
                    print(f"      Found real header at row {row_idx} (fill: {fill_ratio:.1%}, max_len: {max_cell_length})")
                return row_idx
            
            # If first row is very sparse or has very long text, it's likely not the header
            if row_idx == 0:
                # Check if first row is a caption or paragraph (single long cell)
                if non_empty_count == 1 and max_cell_length > 50:
                    # Likely a caption like "Table 1. Description of the table..."
                    if verbose:
                        print(f"      Row 0 looks like caption: '{non_empty_cells[0][:60]}...'")
                    continue
                elif fill_ratio < 0.5:
                    # Very sparse first row - probably not the header
                    if verbose:
                        print(f"      Row 0 is sparse (fill: {fill_ratio:.1%}), checking next row")
                    continue
        
        # If no clear header found, assume row 0
        return 0
    
    @staticmethod
    def clean_table_data(df: pd.DataFrame, verbose: bool = False) -> Tuple[pd.DataFrame, int]:
        """
        Remove extra rows above the real table header and clean the data.
        
        Args:
            df: Table dataframe from Camelot
            verbose: Print cleaning information
            
        Returns:
            Tuple of (cleaned_dataframe, rows_removed)
        """
        if df.empty:
            return df, 0
        
        rows_before = len(df)
        
        # Find real header
        header_row = TableHeaderCleaner.find_real_header_row(df, verbose=verbose)
        
        rows_removed = header_row
        
        if header_row > 0:
            if verbose:
                print(f"      🧹 Removing {header_row} extra row(s) above table header")
            
            # Keep only from header row onwards
            df = df.iloc[header_row:].reset_index(drop=True)
        
        rows_after = len(df)
        
        if verbose and rows_before != rows_after:
            print(f"      ✨ Table cleaned: {rows_before} rows → {rows_after} rows")
        
        return df, rows_removed


def is_valid_table_caption(text: str) -> bool:
    """
    Check if text is a valid table caption.

    A valid table caption must:
    - Start with "Table", "Tbl.", "Tbl ", or similar pattern
    - Be followed by a number (e.g., "Table 1", "Table 2.3", "Tbl. 1-2")
    - Have a delimiter after the number: ".", ":", "-", ";", ")", "]" or space followed by text

    Args:
        text: The text to check

    Returns:
        True if text appears to be a valid table caption
    """
    import re

    if not text or len(text.strip()) < 5:
        return False

    text_stripped = text.strip()
    text_lower = text_stripped.lower()

    # Number pattern: handles 1, 1.2, 1-2, 1a, etc.
    num_pattern = r'\d+(?:[\.\-]\d+)?[a-z]?'

    # Delimiter pattern: requires punctuation or space followed by word character
    # This helps distinguish real captions from false positives
    delim_pattern = r'(?:[\.\:\-\;\)\]]|\s+\w)'

    # Table keywords to look for
    table_keywords = [
        r'table',             # Table 1
        r'tbl\.',             # Tbl. 1
        r'tbl',               # Tbl 1
        r'tableau',           # Tableau 1 (French)
        r'tabelle',           # Tabelle 1 (German)
        r'schedule',          # Schedule 1 (legal docs)
        r'appendix\s+[a-z]?', # Appendix A1, Appendix 1
        r'exhibit',           # Exhibit 1
        r'list',              # List 1
        r'matrix',            # Matrix 1
    ]

    for keyword in table_keywords:
        pattern = rf'^{keyword}\s*{num_pattern}\s*{delim_pattern}'
        if re.match(pattern, text_lower):
            return True

    return False


def find_table_caption(
    region_rect: fitz.Rect,
    blocks: List[Dict[str, Any]],
    max_distance: float = 60.0,
    require_table_pattern: bool = True,
) -> str:
    """
    Find caption for a table region.

    Looks for a "Table X" pattern both ABOVE and BELOW the table,
    preferring the closest match. Tables usually have captions above,
    but some place them below.

    Args:
        region_rect: The bounding box of the table region
        blocks: Text blocks on the page
        max_distance: Maximum distance from table to look for caption
        require_table_pattern: If True, only accept text starting with "Table X" pattern

    Returns:
        Caption string (empty if none found)
    """
    best_caption = ""
    best_distance = None

    for blk in blocks:
        r = blk["bbox"]
        text = blk["text"].strip()

        # Skip empty text
        if not text:
            continue

        # Check horizontal alignment (block must overlap with table horizontally)
        if not (region_rect.x1 > r.x0 and region_rect.x0 < r.x1):
            continue

        # Check if this matches the table pattern (if required)
        if require_table_pattern and not is_valid_table_caption(text):
            continue

        # Calculate distance - check both above and below
        dist = None

        # Check if block is ABOVE the table
        if r.y1 <= region_rect.y0:
            dist = region_rect.y0 - r.y1

        # Check if block is BELOW the table
        elif r.y0 >= region_rect.y1:
            dist = r.y0 - region_rect.y1

        # Skip if not within max distance
        if dist is None or dist > max_distance:
            continue

        # Keep the closest match
        if best_distance is None or dist < best_distance:
            best_distance = dist
            best_caption = text

    return best_caption


def find_figure_caption(
    region_rect: fitz.Rect,
    blocks: List[Dict[str, Any]],
    max_distance: float = 50.0,
    require_figure_pattern: bool = True,
) -> str:
    """
    Find caption for an image/figure region.

    Looks for a "Figure X" pattern both ABOVE and BELOW the image,
    preferring the closest match. Most documents have captions below,
    but some place them above.

    Args:
        region_rect: The bounding box of the image region
        blocks: Text blocks on the page
        max_distance: Maximum distance from image to look for caption
        require_figure_pattern: If True, only accept text starting with "Figure X" pattern

    Returns:
        Caption string (empty if none found)
    """
    best_caption = ""
    best_distance = None

    for blk in blocks:
        r = blk["bbox"]
        text = blk["text"].strip()

        # Skip empty text
        if not text:
            continue

        # Check horizontal alignment (block must overlap with image horizontally)
        if not (region_rect.x1 > r.x0 and region_rect.x0 < r.x1):
            continue

        # Check if this matches the figure pattern (if required)
        if require_figure_pattern and not is_valid_figure_caption(text):
            continue

        # Calculate distance - check both above and below
        dist = None

        # Check if block is ABOVE the image
        if r.y1 <= region_rect.y0:
            dist = region_rect.y0 - r.y1

        # Check if block is BELOW the image
        elif r.y0 >= region_rect.y1:
            dist = r.y0 - region_rect.y1

        # Skip if not within max distance
        if dist is None or dist > max_distance:
            continue

        # Keep the closest match
        if best_distance is None or dist < best_distance:
            best_distance = dist
            best_caption = text

    return best_caption


def find_title_caption_for_region(
    region_rect: fitz.Rect,
    blocks: List[Dict[str, Any]],
    title_max_distance: float = 25.0,
    caption_max_distance: float = 50.0,
    require_figure_pattern: bool = True,
) -> Tuple[str, str]:
    """
    Find caption for an image/figure region.

    This is a compatibility wrapper that returns (title, caption) tuple.
    Since figures typically have only one label (not separate title and caption),
    this returns the caption in the second position and empty string for title.

    Args:
        region_rect: The bounding box of the image region
        blocks: Text blocks on the page
        title_max_distance: Ignored (kept for compatibility)
        caption_max_distance: Maximum distance from image to look for caption
        require_figure_pattern: If True, only accept captions starting with "Figure X" pattern

    Returns:
        Tuple of ("", caption) - title is always empty, caption contains the figure label
    """
    caption = find_figure_caption(
        region_rect,
        blocks,
        max_distance=caption_max_distance,
        require_figure_pattern=require_figure_pattern,
    )

    # Return empty title, caption contains the figure label
    return "", caption


def has_figure_keywords_nearby(
    region_rect: fitz.Rect,
    blocks: List[Dict[str, Any]],
    max_distance: float = 100.0,
    horizontal_tolerance: float = 50.0,
) -> bool:
    """
    Check if there's text containing "Figure", "Image", or "Table" near the region.

    Looks for keywords in text blocks both above and below the region.

    Args:
        region_rect: The bounding box of the image/vector region
        blocks: Text blocks on the page
        max_distance: Maximum distance to search for keywords (default: 100.0)
        horizontal_tolerance: Extra horizontal margin for alignment check (default: 50.0)

    Returns:
        True if any nearby text contains figure-related keywords
    """
    keywords = ["figure", "image", "table", "fig.", "fig ", "tbl.", "tbl "]

    for blk in blocks:
        r = blk["bbox"]
        text_lower = blk["text"].lower()

        # Check if any keyword is in the text
        if not any(keyword in text_lower for keyword in keywords):
            continue

        # Check if block is within the region (caption embedded in diagram)
        if region_rect.contains(r) or region_rect.intersects(r):
            return True

        # Check if block is horizontally aligned (overlaps in x-axis)
        # Use tolerance to be more lenient with alignment
        region_x_min = region_rect.x0 - horizontal_tolerance
        region_x_max = region_rect.x1 + horizontal_tolerance
        if not (region_x_max > r.x0 and region_x_min < r.x1):
            continue

        # Check if block is above the region
        if r.y1 <= region_rect.y0:
            dist = region_rect.y0 - r.y1
            if 0 <= dist <= max_distance:
                return True

        # Check if block is below the region
        if r.y0 >= region_rect.y1:
            dist = r.y0 - region_rect.y1
            if 0 <= dist <= max_distance:
                return True

    return False


def get_links_overlapping_rect(page: fitz.Page, rect: fitz.Rect) -> List[str]:
    """Collect all hyperlink URIs whose annotation rect intersects the given region."""
    links: List[str] = []
    annots = page.annots()
    if not annots:
        return links
    for a in annots:
        try:
            if a.type[0] == fitz.PDF_ANNOT_LINK and a.rect.intersects(rect):
                uri = a.uri or ""
                if uri:
                    links.append(uri)
        except Exception:
            continue
    return links


def camelot_bbox_to_fitz_rect(
    bbox: Tuple[float, float, float, float],
    page_height: float,
) -> fitz.Rect:
    """
    Convert Camelot bbox (x1, y1, x2, y2) with bottom-left origin
    to PyMuPDF Rect with top-left origin.
    """
    x1, y1, x2, y2 = bbox  # Camelot bottom-left
    # Invert Y axis
    y0_fitz = page_height - y2
    y1_fitz = page_height - y1
    return fitz.Rect(x1, y0_fitz, x2, y1_fitz)


def validate_table_structure(
    page: fitz.Page,
    table_rect: fitz.Rect,
    min_lines: int = 2,
    margin: float = 5.0
) -> Optional[fitz.Rect]:
    """
    Validate if a table has actual drawn borders/grid lines.
    If found, returns the precise bounding box of the drawn structure.
    If not found (text-only table), returns None.
    
    This helps filter out false positives where Camelot captures text
    outside the actual table boundaries (captions, notes, etc.)
    
    Args:
        page: PyMuPDF page object
        table_rect: Camelot-detected table bbox
        min_lines: Minimum horizontal + vertical lines to consider a real table structure
        margin: Tolerance for line detection around table bbox
        
    Returns:
        fitz.Rect of actual drawn structure, or None if text-only table
    """
    drawings = page.get_drawings()
    
    h_lines = []
    v_lines = []
    
    # Extract lines within table area
    for drawing in drawings:
        # Skip background fills
        if drawing.get('fill') is not None:
            continue
        
        # Check if drawing is near table
        draw_rect = drawing.get('rect')
        if not draw_rect:
            continue
        
        if not table_rect.intersects(draw_rect):
            continue
        
        # Extract line items
        for item in drawing.get('items', []):
            if item[0] == 'l' and len(item) >= 3:
                try:
                    p1, p2 = item[1], item[2]
                    x1, y1 = float(p1.x), float(p1.y)
                    x2, y2 = float(p2.x), float(p2.y)
                    
                    # Check if line is within table bbox
                    line_in_bbox = (
                        (table_rect.x0 - margin <= x1 <= table_rect.x1 + margin and 
                         table_rect.y0 - margin <= y1 <= table_rect.y1 + margin) or
                        (table_rect.x0 - margin <= x2 <= table_rect.x1 + margin and 
                         table_rect.y0 - margin <= y2 <= table_rect.y1 + margin)
                    )
                    
                    if not line_in_bbox:
                        continue
                    
                    # Classify as horizontal or vertical
                    if abs(y2 - y1) < 2:  # horizontal
                        h_lines.append((min(x1, x2), max(x1, x2), (y1 + y2) / 2))
                    elif abs(x2 - x1) < 2:  # vertical
                        v_lines.append((min(y1, y2), max(y1, y2), (x1 + x2) / 2))
                except (AttributeError, IndexError, ValueError):
                    pass
    
    # Check if we have a real table grid
    if len(h_lines) >= min_lines and len(v_lines) >= min_lines:
        # Calculate structure bbox from grid lines
        v_xs = [line[2] for line in v_lines]
        h_ys = [line[2] for line in h_lines]
        
        structure_rect = fitz.Rect(
            min(v_xs),
            min(h_ys),
            max(v_xs),
            max(h_ys)
        )
        
        return structure_rect
    
    # Text-only table or insufficient lines
    return None


def validate_text_only_table(
    page: fitz.Page,
    table_rect: fitz.Rect,
    blocks: List[Dict[str, Any]],
    min_column_alignment: float = 3.0,
    min_rows: int = 3
) -> Optional[fitz.Rect]:
    """
    FALLBACK: Validate text-only tables (no drawn borders) using text structure analysis.
    
    Analyzes:
    - Column alignment (vertical text alignment patterns)
    - Row spacing (consistent gaps between rows)
    - Font changes (headers typically bold/larger)
    - Content density (tables have higher text density than paragraphs)
    
    This helps filter out text that Camelot mistakenly includes (captions, notes).
    
    Args:
        page: PyMuPDF page object
        table_rect: Camelot-detected table bbox
        blocks: Text blocks from page (for text analysis)
        min_column_alignment: Minimum X-coordinate alignment tolerance (points)
        min_rows: Minimum rows needed to consider it a real table
        
    Returns:
        fitz.Rect of validated text structure, or None if validation fails
    """
    # Get all text blocks within the table area
    table_blocks = []
    for block in blocks:
        if block.get("type") != 0:  # Only text blocks
            continue
        
        bbox = block.get("bbox")
        if not bbox:
            continue
        
        block_rect = fitz.Rect(bbox)
        
        # Check if block is within or overlaps table area
        if table_rect.intersects(block_rect):
            table_blocks.append({
                'rect': block_rect,
                'text': block.get('text', ''),
                'lines': block.get('lines', [])
            })
    
    if len(table_blocks) < min_rows:
        # Not enough content to be a table
        return None
    
    # Analyze column structure by detecting vertical alignment
    x_positions = []  # Collect X positions of text starts
    y_positions = []  # Collect Y positions of lines
    
    for block in table_blocks:
        for line in block.get('lines', []):
            line_bbox = line.get('bbox')
            if line_bbox:
                x_positions.append(line_bbox[0])  # Left edge
                y_positions.append(line_bbox[1])  # Top edge
    
    if len(x_positions) < min_rows:
        return None
    
    # Find column boundaries by clustering X positions
    x_positions.sort()
    y_positions.sort()
    
    # Detect columns: group X positions that are close together
    columns = []
    current_column = [x_positions[0]]
    
    for i in range(1, len(x_positions)):
        if x_positions[i] - x_positions[i-1] <= min_column_alignment:
            current_column.append(x_positions[i])
        else:
            if len(current_column) >= min_rows:  # Column must have min_rows entries
                columns.append(sum(current_column) / len(current_column))
            current_column = [x_positions[i]]
    
    # Don't forget last column
    if len(current_column) >= min_rows:
        columns.append(sum(current_column) / len(current_column))
    
    # Need at least 2 columns for a table
    if len(columns) < 2:
        return None
    
    # Detect rows: group Y positions that are close together
    rows = []
    current_row = [y_positions[0]]
    row_spacing_threshold = 5.0  # Lines within 5 points are same row
    
    for i in range(1, len(y_positions)):
        if y_positions[i] - y_positions[i-1] <= row_spacing_threshold:
            current_row.append(y_positions[i])
        else:
            rows.append(sum(current_row) / len(current_row))
            current_row = [y_positions[i]]
    
    # Don't forget last row
    if current_row:
        rows.append(sum(current_row) / len(current_row))
    
    if len(rows) < min_rows:
        return None
    
    # Calculate validated bbox from detected structure
    # Use outermost column positions and row positions
    validated_x0 = min(columns) - 5  # Small margin
    validated_x1 = table_rect.x1  # Use Camelot's right edge (column ends vary)
    validated_y0 = min(rows) - 2
    validated_y1 = max(rows) + 10  # Account for text height
    
    # Make sure validated rect is within original table rect
    validated_rect = fitz.Rect(
        max(validated_x0, table_rect.x0),
        max(validated_y0, table_rect.y0),
        min(validated_x1, table_rect.x1),
        min(validated_y1, table_rect.y1)
    )
    
    # Sanity check: validated rect should be at least 50% of original
    # Use width * height to calculate area (Rect doesn't have get_area() method)
    validated_area = validated_rect.width * validated_rect.height
    original_area = table_rect.width * table_rect.height
    
    if validated_area < original_area * 0.5:
        # Too much trimming - probably not a real table structure
        return None
    
    # Check if we're actually trimming significantly
    # If trimming less than 5%, just return None (no validation needed)
    if validated_area > original_area * 0.95:
        return None
    
    return validated_rect


# ----------------------------
# Raster image extraction
# ----------------------------

def extract_raster_images_for_page(
    page: fitz.Page,
    page_no: int,
    blocks: List[Dict[str, Any]],
    media_dir: str,
    page_el: ET.Element,
    content_area: Optional[fitz.Rect] = None,
    dpi: int = 200,
    min_size: float = 5.0,
    full_page_threshold: float = 0.85,
    max_caption_chars: int = 200,
    icon_size_threshold: float = 100.0,  # Images smaller than this are treated as inline icons
) -> List[fitz.Rect]:
    """
    Extract raster images on a single page.

    Rules:
      - Ignore images in header/footer/margin areas (outside content_area).
      - Ignore near-full-page images with no overlapping text (likely decorative backgrounds).
      - Capture ALL other images including author/editor photos, diagrams, etc.
      - Save each unique image XREF only once into SharedImages/, dedupe by xref.
      - For every placement (rect) create a <media> entry with its own coordinates
        but file pointing to SharedImages/<img_xrefN.ext>.

    Args:
        content_area: Optional rect defining the content area. Images outside this area
                      (in headers, footers, margins) will be skipped.
        full_page_threshold: Percentage of page area (0.0-1.0) above which an image is 
                            considered full-page decorative (default: 0.85 = 85%)

    Returns:
      List of raster image bounding rectangles (for use in vector deduplication).
    """
    images = page.get_images(full=True)
    img_counter = 0
    extracted_rects: List[fitz.Rect] = []
    used_ids: set = set()  # Track used IDs for uniqueness in semantic IDs
    page_rect = page.rect
    page_area = page_rect.width * page_rect.height

    for img in images:
        xref = img[0]
        rects = page.get_image_rects(xref)
        for rect in rects:
            if rect.width < min_size or rect.height < min_size:
                continue

            # Skip images outside the content area (in headers, footers, margins)
            # This handles logos, page numbers, running headers/footers
            if content_area is not None and not is_in_content_area(rect, content_area, min_overlap_pct=0.5):
                continue

            # FILTER: Skip full-page decorative images (backgrounds, watermarks)
            # Check if image covers most of the page (>85% by default)
            image_area = rect.width * rect.height
            if page_area > 0:
                coverage_ratio = image_area / page_area
                if coverage_ratio > full_page_threshold:
                    # Additional check: if image has significant text overlay, it's not just decorative
                    overlapping_text = sum(1 for blk in blocks if rect.intersects(blk["bbox"]))
                    # If very little text overlaps, it's likely a decorative background
                    if overlapping_text < 3:  # Less than 3 text blocks overlay
                        continue  # Skip full-page decorative image

            # ALL OTHER IMAGES ARE CAPTURED
            # This includes:
            # - Author/editor photos (no figure caption)
            # - Diagrams and illustrations
            # - Charts and graphs
            # - Icons and symbols
            # - Any image within content area that isn't full-page decorative

            # Now save the image (only if it passed the filters above)
            img_counter += 1
            filename = f"page{page_no}_img{img_counter}.png"
            out_path = os.path.join(media_dir, filename)

            pix = page.get_pixmap(clip=rect, dpi=dpi)
            pix.save(out_path)
            
            # Register in reference mapper for page-to-chapter tracking
            if HAS_REFERENCE_MAPPER:
                try:
                    mapper = get_mapper()
                    mapper.add_resource(
                        original_path=filename,
                        intermediate_name=filename,
                        resource_type="image",
                        first_seen_in=f"page_{page_no}",
                        width=int(rect.width),
                        height=int(rect.height),
                        is_raster=True,
                    )
                except Exception as e:
                    print(f"Warning: Failed to register image in mapper: {e}")

            # Track this rectangle for vector deduplication
            extracted_rects.append(rect)

            # Title / caption (but don't let caption become an entire column)
            title, caption = find_title_caption_for_region(rect, blocks)
            links = get_links_overlapping_rect(page, rect)

            # Generate semantic ID from caption if available, otherwise use sequential ID
            media_id = generate_semantic_media_id(
                page_no=page_no,
                counter=img_counter,
                caption=caption,
                media_type="img",
                used_ids=used_ids,
            )

            media_el = ET.SubElement(
                page_el,
                "media",
                {
                    "id": media_id,
                    "type": "raster",
                    "file": filename,
                    "x1": str(rect.x0),
                    "y1": str(rect.y0),
                    "x2": str(rect.x1),
                    "y2": str(rect.y1),
                    "alt": "",  # Placeholder for true /Alt text, if you ever add it
                    "title": sanitize_xml_text(title or ""),
                },
            )

            if caption:
                cap_el = ET.SubElement(media_el, "caption")
                cap_el.text = sanitize_xml_text(caption)

            for uri in links:
                ET.SubElement(media_el, "link", {"href": uri})

    return extracted_rects


# ----------------------------
# Vector drawing extraction (using cluster_drawings)
# ----------------------------

def extract_vector_blocks_for_page(
    page: fitz.Page,
    page_no: int,
    blocks: List[Dict[str, Any]],
    spans: List[Dict[str, Any]],
    media_dir: str,
    page_el: ET.Element,
    table_rects: Optional[List[fitz.Rect]] = None,
    raster_rects: Optional[List[fitz.Rect]] = None,
    content_area: Optional[fitz.Rect] = None,
    dpi: int = 200,
    min_size: float = 30.0,
    overlap_iou_thresh: float = 0.3,
) -> None:
    """
    Extract vector drawings from the page and combine fragmented pieces.

    This function now merges nearby drawing clusters to avoid fragmentation
    of complex diagrams that were split into multiple small pieces.

    Also detects complex drawing shapes (circles, ovals, arrows, curves) and
    captures them as images even if they're text-heavy, since diagrams often
    contain embedded text labels.

    Args:
        page: The fitz.Page object to extract from
        page_no: Page number (1-indexed)
        blocks: Text blocks from the page
        spans: Text spans from the page (for text-heavy detection)
        media_dir: Directory to save extracted images
        page_el: XML element to add media elements to
        table_rects: List of table bounding rectangles to avoid duplicate captures
        raster_rects: List of raster image bounding rectangles to avoid duplicate captures
        content_area: Optional rect defining the content area. Vectors outside this area
                      (in headers, footers, margins) will be skipped.
        dpi: Resolution for rendering
        min_size: Minimum size for vector clusters
        overlap_iou_thresh: IoU threshold for overlap detection with tables/raster images
    """
    if table_rects is None:
        table_rects = []
    if raster_rects is None:
        raster_rects = []
    drawings = page.get_drawings()
    if not drawings:
        return

    # cluster_drawings returns a list of bounding rects for grouped drawings
    try:
        clusters = page.cluster_drawings(drawings=drawings)
    except TypeError:
        # Fallback for older PyMuPDF without cluster_drawings(drawings=...)
        clusters = page.cluster_drawings()

    # Filter very small clusters and clusters outside content area
    clustered_rects = []
    for r in clusters:
        if r.width < min_size or r.height < min_size:
            continue
        # Skip vectors outside the content area (in headers, footers, margins)
        if content_area is not None and not is_in_content_area(r, content_area, min_overlap_pct=0.5):
            continue
        clustered_rects.append(r)

    vec_counter = 0
    used_ids: set = set()  # Track used IDs for uniqueness in semantic IDs
    for rect in clustered_rects:
        # 1) Skip vector blocks that overlap with table areas (already captured by Camelot)
        # Use a lower IoU threshold (0.15) to be more aggressive about excluding tables
        table_iou_thresh = 0.15
        if any(rect_iou(rect, t_rect) > table_iou_thresh for t_rect in table_rects):
            continue

        # 1a) Skip vector if it intersects ANY table rect significantly
        # Even partial overlap suggests this vector is part of a table
        skip_due_to_table = False
        for t_rect in table_rects:
            if rect.intersects(t_rect):
                # Calculate intersection area
                x_overlap = max(0, min(rect.x1, t_rect.x1) - max(rect.x0, t_rect.x0))
                y_overlap = max(0, min(rect.y1, t_rect.y1) - max(rect.y0, t_rect.y0))
                intersection_area = x_overlap * y_overlap
                vector_area = rect.width * rect.height
                table_area = t_rect.width * t_rect.height

                # Skip if >10% of vector overlaps with table OR >10% of table overlaps with vector
                if vector_area > 0 and (intersection_area / vector_area) > 0.1:
                    skip_due_to_table = True
                    break
                if table_area > 0 and (intersection_area / table_area) > 0.1:
                    skip_due_to_table = True
                    break
        if skip_due_to_table:
            continue

        # 1b) Skip vector if it looks like a table (grid of horizontal/vertical lines)
        # This catches tables that Camelot missed or that have slight bbox differences
        if is_table_like_drawing_region(rect, drawings):
            # print(f"    Page {page_no}: Skipping table-like vector region at {rect}")
            continue

        # 1c) Skip vector blocks that overlap with raster images (already captured)
        # This prevents capturing border rectangles around images as separate vectors
        if any(rect_iou(rect, r_rect) > overlap_iou_thresh for r_rect in raster_rects):
            continue

        # 1d) Skip vector if ANY raster image overlaps with this vector region
        # This handles cases where Figure label + multiple raster images form a large vector block
        # If rasters already captured the content, no need to capture the vector
        skip_vector = False
        skip_reason = ""
        for r_idx, r_rect in enumerate(raster_rects, 1):
            # Check if raster is fully or partially contained in vector region
            if r_rect.intersects(rect):
                # Calculate intersection area vs raster area
                x_overlap = max(0, min(rect.x1, r_rect.x1) - max(rect.x0, r_rect.x0))
                y_overlap = max(0, min(rect.y1, r_rect.y1) - max(rect.y0, r_rect.y0))
                intersection_area = x_overlap * y_overlap
                raster_area = r_rect.width * r_rect.height
                overlap_pct = (intersection_area / raster_area) * 100 if raster_area > 0 else 0
                
                # If > 20% of the raster is within this vector region, skip the vector
                # This catches cases where vector bbox includes raster images + labels
                if raster_area > 0 and (intersection_area / raster_area) > 0.2:
                    skip_vector = True
                    skip_reason = f"contains raster image #{r_idx} ({overlap_pct:.1f}% overlap)"
                    break
        
        if skip_vector:
            # Optional: Uncomment for debugging
            # print(f"    Page {page_no}: Skipping vector region - {skip_reason}")
            continue

        # 2) Check if region contains complex drawing shapes (circles, ovals, arrows, etc.)
        #    These indicate it's a diagram/figure worth capturing
        has_complex_shapes = has_complex_drawing_shapes(rect, drawings)

        # 3) Determine if this is text-heavy (mostly text, not a diagram)
        is_text_heavy = is_text_heavy_region(rect, spans)

        # 4) RELAXED FILTER: Capture vectors that are likely figures/diagrams
        #    - Keep if has complex drawing shapes (circles, arrows, diagrams)
        #    - Keep if NOT text-heavy (likely a simple vector graphic)
        #    - Skip ONLY if text-heavy AND no complex shapes (pure text box)
        #
        # This allows capturing:
        # - Diagrams with or without "Figure X" captions
        # - Simple vector graphics (borders, decorations around images)
        # - Charts and graphs
        # - Author/editor photo borders
        #
        # This skips:
        # - Pure text boxes (no drawing elements)
        if is_text_heavy and not has_complex_shapes:
            continue

        # 7) Expand bounding box to include nearby text labels that are part of the diagram
        #    This fixes the issue where flowchart labels and diagram text get cropped
        expanded_rect = expand_rect_for_nearby_text(rect, spans, max_distance=15.0)

        vec_counter += 1
        filename = f"page{page_no}_vector{vec_counter}.png"
        out_path = os.path.join(media_dir, filename)

        # Render using expanded rect to capture associated text
        pix = page.get_pixmap(clip=expanded_rect, dpi=dpi)
        pix.save(out_path)
        
        # Register in reference mapper for page-to-chapter tracking
        if HAS_REFERENCE_MAPPER:
            try:
                mapper = get_mapper()
                mapper.add_resource(
                    original_path=filename,
                    intermediate_name=filename,
                    resource_type="image",
                    first_seen_in=f"page_{page_no}",
                    width=int(expanded_rect.width),
                    height=int(expanded_rect.height),
                    is_vector=True,
                )
            except Exception as e:
                print(f"Warning: Failed to register vector in mapper: {e}")

        # Use expanded rect for metadata and caption detection
        title, caption = find_title_caption_for_region(expanded_rect, blocks)
        links = get_links_overlapping_rect(page, expanded_rect)

        # Generate semantic ID from caption if available, otherwise use sequential ID
        media_id = generate_semantic_media_id(
            page_no=page_no,
            counter=vec_counter,
            caption=caption,
            media_type="vector",
            used_ids=used_ids,
        )

        media_el = ET.SubElement(
            page_el,
            "media",
            {
                "id": media_id,
                "type": "vector",
                "file": filename,
                "x1": str(expanded_rect.x0),
                "y1": str(expanded_rect.y0),
                "x2": str(expanded_rect.x1),
                "y2": str(expanded_rect.y1),
                "alt": "",
                "title": sanitize_xml_text(title or ""),
            },
        )

        if caption:
            cap_el = ET.SubElement(media_el, "caption")
            cap_el.text = sanitize_xml_text(caption)

        for uri in links:
            ET.SubElement(media_el, "link", {"href": uri})


# ----------------------------
# Table extraction + XML
# ----------------------------



def _page_contains_table_like_heading(page: fitz.Page) -> bool:
    """
    Heuristic: identify pages that likely contain tables even without explicit 'Table X.' captions.
    This is intentionally conservative to avoid triggering Camelot on ordinary text pages.
    """
    import re
    try:
        text = " ".join(b.get("text", "") for b in get_text_blocks(page))
    except Exception:
        try:
            text = page.get_text("text")
        except Exception:
            return False
    t = re.sub(r"\s+", " ", text).strip().lower()
    # Common signals for caption-less tables (e.g., 'Brief Timeline of ...', column header words).
    if any(k in t for k in ["timeline", "outbreaks/discoveries", "brief timeline", "virus/disease", "host animal", "disease severity", "transmission"]):
        return True
    return False


def _page_looks_like_ruled_table(page: fitz.Page) -> bool:
    """
    Heuristic: detect ruled/striped table grids via vector drawing lines.
    Useful for pages where Camelot gating based on 'Table X.' would miss real tables.
    """
    try:
        drawings = page.get_drawings()
    except Exception:
        return False

    # Count long vertical and horizontal line segments.
    v = h = 0
    pw = float(page.rect.width)
    ph = float(page.rect.height)
    min_v_len = ph * 0.25
    min_h_len = pw * 0.25

    for d in drawings:
        for it in d.get("items", []):
            if not it:
                continue
            op = it[0]
            if op != "l":
                continue
            p1 = it[1]
            p2 = it[2]
            dx = abs(p2.x - p1.x)
            dy = abs(p2.y - p1.y)
            # vertical
            if dx <= 2 and dy >= min_v_len:
                v += 1
            # horizontal
            if dy <= 2 and dx >= min_h_len:
                h += 1

    # Conservative threshold: require multiple long verticals and at least one long horizontal
    return v >= 3 and h >= 1


def _make_temp_rotated_pdf(doc: fitz.Document, page_no: int) -> Tuple[str, int]:
    """
    Create a temporary single-page PDF where the page is rotated to 0 degrees (upright),
    so Camelot/pdfminer can parse text direction more reliably.
    Returns (temp_pdf_path, applied_rotation_degrees).
    """
    page = doc[page_no - 1]
    rot = int(getattr(page, "rotation", 0) or 0) % 360
    if rot not in (90, 180, 270):
        return "", 0

    import tempfile
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()
    tmp_doc = fitz.open()
    tmp_doc.insert_pdf(doc, from_page=page_no - 1, to_page=page_no - 1)
    # Rotate back to upright
    tmp_page = tmp_doc[0]
    tmp_page.set_rotation((360 - rot) % 360)
    tmp_doc.save(tmp.name)
    tmp_doc.close()
    return tmp.name, rot

def detect_table_keywords_on_page(page: fitz.Page) -> List[Tuple[str, fitz.Rect]]:
    """
    Detect 'Table X.' patterns on a page.
    Returns list of (caption_text, rect) tuples for each detected table reference.

    Patterns matched:
    - "Table 1."
    - "Table 2:"
    - "Table A."
    - "Table I."
    etc.
    """
    import re

    results = []
    blocks = get_text_blocks(page)

    # Pattern: "Table" followed by number/letter, followed by period or colon
    # Captures the full caption line(s) after "Table X."
    pattern = re.compile(
        r'\b[Tt]able\s+([0-9]+|[A-Z]|[IVX]+)[\.:]\s*([^\n]*)',
        re.IGNORECASE
    )

    for block in blocks:
        text = block["text"]
        matches = pattern.finditer(text)

        for match in matches:
            table_num = match.group(1)
            caption_rest = match.group(2).strip()

            # Construct full caption (e.g., "Table 1. Summary of results")
            full_caption = f"Table {table_num}. {caption_rest}".strip()

            results.append((full_caption, block["bbox"]))

    return results


def is_valid_table(
    table: Any,
    min_accuracy: float = 60.0,
    min_rows: int = 2,
    min_cols: int = 2,
    min_area: float = 5000.0,
) -> bool:
    """
    Validate if a Camelot-detected table is actually a real table.

    Filters out false positives by checking:
    - Accuracy score (Camelot's confidence)
    - Minimum dimensions (rows x columns)
    - Minimum area (to avoid tiny fragments)
    - Data quality (non-empty cells)
    - Detects and rejects bullet lists disguised as tables
    """
    # Check accuracy threshold
    if hasattr(table, 'accuracy') and table.accuracy < min_accuracy:
        return False

    # Check minimum dimensions
    df = table.df
    rows, cols = df.shape
    if rows < min_rows or cols < min_cols:
        return False

    # Check minimum area (bbox size)
    x1, y1, x2, y2 = table._bbox
    area = (x2 - x1) * (y2 - y1)
    if area < min_area:
        return False

    # Check if table has meaningful content (at least some non-empty cells)
    non_empty_cells = df.astype(str).apply(lambda x: x.str.strip() != '').sum().sum()
    total_cells = rows * cols
    if total_cells > 0 and non_empty_cells / total_cells < 0.1:  # Less than 10% filled
        return False

    # CRITICAL: Detect and reject bullet lists disguised as tables
    # Bullet lists often get detected as 2-column tables with:
    # - First column: bullet character (•, -, *, etc.)
    # - Second column: list item text
    # Check if this looks like a bullet list
    if cols == 2:
        # Get first column content
        first_col = df.iloc[:, 0].astype(str).str.strip()
        
        # Count how many cells in first column are single bullet characters
        bullet_chars = {'•', '●', '○', '■', '□', '▪', '▫', '·', '-', '*', '–', '—'}
        bullet_count = sum(1 for val in first_col if val in bullet_chars or len(val) <= 1)
        
        # If > 70% of first column is bullets, this is likely a bullet list
        if bullet_count / len(first_col) > 0.7:
            return False
        
        # Also check if first column is very narrow compared to second column
        # (typical of bullet list layouts)
        first_col_width = abs(x2 - x1) * 0.15  # Assume first col is ~15% or less
        if first_col_width < 30:  # Less than 30 points wide
            # Check if many cells in first column are very short
            short_content_count = sum(1 for val in first_col if len(val) <= 2)
            if short_content_count / len(first_col) > 0.6:
                return False
    
    # CRITICAL: Detect and reject numbered lists disguised as tables
    # Numbered lists often get detected as 2-column tables with:
    # - First column: numbers (1, 2, 3, etc.) or letters (a, b, c, etc.)
    # - Second column: list item text
    if cols == 2:
        first_col = df.iloc[:, 0].astype(str).str.strip()
        
        # Check if first column contains sequential numbers
        numbers_count = 0
        sequential_count = 0
        prev_num = None
        
        for val in first_col:
            # Check for patterns like "1", "2", "3" or "1.", "2.", "3." or "(1)", "(2)", "(3)"
            val_clean = val.replace('.', '').replace('(', '').replace(')', '').strip()
            
            # Check if it's a number
            if val_clean.isdigit():
                numbers_count += 1
                num = int(val_clean)
                
                # Check if it's sequential
                if prev_num is not None and num == prev_num + 1:
                    sequential_count += 1
                prev_num = num
            
            # Check for alphabetic sequences like "a", "b", "c"
            elif len(val_clean) == 1 and val_clean.isalpha():
                numbers_count += 1
                # Count as sequential if lowercase letters
                if val_clean.islower() and prev_num is not None:
                    if ord(val_clean) == prev_num + 1:
                        sequential_count += 1
                if val_clean.islower():
                    prev_num = ord(val_clean)
        
        # If > 70% of first column is numbers/letters AND most are sequential
        # This is likely a numbered list
        if len(first_col) > 0:
            if numbers_count / len(first_col) > 0.7 and sequential_count > 0:
                return False
            
            # Also check for Roman numerals
            roman_pattern = {'i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x'}
            roman_count = sum(1 for val in first_col if val.lower() in roman_pattern)
            if roman_count / len(first_col) > 0.7:
                return False
    
    # CRITICAL: Detect and reject paragraph text disguised as tables
    # Sometimes Camelot captures regular paragraph text as a single-column table
    # Check if this looks like paragraph text rather than tabular data
    if cols == 1 or (cols == 2 and rows == 1):
        # Get all cell content
        all_text = ' '.join(df.astype(str).values.flatten())
        
        # Paragraph text characteristics:
        # 1. Contains many words (> 20 words typically)
        # 2. Average word length is normal (4-8 characters)
        # 3. Contains proper sentences (ends with periods)
        
        words = all_text.split()
        if len(words) > 20:
            # Check average word length
            avg_word_len = sum(len(w) for w in words) / len(words) if words else 0
            
            # Check for sentence patterns (periods followed by spaces)
            sentence_endings = all_text.count('. ')
            
            # If it looks like continuous prose, reject it
            if 3 <= avg_word_len <= 10 and sentence_endings >= 2:
                return False
    
    # Additional check: Reject tables where all cells are too long
    # (Real tables typically have short cell values)
    all_values = df.astype(str).values.flatten()
    very_long_cells = sum(1 for val in all_values if len(val.strip()) > 100)
    if very_long_cells / len(all_values) > 0.5:
        # More than 50% of cells have > 100 characters - likely paragraph text
        return False

    # CRITICAL: Detect and reject figure captions disguised as tables
    # Figure captions often get detected as tables when they wrap around images
    # in half-column layouts. Check if the content starts with "Figure X" pattern.
    import re

    # Get all text from the table (concatenated)
    all_text = ' '.join(df.astype(str).values.flatten()).strip()

    # Also check just the first cell content
    first_cell = df.iloc[0, 0] if rows > 0 and cols > 0 else ""
    first_cell = str(first_cell).strip()

    # Figure caption patterns to detect
    figure_patterns = [
        r'^fig(?:ure)?\.?\s*\d+',          # Figure 1, Fig. 1, Fig 1
        r'^fig(?:ure)?\.?\s*[a-z]\d*',     # Figure A, Fig. A1
        r'^fig(?:ure)?\.?\s*\d+\.\d+',     # Figure 1.1, Fig. 1.2
        r'^fig(?:ure)?\.?\s*\d+[a-z]',     # Figure 1a, Fig. 1b
        r'^fig(?:ure)?\.?\s*\d+\s*[:\.\-–—]', # Figure 1: or Figure 1. or Figure 1-
    ]

    # Check if the first cell or all text starts with a figure pattern
    for pattern in figure_patterns:
        if re.match(pattern, first_cell.lower(), re.IGNORECASE):
            return False
        if re.match(pattern, all_text.lower(), re.IGNORECASE):
            return False

    return True


def trim_table_boundary_text(
    table,
    page: fitz.Page,
    captions: List[Tuple[str, fitz.Rect]],
) -> Any:
    """
    Adjust table bounding box to exclude caption text and other surrounding text.
    
    Camelot sometimes includes:
    - Table captions (e.g., "Table 1. Caption text")
    - Surrounding paragraph text
    
    This function trims the table boundary to exclude such text.
    
    Args:
        table: Camelot table object
        page: PyMuPDF page object
        captions: List of (caption_text, rect) tuples on this page
    
    Returns:
        Modified table object with adjusted _bbox
    """
    page_height = page.rect.height
    bbox_pdf = table._bbox  # (x1, y1, x2, y2) in PDF coords (bottom-left origin)
    table_rect = camelot_bbox_to_fitz_rect(bbox_pdf, page_height)
    
    # Check if any caption is inside the table boundary
    for cap_text, cap_rect in captions:
        # If caption is inside or overlapping with table, trim table boundary
        if table_rect.intersects(cap_rect):
            # Caption typically appears above or below the table
            # If caption is near the top of table (in reading order), trim from top
            if cap_rect.y0 < table_rect.y0 + (table_rect.height * 0.3):
                # Trim from top - move table start down to below caption
                new_y0 = max(table_rect.y0, cap_rect.y1 + 5)  # 5pt margin
                table_rect.y0 = new_y0
            # If caption is near the bottom of table, trim from bottom
            elif cap_rect.y1 > table_rect.y1 - (table_rect.height * 0.3):
                # Trim from bottom - move table end up to above caption
                new_y1 = min(table_rect.y1, cap_rect.y0 - 5)  # 5pt margin
                table_rect.y1 = new_y1
    
    # Convert back to PDF coords
    # PyMuPDF: (x0, y0) is top-left, (x1, y1) is bottom-right
    # PDF: (x1, y1) is bottom-left, (x2, y2) is top-right
    x1_pdf = table_rect.x0
    y1_pdf = page_height - table_rect.y1  # Bottom in PDF coords
    x2_pdf = table_rect.x1
    y2_pdf = page_height - table_rect.y0  # Top in PDF coords
    
    # Update table bbox
    table._bbox = (x1_pdf, y1_pdf, x2_pdf, y2_pdf)
    
    return table


def extract_tables(
    pdf_path: str,
    doc: fitz.Document,
) -> Tuple[Dict[int, List[Any]], Dict[int, List[str]]]:
    """
    Use Camelot to read tables, but ONLY on pages that contain 'Table X.' keywords.
    Returns (tables_by_page, captions_by_page):
      - tables_by_page: {page_no: [table, ...]}
      - captions_by_page: {page_no: [caption1, caption2, ...]}

    Uses ONLY stream flavor for table detection:
    - stream: works for both bordered and borderless tables
    - lattice flavor is DISABLED to avoid duplicates

    Applies strict filtering to reduce false positives:
    - Bullet list detection (rejects 2-column bullet lists)
    - Numbered list detection (rejects 2-column numbered lists)
    - Minimum size and accuracy thresholds
    - Deduplication for tables detected multiple times
    - Caption trimming to exclude caption text from table boundaries
    """
    # You can tweak flavor='lattice'/'stream' depending on your PDFs
    tables = camelot.read_pdf(pdf_path, pages="all", flavor="stream", strip_text="\n")
    tables_by_page: Dict[int, List[Any]] = {}
    captions_by_page: Dict[int, List[str]] = {}

    # Step 1: Scan all pages for "Table X." keywords
    print("  Scanning pages for 'Table X.' keywords...")
    pages_with_tables = set()
    
    for page_index in range(len(doc)):
        page_no = page_index + 1
        page = doc[page_index]
        table_refs = detect_table_keywords_on_page(page)

        if table_refs:
            pages_with_tables.add(page_no)
            # Store captions WITH positions for this page (for proximity matching)
            captions_by_page[page_no] = table_refs  # List of (caption, rect) tuples
            print(f"    Page {page_no}: Found {len(table_refs)} table reference(s)")
    
    if not pages_with_tables:
        # Fallback: try to detect caption-less or rotated ruled tables (e.g., timeline tables).
        print("  No 'Table X.' keywords found. Falling back to heuristic table-page detection...")
        for page_index in range(len(doc)):
            page_no = page_index + 1
            page = doc[page_index]
            if _page_contains_table_like_heading(page) or _page_looks_like_ruled_table(page):
                pages_with_tables.add(page_no)
                captions_by_page.setdefault(page_no, [])  # keep structure consistent
                print(f"    Page {page_no}: Added by heuristic (no explicit 'Table X.' caption)")

        if not pages_with_tables:
            print("  No table-like pages found in document. Skipping Camelot detection.")
            return tables_by_page, captions_by_page

    # Convert page numbers to comma-separated string for Camelot
    pages_str = ",".join(str(p) for p in sorted(pages_with_tables))
    print(f"  Running Camelot on {len(pages_with_tables)} page(s) with table keywords: {pages_str}")

    # DISABLED: Lattice flavor (for bordered tables)
    # Using only stream flavor as per user request
    # try:
    #     lattice_tables = camelot.read_pdf(
    #         pdf_path,
    #         pages=pages_str,
    #         flavor="lattice",
    #         strip_text="\n",
    #     )
    #     print(f"  Lattice flavor detected {len(lattice_tables)} candidates")
    #
    #     # Filter valid tables
    #     valid_count = 0
    #     for t in lattice_tables:
    #         if is_valid_table(t):
    #             page_no = int(t.page)
    #             tables_by_page.setdefault(page_no, []).append(t)
    #             valid_count += 1
    #
    #     print(f"  Lattice flavor: {valid_count} valid tables after filtering")
    # except Exception as e:
    #     print(f"  Lattice flavor failed: {e}")

    # Use stream flavor ONLY (lattice disabled to avoid duplicates)
    # With bullet list detection and validation
    # Enhanced parameters for more precise table detection:
    # - edge_tol: Tolerance for detecting table edges (lower = stricter)
    # - row_tol: Tolerance for grouping text into rows (lower = stricter)
    # - column_tol: Tolerance for grouping text into columns (lower = stricter)
    try:
        stream_tables = camelot.read_pdf(
            pdf_path,
            pages=pages_str,
            flavor="stream",
            strip_text="\n",
            edge_tol=100,        # Reduced from default 500 - be stricter about table edges
            row_tol=5,           # Reduced from default 15 - be stricter about row alignment
            column_tol=5,        # Reduced from default 0 - be stricter about column alignment
        )
        print(f"  Stream flavor detected {len(stream_tables)} candidates")

        # Filter valid tables and avoid duplicates
        # (Deduplication still needed as stream can detect same table multiple times)
        valid_count = 0
        skipped_duplicates = 0
        skipped_invalid = 0
        for t in stream_tables:
            # FILTERING: Only use confidence score from Camelot
            # Keep everything above 75% confidence
            if hasattr(t, 'accuracy') and t.accuracy < 75.0:
                continue

            # CRITICAL: Validate table structure, dimensions, and content
            # This filters out bullet lists, numbered lists, figure captions,
            # paragraph text, and other false positives
            if not is_valid_table(t):
                skipped_invalid += 1
                continue

            page_no = int(t.page)
            t_rect = camelot_bbox_to_fitz_rect(t._bbox, doc[page_no - 1].rect.height)

            # Check if this table overlaps with already-accepted tables on same page
            # Camelot stream can sometimes detect the same table multiple times
            is_duplicate = False
            if page_no in tables_by_page:
                for existing_t in tables_by_page[page_no]:
                    existing_rect = camelot_bbox_to_fitz_rect(
                        existing_t._bbox,
                        doc[page_no - 1].rect.height
                    )
                    
                    # Check IoU (Intersection over Union)
                    iou = rect_iou(t_rect, existing_rect)
                    
                    # Also check if centers are very close (indicates same table)
                    t_center_x = (t_rect.x0 + t_rect.x1) / 2
                    t_center_y = (t_rect.y0 + t_rect.y1) / 2
                    existing_center_x = (existing_rect.x0 + existing_rect.x1) / 2
                    existing_center_y = (existing_rect.y0 + existing_rect.y1) / 2
                    
                    center_distance = ((t_center_x - existing_center_x) ** 2 + 
                                     (t_center_y - existing_center_y) ** 2) ** 0.5
                    
                    # Consider duplicate if:
                    # 1. IoU > 0.5 (50% overlap)
                    # 2. OR centers are within 50 pixels and there's any overlap (IoU > 0.1)
                    if iou > 0.5 or (center_distance < 50 and iou > 0.1):
                        is_duplicate = True
                        skipped_duplicates += 1
                        break

            if not is_duplicate:
                # Trim table boundary to exclude captions and surrounding text
                page = doc[page_no - 1]
                page_captions = captions_by_page.get(page_no, [])
                if page_captions:
                    t = trim_table_boundary_text(t, page, page_captions)
                
                tables_by_page.setdefault(page_no, []).append(t)
                valid_count += 1

        print(f"  Stream flavor: {valid_count} valid tables after filtering and deduplication")
        if skipped_invalid > 0:
            print(f"  Stream flavor: Skipped {skipped_invalid} invalid tables (bullet lists, figure captions, etc.)")
        if skipped_duplicates > 0:
            print(f"  Stream flavor: Skipped {skipped_duplicates} duplicate tables")
    except Exception as e:
        print(f"  Stream flavor failed: {e}")

    

    # Fallback pass: for pages we decided are table-like but stream detection returned nothing,
    # try lattice and (for rotated pages) retry on an upright temporary PDF.
    missing_pages = [p for p in sorted(pages_with_tables) if p not in tables_by_page]
    if missing_pages:
        print(f"  Stream produced no tables on {len(missing_pages)} candidate page(s). Trying fallbacks: {missing_pages}")

    for page_no in missing_pages:
        page = doc[page_no - 1]

        # 1) Try lattice on the original page (good for ruled tables)
        try:
            lattice_tables = camelot.read_pdf(
                pdf_path,
                pages=str(page_no),
                flavor="lattice",
                strip_text="\n",
                process_background=True,
                line_scale=40,
            )
            for t in lattice_tables:
                if not is_valid_table(t):
                    continue
                # Ensure bbox is present and reasonable
                if not getattr(t, "_bbox", None):
                    t._bbox = (0.0, 0.0, float(page.rect.width), float(page.rect.height))
                # Trim boundary if captions exist
                page_captions = captions_by_page.get(page_no, [])
                if page_captions:
                    t = trim_table_boundary_text(t, page, page_captions)
                tables_by_page.setdefault(page_no, []).append(t)
            if page_no in tables_by_page and tables_by_page[page_no]:
                print(f"    Page {page_no}: Lattice fallback recovered {len(tables_by_page[page_no])} table(s)")
                continue
        except Exception as e:
            print(f"    Page {page_no}: Lattice fallback failed: {e}")

        # 2) If still nothing and page is rotated, retry Camelot on an upright temporary PDF
        try:
            tmp_path, rot = _make_temp_rotated_pdf(doc, page_no)
            if tmp_path and rot:
                # Try stream on rotated-to-upright page
                try:
                    rot_stream = camelot.read_pdf(
                        tmp_path,
                        pages="1",
                        flavor="stream",
                        strip_text="\n",
                        edge_tol=150,
                        row_tol=7,
                        column_tol=7,
                    )
                    recovered = 0
                    for t in rot_stream:
                        if not is_valid_table(t):
                            continue
                        # Force table to be associated with original page
                        try:
                            t.page = page_no
                        except Exception:
                            setattr(t, "page", page_no)
                        # Use full-page bbox to ensure exclusion works even if bbox mapping is uncertain
                        t._bbox = (0.0, 0.0, float(page.rect.width), float(page.rect.height))
                        page_captions = captions_by_page.get(page_no, [])
                        if page_captions:
                            t = trim_table_boundary_text(t, page, page_captions)
                        tables_by_page.setdefault(page_no, []).append(t)
                        recovered += 1
                    if recovered > 0:
                        print(f"    Page {page_no}: Rotated-page stream fallback recovered {recovered} table(s)")
                        continue
                except Exception as e:
                    print(f"    Page {page_no}: Rotated-page stream fallback failed: {e}")

                # Try lattice on rotated-to-upright page
                try:
                    rot_lattice = camelot.read_pdf(
                        tmp_path,
                        pages="1",
                        flavor="lattice",
                        strip_text="\n",
                        process_background=True,
                        line_scale=40,
                    )
                    recovered = 0
                    for t in rot_lattice:
                        if not is_valid_table(t):
                            continue
                        try:
                            t.page = page_no
                        except Exception:
                            setattr(t, "page", page_no)
                        t._bbox = (0.0, 0.0, float(page.rect.width), float(page.rect.height))
                        page_captions = captions_by_page.get(page_no, [])
                        if page_captions:
                            t = trim_table_boundary_text(t, page, page_captions)
                        tables_by_page.setdefault(page_no, []).append(t)
                        recovered += 1
                    if recovered > 0:
                        print(f"    Page {page_no}: Rotated-page lattice fallback recovered {recovered} table(s)")
                except Exception as e:
                    print(f"    Page {page_no}: Rotated-page lattice fallback failed: {e}")
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
        except Exception as e:
            print(f"    Page {page_no}: Rotated-page fallback setup failed: {e}")
    total_tables = sum(len(tables) for tables in tables_by_page.values())
    print(f"  Total valid tables detected: {total_tables}")

    return tables_by_page, captions_by_page


def extract_table_bboxes_fast(pdf_path: str) -> Tuple[Dict[int, List[Tuple[float, float, float, float]]], Dict[int, Tuple[float, float]]]:
    """
    Fast extraction of table bounding boxes only (no images, no cell data).

    This function is used to get table regions BEFORE text extraction so that
    text inside tables can be filtered out before column detection. This prevents
    table text from distorting the reading order and column assignment.

    Returns:
        Tuple of:
        - Dict mapping page_no (1-indexed) to list of table bboxes.
          Each bbox is (x0, y0, x1, y1) in PyMuPDF coordinates (top-left origin).
        - Dict mapping page_no (1-indexed) to (page_width, page_height) in PyMuPDF coordinates.
          This is needed to calculate scale factors when comparing with pdftohtml coordinates.
    """
    doc = fitz.open(pdf_path)
    tables_by_page, _ = extract_tables(pdf_path, doc)

    result: Dict[int, List[Tuple[float, float, float, float]]] = {}
    page_dimensions: Dict[int, Tuple[float, float]] = {}

    # Store page dimensions for ALL pages (not just pages with tables)
    for page_no in range(1, len(doc) + 1):
        page = doc[page_no - 1]
        page_dimensions[page_no] = (page.rect.width, page.rect.height)

    for page_no, tables in tables_by_page.items():
        page_height = doc[page_no - 1].rect.height
        bboxes = []
        for t in tables:
            rect = camelot_bbox_to_fitz_rect(t._bbox, page_height)
            # Return as (x0, y0, x1, y1) tuple
            bboxes.append((rect.x0, rect.y0, rect.x1, rect.y1))
        result[page_no] = bboxes

    doc.close()
    return result, page_dimensions


def add_tables_for_page(
    pdf_path: str,
    doc: fitz.Document,
    page: fitz.Page,
    page_no: int,
    tables_for_page: List[Any],
    blocks: List[Dict[str, Any]],
    spans: List[Dict[str, Any]],
    media_dir: str,
    page_el: ET.Element,
    extracted_captions: List[Tuple[str, fitz.Rect]] = None,
    dpi: int = 200,
    require_table_caption: bool = True,
    max_caption_distance: float = 100.0,
) -> None:
    """
    For each Camelot table on this page:
      - compute region rect in PyMuPDF coords
      - validate table has a "Table X" caption nearby (if require_table_caption=True)
      - Match captions to tables by PROXIMITY, not by index
      - add <table> element with bbox (NO PNG rendering - bounds are unreliable)
      - add per-cell bbox + spans (font, size, color, text)
      - attach title/caption/links from extracted 'Table X.' keywords.

    Args:
        extracted_captions: List of (caption_text, rect) tuples from 'Table X.' keywords on this page.
                           Captions are matched to tables by PROXIMITY, not by index.
        require_table_caption: If True, skip tables that don't have a "Table X" caption nearby.
                               This helps filter out false positives from Camelot.
        max_caption_distance: Maximum distance (in points) between table and caption for a match.
    """
    page_height = page.rect.height
    tables_added = 0
    tables_skipped = 0

    # Track which captions have been used to avoid duplicate assignment
    used_captions = set()

    for idx, t in enumerate(tables_for_page, start=1):
        bbox_pdf = t._bbox  # (x1, y1, x2, y2) bottom-left origin
        table_rect = camelot_bbox_to_fitz_rect(bbox_pdf, page_height)

        # PNG rendering removed - table bounds are often incorrect
        # Table data is preserved in cell-level XML structure below

        # CLEANING: Remove extra rows (captions, paragraph text, headers/footers)
        # that Camelot may have incorrectly included in the table
        df_original = t.df
        df_cleaned, rows_removed = TableHeaderCleaner.clean_table_data(
            df_original, 
            verbose=True  # Print when rows are removed
        )
        
        # Track which rows to skip when processing cells
        # Rows 0 to (rows_removed-1) should be skipped
        skip_first_n_rows = rows_removed

        # VALIDATION: Check if table has actual drawn structure
        # If yes, use the precise structure bbox to filter out false positive rows
        structure_rect = validate_table_structure(page, table_rect, min_lines=2)
        
        # FALLBACK: If no drawn structure, try text-based validation
        text_validated_rect = None
        if not structure_rect:
            text_validated_rect = validate_text_only_table(page, table_rect, blocks, min_rows=2)
        
        # Determine validation status and final rect
        if structure_rect:
            validation_status = "has_structure"
            validated_rect = structure_rect
            validation_method = "drawing_lines"
        elif text_validated_rect:
            validation_status = "text_validated"
            validated_rect = text_validated_rect
            validation_method = "text_analysis"
        else:
            validation_status = "text_only"
            validated_rect = table_rect
            validation_method = "none"
        
        # Debug output for validation
        if structure_rect or text_validated_rect:
            # Calculate how much we're trimming
            # Use width * height to calculate area (Rect doesn't have get_area() method)
            area_original = table_rect.width * table_rect.height
            area_validated = validated_rect.width * validated_rect.height
            trim_pct = (1 - area_validated / area_original) * 100 if area_original > 0 else 0
            if trim_pct > 5:  # Only report significant trimming
                method_label = "Border" if structure_rect else "Text"
                print(f"    Page {page_no}, Table {idx}: {method_label} validation trimmed {trim_pct:.1f}% of bbox")

        # Find the closest caption to this table by PROXIMITY (not index)
        caption = ""
        best_distance = None

        if extracted_captions:
            for cap_idx, (cap_text, cap_rect) in enumerate(extracted_captions):
                # Skip already-used captions
                if cap_idx in used_captions:
                    continue

                # Calculate distance between table and caption
                # Caption can be above or below the table
                dist = None

                # Check if caption is ABOVE the table
                if cap_rect.y1 <= table_rect.y0:
                    dist = table_rect.y0 - cap_rect.y1

                # Check if caption is BELOW the table
                elif cap_rect.y0 >= table_rect.y1:
                    dist = cap_rect.y0 - table_rect.y1

                # Check horizontal overlap (caption should be horizontally aligned with table)
                if dist is not None:
                    h_overlap = min(table_rect.x1, cap_rect.x1) - max(table_rect.x0, cap_rect.x0)
                    if h_overlap < 0:
                        # No horizontal overlap - penalize heavily
                        dist += 500

                # Skip if too far
                if dist is None or dist > max_caption_distance:
                    continue

                # Keep the closest match
                if best_distance is None or dist < best_distance:
                    best_distance = dist
                    caption = cap_text
                    best_cap_idx = cap_idx

        # Mark caption as used if found
        if caption and best_distance is not None:
            used_captions.add(best_cap_idx)
        else:
            # Fallback: Look for "Table X" pattern near this specific table using text blocks
            caption = find_table_caption(table_rect, blocks, max_distance=60.0, require_table_pattern=True)

        # Caption is optional now - we filter by confidence score instead
        # Keep all tables that passed the 75% confidence threshold

        tables_added += 1
        title = caption  # Use caption as title

        # Save table snapshot image (for visual reference and backup)
        # Use validated_rect if available for more accurate bounds
        snapshot_rect = validated_rect if (structure_rect or text_validated_rect) else table_rect
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        table_img_filename = f"{base_name}_p{page_no:04d}_table{idx}.png"
        table_img_path = os.path.join(media_dir, table_img_filename)
        try:
            pix = page.get_pixmap(clip=snapshot_rect, dpi=dpi)
            pix.save(table_img_path)
        except Exception as e:
            print(f"    Warning: Could not save table snapshot: {e}")
            table_img_filename = ""  # Clear filename if save failed

        links = get_links_overlapping_rect(page, table_rect)

        # Create DocBook-compliant table structure
        # Store bounding box as attributes for reference during conversion
        # Include validation metadata
        table_attrs = {
            "id": f"p{page_no}_table{idx}",
            "frame": "all",
            "x1": str(table_rect.x0),
            "y1": str(table_rect.y0),
            "x2": str(table_rect.x1),
            "y2": str(table_rect.y1),
            "validation_status": validation_status,  # "has_structure", "text_validated", or "text_only"
            "validation_method": validation_method,  # "drawing_lines", "text_analysis", or "none"
        }

        # Add table snapshot image filename if saved successfully
        if table_img_filename:
            table_attrs["snapshot"] = table_img_filename
        
        # Add validated structure bbox if available (from either method)
        if structure_rect or text_validated_rect:
            table_attrs["validated_x1"] = str(validated_rect.x0)
            table_attrs["validated_y1"] = str(validated_rect.y0)
            table_attrs["validated_x2"] = str(validated_rect.x1)
            table_attrs["validated_y2"] = str(validated_rect.y1)
        
        table_el = ET.SubElement(page_el, "table", table_attrs)

        # Add title element (DocBook-compliant)
        if caption:
            title_el = ET.SubElement(table_el, "title")
            title_el.text = sanitize_xml_text(caption)

        # Note: Links are typically added to the caption/title, not as separate elements
        # For now, store them as comments or custom attributes if needed

        # ---- Create DocBook tgroup structure ----
        cells = t.cells  # 2D list [row][col]
        nrows = len(cells)
        ncols = len(cells[0]) if nrows > 0 else 0

        if ncols == 0:
            continue  # Skip empty tables

        # Create tgroup with required cols attribute
        tgroup_el = ET.SubElement(table_el, "tgroup", {"cols": str(ncols)})

        # Spans: list of {"text","bbox":(x0,y0,x1,y1),"font","size","color"}
        # We'll assign a span to a cell if its center lies inside the cell rect
        def spans_for_rect(cell_rect: fitz.Rect) -> List[Dict[str, Any]]:
            cell_spans: List[Dict[str, Any]] = []
            for s in spans:
                x0, y0, x1, y1 = s["bbox"]
                s_rect = fitz.Rect(x0, y0, x1, y1)
                if not s_rect.intersects(cell_rect):
                    continue
                cx = (x0 + x1) / 2.0
                cy = (y0 + y1) / 2.0
                if not cell_rect.contains(fitz.Point(cx, cy)):
                    continue
                cell_spans.append(s)
            return cell_spans

        # Create tbody (required in DocBook table structure)
        tbody_el = ET.SubElement(tgroup_el, "tbody")

        # Track rows filtered by structure validation and header cleaning
        rows_filtered = 0
        rows_filtered_header_cleaning = 0

        for r_idx in range(nrows):
            # HEADER CLEANING: Skip rows identified as extra content (captions, paragraphs, etc.)
            if r_idx < skip_first_n_rows:
                rows_filtered_header_cleaning += 1
                continue
            
            # VALIDATION: Check if row is within validated structure
            # Works for both drawing-based and text-based validation
            if structure_rect or text_validated_rect:
                # Check if any cell in this row is within the validated rect
                row_in_structure = False
                for c_idx_check in range(ncols):
                    cell_check = cells[r_idx][c_idx_check]
                    cbbox_check = (cell_check.x1, cell_check.y1, cell_check.x2, cell_check.y2)
                    cell_rect_check = camelot_bbox_to_fitz_rect(cbbox_check, page_height)
                    
                    # Check if cell center is within validated structure
                    cell_center = fitz.Point(
                        (cell_rect_check.x0 + cell_rect_check.x1) / 2,
                        (cell_rect_check.y0 + cell_rect_check.y1) / 2
                    )
                    if validated_rect.contains(cell_center):
                        row_in_structure = True
                        break
                
                # Skip row if it's outside the validated structure
                if not row_in_structure:
                    rows_filtered += 1
                    continue
            
            row_el = ET.SubElement(tbody_el, "row")
            
            for c_idx in range(ncols):
                cell = cells[r_idx][c_idx]
                # Camelot cell bbox in PDF coords
                cbbox_pdf = (cell.x1, cell.y1, cell.x2, cell.y2)
                cell_rect = camelot_bbox_to_fitz_rect(cbbox_pdf, page_height)

                # Create entry element (DocBook cell element)
                # Store bounding box as attributes for reference
                entry_el = ET.SubElement(
                    row_el,
                    "entry",
                    {
                        "x1": str(cell_rect.x0),
                        "y1": str(cell_rect.y0),
                        "x2": str(cell_rect.x1),
                        "y2": str(cell_rect.y1),
                    },
                )

                # Get spans for this cell
                cell_spans = spans_for_rect(cell_rect)
                
                if cell_spans:
                    # Create a para element to hold the cell content
                    para_el = ET.SubElement(entry_el, "para")
                    
                    # Combine spans into text with formatting preserved as attributes
                    # For now, concatenate text; styling can be preserved via emphasis elements
                    cell_text_parts = []
                    for s in cell_spans:
                        cell_text_parts.append(sanitize_xml_text(s["text"]))
                    
                    para_el.text = " ".join(cell_text_parts).strip()
                    
                    # Optionally store font info on para for reference
                    if cell_spans:
                        # Use the most common font in the cell
                        para_el.set("font-family", sanitize_xml_text(cell_spans[0]["font"]))
                        para_el.set("font-size", str(cell_spans[0]["size"]))
                else:
                    # Empty cell - still need a para for DTD compliance
                    para_el = ET.SubElement(entry_el, "para")
                    para_el.text = ""
        
        # Report cleaning and validation results for this table
        total_rows_filtered = rows_filtered + rows_filtered_header_cleaning
        if total_rows_filtered > 0:
            rows_kept = nrows - total_rows_filtered
            filter_pct = (total_rows_filtered / nrows) * 100 if nrows > 0 else 0
            
            reasons = []
            if rows_filtered_header_cleaning > 0:
                reasons.append(f"header cleaning: {rows_filtered_header_cleaning}")
            if rows_filtered > 0:
                reasons.append(f"structure validation: {rows_filtered}")
            
            reason_str = ", ".join(reasons)
            print(f"      Table {idx}: Filtered {total_rows_filtered}/{nrows} rows ({filter_pct:.1f}%) "
                  f"[{reason_str}] - kept {rows_kept} valid rows")

    # Log summary for this page
    if tables_added > 0 or tables_skipped > 0:
        print(f"    Page {page_no}: Added {tables_added} table(s), skipped {tables_skipped} table(s)")


# ----------------------------
# Main driver
# ----------------------------

def extract_media_and_tables(
    pdf_path: str,
    out_dir: str | None = None,
    dpi: int = 200,
    require_table_caption: bool = True,
    max_caption_distance: float = 100.0,
) -> str:
    """
    Extracts:
      - raster images (ALL images except full-page decorative and header/footer logos)
      - vector drawing snapshots (clustered into full blocks)
      - tables + per-cell bboxes + spans

    Image Filtering Rules:
      - CAPTURE: All images in content area (author photos, figures, diagrams, etc.)
      - SKIP: Images in headers/footers/margins (logos, page numbers, running headers)
      - SKIP: Full-page decorative images (backgrounds, watermarks covering >85% of page)

    Table Filtering:
      - If require_table_caption=True, only tables with "Table X" captions are kept
      - If require_table_caption=False, all Camelot detections are kept (may include false positives)
      - max_caption_distance controls how far caption can be from table (in points)

    Args:
        pdf_path: Path to input PDF
        out_dir: Optional output directory for media files
        dpi: DPI for rendering images
        require_table_caption: If True, filter out tables without "Table X" captions (default: True)
        max_caption_distance: Maximum distance between table and caption in points (default: 100.0)

    Saves:
      - All PNGs into <basename>_MultiMedia/
      - XML metadata into <basename>_MultiMedia.xml

    Returns path to XML file.
    """
    pdf_path = os.path.abspath(pdf_path)
    base_dir = os.path.dirname(pdf_path)
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]

    # Output folder: <inputfile>_MultiMedia
    media_dir = out_dir or os.path.join(base_dir, f"{base_name}_MultiMedia")
    ensure_dir(media_dir)

    xml_path = os.path.join(base_dir, f"{base_name}_MultiMedia.xml")

    doc = fitz.open(pdf_path)
    root = ET.Element("document", {"source": os.path.basename(pdf_path)})

    print("Running table detection with Camelot...")
    tables_by_page, captions_by_page = extract_tables(pdf_path, doc)
    print("Table detection done.")

    num_pages = len(doc)
    print(f"Processing {num_pages} pages...")

    for page_index in range(num_pages):
        page_no = page_index + 1
        page = doc[page_index]
        page_rect = page.rect

        page_el = ET.SubElement(
            root,
            "page",
            {
                "index": str(page_no),
                "width": str(page_rect.width),
                "height": str(page_rect.height),
            },
        )

        # Calculate content area (excludes headers, footers, and margins)
        # This prevents capturing logos, page numbers, and decorative elements
        content_area = get_content_area_rect(
            page_rect,
            header_margin_pct=0.08,   # Skip top 8% (header area)
            footer_margin_pct=0.08,   # Skip bottom 8% (footer area)
            left_margin_pct=0.05,     # Skip left 5% (margin)
            right_margin_pct=0.05,    # Skip right 5% (margin)
        )

        # Get drawings for form detection
        drawings = page.get_drawings()

        # Check for complex form pages (should be rendered as full-page image)
        is_form, form_reason = is_form_page(page, drawings)

        # Check for RTL/complex script pages (may not render correctly when text extracted)
        has_rtl, rtl_reason = has_rtl_or_complex_script(page)

        # If page is a complex form OR has significant RTL content, render as full-page image
        if is_form or has_rtl:
            render_reasons = []
            if is_form:
                render_reasons.append(f"Form: {form_reason}")
            if has_rtl:
                render_reasons.append(f"RTL/Complex script: {rtl_reason}")

            combined_reason = "; ".join(render_reasons)

            # Mark page as full-page-image in XML
            page_el.set("render_mode", "fullpage")
            page_el.set("render_reason", combined_reason)

            render_full_page_as_image(
                page=page,
                page_no=page_no,
                media_dir=media_dir,
                page_el=page_el,
                dpi=dpi,
                reason=combined_reason,
                crop_margins=True,
                header_margin_pct=0.08,   # Skip top 8% (header area)
                footer_margin_pct=0.08,   # Skip bottom 8% (footer area)
                left_margin_pct=0.05,     # Skip left 5% (margin)
                right_margin_pct=0.05,    # Skip right 5% (margin)
            )

            # Skip normal text/media extraction for this page
            continue

        # Collect text blocks once (for caption / title heuristics)
        blocks = get_text_blocks(page)
        # Collect detailed spans once (for cell-level chunks)
        spans = get_page_spans(page)

        # Raster images - get rectangles for deduplication with vectors
        page_raster_rects = extract_raster_images_for_page(
            page=page,
            page_no=page_no,
            blocks=blocks,
            media_dir=media_dir,
            page_el=page_el,
            content_area=content_area,
            dpi=dpi,
        )

        # Compute table bounding rects for this page (to avoid duplicate captures)
        page_table_rects: List[fitz.Rect] = []
        if page_no in tables_by_page:
            page_height = page_rect.height
            for t in tables_by_page[page_no]:
                t_rect = camelot_bbox_to_fitz_rect(t._bbox, page_height)
                page_table_rects.append(t_rect)

        # Vector drawings (clustered) - skip areas already captured as raster/table
        extract_vector_blocks_for_page(
            page=page,
            page_no=page_no,
            blocks=blocks,
            spans=spans,
            media_dir=media_dir,
            page_el=page_el,
            table_rects=page_table_rects,
            raster_rects=page_raster_rects,
            content_area=content_area,
            dpi=dpi,
        )

        # Tables on this page (if any)
        if page_no in tables_by_page:
            # Get extracted captions for this page
            page_captions = captions_by_page.get(page_no, [])

            add_tables_for_page(
                pdf_path=pdf_path,
                doc=doc,
                page=page,
                page_no=page_no,
                tables_for_page=tables_by_page[page_no],
                blocks=blocks,
                spans=spans,
                media_dir=media_dir,
                page_el=page_el,
                extracted_captions=page_captions,
                dpi=dpi,
                require_table_caption=require_table_caption,
                max_caption_distance=max_caption_distance,
            )

        # Progress reporting
        if page_no == 1 or page_no % 10 == 0 or page_no == num_pages:
            print(f"  Processed page {page_no}/{num_pages}")
        
        # Aggressive garbage collection every 50 pages to free memory
        # This is critical for large PDFs (500+ pages) to avoid memory accumulation
        if page_no % 50 == 0:
            gc.collect()
            print(f"  [Memory cleanup after page {page_no}]")

    doc.close()

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")  # Python 3.9+
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)

    # Count tables in final XML
    total_tables_in_xml = len(root.findall('.//table'))
    total_tables_detected = sum(len(tables) for tables in tables_by_page.values())
    
    # Count validation statistics
    tables_with_structure = len([t for t in root.findall('.//table') 
                                 if t.get('validation_status') == 'has_structure'])
    tables_text_validated = len([t for t in root.findall('.//table') 
                                 if t.get('validation_status') == 'text_validated'])
    tables_text_only = len([t for t in root.findall('.//table') 
                           if t.get('validation_status') == 'text_only'])
    
    print(f"\n{'='*60}")
    print(f"Table Extraction Summary:")
    print(f"  Total tables detected by Camelot: {total_tables_detected}")
    print(f"  Total tables written to media.xml: {total_tables_in_xml}")
    if total_tables_in_xml < total_tables_detected:
        print(f"  Tables filtered out: {total_tables_detected - total_tables_in_xml}")
        print(f"  Reason: No 'Table X' caption found within 100 points")
        print(f"  Tip: Check the detailed logs above to see which tables were skipped")
    print(f"\nValidation Summary:")
    print(f"  Tables validated by drawn borders: {tables_with_structure}")
    print(f"  Tables validated by text analysis: {tables_text_validated}")
    print(f"  Tables with no validation: {tables_text_only}")
    print(f"  Total validated: {tables_with_structure + tables_text_validated}/{total_tables_in_xml}")
    if tables_with_structure > 0:
        print(f"\n  ✓ Border validation: Used actual PDF drawing lines to define table boundaries")
    if tables_text_validated > 0:
        print(f"  ✓ Text validation: Used column alignment and row spacing to refine boundaries")
    if tables_with_structure + tables_text_validated > 0:
        print(f"  Note: Validated tables had rows outside boundaries automatically filtered")
    print(f"{'='*60}\n")
    
    print(f"XML metadata written to: {xml_path}")
    print(f"Media saved under: {media_dir}")
    
    # Post-process multimedia XML to remove rows outside validated boundaries
    if HAS_MULTIMEDIA_VALIDATION and total_tables_in_xml > 0:
        print(f"\n{'='*60}")
        print("POST-PROCESSING: Validating table boundaries")
        print(f"{'='*60}")
        try:
            validation_stats = update_multimedia_xml(
                multimedia_xml_path=xml_path,
                pdf_path=pdf_path,
                output_path=xml_path,  # Overwrite the original
                min_lines=2,
                margin=5.0,
                create_backup=True,
                create_intermediate=True
            )
            
            print(f"\n{'='*60}")
            print("Table Boundary Validation Results:")
            print(f"  Total tables processed: {validation_stats['total_tables']}")
            print(f"  Tables with drawn structure: {validation_stats['tables_with_structure']}")
            print(f"  Text-only tables: {validation_stats['tables_text_only']}")
            print(f"\n  Rows before validation: {validation_stats['total_rows_before']}")
            print(f"  Rows after validation: {validation_stats['total_rows_after']}")
            if validation_stats['total_rows_before'] > 0:
                removed = validation_stats['total_rows_before'] - validation_stats['total_rows_after']
                pct = removed / validation_stats['total_rows_before'] * 100
                print(f"  Rows removed (outside boundaries): {removed} ({pct:.1f}%)")
            
            if validation_stats.get('backup_path'):
                print(f"\n  ✓ Backup created: {validation_stats['backup_path']}")
            if validation_stats.get('intermediate_path'):
                print(f"  ✓ Intermediate XML: {validation_stats['intermediate_path']}")
            
            print(f"{'='*60}\n")
        except Exception as e:
            print(f"Warning: Table boundary validation failed: {e}")
            print(f"Continuing with unvalidated multimedia XML at: {xml_path}")
    elif not HAS_MULTIMEDIA_VALIDATION:
        print(f"\nNote: Table boundary validation skipped (update_multimedia_with_validation.py not available)")
    
    return xml_path


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Extract all media (raster images, vector drawing blocks, tables with per-cell bboxes/spans) "
            "from a PDF into <input>_MultiMedia/ + XML metadata."
        )
    )
    parser.add_argument("pdf_path", help="Path to input PDF")
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Render dpi for PNG snapshots (default: 200)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional override for multimedia folder. "
             "Default: <inputfile>_MultiMedia in the same directory.",
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

    args = parser.parse_args()

    extract_media_and_tables(
        pdf_path=args.pdf_path,
        out_dir=args.out,
        dpi=args.dpi,
        require_table_caption=not args.no_caption_filter,
        max_caption_distance=args.caption_distance,
    )


if __name__ == "__main__":
    main()
