#!/usr/bin/env python3
"""
PDF -> DocBook XML 4.2 (via Claude Vision API - Page-by-Page Processing)

Key properties:
- Uses Claude Vision API to process each PDF page as a high-DPI image
- Extracts text with exact formatting (indentation, bullets, super/subscripts)
- Handles tables with rotation detection and HTML output
- Creates intermediate Markdown, then converts to DocBook 4.2
- Temperature 0.0 for exact transcription (no creativity)

Requirements:
- pip install anthropic pymupdf
- ANTHROPIC_API_KEY environment variable
"""

from __future__ import annotations

import os
import re
import json
import base64
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set
from html.parser import HTMLParser
import io

import anthropic

# PyMuPDF for rendering PDF pages as images
try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False
    print("ERROR: PyMuPDF (fitz) is required. Install with: pip install pymupdf")


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class VisionConfig:
    """Configuration for Vision AI PDF processing."""
    model: str = "claude-sonnet-4-20250514"  # Claude Sonnet 4 for vision
    dpi: int = 300  # High DPI for better text recognition
    temperature: float = 0.0  # No creativity - exact transcription
    max_tokens: int = 8192  # Max tokens per page response
    confidence_threshold: float = 0.85  # Trigger 2nd pass below this
    enable_second_pass: bool = True  # Enable 2nd pass for low confidence
    # Batch processing for large PDFs
    batch_size: int = 10  # Pages per batch
    save_intermediate: bool = True  # Save progress after each batch
    resume_from_page: int = 1  # Resume from specific page (for crash recovery)
    parallel_workers: int = 1  # Number of parallel API calls (be careful with rate limits)
    # Header/footer cropping - crop these areas before sending to AI
    crop_header_pct: float = 0.06  # Crop top 6% (header area)
    crop_footer_pct: float = 0.06  # Crop bottom 6% (footer area)


# API limits
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB - Anthropic API limit for images
# DPI levels to try when image is too large (from highest to lowest quality)
FALLBACK_DPI_LEVELS = [200, 150, 100]


# =============================================================================
# PROMPTS
# =============================================================================

TEXT_EXTRACTION_PROMPT = """You are a precise document text extractor. Analyze this PDF page image and extract ALL text content exactly as it appears.

## HEADER/FOOTER DETECTION

First, identify and EXCLUDE these elements (do NOT include in output):
- Running headers (repeated text at top of pages, often chapter/section names)
- Running footers (repeated text at bottom of pages)
- Page numbers (usually at top or bottom corners/center)
- Watermarks
- Copyright notices repeated on every page

## TEXT EXTRACTION RULES

Extract ALL remaining text with EXACT fidelity:

1. **Preserve formatting exactly**:
   - Indentation (use spaces to represent indentation levels)
   - Bullet points (use •, -, *, or the actual symbol shown)
   - Numbered lists (preserve exact numbering: 1., 1), (1), a., etc.)
   - Line breaks within paragraphs
   - Paragraph breaks (use blank lines)

2. **Preserve special characters**:
   - Superscripts: wrap in <superscript>text</superscript>
   - Subscripts: wrap in <subscript>text</subscript>
   - Special symbols: ©, ®, ™, °, ±, ×, ÷, →, ←, etc.
   - Mathematical symbols and Greek letters
   - Currency symbols
   - Fractions (½, ¼, etc.)

3. **Handle multi-column layouts**:
   - Read columns left-to-right, top-to-bottom
   - Separate columns with a clear marker: <!-- COLUMN BREAK -->
   - Preserve the reading order as a human would read it

4. **Handle small text (footnotes, citations)**:
   - Include ALL footnotes at the bottom of the page content
   - Mark footnotes with: <!-- FOOTNOTE: N --> where N is the footnote number
   - Preserve citation formatting exactly

5. **DO NOT**:
   - Add any content not present in the image
   - Edit, correct, or improve any text
   - Summarize or paraphrase anything
   - Skip any text, no matter how small
   - Change any formatting or structure

6. **FIGURES, DIAGRAMS, AND IMAGES WITH TEXT**:
   - DO NOT extract text labels, annotations, or captions that are INSIDE a figure, diagram, chart, flowchart, anatomical illustration, or any graphical element
   - Text that is visually part of an image/figure (labels pointing to parts, axis labels, legend text within the figure boundaries) should NOT be extracted as separate paragraphs
   - ONLY extract the figure TITLE/CAPTION that appears OUTSIDE the figure (usually above or below it, like "Figure 1.2 Anatomical diagram of...")
   - Mark the figure position with: <!-- IMAGE: brief_description -->
   - The figure's internal text/labels are part of the image itself and will be preserved in the image file
   - Example: For an anatomical diagram with labels like "Tonsil (C09.9)", DO NOT extract those labels as text - they are part of the figure

## OUTPUT FORMAT

Output the extracted text in Markdown format:
- For headings, use markdown heading syntax WITH font size annotation:
  - Format: # Heading Text <!-- font:SIZE --> where SIZE is approximate pt size (e.g., 24, 18, 14)
  - Use # for largest headings on this page
  - Use ## for next smaller headings
  - Use ### for even smaller headings
  - IMPORTANT: Include the font size comment on the SAME LINE as the heading
  - Example: # Chapter 1 Introduction <!-- font:24 -->
  - Example: ## Section 1.1 Background <!-- font:18 -->
- Use **bold** for bold text
- Use *italic* for italic text
- Use `code` for monospace/code text
- Preserve lists as markdown lists
- For images/figures in the text, mark position with: <!-- IMAGE: description -->

If this page contains a TABLE, output:
<!-- TABLE DETECTED - SEE TABLE EXTRACTION -->

Then provide the table separately after the text content.

Begin extraction now. Output ONLY the extracted content, no explanations."""


TABLE_EXTRACTION_PROMPT = """You are a precise document table extractor. Analyze this PDF page image and extract ALL tables present.

## HANDLING ROTATED/LANDSCAPE TABLES

Before extracting, check for rotated content:
- **90° clockwise rotation**: Text reads from bottom-to-top on the left side
- **90° counter-clockwise rotation**: Text reads from top-to-bottom on the right side
- **180° rotation**: Text appears upside down
- **Landscape tables on portrait pages**: Wide tables rotated to fit the page

**If you detect a rotated table:**
1. Mentally rotate the content to its correct reading orientation
2. Identify the TRUE header row (typically at the top after mental rotation)
3. Extract content in the correct logical reading order (left-to-right, top-to-bottom after rotation)
4. Output the table in standard non-rotated HTML format

**Rotation detection clues:**
- Text running vertically instead of horizontally
- Headers appearing on the left side instead of top
- Page numbers or footers appearing on the side
- Obvious landscape-format table squeezed onto portrait page

---

## TABLE EXTRACTION RULES

For each table found:

1. **CAPTURE TABLE CAPTION (CRITICAL)**:
   - ALWAYS look for table captions like "Table 1.", "Table 2.", "Table A.", "Table I." etc.
   - Captions typically appear ABOVE or BELOW the table (e.g., "Table 2. M_Z recovery fractions at different multiples of T_1 times")
   - Include the FULL caption text in the output comment: <!-- Table: Table 2. M_Z recovery fractions... -->
   - Captions may be rotated along with the table - look for them in the same orientation
   - If no caption is visible, use: <!-- Table: [No caption] -->

2. **Preserve exact structure**: Maintain all rows, columns, merged cells, and hierarchical relationships

3. **Capture ALL content**: Include:
   - Header rows (may span multiple lines)
   - All data rows
   - Footer rows (like source citations, notes)
   - Cells that span multiple columns (colspan)
   - Cells that span multiple rows (rowspan)
   - Indented subcategories (e.g., N1a, N1b under N1)
   - Nested tables within cells (if present)

4. **Output as clean HTML table** with:
   - Proper <thead> and <tbody> sections
   - colspan and rowspan attributes where needed
   - Preserve text formatting (bold, italic, superscripts, subscripts)
   - Include any footnotes or source citations as rows at the bottom

---

## CRITICAL RULES

- Do NOT summarize or skip any rows
- Do NOT merge separate tables into one
- Do NOT omit source/citation rows at the bottom of tables
- Preserve the exact text content - do not paraphrase
- If a cell contains a list, preserve the list structure
- For rotated tables, output in STANDARD orientation (headers on top, reading left-to-right)
- If table continues from previous page or continues to next page, extract what is visible and note it

---

## SPECIAL CASES

**Split tables (spanning multiple pages):**
- Add comment: `<!-- Table continues from previous page -->` or `<!-- Table continues on next page -->`

**Tables within tables:**
- Preserve nested structure using nested <table> tags

**Tables with vertical headers (not rotated, intentionally vertical):**
- Use `<th>` tags with appropriate scope attributes
- Preserve the vertical text orientation note in a comment

---

## OUTPUT FORMAT

For each table found on the page:
```html
<!-- Table: Table 2. M_Z recovery fractions at different multiples of T_1 times -->
<!-- Rotation: [None/90° CW/90° CCW/180°] -->
<table>
  <thead>
    <tr>
      <th>t/T1</th>
      <th>MZ</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>-1</td>
      <td>0.6321</td>
    </tr>
  </tbody>
</table>
```

IMPORTANT: The table caption (e.g., "Table 2. M_Z recovery fractions...") MUST be included in the <!-- Table: ... --> comment. Look for captions above or below the table.

If NO tables are found on this page, respond with:
```html
<!-- No tables found on this page -->
```

---

Extract all tables from this page now, handling any rotation as needed. Remember to capture the table caption!"""


COMBINED_EXTRACTION_PROMPT = """You are a precise document content extractor. Analyze this PDF page image and extract ALL content exactly as it appears.

## CRITICAL: ZERO HALLUCINATION POLICY

**ABSOLUTE RULES - VIOLATION IS UNACCEPTABLE:**
1. ONLY output text that is VISIBLY PRESENT in the image
2. If you cannot read a word clearly, use [ILLEGIBLE] - do NOT guess
3. If text is partially obscured, transcribe only what is visible
4. NEVER invent, assume, or fill in missing content
5. NEVER "complete" partial sentences or words
6. NEVER add explanatory text, context, or interpretations
7. If uncertain about a character, use the closest visible match or [?]
8. Do NOT correct spelling errors - transcribe exactly as shown
9. Do NOT fix grammatical errors - transcribe exactly as shown
10. Do NOT add punctuation that is not visible

## STEP 1: HEADER/FOOTER DETECTION

First, identify and EXCLUDE these elements (do NOT include in output):
- Running headers (repeated text at top of pages)
- Running footers (repeated text at bottom of pages)
- Page numbers
- Watermarks
- Repeated copyright notices

## STEP 2: TEXT EXTRACTION

Extract ALL text with EXACT fidelity:

**Formatting to preserve:**
- Indentation (use spaces)
- Bullet points: Preserve exact symbols (•, ○, ▪, ▸, -, *, etc.) followed by a space
- Numbered lists: Preserve exact format (1., 1), (1), a., a), (a), i., i), (i), etc.) followed by a space
- Line/paragraph breaks
- Superscripts: <superscript>text</superscript>
- Subscripts: <subscript>text</subscript>
- Special symbols: ©, ®, ™, °, ±, ×, ÷, →, ←, Greek letters, etc.
- Bold text: **bold text**
- Italic text: *italic text*

**MATHEMATICAL FORMULAS & SCIENTIFIC EQUATIONS - CRITICAL:**
Capture ALL math and scientific content EXACTLY as it appears:
- Greek letters: α, β, γ, δ, ε, θ, λ, μ, π, σ, Σ, Ω, etc.
- Mathematical operators: +, -, ×, ÷, =, ≠, <, >, ≤, ≥, ≈, ∝, ±
- Summation/Product: Σ, ∏ with limits as subscripts/superscripts
- Integrals: ∫ with limits
- Square roots: √ followed by content, or use √(content)
- Fractions: Use format numerator/denominator or (numerator)/(denominator)
- Exponents: Use <superscript> tags, e.g., x<superscript>2</superscript> for x²
- Subscripts: Use <subscript> tags, e.g., x<subscript>i</subscript> for xᵢ
- Chemical formulas: H<subscript>2</subscript>O, CO<subscript>2</subscript>, etc.
- Scientific notation: 6.022 × 10<superscript>23</superscript>
- Special math symbols: ∞, ∂, ∇, ∈, ∉, ⊂, ⊃, ∪, ∩, ∀, ∃
- Brackets/Parentheses: Preserve (), [], {}, ⟨⟩ exactly
- DO NOT convert formulas to LaTeX unless that's how they appear
- Preserve spacing within equations exactly as shown

**Multi-column layouts:**
- Read left-to-right, top-to-bottom
- Mark column breaks with: <!-- COLUMN_BREAK -->

**Small text (footnotes, citations):**
- Include ALL footnotes
- Mark with: [^N] for footnote reference, then list at bottom

**PROHIBITED ACTIONS (will cause rejection):**
- Adding content not visible in the image
- Editing, correcting, or improving text
- Summarizing or paraphrasing
- Skipping any visible text
- Guessing at unclear text
- Adding descriptions or explanations

**FIGURES, DIAGRAMS, AND IMAGES WITH TEXT - CRITICAL:**
- DO NOT extract text that is INSIDE a figure, diagram, chart, flowchart, anatomical illustration, schematic, or any graphical element
- Labels pointing to parts of a diagram (e.g., "Tonsil (C09.9)", "Valve A", "Step 1") are PART OF THE IMAGE - do not extract them as text
- Axis labels, legend text, and annotations within figure boundaries are PART OF THE IMAGE
- ONLY extract the figure TITLE/CAPTION that appears OUTSIDE the figure (e.g., "Figure 1.2 Anatomical sites of the oropharynx")
- The figure's internal labels will be preserved in the image file itself
- If in doubt: if text has lines/arrows pointing to parts of a graphic, it's part of the figure

## STEP 3: TABLE EXTRACTION

**CRITICAL TABLE RULES:**

1. ALWAYS use HTML table format - NEVER use markdown pipe tables (| col | col |)
2. Check for rotation first:
   - 90° CW: Text reads bottom-to-top on left
   - 90° CCW: Text reads top-to-bottom on right
   - 180°: Text upside down

3. HTML table structure (REQUIRED):
   - Wrap tables with <!-- TABLE_START --> and <!-- TABLE_END -->
   - Use <table>, <thead>, <tbody> tags
   - Use <th> for header cells, <td> for data cells
   - Use colspan/rowspan for merged cells
   - EVERY row must have same number of cells (use empty <td></td> for blanks)

4. **TABLE CAPTION/TITLE (CRITICAL)**:
   - ALWAYS look for table captions like "Table 1.", "Table 2.", "Table A." etc.
   - Captions typically appear ABOVE or BELOW the table (e.g., "Table 2. Results of the experiment")
   - Include the FULL caption text in the comment: <!-- Table: Table 2. Results of the experiment -->
   - Do NOT output the caption as a separate paragraph - it MUST be in the <!-- Table: ... --> comment
   - If no caption is visible, use: <!-- Table: [No caption] -->

5. Include footnotes, source citations as part of the table
6. Do NOT skip rows or summarize
7. Transcribe cell content EXACTLY as shown

## STEP 4: IMAGE POSITIONS

For images/figures, mark their position with:
<!-- IMAGE: p{PAGE_NUMBER}_img{K} -->
Do NOT add descriptions - only mark the position.

## OUTPUT FORMAT

IMPORTANT: Do NOT wrap output in code fences (``` or ```markdown). Output raw content directly.

**HEADING FORMAT WITH FONT SIZE:**
- For ALL headings, include approximate font size in points
- Format: # Heading Text <!-- font:SIZE --> (SIZE is approximate pt, e.g., 24, 18, 14, 12)
- This helps maintain consistent heading hierarchy across the entire document
- Example: # Chapter 1 Introduction <!-- font:24 -->
- Example: ## 1.1 Background <!-- font:18 -->
- Example: ### Overview <!-- font:14 -->

Start your output with the page marker, then content:

<!-- Page {PAGE_NUMBER} -->

# Heading <!-- font:XX --> (if present - exact text only, include font size)

Regular paragraph text exactly as shown...

- Bullet item (exact text)
- Another item

1. Numbered item (exact text)
2. Another

<!-- IMAGE: p{PAGE_NUMBER}_img{K} -->

<!-- TABLE_START -->
<!-- Table: Table 1. Summary of experimental results -->
<table>
  <thead>
    <tr>
      <th>Header 1</th>
      <th>Header 2</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Cell 1</td>
      <td>Cell 2</td>
    </tr>
  </tbody>
</table>
<!-- TABLE_END -->

NOTE: The table caption (e.g., "Table 1. Summary of experimental results") MUST be in the <!-- Table: ... --> comment, NOT as a separate paragraph before or after the table.

[^1]: Footnote text exactly as shown

<!-- CONFIDENCE: XX% -->

Output ONLY the extracted content. No explanations, no code fences, no additions, no interpretations."""


