from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Iterable, List, Optional, Sequence
from typing import Optional, Sequence
from lxml import etree

# Import word split fixer
try:  # Prefer absolute import when executed as a script
    from enhanced_word_split_fixer import fix_word_splits_enhanced
except ImportError:  # pragma: no cover - fallback when packaged differently
    try:
        from .enhanced_word_split_fixer import fix_word_splits_enhanced
    except ImportError:
        # Fallback if the helper is unavailable
        def fix_word_splits_enhanced(text: str) -> str:  # type: ignore[override]
            return text


# Import PyPDF2 for bookmark extraction (fallback to older name if needed)
try:
    from pypdf import PdfReader
except ImportError:
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        PdfReader = None

logger = logging.getLogger(__name__)

# Additional imports for new preprocessing functionality
from typing import Set, Tuple, Dict
from collections import defaultdict

# Import link processor and reference mapper for cross-references and figure labeling
try:
    from link_processor import (
        LinkProcessor,
        extract_figure_label_from_caption,
        generate_chapter_based_image_name,
        normalize_figure_label
    )
    from reference_mapper import get_mapper, ReferenceMapper
    HAS_LINK_PROCESSOR = True
except ImportError:
    HAS_LINK_PROCESSOR = False
    logger.warning("Link processor not available - figure cross-references will not be generated")


def _f(x):
    try:
        return float(x)
    except Exception:
        return float("nan")

# --- compatibility: synthesize a "line" view from an entry ---
class _LineCompat:
    __slots__ = (
        "text",
        "font_size",
        "top",
        "left",
        "right",
        "bottom",
        "height",
        "el",
        "page_height",
        "page_width",
        "page_num",
        "para_elem",
        "para_id",
        "text_elem",
    )

    def __init__(
        self,
        text,
        font_size,
        top,
        left,
        el=None,
        page_height=None,
        page_width=None,
        page_num=None,
        right=None,
        bottom=None,
        height=None,
        para_elem=None,
        para_id=None,
        text_elem=None,
    ):
        self.text = text or ""
        self.font_size = font_size
        self.top = top
        self.left = left
        self.right = right
        self.bottom = bottom
        self.height = height
        self.el = el  # optional backref
        self.page_height = page_height
        self.page_width = page_width
        self.page_num = page_num
        self.para_elem = para_elem
        self.para_id = para_id
        self.text_elem = text_elem

def _line_of(entry: dict) -> _LineCompat:
    # If the old path provided a real line, prefer it.
    if "line" in entry and entry["line"] is not None:
        ln = entry["line"]
        # assume ln has .text, .font_size, .top, .left
        return _LineCompat(
            getattr(ln, "text", ""),
            getattr(ln, "font_size", None),
            getattr(ln, "top", None),
            getattr(ln, "left", None),
            getattr(ln, "el", None),
            getattr(ln, "page_height", None),
            getattr(ln, "page_width", None),
            getattr(ln, "page_num", None),
            getattr(ln, "right", None),
            getattr(ln, "bottom", None),
            getattr(ln, "height", None),
            getattr(ln, "para_elem", None),
            getattr(ln, "para_id", None),
            getattr(ln, "text_elem", None),
        )
    # New path (preserve-flow entries): synthesize from the entry itself
    el = entry.get("el")
    right = entry.get("right", None)
    bottom = entry.get("bottom", None)
    height = entry.get("height", None)
    left = entry.get("left", None)
    top = entry.get("top", None)

    if el is not None:
        if height is None:
            hv = el.get("height")
            if hv is not None:
                try:
                    height = float(hv)
                except Exception:
                    pass
        if right is None:
            wv = el.get("width")
            if wv is not None and left is not None:
                try:
                    width_f = float(wv)
                    left_f = float(left)
                    right = left_f + width_f
                except Exception:
                    pass
        if bottom is None and top is not None and height is not None:
            try:
                bottom = float(top) + float(height)
            except Exception:
                pass

    return _LineCompat(
        entry.get("text", ""),
        entry.get("font_size", None),
        top,
        left,
        entry.get("el"),
        entry.get("page_height", None),
        entry.get("page_width", None),
        entry.get("page_num", None),
        right,
        bottom,
        height,
        entry.get("para_elem"),
        entry.get("para_id"),
        entry.get("text_elem"),
    )

def _collect_fontspecs(root):
    """Map fontspec id -> size (float)."""
    mp = {}
    for fs in root.findall(".//fontspec"):
        fid = fs.get("id")
        sz = fs.get("size")
        if not fid or not sz:
            continue
        try:
            mp[fid] = float(sz)
        except Exception:
            pass
    return mp

def _max_text_size(text_el, fontspec_map):
    """
    Return the best font size for a <text> node:
    - prefer explicit text_el.get('font_size') if present
    - else resolve text_el.get('font') via fontspecs
    - else check child <span font="..."> and take max
    """
    v = text_el.get("font_size")
    if v:
        try:
            return float(v)
        except Exception:
            pass

    f = text_el.get("font")
    if f and f in fontspec_map:
        return fontspec_map[f]

    best = None
    for sp in text_el.findall(".//span"):
        sf = sp.get("font")
        if sf and sf in fontspec_map:
            sz = fontspec_map[sf]
            best = sz if best is None or sz > best else best
    return best

# ============================================================================
# NEW: Global Coordinate Transformation Functions
# ============================================================================

def _add_global_coordinates(tree: etree._ElementTree) -> dict:
    """
    Add global coordinate metadata and transform all coordinates from page-relative to document-global.
    
    Returns:
        Metadata dict with page offsets and dimensions
    """
    pages = tree.findall(".//page")
    cumulative_height = 0.0
    page_metadata = []
    
    for page in pages:
        page_num = int(page.get("number", "0") or 0)
        page_height = float(page.get("height", "0") or 0)
        page_width = float(page.get("width", "0") or 0)
        
        # Store metadata
        page_metadata.append({
            "page_num": page_num,
            "offset_top": cumulative_height,
            "page_width": page_width,
            "page_height": page_height,
        })
        
        # Add global coordinates to all text elements
        for text_node in page.findall("text"):
            local_top = float(text_node.get("top", "0") or 0)
            local_left = float(text_node.get("left", "0") or 0)
            
            # Add global attributes
            text_node.set("global_top", str(cumulative_height + local_top))
            text_node.set("global_left", str(local_left))
            text_node.set("norm_top", str(local_top / page_height if page_height else 0))
            text_node.set("norm_left", str(local_left / page_width if page_width else 0))
        
        # Add global coordinates to images
        for image_node in page.findall("image"):
            local_top = float(image_node.get("top", "0") or 0)
            local_left = float(image_node.get("left", "0") or 0)
            
            image_node.set("global_top", str(cumulative_height + local_top))
            image_node.set("global_left", str(local_left))
            image_node.set("norm_top", str(local_top / page_height if page_height else 0))
            image_node.set("norm_left", str(local_left / page_width if page_width else 0))
        
        cumulative_height += page_height
    
    return {
        "total_pages": len(pages),
        "page_metadata": page_metadata,
        "total_height": cumulative_height,
    }


# ============================================================================
# NEW: Print Artifact Removal Functions
# ============================================================================

def _is_roman_numeral(text: str) -> bool:
    """Check if text is a Roman numeral (i, ii, iii, iv, v, etc.)"""
    if not text:
        return False
    return bool(re.match(r'^[ivxlcdm]+$', text, re.IGNORECASE))


def _is_sequential_pattern(texts: Set[str]) -> bool:
    """
    Check if texts follow a sequential pattern like page numbers.
    Returns True for page number patterns, False for content.
    """
    if not texts or len(texts) < 2:
        return False

    # Don't mark chapter/section headings as sequential (they're content!)
    # But DO mark running headers that are "Chapter Title + page number"
    text_lower = [t.lower() for t in texts]
    content_keywords = ["chapter", "section", "unit", "lesson", "appendix", "part"]

    # Check if these look like chapter HEADINGS (short, start with keyword)
    # vs running HEADERS (longer text with page number at end)
    looks_like_chapter_heading = any(
        any(t.startswith(keyword) for keyword in content_keywords) and len(t) < 30
        for t in text_lower
    )
    if looks_like_chapter_heading:
        return False

    # Check for numeric sequences
    digits = []
    for text in texts:
        nums = re.findall(r'\d+', text)
        if nums:
            digits.extend(int(n) for n in nums)

    if len(digits) >= 2:
        sorted_digits = sorted(set(digits))
        if len(sorted_digits) >= 2:
            span = sorted_digits[-1] - sorted_digits[0]
            if span <= len(sorted_digits) * 3:
                return True

    # Check for roman numerals
    if len(texts) >= 2 and all(_is_roman_numeral(t) for t in texts):
        return True

    # Check for "Page X" patterns
    page_pattern = re.compile(r'^(page\s+)?\d+$', re.IGNORECASE)
    if len(texts) >= 2 and all(page_pattern.match(t) for t in texts):
        return True

    # Check for running header pattern: "Title/Chapter Name + Page Number"
    # These share a common prefix/suffix but differ only in the page number
    if _is_running_header_with_page_number(texts):
        return True

    return False


def _is_running_header_with_page_number(texts: Set[str]) -> bool:
    """
    Detect running headers that combine title text with page numbers.

    Examples that should match:
    - "MRI Bioeffects, Safety, and Patient Management 993" (text + number)
    - "993 MRI Bioeffects, Safety, and Patient Management" (number + text)

    These share a common text part but differ only in the page number.
    """
    if len(texts) < 3:
        return False

    text_list = list(texts)

    # Try pattern 1: "Text + Number" (trailing page number)
    if _check_text_plus_number_pattern(text_list):
        return True

    # Try pattern 2: "Number + Text" (leading page number)
    if _check_number_plus_text_pattern(text_list):
        return True

    return False


def _check_text_plus_number_pattern(text_list: list) -> bool:
    """Check for 'Title Text 123' pattern (page number at end)."""
    trailing_number_pattern = re.compile(r'^(.+?)\s+(\d+)\s*$')
    prefixes = []
    numbers = []

    for text in text_list:
        match = trailing_number_pattern.match(text)
        if match:
            prefixes.append(match.group(1).strip())
            numbers.append(int(match.group(2)))
        else:
            return False

    # Check if all prefixes are the same
    unique_prefixes = set(prefixes)
    if len(unique_prefixes) != 1:
        return False

    # Check if numbers are sequential (page numbers)
    return _are_sequential_page_numbers(numbers, list(unique_prefixes)[0])


def _check_number_plus_text_pattern(text_list: list) -> bool:
    """Check for '123 Title Text' pattern (page number at beginning)."""
    leading_number_pattern = re.compile(r'^\s*(\d+)\s+(.+)$')
    suffixes = []
    numbers = []

    for text in text_list:
        match = leading_number_pattern.match(text)
        if match:
            numbers.append(int(match.group(1)))
            suffixes.append(match.group(2).strip())
        else:
            return False

    # Check if all suffixes are the same
    unique_suffixes = set(suffixes)
    if len(unique_suffixes) != 1:
        return False

    # Check if numbers are sequential (page numbers)
    return _are_sequential_page_numbers(numbers, list(unique_suffixes)[0])


def _are_sequential_page_numbers(numbers: list, text_part: str) -> bool:
    """Check if numbers look like sequential page numbers and text is substantial."""
    if len(numbers) < 3:
        return False

    sorted_nums = sorted(set(numbers))
    if len(sorted_nums) < 3:
        return False

    # Numbers should be reasonably sequential (within 3x the count)
    span = sorted_nums[-1] - sorted_nums[0]
    if span > len(sorted_nums) * 3:
        return False

    # Text part should be substantial (not just "Page" or single word)
    # Multi-word or 10+ characters indicates a chapter/section title
    if len(text_part) >= 10 or ' ' in text_part:
        return True

    return False


def _remove_duplicate_text(page: etree._Element) -> int:
    """
    Remove duplicate text nodes at the same position.
    Returns count of duplicates removed.
    """
    text_nodes = list(page.findall("text"))
    if not text_nodes:
        return 0
    
    position_groups = defaultdict(list)
    
    for node in text_nodes:
        top = node.get("top")
        left = node.get("left")
        text = "".join(node.itertext()).strip()
        width = node.get("width")
        height = node.get("height")
        
        key = (top, left, text, width, height)
        position_groups[key].append(node)
    
    removed_count = 0
    for key, nodes in position_groups.items():
        if len(nodes) > 1:
            for duplicate in nodes[1:]:
                parent = duplicate.getparent()
                if parent is not None:
                    parent.remove(duplicate)
                    removed_count += 1
    
    return removed_count


def _detect_repeated_text_patterns(tree: etree._ElementTree) -> Tuple[Set[str], Set[str]]:
    """
    Find text that repeats in the same position across multiple pages.
    
    Returns:
        Tuple of (repeated_patterns, copyright_patterns)
        - repeated_patterns: Text to filter out everywhere
        - copyright_patterns: Copyright text (keep first occurrence only)
    """
    position_text = defaultdict(list)
    copyright_patterns = set()
    
    for page in tree.findall(".//page"):
        page_num = int(page.get("number", "0") or 0)
        page_height = float(page.get("height", "0") or 0)
        page_width = float(page.get("width", "0") or 0)
        
        if not page_height or not page_width:
            continue
        
        for text_node in page.findall("text"):
            text = "".join(text_node.itertext()).strip()
            if not text or len(text) < 2:
                continue
            
            # Check for copyright patterns
            if text.lower().startswith("copyright") or "©" in text or "(c)" in text.lower():
                copyright_patterns.add(text)
            
            top = float(text_node.get("top", "0") or 0)
            left = float(text_node.get("left", "0") or 0)
            norm_top = round(top / page_height, 2)
            norm_left = round(left / page_width, 2)
            
            in_header_footer_zone = norm_top < 0.12 or norm_top > 0.88
            position_text[(norm_top, norm_left)].append((text, page_num, in_header_footer_zone))
    
    repeated_patterns = set()
    
    for position, occurrences in position_text.items():
        if len(occurrences) < 3:
            continue
        
        hf_occurrences = [(text, page) for text, page, in_zone in occurrences if in_zone]
        unique_texts = set(text for text, _, _ in occurrences)
        
        # Exact repetition
        if len(unique_texts) == 1 and len(hf_occurrences) >= 3:
            text = list(unique_texts)[0]
            if len(text) <= 100:
                # Don't add copyright to repeated_patterns (we handle it separately)
                if not (text.lower().startswith("copyright") or "©" in text):
                    repeated_patterns.add(text)
        
        # Sequential pattern (page numbers, etc.)
        elif len(unique_texts) >= 2 and _is_sequential_pattern(unique_texts):
            if len(hf_occurrences) >= 2:
                repeated_patterns.update(unique_texts)
    
    return repeated_patterns, copyright_patterns


def _is_header_footer_enhanced(line, repeated_patterns: Set[str], 
                                 copyright_patterns: Set[str], 
                                 seen_copyright: Set[str]) -> bool:
    
    ph = getattr(line, "page_height", None)
    lt = getattr(line, "top", None)
    if ph is None or lt is None:
        return False
    """
    Enhanced header/footer detection.
    Keeps first occurrence of copyright, removes repeats.
    
    Args:
        line: Line object
        repeated_patterns: Patterns to always filter
        copyright_patterns: Copyright text patterns
        seen_copyright: Set tracking which copyright we've already seen
    """
    text = line.text.strip()
    if not text:
        return True
    
    # Check if this is a copyright pattern
    if text in copyright_patterns:
        # Keep first occurrence (on early pages)
        if line.page_num is not None and line.page_num <= 5 and text not in seen_copyright:
            seen_copyright.add(text)
            return False  # Keep it
        else:
            return True  # Remove repeated copyright
    
    # Check if in repeated patterns
    if text in repeated_patterns:
        return True
    
    # ========== POSITION-BASED DETECTION ==========
    
    # Simple numeric page numbers
    if len(text) <= 4 and text.isdigit():
        if line.page_height and (
            line.top < line.page_height * 0.08 or line.top > line.page_height * 0.9
        ):
            return True
    
    # Roman numerals as page numbers
    if _is_roman_numeral(text) and len(text) <= 5:
        if line.page_height and (
            line.top < line.page_height * 0.08 or line.top > line.page_height * 0.9
        ):
            return True
    
    # ========== PATTERN-BASED DETECTION ==========
    
    # Print production artifacts
    production_patterns = [
        r'\d{3,}-\d{3,}.*\.indd',
        r'\d{3,}-\d{3,}.*\.qxd',
        r'\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}',
        r'^\d{4}-\d{2}-\d{2}$',
        r'^\d{2}-\d{2}-\d{4}$',
    ]
    
    for pattern in production_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    
    # Continuation markers
    continuation_patterns = [
        r'\(continued\)',
        r'continued on',
        r'continued from',
        r'\(cont\.\)',
        r'\(cont\)',
    ]
    
    for pattern in continuation_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    
    # "Page X of Y"
    if re.match(r'^page\s+\d+(\s+of\s+\d+)?$', text, re.IGNORECASE):
        return True
    
    # Short text at page edges
    if len(text) <= 25:
        if line.page_height and (
            line.top < line.page_height * 0.10 or line.top > line.page_height * 0.88
        ):
            if line.page_width and (
                line.left < line.page_width * 0.15 or line.left > line.page_width * 0.80
            ):
                if line.font_size is not None and line.font_size < 16:
                    return True
    
    # Isolated numbers at bottom
    if text.isdigit() and len(text) <= 4:
        if line.page_height and line.top > line.page_height * 0.85:
            return True
    
    # Publisher/ISBN info at bottom
    publisher_patterns = [
        r'isbn[-:\s]*\d',
        r'published by',
        r'all rights reserved',
        r'printed in',
    ]
    
    for pattern in publisher_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            if line.page_height and line.top > line.page_height * 0.80:
                return True
    
    return False


