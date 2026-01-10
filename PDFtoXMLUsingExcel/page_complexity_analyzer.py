#!/usr/bin/env python3
"""
Page Complexity Analyzer for PDF to XML Conversion

This module analyzes each page of a PDF to determine its complexity level.
Complex pages (with multiple tables, images, or complex layouts) are routed
to the AI conversion pipeline, while simple pages use the faster non-AI pipeline.

Complexity Indicators:
- Multiple tables on a single page
- High image count or large image coverage area
- Multi-column layouts
- Mixed content types (text + tables + images)
- Complex formatting (nested structures, footnotes, etc.)

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

# PyMuPDF for PDF analysis
try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False
    print("ERROR: PyMuPDF (fitz) is required. Install with: pip install pymupdf")

# Camelot for advanced table detection (optional)
try:
    import camelot
    HAS_CAMELOT = True
except ImportError:
    HAS_CAMELOT = False


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
    table_count_complex: int = 2      # 2+ tables = complex

    # Image thresholds
    image_count_simple: int = 1       # 0-1 images = simple
    image_count_moderate: int = 3     # 2-3 images = moderate
    image_count_complex: int = 4      # 4+ images = complex
    image_area_threshold: float = 0.3 # 30% of page covered by images = complex

    # Layout thresholds
    column_count_complex: int = 3     # 3+ columns = complex

    # Text density thresholds
    min_chars_per_page: int = 100     # Pages with < 100 chars might be mostly images

    # Mixed content scoring
    mixed_content_score_complex: int = 5  # Combined score threshold for complexity


@dataclass
class PageComplexity:
    """Analysis result for a single PDF page."""
    page_num: int

    # Content counts
    table_count: int = 0
    image_count: int = 0
    text_block_count: int = 0
    drawing_count: int = 0  # Vector graphics

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

    Uses PyMuPDF (fitz) for fast PDF analysis without external dependencies.
    Optionally uses Camelot for more accurate table detection.
    """

    def __init__(
        self,
        thresholds: Optional[ComplexityThresholds] = None,
        use_camelot: bool = False,
        verbose: bool = True
    ):
        """
        Initialize the complexity analyzer.

        Args:
            thresholds: Custom thresholds for complexity detection
            use_camelot: Use Camelot for table detection (slower but more accurate)
            verbose: Print progress messages
        """
        if not HAS_FITZ:
            raise ImportError("PyMuPDF (fitz) is required. Install with: pip install pymupdf")

        self.thresholds = thresholds or ComplexityThresholds()
        self.use_camelot = use_camelot and HAS_CAMELOT
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

        # 1. Analyze images
        self._analyze_images(page, complexity, page_area)

        # 2. Analyze tables (using line detection)
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
        """Analyze images on the page."""
        images = page.get_images(full=True)
        complexity.image_count = len(images)

        # Calculate image area coverage
        total_image_area = 0.0
        for img in images:
            try:
                xref = img[0]
                img_rects = page.get_image_rects(xref)
                for rect in img_rects:
                    total_image_area += rect.width * rect.height
            except Exception:
                pass

        if page_area > 0:
            complexity.image_area_pct = total_image_area / page_area

    def _analyze_tables(self, page: fitz.Page, complexity: PageComplexity, page_area: float) -> None:
        """
        Analyze tables on the page using line detection.

        Uses PyMuPDF's drawing detection to identify table structures
        based on horizontal and vertical lines forming grids.
        """
        # Get all paths (lines/drawings) on the page
        paths = page.get_drawings()

        # Separate horizontal and vertical lines
        h_lines = []
        v_lines = []

        for path in paths:
            for item in path.get("items", []):
                if item[0] == "l":  # Line
                    p1, p2 = item[1], item[2]

                    # Horizontal line (y values approximately equal)
                    if abs(p1.y - p2.y) < 2:
                        h_lines.append((min(p1.x, p2.x), max(p1.x, p2.x), p1.y))
                    # Vertical line (x values approximately equal)
                    elif abs(p1.x - p2.x) < 2:
                        v_lines.append((p1.x, min(p1.y, p2.y), max(p1.y, p2.y)))

        # Detect table regions by finding grids of lines
        table_regions = self._detect_table_regions(h_lines, v_lines, page.rect)
        complexity.table_count = len(table_regions)

        # Calculate table area coverage
        total_table_area = sum(r.width * r.height for r in table_regions)
        if page_area > 0:
            complexity.table_area_pct = total_table_area / page_area

    def _detect_table_regions(
        self,
        h_lines: List[Tuple[float, float, float]],
        v_lines: List[Tuple[float, float, float]],
        page_rect: fitz.Rect
    ) -> List[fitz.Rect]:
        """
        Detect table regions from horizontal and vertical lines.

        A table is detected when we find a grid pattern of intersecting lines.
        """
        if len(h_lines) < 2 or len(v_lines) < 2:
            return []

        # Group lines by approximate position
        def cluster_lines(lines: List, pos_idx: int, tolerance: float = 5.0) -> List[List]:
            """Cluster lines by position."""
            if not lines:
                return []

            sorted_lines = sorted(lines, key=lambda x: x[pos_idx])
            clusters = []
            current_cluster = [sorted_lines[0]]

            for line in sorted_lines[1:]:
                if line[pos_idx] - current_cluster[-1][pos_idx] < tolerance:
                    current_cluster.append(line)
                else:
                    if len(current_cluster) >= 2:  # At least 2 lines in same row/col
                        clusters.append(current_cluster)
                    current_cluster = [line]

            if len(current_cluster) >= 2:
                clusters.append(current_cluster)

            return clusters

        # Find horizontal line clusters (table rows)
        h_clusters = cluster_lines(h_lines, 2)  # Group by y position

        # Find vertical line clusters (table columns)
        v_clusters = cluster_lines(v_lines, 0)  # Group by x position

        # A table requires at least 2 horizontal rows and 2 vertical columns
        if len(h_clusters) < 2 or len(v_clusters) < 2:
            return []

        # Find table bounding boxes
        tables = []

        # Simple heuristic: find rectangular regions bounded by lines
        for h_cluster in h_clusters:
            if len(h_cluster) < 2:
                continue

            # Get y bounds
            y_min = min(l[2] for l in h_cluster)
            y_max = max(l[2] for l in h_cluster)

            if y_max - y_min < 20:  # Too small
                continue

            # Find vertical lines within this y range
            relevant_v_lines = [
                v for v in v_lines
                if v[1] <= y_max and v[2] >= y_min
            ]

            if len(relevant_v_lines) < 2:
                continue

            x_min = min(v[0] for v in relevant_v_lines)
            x_max = max(v[0] for v in relevant_v_lines)

            if x_max - x_min < 50:  # Too narrow
                continue

            table_rect = fitz.Rect(x_min, y_min, x_max, y_max)

            # Check if this table overlaps with existing ones
            is_new = True
            for existing in tables:
                if table_rect.intersects(existing):
                    # Merge overlapping tables
                    existing.include_rect(table_rect)
                    is_new = False
                    break

            if is_new:
                tables.append(table_rect)

        return tables

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
                # Check for footnote indicators
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        size = span.get("size", 12)
                        if size < 9 or re.match(r'^[\d\*\†\‡]+\s', text):
                            complexity.has_footnotes = True
                            break

        # Check for headers/footers (repeated text patterns at top/bottom)
        for block in text_blocks:
            bbox = block.get("bbox", (0, 0, 0, 0))
            if bbox[1] < page_height * 0.08 or bbox[3] > page_height * 0.92:
                complexity.has_headers_footers = True
                break

    def _analyze_drawings(self, page: fitz.Page, complexity: PageComplexity) -> None:
        """Analyze vector drawings on the page."""
        drawings = page.get_drawings()

        # Count non-table drawings (excluding simple lines for tables)
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

    def _analyze_layout(self, page: fitz.Page, complexity: PageComplexity) -> None:
        """Analyze page layout for multi-column detection."""
        text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        blocks = text_dict.get("blocks", [])
        text_blocks = [b for b in blocks if b.get("type") == 0]

        if len(text_blocks) < 3:
            complexity.column_count = 1
            return

        # Get x-positions of text blocks
        x_positions = []
        for block in text_blocks:
            bbox = block.get("bbox", (0, 0, 0, 0))
            x_positions.append(bbox[0])  # Left edge

        # Cluster x-positions to detect columns
        if not x_positions:
            complexity.column_count = 1
            return

        x_positions.sort()
        page_width = page.rect.width

        # Use a tolerance based on page width
        tolerance = page_width * 0.1  # 10% of page width

        columns = []
        current_col = [x_positions[0]]

        for x in x_positions[1:]:
            if x - current_col[-1] < tolerance:
                current_col.append(x)
            else:
                columns.append(current_col)
                current_col = [x]

        columns.append(current_col)

        complexity.column_count = len(columns)
        complexity.has_multi_column = complexity.column_count > 1

    def _calculate_complexity(self, complexity: PageComplexity) -> None:
        """Calculate overall complexity score and determine routing."""
        score = 0
        t = self.thresholds

        # Table scoring (highest weight)
        if complexity.table_count >= t.table_count_complex:
            score += 4
            complexity.add_reason(f"{complexity.table_count} tables detected")
        elif complexity.table_count >= t.table_count_moderate:
            score += 2
            complexity.add_reason(f"{complexity.table_count} table detected")

        # Image scoring
        if complexity.image_count >= t.image_count_complex:
            score += 3
            complexity.add_reason(f"{complexity.image_count} images detected")
        elif complexity.image_count >= t.image_count_moderate:
            score += 1

        # Image area coverage
        if complexity.image_area_pct >= t.image_area_threshold:
            score += 2
            complexity.add_reason(f"{complexity.image_area_pct*100:.0f}% image coverage")

        # Multi-column layout
        if complexity.column_count >= t.column_count_complex:
            score += 2
            complexity.add_reason(f"{complexity.column_count}-column layout")
        elif complexity.has_multi_column:
            score += 1

        # Complex drawings (diagrams, charts)
        if complexity.drawing_count > 3:
            score += 2
            complexity.add_reason(f"{complexity.drawing_count} complex drawings")
        elif complexity.drawing_count > 0:
            score += 1

        # Footnotes add some complexity
        if complexity.has_footnotes:
            score += 1
            complexity.add_reason("has footnotes")

        # Rotated content is complex
        if complexity.has_rotated_content:
            score += 2
            complexity.add_reason("rotated content")

        # Low text density might indicate mostly images
        if complexity.char_count < t.min_chars_per_page and complexity.image_count > 0:
            score += 1
            complexity.add_reason("low text density")

        # Store complexity score
        complexity.complexity_score = score

        # Determine complexity level
        if score >= 6:
            complexity.complexity_level = ComplexityLevel.HIGHLY_COMPLEX
        elif score >= t.mixed_content_score_complex:
            complexity.complexity_level = ComplexityLevel.COMPLEX
        elif score >= 2:
            complexity.complexity_level = ComplexityLevel.MODERATE
        else:
            complexity.complexity_level = ComplexityLevel.SIMPLE

        # Routing decision: Complex and Highly Complex -> AI pipeline
        complexity.is_complex = complexity.complexity_level in (
            ComplexityLevel.COMPLEX,
            ComplexityLevel.HIGHLY_COMPLEX
        )
        complexity.route_to_ai = complexity.is_complex

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
        default=2,
        help="Number of tables to consider page complex (default: 2)"
    )
    parser.add_argument(
        "--image-threshold",
        type=int,
        default=4,
        help="Number of images to consider page complex (default: 4)"
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