# =============================================================================
# HTML TABLE VALIDATOR
# =============================================================================

class TableValidator(HTMLParser):
    """Validates HTML table structure and counts rows/columns."""

    def __init__(self):
        super().__init__()
        self.tables = []
        self.current_table = None
        self.current_row = None
        self.in_thead = False
        self.in_tbody = False
        self.errors = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        if tag == 'table':
            self.current_table = {
                'header_rows': [],
                'body_rows': [],
                'max_cols': 0
            }
        elif tag == 'thead':
            self.in_thead = True
        elif tag == 'tbody':
            self.in_tbody = True
        elif tag == 'tr':
            self.current_row = {'cells': [], 'col_count': 0}
        elif tag in ('td', 'th'):
            if self.current_row is not None:
                colspan = int(attrs_dict.get('colspan', 1))
                self.current_row['cells'].append({
                    'tag': tag,
                    'colspan': colspan,
                    'rowspan': int(attrs_dict.get('rowspan', 1))
                })
                self.current_row['col_count'] += colspan

    def handle_endtag(self, tag):
        if tag == 'tr' and self.current_row is not None and self.current_table is not None:
            if self.in_thead:
                self.current_table['header_rows'].append(self.current_row)
            else:
                self.current_table['body_rows'].append(self.current_row)

            if self.current_row['col_count'] > self.current_table['max_cols']:
                self.current_table['max_cols'] = self.current_row['col_count']

            self.current_row = None
        elif tag == 'thead':
            self.in_thead = False
        elif tag == 'tbody':
            self.in_tbody = False
        elif tag == 'table' and self.current_table is not None:
            self.tables.append(self.current_table)
            self.current_table = None

    def validate(self, html: str) -> Tuple[bool, List[str], Dict]:
        """
        Validate HTML table structure.
        Returns: (is_valid, errors, stats)
        """
        self.tables = []
        self.errors = []

        try:
            self.feed(html)
        except Exception as e:
            self.errors.append(f"HTML parse error: {e}")
            return False, self.errors, {}

        stats = {
            'table_count': len(self.tables),
            'tables': []
        }

        for i, table in enumerate(self.tables):
            table_stats = {
                'header_rows': len(table['header_rows']),
                'body_rows': len(table['body_rows']),
                'max_cols': table['max_cols'],
                'row_col_counts': []
            }

            all_rows = table['header_rows'] + table['body_rows']
            for row in all_rows:
                table_stats['row_col_counts'].append(row['col_count'])

                # Check if row has consistent column count
                if row['col_count'] != table['max_cols']:
                    # This might be OK due to rowspan from previous rows
                    # but flag it for review
                    pass

            stats['tables'].append(table_stats)

        is_valid = len(self.errors) == 0
        return is_valid, self.errors, stats


def validate_table_html(html: str) -> Tuple[bool, List[str], Dict]:
    """Validate HTML table and return validation results."""
    validator = TableValidator()
    return validator.validate(html)


# =============================================================================
# PDF PAGE RENDERER
# =============================================================================

def render_pdf_page(
    pdf_path: Path,
    page_num: int,
    dpi: int = 300,
    output_format: str = "png",
    crop_header_pct: float = 0.0,
    crop_footer_pct: float = 0.0
) -> Optional[bytes]:
    """
    Render a single PDF page as an image with optional header/footer cropping.
    Page numbers are 1-based.
    Args:
        pdf_path: Path to the PDF file
        page_num: 1-based page number
        dpi: Resolution for rendering
        output_format: "png" or "jpeg"
        crop_header_pct: Percentage of page height to crop from top (0.0-1.0)
        crop_footer_pct: Percentage of page height to crop from bottom (0.0-1.0)
    Returns image bytes or None on error.
    """
    if not HAS_FITZ:
        print("ERROR: PyMuPDF not installed")
        return None

    try:
        doc = fitz.open(str(pdf_path))
        if page_num < 1 or page_num > len(doc):
            print(f"  Page {page_num} out of range (PDF has {len(doc)} pages)")
            doc.close()
            return None

        page = doc[page_num - 1]  # 0-indexed
        page_rect = page.rect

        # Calculate content area by cropping header and footer
        if crop_header_pct > 0 or crop_footer_pct > 0:
            header_crop = page_rect.height * crop_header_pct
            footer_crop = page_rect.height * crop_footer_pct
            content_rect = fitz.Rect(
                page_rect.x0,
                page_rect.y0 + header_crop,  # Move top down
                page_rect.x1,
                page_rect.y1 - footer_crop   # Move bottom up
            )
        else:
            content_rect = page_rect

        # Handle rotation
        rotation = page.rotation

        # Render at specified DPI
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        if rotation:
            mat = mat.prerotate(-rotation)

        # Render with clip to crop header/footer
        pix = page.get_pixmap(matrix=mat, clip=content_rect)

        if output_format.lower() == "jpeg":
            image_data = pix.tobytes("jpeg")
        else:
            image_data = pix.tobytes("png")
        doc.close()

        return image_data
    except Exception as e:
        print(f"  Error rendering page {page_num}: {e}")
        return None


def get_pdf_page_count(pdf_path: Path) -> int:
    """Get the number of pages in a PDF."""
    if not HAS_FITZ:
        return 0
    try:
        doc = fitz.open(str(pdf_path))
        count = len(doc)
        doc.close()
        return count
    except Exception as e:
        print(f"Error getting page count: {e}")
        return 0


# =============================================================================
# CLAUDE VISION API
# =============================================================================

class ClaudeVisionProcessor:
    """Process PDF pages using Claude Vision API."""

    def __init__(self, config: Optional[VisionConfig] = None):
        self.config = config or VisionConfig()
        self.client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )

    def extract_page_content(
        self,
        image_data: bytes,
        page_num: int,
        total_pages: int,
        media_type: str = "image/png"
    ) -> Dict:
        """
        Extract content from a single page image using Claude Vision.
        Returns dict with text, tables, confidence, etc.
        """
        # Encode image to base64
        image_base64 = base64.b64encode(image_data).decode('utf-8')

        # Build the prompt with page context
        prompt = COMBINED_EXTRACTION_PROMPT.replace("{PAGE_NUMBER}", str(page_num))
        prompt += f"\n\nThis is page {page_num} of {total_pages}."

        try:
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
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

            content = response.content[0].text

            # Parse confidence if present
            confidence = 0.9  # Default
            conf_match = re.search(r'<!--\s*CONFIDENCE:\s*(\d+)%?\s*-->', content)
            if conf_match:
                confidence = float(conf_match.group(1)) / 100.0

            # Extract tables from content
            tables = self._extract_tables_from_content(content)

            # Validate tables
            table_validations = []
            for table_html in tables:
                is_valid, errors, stats = validate_table_html(table_html)
                table_validations.append({
                    'html': table_html,
                    'valid': is_valid,
                    'errors': errors,
                    'stats': stats
                })

            return {
                'page': page_num,
                'content': content,
                'tables': table_validations,
                'confidence': confidence,
                'needs_review': confidence < self.config.confidence_threshold
            }

        except Exception as e:
            error_str = str(e)
            # Check for content filtering error
            if 'content filtering policy' in error_str.lower() or 'blocked by content' in error_str.lower():
                print(f"  Page {page_num}: Content filter triggered (false positive for educational content)")
                print(f"  This is a known issue with the AI API - the page content is likely benign")
                return {
                    'page': page_num,
                    'content': f"<!-- CONTENT_FILTER_ERROR: Page {page_num} was blocked by AI content filter (likely false positive). Manual review needed. -->",
                    'tables': [],
                    'confidence': 0.0,
                    'needs_review': True,
                    'error': 'content_filter_blocked',
                    'error_detail': 'The AI content filter blocked this page. This is often a false positive for educational/medical content. The page may need manual extraction.'
                }
            # Check for image size exceeds error
            if 'image exceeds' in error_str.lower() and 'maximum' in error_str.lower():
                print(f"  Page {page_num}: Image exceeds 5MB API limit")
                return {
                    'page': page_num,
                    'content': f"<!-- IMAGE_SIZE_ERROR: Page {page_num} image exceeds API limit -->",
                    'tables': [],
                    'confidence': 0.0,
                    'needs_review': True,
                    'error': 'image_size_exceeded',
                    'error_detail': error_str
                }
            # Check for 500 internal server error (transient, should retry)
            if 'error code: 500' in error_str.lower() or 'internal server error' in error_str.lower():
                print(f"  Page {page_num}: API internal server error (500)")
                return {
                    'page': page_num,
                    'content': f"<!-- API_ERROR: Page {page_num} - Internal server error (500) -->",
                    'tables': [],
                    'confidence': 0.0,
                    'needs_review': True,
                    'error': 'internal_server_error',
                    'error_detail': error_str
                }
            print(f"  Error extracting page {page_num}: {e}")
            return {
                'page': page_num,
                'content': f"<!-- ERROR: Failed to extract page {page_num}: {e} -->",
                'tables': [],
                'confidence': 0.0,
                'needs_review': True,
                'error': str(e)
            }

    def _extract_tables_from_content(self, content: str) -> List[str]:
        """Extract HTML tables from the content."""
        tables = []

        # Find all table blocks
        table_pattern = re.compile(
            r'<!--\s*TABLE_START\s*-->.*?<table.*?>.*?</table>.*?<!--\s*TABLE_END\s*-->',
            re.DOTALL | re.IGNORECASE
        )

        for match in table_pattern.finditer(content):
            table_block = match.group(0)
            # Extract just the <table>...</table> part
            table_match = re.search(r'<table.*?>.*?</table>', table_block, re.DOTALL | re.IGNORECASE)
            if table_match:
                tables.append(table_match.group(0))

        # Also check for tables outside TABLE_START/END markers
        if not tables:
            simple_pattern = re.compile(r'<table.*?>.*?</table>', re.DOTALL | re.IGNORECASE)
            for match in simple_pattern.finditer(content):
                tables.append(match.group(0))

        return tables

    def refine_table(
        self,
        image_data: bytes,
        page_num: int,
        original_table: str
    ) -> Optional[str]:
        """
        Second pass: refine table extraction with focused prompt.
        """
        image_base64 = base64.b64encode(image_data).decode('utf-8')

        prompt = TABLE_EXTRACTION_PROMPT + f"""

ORIGINAL EXTRACTION (may have errors):
```html
{original_table}
```

Please carefully re-examine the image and output a CORRECTED table.
Focus on:
1. Correct column count
2. Empty cells (must have <td></td> even if blank)
3. Merged cells (colspan/rowspan)
4. All rows included (no skipping)
"""

        try:
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
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

            content = response.content[0].text

            # Extract table from response
            table_match = re.search(r'<table.*?>.*?</table>', content, re.DOTALL | re.IGNORECASE)
            if table_match:
                return table_match.group(0)

            return None

        except Exception as e:
            print(f"  Error refining table on page {page_num}: {e}")
            return None


# =============================================================================
# MARKDOWN TO DOCBOOK CONVERTER
# =============================================================================

DOCBOOK42_PUBLIC = '-//OASIS//DTD DocBook XML V4.2//EN'
DOCBOOK42_SYSTEM = 'http://www.oasis-open.org/docbook/xml/4.2/docbookx.dtd'


def html_table_to_docbook(html_table: str, table_title: str = "") -> str:
    """
    Convert HTML table to DocBook 4.2 table format.
    Uses <informaltable> when no title is present, <table> with <title> otherwise.
    """
    # Parse HTML table
    validator = TableValidator()
    validator.feed(html_table)

    if not validator.tables:
        return ""

    table = validator.tables[0]
    cols = table['max_cols']

    # Build DocBook table
    lines = []

    # Extract title from HTML comment if present
    title_match = re.search(r'<!--\s*Table:\s*(.+?)\s*-->', html_table)
    if title_match:
        table_title = title_match.group(1).strip()
        # Skip generic titles like "Table" or empty titles
        if table_title.lower() == 'table' or not table_title:
            table_title = ""

    # Always use <table> with title (RittDoc DTD requires title)
    lines.append('<table frame="all">')
    if table_title:
        lines.append(f'  <title>{escape_xml(table_title)}</title>')
    else:
        lines.append('  <title/>')

    lines.append(f'  <tgroup cols="{cols}">')

    # Add colspecs
    for i in range(1, cols + 1):
        lines.append(f'    <colspec colname="c{i}"/>')

    # Helper to convert HTML cell content to DocBook
    def convert_cell_content(html: str) -> str:
        """Convert HTML inline elements and markdown formatting to DocBook.

        This function mirrors the formatting capabilities of convert_markdown_formatting()
        to ensure table cells have the same formatting support as regular paragraphs.
        """
        # First convert HTML entities to Unicode
        text = convert_html_entities(html)

        # IMPORTANT: Pre-process consecutive underscores (form fill-in blanks) BEFORE
        # any markdown conversion to prevent them from being misinterpreted as italic markers.
        # This handles PDF form fields like "Name: _______________"
        # NOTE: Use _{5,} (5+ underscores) to preserve __bold__ markdown syntax (2 underscores)
        underscore_placeholder = '<<USCOREPHOLD'
        underscore_placeholder_end = 'PHOLDEND>>'
        text = re.sub(r'_{5,}', lambda m: underscore_placeholder + str(len(m.group(0))) + underscore_placeholder_end, text)

        # Extract and process inline formatting, escaping text content
        # We need to handle this carefully to avoid escaping DocBook tags

        # Process formatting tags - extract content, escape it, wrap in DocBook
        def process_tag(match, docbook_open, docbook_close):
            inner = match.group(1)
            # Escape the inner content
            inner_escaped = escape_xml_content(inner)
            return f'{docbook_open}{inner_escaped}{docbook_close}'

        # Process markdown formatting BEFORE HTML tags
        # Bold: **text** or __text__ (must be processed before single * or _)
        text = re.sub(r'\*\*(.+?)\*\*',
                      lambda m: process_tag(m, '<emphasis role="bold">', '</emphasis>'),
                      text, flags=re.DOTALL)
        text = re.sub(r'__(.+?)__',
                      lambda m: process_tag(m, '<emphasis role="bold">', '</emphasis>'),
                      text, flags=re.DOTALL)
        # Italic: *text* or _text_ (use negative lookbehind/lookahead to avoid matching inside words)
        text = re.sub(r'(?<!\w)\*([^\*]+?)\*(?!\w)',
                      lambda m: process_tag(m, '<emphasis role="italics">', '</emphasis>'),
                      text, flags=re.DOTALL)
        text = re.sub(r'(?<!\w)_([^_]+?)_(?!\w)',
                      lambda m: process_tag(m, '<emphasis role="italics">', '</emphasis>'),
                      text, flags=re.DOTALL)

        # Code/literal: `text` - convert to DocBook literal
        text = re.sub(r'`(.+?)`',
                      lambda m: process_tag(m, '<literal>', '</literal>'),
                      text, flags=re.DOTALL)

        # Bold HTML tags
        text = re.sub(r'<b>(.*?)</b>',
                      lambda m: process_tag(m, '<emphasis role="bold">', '</emphasis>'),
                      text, flags=re.DOTALL)
        text = re.sub(r'<strong>(.*?)</strong>',
                      lambda m: process_tag(m, '<emphasis role="bold">', '</emphasis>'),
                      text, flags=re.DOTALL)
        # Italic HTML tags
        text = re.sub(r'<i>(.*?)</i>',
                      lambda m: process_tag(m, '<emphasis role="italics">', '</emphasis>'),
                      text, flags=re.DOTALL)
        text = re.sub(r'<em>(.*?)</em>',
                      lambda m: process_tag(m, '<emphasis role="italics">', '</emphasis>'),
                      text, flags=re.DOTALL)
        # Code HTML tags
        text = re.sub(r'<code>(.*?)</code>',
                      lambda m: process_tag(m, '<literal>', '</literal>'),
                      text, flags=re.DOTALL)
        # Superscript - handle both HTML <sup> and DocBook <superscript> tags
        text = re.sub(r'<sup>(.*?)</sup>',
                      lambda m: process_tag(m, '<superscript>', '</superscript>'),
                      text, flags=re.DOTALL)
        text = re.sub(r'<superscript>(.*?)</superscript>',
                      lambda m: process_tag(m, '<superscript>', '</superscript>'),
                      text, flags=re.DOTALL)
        # Subscript - handle both HTML <sub> and DocBook <subscript> tags
        text = re.sub(r'<sub>(.*?)</sub>',
                      lambda m: process_tag(m, '<subscript>', '</subscript>'),
                      text, flags=re.DOTALL)
        text = re.sub(r'<subscript>(.*?)</subscript>',
                      lambda m: process_tag(m, '<subscript>', '</subscript>'),
                      text, flags=re.DOTALL)

        # Restore underscore placeholders to actual underscores
        # Use re.escape() for placeholder strings to ensure safe regex matching
        text = re.sub(re.escape(underscore_placeholder) + r'(\d+)' + re.escape(underscore_placeholder_end),
                      lambda m: '_' * int(m.group(1)), text)

        # Remove any remaining HTML tags but keep content
        text = re.sub(r'<[^>]+>', '', text)

        # Escape any remaining unescaped text (outside DocBook tags)
        # Split by DocBook tags, escape text parts, rejoin
        parts = re.split(r'(</?(?:emphasis|superscript|subscript|literal)[^>]*>)', text.strip())
        result = []
        for part in parts:
            if part.startswith('<') and any(part.startswith(f'<{tag}') or part.startswith(f'</{tag}')
                                            for tag in ('emphasis', 'superscript', 'subscript', 'literal')):
                # DocBook tag - keep as-is
                result.append(part)
            else:
                # Text content - escape it
                result.append(escape_xml_content(part))
        return ''.join(result)

    # Parse HTML to get actual content
    # This is a simplified parser - for production, use BeautifulSoup
    def extract_rows(html: str, section: str) -> List[List[Dict]]:
        """Extract rows from thead or tbody section."""
        rows = []
        section_match = re.search(
            rf'<{section}[^>]*>(.*?)</{section}>',
            html,
            re.DOTALL | re.IGNORECASE
        )
        if not section_match:
            return rows

        section_html = section_match.group(1)
        row_matches = re.finditer(r'<tr[^>]*>(.*?)</tr>', section_html, re.DOTALL | re.IGNORECASE)

        for row_match in row_matches:
            row_html = row_match.group(1)
            cells = []

            cell_pattern = re.compile(
                r'<(td|th)([^>]*)>(.*?)</\1>',
                re.DOTALL | re.IGNORECASE
            )

            for cell_match in cell_pattern.finditer(row_html):
                tag = cell_match.group(1).lower()
                attrs = cell_match.group(2)
                content = cell_match.group(3)

                # Parse attributes
                colspan = 1
                rowspan = 1
                colspan_match = re.search(r'colspan=["\']?(\d+)', attrs)
                if colspan_match:
                    colspan = int(colspan_match.group(1))
                rowspan_match = re.search(r'rowspan=["\']?(\d+)', attrs)
                if rowspan_match:
                    rowspan = int(rowspan_match.group(1))

                cells.append({
                    'tag': tag,
                    'content': content,
                    'colspan': colspan,
                    'rowspan': rowspan
                })

            rows.append(cells)

        return rows

    # Process thead
    header_rows = extract_rows(html_table, 'thead')
    if header_rows:
        lines.append('    <thead>')
        for row in header_rows:
            lines.append('      <row>')
            col_pos = 1  # Track actual column position
            for cell in row:
                entry_attrs = []
                if cell['colspan'] > 1:
                    # Calculate correct column span based on current position
                    end_col = col_pos + cell['colspan'] - 1
                    entry_attrs.append(f'namest="c{col_pos}" nameend="c{end_col}"')
                if cell['rowspan'] > 1:
                    entry_attrs.append(f'morerows="{cell["rowspan"] - 1}"')

                attrs_str = ' ' + ' '.join(entry_attrs) if entry_attrs else ''
                content = convert_cell_content(cell['content'])
                lines.append(f'        <entry{attrs_str}>{content}</entry>')
                col_pos += cell['colspan']  # Move to next column position
            lines.append('      </row>')
        lines.append('    </thead>')

    # Process tbody
    body_rows = extract_rows(html_table, 'tbody')
    if body_rows:
        lines.append('    <tbody>')
        for row in body_rows:
            lines.append('      <row>')
            col_pos = 1  # Track actual column position
            for cell in row:
                entry_attrs = []
                if cell['colspan'] > 1:
                    # Calculate correct column span based on current position
                    end_col = col_pos + cell['colspan'] - 1
                    entry_attrs.append(f'namest="c{col_pos}" nameend="c{end_col}"')
                if cell['rowspan'] > 1:
                    entry_attrs.append(f'morerows="{cell["rowspan"] - 1}"')

                attrs_str = ' ' + ' '.join(entry_attrs) if entry_attrs else ''
                content = convert_cell_content(cell['content'])
                lines.append(f'        <entry{attrs_str}>{content}</entry>')
                col_pos += cell['colspan']  # Move to next column position
            lines.append('      </row>')
        lines.append('    </tbody>')

    lines.append('  </tgroup>')
    lines.append('</table>')

    return '\n'.join(lines)