def _preprocess_xml_for_ebook(tree: etree._ElementTree) -> Tuple[dict, Set[str], Set[str]]:
    """
    Master preprocessing function for ebook production.
    
    Returns:
        Tuple of (statistics, repeated_patterns, copyright_patterns)
    """
    stats = {
        "duplicates_removed": 0,
        "repeated_patterns_found": 0,
        "copyright_patterns_found": 0,
        "total_pages": 0,
    }
    
    # Phase 1: Remove duplicates
    logger.info("📄 Phase 1: Removing duplicate/layered text...")
    for page in tree.findall(".//page"):
        stats["total_pages"] += 1
        duplicates = _remove_duplicate_text(page)
        stats["duplicates_removed"] += duplicates
    
    if stats["duplicates_removed"] > 0:
        logger.info(f"   ✅ Removed {stats['duplicates_removed']} duplicate text nodes")
    else:
        logger.info(f"   ✓ No duplicates found")
    
    # Phase 2: Detect repeated patterns
    logger.info("🔍 Phase 2: Detecting repeated patterns...")
    repeated_patterns, copyright_patterns = _detect_repeated_text_patterns(tree)
    stats["repeated_patterns_found"] = len(repeated_patterns)
    stats["copyright_patterns_found"] = len(copyright_patterns)
    
    if repeated_patterns:
        logger.info(f"   ✅ Found {len(repeated_patterns)} repeated patterns to remove")
        for pattern in sorted(list(repeated_patterns)[:5]):
            logger.info(f"      • {pattern}")
        if len(repeated_patterns) > 5:
            logger.info(f"      ... and {len(repeated_patterns) - 5} more")
    
    if copyright_patterns:
        logger.info(f"   ℹ️  Found {len(copyright_patterns)} copyright patterns (keeping first occurrence)")
    
    return stats, repeated_patterns, copyright_patterns




@dataclass
class TextSegment:
    text: str
    left: float
    width: float
    font_size: float


@dataclass
class Line:
    page_num: int
    page_width: float
    page_height: float
    top: float
    left: float
    height: float
    font_size: float
    text: str = ""
    segments: List[TextSegment] = field(default_factory=list)
    column_index: Optional[int] = None

    @property
    def right(self) -> float:
        return max((seg.left + seg.width) for seg in self.segments) if self.segments else self.left

    @property
    def column_positions(self) -> List[float]:
        """Return the canonical left positions for text columns within the line."""

        positions: List[float] = []
        tolerance = 6.0
        for segment in sorted(self.segments, key=lambda s: s.left):
            placed = False
            for idx, value in enumerate(positions):
                if abs(value - segment.left) <= tolerance:
                    # Smooth the column position to absorb minor jitter
                    positions[idx] = (positions[idx] + segment.left) / 2.0
                    placed = True
                    break
            if not placed:
                positions.append(segment.left)
        return sorted(positions)


def _clean_join(segments: Sequence[TextSegment]) -> str:
    parts: List[str] = []
    for segment in sorted(segments, key=lambda s: s.left):
        text = segment.text
        if not text:
            continue
        if parts and not parts[-1].endswith(" ") and not text.startswith(" "):
            parts.append(" ")
        parts.append(text)
    # Apply word split fixer to handle PDF ligature issues
    result = "".join(parts)
    return fix_word_splits_enhanced(result)


def _parse_lines(page: etree._Element, fontspecs: dict) -> List[Line]:
    nodes = sorted(
        [
            (
                float(node.get("top", "0")),
                float(node.get("left", "0")),
                node,
            )
            for node in page.findall("text")
        ],
        key=lambda item: (item[0], item[1]),
    )

    lines: List[Line] = []
    tolerance = 2.0
    for top, left, node in nodes:
        content = "".join(node.itertext())
        if not content.strip():
            continue
        font_id = node.get("font")
        fontspec = fontspecs.get(font_id, {})
        font_size = float(fontspec.get("size", node.get("size", 0)) or 0)
        width = float(node.get("width", "0"))
        height = float(node.get("height", "0"))
        segment = TextSegment(text=content, left=left, width=width, font_size=font_size)

        if lines and abs(lines[-1].top - top) <= tolerance:
            line = lines[-1]
            line.segments.append(segment)
            line.left = min(line.left, left)
            line.height = max(line.height, height)
            if segment.font_size:
                line.font_size = max(line.font_size, segment.font_size)
        else:
            lines.append(
                Line(
                    page_num=int(page.get("number", "0") or 0),
                    page_width=float(page.get("width", "0") or 0),
                    page_height=float(page.get("height", "0") or 0),
                    top=top,
                    left=left,
                    height=height,
                    font_size=font_size,
                    segments=[segment],
                )
            )

        # --- finalize lines safely ---
    valid_lines = []
    for line in lines:
        # join segments into text
        line.text = _clean_join(line.segments)
        # collect sizes (skip Nones)
        sizes = [seg.font_size for seg in line.segments if seg.font_size is not None]
        # drop lines with no segments or no usable sizes
        if not line.segments or not sizes:
            continue
        # safe max
        line.font_size = max(sizes)
        valid_lines.append(line)

    # return only lines that have visible text
    return [ln for ln in valid_lines if ln.text and ln.text.strip()]


def _detect_page_columns(lines: Sequence[Line]) -> List[float]:
    """
    Enhanced column detection with tighter thresholds for index pages.
    """
    if not lines:
        return []
    
    # Check if this looks like an index page
    first_lines = [line.text.lower() for line in lines[:5]]
    has_index_keyword = any('index' in text or 'glossary' in text for text in first_lines)
    lines_with_numbers = sum(1 for line in lines if re.search(r'\d+', line.text))
    number_ratio = lines_with_numbers / len(lines) if lines else 0
    is_index_page = has_index_keyword or number_ratio > 0.5
    
    if is_index_page:
        tolerance = 15.0
        min_samples = 2
        logger.info(f"🔖 Detected index/glossary page")
    else:
        tolerance = 25.0
        min_samples = 3
    
    page_width = max((line.page_width for line in lines), default=0.0)
    bins: List[dict] = []
    
    for line in lines:
        if line.right is None or line.left is None:
            continue
        width = line.right - line.left
        if page_width and width >= page_width * 0.65:
            continue
        if width < 20:
            continue
        position = line.left
        for bin_ in bins:
            if abs(bin_["pos"] - position) <= tolerance:
                bin_["pos"] = (bin_["pos"] * bin_["count"] + position) / (bin_["count"] + 1)
                bin_["count"] += 1
                break
        else:
            bins.append({"pos": position, "count": 1})
    
    columns = [bin_["pos"] for bin_ in bins if bin_["count"] >= min_samples]
    columns.sort()
    
    if len(columns) <= 1:
        return []
    
    if page_width:
        span = columns[-1] - columns[0]
        min_span = max(120.0, page_width * 0.25)
        if span < min_span:
            return []
    
    filtered: List[float] = []
    for pos in columns:
        if not filtered:
            filtered.append(pos)
            continue
        if all(abs(pos - existing) > tolerance for existing in filtered):
            filtered.append(pos)
    
    result = filtered if len(filtered) >= 2 else []
    if result:
        logger.info(f"📊 Detected {len(result)} columns at positions: {[f'{p:.1f}' for p in result]}")
    return result

def _assign_column(line: Line, columns: Sequence[float]) -> Optional[int]:
    """Assign line to column using center point."""
    if not columns:
        return None
    if line.right is None or line.left is None:
        return None
    width = line.right - line.left
    page_width = line.page_width or 0.0
    if page_width and width >= page_width * 0.65:
        return None
    line_center = line.left + (width / 2)
    nearest = min(range(len(columns)), key=lambda idx: abs(columns[idx] - line_center))
    if abs(columns[nearest] - line.left) > 50.0:
        return None
    return nearest

def _emit_column_chunk(chunk: Sequence[Line], column_count: int) -> List[Line]:
    ordered: List[Line] = []
    groups: List[List[Line]] = [[] for _ in range(column_count)]
    for line in chunk:
        if line.column_index is None:
            continue
        groups[line.column_index].append(line)
    for group in groups:
        group.sort(key=lambda ln: ln.top)
        ordered.extend(group)
    return ordered


def _reorder_lines_for_columns(lines: Sequence[Line]) -> List[Line]:
    """Enhanced column reordering ensuring column-by-column reading."""
    columns = _detect_page_columns(lines)
    if not columns:
        for line in lines:
            line.column_index = None
        return list(lines)
    
    for line in lines:
        line.column_index = _assign_column(line, columns)
    
    column_groups: List[List[Line]] = [[] for _ in range(len(columns))]
    full_width_lines: List[Line] = []
    
    for line in lines:
        if line.column_index is None:
            full_width_lines.append(line)
        else:
            column_groups[line.column_index].append(line)
    
    for group in column_groups:
        group.sort(key=lambda ln: ln.top)
    
    reordered: List[Line] = []
    for col_idx, group in enumerate(column_groups):
        if group:
            logger.debug(f"  Column {col_idx}: {len(group)} lines")
            reordered.extend(group)
    
    if full_width_lines:
        if full_width_lines[0].top < 150:
            reordered = full_width_lines + reordered
        else:
            reordered.extend(full_width_lines)
    
    if len(columns) > 1:
        logger.info(f"✅ Reordered {len(lines)} lines into {len(columns)} columns (column-by-column)")
    return reordered


def _is_front_matter_heading(text: str, page_num: Optional[int]) -> bool:
    """Detect if this is front matter (not a chapter)."""
    text_lower = text.lower().strip()
    chapter_pattern = r'\bchapter\s+\d+\b'
    if re.search(chapter_pattern, text_lower):
        return False
    if page_num is not None and page_num < 30:
        front_matter_keywords = [
            'index', 'table of contents', 'contents in brief', 'detailed contents',
            'contributors', 'reviewers', 'acknowledgments', 'acknowledgements',
            'preface', 'foreword', 'introduction', 'about the author',
            'about this book', 'how to use', 'key to', 'nursing diagnoses',
            'essential terminology', 'contents on', 'davis plus',
        ]
        if any(keyword in text_lower for keyword in front_matter_keywords):
            logger.debug(f"✅ Identified as front matter: {text[:50]}")
            return True
        if len(text) < 200:
            logger.debug(f"✅ Early page heading = front matter: {text[:50]}")
            return True
    return False

def _line_gap(prev_line: Line, next_line: Line) -> float:
    return next_line.top - prev_line.top


# OLD _is_header_footer function removed - replaced with _is_header_footer_enhanced above


def _is_header_footer(line: Line) -> bool:
    """
    Basic header/footer detection for internal helper functions.
    For main filtering, use _is_header_footer_enhanced instead.
    """
    text = line.text.strip()
    if not text:
        return True
    
    # Simple page numbers at top/bottom
    if len(text) <= 4 and text.isdigit():
        if line.page_height and (
            line.top < line.page_height * 0.08 or line.top > line.page_height * 0.9
        ):
            return True
    
    # Very short text at edges
    if len(text) <= 10:
        if line.page_height and (
            line.top < line.page_height * 0.08 or line.top > line.page_height * 0.9
        ):
            return True
    
    return False

def _body_font_size(lines: Sequence[Line]) -> float:
    if not lines:
        return 12.0
    samples = [line.font_size for line in lines if len(line.text.strip()) >= 30 and line.font_size]
    if not samples:
        samples = [line.font_size for line in lines if line.font_size]
    if not samples:
        return 12.0
    return float(median(samples))


CHAPTER_KEYWORD_RE = re.compile(r"\bchapter\b", re.IGNORECASE)
CHAPTER_RE = re.compile(r"^(chapter|chap\.|unit|lesson|module)\b", re.IGNORECASE)
# PART patterns - for book parts which are higher in hierarchy than chapters
# Matches: "Part I", "Part 1", "Part One", "PART I:", "Part I: Introduction", etc.
PART_KEYWORD_RE = re.compile(r"\bpart\b", re.IGNORECASE)
PART_RE = re.compile(
    r"^part\s+(?:"
    r"[IVXLCDM]+|"           # Roman numerals: Part I, Part II, Part IV, etc.
    r"\d+|"                   # Arabic numbers: Part 1, Part 2, etc.
    r"one|two|three|four|five|six|seven|eight|nine|ten|"  # Written numbers
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty"
    r")\b",
    re.IGNORECASE
)
TOC_RE = re.compile(r"^table of contents$", re.IGNORECASE)
INDEX_RE = re.compile(r"^index\b", re.IGNORECASE)
GLOSSARY_RE = re.compile(r"^glossary\b", re.IGNORECASE)
SECTION_RE = re.compile(r"^(section|sec\.)\b", re.IGNORECASE)
CAPTION_RE = re.compile(r"^(figure|fig\.|table)\s+\d+", re.IGNORECASE)
# More restrictive ordered list pattern:
# - Digits with period/paren: 1. 2) (3. etc.
# - Letters with period/paren BUT exclude I/i (Roman numerals for sections)
# - Requires at least 2 word chars after the marker to avoid abbreviations
ORDERED_LIST_RE = re.compile(r"^(?:\(?\d{1,3}[\.\)]|[A-HJ-Za-hj-z][\.\)])\s+(?=\w{2,})")

HEADING_FONT_TOLERANCE = 1.0


def _get_bookmark_page_number(bookmark, reader) -> Optional[int]:
    """
    Try multiple methods to extract page number from a bookmark.
    
    Different PDFs and PyPDF2 versions store bookmark destinations differently.
    This function tries all known methods to maximize compatibility.
    
    Args:
        bookmark: Bookmark object from PyPDF2
        reader: PdfReader instance
    
    Returns:
        Page number (0-indexed) or None if not found
    """
    # Method 1: Try the .page attribute (most common)
    try:
        if hasattr(bookmark, 'page') and bookmark.page is not None:
            return reader.pages.index(bookmark.page)
    except Exception:
        pass
    
    # Method 2: Try accessing as dictionary with '/Page' key
    try:
        if isinstance(bookmark, dict) and '/Page' in bookmark:
            page_obj = bookmark['/Page']
            return reader.pages.index(page_obj)
    except Exception:
        pass
    
    # Method 3: Try get_destination() method (older PyPDF2 versions)
    try:
        if hasattr(bookmark, 'get_destination'):
            dest = bookmark.get_destination()
            if dest and hasattr(dest, 'page'):
                return reader.pages.index(dest.page)
    except Exception:
        pass
    
    # Method 4: Try dictionary-style access for destination
    try:
        if hasattr(bookmark, '__getitem__'):
            dest = bookmark['/Dest']
            if dest:
                # Destination can be an array [page, /XYZ, left, top, zoom]
                if isinstance(dest, list) and len(dest) > 0:
                    page_ref = dest[0]
                    return reader.pages.index(page_ref)
    except Exception:
        pass
    
    # Method 5: Try named destinations
    try:
        if hasattr(reader, 'named_destinations'):
            # Some bookmarks reference named destinations
            if hasattr(bookmark, 'title'):
                for name, dest in reader.named_destinations.items():
                    if hasattr(dest, 'page'):
                        # This is a guess - named destinations don't always match titles
                        return reader.pages.index(dest.page)
    except Exception:
        pass
    
    return None

def _extract_outline_from_xml(xml_tree: etree._ElementTree) -> Optional[List[dict]]:
    """
    Extract chapter information from pdftohtml <outline> element.

    This is the HIGHEST PRIORITY method for chapter detection.
    pdftohtml can embed PDF outline/bookmarks directly in the XML output.

    Args:
        xml_tree: Parsed XML tree from pdftohtml

    Returns:
        List of chapter dictionaries with 'title', 'start_page'
        Returns None if no outline element is found
    """
    root = xml_tree.getroot()

    # Look for <outline> element at the top level or within <pdf2xml>
    outline_el = root.find('.//outline')

    if outline_el is None:
        logger.info("📖 No <outline> element found in pdftohtml XML")
        return None

    logger.info("📖 Found <outline> element in pdftohtml XML - extracting ALL items")

    # Extract ALL <item> elements as chapter breakpoints
    # User requirement: Treat ALL outline items as chapters, not just "Chapter X:" items
    chapters = []

    # COMMENTED OUT: Old filtering that only kept "Chapter X:" items
    # chapter_pattern = re.compile(r'^Chapter\s+\d+', re.IGNORECASE)

    # Get all top-level <item> elements (direct children of <outline>)
    for item in outline_el.findall('item'):
        page_str = item.get('page')
        title = item.text or ''
        title = title.strip()

        # Skip empty items
        if not title or not page_str:
            continue

        # REMOVED FILTER: Now accepting ALL outline items, not just "Chapter X:"
        # if chapter_pattern.match(title):
        try:
            # pdftohtml page numbers are 1-indexed, convert to 0-indexed for internal use
            page_num = int(page_str) - 1
            chapters.append({
                'title': title,
                'start_page': page_num,  # 0-indexed
            })
            logger.debug(f"   Found outline item: '{title}' at page {page_num + 1}")
        except ValueError:
            logger.warning(f"Could not parse page number for outline item: '{title}'")
            continue

    if not chapters:
        logger.info("   No items found in <outline> - will try other methods")
        return None

    # Calculate end_page for each chapter
    # Each chapter ends where the next one starts (minus 1)
    # Need to know total pages - try to get from XML
    pages = root.findall('.//page')
    total_pages = len(pages) if pages else 1000  # fallback to large number

    for i in range(len(chapters)):
        if i < len(chapters) - 1:
            # Not the last chapter - ends where next one starts
            chapters[i]['end_page'] = chapters[i + 1]['start_page'] - 1
        else:
            # Last chapter - ends at the end of the document
            chapters[i]['end_page'] = total_pages - 1

    logger.info(f"✅ Extracted {len(chapters)} items from XML <outline>")
    logger.info(f"   Chapter page ranges:")
    for ch in chapters:
        logger.info(f"      '{ch['title']}': pages {ch['start_page']+1}-{ch['end_page']+1}")

    return chapters


