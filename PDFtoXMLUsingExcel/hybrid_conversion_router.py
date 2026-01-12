#!/usr/bin/env python3
"""
Hybrid Conversion Router for PDF to XML Conversion (v2.0)

This module routes PDF pages between AI and non-AI conversion pipelines
based on page complexity analysis. Complex pages (with tables, images,
complex layouts) go to the AI pipeline for better accuracy, while simple
text-only pages use the faster non-AI pipeline.

Architecture:
    1. Analyze PDF -> Determine complexity per page
    2. Route complex pages -> AI Pipeline (Claude Vision)
    3. Route simple pages -> Non-AI Pipeline (PyMuPDF text extraction)
    4. Merge results -> Unified DocBook XML output

Usage:
    from hybrid_conversion_router import HybridConversionRouter

    router = HybridConversionRouter(config)
    result = router.convert_pdf("document.pdf", output_dir="./output")
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

# PyMuPDF for PDF processing
try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

# Anthropic API for AI conversion
try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

# Import complexity analyzer
from page_complexity_analyzer import (
    PageComplexityAnalyzer,
    PDFComplexityReport,
    ComplexityThresholds,
    ComplexityLevel,
)


@dataclass
class HybridConfig:
    """Configuration for hybrid conversion routing."""
    # Complexity analysis settings
    complexity_thresholds: ComplexityThresholds = field(default_factory=ComplexityThresholds)

    # AI pipeline settings
    ai_model: str = "claude-sonnet-4-20250514"
    ai_dpi: int = 300
    ai_temperature: float = 0.0
    ai_max_tokens: int = 8192
    ai_batch_size: int = 10

    # Non-AI pipeline settings
    nonai_preserve_formatting: bool = True
    nonai_extract_images: bool = True

    # Routing behavior
    force_ai_pages: List[int] = field(default_factory=list)  # Always use AI for these pages
    force_nonai_pages: List[int] = field(default_factory=list)  # Always use non-AI for these pages
    ai_fallback_enabled: bool = True  # Fall back to AI if non-AI fails

    # Output settings
    merge_strategy: str = "sequential"  # sequential, interleave
    preserve_page_order: bool = True
    create_merged_xml: bool = True

    # Performance settings
    parallel_nonai: bool = True
    max_workers: int = 4

    # Verbose output
    verbose: bool = True


@dataclass
class PageConversionResult:
    """Result of converting a single page."""
    page_num: int
    pipeline: str  # "ai" or "nonai"
    success: bool
    content: str  # Markdown or XML content
    tables: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    error: Optional[str] = None
    confidence: float = 1.0


@dataclass
class HybridConversionResult:
    """Result of hybrid PDF conversion."""
    pdf_path: str
    output_dir: str
    success: bool

    # Routing statistics
    total_pages: int = 0
    ai_pages: List[int] = field(default_factory=list)
    nonai_pages: List[int] = field(default_factory=list)

    # Conversion results
    page_results: Dict[int, PageConversionResult] = field(default_factory=dict)

    # Output files
    merged_xml_path: Optional[str] = None
    merged_md_path: Optional[str] = None
    complexity_report_path: Optional[str] = None

    # Error tracking
    failed_pages: List[int] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        """Generate a summary of the conversion."""
        lines = [
            f"Hybrid Conversion Result: {Path(self.pdf_path).name}",
            f"{'=' * 60}",
            f"Total pages: {self.total_pages}",
            f"AI pipeline: {len(self.ai_pages)} pages",
            f"Non-AI pipeline: {len(self.nonai_pages)} pages",
            f"Failed: {len(self.failed_pages)} pages",
            f"Success: {'Yes' if self.success else 'No'}",
        ]
        if self.merged_xml_path:
            lines.append(f"Output XML: {self.merged_xml_path}")
        if self.errors:
            lines.append(f"Errors: {len(self.errors)}")
        return "\n".join(lines)


# AI Extraction Prompt (simplified version for hybrid mode)
AI_EXTRACTION_PROMPT = """You are a precise document text extractor. Extract ALL text content from this PDF page image exactly as it appears.

## RULES:
1. Extract text with exact formatting (indentation, bullets, lists)
2. Preserve special characters, superscripts, subscripts
3. For multi-column layouts, read left-to-right, top-to-bottom
4. Mark column breaks with: <!-- COLUMN_BREAK -->
5. Include ALL footnotes at the bottom
6. Do NOT add, edit, or improve any text

## TABLES:
For tables, use HTML format:
<!-- TABLE_START -->
<table>
  <thead><tr><th>Header</th></tr></thead>
  <tbody><tr><td>Cell</td></tr></tbody>