def convert_html_entities(text: str) -> str:
    """
    Convert HTML presentation entities to their Unicode character equivalents.

    IMPORTANT: This does NOT convert XML structural entities (&amp; &lt; &gt; &quot; &apos;)
    as those are valid XML and should be preserved or handled by escape_xml().
    """
    if not text:
        return ""

    # Convert presentation HTML entities to their character equivalents
    # DO NOT include &amp; &lt; &gt; &quot; &apos; - these are valid XML entities
    html_entities = {
        '&emsp;': ' ',      # Em space -> regular space
        '&ensp;': ' ',      # En space -> regular space
        '&nbsp;': ' ',      # Non-breaking space -> regular space
        '&thinsp;': ' ',    # Thin space -> regular space
        '&mdash;': '—',     # Em dash
        '&ndash;': '–',     # En dash
        '&lsquo;': ''',     # Left single quote
        '&rsquo;': ''',     # Right single quote
        '&ldquo;': '"',     # Left double quote
        '&rdquo;': '"',     # Right double quote
        '&hellip;': '…',    # Ellipsis
        '&bull;': '•',      # Bullet
        '&middot;': '·',    # Middle dot
        '&deg;': '°',       # Degree
        '&plusmn;': '±',    # Plus-minus
        '&times;': '×',     # Multiplication
        '&divide;': '÷',    # Division
        '&frac12;': '½',    # One half
        '&frac14;': '¼',    # One quarter
        '&frac34;': '¾',    # Three quarters
        '&copy;': '©',      # Copyright
        '&reg;': '®',       # Registered
        '&trade;': '™',     # Trademark
        '&euro;': '€',      # Euro
        '&pound;': '£',     # Pound
        '&yen;': '¥',       # Yen
        '&cent;': '¢',      # Cent
        '&alpha;': 'α',     # Greek alpha
        '&beta;': 'β',      # Greek beta
        '&gamma;': 'γ',     # Greek gamma
        '&delta;': 'δ',     # Greek delta
        '&epsilon;': 'ε',   # Greek epsilon
        '&pi;': 'π',        # Greek pi
        '&sigma;': 'σ',     # Greek sigma
        '&omega;': 'ω',     # Greek omega
        '&Sigma;': 'Σ',     # Greek capital sigma
        '&Omega;': 'Ω',     # Greek capital omega
        '&infin;': '∞',     # Infinity
        '&sum;': 'Σ',       # Summation
        '&prod;': '∏',      # Product
        '&radic;': '√',     # Square root
        '&ne;': '≠',        # Not equal
        '&le;': '≤',        # Less than or equal
        '&ge;': '≥',        # Greater than or equal
        '&asymp;': '≈',     # Approximately equal
        '&equiv;': '≡',     # Equivalent
        '&rarr;': '→',      # Right arrow
        '&larr;': '←',      # Left arrow
        '&uarr;': '↑',      # Up arrow
        '&darr;': '↓',      # Down arrow
        '&harr;': '↔',      # Left-right arrow
        # Note: &amp; &lt; &gt; &quot; &apos; are NOT converted
        # They are valid XML entities and will be handled by escape_xml()
    }

    for entity, replacement in html_entities.items():
        text = text.replace(entity, replacement)

    # Handle numeric HTML entities for whitespace characters
    text = re.sub(r'&#160;', ' ', text)
    text = re.sub(r'&#8194;', ' ', text)  # en space
    text = re.sub(r'&#8195;', ' ', text)  # em space
    text = re.sub(r'&#8201;', ' ', text)  # thin space

    # Handle other numeric entities, but NOT the XML structural ones (38, 60, 62, 34, 39)
    def convert_numeric_entity(m):
        code = int(m.group(1))
        # Skip XML structural character codes: & (38), < (60), > (62), " (34), ' (39)
        if code in (38, 60, 62, 34, 39):
            return m.group(0)  # Return unchanged
        try:
            return chr(code)
        except (ValueError, OverflowError):
            return m.group(0)  # Return unchanged if invalid

    text = re.sub(r'&#(\d+);', convert_numeric_entity, text)

    return text


def sanitize_xml_chars(text: str) -> str:
    """
    Remove invalid XML characters (like null bytes and other control characters).
    XML 1.0 only allows: #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF]
    """
    if not text:
        return ""
    valid_chars = []
    for char in text:
        code = ord(char)
        # Allow: Tab (0x09), Newline (0x0A), CR (0x0D), and chars >= 0x20 (excluding surrogates and FFFE/FFFF)
        if code == 0x09 or code == 0x0A or code == 0x0D or (0x20 <= code <= 0xD7FF) or (0xE000 <= code <= 0xFFFD) or (0x10000 <= code <= 0x10FFFF):
            valid_chars.append(char)
    return ''.join(valid_chars)


def escape_xml(text: str) -> str:
    """
    Escape special XML characters for use in XML content.
    Call convert_html_entities() first if the text may contain HTML entities.
    """
    if not text:
        return ""
    # Remove invalid XML characters first (like null bytes)
    text = sanitize_xml_chars(text)
    # Convert HTML entities first
    text = convert_html_entities(text)
    # Escape XML special characters
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    text = text.replace('"', '&quot;')
    text = text.replace("'", '&apos;')
    return text


def escape_xml_text_only(text: str) -> str:
    """
    Escape only ampersands for text that will be placed in XML.
    Use this when the text may contain valid XML tags that should not be escaped.
    """
    if not text:
        return ""
    # Remove invalid XML characters first (like null bytes)
    text = sanitize_xml_chars(text)
    # Convert HTML entities first
    text = convert_html_entities(text)
    # Only escape standalone ampersands (not part of XML entities)
    # This preserves &amp; &lt; &gt; etc while escaping raw &
    text = re.sub(r'&(?!(?:amp|lt|gt|quot|apos);)', '&amp;', text)
    return text


def escape_xml_content(text: str) -> str:
    """
    Escape XML special characters in text content.
    Only escapes & < > (not quotes, as those are for attributes).
    """
    if not text:
        return ""
    # Remove invalid XML characters first (like null bytes)
    text = sanitize_xml_chars(text)
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    return text


def escape_xml_attr(text: str) -> str:
    """
    Escape XML special characters for use in attribute values.
    Escapes & < > " '
    """
    if not text:
        return ""
    # Remove invalid XML characters first (like null bytes)
    text = sanitize_xml_chars(text)
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    text = text.replace('"', '&quot;')
    text = text.replace("'", '&apos;')
    return text


def clean_title_markdown(text: str) -> str:
    """
    Clean markdown formatting from titles.
    Removes bold markers (**text** or __text__) and escaped characters (\* \_ \`)
    but keeps the text content. Titles don't need emphasis tags since they're already styled.
    """
    if not text:
        return ""
    # Remove backslash escapes
    text = text.replace('\\*', '*')
    text = text.replace('\\_', '_')
    text = text.replace('\\`', '`')
    # Remove bold markers but keep content
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    # Remove italic markers but keep content
    text = re.sub(r'(?<![*_])\*([^*]+?)\*(?![*_])', r'\1', text)
    text = re.sub(r'(?<![*_])_([^_]+?)_(?![*_])', r'\1', text)
    return text


def convert_markdown_formatting(text: str) -> str:
    """
    Convert markdown inline formatting to DocBook.
    This handles bold, italic, code, superscript, subscript.

    IMPORTANT: This function escapes text content to prevent XML injection
    and ensure valid XML output.
    """
    # First, convert HTML presentation entities to Unicode characters
    text = convert_html_entities(text)

    # Remove backslash escapes that Vision AI might add (e.g., \* -> *)
    text = text.replace('\\*', '*')
    text = text.replace('\\_', '_')
    text = text.replace('\\`', '`')

    # IMPORTANT: Pre-process consecutive underscores (form fill-in blanks) BEFORE
    # any markdown conversion to prevent them from being misinterpreted as italic markers.
    # Replace 2+ consecutive underscores with a placeholder, then restore after conversion.
    # This handles PDF form fields like "Name: _______________"
    # NOTE: Using safe text placeholder without underscores to prevent italic pattern matching
    # (the italic pattern _..._  would otherwise corrupt a placeholder containing underscores)
    underscore_placeholder = '<<USCOREPHOLD'
    underscore_placeholder_end = 'PHOLDEND>>'
    text = re.sub(r'_{2,}', lambda m: underscore_placeholder + str(len(m.group(0))) + underscore_placeholder_end, text)

    # Helper to escape content inside formatting tags
    def escape_and_wrap(match, open_tag, close_tag):
        content = match.group(1)
        # Escape XML special characters in the content
        escaped = escape_xml_content(content)
        return f'{open_tag}{escaped}{close_tag}'

    # Bold (must be before italic since * is used in both)
    text = re.sub(r'\*\*(.+?)\*\*',
                  lambda m: escape_and_wrap(m, '<emphasis role="bold">', '</emphasis>'),
                  text)
    text = re.sub(r'__(.+?)__',
                  lambda m: escape_and_wrap(m, '<emphasis role="bold">', '</emphasis>'),
                  text)
    # Italic - use negative lookahead/lookbehind to avoid matching across XML tags
    # Only match single underscores that have actual content between them (not just whitespace)
    text = re.sub(r'(?<![<>/])\*([^*<>]+?)\*(?![<>/])',
                  lambda m: escape_and_wrap(m, '<emphasis role="italics">', '</emphasis>'),
                  text)
    text = re.sub(r'(?<![<>/])_([^_<>]+?)_(?![<>/])',
                  lambda m: escape_and_wrap(m, '<emphasis role="italics">', '</emphasis>'),
                  text)
    # Code
    text = re.sub(r'`(.+?)`',
                  lambda m: escape_and_wrap(m, '<literal>', '</literal>'),
                  text)
    # Superscript - handle both HTML <sup> and DocBook <superscript> tags
    text = re.sub(r'<sup>(.+?)</sup>',
                  lambda m: escape_and_wrap(m, '<superscript>', '</superscript>'),
                  text)
    text = re.sub(r'<superscript>(.+?)</superscript>',
                  lambda m: escape_and_wrap(m, '<superscript>', '</superscript>'),
                  text)
    # Subscript - handle both HTML <sub> and DocBook <subscript> tags
    text = re.sub(r'<sub>(.+?)</sub>',
                  lambda m: escape_and_wrap(m, '<subscript>', '</subscript>'),
                  text)
    text = re.sub(r'<subscript>(.+?)</subscript>',
                  lambda m: escape_and_wrap(m, '<subscript>', '</subscript>'),
                  text)

    # Restore underscore placeholders to actual underscores
    text = re.sub(underscore_placeholder + r'(\d+)' + underscore_placeholder_end,
                  lambda m: '_' * int(m.group(1)), text)

    # Escape any remaining unescaped text (text OUTSIDE of formatting tags)
    # Content INSIDE formatting tags is already escaped by escape_and_wrap above
    # We need to track whether we're inside or outside DocBook elements
    docbook_tags = ('emphasis', 'literal', 'superscript', 'subscript')
    parts = re.split(r'(</?(?:emphasis|literal|superscript|subscript)[^>]*>)', text)
    result = []
    depth = 0  # Track nesting depth to know if we're inside or outside
    for part in parts:
        is_opening = any(part.startswith(f'<{tag}') for tag in docbook_tags)
        is_closing = any(part.startswith(f'</{tag}') for tag in docbook_tags)

        if is_opening:
            result.append(part)
            depth += 1
        elif is_closing:
            result.append(part)
            depth -= 1
        elif depth > 0:
            # Inside a DocBook element - content already escaped
            result.append(part)
        else:
            # Outside DocBook elements - need to escape
            result.append(escape_xml_content(part))
    return ''.join(result)


def _strip_font_annotation(text: str) -> str:
    """
    Strip font size annotation from heading text.
    Removes patterns like '<!-- font:24 -->' from the text.
    """
    return re.sub(r'\s*<!--\s*font:\s*\d+\s*-->\s*', '', text).strip()