"""
def _extract_bookmark_page_ranges(pdf_path: str) -> Optional[List[dict]]:
    # Extract level 0 (top-level) bookmarks from PDF with their page ranges.
    # This is the SECONDARY method for chapter detection - only used if
    # XML outline is not available. Falls back to heuristics if bookmarks are not available.
    # Args: pdf_path - Path to the PDF file
    # Returns: List of bookmark dictionaries with 'title', 'start_page', 'end_page'
    #          Returns None if bookmarks cannot be extracted
    
    if PdfReader is None:
        logger.info("📚 PyPDF2/pypdf not available - skipping bookmark extraction")
        return None
    
    try:
        reader = PdfReader(pdf_path)
        outlines = reader.outline
        total_pages = len(reader.pages)
        
        if not outlines:
            logger.info("📚 No bookmarks found in PDF - will use heuristic detection")
            return None
        
        # Collect level 0 (top-level) bookmarks only
        level_0_bookmarks = []
        
        for item in outlines:
            # Level 0 bookmarks are NOT nested in lists
            # If item is a list, it contains nested sub-bookmarks - skip it
            if isinstance(item, list):
                continue
            
            # This is a level 0 bookmark
            if not hasattr(item, 'title'):
                continue
                
            title = item.title
            
            # Get the starting page number using robust method
            start_page = _get_bookmark_page_number(item, reader)
            
            if start_page is not None:
                level_0_bookmarks.append({
                    'title': title,
                    'start_page': start_page,  # 0-indexed
                })
            else:
                logger.warning(f"Could not extract page for bookmark '{title}': no valid page reference found")
                continue
        
        if not level_0_bookmarks:
            logger.info("📚 No valid level 0 bookmarks found - will use heuristic detection")
            return None
        
        # ═══════════════════════════════════════════════════════════════
        # KEEP ALL BOOKMARKS - No filtering
        # User requirement: Treat ALL outline items as chapters
        # ═══════════════════════════════════════════════════════════════
        logger.info(f"📚 Found {len(level_0_bookmarks)} level 0 bookmarks total")
        logger.info(f"✅ Will use ALL {len(level_0_bookmarks)} bookmarks as chapter breakpoints")

        # # COMMENTED OUT: Old filtering logic that only kept "Chapter X:" items
        # # This was causing only 18 chapters to be generated instead of all 26
        # chapter_bookmarks = []
        # non_chapter_bookmarks = []
        #
        # for bm in level_0_bookmarks:
        #     title = bm['title'].strip()
        #     # Check if this looks like a chapter heading
        #     if re.match(r'^Chapter\s+\d+', title, re.IGNORECASE):
        #         chapter_bookmarks.append(bm)
        #     else:
        #         non_chapter_bookmarks.append(title)
        #
        # if not chapter_bookmarks:
        #     logger.warning("⚠️  No chapter bookmarks found after filtering")
        #     logger.warning("   (Looking for bookmarks starting with 'Chapter N:')")
        #     logger.warning("   Falling back to heuristic chapter detection")
        #     return None
        #
        # logger.info(f"✅ Filtered to {len(chapter_bookmarks)} CHAPTER bookmarks only")
        # logger.info(f"   (Ignored {len(non_chapter_bookmarks)} non-chapter bookmarks)")
        #
        # # Show a few examples of what was filtered out
        # if non_chapter_bookmarks:
        #     logger.info("   📝 Examples of ignored bookmarks:")
        #     for title in non_chapter_bookmarks[:5]:
        #         logger.info(f"      • {title}")
        #     if len(non_chapter_bookmarks) > 5:
        #         logger.info(f"      ... and {len(non_chapter_bookmarks) - 5} more")
        #
        # # Use the filtered chapter bookmarks from here on
        # level_0_bookmarks = chapter_bookmarks
        
        # Calculate ending pages
        # Each bookmark ends where the next one starts (minus 1)
        for i in range(len(level_0_bookmarks)):
            if i < len(level_0_bookmarks) - 1:
                # Not the last bookmark - ends where next one starts
                level_0_bookmarks[i]['end_page'] = level_0_bookmarks[i + 1]['start_page'] - 1
            else:
                # Last bookmark - ends at the end of the document
                level_0_bookmarks[i]['end_page'] = total_pages - 1
        
        logger.info(f"✅ Successfully extracted {len(level_0_bookmarks)} bookmarks from PDF")
        for bm in level_0_bookmarks:
            logger.info(f"   📖 '{bm['title']}': pages {bm['start_page']+1}-{bm['end_page']+1}")
        
        return level_0_bookmarks
    
    except Exception as e:
        logger.warning(f"❌ Error extracting bookmarks from PDF: {e}")
        logger.info("📚 Will use heuristic detection instead")
        return None
"""

def _create_blocks_from_bookmarks(
    bookmark_ranges: List[dict],
    pdfxml_path: str,
    config: dict
) -> Optional[List[dict]]:
    """
    Create chapter blocks based on PDF bookmarks.
    
    This converts bookmark page ranges into the block structure expected
    by the rest of the pipeline.
    
    Args:
        bookmark_ranges: List of bookmarks with start_page and end_page
        pdfxml_path: Path to the PDF XML file (to get page dimensions)
        config: Configuration dict
    
    Returns:
        List of blocks with chapter labels, or None if conversion fails
    """
    try:
        tree = etree.parse(pdfxml_path)
        root = tree.getroot()
        
        # Build a mapping of page numbers to page elements (for dimensions)
        pages = {}
        for page in root.findall(".//page"):
            page_num = int(page.get("number", "0"))
            pages[page_num] = page
        
        blocks = []
        
        for bm in bookmark_ranges:
            title = bm['title']
            start_page = bm['start_page']
            end_page = bm['end_page']
            
            # Get page dimensions from the first page of this chapter
            page_elem = pages.get(start_page)
            if page_elem is not None:
                page_width = float(page_elem.get("width", "612") or "612")
                page_height = float(page_elem.get("height", "792") or "792")
            else:
                page_width = 612.0
                page_height = 792.0
            
            # Create a chapter block
            # Note: We use page_num as the start page where this chapter begins
            block = {
                "label": "chapter",
                "text": title,
                "page_num": start_page,  # 0-indexed page number
                "bbox": {
                    "top": 0.0,
                    "left": 0.0,
                    "width": page_width,
                    "height": 72.0,  # Approximate heading height
                },
                "font_size": 18.0,  # Default chapter heading size
                "bookmark_based": True,  # Flag to indicate this came from bookmarks
                "end_page": end_page,  # Store the ending page for reference
            }
            blocks.append(block)
        
        logger.info(f"✅ Created {len(blocks)} chapter blocks from bookmarks")
        return blocks
    
    except Exception as e:
        logger.error(f"❌ Error creating blocks from bookmarks: {e}")
        return None


def _extract_title_from_chapter_content(
    blocks: List[dict],
    start_page: int,
    end_page: int,
    bookmark_title: str
) -> str:
    """
    Extract the chapter title by finding the text with the biggest font size
    in the chapter's page range, merging consecutive blocks with similar font sizes.

    Strategy:
    - Look for text blocks on the first few pages of the chapter (usually page 1-2)
    - Find the block with the largest font size
    - Collect consecutive blocks with the same/similar font size (multiline titles)
    - Merge them into one title string
    - Fallback to bookmark title if no suitable text is found

    Args:
        blocks: All content blocks
        start_page: Starting page number of the chapter (0-indexed)
        end_page: Ending page number of the chapter (0-indexed)
        bookmark_title: Fallback title from bookmark/outline

    Returns:
        The extracted title text (may be multiline/merged)
    """
    # Look at the first 2 pages of the chapter for the title
    search_pages = range(start_page, min(start_page + 2, end_page + 1))

    # Find all text blocks in the chapter's first pages
    candidate_blocks = []
    for block in blocks:
        page_num = block.get("page_num", -1)
        if page_num in search_pages:
            # Only consider blocks with text and font size
            text = block.get("text", "").strip()
            font_size = block.get("font_size")
            if text and font_size:
                candidate_blocks.append(block)

    if not candidate_blocks:
        logger.debug(f"   No text blocks found in chapter starting at page {start_page}, using bookmark title")
        return bookmark_title

    # Find the block with the biggest font size
    biggest_block = max(candidate_blocks, key=lambda b: b.get("font_size", 0))
    biggest_font_size = biggest_block.get("font_size", 0)
    biggest_block_idx = candidate_blocks.index(biggest_block)
    
    # Collect consecutive blocks with similar font size (within 1pt tolerance)
    # This handles multiline chapter titles that span multiple blocks
    title_blocks = [biggest_block]
    font_tolerance = 1.0  # Allow 1pt difference for multiline titles
    
    # Look forward for consecutive blocks with similar font size
    for i in range(biggest_block_idx + 1, len(candidate_blocks)):
        next_block = candidate_blocks[i]
        next_font_size = next_block.get("font_size", 0)
        
        # Check if font size is similar
        if abs(next_font_size - biggest_font_size) <= font_tolerance:
            # Check if blocks are vertically close (same paragraph/title)
            prev_block = title_blocks[-1]
            prev_bottom = prev_block.get("bbox", {}).get("top", 0) + prev_block.get("bbox", {}).get("height", 0)
            next_top = next_block.get("bbox", {}).get("top", 0)
            vertical_gap = next_top - prev_bottom
            
            # If vertical gap is reasonable (less than 2x font size), consider it part of the title
            if vertical_gap < biggest_font_size * 2:
                title_blocks.append(next_block)
            else:
                break  # Too much gap, title has ended
        else:
            break  # Font size changed, title has ended
    
    # Look backward for consecutive blocks with similar font size
    for i in range(biggest_block_idx - 1, -1, -1):
        prev_block = candidate_blocks[i]
        prev_font_size = prev_block.get("font_size", 0)
        
        # Check if font size is similar
        if abs(prev_font_size - biggest_font_size) <= font_tolerance:
            # Check if blocks are vertically close
            prev_bottom = prev_block.get("bbox", {}).get("top", 0) + prev_block.get("bbox", {}).get("height", 0)
            next_top = title_blocks[0].get("bbox", {}).get("top", 0)
            vertical_gap = next_top - prev_bottom
            
            # If vertical gap is reasonable, consider it part of the title
            if vertical_gap < biggest_font_size * 2:
                title_blocks.insert(0, prev_block)
            else:
                break
        else:
            break
    
    # Merge all title blocks into one string
    title_parts = [block.get("text", "").strip() for block in title_blocks]
    title_text = " ".join(title_parts)

    if len(title_blocks) > 1:
        logger.debug(f"   Page {start_page}: Merged {len(title_blocks)} blocks into title with font size {biggest_font_size:.1f}pt")
    logger.debug(f"   Page {start_page}: Found title: '{title_text[:60]}...' " if len(title_text) > 60 else f"   Page {start_page}: Found title: '{title_text}'")

    return title_text if title_text else bookmark_title


def _inject_bookmark_chapters(blocks: List[dict], bookmark_ranges: List[dict]) -> List[dict]:
    """
    Inject bookmark-based chapter headings into the blocks at appropriate positions.

    This function:
    1. Removes heuristically-detected chapter headings (to avoid duplicates)
    2. Determines actual chapter titles by finding the biggest font in each chapter
    3. Inserts chapter headings at the correct page numbers
    4. Preserves all other content blocks (paragraphs, figures, etc.)

    Args:
        blocks: List of all content blocks (from heuristic extraction)
        bookmark_ranges: List of bookmark dictionaries with title, start_page, end_page

    Returns:
        Updated list of blocks with bookmark-based chapter headings
    """
    logger.info(f"🔄 Injecting {len(bookmark_ranges)} bookmark-based chapters into {len(blocks)} blocks")

    # Step 1: Remove heuristically-detected chapter headings
    # Keep everything else (paragraphs, sections, figures, etc.)
    filtered_blocks = []
    removed_chapters = 0

    for block in blocks:
        if block.get("label") == "chapter":
            # Remove heuristic chapter headings - we'll replace with bookmark-based ones
            removed_chapters += 1
            logger.debug(f"   Removing heuristic chapter: '{block.get('text', '')[:50]}'")
            continue
        filtered_blocks.append(block)

    if removed_chapters > 0:
        logger.info(f"   📝 Removed {removed_chapters} heuristically-detected chapter headings")

    # Step 2: Create chapter heading blocks from bookmarks
    # IMPORTANT: Determine title from biggest font in chapter, not from bookmark text
    bookmark_chapter_blocks = []

    for bm in bookmark_ranges:
        bookmark_title = bm['title']
        start_page = bm['start_page']
        end_page = bm['end_page']

        # Extract the actual title from the chapter content based on font size
        actual_title = _extract_title_from_chapter_content(
            filtered_blocks, start_page, end_page, bookmark_title
        )

        logger.info(f"   📖 Chapter at page {start_page+1}:")
        logger.info(f"      Bookmark: '{bookmark_title}'")
        logger.info(f"      Actual title: '{actual_title[:60]}...' " if len(actual_title) > 60 else f"      Actual title: '{actual_title}'")

        # Create a chapter heading block
        chapter_block = {
            "label": "chapter",
            "text": actual_title,  # Use actual title from content, not bookmark
            "page_num": start_page,  # 0-indexed
            "bbox": {
                "top": 0.0,
                "left": 0.0,
                "width": 612.0,
                "height": 72.0,
            },
            "font_size": 18.0,  # Default chapter heading size
            "bookmark_based": True,  # Flag to indicate source
            "bookmark_title": bookmark_title,  # Keep original bookmark for reference
            "end_page": end_page,  # Store ending page
        }
        bookmark_chapter_blocks.append(chapter_block)
    
    # Step 3: Merge blocks - insert chapter headings at correct positions
    # NOTE: This function is currently unused. Blocks already come in correct document order
    # from unified.xml (sorted by reading_order_block, col_id, baseline).
    # Sort all blocks by page number first
    all_blocks_to_sort = filtered_blocks + bookmark_chapter_blocks

    # Sort by page number, then by vertical position (top)
    # Blocks are already in correct reading order from unified.xml, so this just ensures
    # bookmark chapter headings are inserted at the right page positions.
    sorted_blocks = sorted(
        all_blocks_to_sort,
        key=lambda b: (b.get("page_num", 0), b.get("bbox", {}).get("top", 0.0))
    )
    
    logger.info(f"   ✅ Merged {len(filtered_blocks)} content blocks with {len(bookmark_chapter_blocks)} bookmark chapters")
    logger.info(f"   📖 Result: {len(sorted_blocks)} total blocks")
    
    return sorted_blocks


def _looks_like_book_title(line: Line, body_size: float) -> bool:
    # text = line.text.strip()
    text = (getattr(line, "text", "") or "").strip()
    # Safely get page_num; if absent, treat as large (so it won't match)
    page_num = getattr(line, "page_num", None)
    if not text or (page_num is not None and page_num > 2):
        return False
    if line.top > line.page_height * 0.45 if line.page_height else line.top > 400:
        return False
    if line.font_size is not None and line.font_size >= body_size + 6:
        return True
    if line.font_size is not None and line.font_size >= body_size + 4 and len(text.split()) <= 12:
        return True
    return False


def _collect_multiline_book_title(
    entries: Sequence[dict], start_idx: int, body_size: float
) -> tuple[list[Line], int]:
    """Collect consecutive lines that belong to the book title block."""

    first_entry = entries[start_idx]
    first_line = _line_of(first_entry)
    heading_lines = [first_line]
    lookahead_idx = start_idx + 1

    while lookahead_idx < len(entries):
        next_entry = entries[lookahead_idx]
        if next_entry.get("kind") not in ("line", "text"):
            break
        next_line = _line_of(next_entry)
        if _is_header_footer(next_line):
            break
        text = next_line.text.strip()
        if not text:
            break
        if text.lower() == "table of contents":
            break

        same_page = (
            next_line.page_num is not None and 
            first_line.page_num is not None and 
            next_line.page_num == first_line.page_num
        )
        similar_font = False
        if first_line.font_size and next_line.font_size:
            similar_font = (
                abs(next_line.font_size - first_line.font_size) <= HEADING_FONT_TOLERANCE
            )

        if same_page and (similar_font or _looks_like_book_title(next_line, body_size)):
            heading_lines.append(next_line)
            lookahead_idx += 1
            continue

        break

    return heading_lines, lookahead_idx
def _has_heading_font(line: Line, body_size: float) -> bool:
    if not line.font_size:
        return False
    return line.font_size >= body_size + 2.0


def _has_part_heading_font(line: Line, body_size: float) -> bool:
    """Parts typically have larger fonts than chapters - at least 4 points above body."""
    if not line.font_size:
        return False
    return line.font_size >= body_size + 4.0


def _looks_like_part_heading(line: Line, body_size: float) -> bool:
    """
    Detect if a line looks like a PART heading.

    Parts are higher-level divisions than chapters, typically:
    - Start with "Part" followed by a number (Roman, Arabic, or written)
    - Have larger font than chapter headings
    - Are positioned near the top of the page
    - Are usually short (just "Part I" or "Part I: Title")
    """
    text = line.text.strip()
    if not text:
        return False

    # Must match the PART pattern (Part I, Part 1, Part One, etc.)
    if not PART_RE.match(text):
        return False

    # Must have a heading font (larger than body)
    if not _has_heading_font(line, body_size):
        return False

    # Parts are typically very short - just the part identifier or with a brief title
    # "Part I" or "Part I: Introduction" - usually <= 8 words
    if len(text.split()) > 8:
        return False

    # Prefer parts that are near the top of the page
    if line.page_height and line.top <= line.page_height * 0.40:
        return True

    # If it explicitly matches PART pattern with heading font, accept it
    return True


def _looks_like_chapter_heading(line: Line, body_size: float) -> bool:
    text = line.text.strip()
    if not text:
        return False
    if CHAPTER_KEYWORD_RE.search(text):
        return _has_heading_font(line, body_size)
    if CHAPTER_RE.match(text):
        return _has_heading_font(line, body_size)
    if not _has_heading_font(line, body_size):
        return False
    if line.page_height and line.top <= line.page_height * 0.45:
        return True
    if len(text.split()) <= 10:
        return True
    return False


def _collect_multiline_heading(
    entries: Sequence[dict], start_idx: int, body_size: float
) -> tuple[list[Line], int]:
    first_entry = entries[start_idx]
    first_line = _line_of(first_entry)
    base_font = first_line.font_size
    heading_lines = [first_line]
    lookahead_idx = start_idx + 1

    while lookahead_idx < len(entries):
        next_entry = entries[lookahead_idx]
        if next_entry.get("kind") not in ("line", "text"):
            break
        next_line = _line_of(next_entry)
        if _is_header_footer(next_line):
            break
        text = next_line.text.strip()
        if not text:
            break
        if base_font and next_line.font_size:
            if abs(next_line.font_size - base_font) <= HEADING_FONT_TOLERANCE:
                heading_lines.append(next_line)
                lookahead_idx += 1
                continue
        break

    return heading_lines, lookahead_idx


def _is_index_heading(line: Line, body_size: float) -> bool:
    text = line.text.strip()
    if not text:
        return False
    if not INDEX_RE.match(text):
        return False
    return _has_heading_font(line, body_size)


def _is_glossary_heading(line: Line, body_size: float) -> bool:
    """Detect if this line is a Glossary heading."""
    text = line.text.strip()
    if not text:
        return False
    if not GLOSSARY_RE.match(text):
        return False
    return _has_heading_font(line, body_size)