</table>
<!-- TABLE_END -->

## IMAGES:
Mark image positions with: <!-- IMAGE: description -->

## OUTPUT FORMAT:
Use Markdown with font size annotations for headings:
# Heading <!-- font:24 -->
## Subheading <!-- font:18 -->

Regular paragraph text...

- Bullet items
1. Numbered items

Output ONLY the extracted content, no explanations.
"""


class NonAIPageConverter:
    """
    Non-AI page converter using PyMuPDF for text extraction.

    This is a fast converter for pages with straightforward
    text content. It extracts text with basic formatting preservation
    but doesn't handle complex tables or layouts well.
    """

    def __init__(self, config: HybridConfig):
        if not HAS_FITZ:
            raise ImportError("PyMuPDF (fitz) is required")
        self.config = config

    def convert_page(
        self,
        pdf_path: Path,
        page_num: int,
        output_dir: Path
    ) -> PageConversionResult:
        """
        Convert a single page using non-AI extraction.

        Args:
            pdf_path: Path to PDF file
            page_num: 1-based page number
            output_dir: Output directory for extracted content

        Returns:
            PageConversionResult with extracted content
        """
        result = PageConversionResult(
            page_num=page_num,
            pipeline="nonai",
            success=False,
            content=""
        )

        try:
            doc = fitz.open(str(pdf_path))
            page = doc[page_num - 1]

            # Extract text with formatting
            content = self._extract_text_with_formatting(page, page_num)

            # Extract images if enabled
            if self.config.nonai_extract_images:
                images = self._extract_page_images(page, page_num, output_dir, doc)
                result.images = images

            doc.close()

            result.content = content
            result.success = True
            result.confidence = 0.85  # Non-AI has good confidence for simple text

        except Exception as e:
            result.error = str(e)
            result.success = False

        return result

    def _extract_text_with_formatting(self, page: fitz.Page, page_num: int) -> str:
        """Extract text from page - clean text without markdown artifacts."""
        lines = []

        # Get text as dictionary for detailed information
        text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        blocks = text_dict.get("blocks", [])

        for block in blocks:
            if block.get("type") != 0:  # Skip non-text blocks
                continue

            block_lines = []
            for line in block.get("lines", []):
                # Collect all spans with their formatting info
                spans_data = []
                line_size = 0

                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if not text:
                        continue
                    font = span.get("font", "").lower()
                    size = span.get("size", 12)
                    flags = span.get("flags", 0)

                    line_size = max(line_size, size)

                    # Detect formatting
                    is_bold = "bold" in font or (flags & 16)
                    is_italic = "italic" in font or "oblique" in font or (flags & 2)

                    spans_data.append({
                        "text": text,
                        "bold": is_bold,
                        "italic": is_italic
                    })

                # Build line text by merging consecutive spans with same formatting
                if not spans_data:
                    continue

                # Merge consecutive spans with same formatting to avoid **a****b** artifacts
                merged_spans = []
                current = spans_data[0].copy()
                
                for span in spans_data[1:]:
                    # If same formatting, merge text
                    if span["bold"] == current["bold"] and span["italic"] == current["italic"]:
                        current["text"] += span["text"]
                    else:
                        merged_spans.append(current)
                        current = span.copy()
                merged_spans.append(current)

                # Build the line with clean formatting
                line_parts = []
                for span in merged_spans:
                    text = span["text"]
                    # Only apply formatting to significant text (not just punctuation/whitespace)
                    text_content = text.strip()
                    if span["bold"] and span["italic"] and text_content and len(text_content) > 1:
                        text = f"***{text}***"
                    elif span["bold"] and text_content and len(text_content) > 1:
                        text = f"**{text}**"
                    elif span["italic"] and text_content and len(text_content) > 1:
                        text = f"*{text}*"
                    line_parts.append(text)

                line_text = "".join(line_parts).strip()

                # Clean up any residual markdown artifacts
                # Remove empty emphasis markers
                line_text = re.sub(r'\*{2,}(?=\s|$)', '', line_text)
                line_text = re.sub(r'(?:^|\s)\*{2,}', ' ', line_text)
                # Merge adjacent markers: **text****more** -> **text more**
                line_text = re.sub(r'\*\*\*\*+', ' ', line_text)
                line_text = re.sub(r'\*\*\s+\*\*', ' ', line_text)
                
                if line_text:
                    # Detect headings by font size
                    if line_size >= 18:
                        line_text = f"# {line_text}"
                    elif line_size >= 14:
                        line_text = f"## {line_text}"

                    block_lines.append(line_text)

            # Join lines within block
            block_text = "\n".join(block_lines)
            if block_text.strip():
                lines.append(block_text)
                lines.append("")  # Blank line between blocks

        return "\n".join(lines)

    def _extract_page_images(
        self,
        page: fitz.Page,
        page_num: int,
        output_dir: Path,
        doc: fitz.Document
    ) -> List[str]:
        """Extract images from a page."""
        images = page.get_images(full=True)
        extracted = []

        # Ensure output directory exists
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        for img_idx, img in enumerate(images, 1):
            try:
                xref = img[0]
                base_image = doc.extract_image(xref)
                if base_image and base_image.get("image"):
                    # Get image data
                    img_data = base_image["image"]
                    ext = base_image.get("ext", "png")
                    
                    # Skip tiny images (likely icons/decorations)
                    width = base_image.get("width", 0)
                    height = base_image.get("height", 0)
                    if width < 50 and height < 50:
                        continue
                    
                    filename = f"page{page_num}_img{img_idx}.{ext}"
                    filepath = output_dir / filename

                    with open(filepath, "wb") as f:
                        f.write(img_data)

                    extracted.append(str(filepath))
                    print(f"    Extracted image: {filename} ({width}x{height})")
            except Exception as e:
                print(f"    Warning: Could not extract image {img_idx} from page {page_num}: {e}")

        return extracted


class AIPageConverter:
    """
    AI page converter using Claude Vision API.

    This converter processes pages using the Claude Vision API
    for accurate text extraction from complex layouts.
    """

    def __init__(self, config: HybridConfig):
        if not HAS_ANTHROPIC:
            raise ImportError("anthropic package is required. Install with: pip install anthropic")

        self.config = config
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set")

        self.client = anthropic.Anthropic(api_key=api_key)

    def convert_page(
        self,
        pdf_path: Path,
        page_num: int,
        total_pages: int,
        output_dir: Path
    ) -> PageConversionResult:
        """
        Convert a single page using Claude Vision AI.

        Args:
            pdf_path: Path to PDF file
            page_num: 1-based page number
            total_pages: Total number of pages in PDF
            output_dir: Output directory

        Returns:
            PageConversionResult with extracted content
        """
        result = PageConversionResult(
            page_num=page_num,
            pipeline="ai",
            success=False,
            content=""
        )

        try:
            # Render page as image
            image_data = self._render_page(pdf_path, page_num)
            if image_data is None:
                result.error = f"Failed to render page {page_num}"
                return result

            # Call Claude Vision API
            content = self._call_vision_api(image_data, page_num, total_pages)

            result.content = content
            result.success = True
            result.confidence = 0.95

            # Extract tables from content
            result.tables = self._extract_tables_from_content(content)

        except anthropic.AuthenticationError as e:
            result.error = f"Authentication error: {e}"
        except anthropic.APIError as e:
            result.error = f"API error: {e}"
        except Exception as e:
            result.error = str(e)

        return result

    # Maximum image size for Claude API (5MB limit is for base64-encoded image)
    # Base64 encoding adds ~33% overhead, so we target 3.7MB raw to stay under 5MB encoded
    MAX_IMAGE_SIZE = int(3.7 * 1024 * 1024)  # ~3.7MB raw = ~5MB base64
    # DPI levels to try when image is too large
    FALLBACK_DPI_LEVELS = [200, 150, 120, 100, 75]

    def _render_page(self, pdf_path: Path, page_num: int) -> Optional[bytes]:
        """
        Render a PDF page as PNG image.
        
        Automatically reduces DPI if the image exceeds Claude's 5MB limit.
        """
        try:
            doc = fitz.open(str(pdf_path))
            page = doc[page_num - 1]
            rotation = page.rotation

            # Start with configured DPI
            current_dpi = self.config.ai_dpi
            image_data = self._render_at_dpi(page, current_dpi, rotation)
            
            # Check if image is too large, reduce DPI if needed
            if len(image_data) > self.MAX_IMAGE_SIZE:
                print(f"    Page {page_num}: Image too large ({len(image_data)/1024/1024:.1f}MB), reducing quality...")
                
                for fallback_dpi in self.FALLBACK_DPI_LEVELS:
                    if fallback_dpi >= current_dpi:
                        continue
                    
                    image_data = self._render_at_dpi(page, fallback_dpi, rotation)
                    
                    if len(image_data) <= self.MAX_IMAGE_SIZE:
                        print(f"    Page {page_num}: Using {fallback_dpi} DPI ({len(image_data)/1024/1024:.1f}MB)")
                        break
                else:
                    # If still too large, try JPEG compression
                    print(f"    Page {page_num}: Trying JPEG compression...")
                    image_data = self._render_as_jpeg(page, 100, rotation, quality=85)
                    
                    if len(image_data) > self.MAX_IMAGE_SIZE:
                        # Last resort: lower quality JPEG
                        image_data = self._render_as_jpeg(page, 100, rotation, quality=70)
                        print(f"    Page {page_num}: Using low-quality JPEG ({len(image_data)/1024/1024:.1f}MB)")

            doc.close()
            return image_data

        except Exception as e:
            print(f"  Error rendering page {page_num}: {e}")
            return None
    
    def _render_at_dpi(self, page: "fitz.Page", dpi: int, rotation: int) -> bytes:
        """Render page at specific DPI as PNG."""
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        
        if rotation:
            mat = mat.prerotate(-rotation)
        
        pix = page.get_pixmap(matrix=mat)
        return pix.tobytes("png")
    
    def _render_as_jpeg(self, page: "fitz.Page", dpi: int, rotation: int, quality: int = 85) -> bytes:
        """Render page as JPEG with compression."""
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        
        if rotation:
            mat = mat.prerotate(-rotation)
        
        pix = page.get_pixmap(matrix=mat)
        return pix.tobytes("jpeg", quality)

    def _call_vision_api(
        self,
        image_data: bytes,
        page_num: int,
        total_pages: int
    ) -> str:
        """Call Claude Vision API to extract content."""
        # Encode image to base64
        image_base64 = base64.b64encode(image_data).decode('utf-8')
        
        # Detect image type from magic bytes
        if image_data[:3] == b'\xff\xd8\xff':
            media_type = "image/jpeg"
        else:
            media_type = "image/png"

        # Build prompt
        prompt = AI_EXTRACTION_PROMPT.replace("{PAGE_NUMBER}", str(page_num))
        prompt += f"\n\nThis is page {page_num} of {total_pages}."

        response = self.client.messages.create(
            model=self.config.ai_model,
            max_tokens=self.config.ai_max_tokens,
            temperature=self.config.ai_temperature,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_base64
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }]
        )

        return response.content[0].text

    def _extract_tables_from_content(self, content: str) -> List[str]:
        """Extract HTML tables from content."""
        tables = []
        pattern = r'<!-- TABLE_START -->(.*?)<!-- TABLE_END -->'
        matches = re.findall(pattern, content, re.DOTALL)
        for match in matches:
            tables.append(match.strip())
        return tables


class HybridConversionRouter:
    """
    Routes PDF pages between AI and non-AI conversion pipelines.

    This is the main class for hybrid conversion. It:
    1. Analyzes page complexity
    2. Routes pages to appropriate pipeline
    3. Coordinates parallel processing
    4. Merges results into unified output
    """

    def __init__(self, config: Optional[HybridConfig] = None):
        """
        Initialize the hybrid conversion router.

        Args:
            config: Optional configuration (uses defaults if not provided)
        """
        if not HAS_FITZ:
            raise ImportError("PyMuPDF (fitz) is required")

        self.config = config or HybridConfig()
        self.analyzer = PageComplexityAnalyzer(
            thresholds=self.config.complexity_thresholds,
            verbose=self.config.verbose
        )
        self.nonai_converter = NonAIPageConverter(self.config)

        # Initialize AI converter only if API key is available
        self.ai_converter = None
        if os.environ.get("ANTHROPIC_API_KEY"):
            try:
                self.ai_converter = AIPageConverter(self.config)
            except Exception as e:
                if self.config.verbose:
                    print(f"  Warning: AI converter not available: {e}")

    def convert_pdf(
        self,
        pdf_path: str | Path,
        output_dir: str | Path,
        multimedia_dir: Optional[str | Path] = None
    ) -> HybridConversionResult:
        """
        Convert a PDF using hybrid routing.

        Args:
            pdf_path: Path to input PDF
            output_dir: Directory for output files
            multimedia_dir: Optional directory for multimedia files

        Returns:
            HybridConversionResult with conversion details
        """
        pdf_path = Path(pdf_path).resolve()
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        if multimedia_dir:
            multimedia_dir = Path(multimedia_dir).resolve()
            multimedia_dir.mkdir(parents=True, exist_ok=True)
        else:
            multimedia_dir = output_dir / f"{pdf_path.stem}_MultiMedia"
            multimedia_dir.mkdir(parents=True, exist_ok=True)

        result = HybridConversionResult(
            pdf_path=str(pdf_path),
            output_dir=str(output_dir),
            success=False
        )

        if self.config.verbose:
            print(f"\n{'=' * 60}")
            print(f"HYBRID CONVERSION: {pdf_path.name}")
            print(f"{'=' * 60}")

        # Step 1: Analyze complexity
        if self.config.verbose:
            print(f"\nStep 1: Analyzing page complexity...")

        complexity_report = self.analyzer.analyze_pdf(pdf_path)
        result.total_pages = complexity_report.total_pages

        # Save complexity report
        report_path = output_dir / f"{pdf_path.stem}_complexity_report.json"
        self._save_complexity_report(complexity_report, report_path)
        result.complexity_report_path = str(report_path)

        # Step 2: Determine routing (with overrides)
        ai_pages, nonai_pages = self._determine_routing(complexity_report)
        result.ai_pages = ai_pages
        result.nonai_pages = nonai_pages

        if self.config.verbose:
            print(f"\nStep 2: Routing decision:")
            print(f"  AI pipeline: {len(ai_pages)} pages {ai_pages[:10]}{'...' if len(ai_pages) > 10 else ''}")
            print(f"  Non-AI pipeline: {len(nonai_pages)} pages {nonai_pages[:10]}{'...' if len(nonai_pages) > 10 else ''}")

        # Step 3: Convert non-AI pages
        if nonai_pages:
            if self.config.verbose:
                print(f"\nStep 3: Converting {len(nonai_pages)} pages with non-AI pipeline...")

            nonai_results = self._convert_nonai_pages(pdf_path, nonai_pages, multimedia_dir)
            result.page_results.update(nonai_results)

            # Check for failures and potentially fall back to AI
            for page_num, page_result in nonai_results.items():
                if not page_result.success and self.config.ai_fallback_enabled:
                    if self.config.verbose:
                        print(f"  Page {page_num} failed, marking for AI fallback...")
                    if page_num not in ai_pages:
                        ai_pages.append(page_num)
                        if page_num in nonai_pages:
                            nonai_pages.remove(page_num)

        # Step 4: Convert AI pages
        if ai_pages:
            if self.config.verbose:
                print(f"\nStep 4: Converting {len(ai_pages)} pages with AI pipeline...")

            if self.ai_converter is None:
                if self.config.verbose:
                    print("  Warning: AI converter not available (API key not set)")
                    print("  AI pages will be skipped or use non-AI fallback")

                # Try non-AI fallback for AI pages
                for page_num in ai_pages:
                    if page_num not in result.page_results:
                        fallback_result = self.nonai_converter.convert_page(
                            pdf_path, page_num, multimedia_dir
                        )
                        fallback_result.pipeline = "nonai-fallback"
                        result.page_results[page_num] = fallback_result
            else:
                ai_results = self._convert_ai_pages(
                    pdf_path, ai_pages, output_dir, multimedia_dir
                )
                result.page_results.update(ai_results)

        # Step 5: Merge results
        if self.config.verbose:
            print(f"\nStep 5: Merging results...")

        merged_content = self._merge_results(result.page_results, complexity_report.total_pages)

        # Save merged output
        md_path = output_dir / f"{pdf_path.stem}_hybrid_intermediate.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(merged_content)
        result.merged_md_path = str(md_path)

        # Convert to DocBook XML
        if self.config.create_merged_xml:
            xml_path = output_dir / f"{pdf_path.stem}_hybrid_docbook42.xml"
            # Collect all extracted images from page results
            all_images = []
            for page_num in sorted(result.page_results.keys()):
                page_result = result.page_results[page_num]
                if page_result.images:
                    all_images.extend(page_result.images)
            
            self._convert_to_docbook(merged_content, xml_path, pdf_path.stem, all_images)
            result.merged_xml_path = str(xml_path)

        # Collect failed pages
        result.failed_pages = [
            page_num for page_num, page_result in result.page_results.items()
            if not page_result.success
        ]

        result.success = len(result.failed_pages) == 0

        if self.config.verbose:
            print(f"\n{result.summary()}")

        return result

    def _determine_routing(
        self,
        report: PDFComplexityReport
    ) -> Tuple[List[int], List[int]]:
        """
        Determine which pages go to which pipeline.

        Applies force overrides from config.
        """
        ai_pages = list(report.ai_route_pages)
        nonai_pages = list(report.non_ai_route_pages)

        # Apply force overrides
        for page in self.config.force_ai_pages:
            if page in nonai_pages:
                nonai_pages.remove(page)
            if page not in ai_pages and 1 <= page <= report.total_pages:
                ai_pages.append(page)

        for page in self.config.force_nonai_pages:
            if page in ai_pages:
                ai_pages.remove(page)
            if page not in nonai_pages and 1 <= page <= report.total_pages:
                nonai_pages.append(page)

        # Sort for consistent ordering
        ai_pages.sort()
        nonai_pages.sort()

        return ai_pages, nonai_pages

    def _convert_nonai_pages(
        self,
        pdf_path: Path,
        pages: List[int],
        output_dir: Path
    ) -> Dict[int, PageConversionResult]:
        """Convert pages using the non-AI pipeline."""
        results = {}

        if self.config.parallel_nonai:
            with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
                futures = {
                    executor.submit(
                        self.nonai_converter.convert_page,
                        pdf_path,
                        page_num,
                        output_dir
                    ): page_num
                    for page_num in pages
                }

                for future in as_completed(futures):
                    page_num = futures[future]
                    try:
                        result = future.result()
                        results[page_num] = result
                    except Exception as e:
                        results[page_num] = PageConversionResult(
                            page_num=page_num,
                            pipeline="nonai",
                            success=False,
                            content="",
                            error=str(e)
                        )
        else:
            for page_num in pages:
                result = self.nonai_converter.convert_page(pdf_path, page_num, output_dir)
                results[page_num] = result

        return results

    def _convert_ai_pages(
        self,
        pdf_path: Path,
        pages: List[int],
        output_dir: Path,
        multimedia_dir: Path
    ) -> Dict[int, PageConversionResult]:
        """Convert pages using the AI pipeline."""
        results = {}

        # Get total page count and keep doc open for image extraction
        doc = fitz.open(str(pdf_path))
        total_pages = len(doc)

        # Process each page
        for page_num in pages:
            if self.config.verbose:
                print(f"  Processing page {page_num} with AI...")

            try:
                result = self.ai_converter.convert_page(
                    pdf_path, page_num, total_pages, multimedia_dir
                )
                
                # Also extract embedded images from AI pages
                if result.success:
                    page = doc[page_num - 1]
                    images = self._extract_ai_page_images(page, page_num, multimedia_dir, doc)
                    if images:
                        result.images = images
                        if self.config.verbose:
                            print(f"    Extracted {len(images)} images from page {page_num}")
                
                results[page_num] = result

                if not result.success:
                    print(f"  Error extracting page {page_num}: {result.error}")

            except Exception as e:
                results[page_num] = PageConversionResult(
                    page_num=page_num,
                    pipeline="ai",
                    success=False,
                    content="",
                    error=str(e)
                )
                print(f"  Error extracting page {page_num}: {e}")

        doc.close()
        return results

    def _extract_ai_page_images(
        self,
        page: fitz.Page,
        page_num: int,
        output_dir: Path,
        doc: fitz.Document
    ) -> List[str]:
        """Extract images from an AI-processed page."""
        images = page.get_images(full=True)
        extracted = []

        # Ensure output directory exists
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        for img_idx, img in enumerate(images, 1):
            try:
                xref = img[0]
                base_image = doc.extract_image(xref)
                if base_image and base_image.get("image"):
                    img_data = base_image["image"]
                    ext = base_image.get("ext", "png")
                    
                    # Skip tiny images
                    width = base_image.get("width", 0)
                    height = base_image.get("height", 0)
                    if width < 50 and height < 50:
                        continue
                    
                    filename = f"page{page_num}_img{img_idx}.{ext}"
                    filepath = output_dir / filename

                    with open(filepath, "wb") as f:
                        f.write(img_data)

                    extracted.append(str(filepath))
            except Exception as e:
                if self.config.verbose:
                    print(f"    Warning: Could not extract image {img_idx}: {e}")

        return extracted

    def _merge_results(
        self,
        page_results: Dict[int, PageConversionResult],
        total_pages: int
    ) -> str:
        """Merge page results into unified content."""
        lines = []

        for page_num in range(1, total_pages + 1):
            result = page_results.get(page_num)

            if result is None:
                # Skip pages with no result
                continue

            if not result.success:
                # Skip failed pages
                continue

            # Add content directly without internal markers
            lines.append(result.content)

        return "\n".join(lines)

    def _convert_to_docbook(
        self,
        markdown_content: str,
        output_path: Path,
        title: str,
        extracted_images: List[str] = None
    ) -> None:
        """Convert merged markdown to DocBook XML."""
        extracted_images = extracted_images or []
        
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<!DOCTYPE book PUBLIC "-//OASIS//DTD DocBook XML V4.2//EN"',
            '  "http://www.oasis-open.org/docbook/xml/4.2/docbookx.dtd">',
            '<book>',
            '<bookinfo>',
            f'  <title>{self._escape_xml(title)}</title>',
            '</bookinfo>',
            '<chapter>',
            '  <title>Content</title>',
        ]
        
        # Track which images we've added
        images_added = set()

        # Convert markdown paragraphs to DocBook
        paragraphs = markdown_content.split("\n\n")
        in_section = False

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # Skip all internal comments (page markers, pipeline info, etc.)
            if para.startswith("<!--"):
                continue

            # Handle headings
            if para.startswith("# "):
                if in_section:
                    lines.append("  </sect1>")
                text = re.sub(r'\s*<!--.*?-->\s*', '', para[2:]).strip()
                lines.append(f"  <sect1><title>{self._escape_xml(text)}</title>")
                in_section = True
            elif para.startswith("## "):
                text = re.sub(r'\s*<!--.*?-->\s*', '', para[3:]).strip()
                lines.append(f"  <sect2><title>{self._escape_xml(text)}</title></sect2>")
            elif para.startswith("### "):
                text = re.sub(r'\s*<!--.*?-->\s*', '', para[4:]).strip()
                lines.append(f"  <sect3><title>{self._escape_xml(text)}</title></sect3>")
            elif para.startswith("<!-- IMAGE:"):
                # Convert image marker to DocBook mediaobject
                # Try to find an actual extracted image for this marker
                desc_match = re.search(r'<!-- IMAGE:\s*(.+?)\s*-->', para)
                desc = desc_match.group(1) if desc_match else "Image"
                desc = self._escape_xml(desc)
                
                # Find an unused extracted image
                image_to_use = None
                for img_path in extracted_images:
                    if img_path not in images_added:
                        image_to_use = img_path
                        images_added.add(img_path)
                        break
                
                if image_to_use:
                    img_filename = Path(image_to_use).name
                    lines.append(f"  <mediaobject>")
                    lines.append(f"    <imageobject>")
                    lines.append(f"      <imagedata fileref=\"MultiMedia/{img_filename}\"/>")
                    lines.append(f"    </imageobject>")
                    lines.append(f"    <textobject><phrase>{desc}</phrase></textobject>")
                    lines.append(f"  </mediaobject>")
            elif para.startswith("<!-- TABLE"):
                # Skip internal table markers (tables should be in HTML format)
                continue
            elif "<table" in para.lower():
                # Pass through HTML tables
                lines.append(f"  {para}")
            elif para.startswith("- ") or para.startswith("* "):
                # Convert bullet list
                items = [p.strip()[2:] for p in para.split("\n") if p.strip().startswith(("- ", "* "))]
                if items:
                    lines.append("  <itemizedlist>")
                    for item in items:
                        escaped_item = self._escape_xml_content(item)
                        lines.append(f"    <listitem><para>{escaped_item}</para></listitem>")
                    lines.append("  </itemizedlist>")
            elif re.match(r'^\d+\.\s', para):
                # Convert numbered list
                items = re.findall(r'^\d+\.\s*(.+)$', para, re.MULTILINE)
                if items:
                    lines.append("  <orderedlist>")
                    for item in items:
                        escaped_item = self._escape_xml_content(item)
                        lines.append(f"    <listitem><para>{escaped_item}</para></listitem>")
                    lines.append("  </orderedlist>")
            else:
                # Regular paragraph - clean up markdown formatting
                text = para
                text = re.sub(r'<!--.*?-->', '', text)  # Remove comments first
                text = text.strip()
                if text:
                    # Escape XML special characters BEFORE adding emphasis tags
                    text = self._escape_xml_content(text)
                    # Now convert markdown to XML emphasis tags
                    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<emphasis role="bold-italic">\1</emphasis>', text)
                    text = re.sub(r'\*\*(.+?)\*\*', r'<emphasis role="bold">\1</emphasis>', text)
                    text = re.sub(r'\*(.+?)\*', r'<emphasis>\1</emphasis>', text)
                    lines.append(f"  <para>{text}</para>")

        if in_section:
            lines.append("  </sect1>")

        # Add any remaining extracted images that weren't matched to IMAGE markers
        remaining_images = [img for img in extracted_images if img not in images_added]
        if remaining_images:
            lines.append("  <sect1><title>Images</title>")
            for img_path in remaining_images:
                img_filename = Path(img_path).name
                lines.append(f"  <mediaobject>")
                lines.append(f"    <imageobject>")
                lines.append(f"      <imagedata fileref=\"MultiMedia/{img_filename}\"/>")
                lines.append(f"    </imageobject>")
                lines.append(f"  </mediaobject>")
            lines.append("  </sect1>")

        lines.extend([
            '</chapter>',
            '</book>',
        ])

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _escape_xml(self, text: str) -> str:
        """Escape special XML characters for use in XML attributes and simple content."""
        # Don't escape if already contains XML tags (for backwards compatibility)
        if "<emphasis" in text or "<para" in text:
            return text
        return self._escape_xml_content(text)
    
    def _escape_xml_content(self, text: str) -> str:
        """
        Escape special XML characters in text content.
        
        Always escapes, regardless of existing XML tags.
        Use this for text that will have XML tags added AFTER escaping.
        """
        # Escape & first (before other replacements that create &)
        # But don't double-escape already escaped entities
        # First, temporarily replace already-escaped entities
        text = re.sub(r'&amp;', '\x00AMP\x00', text)
        text = re.sub(r'&lt;', '\x00LT\x00', text)
        text = re.sub(r'&gt;', '\x00GT\x00', text)
        text = re.sub(r'&quot;', '\x00QUOT\x00', text)
        text = re.sub(r'&apos;', '\x00APOS\x00', text)
        
        # Now escape remaining special characters
        text = text.replace("&", "&amp;")
        text = text.replace("<", "&lt;")
        text = text.replace(">", "&gt;")
        text = text.replace('"', "&quot;")
        
        # Restore already-escaped entities
        text = text.replace('\x00AMP\x00', '&amp;')
        text = text.replace('\x00LT\x00', '&lt;')
        text = text.replace('\x00GT\x00', '&gt;')
        text = text.replace('\x00QUOT\x00', '&quot;')
        text = text.replace('\x00APOS\x00', '&apos;')
        
        return text

    def _save_complexity_report(
        self,
        report: PDFComplexityReport,
        output_path: Path
    ) -> None:
        """Save complexity report to JSON."""
        data = {
            "pdf_path": report.pdf_path,
            "total_pages": report.total_pages,
            "routing": {
                "ai_pages": report.ai_route_pages,
                "non_ai_pages": report.non_ai_route_pages,
            },
            "classification": {
                "simple": report.simple_pages,
                "moderate": report.moderate_pages,
                "complex": report.complex_pages,
                "highly_complex": report.highly_complex_pages,
            },
            "statistics": {
                "total_tables": report.total_tables,
                "total_images": report.total_images,
                "avg_complexity_score": report.avg_complexity_score,
                "max_tables_per_page": report.max_tables_per_page,
                "max_images_per_page": report.max_images_per_page,
            },
            "page_details": {
                str(page_num): {
                    "complexity_level": complexity.complexity_level.value,
                    "route_to_ai": complexity.route_to_ai,
                    "table_count": complexity.table_count,
                    "image_count": complexity.image_count,
                    "text_block_count": complexity.text_block_count,
                    "column_count": complexity.column_count,
                    "complexity_score": complexity.complexity_score,
                    "reasons": complexity.complexity_reasons,
                }
                for page_num, complexity in report.page_results.items()
            }
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def main():
    """CLI entry point for hybrid conversion."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert PDF using hybrid AI/non-AI routing"
    )
    parser.add_argument("pdf", help="Path to PDF file")
    parser.add_argument("--out", "-o", default="output", help="Output directory")
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Claude model for AI conversion"
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="DPI for page rendering"
    )
    parser.add_argument(
        "--force-ai",
        type=str,
        default="",
        help="Comma-separated page numbers to force AI processing"
    )
    parser.add_argument(
        "--force-nonai",
        type=str,
        default="",
        help="Comma-separated page numbers to force non-AI processing"
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress verbose output"
    )

    args = parser.parse_args()

    # Parse force lists
    force_ai = []
    if args.force_ai:
        force_ai = [int(p.strip()) for p in args.force_ai.split(",") if p.strip()]

    force_nonai = []
    if args.force_nonai:
        force_nonai = [int(p.strip()) for p in args.force_nonai.split(",") if p.strip()]

    # Configure
    config = HybridConfig(
        ai_model=args.model,
        ai_dpi=args.dpi,
        force_ai_pages=force_ai,
        force_nonai_pages=force_nonai,
        verbose=not args.quiet,
    )

    # Run conversion
    router = HybridConversionRouter(config)
    result = router.convert_pdf(args.pdf, args.out)

    # Exit code
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
