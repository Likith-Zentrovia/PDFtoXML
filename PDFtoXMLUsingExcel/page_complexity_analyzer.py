#!/usr/bin/env python3
"""
Page Complexity Analyzer for PDF to XML Conversion (v2.0)

This module analyzes each page of a PDF to determine its complexity level.
Complex pages (with multiple tables, images, or complex layouts) are routed
to the AI conversion pipeline, while simple pages use the faster non-AI pipeline.

Improved Detection Methods:
- Table detection using line intersection analysis and text grid patterns
- Proper image extraction using PyMuPDF's get_images()
- Multi-column detection using text block clustering
- Drawing/vector graphics analysis

Usage:
    from page_complexity_analyzer import PageComplexityAnalyzer

    analyzer = PageComplexityAnalyzer()
    results = analyzer.analyze_pdf("document.pdf")

    for page_num, complexity in results.items():
        if complexity.is_complex:
            # Route to AI pipeline
        else:
            # Route to non-AI pipeline
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict

# PyMuPDF for PDF analysis
try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False
    print("ERROR: PyMuPDF (fitz) is required. Install with: pip install pymupdf")


class ComplexityLevel(Enum):
    """Complexity level for a PDF page."""
    SIMPLE = "simple"           # Text-only or minimal formatting
    MODERATE = "moderate"       # Some tables/images but straightforward
    COMPLEX = "complex"         # Multiple tables, images, or complex layout
    HIGHLY_COMPLEX = "highly_complex"  # Very complex - definitely needs AI


@dataclass
class ComplexityThresholds:
    """Configurable thresholds for complexity detection."""
    # Table thresholds
    table_count_simple: int = 0       # 0 tables = simple
    table_count_moderate: int = 1     # 1 table = moderate
    table_count_complex: int = 1      # 1+ tables = complex (tables need AI)

    # Image thresholds
    image_count_simple: int = 0       # 0 images = simple
    image_count_moderate: int = 1     # 1 image = moderate
    image_count_complex: int = 2      # 2+ images = complex
    image_area_threshold: float = 0.25 # 25% of page covered by images = complex

    # Layout thresholds
    column_count_complex: int = 2     # 2+ columns = complex (multi-column needs AI)

    # Text density thresholds
    min_chars_per_page: int = 50      # Pages with < 50 chars might be mostly images

    # Mixed content scoring
    mixed_content_score_complex: int = 3  # Combined score threshold for complexity


@dataclass
class PageComplexity:
    """Analysis result for a single PDF page."""
    page_num: int

    # Content counts
    table_count: int = 0
    image_count: int = 0
    text_block_count: int = 0
    drawing_count: int = 0  # Vector graphics
    line_count: int = 0     # Horizontal/vertical lines (table indicators)

    # Layout metrics
    column_count: int = 1
    has_multi_column: bool = False

    # Area coverage (percentage of page)
    image_area_pct: float = 0.0
    table_area_pct: float = 0.0
    text_area_pct: float = 0.0

    # Character and word counts
    char_count: int = 0
    word_count: int = 0

    # Special elements
    has_footnotes: bool = False
    has_headers_footers: bool = False
    has_rotated_content: bool = False
    has_nested_tables: bool = False

    # Complexity scoring
    complexity_score: int = 0
    complexity_level: ComplexityLevel = ComplexityLevel.SIMPLE

    # Final routing decision
    is_complex: bool = False
    route_to_ai: bool = False

    # Reasoning for complexity decision
    complexity_reasons: List[str] = field(default_factory=list)

    def add_reason(self, reason: str) -> None:
        """Add a reason for complexity classification."""
        if reason not in self.complexity_reasons:
            self.complexity_reasons.append(reason)


@dataclass
class PDFComplexityReport:
    """Overall complexity analysis for a PDF document."""
    pdf_path: str
    total_pages: int

    # Page classifications
    simple_pages: List[int] = field(default_factory=list)
    moderate_pages: List[int] = field(default_factory=list)
    complex_pages: List[int] = field(default_factory=list)
    highly_complex_pages: List[int] = field(default_factory=list)

    # Routing decisions
    ai_route_pages: List[int] = field(default_factory=list)
    non_ai_route_pages: List[int] = field(default_factory=list)

    # Per-page details
    page_results: Dict[int, PageComplexity] = field(default_factory=dict)

    # Summary statistics
    avg_complexity_score: float = 0.0
    max_tables_per_page: int = 0
    max_images_per_page: int = 0
    total_tables: int = 0
    total_images: int = 0

    def summary(self) -> str:
        """Generate a summary of the complexity analysis."""
        lines = [
            f"PDF Complexity Analysis: {self.pdf_path}",
            f"=" * 60,
            f"Total pages: {self.total_pages}",
            f"",
            f"Routing Summary:",
            f"  - AI pipeline pages: {len(self.ai_route_pages)} ({self._pct(len(self.ai_route_pages))}%)",
            f"  - Non-AI pipeline pages: {len(self.non_ai_route_pages)} ({self._pct(len(self.non_ai_route_pages))}%)",
            f"",
            f"Complexity Breakdown:",
            f"  - Simple: {len(self.simple_pages)} pages",
            f"  - Moderate: {len(self.moderate_pages)} pages",
            f"  - Complex: {len(self.complex_pages)} pages",
            f"  - Highly Complex: {len(self.highly_complex_pages)} pages",
            f"",
            f"Content Statistics:",
            f"  - Total tables: {self.total_tables}",
            f"  - Total images: {self.total_images}",
            f"  - Max tables/page: {self.max_tables_per_page}",
            f"  - Max images/page: {self.max_images_per_page}",
            f"  - Avg complexity score: {self.avg_complexity_score:.2f}",
        ]
        return "\n".join(lines)

    def _pct(self, count: int) -> str:
        """Calculate percentage of total pages."""
        if self.total_pages == 0:
            return "0"
        return f"{(count / self.total_pages) * 100:.1f}"


class PageComplexityAnalyzer:
    """
    Analyzes PDF pages to determine complexity and route to appropriate pipeline.

    Uses PyMuPDF (fitz) for fast PDF analysis with improved detection methods:
    - Table detection via line patterns and text grid analysis
    - Image detection via get_images() API
    - Multi-column detection via text block clustering
    """

    def __init__(
        self,
        thresholds: Optional[ComplexityThresholds] = None,
        verbose: bool = True
    ):
        """
        Initialize the complexity analyzer.

        Args:
            thresholds: Custom thresholds for complexity detection
            verbose: Print progress messages
        """
        if not HAS_FITZ:
            raise ImportError("PyMuPDF (fitz) is required. Install with: pip install pymupdf")

        self.thresholds = thresholds or ComplexityThresholds()
        self.verbose = verbose

    def analyze_pdf(self, pdf_path: str | Path) -> PDFComplexityReport:
        """
        Analyze all pages of a PDF for complexity.

        Args:
            pdf_path: Path to the PDF file

        Returns:
            PDFComplexityReport with per-page complexity analysis
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        if self.verbose:
            print(f"\n{'=' * 60}")
            print(f"Analyzing PDF complexity: {pdf_path.name}")
            print(f"{'=' * 60}")

        doc = fitz.open(str(pdf_path))
        total_pages = len(doc)

        report = PDFComplexityReport(
            pdf_path=str(pdf_path),
            total_pages=total_pages
        )

        # Analyze each page
        for page_num in range(total_pages):
            page = doc[page_num]
            complexity = self._analyze_page(page, page_num + 1)
            report.page_results[page_num + 1] = complexity

            # Update routing lists
            if complexity.route_to_ai:
                report.ai_route_pages.append(page_num + 1)
            else:
                report.non_ai_route_pages.append(page_num + 1)

            # Update complexity classification lists
            if complexity.complexity_level == ComplexityLevel.SIMPLE:
                report.simple_pages.append(page_num + 1)
            elif complexity.complexity_level == ComplexityLevel.MODERATE:
                report.moderate_pages.append(page_num + 1)
            elif complexity.complexity_level == ComplexityLevel.COMPLEX:
                report.complex_pages.append(page_num + 1)
            else:
                report.highly_complex_pages.append(page_num + 1)

            # Update statistics
            report.total_tables += complexity.table_count
            report.total_images += complexity.image_count
            report.max_tables_per_page = max(report.max_tables_per_page, complexity.table_count)
            report.max_images_per_page = max(report.max_images_per_page, complexity.image_count)

        doc.close()

        # Calculate average complexity score
        if total_pages > 0:
            report.avg_complexity_score = sum(
                r.complexity_score for r in report.page_results.values()
            ) / total_pages

        if self.verbose:
            print(f"\n{report.summary()}")

        return report

    def analyze_page(self, pdf_path: str | Path, page_num: int) -> PageComplexity:
        """
        Analyze a single page of a PDF.

        Args:
            pdf_path: Path to the PDF file
            page_num: 1-based page number

        Returns:
            PageComplexity analysis for the page
        """
        pdf_path = Path(pdf_path)
        doc = fitz.open(str(pdf_path))

        if page_num < 1 or page_num > len(doc):
            doc.close()
            raise ValueError(f"Page {page_num} out of range (1-{len(doc)})")

        page = doc[page_num - 1]
        complexity = self._analyze_page(page, page_num)
        doc.close()

        return complexity

    def _analyze_page(self, page: fitz.Page, page_num: int) -> PageComplexity:
        """
        Analyze a single page for complexity.

        Args:
            page: PyMuPDF page object
            page_num: 1-based page number

        Returns:
            PageComplexity analysis result
        """
        complexity = PageComplexity(page_num=page_num)
        page_rect = page.rect
        page_area = page_rect.width * page_rect.height

        # 1. Analyze images (using proper PyMuPDF API)
        self._analyze_images(page, complexity, page_area)

        # 2. Analyze tables using multiple detection methods
        self._analyze_tables(page, complexity, page_area)

        # 3. Analyze text content
        self._analyze_text(page, complexity, page_area)

        # 4. Analyze vector drawings
        self._analyze_drawings(page, complexity)

        # 5. Detect multi-column layout
        self._analyze_layout(page, complexity)

        # 6. Calculate complexity score and determine routing
        self._calculate_complexity(complexity)

        if self.verbose:
            route = "AI" if complexity.route_to_ai else "Non-AI"
            level = complexity.complexity_level.value
            reasons = ", ".join(complexity.complexity_reasons[:3]) if complexity.complexity_reasons else "simple content"
            print(f"  Page {page_num:3d}: {level:15s} -> {route:6s} ({reasons})")

        return complexity

    def _analyze_images(self, page: fitz.Page, complexity: PageComplexity, page_area: float) -> None:
        """Analyze images on the page using PyMuPDF's proper image extraction."""
        # Get all images on the page
        image_list = page.get_images(full=True)
        complexity.image_count = len(image_list)

        # Calculate image area coverage
        total_image_area = 0.0
        for img in image_list:
            try:
                xref = img[0]
                # Get all rectangles where this image appears on the page
                img_rects = page.get_image_rects(xref)
                for rect in img_rects:
                    if rect and not rect.is_empty:
                        total_image_area += rect.width * rect.height
            except Exception:
                pass

        if page_area > 0:
            complexity.image_area_pct = min(total_image_area / page_area, 1.0)

    def _analyze_tables(self, page: fitz.Page, complexity: PageComplexity, page_area: float) -> None:
        """
        Analyze tables on the page using multiple detection methods:
        1. Line-based detection (horizontal/vertical lines forming grids)
        2. Text pattern detection (aligned text blocks in grid formation)
        """
        # Method 1: Count lines that could form table borders
        h_lines, v_lines = self._extract_lines(page)
        complexity.line_count = len(h_lines) + len(v_lines)

        # Method 2: Detect table-like structures from line intersections
        table_regions = self._detect_table_regions_from_lines(h_lines, v_lines, page.rect)

        # Method 3: Detect tables from text alignment patterns
        text_tables = self._detect_tables_from_text(page)

        # Combine detections (remove duplicates based on overlap)
        all_tables = self._merge_table_detections(table_regions, text_tables, page.rect)
        complexity.table_count = len(all_tables)

        # Calculate table area coverage
        total_table_area = sum(r.width * r.height for r in all_tables if r)
        if page_area > 0:
            complexity.table_area_pct = min(total_table_area / page_area, 1.0)

    def _extract_lines(self, page: fitz.Page) -> Tuple[List, List]:
        """Extract horizontal and vertical lines from page drawings."""
        h_lines = []
        v_lines = []

        try:
            paths = page.get_drawings()

            for path in paths:
                for item in path.get("items", []):
                    if item[0] == "l":  # Line
                        p1, p2 = item[1], item[2]

                        # Horizontal line (y values approximately equal)
                        if abs(p1.y - p2.y) < 3:
                            length = abs(p2.x - p1.x)
                            if length > 20:  # Minimum line length
                                h_lines.append((min(p1.x, p2.x), max(p1.x, p2.x), (p1.y + p2.y) / 2))

                        # Vertical line (x values approximately equal)
                        elif abs(p1.x - p2.x) < 3:
                            length = abs(p2.y - p1.y)
                            if length > 20:  # Minimum line length
                                v_lines.append(((p1.x + p2.x) / 2, min(p1.y, p2.y), max(p1.y, p2.y)))

                    elif item[0] == "re":  # Rectangle (4 lines)
                        rect = item[1]
                        if rect.width > 20 and rect.height > 10:
                            # Add rectangle edges as lines
                            h_lines.append((rect.x0, rect.x1, rect.y0))
                            h_lines.append((rect.x0, rect.x1, rect.y1))
                            v_lines.append((rect.x0, rect.y0, rect.y1))
                            v_lines.append((rect.x1, rect.y0, rect.y1))
        except Exception:
            pass

        return h_lines, v_lines

    def _detect_table_regions_from_lines(
        self,
        h_lines: List[Tuple[float, float, float]],
        v_lines: List[Tuple[float, float, float]],
        page_rect: fitz.Rect
    ) -> List[fitz.Rect]:
        """Detect table regions from line patterns."""
        if len(h_lines) < 2 or len(v_lines) < 2:
            return []

        tables = []

        # Cluster horizontal lines by y-position
        h_clusters = self._cluster_lines_by_position(h_lines, pos_idx=2, tolerance=10)

        # Cluster vertical lines by x-position
        v_clusters = self._cluster_lines_by_position(v_lines, pos_idx=0, tolerance=10)

        # A table needs at least 2 horizontal rows and 2 vertical columns of lines
        if len(h_clusters) >= 2 and len(v_clusters) >= 2:
            # Find bounding box of the line grid
            all_h_y = [l[2] for l in h_lines]
            all_v_x = [l[0] for l in v_lines]
            all_h_x = [x for l in h_lines for x in (l[0], l[1])]
            all_v_y = [y for l in v_lines for y in (l[1], l[2])]

            if all_h_y and all_v_x:
                y_min, y_max = min(all_h_y), max(all_h_y)
                x_min, x_max = min(all_v_x), max(all_v_x)

                # Also consider line extents
                if all_h_x:
                    x_min = min(x_min, min(all_h_x))
                    x_max = max(x_max, max(all_h_x))
                if all_v_y:
                    y_min = min(y_min, min(all_v_y))
                    y_max = max(y_max, max(all_v_y))

                # Check if this is a reasonable table size
                width = x_max - x_min
                height = y_max - y_min

                if width > 50 and height > 30:
                    table_rect = fitz.Rect(x_min, y_min, x_max, y_max)
                    tables.append(table_rect)

        return tables

    def _cluster_lines_by_position(
        self,
        lines: List[Tuple],
        pos_idx: int,
        tolerance: float
    ) -> List[List]:
        """Cluster lines by their position (y for horizontal, x for vertical)."""
        if not lines:
            return []

        sorted_lines = sorted(lines, key=lambda x: x[pos_idx])
        clusters = []
        current_cluster = [sorted_lines[0]]

        for line in sorted_lines[1:]:
            if abs(line[pos_idx] - current_cluster[-1][pos_idx]) <= tolerance:
                current_cluster.append(line)
            else:
                if len(current_cluster) >= 1:
                    clusters.append(current_cluster)
                current_cluster = [line]

        if len(current_cluster) >= 1:
            clusters.append(current_cluster)

        return clusters

    def _detect_tables_from_text(self, page: fitz.Page) -> List[fitz.Rect]:
        """Detect tables by analyzing text alignment patterns."""
        tables = []

        try:
            # Get text blocks
            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE).get("blocks", [])
            text_blocks = [b for b in blocks if b.get("type") == 0]

            if len(text_blocks) < 4:
                return tables

            # Group blocks by their left x-position (potential columns)
            x_positions = defaultdict(list)
            for block in text_blocks:
                bbox = block.get("bbox", (0, 0, 0, 0))
                x_pos = round(bbox[0] / 10) * 10  # Round to nearest 10
                x_positions[x_pos].append(block)

            # If we have multiple columns with multiple items each, might be a table
            columns_with_multiple = [x for x, blocks in x_positions.items() if len(blocks) >= 2]

            if len(columns_with_multiple) >= 2:
                # Check if items are aligned horizontally (same y positions)
                all_blocks_in_columns = []
                for x in columns_with_multiple:
                    all_blocks_in_columns.extend(x_positions[x])

                y_positions = defaultdict(int)
                for block in all_blocks_in_columns:
                    bbox = block.get("bbox", (0, 0, 0, 0))
                    y_pos = round(bbox[1] / 10) * 10
                    y_positions[y_pos] += 1

                # If multiple y-positions have multiple blocks, looks like a table
                rows_with_multiple = [y for y, count in y_positions.items() if count >= 2]

                if len(rows_with_multiple) >= 2 and len(columns_with_multiple) >= 2:
                    # Calculate bounding box
                    all_bboxes = [b.get("bbox", (0, 0, 0, 0)) for b in all_blocks_in_columns]
                    x0 = min(b[0] for b in all_bboxes)
                    y0 = min(b[1] for b in all_bboxes)
                    x1 = max(b[2] for b in all_bboxes)
                    y1 = max(b[3] for b in all_bboxes)

                    if x1 - x0 > 100 and y1 - y0 > 50:
                        tables.append(fitz.Rect(x0, y0, x1, y1))

        except Exception:
            pass

        return tables

    def _merge_table_detections(
        self,
        line_tables: List[fitz.Rect],
        text_tables: List[fitz.Rect],
        page_rect: fitz.Rect
    ) -> List[fitz.Rect]:
        """Merge table detections from different methods, removing duplicates."""
        all_tables = line_tables + text_tables

        if not all_tables:
            return []

        # Remove duplicates based on significant overlap (>50%)
        merged = []
        for table in all_tables:
            is_duplicate = False
            for existing in merged:
                intersection = table & existing  # Intersection
                if intersection and not intersection.is_empty:
                    # Calculate overlap percentage
                    intersection_area = intersection.width * intersection.height
                    table_area = table.width * table.height
                    existing_area = existing.width * existing.height

                    if table_area > 0 and existing_area > 0:
                        overlap_pct = intersection_area / min(table_area, existing_area)
                        if overlap_pct > 0.5:
                            # Merge into existing (expand to cover both)
                            existing.include_rect(table)
                            is_duplicate = True
                            break

            if not is_duplicate:
                merged.append(table)

        return merged

    def _analyze_text(self, page: fitz.Page, complexity: PageComplexity, page_area: float) -> None:
        """Analyze text content on the page."""
        text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

        blocks = text_dict.get("blocks", [])
        text_blocks = [b for b in blocks if b.get("type") == 0]
        complexity.text_block_count = len(text_blocks)

        # Calculate text area and content
        total_text_area = 0.0
        total_text = ""

        for block in text_blocks:
            bbox = block.get("bbox", (0, 0, 0, 0))
            total_text_area += (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])

            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    total_text += span.get("text", "")

        if page_area > 0:
            complexity.text_area_pct = total_text_area / page_area

        complexity.char_count = len(total_text)
        complexity.word_count = len(total_text.split())

        # Check for footnotes (small text at bottom of page)
        page_height = page.rect.height
        for block in text_blocks:
            bbox = block.get("bbox", (0, 0, 0, 0))
            if bbox[1] > page_height * 0.85:  # Bottom 15% of page
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        size = span.get("size", 12)
                        if size < 9 or re.match(r'^[\d\*\†\‡]+\s', text):
                            complexity.has_footnotes = True
                            break

        # Check for headers/footers
        for block in text_blocks:
            bbox = block.get("bbox", (0, 0, 0, 0))
            if bbox[1] < page_height * 0.08 or bbox[3] > page_height * 0.92:
                complexity.has_headers_footers = True
                break

    def _analyze_drawings(self, page: fitz.Page, complexity: PageComplexity) -> None:
        """Analyze vector drawings on the page."""
        try:
            drawings = page.get_drawings()

            # Count complex drawings (not simple lines)
            drawing_count = 0
            for path in drawings:
                items = path.get("items", [])
                # Complex drawing if it has curves, fills, or many segments
                has_curve = any(item[0] in ("c", "qu") for item in items)
                has_fill = path.get("fill") is not None
                many_segments = len(items) > 4

                if has_curve or has_fill or many_segments:
                    drawing_count += 1

            complexity.drawing_count = drawing_count
        except Exception:
            pass

    def _analyze_layout(self, page: fitz.Page, complexity: PageComplexity) -> None:
        """Analyze page layout for multi-column detection."""
        text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        blocks = text_dict.get("blocks", [])
        text_blocks = [b for b in blocks if b.get("type") == 0]

        if len(text_blocks) < 3:
            complexity.column_count = 1
            return

        # Get x-positions of text block left edges
        x_positions = []
        for block in text_blocks:
            bbox = block.get("bbox", (0, 0, 0, 0))
            x_positions.append(bbox[0])

        if not x_positions:
            complexity.column_count = 1
            return

        # Cluster x-positions to detect columns
        page_width = page.rect.width
        tolerance = page_width * 0.08  # 8% of page width

        x_positions.sort()
        columns = []
        current_col = [x_positions[0]]

        for x in x_positions[1:]:
            if x - current_col[0] < tolerance:
                current_col.append(x)
            else:
                columns.append(current_col)
                current_col = [x]

        columns.append(current_col)

        # Filter out columns with too few blocks
        significant_columns = [c for c in columns if len(c) >= 2]

        complexity.column_count = max(1, len(significant_columns))
        complexity.has_multi_column = complexity.column_count > 1

    def _calculate_complexity(self, complexity: PageComplexity) -> None:
        """
        Calculate overall complexity score and determine routing.

        ROUTING LOGIC (Simplified):
        - Pages with TABLES -> AI (tables need AI for proper extraction)
        - Pages with IMAGES -> AI (images need AI for proper handling)
        - Plain text pages -> Non-AI (fast text extraction)
        """
        score = 0
        t = self.thresholds

        # Only tables and images determine AI routing
        has_tables = complexity.table_count > 0
        has_images = complexity.image_count > 0

        # Table scoring
        if has_tables:
            score += 5
            complexity.add_reason(f"{complexity.table_count} table(s)")

        # Image scoring - only actual embedded images, not drawings
        if has_images:
            score += 4
            complexity.add_reason(f"{complexity.image_count} image(s)")

        # Additional info for reporting (doesn't affect routing)
        if complexity.has_multi_column:
            complexity.add_reason(f"{complexity.column_count}-column layout")

        if complexity.has_footnotes:
            complexity.add_reason("has footnotes")

        # Store complexity score
        complexity.complexity_score = score

        # Determine complexity level based on tables/images only
        if has_tables and has_images:
            complexity.complexity_level = ComplexityLevel.HIGHLY_COMPLEX
        elif has_tables or has_images:
            complexity.complexity_level = ComplexityLevel.COMPLEX
        elif complexity.has_multi_column or complexity.drawing_count > 5:
            complexity.complexity_level = ComplexityLevel.MODERATE
        else:
            complexity.complexity_level = ComplexityLevel.SIMPLE

        # ROUTING DECISION: Only route to AI if page has tables OR images
        # Plain text pages (even with multi-column, footnotes, etc.) go to non-AI
        complexity.route_to_ai = has_tables or has_images
        complexity.is_complex = complexity.route_to_ai

        # Simple pages with no reason
        if not complexity.complexity_reasons:
            complexity.add_reason("text-only content")