def _looks_like_section_heading(line: Line, body_size: float) -> bool:
    text = line.text.strip()
    if not text:
        return False
    if SECTION_RE.match(text):
        return True
    if line.font_size is not None and line.font_size >= body_size + 1.5 and len(text.split()) <= 14:
        return True
    if len(text.split()) <= 8 and text.isupper() and line.font_size is not None and line.font_size >= body_size:
        return True
    return False


def _looks_like_caption(line: Line) -> bool:
    text = line.text.strip()
    if not text:
        return False
    if CAPTION_RE.match(text):
        return True
    return False


def _is_list_item(text: str, mapping: dict) -> tuple[bool, str, str]:
    """
    Check if text looks like a list item.
    Returns: (is_list, list_type, cleaned_text)
    
    More conservative than before - excludes common false positives.
    """
    stripped = text.lstrip()
    
    # Skip if text is very short (likely not a real list item)
    if len(stripped) < 3:
        return False, "", text
    
    pdf_cfg = mapping.get("pdf", {})
    markers = pdf_cfg.get("list_markers", [])
    
    # Check bullet markers (but not plain hyphen at start unless specified)
    for marker in markers:
        if marker == "-":
            # For plain hyphen, be more careful
            # Don't match if it looks like a range or compound word
            if stripped.startswith("- "):
                # Check it's not a number range like "- 50" 
                remainder = stripped[2:].strip()
                if remainder and not remainder[0].isdigit():
                    return True, "itemized", remainder or text.strip()
        elif stripped.startswith(marker):
            remainder = stripped[len(marker):].strip()
            # Ensure there's actual content after marker
            if remainder:
                return True, "itemized", remainder
    
    # Check ordered list patterns
    if ORDERED_LIST_RE.match(stripped):
        # Extra validation: avoid common abbreviations
        # Check if this looks like "A. Smith" or "Dr. Jones"
        match = ORDERED_LIST_RE.match(stripped)
        if match:
            marker_text = match.group(0).strip()
            remainder = ORDERED_LIST_RE.sub("", stripped, count=1).strip()
            
            # If marker is a single letter and next word is capitalized,
            # might be a name (e.g., "A. Smith")
            if len(marker_text) == 2 and marker_text[0].isupper():
                words = remainder.split()
                if words and words[0] and words[0][0].isupper() and len(words[0]) > 2:
                    # Likely a name like "A. Smith", not a list
                    return False, "", text
            
            if remainder:
                return True, "ordered", remainder
    
    return False, "", text


def _detect_list_sequence(entries: Sequence, start_idx: int, mapping: dict) -> tuple[bool, str, int]:
    """
    Look ahead to confirm this is actually a list by checking for consecutive items.
    
    Args:
        entries: Sequence of entry dictionaries (will be converted to Line objects via _line_of)
        start_idx: Starting index in entries
        mapping: Font mapping dictionary
    
    Returns: (is_list, list_type, num_items)
    """
    if start_idx >= len(entries):
        return False, "", 0
    
    # Skip non-text/line entries
    if entries[start_idx].get("kind") not in ("text", "line"):
        return False, "", 0
    
    first_line = _line_of(entries[start_idx])
    matched, list_type, _ = _is_list_item(first_line.text, mapping)
    
    if not matched:
        return False, "", 0
    
    # Check indentation of first item
    first_indent = first_line.left if first_line.left is not None else 0
    
    # Look ahead for more items with similar pattern
    consecutive_items = 1
    indent_tolerance = 15  # points
    max_lookahead = 10
    
    for i in range(start_idx + 1, min(start_idx + max_lookahead, len(entries))):
        # Skip non-text/line entries
        if entries[i].get("kind") not in ("text", "line"):
            break
            
        line = _line_of(entries[i])
        
        # Check if same page
        if line.page_num != first_line.page_num:
            break
        
        # Check vertical gap
        prev_entry = entries[i - 1]
        if prev_entry.get("kind") not in ("text", "line"):
            break
        prev_line = _line_of(prev_entry)
        gap = _line_gap(prev_line, line)
        if prev_line.height and line.height:
            max_gap = max(prev_line.height, line.height) * 2.5
            if gap > max_gap:
                break
        
        # Check indentation similarity
        line_indent = line.left if line.left is not None else 0
        if abs(line_indent - first_indent) > indent_tolerance:
            break
        
        # Check if this line is also a list item of the same type
        is_item, item_type, _ = _is_list_item(line.text, mapping)
        if is_item and item_type == list_type:
            consecutive_items += 1
        else:
            break
    
    # Require at least 2 consecutive items to confirm it's a list
    # (Unless it's a very clear bullet marker like •)
    min_items = 2
    if list_type == "itemized":
        first_text = first_line.text.lstrip()
        # If using a strong bullet marker, single item is ok
        if any(first_text.startswith(m) for m in ["•", "◦", "▪", "✓", "●"]):
            min_items = 1
    
    is_confirmed = consecutive_items >= min_items
    return is_confirmed, list_type, consecutive_items


def _should_merge(prev_line: Line, next_line: Line, body_size: float) -> bool:
    if prev_line.page_num != next_line.page_num:
        return False
    vertical_gap = _line_gap(prev_line, next_line)
    if prev_line.height is None or next_line.height is None:
        return False
    if vertical_gap > max(prev_line.height, next_line.height) * 1.9 + 2:
        return False
    if prev_line.left is None or next_line.left is None:
        return False
    indent_diff = abs(prev_line.left - next_line.left)
    if indent_diff > 60 and vertical_gap > min(prev_line.height, next_line.height) * 1.1:
        return False
    # Treat significant negative indent as new paragraph (hanging indent)
    if next_line.left - prev_line.left < -80:
        return False
    return True


INLINE_TOKEN_TEMPLATE = "__INLINE_TOKEN_{idx}__"
INLINE_TOKEN_RE = re.compile(r"__INLINE_TOKEN_(\d+)__")


def _get_phrase_format_key(elem: etree._Element) -> str:
    """
    Generate a key representing the formatting attributes of a phrase element.
    Phrases with the same key can be consolidated.
    """
    # Extract formatting attributes that matter for consolidation
    attrs = []
    for attr in ["font", "size", "color", "style"]:
        val = elem.get(attr)
        if val:
            attrs.append(f"{attr}={val}")
    return "|".join(sorted(attrs)) if attrs else ""


def _append_text_node_content(text_elem: etree._Element, parts: List[str], token_map: dict) -> None:
    """
    Append text node content while capturing inline XML via tokens.

    All inline elements (phrase, superscript, subscript) are tokenized to preserve
    their formatting. Post-processing will consolidate adjacent phrases with
    identical formatting and handle hyphen removal across boundaries.
    """
    if text_elem.text:
        parts.append(text_elem.text)
    for child in text_elem:
        token = INLINE_TOKEN_TEMPLATE.format(idx=len(token_map))
        parts.append(token)
        token_map[token] = etree.tostring(child, encoding="unicode")
        if child.tail:
            parts.append(child.tail)
    if text_elem.tail:
        parts.append(text_elem.tail)


def _consolidate_phrase_segments(segments: List[dict]) -> List[dict]:
    """
    Post-process segments to:
    1. Consolidate adjacent <phrase> elements with identical formatting
    2. Handle hyphen removal across phrase boundaries

    This preserves formatting while creating cleaner, more readable output.
    """
    if not segments:
        return segments

    result: List[dict] = []

    for seg in segments:
        if seg["type"] != "xml":
            # Plain text segment - just append
            if result and result[-1]["type"] == "text":
                # Merge adjacent text segments
                result[-1]["value"] += seg["value"]
            else:
                result.append({"type": "text", "value": seg["value"]})
            continue

        # Parse the XML segment
        try:
            node = etree.fromstring(seg["value"])
        except Exception:
            result.append(seg)
            continue

        tag_name = etree.QName(node.tag).localname if isinstance(node.tag, str) else str(node.tag)

        # Only consolidate phrase elements
        if tag_name != "phrase":
            result.append(seg)
            continue

        current_text = "".join(node.itertext())
        current_format = _get_phrase_format_key(node)

        # Check if we can merge with previous segment
        if result and result[-1]["type"] == "xml":
            try:
                prev_node = etree.fromstring(result[-1]["value"])
                prev_tag = etree.QName(prev_node.tag).localname if isinstance(prev_node.tag, str) else str(prev_node.tag)

                if prev_tag == "phrase":
                    prev_text = "".join(prev_node.itertext())
                    prev_format = _get_phrase_format_key(prev_node)

                    # Check for hyphen word break: "word-" + "continuation"
                    # In this case, merge regardless of formatting (use prev formatting)
                    if prev_text.rstrip().endswith("-") and current_text and current_text[0].islower():
                        # Remove the hyphen and join
                        merged_text = prev_text.rstrip()[:-1] + current_text
                        prev_node.text = merged_text
                        # Clear any child elements and set text directly
                        for child in list(prev_node):
                            prev_node.remove(child)
                        prev_node.text = merged_text
                        result[-1]["value"] = etree.tostring(prev_node, encoding="unicode")
                        continue

                    # Same formatting - consolidate
                    if prev_format == current_format:
                        merged_text = prev_text + current_text
                        prev_node.text = merged_text
                        for child in list(prev_node):
                            prev_node.remove(child)
                        prev_node.text = merged_text
                        result[-1]["value"] = etree.tostring(prev_node, encoding="unicode")
                        continue
            except Exception:
                pass

        # Check if previous is plain text ending with hyphen (word break before phrase)
        if result and result[-1]["type"] == "text":
            prev_text = result[-1]["value"]
            if prev_text.rstrip().endswith("-") and current_text and current_text[0].islower():
                # Pattern: "some text word-" + phrase("continuation")
                # Result: "some text " + phrase("wordcontinuation")
                text_before_hyphen = prev_text.rstrip()[:-1]  # Remove trailing hyphen
                words = text_before_hyphen.split()
                if words:
                    # Get the partial word before the hyphen
                    partial_word = words[-1]
                    text_prefix = " ".join(words[:-1])
                    # Update the plain text segment to exclude the partial word
                    if text_prefix:
                        result[-1]["value"] = text_prefix + " "
                    else:
                        result.pop()  # Remove empty/partial text segment
                    # Merge partial word with phrase content
                    merged_word = partial_word + current_text
                    for child in list(node):
                        node.remove(child)
                    node.text = merged_word
                    result.append({"type": "xml", "value": etree.tostring(node, encoding="unicode")})
                    continue

        # No consolidation possible - add as new segment
        result.append(seg)

    return result


def _segments_from_clean_text(clean_text: str, token_map: dict) -> List[dict]:
    """Split cleaned text back into text/xml segments based on tokens."""
    if not token_map:
        return [{"type": "text", "value": clean_text}]

    segments: List[dict] = []
    idx = 0
    while idx < len(clean_text):
        match = INLINE_TOKEN_RE.search(clean_text, idx)
        if not match:
            tail = clean_text[idx:]
            if tail:
                segments.append({"type": "text", "value": tail})
            break
        if match.start() > idx:
            chunk = clean_text[idx:match.start()]
            if chunk:
                segments.append({"type": "text", "value": chunk})
        token = match.group(0)
        xml_payload = token_map.get(token)
        if xml_payload:
            segments.append({"type": "xml", "value": xml_payload})
        idx = match.end()
    return segments


def _segments_to_plain_text(segments: List[dict]) -> str:
    """Convert segments (text + inline XML) into plain text for heuristics."""
    parts: List[str] = []
    for seg in segments:
        if seg["type"] == "text":
            parts.append(seg["value"])
        else:
            try:
                node = etree.fromstring(seg["value"])
                parts.append("".join(node.itertext()))
            except Exception:
                # If parsing fails, drop inline markup but keep placeholder text
                parts.append("")
    return "".join(parts)


def _segment_plain_text(segment: dict) -> str:
    if segment["type"] == "text":
        return segment["value"]
    try:
        node = etree.fromstring(segment["value"])
        return "".join(node.itertext())
    except Exception:
        return ""


def _inject_xrefs_into_segments(segments: List[dict], figure_refs: List) -> Optional[List[dict]]:
    """Insert xref markers into rich segments; return None if insertion fails."""
    if not figure_refs:
        return segments

    refs = sorted(figure_refs, key=lambda r: getattr(r, "start_pos", 0))
    ref_idx = 0
    char_pos = 0
    result: List[dict] = []

    for seg in segments:
        seg_plain = _segment_plain_text(seg)
        seg_len = len(seg_plain)
        if seg_len == 0:
            result.append(seg)
            continue

        if seg["type"] != "text":
            # If a figure reference falls within an inline segment, abort so we can fall back.
            if ref_idx < len(refs) and getattr(refs[ref_idx], "start_pos", 0) < char_pos + seg_len:
                return None
            result.append(seg)
            char_pos += seg_len
            continue

        segment_text = seg["value"]
        local_cursor = 0

        while ref_idx < len(refs):
            ref = refs[ref_idx]
            ref_start = getattr(ref, "start_pos", 0)
            ref_end = getattr(ref, "end_pos", ref_start)

            if ref_start >= char_pos + seg_len:
                break
            if ref_end <= char_pos:
                ref_idx += 1
                continue

            local_start = max(ref_start - char_pos, 0)
            local_end = max(ref_end - char_pos, 0)

            if local_start < local_cursor:
                # Overlapping or malformed reference; bail out.
                return None

            prefix = segment_text[local_cursor:local_start]
            if prefix:
                result.append({"type": "text", "value": prefix})

            xref_seg = {
                "type": "xref",
                "value": getattr(ref, "full_text", ""),
                "target": getattr(ref, "target_image", None),
            }
            result.append(xref_seg)
            local_cursor = local_end
            ref_idx += 1

        remainder = segment_text[local_cursor:]
        if remainder:
            result.append({"type": "text", "value": remainder})

        char_pos += seg_len

    # Append any remaining references at the end as plain text if we couldn't place them
    while ref_idx < len(refs):
        ref = refs[ref_idx]
        xref_seg = {
            "type": "xref",
            "value": getattr(ref, "full_text", ""),
            "target": getattr(ref, "target_image", None),
        }
        result.append({"type": "text", "value": " "})
        result.append(xref_seg)
        ref_idx += 1

    return result


def _append_text_segment(parent: etree._Element, value: str) -> None:
    """Append plain text to parent while respecting existing children."""
    if not value:
        return
    children = parent.getchildren()
    if not children:
        if parent.text:
            parent.text += value
        else:
            parent.text = value
        return
    last_child = children[-1]
    if last_child.tail:
        last_child.tail += value
    else:
        last_child.tail = value


def _apply_rich_segments_to_para(para_el: etree._Element, segments: List[dict]) -> None:
    """Populate a DocBook para element using precomputed rich segments."""
    if not segments:
        return
    for seg in segments:
        if seg["type"] == "text":
            _append_text_segment(para_el, seg["value"])
        else:
            try:
                node = etree.fromstring(seg["value"])
                para_el.append(node)
            except Exception:
                # If parsing fails, skip the inline fragment
                continue


def _extract_para_rich_content(para_elem: etree._Element) -> tuple[str, List[dict]]:
    """
    Join all <text> children inside a unified <para>, preserving inline markup.

    The extraction process:
    1. Tokenize inline elements (phrase, superscript, subscript)
    2. Apply word split fixes to plain text portions
    3. Reconstruct segments from cleaned text
    4. Consolidate adjacent phrases with same formatting
    5. Handle hyphen removal across phrase boundaries

    Returns:
        plain_text: Text with inline markup stripped (for heuristics)
        segments: Ordered list of {"type": "text"|"xml", "value": ...}
    """
    text_nodes = para_elem.findall("text")
    if not text_nodes:
        return "", []

    token_map = {}
    parts: List[str] = []

    for idx, text_elem in enumerate(text_nodes):
        if idx > 0:
            parts.append(" ")
        _append_text_node_content(text_elem, parts, token_map)

    combined = "".join(parts)
    try:
        cleaned = fix_word_splits_enhanced(combined)
    except Exception:
        cleaned = combined

    segments = _segments_from_clean_text(cleaned, token_map)

    # Consolidate adjacent phrases with same formatting and handle cross-phrase hyphenation
    segments = _consolidate_phrase_segments(segments)

    plain_text = _segments_to_plain_text(segments).strip()
    return plain_text, segments


from typing import Optional, Sequence

def _finalize_paragraph(lines: Sequence, default_font_size: Optional[float] = None) -> Optional[dict]:
    """
    Build a paragraph block from a list of line objects.
    Robust to empty / degenerate lines. Returns None if nothing usable.
    """
    if not lines:
        return None

    has_unified_para = False
    unified_para_elem = None
    first_para_id = getattr(lines[0], "para_id", None)
    if first_para_id is not None and all(getattr(ln, "para_id", None) == first_para_id for ln in lines):
        unified_para_elem = getattr(lines[0], "para_elem", None)
        if unified_para_elem is not None:
            # Only use unified para extraction if we're processing ALL text elements in the para.
            # Otherwise we'd duplicate/concatenate content when processing individual TOC/Index entries
            # (where each text element in a multi-text para is processed separately).
            text_nodes_in_para = unified_para_elem.findall("text")
            if len(text_nodes_in_para) == len(lines):
                has_unified_para = True
            # else: skip unified extraction - we're only processing a subset of the para's text elements

    # keep lines that actually have visible text
    lines = [ln for ln in lines if getattr(ln, "text", "") and ln.text.strip()]
    if not lines:
        return None

    # aggregate font sizes safely
    sizes = [getattr(ln, "font_size", None) for ln in lines if getattr(ln, "font_size", None) is not None]
    para_font_size = max(sizes) if sizes else (default_font_size if default_font_size is not None else 0.0)

    rich_segments: List[dict] = []
    if has_unified_para and unified_para_elem is not None:
        text, rich_segments = _extract_para_rich_content(unified_para_elem)
    else:
        # join text (preserve line breaks minimally)
        text = " ".join(ln.text.strip() for ln in lines).strip()
        # if you have a better de-hyphenation, keep it; otherwise ignore failure
        try:
            text = fix_word_splits_enhanced(text)
        except Exception:
            pass

    # compute bbox if coords exist
    def _f(v):
        try:
            return float(v)
        except Exception:
            return float("nan")

    xs0 = [_f(getattr(ln, "left", None))   for ln in lines if getattr(ln, "left",   None) is not None]
    ys0 = [_f(getattr(ln, "top", None))    for ln in lines if getattr(ln, "top",    None) is not None]
    xs1 = [_f(getattr(ln, "right", None))  for ln in lines if getattr(ln, "right",  None) is not None]
    ys1 = [_f(getattr(ln, "bottom", None)) for ln in lines if getattr(ln, "bottom", None) is not None]

    bbox = None
    if xs0 and ys0 and xs1 and ys1:
        bbox = {
            "top":    min(ys0),
            "left":   min(xs0),
            "width":  max(xs1) - min(xs0),
            "height": max(ys1) - min(ys0),
        }

    block = {
        "label": "para",
        "text": text,
        "page_num": getattr(lines[0], "page_num", None),
        "bbox": bbox,
        "font_size": para_font_size,
    }
    if rich_segments:
        block["rich_segments"] = rich_segments
    return block