def _close_all_lists(lines: List[str], list_stack: List[Tuple[str, int]]) -> List[Tuple[str, int]]:
    """
    Close all open lists in the stack.
    Returns empty list stack.
    """
    first_item = True
    while list_stack:
        list_type, _ = list_stack.pop()
        list_tag = 'itemizedlist' if list_type == 'itemized' else 'orderedlist'
        depth = len(list_stack)
        indent = '    ' + '  ' * depth
        item_indent = indent + '  ' if depth > 0 else '      '
        # Close the last open listitem first (we leave listitems open for nesting)
        if first_item:
            lines.append(f'{item_indent}</listitem>')
            first_item = False
        lines.append(f'{indent}</{list_tag}>')
        if list_stack:
            # Close the containing listitem when exiting nested list
            lines.append(f'{indent}</listitem>')
    return []


def _get_list_level(line: str) -> int:
    """
    Determine nesting level based on leading indentation (spaces).
    Uses the actual visual indentation from the PDF (preserved by Vision AI)
    rather than bullet symbol type.

    Each 2 spaces of indentation = 1 additional level of nesting.
    No indentation = level 1.
    """
    # Count leading whitespace (spaces before the bullet character)
    leading_spaces = len(line) - len(line.lstrip())
    # Each 2 spaces = 1 additional level (level starts at 1)
    level = 1 + (leading_spaces // 2)
    return max(1, level)  # Minimum level is 1


def _merge_continuation_tables(content: str) -> str:
    """
    Merge tables that span multiple pages.

    Detection strategies (strict):
    1. Explicit continuation markers from Vision AI
    2. Adjacent tables across page boundaries with NO other content between
       - Previous page must end with a table row
       - Current page must start with a table row
       - Only page marker(s) allowed between them
    """
    # Find all tables in the content (both wrapped and standalone)
    table_pattern = re.compile(
        r'(<!--\s*TABLE_START\s*-->.*?<table.*?>.*?</table>.*?<!--\s*TABLE_END\s*-->|<table.*?>.*?</table>)',
        re.DOTALL | re.IGNORECASE
    )

    tables = []
    for match in table_pattern.finditer(content):
        table_html = match.group(0)
        start_pos = match.start()
        end_pos = match.end()

        # Check for continuation markers
        continues_from_prev = bool(re.search(r'<!--.*?continues?\s+from\s+prev', table_html, re.IGNORECASE))
        continues_to_next = bool(re.search(r'<!--.*?continues?\s+(on|to)\s+next', table_html, re.IGNORECASE))

        # Extract column count from first row (for validation)
        col_count = 0
        first_row_match = re.search(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL | re.IGNORECASE)
        if first_row_match:
            first_row_html = first_row_match.group(1)
            # Count cells, accounting for colspan
            for cell_match in re.finditer(r'<t[hd]([^>]*)>', first_row_html, re.IGNORECASE):
                attrs = cell_match.group(1)
                colspan_match = re.search(r'colspan=["\']?(\d+)', attrs)
                if colspan_match:
                    col_count += int(colspan_match.group(1))
                else:
                    col_count += 1

        tables.append({
            'html': table_html,
            'start': start_pos,
            'end': end_pos,
            'continues_from_prev': continues_from_prev,
            'continues_to_next': continues_to_next,
            'col_count': col_count,
            'merged': False
        })

    if len(tables) < 2:
        return content

    # Identify tables to merge
    merged_content = content
    offset = 0  # Track position offset due to replacements

    i = 0
    while i < len(tables) - 1:
        current = tables[i]
        next_table = tables[i + 1]

        # Check if tables should be merged
        should_merge = False

        # Strategy 1: Explicit continuation markers
        if current['continues_to_next'] or next_table['continues_from_prev']:
            should_merge = True

        # Strategy 2: Adjacent tables across page boundary with NO other content
        else:
            between_text = content[current['end']:next_table['start']]

            # Must have a page marker between them
            has_page_marker = bool(re.search(r'<!--\s*Page\s+\d+\s*-->', between_text))

            if has_page_marker:
                # Strip all comments (page markers, table markers, etc.) and whitespace
                clean_between = re.sub(r'<!--.*?-->', '', between_text)
                clean_between = clean_between.strip()

                # STRICT: Only merge if there's NO other content between tables
                # (after removing comments and whitespace, nothing should remain)
                if len(clean_between) == 0:
                    # Additional validation: column counts should match
                    if current['col_count'] == next_table['col_count'] and current['col_count'] > 0:
                        should_merge = True

        if should_merge and not current['merged'] and not next_table['merged']:
            # Merge tables: take tbody rows from next_table and append to current
            current_html = current['html']
            next_html = next_table['html']

            # Extract tbody from next table
            next_tbody_match = re.search(r'<tbody[^>]*>(.*?)</tbody>', next_html, re.DOTALL | re.IGNORECASE)
            if next_tbody_match:
                next_rows = next_tbody_match.group(1)

                # Insert rows into current table's tbody
                current_tbody_end = re.search(r'</tbody>', current_html, re.IGNORECASE)
                if current_tbody_end:
                    merged_html = (
                        current_html[:current_tbody_end.start()] +
                        next_rows +
                        current_html[current_tbody_end.start():]
                    )

                    # Remove continuation markers from merged table
                    merged_html = re.sub(r'<!--.*?continues?\s+(on|to)\s+next.*?-->', '', merged_html, flags=re.IGNORECASE)
                    merged_html = re.sub(r'<!--.*?continues?\s+from\s+prev.*?-->', '', merged_html, flags=re.IGNORECASE)

                    # Replace current table with merged version
                    adjusted_start = current['start'] + offset
                    adjusted_end = current['end'] + offset
                    merged_content = merged_content[:adjusted_start] + merged_html + merged_content[adjusted_end:]
                    offset += len(merged_html) - len(current_html)

                    # Remove next table entirely
                    next_adjusted_start = next_table['start'] + offset
                    next_adjusted_end = next_table['end'] + offset
                    merged_content = merged_content[:next_adjusted_start] + merged_content[next_adjusted_end:]
                    offset -= (next_table['end'] - next_table['start'])

                    # Mark as merged
                    current['merged'] = True
                    next_table['merged'] = True

                    # Update current table's html for potential further merges
                    current['html'] = merged_html
                    current['end'] = current['start'] + len(merged_html)
                    current['continues_to_next'] = False

                    # Don't increment i - check if more tables should merge with current
                    continue

        i += 1

    return merged_content


def _normalize_heading_levels_across_pages(content: str) -> str:
    """
    Normalize heading levels across all pages based on font size information.

    Problem: Vision AI assigns heading levels per-page based on "largest = #".
    This means a 14pt heading on one page might be # while a 24pt heading
    on another page is also #, creating inconsistent hierarchy.

    Solution:
    1. Collect all headings with their font size annotations
    2. Determine document-wide font size to heading level mapping
    3. Re-assign heading levels based on font size, not per-page assignment

    Expected input format: # Heading Text <!-- font:SIZE -->
    """
    # Pattern to match headings with font size annotations
    # Captures: 1=heading level (#, ##, etc), 2=heading text, 3=font size
    heading_pattern = re.compile(
        r'^(#{1,6})\s+(.+?)\s*<!--\s*font:\s*(\d+)\s*-->',
        re.MULTILINE
    )

    # Collect all font sizes from the document
    font_sizes = []
    for match in heading_pattern.finditer(content):
        font_size = int(match.group(3))
        font_sizes.append(font_size)

    if not font_sizes:
        # No font size annotations matched the heading pattern
        # Still strip any remaining font annotations (malformed or on non-heading lines)
        return re.sub(r'\s*<!--\s*font:\s*\d+\s*-->\s*', '', content)

    # Determine unique font sizes and create a mapping
    unique_sizes = sorted(set(font_sizes), reverse=True)  # Largest first

    # Create font size to heading level mapping
    # Largest font = level 1 (#), second largest = level 2 (##), etc.
    # Cap at 6 levels (DocBook/HTML limit)
    font_to_level = {}
    for i, size in enumerate(unique_sizes):
        level = min(i + 1, 6)  # Cap at 6
        font_to_level[size] = level

    # Replace headings with normalized levels
    def replace_heading(match):
        original_level = len(match.group(1))
        heading_text = match.group(2)
        font_size = int(match.group(3))

        # Get the correct level based on document-wide font size
        new_level = font_to_level.get(font_size, original_level)

        # Build new heading
        new_hashes = '#' * new_level
        # Build new heading WITHOUT font annotation (it's been used for normalization)
        return f'{new_hashes} {heading_text}'

    normalized_content = heading_pattern.sub(replace_heading, content)

    # Also strip any remaining font annotations that weren't matched
    # (e.g., malformed annotations or annotations on non-heading lines)
    normalized_content = re.sub(r'\s*<!--\s*font:\s*\d+\s*-->\s*', '', normalized_content)

    return normalized_content


def _normalize_list_indentation_across_pages(content: str) -> str:
    """
    Normalize list item indentation when lists span across page boundaries.

    Problem: Vision AI processes each page in isolation, so a list that continues
    from page N to page N+1 may have incorrect indentation on page N+1 because
    the AI doesn't know the context from the previous page.

    Solution:
    1. Split content by page markers
    2. For each page boundary, check if previous page ends with list items
    3. Check if next page starts with list items
    4. Compare bullet/numbering patterns to detect continuation
    5. Adjust indentation of continuation items to match the context

    Bullet types by level (typical hierarchy):
    - Level 1: •, -, *, 1., a., A., i., I.
    - Level 2: ○, ▪, ▸, nested numbers (1.1, 1.a)
    - Level 3: ◦, ◆, deeper nested
    """
    # Split by page markers but preserve them
    page_pattern = re.compile(r'(<!--\s*Page\s+\d+\s*-->)')
    parts = page_pattern.split(content)

    if len(parts) < 3:
        return content  # No page boundaries to process

    # Bullet patterns for detection
    bullet_symbols = r'[•○▪▸►‣⁃◦◆◇\-\*\+]'
    ordered_pattern = r'(?:\d+[\.\)]|\(\d+\)|[a-zA-Z][\.\)]|\([a-zA-Z]\)|[ivxlcdmIVXLCDM]+[\.\)]|\([ivxlcdmIVXLCDM]+\))'

    def get_list_context(text: str, from_end: bool = False) -> dict:
        """
        Extract list context from text.
        If from_end=True, look at the end of text; otherwise look at the start.
        Returns info about list items found.
        """
        lines = text.strip().split('\n')
        if from_end:
            lines = lines[::-1]  # Reverse to process from end

        list_items = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Check for bullet or ordered list
            bullet_match = re.match(rf'^({bullet_symbols})\s+(.*)$', stripped)
            ordered_match = re.match(rf'^({ordered_pattern})\s+(.*)$', stripped)

            if bullet_match:
                leading_spaces = len(line) - len(line.lstrip())
                level = 1 + (leading_spaces // 2)
                list_items.append({
                    'type': 'itemized',
                    'marker': bullet_match.group(1),
                    'text': bullet_match.group(2),
                    'level': level,
                    'leading_spaces': leading_spaces,
                    'original_line': line
                })
            elif ordered_match:
                leading_spaces = len(line) - len(line.lstrip())
                level = 1 + (leading_spaces // 2)
                list_items.append({
                    'type': 'ordered',
                    'marker': ordered_match.group(1),
                    'text': ordered_match.group(1),
                    'level': level,
                    'leading_spaces': leading_spaces,
                    'original_line': line
                })
            else:
                # Non-list line encountered - stop
                break

        if from_end:
            list_items = list_items[::-1]  # Restore original order

        return {
            'items': list_items,
            'has_list': len(list_items) > 0,
            'last_level': list_items[-1]['level'] if list_items else 0,
            'last_type': list_items[-1]['type'] if list_items else None
        }

    def adjust_indentation(text: str, context_level: int, context_type: str) -> str:
        """
        Adjust indentation of list items at the start of text based on context.
        """
        lines = text.split('\n')
        result_lines = []
        in_continuation = True
        first_item_level = None

        for line in lines:
            stripped = line.strip()

            if not stripped:
                result_lines.append(line)
                continue

            # Check if this is a list item
            bullet_match = re.match(rf'^({bullet_symbols})\s+(.*)$', stripped)
            ordered_match = re.match(rf'^({ordered_pattern})\s+(.*)$', stripped)

            if in_continuation and (bullet_match or ordered_match):
                current_leading = len(line) - len(line.lstrip())
                current_level = 1 + (current_leading // 2)

                if first_item_level is None:
                    first_item_level = current_level

                # Calculate adjustment needed
                # Adjust if there's a mismatch between first item level and context level
                # This handles cases where the new page starts at any level that differs from context
                if first_item_level is not None and context_level > first_item_level:
                    level_diff = context_level - first_item_level
                    # Adjust this item's indentation proportionally
                    new_leading = current_leading + (level_diff * 2)
                    new_line = ' ' * new_leading + stripped
                    result_lines.append(new_line)
                else:
                    result_lines.append(line)
            else:
                in_continuation = False
                result_lines.append(line)

        return '\n'.join(result_lines)

    # Process page boundaries
    result_parts = []
    for i, part in enumerate(parts):
        if i == 0:
            result_parts.append(part)
            continue

        # Check if this is a page marker
        if page_pattern.match(part):
            result_parts.append(part)
            continue

        # This is page content - check if previous content ended with a list
        # Find the previous content (skip page markers)
        prev_content = None
        for j in range(i - 1, -1, -1):
            if not page_pattern.match(parts[j]) and parts[j].strip():
                prev_content = parts[j]
                break

        if prev_content:
            prev_context = get_list_context(prev_content, from_end=True)
            curr_context = get_list_context(part, from_end=False)

            # Check if we need to adjust indentation
            # Conditions for potential list continuation:
            # 1. Previous page ended with a list
            # 2. Current page starts with a list
            # 3. There's a level mismatch (current starts lower than prev ended)
            if prev_context['has_list'] and curr_context['has_list']:
                # Get the first item level from current page
                curr_first_level = curr_context['items'][0]['level'] if curr_context['items'] else 1

                # Adjust if previous page ended at a higher level than current starts
                # This indicates the AI lost context and reset indentation
                if prev_context['last_level'] > curr_first_level:
                    adjusted_part = adjust_indentation(
                        part,
                        prev_context['last_level'],
                        prev_context['last_type']
                    )
                    result_parts.append(adjusted_part)
                else:
                    result_parts.append(part)
            else:
                result_parts.append(part)
        else:
            result_parts.append(part)

    return ''.join(result_parts)


def _convert_pipe_table_to_html(pipe_table: str) -> Optional[str]:
    """
    Convert a markdown pipe table to HTML table.

    Example input:
    | Header1 | Header2 |
    |---------|---------|
    | Cell1   | Cell2   |

    Returns HTML table string or None if not a valid table.
    """
    lines = pipe_table.strip().split('\n')
    if len(lines) < 2:
        return None

    # Parse rows
    rows = []
    separator_idx = -1

    for i, line in enumerate(lines):
        line = line.strip()
        if not line.startswith('|') or not line.endswith('|'):
            continue

        # Check if this is a separator line (|---|---|)
        if re.match(r'^\|[\s\-:|\+]+\|$', line):
            separator_idx = i
            continue

        # Parse cells
        cells = [cell.strip() for cell in line.split('|')[1:-1]]
        if cells:
            rows.append(cells)

    if not rows:
        return None

    # Build HTML table
    html_lines = ['<table>', '  <thead>']

    # First row is header (or if separator exists, rows before it)
    header_end = 1 if separator_idx <= 1 else separator_idx
    for row in rows[:header_end]:
        html_lines.append('    <tr>')
        for cell in row:
            # Escape cell content to prevent HTML/XML parsing issues
            safe_cell = escape_xml_content(cell)
            html_lines.append(f'      <th>{safe_cell}</th>')
        html_lines.append('    </tr>')
    html_lines.append('  </thead>')

    # Remaining rows are body
    body_rows = rows[header_end:]
    if body_rows:
        html_lines.append('  <tbody>')
        for row in body_rows:
            html_lines.append('    <tr>')
            for cell in row:
                # Escape cell content to prevent HTML/XML parsing issues
                safe_cell = escape_xml_content(cell)
                html_lines.append(f'      <td>{safe_cell}</td>')
            html_lines.append('    </tr>')
        html_lines.append('  </tbody>')

    html_lines.append('</table>')
    return '\n'.join(html_lines)


def _generate_book_id(title: str) -> str:
    """Generate a valid XML ID from book title."""
    if not title:
        return "book1"
    # Sanitize: keep alphanumeric, replace spaces/special chars with underscore
    sanitized = re.sub(r'[^a-zA-Z0-9]+', '_', title)
    # Ensure starts with letter (XML ID requirement)
    if sanitized and not sanitized[0].isalpha():
        sanitized = 'book_' + sanitized
    # Truncate if too long
    sanitized = sanitized[:50].rstrip('_')
    return sanitized or "book1"


def _generate_chapter_label(chapter_num: int) -> str:
    """Generate chapter label per R2 spec.

    Args:
        chapter_num: The chapter number (0 = intro, 1+ = chapter number)

    Returns:
        Label string: "intro" for chapter 0, number string for others
    """
    if chapter_num == 0:
        return "intro"
    return str(chapter_num)


def _generate_appendix_label(appendix_num: int) -> str:
    """Generate appendix label per R2 spec (A, B, C, ...).

    Args:
        appendix_num: The appendix number (1 = A, 2 = B, etc.)

    Returns:
        Label string: A, B, C, ... AA, AB for larger numbers
    """
    if appendix_num <= 0:
        return "A"
    # Convert to A, B, C... format
    result = ""
    num = appendix_num
    while num > 0:
        num -= 1
        result = chr(ord('A') + (num % 26)) + result
        num //= 26
    return result


def markdown_to_docbook(
    markdown_content: str,
    images_by_page: Dict[int, List[Dict]],
    book_title: str = None,
    bookmark_hierarchy: Optional[Dict] = None,
    book_id: str = None
) -> str:
    """
    Convert Markdown content to DocBook 4.2 XML.

    Args:
        markdown_content: The markdown content to convert
        images_by_page: Dictionary mapping page numbers to image info
        book_title: Optional book title. If not provided, will try to extract from content.
        bookmark_hierarchy: Optional bookmark hierarchy for chapter/section structure.
            Expected format:
            {
                "bookmarks": [{"level": 0, "title": "...", "start_page": N, "end_page": M, "children": [...]}],
                "front_matter_end_page": -1,  # 0-indexed, -1 if no front matter
                "back_matter_start_page": -1, # 0-indexed, -1 if no back matter
                "total_pages": N
            }
        book_id: Optional book ID. If not provided, will be generated from title.
    """
    # Pre-process: merge tables that span multiple pages
    markdown_content = _merge_continuation_tables(markdown_content)

    # Pre-process: normalize heading levels based on font size across all pages
    # This ensures consistent heading hierarchy throughout the document
    markdown_content = _normalize_heading_levels_across_pages(markdown_content)

    # Pre-process: normalize list indentation across page boundaries
    markdown_content = _normalize_list_indentation_across_pages(markdown_content)

    lines = []

    # Try to extract title from content if not provided
    if not book_title:
        # Look for a title-like heading at the start (# Title or ## Title on first lines)
        first_lines = markdown_content.strip().split('\n')[:20]  # Check first 20 lines
        for line in first_lines:
            line = line.strip()
            # Skip page markers and empty lines
            if not line or line.startswith('<!--'):
                continue
            # Check for markdown heading
            heading_match = re.match(r'^#{1,2}\s+(.+)$', line)
            if heading_match:
                book_title = heading_match.group(1).strip()
                # Clean up markdown formatting from title
                book_title = re.sub(r'\*\*(.+?)\*\*', r'\1', book_title)  # Remove bold
                book_title = re.sub(r'\*(.+?)\*', r'\1', book_title)  # Remove italic
                break

    # Default to a generic title if still not found
    if not book_title:
        book_title = "Untitled Document"

    # Generate book ID if not provided
    if not book_id:
        book_id = _generate_book_id(book_title)

    # Start with book element (with ID attribute)
    lines.append(f'<book id="{book_id}">')
    lines.append(f'  <title>{escape_xml(book_title)}</title>')

    current_chapter = None
    current_sect1 = None
    current_sect2 = None
    current_sect3 = None
    current_sect4 = None
    current_sect5 = None
    list_stack = []  # Stack of (list_type, level) for nested lists

    # Counters for generating unique IDs
    chapter_counter = 0
    sect1_counter = 0
    sect2_counter = 0
    sect3_counter = 0
    sect4_counter = 0
    sect5_counter = 0
    preface_counter = 0
    appendix_counter = 0

    # Build page-to-bookmark mapping from hierarchy if provided
    # Maps 1-indexed page numbers to list of bookmarks starting on that page
    page_to_bookmarks: Dict[int, List[Dict]] = {}
    front_matter_end_page = -1  # 0-indexed
    back_matter_start_page = -1  # 0-indexed
    in_front_matter = False
    in_back_matter = False

    if bookmark_hierarchy:
        front_matter_end_page = bookmark_hierarchy.get('front_matter_end_page', -1)
        back_matter_start_page = bookmark_hierarchy.get('back_matter_start_page', -1)

        def collect_bookmarks(nodes: List[Dict], parent_level: int = -1):
            """Recursively collect bookmarks and map them to their start pages."""
            for node in nodes:
                # Convert 0-indexed page to 1-indexed for internal use
                page_1idx = node['start_page'] + 1
                if page_1idx not in page_to_bookmarks:
                    page_to_bookmarks[page_1idx] = []
                page_to_bookmarks[page_1idx].append(node)
                # Recursively process children
                if node.get('children'):
                    collect_bookmarks(node['children'], node['level'])

        bookmarks = bookmark_hierarchy.get('bookmarks', [])
        collect_bookmarks(bookmarks)

        # Sort bookmarks at each page by level (chapter first, then sect1, etc.)
        for page in page_to_bookmarks:
            page_to_bookmarks[page].sort(key=lambda b: b['level'])

    # Split by page markers
    pages = re.split(r'<!--\s*Page\s+(\d+)\s*-->', markdown_content)

    # pages[0] is content before first page marker (usually empty)
    # pages[1] is page number, pages[2] is content, etc.

    content_parts = []
    if len(pages) > 1:
        for i in range(1, len(pages), 2):
            page_num = int(pages[i])
            content = pages[i + 1] if i + 1 < len(pages) else ""
            content_parts.append((page_num, content))
    else:
        # No page markers, treat as single page
        content_parts.append((1, markdown_content))

    # Track last page number to avoid duplicates and out-of-order page breaks
    last_page_num = 0

    for page_num, page_content in content_parts:
        # Skip duplicate or out-of-order page breaks
        if page_num <= last_page_num:
            # Still process the content, just don't add another page break
            pass
        else:
            # Track page for later, but don't add processing instruction
            # (Processing instructions can cause issues in some renderers)
            last_page_num = page_num

        # Handle front matter: pages before first chapter (if bookmarks provided)
        # Convert page_num (1-indexed) to 0-indexed for comparison
        page_0idx = page_num - 1
        if bookmark_hierarchy and front_matter_end_page >= 0:
            if page_0idx == 0 and not in_front_matter and page_0idx <= front_matter_end_page:
                # Start front matter (preface) with R2 spec compliant ID
                in_front_matter = True
                preface_counter += 1
                preface_id = f"pr{preface_counter:04d}"
                lines.append(f'  <preface id="{preface_id}">')
                lines.append('    <title>Front Matter</title>')
                # Add required sect1 for R2 spec compliance
                lines.append(f'    <sect1 id="{preface_id}s01">')
                lines.append('      <title/>')
            elif in_front_matter and page_0idx > front_matter_end_page:
                # End front matter - close sect1 first
                lines.append('    </sect1>')
                lines.append('  </preface>')
                in_front_matter = False

        # Handle bookmarks: inject chapters/sections at their start pages
        if page_num in page_to_bookmarks:
            # Close any open front matter before first chapter
            if in_front_matter:
                lines.append('    </sect1>')
                lines.append('  </preface>')
                in_front_matter = False

            for bm in page_to_bookmarks[page_num]:
                bm_level = bm['level']
                bm_title = bm.get('title', 'Untitled')

                # Close sections down to this level and open new section
                if bm_level == 0:
                    # Chapter - close any existing chapter and all sections
                    if current_sect5:
                        lines.append('            </sect5>')
                        current_sect5 = None
                    if current_sect4:
                        lines.append('          </sect4>')
                        current_sect4 = None
                    if current_sect3:
                        lines.append('        </sect3>')
                        current_sect3 = None
                    if current_sect2:
                        lines.append('      </sect2>')
                        current_sect2 = None
                    if current_sect1:
                        lines.append('    </sect1>')
                        current_sect1 = None
                    if current_chapter:
                        lines.append('  </chapter>')
                        current_chapter = None

                    # Start new chapter
                    chapter_counter += 1
                    sect1_counter = 0
                    sect2_counter = 0
                    sect3_counter = 0
                    sect4_counter = 0
                    sect5_counter = 0
                    chapter_id = f"ch{chapter_counter:04d}"
                    chapter_label = _generate_chapter_label(chapter_counter)
                    lines.append(f'  <chapter id="{chapter_id}" label="{chapter_label}">')
                    lines.append(f'    <title>{escape_xml(bm_title)}</title>')
                    current_chapter = chapter_id

                elif bm_level == 1:
                    # Sect1 - ensure chapter exists, close deeper sections
                    if not current_chapter:
                        chapter_counter += 1
                        sect1_counter = 0
                        chapter_id = f"ch{chapter_counter:04d}"
                        chapter_label = _generate_chapter_label(chapter_counter)
                        lines.append(f'  <chapter id="{chapter_id}" label="{chapter_label}">')
                        lines.append('    <title/>')
                        current_chapter = chapter_id

                    if current_sect5:
                        lines.append('            </sect5>')
                        current_sect5 = None
                    if current_sect4:
                        lines.append('          </sect4>')
                        current_sect4 = None
                    if current_sect3:
                        lines.append('        </sect3>')
                        current_sect3 = None
                    if current_sect2:
                        lines.append('      </sect2>')
                        current_sect2 = None
                    if current_sect1:
                        lines.append('    </sect1>')
                        current_sect1 = None

                    sect1_counter += 1
                    sect2_counter = 0
                    sect3_counter = 0
                    sect4_counter = 0
                    sect5_counter = 0
                    sect1_id = f"{current_chapter}s{sect1_counter:02d}"
                    lines.append(f'    <sect1 id="{sect1_id}">')
                    lines.append(f'      <title>{escape_xml(bm_title)}</title>')
                    current_sect1 = sect1_id

                elif bm_level == 2:
                    # Sect2 - ensure chapter and sect1 exist
                    if not current_chapter:
                        chapter_counter += 1
                        chapter_id = f"ch{chapter_counter:04d}"
                        chapter_label = _generate_chapter_label(chapter_counter)
                        lines.append(f'  <chapter id="{chapter_id}" label="{chapter_label}">')
                        lines.append('    <title/>')
                        current_chapter = chapter_id
                    if not current_sect1:
                        sect1_counter += 1
                        sect1_id = f"{current_chapter}s{sect1_counter:02d}"
                        lines.append(f'    <sect1 id="{sect1_id}">')
                        lines.append('      <title/>')
                        current_sect1 = sect1_id

                    if current_sect5:
                        lines.append('            </sect5>')
                        current_sect5 = None
                    if current_sect4:
                        lines.append('          </sect4>')
                        current_sect4 = None
                    if current_sect3:
                        lines.append('        </sect3>')
                        current_sect3 = None
                    if current_sect2:
                        lines.append('      </sect2>')
                        current_sect2 = None

                    sect2_counter += 1
                    sect3_counter = 0
                    sect4_counter = 0
                    sect5_counter = 0
                    sect2_id = f"{current_sect1}s{sect2_counter:02d}"
                    lines.append(f'      <sect2 id="{sect2_id}">')
                    lines.append(f'        <title>{escape_xml(bm_title)}</title>')
                    current_sect2 = sect2_id

                elif bm_level == 3:
                    # Sect3 - ensure ancestors exist
                    if not current_chapter:
                        chapter_counter += 1
                        chapter_id = f"ch{chapter_counter:04d}"
                        chapter_label = _generate_chapter_label(chapter_counter)
                        lines.append(f'  <chapter id="{chapter_id}" label="{chapter_label}">')
                        lines.append('    <title/>')
                        current_chapter = chapter_id
                    if not current_sect1:
                        sect1_counter += 1
                        sect1_id = f"{current_chapter}s{sect1_counter:02d}"
                        lines.append(f'    <sect1 id="{sect1_id}">')
                        lines.append('      <title/>')
                        current_sect1 = sect1_id
                    if not current_sect2:
                        sect2_counter += 1
                        sect2_id = f"{current_sect1}s{sect2_counter:02d}"
                        lines.append(f'      <sect2 id="{sect2_id}">')
                        lines.append('        <title/>')
                        current_sect2 = sect2_id

                    if current_sect5:
                        lines.append('            </sect5>')
                        current_sect5 = None
                    if current_sect4:
                        lines.append('          </sect4>')
                        current_sect4 = None
                    if current_sect3:
                        lines.append('        </sect3>')
                        current_sect3 = None

                    sect3_counter += 1
                    sect4_counter = 0
                    sect5_counter = 0
                    sect3_id = f"{current_sect2}s{sect3_counter:02d}"
                    lines.append(f'        <sect3 id="{sect3_id}">')
                    lines.append(f'          <title>{escape_xml(bm_title)}</title>')
                    current_sect3 = sect3_id

                elif bm_level == 4:
                    # Sect4 - ensure ancestors exist
                    if not current_chapter:
                        chapter_counter += 1
                        chapter_id = f"ch{chapter_counter:04d}"
                        chapter_label = _generate_chapter_label(chapter_counter)
                        lines.append(f'  <chapter id="{chapter_id}" label="{chapter_label}">')
                        lines.append('    <title/>')
                        current_chapter = chapter_id
                    if not current_sect1:
                        sect1_counter += 1
                        sect1_id = f"{current_chapter}s{sect1_counter:02d}"
                        lines.append(f'    <sect1 id="{sect1_id}">')
                        lines.append('      <title/>')
                        current_sect1 = sect1_id
                    if not current_sect2:
                        sect2_counter += 1
                        sect2_id = f"{current_sect1}s{sect2_counter:02d}"
                        lines.append(f'      <sect2 id="{sect2_id}">')
                        lines.append('        <title/>')
                        current_sect2 = sect2_id
                    if not current_sect3:
                        sect3_counter += 1
                        sect3_id = f"{current_sect2}s{sect3_counter:02d}"
                        lines.append(f'        <sect3 id="{sect3_id}">')
                        lines.append('          <title/>')
                        current_sect3 = sect3_id

                    if current_sect5:
                        lines.append('            </sect5>')
                        current_sect5 = None
                    if current_sect4:
                        lines.append('          </sect4>')
                        current_sect4 = None

                    sect4_counter += 1
                    sect5_counter = 0
                    sect4_id = f"{current_sect3}s{sect4_counter:02d}"
                    lines.append(f'          <sect4 id="{sect4_id}">')
                    lines.append(f'            <title>{escape_xml(bm_title)}</title>')
                    current_sect4 = sect4_id

                elif bm_level >= 5:
                    # Sect5 (max depth) - ensure ancestors exist
                    if not current_chapter:
                        chapter_counter += 1
                        chapter_id = f"ch{chapter_counter:04d}"
                        chapter_label = _generate_chapter_label(chapter_counter)
                        lines.append(f'  <chapter id="{chapter_id}" label="{chapter_label}">')
                        lines.append('    <title/>')
                        current_chapter = chapter_id
                    if not current_sect1:
                        sect1_counter += 1
                        sect1_id = f"{current_chapter}s{sect1_counter:02d}"
                        lines.append(f'    <sect1 id="{sect1_id}">')
                        lines.append('      <title/>')
                        current_sect1 = sect1_id
                    if not current_sect2:
                        sect2_counter += 1
                        sect2_id = f"{current_sect1}s{sect2_counter:02d}"
                        lines.append(f'      <sect2 id="{sect2_id}">')
                        lines.append('        <title/>')
                        current_sect2 = sect2_id
                    if not current_sect3:
                        sect3_counter += 1
                        sect3_id = f"{current_sect2}s{sect3_counter:02d}"
                        lines.append(f'        <sect3 id="{sect3_id}">')
                        lines.append('          <title/>')
                        current_sect3 = sect3_id
                    if not current_sect4:
                        sect4_counter += 1
                        sect4_id = f"{current_sect3}s{sect4_counter:02d}"
                        lines.append(f'          <sect4 id="{sect4_id}">')
                        lines.append('            <title/>')
                        current_sect4 = sect4_id

                    if current_sect5:
                        lines.append('            </sect5>')
                        current_sect5 = None

                    sect5_counter += 1
                    sect5_id = f"{current_sect4}s{sect5_counter:02d}"
                    lines.append(f'            <sect5 id="{sect5_id}">')
                    lines.append(f'              <title>{escape_xml(bm_title)}</title>')
                    current_sect5 = sect5_id

        # Handle back matter: pages after last chapter (if bookmarks provided)
        if bookmark_hierarchy and back_matter_start_page >= 0:
            if page_0idx == back_matter_start_page and not in_back_matter:
                # Close all open sections and chapters before back matter
                if current_sect5:
                    lines.append('            </sect5>')
                    current_sect5 = None
                if current_sect4:
                    lines.append('          </sect4>')
                    current_sect4 = None
                if current_sect3:
                    lines.append('        </sect3>')
                    current_sect3 = None
                if current_sect2:
                    lines.append('      </sect2>')
                    current_sect2 = None
                if current_sect1:
                    lines.append('    </sect1>')
                    current_sect1 = None
                if current_chapter:
                    lines.append('  </chapter>')
                    current_chapter = None

                # Start back matter (appendix) with R2 spec compliant ID and label
                in_back_matter = True
                appendix_counter += 1
                appendix_id = f"ap{appendix_counter:04d}"
                appendix_label = _generate_appendix_label(appendix_counter)
                lines.append(f'  <appendix id="{appendix_id}" label="{appendix_label}">')
                lines.append('    <title>Back Matter</title>')
                # Add required sect1 for R2 spec compliance
                lines.append(f'    <sect1 id="{appendix_id}s01">')
                lines.append('      <title/>')

        # Pre-process: Extract all HTML tables and replace with placeholders
        # This prevents table HTML from being output as paragraphs
        tables_in_page = []
        processed_content = page_content

        # Find tables wrapped in TABLE_START/TABLE_END markers
        table_block_pattern = re.compile(
            r'<!--\s*TABLE_START\s*-->.*?<table.*?>.*?</table>.*?<!--\s*TABLE_END\s*-->',
            re.DOTALL | re.IGNORECASE
        )
        for match in table_block_pattern.finditer(page_content):
            table_html = match.group(0)
            # Extract just the <table>...</table> part
            inner_match = re.search(r'<table.*?>.*?</table>', table_html, re.DOTALL | re.IGNORECASE)
            if inner_match:
                tables_in_page.append(inner_match.group(0))
                placeholder = f'__TABLE_PLACEHOLDER_{len(tables_in_page) - 1}__'
                processed_content = processed_content.replace(table_html, placeholder, 1)

        # Also find standalone tables (not wrapped in markers)
        standalone_table_pattern = re.compile(r'<table.*?>.*?</table>', re.DOTALL | re.IGNORECASE)
        for match in standalone_table_pattern.finditer(processed_content):
            table_html = match.group(0)
            # Skip if it's already a placeholder
            if '__TABLE_PLACEHOLDER_' in table_html:
                continue
            tables_in_page.append(table_html)
            placeholder = f'__TABLE_PLACEHOLDER_{len(tables_in_page) - 1}__'
            processed_content = processed_content.replace(table_html, placeholder, 1)

        # Also find markdown pipe tables and convert to HTML
        # Pattern: consecutive lines starting and ending with |
        pipe_table_pattern = re.compile(
            r'((?:^\|.+\|\s*$\n?)+)',
            re.MULTILINE
        )
        for match in pipe_table_pattern.finditer(processed_content):
            pipe_table = match.group(0)
            # Convert pipe table to HTML
            html_table = _convert_pipe_table_to_html(pipe_table)
            if html_table:
                tables_in_page.append(html_table)
                placeholder = f'__TABLE_PLACEHOLDER_{len(tables_in_page) - 1}__'
                processed_content = processed_content.replace(pipe_table, placeholder + '\n', 1)

        # Process content line by line (with tables replaced by placeholders)
        page_lines = processed_content.split('\n')

        for line in page_lines:
            stripped = line.strip()

            # Skip empty lines
            if not stripped:
                continue

            # Skip metadata comments (but not table placeholders)
            if stripped.startswith('<!--') and stripped.endswith('-->'):
                # Check for image markers
                img_match = re.match(r'<!--\s*IMAGE:\s*(\S+)', stripped)
                if img_match:
                    img_id = img_match.group(1)
                    # Insert figure (RittDoc DTD does not allow informalfigure)
                    safe_img_id = escape_xml_attr(img_id)
                    lines.append(f'''  <figure>
    <title/>
    <mediaobject>
      <imageobject>
        <imagedata fileref="{safe_img_id}.png" width="100%" scalefit="1"/>
      </imageobject>
    </mediaobject>
  </figure>''')
                continue

            # Handle markdown image syntax: ![alt text](filename)
            # This is used for fullpage images and other embedded images
            md_img_match = re.match(r'^!\[([^\]]*)\]\(([^)]+)\)$', stripped)
            if md_img_match:
                alt_text = md_img_match.group(1)
                filename = md_img_match.group(2)
                # Escape for XML attribute safety
                safe_filename = escape_xml_attr(filename)
                safe_alt = escape_xml_content(alt_text) if alt_text else ""
                # Use figure with title (RittDoc DTD does not allow informalfigure)
                if safe_alt:
                    lines.append(f'''  <figure>
    <title>{safe_alt}</title>
    <mediaobject>
      <imageobject>
        <imagedata fileref="{safe_filename}" width="100%" scalefit="1"/>
      </imageobject>
    </mediaobject>
  </figure>''')
                else:
                    lines.append(f'''  <figure>
    <title/>
    <mediaobject>
      <imageobject>
        <imagedata fileref="{safe_filename}" width="100%" scalefit="1"/>
      </imageobject>
    </mediaobject>
  </figure>''')
                continue

            # Skip HTML table-related tags that might have leaked through
            # (thead, tbody, tr, td, th tags that weren't part of a complete table)
            if re.match(r'^</?(?:thead|tbody|tr|td|th|caption)\b[^>]*>.*$', stripped, re.IGNORECASE):
                continue

            # Skip code fence markers (``` or ```language)
            if re.match(r'^`{3,}\w*$', stripped):
                continue

            # Skip markdown table separator lines (|---|---|)
            if re.match(r'^\|[\s\-:|\+]+\|$', stripped):
                continue

            # Check for table placeholders
            placeholder_match = re.match(r'__TABLE_PLACEHOLDER_(\d+)__', stripped)
            if placeholder_match:
                # Close any open lists before table
                list_stack = _close_all_lists(lines, list_stack)
                table_idx = int(placeholder_match.group(1))
                if table_idx < len(tables_in_page):
                    html_table = tables_in_page[table_idx]
                    docbook_table = html_table_to_docbook(html_table)
                    if docbook_table:
                        lines.append(docbook_table)
                continue

            # Headings - close any open list before starting new section
            # Handle both "# Title" and "#Title" (with or without space)
            # Match heading levels from h6 (######) down to h1 (#)
            h6_match = re.match(r'^######\s*(.+)$', stripped)
            h5_match = re.match(r'^#####\s*(.+)$', stripped) if not h6_match else None
            h4_match = re.match(r'^####\s*(.+)$', stripped) if not h5_match and not h6_match else None
            h3_match = re.match(r'^###\s*(.+)$', stripped) if not h4_match and not h5_match and not h6_match else None
            h2_match = re.match(r'^##\s*(.+)$', stripped) if not h3_match and not h4_match and not h5_match and not h6_match else None
            # h1_match disabled - chapters should come from PDF bookmarks/outlines or heuristics, not AI inference
            # h1_match = re.match(r'^#\s*(.+)$', stripped) if not h2_match and not h3_match and not h4_match and not h5_match and not h6_match else None

            # Helper function to close sections down to a specific level
            def close_sections_to_level(target_level):
                """Close all sections at or deeper than target_level.
                Level 0=chapter, 1=sect1, 2=sect2, 3=sect3, 4=sect4, 5=sect5
                Use <= to also close the target level (for starting a new section at same level)"""
                nonlocal current_sect5, current_sect4, current_sect3, current_sect2, current_sect1, current_chapter
                if current_sect5 and target_level <= 5:
                    lines.append('            </sect5>')
                    current_sect5 = None
                if current_sect4 and target_level <= 4:
                    lines.append('          </sect4>')
                    current_sect4 = None
                if current_sect3 and target_level <= 3:
                    lines.append('        </sect3>')
                    current_sect3 = None
                if current_sect2 and target_level <= 2:
                    lines.append('      </sect2>')
                    current_sect2 = None
                if current_sect1 and target_level <= 1:
                    lines.append('    </sect1>')
                    current_sect1 = None
                if current_chapter and target_level <= 0:
                    lines.append('  </chapter>')
                    current_chapter = None

            # Helper to ensure parent sections exist up to a level
            def ensure_parent_sections(level):
                """Ensure all parent sections exist for the given level."""
                nonlocal current_chapter, current_sect1, current_sect2, current_sect3, current_sect4
                nonlocal chapter_counter, sect1_counter, sect2_counter, sect3_counter, sect4_counter, sect5_counter
                if not current_chapter:
                    chapter_counter += 1
                    # Reset all section counters for new chapter
                    sect1_counter = 0
                    sect2_counter = 0
                    sect3_counter = 0
                    sect4_counter = 0
                    sect5_counter = 0
                    chapter_id = f"ch{chapter_counter:04d}"
                    chapter_label = _generate_chapter_label(chapter_counter)
                    lines.append(f'  <chapter id="{chapter_id}" label="{chapter_label}">')
                    lines.append('    <title/>')
                    current_chapter = chapter_id
                if level >= 1 and not current_sect1:
                    sect1_counter += 1
                    # Reset nested section counters
                    sect2_counter = 0
                    sect3_counter = 0
                    sect4_counter = 0
                    sect5_counter = 0
                    sect1_id = f"{current_chapter}s{sect1_counter:02d}"
                    lines.append(f'    <sect1 id="{sect1_id}">')
                    lines.append('      <title/>')
                    current_sect1 = sect1_id
                if level >= 2 and not current_sect2:
                    sect2_counter += 1
                    # Reset nested section counters
                    sect3_counter = 0
                    sect4_counter = 0
                    sect5_counter = 0
                    sect2_id = f"{current_sect1}s{sect2_counter:02d}"
                    lines.append(f'      <sect2 id="{sect2_id}">')
                    lines.append('        <title/>')
                    current_sect2 = sect2_id
                if level >= 3 and not current_sect3:
                    sect3_counter += 1
                    # Reset nested section counters
                    sect4_counter = 0
                    sect5_counter = 0
                    sect3_id = f"{current_sect2}s{sect3_counter:02d}"
                    lines.append(f'        <sect3 id="{sect3_id}">')
                    lines.append('          <title/>')
                    current_sect3 = sect3_id
                if level >= 4 and not current_sect4:
                    sect4_counter += 1
                    # Reset nested section counters
                    sect5_counter = 0
                    sect4_id = f"{current_sect3}s{sect4_counter:02d}"
                    lines.append(f'          <sect4 id="{sect4_id}">')
                    lines.append('            <title/>')
                    current_sect4 = sect4_id

            # Process headings from deepest to shallowest
            # New mapping (Option A): # is top-level, everything shifts down
            # # → sect1, ## → sect2, ### → sect3, #### → sect4, ##### → sect5, ###### → sect5 (capped)

            # ###### → sect5 (capped at deepest level)
            if h6_match:
                list_stack = _close_all_lists(lines, list_stack)
                close_sections_to_level(5)  # Close any existing sect5
                ensure_parent_sections(4)   # Ensure we have chapter, sect1-4
                title = re.sub(r'^#+\s*', '', h6_match.group(1).strip())
                title = _strip_font_annotation(title)  # Remove font size annotations
                title = clean_title_markdown(title)
                sect5_counter += 1
                sect5_id = f"{current_sect4}s{sect5_counter:02d}"
                lines.append(f'            <sect5 id="{sect5_id}">')
                lines.append(f'              <title>{escape_xml(title)}</title>')
                current_sect5 = sect5_id
                continue

            # ##### → sect5 (capped at deepest level, same as ######)
            elif h5_match:
                list_stack = _close_all_lists(lines, list_stack)
                close_sections_to_level(5)  # Close any existing sect5
                ensure_parent_sections(4)   # Ensure we have chapter, sect1-4
                title = re.sub(r'^#+\s*', '', h5_match.group(1).strip())
                title = _strip_font_annotation(title)  # Remove font size annotations
                title = clean_title_markdown(title)
                sect5_counter += 1
                sect5_id = f"{current_sect4}s{sect5_counter:02d}"
                lines.append(f'            <sect5 id="{sect5_id}">')
                lines.append(f'              <title>{escape_xml(title)}</title>')
                current_sect5 = sect5_id
                continue

            # #### → sect4
            elif h4_match:
                list_stack = _close_all_lists(lines, list_stack)
                close_sections_to_level(4)  # Close sect5, sect4
                ensure_parent_sections(3)   # Ensure we have chapter, sect1-3
                title = re.sub(r'^#+\s*', '', h4_match.group(1).strip())
                title = _strip_font_annotation(title)  # Remove font size annotations
                title = clean_title_markdown(title)
                sect4_counter += 1
                # Reset nested section counters
                sect5_counter = 0
                sect4_id = f"{current_sect3}s{sect4_counter:02d}"
                lines.append(f'          <sect4 id="{sect4_id}">')
                lines.append(f'            <title>{escape_xml(title)}</title>')
                current_sect4 = sect4_id
                continue

            # ### → sect3
            elif h3_match:
                list_stack = _close_all_lists(lines, list_stack)
                close_sections_to_level(3)  # Close sect5, sect4, sect3
                ensure_parent_sections(2)   # Ensure we have chapter, sect1-2
                title = re.sub(r'^#+\s*', '', h3_match.group(1).strip())
                title = _strip_font_annotation(title)  # Remove font size annotations
                title = clean_title_markdown(title)
                sect3_counter += 1
                # Reset nested section counters
                sect4_counter = 0
                sect5_counter = 0
                sect3_id = f"{current_sect2}s{sect3_counter:02d}"
                lines.append(f'        <sect3 id="{sect3_id}">')
                lines.append(f'          <title>{escape_xml(title)}</title>')
                current_sect3 = sect3_id
                continue

            # ## → sect2
            elif h2_match:
                list_stack = _close_all_lists(lines, list_stack)
                close_sections_to_level(2)  # Close sect5-2
                ensure_parent_sections(1)   # Ensure we have chapter, sect1
                title = re.sub(r'^#+\s*', '', h2_match.group(1).strip())
                title = _strip_font_annotation(title)  # Remove font size annotations
                title = clean_title_markdown(title)
                sect2_counter += 1
                # Reset nested section counters
                sect3_counter = 0
                sect4_counter = 0
                sect5_counter = 0
                sect2_id = f"{current_sect1}s{sect2_counter:02d}"
                lines.append(f'      <sect2 id="{sect2_id}">')
                lines.append(f'        <title>{escape_xml(title)}</title>')
                current_sect2 = sect2_id
                continue

            # # → sect1 (top-level section, chapters come from PDF bookmarks)
            h1_match = re.match(r'^#\s+(.+)$', stripped) if not h2_match and not h3_match and not h4_match and not h5_match and not h6_match else None
            if h1_match:
                list_stack = _close_all_lists(lines, list_stack)
                close_sections_to_level(1)  # Close sect5-1
                ensure_parent_sections(0)   # Ensure we have chapter
                title = re.sub(r'^#+\s*', '', h1_match.group(1).strip())
                title = _strip_font_annotation(title)  # Remove font size annotations
                title = clean_title_markdown(title)
                sect1_counter += 1
                # Reset nested section counters
                sect2_counter = 0
                sect3_counter = 0
                sect4_counter = 0
                sect5_counter = 0
                sect1_id = f"{current_chapter}s{sect1_counter:02d}"
                lines.append(f'    <sect1 id="{sect1_id}">')
                lines.append(f'      <title>{escape_xml(title)}</title>')
                current_sect1 = sect1_id
                continue

            # Lists - handle various bullet symbols
            # Nesting level is determined by indentation (leading spaces), not bullet type
            # This preserves the visual hierarchy from the PDF
            bullet_match = re.match(r'^([•○▪▸►‣⁃◦◆◇\-\*\+])\s+(.*)$', stripped)

            # Ordered lists - handle various formats:
            # 1. or 1) or (1) - numeric
            # a. or a) or (a) - lowercase alpha
            # A. or A) or (A) - uppercase alpha
            # i. or i) or (i) - lowercase roman
            # I. or I) or (I) - uppercase roman
            ordered_match = re.match(r'^(?:\d+[\.\)]|\(\d+\)|[a-zA-Z][\.\)]|\([a-zA-Z]\)|[ivxlcdmIVXLCDM]+[\.\)]|\([ivxlcdmIVXLCDM]+\))\s+(.*)$', stripped)

            if bullet_match or ordered_match:
                # Determine list type, level, and extract item text
                # Level is determined by indentation (leading spaces), not bullet symbol
                if bullet_match:
                    current_list_type = 'itemized'
                    item_text = bullet_match.group(2).strip()
                    current_level = _get_list_level(line)
                else:
                    current_list_type = 'ordered'
                    item_text = ordered_match.group(1).strip()
                    current_level = _get_list_level(line)  # Use indentation for ordered lists too

                # Convert markdown formatting in item text (handles escaping internally)
                item_text = convert_markdown_formatting(item_text)

                # Get current nesting depth
                current_depth = len(list_stack)

                # If we need to go deeper (new nested list)
                if current_level > current_depth:
                    # Open new nested list(s) to reach the target level
                    # The first nested list goes inside the previous listitem (which we left open)
                    # But subsequent nested lists need their own listitem wrappers
                    first_nested = True
                    while len(list_stack) < current_level:
                        depth = len(list_stack)
                        base_indent = '    ' + '  ' * depth
                        list_tag = 'itemizedlist' if current_list_type == 'itemized' else 'orderedlist'
                        # After the first nested list, each additional list needs its own listitem wrapper
                        # because itemizedlist cannot directly contain another itemizedlist
                        if not first_nested:
                            # Add a listitem to contain this nested list (left open for nesting)
                            item_indent = '    ' + '  ' * (depth - 1)
                            lines.append(f'{item_indent}  <listitem>')
                        lines.append(f'{base_indent}<{list_tag}>')
                        list_stack.append((current_list_type, len(list_stack) + 1))
                        first_nested = False

                # If we need to go shallower (close nested lists)
                elif current_level < current_depth:
                    # Close lists until we reach the target level
                    # First, close the last open listitem
                    first_close = True
                    while len(list_stack) > current_level:
                        old_type, _ = list_stack.pop()
                        depth = len(list_stack)
                        base_indent = '    ' + '  ' * depth
                        item_inner_indent = base_indent + '  '
                        old_tag = 'itemizedlist' if old_type == 'itemized' else 'orderedlist'
                        # Close the last open listitem first
                        if first_close:
                            lines.append(f'{item_inner_indent}</listitem>')
                            first_close = False
                        lines.append(f'{base_indent}</{old_tag}>')
                        # Close the containing listitem when exiting a nested list
                        if list_stack:
                            lines.append(f'{base_indent}</listitem>')

                    # If at same level but different type, close and reopen
                    if list_stack and list_stack[-1][0] != current_list_type:
                        old_type, _ = list_stack.pop()
                        depth = len(list_stack)
                        base_indent = '    ' + '  ' * depth
                        old_tag = 'itemizedlist' if old_type == 'itemized' else 'orderedlist'
                        lines.append(f'{base_indent}</{old_tag}>')
                        list_tag = 'itemizedlist' if current_list_type == 'itemized' else 'orderedlist'
                        lines.append(f'{base_indent}<{list_tag}>')
                        list_stack.append((current_list_type, current_level))

                # Same level - close previous listitem before starting new one
                elif list_stack:
                    # Check if type changed at same level
                    if list_stack[-1][0] != current_list_type:
                        old_type, _ = list_stack.pop()
                        depth = len(list_stack)
                        base_indent = '    ' + '  ' * depth
                        old_tag = 'itemizedlist' if old_type == 'itemized' else 'orderedlist'
                        lines.append(f'{base_indent}</{old_tag}>')
                        list_tag = 'itemizedlist' if current_list_type == 'itemized' else 'orderedlist'
                        lines.append(f'{base_indent}<{list_tag}>')
                        list_stack.append((current_list_type, current_level))
                    else:
                        # Same type, same level - close previous listitem
                        depth = len(list_stack)
                        item_indent = '    ' + '  ' * (depth - 1)
                        lines.append(f'{item_indent}  </listitem>')

                # Start first list if stack is empty
                if not list_stack:
                    list_tag = 'itemizedlist' if current_list_type == 'itemized' else 'orderedlist'
                    lines.append(f'    <{list_tag}>')
                    list_stack.append((current_list_type, 1))

                # Add list item at current depth (leave it open for potential nested content)
                depth = len(list_stack)
                item_indent = '    ' + '  ' * (depth - 1)
                lines.append(f'{item_indent}  <listitem><para>{item_text}</para>')
                continue  # Don't process this line further

            # Handle indented lines without bullet markers
            # These are lines with leading whitespace that should maintain their indentation
            # (e.g., form fill-in options, continuation text)
            leading_spaces = len(line) - len(line.lstrip())
            if leading_spaces >= 2 and stripped:
                # Line is indented - treat as a list item without explicit bullet
                item_text = convert_markdown_formatting(stripped)
                indent_level = (leading_spaces + 1) // 2  # Convert spaces to level (2 spaces = level 1)
                indent_level = max(1, min(indent_level, 5))  # Clamp to reasonable range

                # If we're already in a list, add as item at appropriate level
                if list_stack:
                    current_depth = len(list_stack)
                    # Adjust to match indentation level - close nested lists if needed
                    first_close = True
                    while len(list_stack) > indent_level:
                        old_type, _ = list_stack.pop()
                        depth = len(list_stack)
                        base_indent = '    ' + '  ' * depth
                        item_inner_indent = base_indent + '  '
                        old_tag = 'itemizedlist' if old_type == 'itemized' else 'orderedlist'
                        # Close the last open listitem first
                        if first_close:
                            lines.append(f'{item_inner_indent}</listitem>')
                            first_close = False
                        lines.append(f'{base_indent}</{old_tag}>')
                        if list_stack:
                            lines.append(f'{base_indent}</listitem>')

                    # Close previous listitem at same level before adding new one
                    if first_close:
                        depth = len(list_stack)
                        item_indent = '    ' + '  ' * (depth - 1)
                        lines.append(f'{item_indent}  </listitem>')

                    depth = len(list_stack)
                    item_indent = '    ' + '  ' * (depth - 1)
                    # These are continuation items - keep them open in case of nesting
                    lines.append(f'{item_indent}  <listitem><para>{item_text}</para>')
                else:
                    # Start a new itemized list for indented content
                    lines.append('    <itemizedlist>')
                    list_stack.append(('itemized', 1))
                    lines.append(f'      <listitem><para>{item_text}</para>')
                continue

            # Regular paragraphs (anything that didn't match above)
            # Close all open lists
            list_stack = _close_all_lists(lines, list_stack)

            # Ensure we're in a chapter - create a proper chapter with ID
            if not current_chapter:
                chapter_counter += 1
                sect1_counter = 0  # Reset section counters for new chapter
                sect2_counter = 0
                sect3_counter = 0
                sect4_counter = 0
                sect5_counter = 0
                chapter_id = f"ch{chapter_counter:04d}"
                chapter_label = _generate_chapter_label(chapter_counter)
                lines.append(f'  <chapter id="{chapter_id}" label="{chapter_label}">')
                lines.append('    <title/>')
                current_chapter = chapter_id

            # Convert markdown inline formatting using helper function
            text = convert_markdown_formatting(stripped)

            lines.append(f'    <para>{text}</para>')

        # NOTE: We no longer inject images from multimedia.xml here.
        # The AI extraction already identifies and places images accurately with <!-- IMAGE: ... --> markers.
        # Injecting from multimedia.xml would cause duplicates.
        # Images are handled by:
        # 1. AI's <!-- IMAGE: id --> markers converted to mediaobject
        # 2. Markdown ![alt](filename) syntax converted to mediaobject
        # 3. Fullpage images from pages with render_mode="fullpage"

    # Close any open sections (from deepest to shallowest per DTD requirements)
    # Close all open lists first
    _close_all_lists(lines, list_stack)
    if current_sect5:
        lines.append('            </sect5>')
    if current_sect4:
        lines.append('          </sect4>')
    if current_sect3:
        lines.append('        </sect3>')
    if current_sect2:
        lines.append('      </sect2>')
    if current_sect1:
        lines.append('    </sect1>')
    if current_chapter:
        lines.append('  </chapter>')

    # Close front matter if still open (shouldn't happen but just in case)
    if in_front_matter:
        lines.append('    </sect1>')
        lines.append('  </preface>')

    # Close back matter if open
    if in_back_matter:
        lines.append('    </sect1>')
        lines.append('  </appendix>')

    lines.append('</book>')

    return '\n'.join(lines)


def ensure_docbook42_doctype(xml_content: str) -> str:
    """Ensure the XML has proper DocBook 4.2 DOCTYPE declaration."""
    # Remove any existing DOCTYPE
    xml_content = re.sub(r'<!DOCTYPE[^>]*>', '', xml_content, flags=re.IGNORECASE)

    # Remove XML declaration if present
    xml_content = re.sub(r'<\?xml[^?]*\?>\s*', '', xml_content, flags=re.IGNORECASE)

    # Find the root element
    root_match = re.search(r'<(book|article|chapter|part)\b', xml_content, re.IGNORECASE)
    if root_match:
        root_tag = root_match.group(1).lower()
        doctype = f'<!DOCTYPE {root_tag} PUBLIC "{DOCBOOK42_PUBLIC}" "{DOCBOOK42_SYSTEM}">'
    else:
        doctype = f'<!DOCTYPE book PUBLIC "{DOCBOOK42_PUBLIC}" "{DOCBOOK42_SYSTEM}">'

    return f'<?xml version="1.0" encoding="UTF-8"?>\n{doctype}\n{xml_content.strip()}'


# =============================================================================
# MULTIMEDIA.XML PARSER
# =============================================================================

def parse_multimedia_xml(xml_path: Path) -> Dict[int, List[Dict]]:
    """
    Parse the MultiMedia.xml file created by Multipage_Image_Extractor.
    Returns dict: page_num -> list of image info dicts
    """
    images_by_page: Dict[int, List[Dict]] = {}

    if not xml_path.exists():
        return images_by_page

    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()

        for page_el in root.findall(".//page"):
            page_num = int(page_el.get("index", "0"))
            if page_num == 0:
                continue

            page_images = []
            for media_el in page_el.findall("media"):
                media_type = media_el.get("type", "")
                # Skip tables
                if media_type == "table":
                    continue

                img_info = {
                    "id": media_el.get("id", ""),
                    "filename": media_el.get("filename", ""),
                    "type": media_type,
                    "x": float(media_el.get("x", 0)),
                    "y": float(media_el.get("y", 0)),
                    "width": float(media_el.get("width", 0)),
                    "height": float(media_el.get("height", 0)),
                }

                if not img_info["filename"] and img_info["id"]:
                    img_info["filename"] = img_info["id"] + ".png"

                if img_info["id"] or img_info["filename"]:
                    page_images.append(img_info)

            if page_images:
                page_images.sort(key=lambda x: x["y"])
                images_by_page[page_num] = page_images

    except Exception as e:
        print(f"  Error parsing MultiMedia.xml: {e}")

    return images_by_page


def get_fullpage_image_pages(xml_path: Path) -> Dict[int, Dict]:
    """
    Parse the MultiMedia.xml file to find pages that were rendered as full-page images.
    These are complex pages (forms, RTL text, etc.) that should skip AI text extraction.

    Returns dict: page_num -> {filename, reason} for pages with render_mode="fullpage"
    """
    fullpage_pages: Dict[int, Dict] = {}

    if not xml_path.exists():
        return fullpage_pages

    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()

        for page_el in root.findall(".//page"):
            render_mode = page_el.get("render_mode", "")
            if render_mode == "fullpage":
                page_num = int(page_el.get("index", "0"))
                if page_num == 0:
                    continue

                render_reason = page_el.get("render_reason", "Complex page")

                # Find the fullpage media element to get the filename
                filename = f"p{page_num}_fullpage.png"  # default
                for media_el in page_el.findall("media"):
                    if media_el.get("type") == "fullpage":
                        filename = media_el.get("file", filename)
                        break

                fullpage_pages[page_num] = {
                    "filename": filename,
                    "reason": render_reason
                }

    except Exception as e:
        print(f"  Error parsing MultiMedia.xml for fullpage pages: {e}")

    return fullpage_pages


# =============================================================================
# MAIN CONVERSION CLASS
# =============================================================================

class VisionPDFConverter:
    """
    Convert PDF to DocBook 4.2 using Claude Vision API.
    Processes each page as a high-DPI image with batch processing support.
    """

    def __init__(self, config: Optional[VisionConfig] = None):
        self.config = config or VisionConfig()
        self.processor = ClaudeVisionProcessor(self.config)

    def _get_progress_file(self, out_dir: Path, pdf_stem: str) -> Path:
        """Get path to progress tracking file."""
        return out_dir / f".{pdf_stem}_progress.json"

    def _load_progress(self, progress_file: Path) -> Dict:
        """Load progress from file if it exists."""
        if progress_file.exists():
            try:
                with open(progress_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"  Warning: Could not load progress file: {e}")
        return {'completed_pages': [], 'content': {}}

    def _save_progress(self, progress_file: Path, progress: Dict) -> None:
        """Save progress to file."""
        try:
            with open(progress_file, 'w') as f:
                json.dump(progress, f)
        except Exception as e:
            print(f"  Warning: Could not save progress: {e}")

    def _process_page(
        self,
        pdf_path_p: Path,
        page_num: int,
        total_pages: int
    ) -> Tuple[str, List[Tuple[int, Dict]]]:
        """
        Process a single page and return content and tables needing review.
        Includes retry logic for content filtering errors, image size limits, and 500 errors.
        """
        import time
        tables_needing_review = []

        # Try different image formats/settings if content filter is triggered
        attempts = [
            {"format": "png", "dpi": self.config.dpi, "media_type": "image/png"},
            {"format": "jpeg", "dpi": self.config.dpi, "media_type": "image/jpeg"},
            {"format": "jpeg", "dpi": 200, "media_type": "image/jpeg"},  # Lower DPI
        ]

        result = None
        image_data = None
        max_retries_500 = 3  # Retry up to 3 times for 500 errors

        for attempt_idx, attempt in enumerate(attempts):
            # Render page with header/footer cropping to remove running headers/footers
            current_dpi = attempt["dpi"]
            current_format = attempt["format"]
            image_data = render_pdf_page(
                pdf_path_p, page_num,
                dpi=current_dpi,
                output_format=current_format,
                crop_header_pct=self.config.crop_header_pct,
                crop_footer_pct=self.config.crop_footer_pct
            )
            if not image_data:
                return f"<!-- Page {page_num} -->\n<!-- ERROR: Failed to render page -->", []

            # Check image size and reduce DPI if needed to stay under 5MB API limit
            image_size = len(image_data)
            if image_size > MAX_IMAGE_SIZE_BYTES:
                print(f"  Page {page_num}: Image size {image_size / (1024*1024):.2f}MB exceeds 5MB limit at {current_dpi} DPI")

                # Try progressively lower DPI until under limit
                for fallback_dpi in FALLBACK_DPI_LEVELS:
                    if fallback_dpi >= current_dpi:
                        continue  # Skip DPI levels >= current

                    print(f"    Trying {fallback_dpi} DPI...", end=" ", flush=True)
                    image_data = render_pdf_page(
                        pdf_path_p, page_num,
                        dpi=fallback_dpi,
                        output_format="jpeg",  # JPEG is smaller
                        crop_header_pct=self.config.crop_header_pct,
                        crop_footer_pct=self.config.crop_footer_pct
                    )
                    if image_data:
                        image_size = len(image_data)
                        print(f"{image_size / (1024*1024):.2f}MB")
                        if image_size <= MAX_IMAGE_SIZE_BYTES:
                            current_dpi = fallback_dpi
                            current_format = "jpeg"
                            attempt["media_type"] = "image/jpeg"
                            break
                    else:
                        print("FAILED")

                # Check if we got under the limit
                if image_size > MAX_IMAGE_SIZE_BYTES:
                    print(f"  Page {page_num}: Could not reduce image size below 5MB limit")
                    return f"<!-- Page {page_num} -->\n<!-- ERROR: Image too large ({image_size / (1024*1024):.2f}MB) even at lowest DPI -->", []

            # Extract content with Vision AI (with retry for 500 errors)
            retry_count = 0
            while retry_count <= max_retries_500:
                result = self.processor.extract_page_content(
                    image_data,
                    page_num,
                    total_pages,
                    media_type=attempt["media_type"]
                )

                # Check for 500 internal server error (transient, retry with backoff)
                if result.get('error') == 'internal_server_error':
                    retry_count += 1
                    if retry_count <= max_retries_500:
                        wait_time = 2 ** retry_count  # Exponential backoff: 2, 4, 8 seconds
                        print(f"  Retry {retry_count}/{max_retries_500} for page {page_num} after {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    else:
                        print(f"  Page {page_num}: All 500 error retries exhausted")
                        break
                else:
                    # Not a 500 error, exit retry loop
                    break

            # Check if content filter error
            if result.get('error') == 'content_filter_blocked':
                if attempt_idx < len(attempts) - 1:
                    next_attempt = attempts[attempt_idx + 1]
                    print(f"  Retrying page {page_num} with {next_attempt['format'].upper()} format at {next_attempt['dpi']} DPI...")
                    continue
                else:
                    print(f"  Page {page_num}: All retry attempts failed due to content filter")
                    break
            # Check if image size error (shouldn't happen after pre-check, but just in case)
            elif result.get('error') == 'image_size_exceeded':
                if attempt_idx < len(attempts) - 1:
                    next_attempt = attempts[attempt_idx + 1]
                    print(f"  Retrying page {page_num} with lower DPI {next_attempt['dpi']}...")
                    continue
                else:
                    print(f"  Page {page_num}: All retry attempts failed due to image size")
                    break
            else:
                # Success or different error, stop retrying
                break

        confidence = result.get('confidence', 0.0)

        # Check if tables need review
        for table_info in result.get('tables', []):
            if not table_info.get('valid', True):
                tables_needing_review.append((page_num, table_info))

        # Get content
        content = result.get('content', '')
        if not content.startswith(f'<!-- Page {page_num}'):
            content = f"<!-- Page {page_num} -->\n{content}"

        # Second pass for low confidence
        if result.get('needs_review') and self.config.enable_second_pass:
            for table_info in result.get('tables', []):
                refined = self.processor.refine_table(
                    image_data,
                    page_num,
                    table_info.get('html', '')
                )
                if refined:
                    old_table = table_info.get('html', '')
                    if old_table in content:
                        content = content.replace(old_table, refined)

        return content, tables_needing_review

    def convert(
        self,
        pdf_path: str,
        output_dir: str,
        multimedia_dir: Optional[str] = None,
        bookmark_hierarchy: Optional[Dict] = None
    ) -> Path:
        """
        Convert PDF to DocBook 4.2 XML with batch processing.

        Args:
            pdf_path: Path to input PDF
            output_dir: Output directory for XML and intermediate files
            multimedia_dir: Path to multimedia folder (for image injection)
            bookmark_hierarchy: Optional bookmark hierarchy for chapter/section structure

        Returns:
            Path to output DocBook XML file
        """
        import time

        pdf_path_p = Path(pdf_path).expanduser().resolve()
        out_dir = Path(output_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        # Resolve multimedia directory
        if multimedia_dir:
            multimedia_dir_p = Path(multimedia_dir).expanduser().resolve()
        else:
            multimedia_dir_p = out_dir / f"{pdf_path_p.stem}_MultiMedia"

        print(f"  PDF: {pdf_path_p}")
        print(f"  Output: {out_dir}")
        print(f"  MultiMedia: {multimedia_dir_p}")
        print(f"  Model: {self.config.model}")
        print(f"  DPI: {self.config.dpi}")
        print(f"  Batch size: {self.config.batch_size}")

        # Get page count
        total_pages = get_pdf_page_count(pdf_path_p)
        print(f"  Total pages: {total_pages}")

        # Parse multimedia.xml to find fullpage image pages (skip AI extraction for these)
        multimedia_xml_path = multimedia_dir_p / f"{pdf_path_p.stem}_MultiMedia.xml"
        if not multimedia_xml_path.exists():
            multimedia_xml_path = out_dir / f"{pdf_path_p.stem}_MultiMedia.xml"
        fullpage_pages = get_fullpage_image_pages(multimedia_xml_path)
        if fullpage_pages:
            print(f"  Fullpage image pages (skipping AI extraction): {sorted(fullpage_pages.keys())}")

        if total_pages == 0:
            raise RuntimeError("Could not read PDF or PDF has no pages")

        # Load progress if resuming
        progress_file = self._get_progress_file(out_dir, pdf_path_p.stem)
        progress = self._load_progress(progress_file)

        completed_pages = set(progress.get('completed_pages', []))
        all_content = progress.get('content', {})

        # Determine start page
        start_page = self.config.resume_from_page
        if completed_pages:
            last_completed = max(completed_pages)
            if start_page <= last_completed:
                start_page = last_completed + 1
                print(f"  Resuming from page {start_page} ({len(completed_pages)} pages already completed)")

        # Track all tables needing review
        tables_needing_review = []

        # Process pages in batches
        pages_to_process = [p for p in range(start_page, total_pages + 1) if p not in completed_pages]
        total_to_process = len(pages_to_process)

        if total_to_process == 0:
            print("  All pages already processed!")
        else:
            print(f"\n  Processing {total_to_process} pages in batches of {self.config.batch_size}...")
            print("=" * 60)

        batch_num = 0
        processed_count = 0
        start_time = time.time()

        for i in range(0, len(pages_to_process), self.config.batch_size):
            batch = pages_to_process[i:i + self.config.batch_size]
            batch_num += 1
            batch_start_time = time.time()

            print(f"\n  Batch {batch_num}: Pages {batch[0]}-{batch[-1]} ({len(batch)} pages)")
            print("-" * 40)

            for page_num in batch:
                print(f"    Page {page_num}/{total_pages}...", end=" ", flush=True)

                # Check if this is a fullpage image page (skip AI extraction)
                if page_num in fullpage_pages:
                    fullpage_info = fullpage_pages[page_num]
                    filename = fullpage_info["filename"]
                    reason = fullpage_info["reason"]

                    # Generate simple markdown content with image reference
                    content = f"""<!-- Page {page_num} -->
<!-- FULLPAGE_IMAGE: This page was rendered as a full-page image due to: {reason} -->
<!-- CONFIDENCE: 100% -->

![Page {page_num} - Full Page Image]({filename})

<!-- The content of this page is contained in the image above -->
"""
                    all_content[str(page_num)] = content
                    completed_pages.add(page_num)
                    processed_count += 1
                    print(f"FULLPAGE_IMAGE (skipped AI)")
                    continue

                try:
                    content, page_tables = self._process_page(pdf_path_p, page_num, total_pages)

                    # Store content
                    all_content[str(page_num)] = content
                    completed_pages.add(page_num)
                    tables_needing_review.extend(page_tables)
                    processed_count += 1

                    # Calculate confidence from content
                    conf_match = re.search(r'<!--\s*CONFIDENCE:\s*(\d+)%?\s*-->', content)
                    confidence = float(conf_match.group(1)) / 100.0 if conf_match else 0.9

                    print(f"OK ({confidence:.0%})")

                except Exception as e:
                    print(f"ERROR: {e}")
                    all_content[str(page_num)] = f"<!-- Page {page_num} -->\n<!-- ERROR: {e} -->"
                    completed_pages.add(page_num)

            # Save progress after each batch
            if self.config.save_intermediate:
                progress = {
                    'completed_pages': list(completed_pages),
                    'content': all_content
                }
                self._save_progress(progress_file, progress)

                # Also save intermediate markdown
                interim_md_path = out_dir / f"{pdf_path_p.stem}_intermediate_batch{batch_num}.md"
                sorted_content = [all_content.get(str(p), '') for p in sorted(int(k) for k in all_content.keys())]
                interim_md_path.write_text('\n\n'.join(sorted_content), encoding='utf-8')

            batch_elapsed = time.time() - batch_start_time
            total_elapsed = time.time() - start_time
            pages_remaining = total_to_process - processed_count

            if processed_count > 0:
                avg_time_per_page = total_elapsed / processed_count
                eta_seconds = pages_remaining * avg_time_per_page
                eta_minutes = eta_seconds / 60

                print(f"\n  Batch {batch_num} completed in {batch_elapsed:.1f}s")
                print(f"  Progress: {processed_count}/{total_to_process} pages ({100*processed_count/total_to_process:.1f}%)")
                print(f"  ETA: {eta_minutes:.1f} minutes ({pages_remaining} pages remaining)")

        # Combine all pages into Markdown (in page order)
        sorted_pages = sorted(int(k) for k in all_content.keys())
        markdown_content = '\n\n'.join(all_content.get(str(p), '') for p in sorted_pages)

        # Save final intermediate Markdown
        md_path = out_dir / f"{pdf_path_p.stem}_intermediate.md"
        md_path.write_text(markdown_content, encoding='utf-8')
        print(f"\n  Intermediate MD: {md_path}")

        # Parse multimedia.xml for image injection
        multimedia_xml_path = multimedia_dir_p / f"{pdf_path_p.stem}_MultiMedia.xml"
        if not multimedia_xml_path.exists():
            multimedia_xml_path = out_dir / f"{pdf_path_p.stem}_MultiMedia.xml"

        images_by_page = parse_multimedia_xml(multimedia_xml_path)
        print(f"  Images from multimedia.xml: {sum(len(v) for v in images_by_page.values())}")

        # Extract book title from PDF metadata or use filename
        book_title = None
        try:
            doc = fitz.open(str(pdf_path_p))
            metadata = doc.metadata
            if metadata and metadata.get('title'):
                book_title = metadata['title'].strip()
                print(f"  Book title (from PDF metadata): {book_title}")
            doc.close()
        except Exception as e:
            print(f"  Warning: Could not read PDF metadata: {e}")

        # Fall back to filename (with spaces instead of underscores/dashes)
        if not book_title:
            book_title = pdf_path_p.stem.replace('_', ' ').replace('-', ' ')
            print(f"  Book title (from filename): {book_title}")

        # Convert to DocBook
        print("  Converting to DocBook 4.2...")
        docbook_content = markdown_to_docbook(
            markdown_content,
            images_by_page,
            book_title=book_title,
            bookmark_hierarchy=bookmark_hierarchy
        )
        docbook_content = ensure_docbook42_doctype(docbook_content)

        # Save DocBook XML
        xml_path = out_dir / f"{pdf_path_p.stem}_docbook42.xml"
        xml_path.write_text(docbook_content, encoding='utf-8')
        print(f"  DocBook XML: {xml_path}")

        # Generate debug Markdown from DocBook (via pandoc)
        debug_md_path = out_dir / f"{pdf_path_p.stem}_debug.md"
        try:
            subprocess.run(
                ["pandoc", "-f", "docbook", "-t", "markdown", "-o", str(debug_md_path), str(xml_path)],
                check=True,
                capture_output=True
            )
            print(f"  Debug MD: {debug_md_path}")
        except Exception as e:
            print(f"  Warning: Could not generate debug MD: {e}")

        # Clean up progress file on successful completion
        if progress_file.exists():
            progress_file.unlink()
            print("  Progress file cleaned up")

        # Report tables needing review
        if tables_needing_review:
            print(f"\n  Tables needing manual review: {len(tables_needing_review)}")
            for page_num, table_info in tables_needing_review[:10]:  # Show first 10
                print(f"    - Page {page_num}: {table_info.get('errors', ['validation failed'])}")
            if len(tables_needing_review) > 10:
                print(f"    ... and {len(tables_needing_review) - 10} more")

        total_time = time.time() - start_time
        print(f"\n  Total processing time: {total_time/60:.1f} minutes")
        print(f"  Average time per page: {total_time/total_pages:.1f}s")

        return xml_path


# =============================================================================
# CLI
# =============================================================================

def main():
    import argparse

    ap = argparse.ArgumentParser(
        description="Convert PDF to DocBook 4.2 using Claude Vision API (page-by-page)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Basic usage (uses defaults - 300 DPI, Claude Sonnet 4):
    python ai_pdf_conversion_service.py mybook.pdf

  Process large PDF with custom batch size:
    python ai_pdf_conversion_service.py mybook.pdf --batch-size 20

  Resume interrupted processing:
    python ai_pdf_conversion_service.py mybook.pdf --resume-from-page 150

  Use Claude Opus 4.5 for better accuracy:
    python ai_pdf_conversion_service.py mybook.pdf --model claude-opus-4-5-20251101
        """
    )
    ap.add_argument("pdf", help="Path to input PDF")
    ap.add_argument("--out", default="output", help="Output directory (default: ./output)")
    ap.add_argument(
        "--multimedia",
        default=None,
        help="Path to multimedia folder containing extracted images"
    )

    # Model options
    model_group = ap.add_argument_group("Model Options")
    model_group.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Claude model ID (default: claude-sonnet-4-20250514). Use claude-opus-4-5-20251101 for better accuracy."
    )
    model_group.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Temperature for AI (default: 0.0 for exact transcription - NO HALLUCINATIONS)"
    )
    model_group.add_argument(
        "--max-tokens",
        type=int,
        default=8192,
        help="Max tokens per page response (default: 8192)"
    )

    # Rendering options
    render_group = ap.add_argument_group("Rendering Options")
    render_group.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="DPI for rendering PDF pages (default: 300)"
    )
    render_group.add_argument(
        "--crop-header",
        type=float,
        default=0.06,
        help="Percentage of page height to crop from top for headers (default: 0.06 = 6%%)"
    )
    render_group.add_argument(
        "--crop-footer",
        type=float,
        default=0.06,
        help="Percentage of page height to crop from bottom for footers (default: 0.06 = 6%%)"
    )

    # Quality options
    quality_group = ap.add_argument_group("Quality Options")
    quality_group.add_argument(
        "--no-second-pass",
        action="store_true",
        help="Disable 2nd pass for low confidence pages"
    )
    quality_group.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.85,
        help="Confidence threshold for 2nd pass (default: 0.85)"
    )

    # Batch processing options
    batch_group = ap.add_argument_group("Batch Processing (for large PDFs)")
    batch_group.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Pages per batch (default: 10). Progress saved after each batch."
    )
    batch_group.add_argument(
        "--resume-from-page",
        type=int,
        default=1,
        help="Resume from specific page number (default: 1 or auto-resume from progress file)"
    )
    batch_group.add_argument(
        "--no-save-intermediate",
        action="store_true",
        help="Don't save intermediate progress (not recommended for large PDFs)"
    )

    # Structure options
    structure_group = ap.add_argument_group("Structure Options")
    structure_group.add_argument(
        "--bookmarks",
        type=str,
        default=None,
        help="Path to bookmark hierarchy JSON file for chapter/section structure"
    )

    args = ap.parse_args()

    config = VisionConfig(
        model=args.model,
        dpi=args.dpi,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        enable_second_pass=not args.no_second_pass,
        confidence_threshold=args.confidence_threshold,
        batch_size=args.batch_size,
        resume_from_page=args.resume_from_page,
        save_intermediate=not args.no_save_intermediate,
        crop_header_pct=args.crop_header,
        crop_footer_pct=args.crop_footer
    )

    print("=" * 60)
    print("PDF to DocBook 4.2 Converter (Claude Vision)")
    print("=" * 60)
    print(f"  Model: {config.model}")
    print(f"  DPI: {config.dpi}")
    print(f"  Temperature: {config.temperature} (zero = no hallucinations)")
    print(f"  Batch size: {config.batch_size}")
    print(f"  Header/Footer crop: {config.crop_header_pct:.0%} / {config.crop_footer_pct:.0%}")
    print("=" * 60)

    # Load bookmark hierarchy if provided
    bookmark_hierarchy = None
    if args.bookmarks:
        try:
            import json
            with open(args.bookmarks, 'r', encoding='utf-8') as f:
                bookmark_hierarchy = json.load(f)
            print(f"  Bookmarks: Loaded from {args.bookmarks}")
            print(f"    - {len(bookmark_hierarchy.get('bookmarks', []))} chapters")
            if bookmark_hierarchy.get('front_matter_end_page', -1) >= 0:
                print(f"    - Front matter: pages 1-{bookmark_hierarchy['front_matter_end_page'] + 1}")
            if bookmark_hierarchy.get('back_matter_start_page', -1) >= 0:
                print(f"    - Back matter: from page {bookmark_hierarchy['back_matter_start_page'] + 1}")
            print("=" * 60)
        except Exception as e:
            print(f"  Warning: Could not load bookmarks: {e}")

    converter = VisionPDFConverter(config)

    try:
        xml_path = converter.convert(
            args.pdf,
            args.out,
            multimedia_dir=args.multimedia,
            bookmark_hierarchy=bookmark_hierarchy
        )
        print("\n" + "=" * 60)
        print("CONVERSION COMPLETE")
        print("=" * 60)
        print(f"  Output: {xml_path}")
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Progress has been saved.")
        print("Run the same command to resume from where you left off.")
        return 130
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