def analyze_pdf_complexity(
    pdf_path: str | Path,
    thresholds: Optional[ComplexityThresholds] = None,
    verbose: bool = True
) -> PDFComplexityReport:
    """
    Convenience function to analyze PDF complexity.

    Args:
        pdf_path: Path to PDF file
        thresholds: Optional custom thresholds
        verbose: Print progress messages

    Returns:
        PDFComplexityReport with analysis results
    """
    analyzer = PageComplexityAnalyzer(thresholds=thresholds, verbose=verbose)
    return analyzer.analyze_pdf(pdf_path)


def main():
    """CLI entry point for PDF complexity analysis."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Analyze PDF page complexity for conversion routing"
    )
    parser.add_argument("pdf", help="Path to PDF file")
    parser.add_argument("--output", "-o", help="Output JSON file for results")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress verbose output")
    parser.add_argument(
        "--table-threshold",
        type=int,
        default=1,
        help="Number of tables to consider page complex (default: 1)"
    )
    parser.add_argument(
        "--image-threshold",
        type=int,
        default=2,
        help="Number of images to consider page complex (default: 2)"
    )

    args = parser.parse_args()

    # Custom thresholds
    thresholds = ComplexityThresholds(
        table_count_complex=args.table_threshold,
        image_count_complex=args.image_threshold
    )

    # Analyze PDF
    report = analyze_pdf_complexity(
        args.pdf,
        thresholds=thresholds,
        verbose=not args.quiet
    )

    # Output results
    if args.output:
        result = {
            "pdf_path": report.pdf_path,
            "total_pages": report.total_pages,
            "ai_route_pages": report.ai_route_pages,
            "non_ai_route_pages": report.non_ai_route_pages,
            "simple_pages": report.simple_pages,
            "moderate_pages": report.moderate_pages,
            "complex_pages": report.complex_pages,
            "highly_complex_pages": report.highly_complex_pages,
            "statistics": {
                "total_tables": report.total_tables,
                "total_images": report.total_images,
                "avg_complexity_score": report.avg_complexity_score,
                "max_tables_per_page": report.max_tables_per_page,
                "max_images_per_page": report.max_images_per_page
            },
            "page_details": {
                str(k): {
                    "complexity_level": v.complexity_level.value,
                    "route_to_ai": v.route_to_ai,
                    "table_count": v.table_count,
                    "image_count": v.image_count,
                    "complexity_score": v.complexity_score,
                    "reasons": v.complexity_reasons
                }
                for k, v in report.page_results.items()
            }
        }

        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)

        print(f"\nResults saved to: {args.output}")

    # Print routing summary
    print(f"\nRouting Decision:")
    print(f"  AI Pipeline: pages {report.ai_route_pages if report.ai_route_pages else 'none'}")
    print(f"  Non-AI Pipeline: pages {report.non_ai_route_pages if report.non_ai_route_pages else 'none'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