def _finalize_index_entry(
    lines: Sequence[Line], default_font_size: Optional[float] = None
) -> Optional[dict]:
    """
    Build a dedicated block for index entries so the structurer can emit cleaner XML.
    """
    block = _finalize_paragraph(lines, default_font_size=default_font_size)
    if block:
        block["label"] = "index_item"
        txt = block.get("text") or ""
        # Ensure a separator between trailing text and the first page number.
        cleaned = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", txt)
        block["text"] = cleaned
    return block


def _extract_table(lines: Sequence[Line], start_idx: int) -> tuple[dict, int] | None:
    rows: List[List[str]] = []
    column_positions: List[float] = []
    idx = start_idx
    min_rows = 2
    while idx < len(lines):
        line = lines[idx]
        cols = line.column_positions
        if len(cols) < 2:
            break
        if not column_positions:
            column_positions = cols
        elif len(cols) != len(column_positions):
            break
        elif any(abs(a - b) > 25 for a, b in zip(cols, column_positions)):
            break

        cells = [""] * len(column_positions)
        for segment in sorted(line.segments, key=lambda s: s.left):
            if not segment.text.strip():
                continue
            nearest = min(
                range(len(column_positions)),
                key=lambda idx_: abs(column_positions[idx_] - segment.left),
            )
            existing = cells[nearest]
            if existing:
                if not existing.endswith(" ") and not segment.text.startswith(" "):
                    existing += " "
                cells[nearest] = existing + segment.text
            else:
                cells[nearest] = segment.text.strip()
        rows.append([cell.strip() for cell in cells])
        idx += 1
        if idx < len(lines):
            gap = _line_gap(line, lines[idx])
            if line.height is not None and lines[idx].height is not None:
                if gap > max(line.height, lines[idx].height) * 1.8:
                    break

    if len(rows) >= min_rows:
        table_block = {
            "label": "table",
            "rows": rows,
            "page_num": lines[start_idx].page_num,
            "bbox": {
                "top": lines[start_idx].top,
                "left": min(column_positions) if column_positions else lines[start_idx].left,
                "width": (
                    (max(column_positions) - min(column_positions)) if column_positions else 0
                ),
                "height": lines[idx - 1].top - lines[start_idx].top + lines[idx - 1].height,
            },
            "text": "\n".join(" | ".join(row) for row in rows),
        }
        return table_block, idx
    return None


# helper (place near other helpers)
# NOTE: This function is overridden below - see second _flow_index definition
def _flow_index(el: ET.Element) -> float:
    # prefer explicit flow index - check reading_order (used by unified.xml) first
    for k in ("reading_order", "flow_idx", "seq", "order", "reading_idx"):
        v = el.get(k)
        if v is not None:
            try:
                # CRITICAL: Keep as FLOAT to preserve fractional reading_order (e.g., 3.5 for images)
                return float(v)
            except:
                pass
    return -1.0

def _entry_from_text(text_el: ET.Element, fontspecs_map) -> dict:
    # reuse your _max_text_size and parsing; keep it simple here:
    # Use itertext() to recursively extract text from nested formatting tags (e.g., <i><b>Index</b></i>)
    return {
        "kind": "text",
        "el": text_el,
        "text": "".join(text_el.itertext()).strip(),
        "font_size": _max_text_size(text_el, fontspecs_map),
        "flow_idx": _flow_index(text_el),
        "top": _f(text_el.get("orig_top") or text_el.get("top")),
        "left": _f(text_el.get("orig_left") or text_el.get("left")),
    }

def _iter_page_entries_preserve(page, fontspec_map):
    # Pull page context (be liberal with attribute names)
    pnum = None
    for k in ("number", "index", "page", "id"):
        v = page.get(k)
        if v is not None:
            try:
                pnum = int(v)
                break
            except Exception:
                pass

    ph = None
    for k in ("height", "pageheight", "h"):
        v = page.get(k)
        if v is not None:
            try:
                ph = float(v)
                break
            except Exception:
                pass

    pw = None
    for k in ("width", "pagewidth", "w"):
        v = page.get(k)
        if v is not None:
            try:
                pw = float(v)
                break
            except Exception:
                pass

    entries = []

    # Support both flat structure (old pdftohtml) and hierarchical structure (unified.xml)
    # For hierarchical structure: <page><texts><para><text>
    # For flat structure: <page><text>

    # Check if this is hierarchical unified.xml format
    texts_elem = page.find("texts")
    if texts_elem is not None:
        para_counter = 0
        # Unified.xml format: iterate through all <text> elements in reading order
        for para_elem in texts_elem.findall("para"):
            para_counter += 1
            para_identifier = (
                para_elem.get("id")
                or f"{page.get('number', '0')}_para_{para_counter}"
            )
            # Extract col_id and reading_block from para element for grouping
            para_col_id = para_elem.get("col_id")
            para_reading_block = para_elem.get("reading_block")

            for text_elem in para_elem.findall("text"):
                # Get the full text content using itertext() to recursively extract
                # text from nested formatting tags (e.g., <i><b>Index</b></i>)
                text_content = "".join(text_elem.itertext()).strip()

                # Build entry with column and reading block info
                entry = {
                    "kind": "text",
                    "el": text_elem,
                    "text": text_content,
                    "font_size": _max_text_size(text_elem, fontspec_map),
                    "flow_idx": _flow_index(text_elem),
                    "top": _f(text_elem.get("orig_top") or text_elem.get("top")),
                    "left": _f(text_elem.get("orig_left") or text_elem.get("left")),
                    "page_height": ph,
                    "page_width": pw,
                    "page_number": pnum,
                    "para_elem": para_elem,
                    "para_id": para_identifier,
                    "text_elem": text_elem,
                }

                # Add column info from parent para element (for multi-column handling)
                if para_col_id is not None:
                    try:
                        entry["col_id"] = int(para_col_id)
                    except ValueError:
                        pass
                if para_reading_block is not None:
                    try:
                        entry["reading_block"] = int(para_reading_block)
                    except ValueError:
                        pass

                entries.append(entry)

        # Add media elements with reading_order and reading_block
        # Check both old location (<media> section) and new location (<texts> section for interleaved content)
        media_elements = []
        media_elem = page.find("media")
        if media_elem is not None:
            media_elements.extend(media_elem.findall("media"))
        # Also check texts section for interleaved media
        texts_elem = page.find("texts")
        if texts_elem is not None:
            media_elements.extend(texts_elem.findall("media"))

        for media_el in media_elements:
            media_entry = {
                "kind": "image",
                "el": media_el,
                "top": _f(media_el.get("y1")),
                "left": _f(media_el.get("x1")),
                "page_height": ph,
                "page_width": pw,
                "page_number": pnum,
                "flow_idx": _flow_index(media_el),  # Use reading_order from media element
            }
            # Extract reading_block if present
            rb = media_el.get("reading_block")
            if rb is not None:
                try:
                    media_entry["reading_block"] = int(rb)
                except ValueError:
                    pass
            entries.append(media_entry)

        # Add table elements with reading_order and reading_block
        # Check both old location (<tables> section) and new location (<texts> section for interleaved content)
        table_elements = []
        tables_elem = page.find("tables")
        if tables_elem is not None:
            table_elements.extend(tables_elem.findall("table"))
        # Also check texts section for interleaved tables
        if texts_elem is not None:
            table_elements.extend(texts_elem.findall("table"))

        for table_el in table_elements:
            table_entry = {
                "kind": "table",
                "el": table_el,
                "top": _f(table_el.get("y1")),
                "left": _f(table_el.get("x1")),
                "page_height": ph,
                "page_width": pw,
                "page_number": pnum,
                "flow_idx": _flow_index(table_el),  # Use reading_order from table element
            }
            # Extract reading_block if present
            rb = table_el.get("reading_block")
            if rb is not None:
                try:
                    table_entry["reading_block"] = int(rb)
                except ValueError:
                    pass
            entries.append(table_entry)
    else:
        # Original flat pdftohtml format
        for ch in list(page):  # document order as written by grid_reading_order
            if ch.tag == "text":
                entries.append({
                    "kind": "text",
                    "el": ch,
                    # Use itertext() to recursively extract text from nested formatting tags
                    "text": "".join(ch.itertext()).strip(),
                    "font_size": _max_text_size(ch, fontspec_map),
                    "flow_idx": _flow_index(ch),
                    "top": _f(ch.get("orig_top") or ch.get("top")),
                    "left": _f(ch.get("orig_left") or ch.get("left")),
                    "page_height": ph,
                    "page_width": pw,
                    "page_number": pnum,
                })
            elif ch.tag in ("figure", "image", "diagram", "table"):
                # For non-text, we still carry page context (some filters look at it)
                entries.append({
                    "kind": ch.tag,
                    "el": ch,
                    "top": _f(ch.get("orig_top") or ch.get("top")),
                    "left": _f(ch.get("orig_left") or ch.get("left")),
                    "page_height": ph,
                    "page_width": pw,
                    "page_number": pnum,
                })
            # ignore other tags

    return entries

def _iter_page_entries(page: etree._Element, fontspecs: dict) -> Iterable[dict]:
    lines = _parse_lines(page, fontspecs)
    lines = _reorder_lines_for_columns(lines)
    for line in lines:
        yield {"kind": "line", "line": line}

    for image in page.findall("image"):
        src = image.get("src")
        if not src:
            continue
        yield {
            "kind": "image",
            "image": {
                "src": src,
                "top": float(image.get("top", "0") or 0),
                "left": float(image.get("left", "0") or 0),
                "width": float(image.get("width", "0") or 0),
                "height": float(image.get("height", "0") or 0),
                "page_num": int(page.get("number", "0") or 0),
            },
        }

def _flow_index(el):
    # prefer explicit flow index - check reading_order (used by unified.xml) first
    # then fallback to other common attribute names
    for k in ("reading_order", "flow_idx", "reading_idx", "seq", "order"):
        v = el.get(k)
        if v is not None:
            try:
                # CRITICAL: Keep reading_order as FLOAT, don't convert to int!
                # Images have fractional reading_order (3.5) to place between text blocks (3 and 4)
                # Converting to int loses this positioning information
                return float(v)
            except Exception:
                pass
    return -1.0


def label_blocks(pdfxml_path: str, mapping: dict, pdf_path: Optional[str] = None) -> List[dict]:
    """
    Produce a linear list of labeled content blocks from the flow XML.

    Pipeline:
      1) Parse flow XML, (optionally) preprocess for ebook production.
      2) For each <page>, collect entries in **preserved flow order** using
         _iter_page_entries_preserve(page, fontspecs_map). If a page lacks
         flow_idx (older outputs), fall back to (top, left) sorting.
      3) Filter headers / footers / repeated copyright with
         _is_header_footer_enhanced(...).
      4) Detect multi-line titles / headings / TOC / Index, list items, captions.
      5) Merge paragraphs with _should_merge and finalize with _finalize_paragraph.
      6) Return a flat list of dict blocks suitable for the structurer.

    Requires the following helpers to exist in this module:
      - _collect_fontspecs(root)
      - _iter_page_entries_preserve(page, fontspecs_map)
      - _line_of(entry)
      - _is_header_footer_enhanced(line, repeated_patterns, copyright_patterns, seen_copyright)
      - _body_font_size(lines)
      - _is_index_heading(line, body_size)
      - _looks_like_book_title(line, body_size)
      - _looks_like_chapter_heading(line, body_size)
      - _looks_like_section_heading(line, body_size)
      - _looks_like_caption(line)
      - _is_list_item(text, mapping) -> (matched: bool, list_type: str, cleaned_text: str)
      - _detect_list_sequence(entries, start_idx, mapping) -> (is_list: bool, list_type: str, num_items: int)
      - _should_merge(prev_line, next_line, body_size)
      - _finalize_paragraph(lines: List[Line], default_font_size: Optional[float] = None) -> Optional[dict]
      - _collect_multiline_heading(entries, idx, body_size)
      - _collect_multiline_book_title(entries, idx, body_size)
      - Constants: CHAPTER_KEYWORD_RE, TOC_RE, HEADING_FONT_TOLERANCE

    Returns:
      List[dict] blocks (labels include 'book_title', 'chapter', 'section',
      'caption', 'list_item', 'toc', 'figure', 'para').
    """
    # ── Parse flow XML
    tree = etree.parse(pdfxml_path)
    root = tree.getroot()

    # ── Initialize link processor and reference mapper for cross-references
    link_processor = None
    ref_mapper = None
    if HAS_LINK_PROCESSOR:
        try:
            ref_mapper = get_mapper()
            link_processor = LinkProcessor(ref_mapper)
            # Build page number to page_id mapping for TOC/Index links
            link_processor.build_page_number_map(root)
            logger.info("✅ Link processor initialized for cross-references")
        except Exception as e:
            logger.warning(f"Failed to initialize link processor: {e}")
            link_processor = None

    # ── Try to extract chapters from XML <outline> element (HIGHEST PRIORITY)
    logger.info("=" * 70)
    logger.info("CHAPTER DETECTION")
    logger.info("=" * 70)
    outline_chapters = _extract_outline_from_xml(tree)

    # If no outline in XML, try PDF bookmarks as fallback
    if not outline_chapters and pdf_path:
        logger.info("📚 No XML outline found - trying PDF bookmarks...")
        outline_chapters = _extract_bookmark_page_ranges(pdf_path)

    if outline_chapters:
        logger.info(f"✅ Will use {len(outline_chapters)} extracted chapters")
    else:
        logger.info("⚠️  No outline/bookmarks found - will use heuristic chapter detection")

    logger.info("=" * 70)

    # ── Optional preprocessing (safe if you have these functions; else no-ops)
    try:
        logger.info("=" * 70); logger.info("PREPROCESSING XML FOR EBOOK PRODUCTION"); logger.info("=" * 70)
        logger.info("🌍 Adding global coordinates...")
        doc_metadata = _add_global_coordinates(tree)  # optional
        logger.info(f"   ✅ Processed {doc_metadata.get('total_pages', 0)} pages")
        logger.info(f"   ✅ Total document height: {doc_metadata.get('total_height', 0):.0f}px")

        preprocessing_stats, repeated_patterns, copyright_patterns = _preprocess_xml_for_ebook(tree)
        logger.info("✅ Preprocessing complete:")
        logger.info(f"   • Removed {preprocessing_stats.get('duplicates_removed', 0)} duplicate text nodes")
        logger.info(f"   • Identified {preprocessing_stats.get('repeated_patterns_found', 0)} repeated patterns")
        logger.info(f"   • Found {preprocessing_stats.get('copyright_patterns_found', 0)} copyright notices (keeping first)")
        logger.info("=" * 70)
    except Exception:
        # If those hooks don't exist or fail, carry on with defaults
        repeated_patterns = []
        copyright_patterns = []
        logger.debug("Preprocessing hooks unavailable or skipped; continuing.")

    # ── Fontspecs map for style lookups
    fontspecs_map = _collect_fontspecs(root)

    # ── Build per-page entries in preserved order, with fallbacks
    entries: List[dict] = []
    for page in root.findall(".//page"):
        page_entries = _iter_page_entries_preserve(page, fontspecs_map)

        # Attach page context for downstream checks
        ph = float(page.get("height") or 0)
        pw = float(page.get("width") or 0)
        pn = int(page.get("number") or 0)
        for e in page_entries:
            e["page_height"] = ph
            e["page_width"] = pw
            e["page_number"] = pn

        # IMPORTANT: For unified.xml format, elements are already in correct reading order
        # (sorted by reading_order_block and norm_baseline in pdf_to_unified_xml.py)
        # We should TRUST the document order and NOT re-sort.
        #
        # Only fall back to (top, left) sorting for OLD pdftohtml format that lacks
        # reading_order attributes AND has no structural hierarchy (no <texts> element)
        texts_elem = page.find("texts")
        has_unified_format = texts_elem is not None

        if not has_unified_format:
            # Old pdftohtml format - check if any entries have flow index
            if not any((e.get("flow_idx", -1) >= 0) for e in page_entries):
                # No reading order info, fall back to position sorting
                page_entries.sort(key=lambda e: (e.get("top", float("inf")), e.get("left", float("inf"))))
        # else: Unified.xml format - TRUST document order from pdf_to_unified_xml.py
        # The XML is already sorted by (reading_order_block, col_id, baseline)
        # DO NOT re-sort by reading_order/flow_idx as that breaks column-first ordering

        entries.extend(page_entries)

    # ── Body font estimate for heuristics
    # Use safe wrapper to build Line objects from entries
    lines_for_body = []
    for e in entries:
        if e.get("kind") in ("text", "line"):
            try:
                lines_for_body.append(_line_of(e))
            except Exception:
                pass
    body_size = _body_font_size(lines_for_body)
    logger.debug("Estimated body font size: %.2f", body_size)

    # ── Main scan
    blocks: List[dict] = []
    current_para: List[Any] = []
    saw_book_title = False
    in_index_section = False
    in_toc_section = False  # Track when inside TOC
    in_glossary_section = False  # Track when inside Glossary
    front_matter_mode = False # JC look this
    front_matter_locked = False
    content_started = False
    seen_copyright = set()
    current_index_lines: List[Line] = []
    current_toc_lines: List[Line] = []  # Collect TOC entries
    current_glossary_lines: List[Line] = []  # Collect Glossary entries
    index_base_left: Optional[float] = None
    glossary_base_left: Optional[float] = None
    toc_heading_font_size: Optional[float] = None  # Track TOC heading font size
    glossary_heading_font_size: Optional[float] = None  # Track Glossary heading font size
    index_letter_re = re.compile(r"^[A-Z]$")

    def _flush_index_entry() -> None:
        nonlocal current_index_lines
        if not current_index_lines:
            return
        blk = _finalize_index_entry(current_index_lines, default_font_size=body_size)
        if blk:
            blocks.append(blk)
        current_index_lines = []

    def _flush_toc_entry() -> None:
        """Flush TOC entry - each line becomes its own entry (no merging)."""
        nonlocal current_toc_lines
        if not current_toc_lines:
            return
        # Emit each TOC line as a separate toc_item entry (no merging)
        for toc_line in current_toc_lines:
            blk = _finalize_index_entry([toc_line], default_font_size=body_size)
            if blk:
                blk["label"] = "toc_item"  # Mark as TOC item
                blocks.append(blk)
        current_toc_lines = []

    def _flush_glossary_entry() -> None:
        """Flush Glossary entry - each entry is kept separate."""
        nonlocal current_glossary_lines
        if not current_glossary_lines:
            return
        blk = _finalize_index_entry(current_glossary_lines, default_font_size=body_size)
        if blk:
            blk["label"] = "glossary_item"  # Mark as glossary item
            blocks.append(blk)
        current_glossary_lines = []

    def _append_paragraph_block(block: Optional[dict], *, fm_override: Optional[str] = None) -> None:
        if not block:
            return
        if front_matter_mode and not front_matter_locked:
            block["label"] = "front_matter"
            block["fm_type"] = fm_override or block.get("fm_type", "para")
        blocks.append(block)

    # Should we enforce that a "chapter" must contain word Chapter/Unit etc.?
    should_enforce_chapter_keyword = any(
        e.get("kind") in ("text", "line")
        and CHAPTER_KEYWORD_RE.search((_line_of(e).text or "").strip())
        and _has_heading_font(_line_of(e), body_size)
        for e in entries
    )
    enforce_chapter_keyword = False
    chapter_heading_font_size: Optional[float] = None

    idx = 0
    N = len(entries)
    while idx < N:
        entry = entries[idx]

        # Tables: flush paragraph then emit table block
        if entry.get("kind") == "table":
            if current_para:
                blk = _finalize_paragraph(current_para, default_font_size=body_size)
                _append_paragraph_block(blk)
                current_para = []
            
            table_el = entry.get("el")
            if table_el is not None:
                # Create a table block with the element and metadata
                table_block = {
                    "label": "table",
                    "el": table_el,
                    "page_num": entry.get("page_number"),
                    "id": table_el.get("id"),
                    "caption": "",
                }
                
                # Get caption from caption element or title attribute
                cap_el = table_el.find(".//caption")
                if cap_el is not None:
                    try:
                        table_block["caption"] = get_text(cap_el)
                    except Exception:
                        table_block["caption"] = "".join(cap_el.itertext()).strip()
                else:
                    table_block["caption"] = table_el.get("title") or ""
                
                blocks.append(table_block)
                logger.debug(f"Added table block: {table_block['id']}")
            idx += 1
            continue

        # Figures (images/figure nodes) pass-through: flush paragraph then emit figure
        if entry.get("kind") in ("image", "figure"):
            if current_para:
                blk = _finalize_paragraph(current_para, default_font_size=body_size)
                _append_paragraph_block(blk)
                current_para = []
            src = None
            source_path = None
            caption_text = ""
            top = entry.get("top")
            left = entry.get("left")
            width = entry.get("width")
            height = entry.get("height")
            page_num = entry.get("page_number")

            if entry.get("kind") == "image" and "image" in entry:
                image = entry["image"]  # expected structure from older extractor path
                src = image.get("src")
                caption_text = (image.get("caption") or "").strip()
                top = image.get("top")
                left = image.get("left")
                width = image.get("width")
                height = image.get("height")
                page_num = image.get("page_num", page_num)
                source_path = src
            else:
                fig_el = entry.get("el")
                if fig_el is not None:
                    # Try multiple attribute names for the file path
                    # "file" is used in unified.xml media elements
                    # "filename" and "src" may be used in other formats
                    src = fig_el.get("file") or fig_el.get("filename") or fig_el.get("src")
                    path_el = fig_el.find(".//path")
                    if path_el is not None and path_el.text:
                        source_path = path_el.text.strip()
                    if not src and source_path:
                        src = Path(source_path).name
                    if not source_path and src:
                        source_path = src

                    # Get caption from caption element FIRST, then fallback to title attribute
                    # The title attribute often contains large blocks of text, not actual captions
                    cap_el = fig_el.find(".//caption")
                    if cap_el is not None:
                        try:
                            caption_text = get_text(cap_el)
                        except Exception:
                            caption_text = "".join(cap_el.itertext()).strip()
                    else:
                        # Only use title attribute if no caption element exists
                        # Filter out large blocks of text that aren't real captions
                        title_text = fig_el.get("title") or ""
                        # Only use title if it's reasonably short (< 200 chars) or starts with "Figure"
                        if len(title_text) < 200 or title_text.strip().lower().startswith("figure"):
                            caption_text = title_text
                        else:
                            caption_text = ""
                            logger.debug(f"Skipping oversized title text ({len(title_text)} chars) for media {fig_el.get('id')}")

                    # For media elements from unified.xml, coordinates are in x1/y1/x2/y2
                    # Calculate width and height from x2-x1, y2-y1
                    x1 = _f(fig_el.get("x1"))
                    y1 = _f(fig_el.get("y1"))
                    x2 = _f(fig_el.get("x2"))
                    y2 = _f(fig_el.get("y2"))

                    if x1 is not None and x2 is not None and y1 is not None and y2 is not None:
                        left = x1
                        top = y1
                        width = x2 - x1
                        height = y2 - y1
                    else:
                        # Fallback to direct attributes
                        top = _f(fig_el.get("top"))
                        left = _f(fig_el.get("left"))
                        width = _f(fig_el.get("width"))
                        height = _f(fig_el.get("height"))

                    if page_num is None:
                        page_num = entry.get("page_number")

            figure_block = {
                "label": "figure",
                "text": caption_text or "",
                "page_num": page_num,
                "src": src,
                "source_path": source_path,
                "format": entry.get("el").get("type") if entry.get("kind") == "figure" and entry.get("el") is not None else None,
                "id": entry.get("el").get("id") if entry.get("kind") == "figure" and entry.get("el") is not None else None,
                "bbox": {
                    "top": top,
                    "left": left,
                    "width": width,
                    "height": height,
                },
            }

            # Extract figure label from caption and update reference mapper
            if link_processor and ref_mapper and caption_text and (source_path or src):
                try:
                    figure_label = extract_figure_label_from_caption(caption_text)
                    if figure_label:
                        # Normalize the label
                        figure_label = normalize_figure_label(figure_label)
                        # Store in the block for later use
                        figure_block["figure_label"] = figure_label
                        # Update reference mapper
                        # We'll use the source_path or src as the original path
                        original_path = source_path or src
                        if original_path not in ref_mapper.resources:
                            # Add resource if not already tracked
                            ref_mapper.add_resource(
                                original_path=original_path,
                                intermediate_name=src or original_path,
                                resource_type="image"
                            )
                        # Update figure metadata
                        ref_mapper.update_figure_metadata(
                            original_path=original_path,
                            figure_label=figure_label,
                            caption_text=caption_text
                        )
                        logger.debug(f"Extracted figure label: {figure_label} for {original_path}")
                except Exception as e:
                    logger.debug(f"Failed to extract figure label: {e}")

            blocks.append(figure_block)
            idx += 1
            continue

        # Normalize to a Line-like object for all text-ish entries
        line = _line_of(entry)
        # Ensure line has page context so downstream checks never see None
        try:
            if getattr(line, "page_num", None) is None:
                line.page_num = entry.get("page_number", 0)
            if getattr(line, "page_height", None) is None:
                line.page_height = entry.get("page_height", 0.0)
            if getattr(line, "page_width", None) is None:
                line.page_width = entry.get("page_width", 0.0)
        except Exception:
            # be defensive: don't let attribute errors break the pass
            pass

        # Skip headers/footers/copyright noise

        text = (line.text or "").strip()
        
        # BUT: Don't filter single uppercase letters when in index mode (alphabet headers)
        is_potential_alphabet_header = (
            in_index_section and
            len(text) == 1 and 
            text.isupper() and 
            text.isalpha()
        )
        
        try:
            if not is_potential_alphabet_header:
                if _is_header_footer_enhanced(line, repeated_patterns, copyright_patterns, seen_copyright):
                    idx += 1
                    continue
        except Exception:
            # Be defensive: if the enhanced predicate needs fields we don't have, skip enhancement
            pass
        # ── Inside TOC: handle leaving the TOC cleanly
        if in_toc_section and entry.get("kind") in ("line", "text"):
            # Check if this line has same or bigger font size than TOC heading
            if toc_heading_font_size is not None and line.font_size >= toc_heading_font_size:
                # Before exiting TOC, check if this line still looks like a TOC entry
                # (contains dots followed by page numbers - typical TOC pattern)
                # Also look ahead for entries on the same row (unified.xml splits text elements)
                combined_text = text
                curr_top = entry.get("top", 0)
                curr_row = entry.get("el").get("row_index") if entry.get("el") is not None else None
                lookahead_idx = idx + 1
                while lookahead_idx < len(entries):
                    next_entry = entries[lookahead_idx]
                    if next_entry.get("kind") not in ("text", "line"):
                        break
                    next_top = next_entry.get("top", 0)
                    next_row = next_entry.get("el").get("row_index") if next_entry.get("el") is not None else None
                    # Check if on same row (either same row_index or within 3px vertical tolerance)
                    same_row = (curr_row is not None and next_row == curr_row) or abs(next_top - curr_top) <= 3
                    if same_row:
                        combined_text += " " + (next_entry.get("text", "") or "").strip()
                        lookahead_idx += 1
                    else:
                        break

                is_toc_like = bool(re.search(r'\.{3,}.*\d+\s*$', combined_text)) or bool(re.search(r'\.\s*\d+\s*$', combined_text))
                if is_toc_like:
                    # Still a TOC entry despite font size (e.g., bold/italic "Index" entry)
                    # Update line text to include all entries on this row
                    if combined_text != text:
                        line.text = combined_text.strip()
                    if line.text:
                        current_toc_lines.append(line)
                    # Skip past the lookahead entries we already combined
                    idx = lookahead_idx
                    continue
                # TOC section ends
                _flush_toc_entry()
                current_toc_lines = []
                in_toc_section = False
                toc_heading_font_size = None
                # Let this line be processed normally - don't advance idx
            else:
                # Still in TOC, collect this entry
                if text:  # Only collect non-empty lines
                    current_toc_lines.append(line)
                idx += 1
                continue

        # ── Inside Glossary: handle leaving the glossary cleanly
        if in_glossary_section and entry.get("kind") in ("line", "text"):
            # Check if this line has same or bigger font size than Glossary heading
            if glossary_heading_font_size is not None and line.font_size >= glossary_heading_font_size:
                # Glossary section ends
                _flush_glossary_entry()
                current_glossary_lines = []
                glossary_base_left = None
                in_glossary_section = False
                glossary_heading_font_size = None
                # Let this line be processed normally - don't advance idx
            else:
                # Still in Glossary - each entry is separate (like Index)
                if text:
                    current_left = line.left if line.left is not None else (glossary_base_left or 0.0)
                    if not current_glossary_lines:
                        glossary_base_left = current_left
                        current_glossary_lines = [line]
                    else:
                        # Check if continuation (indented) or new entry
                        continuation = (
                            current_left > glossary_base_left + 8
                            or bool(re.match(r"^[,0-9\-–—]", text))  # Continuation markers
                        )
                        if continuation:
                            current_glossary_lines.append(line)
                        else:
                            _flush_glossary_entry()
                            glossary_base_left = current_left
                            current_glossary_lines = [line]
                idx += 1
                continue

        # ── Inside Index: handle leaving the index cleanly
        if in_index_section and _has_heading_font(line, body_size):
            # If it's just another "Index/References" heading inside the index, skip duplicate
            if _is_index_heading(line, body_size):
                idx += 1
                continue
            # Check if this is an alphabet header (single uppercase letter)
            # These should stay in index mode and be processed as index_letter blocks
            if len(text) == 1 and text.isupper() and text.isalpha():
                # This is an alphabet header - let it be processed by index_letter_re check below
                # Don't exit index mode, don't skip
                pass
            # Only exit index on EXPLICIT chapter/section keywords (very conservative)
            # Index is typically at the end of a book, so we should only exit for:
            # - Explicit chapter keywords (Chapter, Part, Unit, etc.)
            # - Other back-matter sections (Appendix, Glossary, Bibliography, etc.)
            # - TOC or front-matter (unlikely after Index but handle it)
            # Do NOT exit just because a line has heading font - index entries
            # and alphabet headers often have larger fonts
            #
            # IMPORTANT: Do NOT exit if text looks like an index entry (has page numbers,
            # dots, or other index-like patterns). These could be index categories
            # like "References 123-456" that should stay in index mode.
            else:
                # Check if it looks like an index entry (has page numbers, dots, or ranges)
                # NOTE: Be careful to distinguish page references from chapter numbers!
                # "Chapter 3" should NOT be treated as an index entry
                # Index entries typically have: comma/space before number, dot leaders, or page ranges
                looks_like_index_entry = (
                    bool(re.search(r'[,\s]{2,}\d+\s*$', text)) or  # Multiple spaces/comma before trailing number
                    bool(re.search(r'\.{2,}', text)) or  # Has dot leaders
                    bool(re.search(r'\d+\s*[-–—,]\s*\d+', text)) or  # Has page range (123-456 or 123, 456)
                    bool(re.search(r'\b\d{3,}\s*$', text))  # Ends with 3+ digit number (page numbers usually > 99)
                )
                if (not looks_like_index_entry
                    and (CHAPTER_KEYWORD_RE.search(text)  # Contains "chapter"
                         or CHAPTER_RE.match(text)  # Starts with chapter/unit/lesson/module
                         or re.match(r'^(appendix|epilogue|afterword|glossary|bibliography|references|preface|foreword|introduction|acknowledgements?|about\s+the\s+author)\b', text, re.IGNORECASE)
                         or TOC_RE.match(text.lower())  # Table of Contents
                         or _is_glossary_heading(line, body_size))):  # Glossary heading
                    # Leaving index for a new chapter/section
                    logger.debug(f"INDEX EXITING on page {line.page_num}: '{text}' (font_size={line.font_size})")
                    _flush_index_entry()
                    current_index_lines = []
                    index_base_left = None
                    if current_para:
                        blk = _finalize_paragraph(current_para, default_font_size=body_size)
                        _append_paragraph_block(blk)
                        current_para = []
                    in_index_section = False
                    # Important: do NOT advance idx here; let the heading logic below process this same line
                # Else: other heading-font lines in index (alphabet headers, sub-categories, etc.) - stay in index mode

        # ── Glossary section entry
        if _is_glossary_heading(line, body_size):
            if current_para:
                blk = _finalize_paragraph(current_para, default_font_size=body_size)
                _append_paragraph_block(blk)
                current_para = []
            _flush_glossary_entry()
            current_glossary_lines = []
            glossary_base_left = None
            heading_lines, next_idx = _collect_multiline_heading(entries, idx, body_size)
            if heading_lines:
                left = min((h.left if h.left is not None else 0.0) for h in heading_lines)
                right = max(
                    h.right if h.right is not None
                    else (h.left if h.left is not None else 0.0)
                    for h in heading_lines
                )
                top = heading_lines[0].top if heading_lines[0].top is not None else 0.0
                bottom = max(
                    (h.top if h.top is not None else 0.0) +
                    (h.height if h.height is not None else 0.0)
                    for h in heading_lines
                )

                combined_text = " ".join((h.text or "").strip() for h in heading_lines if (h.text or "").strip())
                blocks.append({
                    "label": "chapter",
                    "text": combined_text,
                    "page_num": heading_lines[0].page_num,
                    "bbox": {"top": top, "left": left, "width": right - left, "height": bottom - top},
                    "font_size": max(h.font_size for h in heading_lines if h.font_size),
                    "chapter_role": "glossary",
                })
                in_glossary_section = True
                glossary_heading_font_size = max(h.font_size for h in heading_lines if h.font_size)
                idx = next_idx
                continue

        # ── Index section entry
        if _is_index_heading(line, body_size):
            if current_para:
                blk = _finalize_paragraph(current_para, default_font_size=body_size)
                _append_paragraph_block(blk)
                current_para = []
            _flush_index_entry()
            current_index_lines = []
            index_base_left = None
            heading_lines, next_idx = _collect_multiline_heading(entries, idx, body_size)
            if heading_lines:
                left = min((h.left if h.left is not None else 0.0) for h in heading_lines)
                right = max(
                    h.right if h.right is not None
                    else (h.left if h.left is not None else 0.0)
                    for h in heading_lines
                )
                top = heading_lines[0].top if heading_lines[0].top is not None else 0.0
                bottom = max(
                    (h.top if h.top is not None else 0.0) +
                    (h.height if h.height is not None else 0.0)
                    for h in heading_lines
                )

                combined_text = " ".join((h.text or "").strip() for h in heading_lines if (h.text or "").strip())
                blocks.append({
                    "label": "chapter",
                    "text": combined_text,
                    "page_num": heading_lines[0].page_num,
                    "bbox": {"top": top, "left": left, "width": right - left, "height": bottom - top},
                    "font_size": max(h.font_size for h in heading_lines if h.font_size),
                    "chapter_role": "index",
                })
                in_index_section = True
                logger.debug(f"INDEX ENTERED on page {heading_lines[0].page_num}: '{combined_text}'")
                # Prime the base indentation using the first non-letter line we encounter
                if heading_lines:
                    first_line = heading_lines[-1]
                    index_base_left = first_line.left if first_line.left is not None else index_base_left
                idx = next_idx
                continue

        # ── TOC heading
        if TOC_RE.match(text.lower()) and _has_heading_font(line, body_size):
            if current_para:
                blk = _finalize_paragraph(current_para, default_font_size=body_size)
                _append_paragraph_block(blk)
                current_para = []
            heading_lines, next_idx = _collect_multiline_heading(entries, idx, body_size)
            if heading_lines:
                left = min((h.left if h.left is not None else 0.0) for h in heading_lines)
                right = max(
                    h.right if h.right is not None
                    else (h.left if h.left is not None else 0.0)
                    for h in heading_lines
                )
                top = heading_lines[0].top if heading_lines[0].top is not None else 0.0
                bottom = max(
                    (h.top if h.top is not None else 0.0) +
                    (h.height if h.height is not None else 0.0)
                    for h in heading_lines
                )

                combined_text = " ".join((h.text or "").strip() for h in heading_lines if (h.text or "").strip())
                blocks.append({
                    "label": "toc",
                    "text": combined_text,
                    "page_num": heading_lines[0].page_num,
                    "bbox": {"top": top, "left": left, "width": right - left, "height": bottom - top},
                    "font_size": max(h.font_size for h in heading_lines if h.font_size),
                })
                
                # NEW: Enable TOC collection mode
                in_toc_section = True
                toc_heading_font_size = max(h.font_size for h in heading_lines if h.font_size)
                current_toc_lines = []
                
                front_matter_mode = False
                front_matter_locked = True
                idx = next_idx
                continue

        if in_index_section and entry.get("kind") in ("line", "text"):
            if not text:
                idx += 1
                continue

            # Check for alphabetical headers (single uppercase letter)
            # Strip whitespace to handle cases like " A " or "A\n"
            text_stripped = text.strip()
            if index_letter_re.match(text_stripped):
                _flush_index_entry()
                current_index_lines = []
                index_base_left = None
                left = line.left if line.left is not None else 0.0
                right = line.right if line.right is not None else left
                blocks.append({
                    "label": "index_letter",
                    "text": text_stripped,  # Use stripped text for clean output
                    "page_num": line.page_num,
                    "bbox": {
                        "top": line.top,
                        "left": left,
                        "width": right - left,
                        "height": line.height,
                    },
                    "font_size": line.font_size,
                })
                idx += 1
                continue

            # For unified.xml format: Trust document order, don't use indentation-based grouping
            # Each line in unified.xml is already in the correct reading order
            # Simply emit each line as a separate index entry to preserve order
            texts_elem = entry.get("el")
            if texts_elem is not None:
                # This entry came from unified.xml - trust the document order
                # Create one index entry per line (don't group by indentation)
                blk = _finalize_index_entry([line], default_font_size=body_size)
                if blk:
                    blocks.append(blk)
                idx += 1
                continue
            else:
                # Fallback for old pdftohtml format - use indentation-based grouping
                current_left = line.left if line.left is not None else (index_base_left or 0.0)
                if not current_index_lines:
                    index_base_left = current_left
                    _flush_index_entry()
                    current_index_lines = [line]
                else:
                    assert index_base_left is not None  # for type-checkers
                    continuation = (
                        current_left > index_base_left + 8
                        or bool(re.match(r"^[,0-9]", text))
                    )
                    if continuation:
                        current_index_lines.append(line)
                    else:
                        _flush_index_entry()
                        index_base_left = current_left
                        current_index_lines = [line]

                idx += 1
                continue

        # ── Book title (only once)
        if not saw_book_title and _looks_like_book_title(line, body_size):
            if current_para:
                blk = _finalize_paragraph(current_para, default_font_size=body_size)
                _append_paragraph_block(blk)
            current_para = []
            heading_lines, next_idx = _collect_multiline_book_title(entries, idx, body_size)
            if heading_lines:
                left = min((h.left if h.left is not None else 0.0) for h in heading_lines)
                right = max(
                    h.right if h.right is not None
                    else (h.left if h.left is not None else 0.0)
                    for h in heading_lines
                )
                top = heading_lines[0].top if heading_lines[0].top is not None else 0.0
                bottom = max(
                    (h.top if h.top is not None else 0.0) +
                    (h.height if h.height is not None else 0.0)
                    for h in heading_lines
                )

                combined_text = " ".join((h.text or "").strip() for h in heading_lines if (h.text or "").strip())
                blocks.append({
                    "label": "book_title",
                    "text": combined_text,
                    "page_num": heading_lines[0].page_num,
                    "bbox": {"top": top, "left": left, "width": right - left, "height": bottom - top},
                    "font_size": max(h.font_size for h in heading_lines if h.font_size),
                })
                saw_book_title = True
                idx = next_idx
                continue

        # ── Part heading (higher level than chapter)
        if _looks_like_part_heading(line, body_size):
            if current_para:
                blk = _finalize_paragraph(current_para, default_font_size=body_size)
                _append_paragraph_block(blk)
                current_para = []

            heading_lines, lookahead_idx = _collect_multiline_heading(entries, idx, body_size)
            if heading_lines:
                combined_text = " ".join((h.text or "").strip() for h in heading_lines if (h.text or "").strip())

                left = min((h.left if h.left is not None else 0.0) for h in heading_lines)
                right = max(
                    h.right if h.right is not None
                    else (h.left if h.left is not None else 0.0)
                    for h in heading_lines
                )
                top = heading_lines[0].top if heading_lines[0].top is not None else 0.0
                bottom = max(
                    (h.top if h.top is not None else 0.0) +
                    (h.height if h.height is not None else 0.0)
                    for h in heading_lines
                )

                blocks.append({
                    "label": "part",
                    "text": combined_text,
                    "page_num": heading_lines[0].page_num,
                    "bbox": {"top": top, "left": left, "width": right - left, "height": bottom - top},
                    "font_size": max(h.font_size for h in heading_lines if h.font_size),
                })

                # Parts mark the start of main content
                front_matter_mode = False
                front_matter_locked = True
                content_started = True

                idx = lookahead_idx
                continue

        # ── Chapter heading
        if _looks_like_chapter_heading(line, body_size):
            if current_para:
                blk = _finalize_paragraph(current_para, default_font_size=body_size)
                _append_paragraph_block(blk)
                current_para = []

            heading_lines, lookahead_idx = _collect_multiline_heading(entries, idx, body_size)
            if heading_lines:
                combined_text = " ".join((h.text or "").strip() for h in heading_lines if (h.text or "").strip())
                contains_keyword = any(
                    CHAPTER_KEYWORD_RE.search((h.text or "").strip()) for h in heading_lines if (h.text or "").strip()
                )

                allow_heading = True
                first_line = heading_lines[0]
                base_font = first_line.font_size or 0.0

                if should_enforce_chapter_keyword and enforce_chapter_keyword and not contains_keyword:
                    allow_heading = False

                if contains_keyword and base_font:
                    if chapter_heading_font_size is None:
                        chapter_heading_font_size = base_font
                    elif abs(base_font - chapter_heading_font_size) > HEADING_FONT_TOLERANCE:
                        allow_heading = False
                    else:
                        chapter_heading_font_size = base_font
                elif chapter_heading_font_size is not None and should_enforce_chapter_keyword and enforce_chapter_keyword:
                    allow_heading = False

                left = min((h.left if h.left is not None else 0.0) for h in heading_lines)
                right = max(
                    h.right if h.right is not None
                    else (h.left if h.left is not None else 0.0)
                    for h in heading_lines
                )
                top = heading_lines[0].top if heading_lines[0].top is not None else 0.0
                bottom = max(
                    (h.top if h.top is not None else 0.0) +
                    (h.height if h.height is not None else 0.0)
                    for h in heading_lines
                )

                if allow_heading:
                    heading_label = (
                        "toc" if re.search(r'\b(table\s+of\s+)?contents\b', combined_text, re.IGNORECASE)
                        else "front_matter" if _is_front_matter_heading(combined_text, heading_lines[0].page_num)
                        else "chapter"
                    )
                    fm_type = None
                    if (
                        front_matter_mode
                        and not front_matter_locked
                        and not content_started
                        and heading_label == "chapter"
                    ):
                        heading_label = "front_matter"
                        fm_type = "heading"
                    if heading_label == "front_matter":
                        front_matter_mode = True
                        fm_type = "heading"
                    elif heading_label == "chapter":
                        front_matter_mode = False
                        front_matter_locked = True
                        content_started = True
                    block_entry = {
                        "label": heading_label,
                        "text": combined_text,
                        "page_num": heading_lines[0].page_num,
                        "bbox": {"top": top, "left": left, "width": right - left, "height": bottom - top},
                        "font_size": max(h.font_size for h in heading_lines if h.font_size),
                    }
                    if fm_type:
                        block_entry["fm_type"] = fm_type
                    blocks.append(block_entry)
                    if contains_keyword:
                        enforce_chapter_keyword = True
                else:
                    # demote to section
                    blocks.append({
                        "label": "section",
                        "text": combined_text,
                        "page_num": heading_lines[0].page_num,
                        "bbox": {"top": top, "left": left, "width": right - left, "height": bottom - top},
                        "font_size": max(h.font_size for h in heading_lines if h.font_size),
                    })
                idx = lookahead_idx
                continue

        # ── Section heading
        if _looks_like_section_heading(line, body_size):
            if current_para:
                blk = _finalize_paragraph(current_para, default_font_size=body_size)
                _append_paragraph_block(blk)
                current_para = []
            blocks.append({
                "label": "section",
                "text": text,
                "page_num": line.page_num,
                "bbox": {
                    "top": line.top, 
                    "left": line.left, 
                    "width": (line.right - line.left) if (line.right is not None and line.left is not None) else None, 
                    "height": line.height
                },
                "font_size": line.font_size,
            })
            idx += 1
            continue

        # ── Caption
        if _looks_like_caption(line):
            if current_para:
                blk = _finalize_paragraph(current_para, default_font_size=body_size)
                _append_paragraph_block(blk)
                current_para = []
            blocks.append({
                "label": "caption",
                "text": text,
                "page_num": line.page_num,
                "bbox": {
                    "top": line.top, 
                    "left": line.left, 
                    "width": (line.right - line.left) if (line.right is not None and line.left is not None) else None, 
                    "height": line.height
                },
                "font_size": line.font_size,
            })
            idx += 1
            continue

        # ── List item (with lookahead confirmation)
        is_list, list_type, num_items = _detect_list_sequence(entries, idx, mapping)
        if is_list:
            # Process all confirmed list items
            if current_para:
                blk = _finalize_paragraph(current_para, default_font_size=body_size)
                _append_paragraph_block(blk)
                current_para = []
            
            # Add all consecutive list items
            for list_idx in range(idx, min(idx + num_items, len(entries))):
                list_line = _line_of(entries[list_idx])
                _, item_type, item_text = _is_list_item(list_line.text, mapping)
                
                blocks.append({
                    "label": "list_item",
                    "text": item_text,
                    "page_num": list_line.page_num,
                    "bbox": {
                        "top": list_line.top, 
                        "left": list_line.left, 
                        "width": (list_line.right - list_line.left) if (list_line.right is not None and list_line.left is not None) else None, 
                        "height": list_line.height
                    },
                    "font_size": list_line.font_size,
                    "list_type": item_type,
                })
            
            idx += num_items
            continue

        # ── Paragraph aggregation
        if not current_para:
            current_para = [line]
        else:
            prev_para_id = getattr(current_para[-1], "para_id", None)
            curr_para_id = getattr(line, "para_id", None)

            if prev_para_id is not None or curr_para_id is not None:
                if prev_para_id is not None and curr_para_id is not None and prev_para_id == curr_para_id:
                    current_para.append(line)
                else:
                    blk = _finalize_paragraph(current_para, default_font_size=body_size)
                    _append_paragraph_block(blk)
                    current_para = [line]
            elif _should_merge(current_para[-1], line, body_size):
                current_para.append(line)
            else:
                blk = _finalize_paragraph(current_para, default_font_size=body_size)
                _append_paragraph_block(blk)
                current_para = [line]

        idx += 1

    # Flush final paragraph and any pending special section entries
    if current_para:
        blk = _finalize_paragraph(current_para, default_font_size=body_size)
        _append_paragraph_block(blk)
    _flush_index_entry()
    _flush_toc_entry()
    _flush_glossary_entry()

    logger.info("Labeled %s blocks", len(blocks))

    # Return blocks and link_processor (if available) for use in DocBook generation
    if link_processor:
        return blocks, link_processor
    return blocks, None


def _split_references_text(text: str) -> List[str]:
    """
    Split merged reference text into individual reference items.

    References are typically numbered like:
    "1. First ref text. 2. Second ref text. 3. Third ref text."

    Returns list of individual references with their numbers preserved.
    """
    if not text:
        return []

    # Pattern to split at numbered references: period/end followed by number+period
    # Matches: ". 2." or ". 10." or "- 3." etc. at the boundary between references
    # Use lookahead to keep the number with the next reference
    split_pattern = re.compile(r'(?<=\.)\s+(?=\d{1,3}\.)')

    parts = split_pattern.split(text)

    # Filter empty parts and strip whitespace
    references = [part.strip() for part in parts if part.strip()]

    # Validate that we actually split into numbered items
    # If we only have one part or parts don't start with numbers, return empty (use original)
    if len(references) <= 1:
        return []

    # Check that most parts start with a number pattern
    numbered_count = sum(1 for ref in references if re.match(r'^\d{1,3}\.', ref))
    if numbered_count < len(references) * 0.5:
        # Not enough numbered items - probably not a references section
        return []

    return references


def blocks_to_docbook_xml(blocks: List[dict], title: str = "Untitled", link_processor: Optional['LinkProcessor'] = None) -> etree._Element:
    """
    Convert labeled blocks to DocBook XML structure with unique IDs.

    Args:
        blocks: List of content blocks from label_blocks()
        title: Book title
        link_processor: Optional LinkProcessor for figure cross-references and page links

    Returns:
        DocBook XML root element
    """
    # Create book root element
    book = etree.Element("book")

    # Add book title if we have one
    book_title_blocks = [b for b in blocks if b.get("label") == "book_title"]
    if book_title_blocks:
        title = book_title_blocks[0].get("text", title)

    book_info = etree.SubElement(book, "bookinfo")
    title_el = etree.SubElement(book_info, "title")
    title_el.text = title

    # Process blocks into parts, chapters and sections with unique IDs
    current_part = None
    current_part_id = None
    current_chapter = None
    current_chapter_id = None
    current_section = None
    # Track nested lists: stack of (list_element, indent_level, list_type)
    list_stack = []
    # Indentation tolerance for determining nesting levels (in points)
    LIST_INDENT_TOLERANCE = 15
    part_counter = 0
    chapter_counter = 0
    section_counter = {}  # Track section counters per chapter
    in_references_section = False  # Track when we're in a REFERENCES section

    for block in blocks:
        label = block.get("label", "para")
        text = block.get("text", "")

        if label == "book_title":
            # Already handled above
            continue

        elif label == "part":
            # Close any open list
            list_stack.clear()

            # Create new part with unique ID
            part_counter += 1
            current_part_id = f"Part{part_counter:04d}"

            current_part = etree.SubElement(book, "part")
            current_part.set("id", current_part_id)

            part_title = etree.SubElement(current_part, "title")
            part_title.text = text

            # Reset chapter tracking for new part
            current_chapter = None
            current_chapter_id = None
            current_section = None
            in_references_section = False

            logger.info(f"Created part: id={current_part_id}, title='{text}'")

        elif label == "chapter":
            # Close any open list
            list_stack.clear()

            # Create new chapter with unique ID
            chapter_counter += 1
            current_chapter_id = f"Ch{chapter_counter:04d}"
            section_counter[current_chapter_id] = 0

            # Add chapter to current part if one exists, otherwise to book
            parent_element = current_part if current_part is not None else book
            current_chapter = etree.SubElement(parent_element, "chapter")
            current_chapter.set("id", current_chapter_id)

            # Set role attribute if specified (e.g., "index", "glossary", "toc")
            chapter_role = block.get("chapter_role")
            if chapter_role:
                current_chapter.set("role", chapter_role)

            chapter_title = etree.SubElement(current_chapter, "title")
            chapter_title.text = text
            current_section = None
            in_references_section = False  # Reset when entering new chapter

        elif label == "section":
            # Close any open list
            list_stack.clear()

            # Check if this is a References section
            section_text_upper = text.strip().upper()
            in_references_section = section_text_upper in ("REFERENCES", "REFERENCE", "BIBLIOGRAPHY", "WORKS CITED")

            # Create section under current chapter with unique ID
            if current_chapter is not None:
                section_counter[current_chapter_id] += 1
                section_id = f"{current_chapter_id}Sec{section_counter[current_chapter_id]:04d}"

                current_section = etree.SubElement(current_chapter, "sect1")
                current_section.set("id", section_id)
                section_title = etree.SubElement(current_section, "title")
                section_title.text = text
            else:
                # No chapter yet, create a default one
                chapter_counter += 1
                current_chapter_id = f"Ch{chapter_counter:04d}"
                section_counter[current_chapter_id] = 0

                current_chapter = etree.SubElement(current_part if current_part is not None else book, "chapter")
                current_chapter.set("id", current_chapter_id)
                chapter_title = etree.SubElement(current_chapter, "title")
                chapter_title.text = "Content"

                section_counter[current_chapter_id] += 1
                section_id = f"{current_chapter_id}Sec{section_counter[current_chapter_id]:04d}"

                current_section = etree.SubElement(current_chapter, "sect1")
                current_section.set("id", section_id)
                section_title = etree.SubElement(current_section, "title")
                section_title.text = text

        elif label == "para":
            # Close any open list before adding paragraph
            list_stack.clear()

            # Add paragraph to current section or chapter
            target = current_section if current_section is not None else current_chapter
            if target is None:
                # Create a default chapter if needed
                chapter_counter += 1
                current_chapter_id = f"Ch{chapter_counter:04d}"
                section_counter[current_chapter_id] = 0

                current_chapter = etree.SubElement(current_part if current_part is not None else book, "chapter")
                current_chapter.set("id", current_chapter_id)
                chapter_title = etree.SubElement(current_chapter, "title")
                chapter_title.text = "Content"
                target = current_chapter

            # Check if we're in a references section and need to split merged references
            if in_references_section and text:
                split_refs = _split_references_text(text)
                if split_refs:
                    # Create orderedlist for references
                    orderedlist = etree.SubElement(target, "orderedlist")
                    for ref_text in split_refs:
                        listitem = etree.SubElement(orderedlist, "listitem")
                        ref_para = etree.SubElement(listitem, "para")
                        # Apply dehyphenation to each reference
                        ref_para.text = fix_word_splits_enhanced(ref_text)
                    continue  # Skip normal para handling

            para = etree.SubElement(target, "para")

            figure_refs = []
            if link_processor and text:
                try:
                    figure_refs = link_processor.find_figure_references(text) or []
                except Exception as e:
                    logger.debug(f"Failed to process figure references: {e}")
                    figure_refs = []

            rich_segments = block.get("rich_segments")
            if rich_segments:
                injected_segments = _inject_xrefs_into_segments(rich_segments, figure_refs)
                if injected_segments is not None:
                    _apply_rich_segments_to_para(para, injected_segments)
                    continue  # Paragraph handled with preserved inline formatting

            # Fallback: existing plain-text handling (inline formatting unavailable)
            if figure_refs:
                para.text = ""
                last_end = 0
                for ref in sorted(figure_refs, key=lambda r: r.start_pos):
                    if ref.start_pos > last_end:
                        if para.text == "":
                            para.text = text[last_end:ref.start_pos]
                        else:
                            children = list(para)
                            if children:
                                if children[-1].tail:
                                    children[-1].tail += text[last_end:ref.start_pos]
                                else:
                                    children[-1].tail = text[last_end:ref.start_pos]
                            else:
                                para.text += text[last_end:ref.start_pos]

                    if ref.target_image:
                        xref = etree.SubElement(para, "xref")
                        xref.set("linkend", ref.target_image.replace(".", "_").replace("/", "_"))
                        xref.text = ref.full_text
                    else:
                        if para.text == "":
                            para.text = ref.full_text
                        else:
                            children = list(para)
                            if children:
                                if children[-1].tail:
                                    children[-1].tail += ref.full_text
                                else:
                                    children[-1].tail = ref.full_text
                            else:
                                para.text += ref.full_text

                    last_end = ref.end_pos

                if last_end < len(text):
                    remaining = text[last_end:]
                    children = list(para)
                    if children:
                        if children[-1].tail:
                            children[-1].tail += remaining
                        else:
                            children[-1].tail = remaining
                    else:
                        if para.text:
                            para.text += remaining
                        else:
                            para.text = remaining
            else:
                para.text = fix_word_splits_enhanced(text)

        elif label == "figure":
            # Close any open list
            list_stack.clear()

            # Add figure
            target = current_section if current_section is not None else current_chapter
            if target is None:
                chapter_counter += 1
                current_chapter_id = f"Ch{chapter_counter:04d}"
                section_counter[current_chapter_id] = 0

                current_chapter = etree.SubElement(current_part if current_part is not None else book, "chapter")
                current_chapter.set("id", current_chapter_id)
                chapter_title = etree.SubElement(current_chapter, "title")
                chapter_title.text = "Content"
                target = current_chapter

            figure = etree.SubElement(target, "figure")

            # Generate unique ID for the figure (for cross-references)
            src = block.get("src") or block.get("source_path")
            block_id = block.get("id")
            page_num = block.get("page_num")

            # Use block ID if available, otherwise generate from src
            if block_id:
                figure.set("id", block_id)
            elif src:
                figure_id = src.replace(".", "_").replace("/", "_")
                figure.set("id", figure_id)

            # Build title text from label and caption
            # Note: <label> is NOT a valid child of <figure> in DocBook DTD
            # So we merge the label into the title text
            figure_label = block.get("figure_label")
            title_text = None

            if figure_label and text:
                # Combine label and caption: "Figure 1.2  Caption text"
                title_text = f"{figure_label}  {text}"
            elif figure_label:
                title_text = figure_label
            elif text:
                title_text = text

            # Add title/caption - title MUST come before content in DocBook
            if title_text:
                caption = etree.SubElement(figure, "title")
                caption.text = title_text

            # Add mediaobject for the image
            if src:
                mediaobject = etree.SubElement(figure, "mediaobject")
                imageobject = etree.SubElement(mediaobject, "imageobject")
                imagedata = etree.SubElement(imageobject, "imagedata")
                imagedata.set("fileref", src)
                imagedata.set("width", "100%")
                imagedata.set("scalefit", "1")
                logger.info(f"Created figure: id={figure.get('id')}, src={src}, page={page_num}, target_chapter={current_chapter_id if current_chapter is not None else 'none'}")
            else:
                logger.warning(f"Figure created without mediaobject - missing src attribute (id={block_id or 'unknown'}, page={page_num})")

        elif label == "table":
            # Handle table blocks
            # Close any open list
            list_stack.clear()

            target = current_section if current_section is not None else current_chapter
            if target is None:
                chapter_counter += 1
                current_chapter_id = f"Ch{chapter_counter:04d}"
                section_counter[current_chapter_id] = 0

                current_chapter = etree.SubElement(current_part if current_part is not None else book, "chapter")
                current_chapter.set("id", current_chapter_id)
                chapter_title = etree.SubElement(current_chapter, "title")
                chapter_title.text = "Content"
                target = current_chapter

            # Get the table element from the block
            table_el = block.get("el")
            if table_el is not None:
                # Create a DocBook table structure
                # Use informaltable if no caption, otherwise use table with title
                caption_text = block.get("caption", "")
                table_id = block.get("id")
                
                if caption_text:
                    # Formal table with caption
                    table_wrapper = etree.SubElement(target, "table")
                    if table_id:
                        table_wrapper.set("id", table_id)
                    
                    title_elem = etree.SubElement(table_wrapper, "title")
                    title_elem.text = caption_text
                    
                    # Create tgroup for the table structure
                    tgroup = etree.SubElement(table_wrapper, "tgroup")
                else:
                    # Informal table (no caption)
                    table_wrapper = etree.SubElement(target, "informaltable")
                    if table_id:
                        table_wrapper.set("id", table_id)
                    
                    # Create tgroup for the table structure
                    tgroup = etree.SubElement(table_wrapper, "tgroup")
                
                # Extract table structure from the unified XML element
                rows_el = table_el.find(".//rows")
                if rows_el is not None:
                    rows = rows_el.findall("row")
                    if rows:
                        # Determine number of columns
                        first_row = rows[0]
                        num_cols = len(first_row.findall("cell"))
                        tgroup.set("cols", str(num_cols))
                        
                        # Create colspec elements
                        for col_idx in range(num_cols):
                            colspec = etree.SubElement(tgroup, "colspec")
                            colspec.set("colname", f"c{col_idx + 1}")
                        
                        # Create tbody
                        tbody = etree.SubElement(tgroup, "tbody")
                        
                        # Add rows
                        for row_el in rows:
                            row = etree.SubElement(tbody, "row")
                            cells = row_el.findall("cell")
                            for cell_el in cells:
                                entry = etree.SubElement(row, "entry")
                                
                                # Extract cell text from chunks
                                chunks = cell_el.findall("chunk")
                                cell_text_parts = []
                                for chunk in chunks:
                                    if chunk.text:
                                        cell_text_parts.append(chunk.text.strip())
                                
                                cell_text = " ".join(cell_text_parts).strip()
                                if cell_text:
                                    entry.text = cell_text
                        
                        logger.debug(f"Created DocBook table structure for {table_id} with {len(rows)} rows and {num_cols} cols")

        elif label == "list_item":
            # Properly handle list items with itemizedlist/orderedlist and nested lists
            target = current_section if current_section is not None else current_chapter
            if target is None:
                chapter_counter += 1
                current_chapter_id = f"Ch{chapter_counter:04d}"
                section_counter[current_chapter_id] = 0

                current_chapter = etree.SubElement(current_part if current_part is not None else book, "chapter")
                current_chapter.set("id", current_chapter_id)
                chapter_title = etree.SubElement(current_chapter, "title")
                chapter_title.text = "Content"
                target = current_chapter

            # Determine list type from block metadata
            list_type = block.get("list_type", "unordered")

            # Get indentation level from bbox
            bbox = block.get("bbox", {})
            item_indent = bbox.get("left", 0) if bbox else 0
            if item_indent is None:
                item_indent = 0

            # Determine where to add the list item based on nesting
            if not list_stack:
                # No existing list - create new top-level list
                if list_type == "ordered":
                    new_list = etree.SubElement(target, "orderedlist")
                else:
                    new_list = etree.SubElement(target, "itemizedlist")
                list_stack.append((new_list, item_indent, list_type))
                current_list = new_list
            else:
                # Compare indentation with current list level
                current_list_elem, current_indent, current_type = list_stack[-1]

                if abs(item_indent - current_indent) <= LIST_INDENT_TOLERANCE:
                    # Same indentation level - use current list
                    # But check if list type changed
                    if list_type != current_type:
                        # Type changed at same level - close current and create new sibling list
                        # Pop current list
                        list_stack.pop()
                        # Determine parent for new list
                        if list_stack:
                            # Parent is the listitem that contains the popped list
                            parent_listitem = current_list_elem.getparent()
                            if parent_listitem is not None and parent_listitem.tag == 'listitem':
                                parent_for_new = parent_listitem
                            else:
                                parent_for_new = target
                        else:
                            parent_for_new = target

                        if list_type == "ordered":
                            new_list = etree.SubElement(parent_for_new, "orderedlist")
                        else:
                            new_list = etree.SubElement(parent_for_new, "itemizedlist")
                        list_stack.append((new_list, item_indent, list_type))
                        current_list = new_list
                    else:
                        current_list = current_list_elem

                elif item_indent > current_indent + LIST_INDENT_TOLERANCE:
                    # Deeper indentation - create nested list inside the last listitem
                    # Find the last listitem in the current list
                    last_listitem = None
                    for child in reversed(list(current_list_elem)):
                        if child.tag == 'listitem':
                            last_listitem = child
                            break

                    if last_listitem is not None:
                        # Create nested list inside the last listitem
                        if list_type == "ordered":
                            nested_list = etree.SubElement(last_listitem, "orderedlist")
                        else:
                            nested_list = etree.SubElement(last_listitem, "itemizedlist")
                        list_stack.append((nested_list, item_indent, list_type))
                        current_list = nested_list
                    else:
                        # No listitem found - just use current list
                        current_list = current_list_elem

                else:
                    # Shallower indentation - pop stack until we find matching level
                    while list_stack and len(list_stack) > 1:
                        _, stack_indent, _ = list_stack[-1]
                        if stack_indent <= item_indent + LIST_INDENT_TOLERANCE:
                            break
                        list_stack.pop()

                    if list_stack:
                        current_list_elem, current_indent, current_type = list_stack[-1]
                        if list_type != current_type:
                            # Different type - create sibling list
                            list_stack.pop()
                            if list_stack:
                                parent_listitem = current_list_elem.getparent()
                                if parent_listitem is not None and parent_listitem.tag == 'listitem':
                                    parent_for_new = parent_listitem
                                else:
                                    parent_for_new = target
                            else:
                                parent_for_new = target

                            if list_type == "ordered":
                                new_list = etree.SubElement(parent_for_new, "orderedlist")
                            else:
                                new_list = etree.SubElement(parent_for_new, "itemizedlist")
                            list_stack.append((new_list, item_indent, list_type))
                            current_list = new_list
                        else:
                            current_list = current_list_elem
                    else:
                        # Stack is empty - create new top-level list
                        if list_type == "ordered":
                            new_list = etree.SubElement(target, "orderedlist")
                        else:
                            new_list = etree.SubElement(target, "itemizedlist")
                        list_stack.append((new_list, item_indent, list_type))
                        current_list = new_list

            # Add list item to the current list
            listitem = etree.SubElement(current_list, "listitem")
            para = etree.SubElement(listitem, "para")
            para.text = fix_word_splits_enhanced(text)

        elif label in ("toc", "index", "front_matter"):
            # Close any open list
            list_stack.clear()

            # Create special chapters for these
            chapter_counter += 1
            current_chapter_id = f"Ch{chapter_counter:04d}"
            section_counter[current_chapter_id] = 0

            chapter = etree.SubElement(current_part if current_part is not None else book, "chapter")
            chapter.set("id", current_chapter_id)
            chapter.set("role", label)
            chapter_title = etree.SubElement(chapter, "title")
            chapter_title.text = text
            current_chapter = chapter
            current_section = None

        elif label == "index_letter":
            # Handle alphabetical index headers (A, B, C, etc.)
            # These should be rendered as bridgehead elements within the index chapter
            target = current_section if current_section is not None else current_chapter
            if target is None:
                # Create a default index chapter if needed
                chapter_counter += 1
                current_chapter_id = f"Ch{chapter_counter:04d}"
                section_counter[current_chapter_id] = 0

                current_chapter = etree.SubElement(current_part if current_part is not None else book, "chapter")
                current_chapter.set("id", current_chapter_id)
                chapter_title = etree.SubElement(current_chapter, "title")
                chapter_title.text = "Index"
                current_chapter.set("role", "index")
                target = current_chapter

            # Create bridgehead element for the alphabet letter
            bridgehead = etree.SubElement(target, "bridgehead")
            bridgehead.set("renderas", "sect2")
            bridgehead.text = text

        elif label == "index_item":
            # Handle index items (entries with page numbers)
            target = current_section if current_section is not None else current_chapter
            if target is None:
                # Create a default chapter if needed
                chapter_counter += 1
                current_chapter_id = f"Ch{chapter_counter:04d}"
                section_counter[current_chapter_id] = 0

                current_chapter = etree.SubElement(current_part if current_part is not None else book, "chapter")
                current_chapter.set("id", current_chapter_id)
                chapter_title = etree.SubElement(current_chapter, "title")
                chapter_title.text = "Index"
                current_chapter.set("role", "index")
                target = current_chapter

            # Create index entry as plain text (no page links to avoid validation errors)
            para = etree.SubElement(target, "para")
            para.text = fix_word_splits_enhanced(text)

        elif label == "toc_item":
            # Handle TOC items - each as a separate para (no merging)
            target = current_section if current_section is not None else current_chapter
            if target is None:
                # Create a default chapter if needed
                chapter_counter += 1
                current_chapter_id = f"Ch{chapter_counter:04d}"
                section_counter[current_chapter_id] = 0

                current_chapter = etree.SubElement(current_part if current_part is not None else book, "chapter")
                current_chapter.set("id", current_chapter_id)
                chapter_title = etree.SubElement(current_chapter, "title")
                chapter_title.text = "Table of Contents"
                current_chapter.set("role", "toc")
                target = current_chapter

            para = etree.SubElement(target, "para")
            para.set("role", "toc-entry")
            para.text = fix_word_splits_enhanced(text)

        elif label == "glossary_item":
            # Handle Glossary items - each as a separate para (no merging)
            target = current_section if current_section is not None else current_chapter
            if target is None:
                # Create a default chapter if needed
                chapter_counter += 1
                current_chapter_id = f"Ch{chapter_counter:04d}"
                section_counter[current_chapter_id] = 0

                current_chapter = etree.SubElement(current_part if current_part is not None else book, "chapter")
                current_chapter.set("id", current_chapter_id)
                chapter_title = etree.SubElement(current_chapter, "title")
                chapter_title.text = "Glossary"
                current_chapter.set("role", "glossary")
                target = current_chapter

            para = etree.SubElement(target, "para")
            para.set("role", "glossary-entry")
            para.text = fix_word_splits_enhanced(text)

        elif label == "caption":
            # Handle captions (might be in TOC/Index with page numbers)
            target = current_section if current_section is not None else current_chapter
            if target is None:
                # Create a default chapter if needed
                chapter_counter += 1
                current_chapter_id = f"Ch{chapter_counter:04d}"
                section_counter[current_chapter_id] = 0

                current_chapter = etree.SubElement(current_part if current_part is not None else book, "chapter")
                current_chapter.set("id", current_chapter_id)
                chapter_title = etree.SubElement(current_chapter, "title")
                chapter_title.text = "Content"
                target = current_chapter

            para = etree.SubElement(target, "para")
            para.set("role", "caption")
            para.text = fix_word_splits_enhanced(text)

    return book


def main():
    """CLI entry point for heuristics processing."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="Apply heuristics to unified XML and create structured DocBook XML"
    )
    parser.add_argument("unified_xml", help="Path to unified XML file")
    parser.add_argument("--font-roles", required=True, help="Path to font roles JSON")
    parser.add_argument("--out", required=True, help="Path to output structured XML")
    parser.add_argument("--pdf", help="Optional path to original PDF (for bookmark extraction)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    logger.info("=" * 70)
    logger.info("HEURISTICS PROCESSING")
    logger.info("=" * 70)
    logger.info(f"Input XML: {args.unified_xml}")
    logger.info(f"Font roles: {args.font_roles}")
    logger.info(f"Output: {args.out}")

    # Load font roles
    try:
        with open(args.font_roles, 'r', encoding='utf-8') as f:
            font_roles = json.load(f)
        logger.info(f"✓ Loaded font roles")
    except Exception as e:
        logger.error(f"✗ Failed to load font roles: {e}")
        sys.exit(1)

    # Create mapping structure expected by label_blocks
    # Note: Removed plain hyphen "-" from default markers to reduce false positives
    # Strong bullet markers are kept (•, ◦, ▪)
    # En-dash and em-dash kept but will be validated with stricter rules
    mapping = {
        "pdf": {
            "list_markers": ["•", "◦", "▪", "✓", "●", "○", "■", "□", "–", "—"],
        },
        "font_roles": font_roles
    }

    # Process blocks
    try:
        logger.info("Processing blocks...")
        result = label_blocks(args.unified_xml, mapping, pdf_path=args.pdf)
        # Handle both old (blocks only) and new (blocks, link_processor) return formats
        if isinstance(result, tuple):
            blocks, link_processor = result
        else:
            blocks = result
            link_processor = None
        logger.info(f"✓ Extracted {len(blocks)} blocks")
    except Exception as e:
        logger.error(f"✗ Failed to process blocks: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Convert to DocBook XML
    try:
        logger.info("Converting to DocBook XML...")
        book_title = "Document"
        # Try to extract title from first book_title block
        for block in blocks:
            if block.get("label") == "book_title":
                book_title = block.get("text", book_title)
                break

        root = blocks_to_docbook_xml(blocks, title=book_title, link_processor=link_processor)
        logger.info(f"✓ Created DocBook structure")
    except Exception as e:
        logger.error(f"✗ Failed to create DocBook XML: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Write output
    try:
        tree = etree.ElementTree(root)
        tree.write(
            args.out,
            encoding='utf-8',
            xml_declaration=True,
            pretty_print=True
        )
        logger.info(f"✓ Wrote structured XML: {args.out}")
        logger.info("=" * 70)
        logger.info("✓ HEURISTICS PROCESSING COMPLETE")
        logger.info("=" * 70)
    except Exception as e:
        logger.error(f"✗ Failed to write output: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()