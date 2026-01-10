Semantic PDF → DocBook 4.3 Pipeline

Design & Requirements Specification

0. Context & Goal

We already have a working PDF processing pipeline that includes:

pdf_to_unified_xml.py

pdf_to_excel_columns.py

Multipage_Image_Extractor.py

We now want to implement a new architecture that:

Produces DocBook 4.3–compliant semantic XML as the core output.

Is deterministic (no nondeterministic AI in the core path).

Is media-aware (images, tables, forms) but produces a reflowable structure suitable for EPUB.

Cleanly separates:

Layout extraction,

Media/table extraction,

Semantic interpretation (heuristics),

DocBook generation.

This pipeline will be business-critical and must be robust, maintainable, and extensible.

1. Functional Requirements
1.1 Input PDFs

V1 supports born-digital PDFs with embedded text.

No OCR in V1:

If a page/book is essentially image-only (no extractable text), the pipeline must not attempt OCR; instead, it flags this for QA.

Language: English only.

No RTL (Arabic/Hebrew) support required in V1.

Typical documents:

Large books: 800–1000 pages per PDF.

Content types:

Single-column and multi-column textbooks.

Scientific/technical books (figures, tables, equations).

Nursing / medical / assessment content (forms, worksheets).

Narrative text with headings and lists.

1.2 Output Format – DocBook 4.3

The pipeline must produce DocBook 4.3 as the canonical semantic representation.

Top-level element: <book>.

Use:

<book>, <bookinfo>, <isbn> as needed.

<sect1>, <sect2>, <sect3> etc. for hierarchy.

<para> for paragraphs.

<figure> with <title> and <mediaobject>/<imageobject>/<imagedata> for images.

<table> with <title>, <tgroup>, <thead>, <tbody>, <row>, <entry> for tabular structures.

<equation><mathphrase>…</mathphrase></equation> for block-level equations.

<remark> for low-confidence/problematic structures.

Important:

We do not need to break the book into <chapter> vs <sect1> at this stage.

Chapter-level concerns will be handled downstream on the generated DocBook.

Internally, the pipeline should still detect heading levels to allow downstream chapter splitting.

1.3 Semantics & AI Usage

Core requirement: deterministic output.

Default pipeline must be purely heuristic/rule-based.

No nondeterministic GPT/LLM calls in the main path.

AI may be added later as an optional plugin, but is out of scope for this spec.

Behavior constraints:

Do not merge adjacent paragraphs beyond what geometry indicates (except cross-page continuation; see 1.6).

Do not split paragraphs that are not split in the PDF geometry.

AI-like decisions must be implemented using deterministic rules/heuristics only.

Allowed inferences (heuristic):

Infer heading levels (sect1, sect2, sect3…) based on:

Font size and weight,

Spacing above/below,

Line length (short vs long),

Typical patterns (e.g., “CHAPTER 1”, all caps).

Infer lists (bulleted/numbered) based on:

Leading markers (•, –, 1., a), etc.),

Alignment and indentation,

Grouping of similar blocks.

Perform minimal corrections only when obviously necessary:

Clean up spurious zero-width characters,

Correct broken hyphenation where extraction created bad splits.

1.4 Media, Tables, and Forms
1.4.1 Figures / Images

Use PyMuPDF (refactor Multipage_Image_Extractor) to detect:

Raster images,

Vector “images” (e.g., diagrams drawn with lines/shapes).

For each image-like region:

Record a bounding box (in a consistent coordinate system).

Extract the image to a file (e.g., PNG) and store its filename.

Add an entry in the layout model as Media.

Use media regions to:

Exclude internal text (labels or vector text) from the main column/paragraph flow.

Help determine where figures should be placed in the linear reading order.

Output:

<figure>
  <title>...</title>
  <mediaobject>
    <imageobject>
      <imagedata fileref="path/to/image.png"/>
    </imageobject>
  </mediaobject>
</figure>


In the final semantic flow, each <figure> must be placed after the nearest logical paragraph, respecting columns where possible (column-aware anchor).

1.4.2 Tables

Use Camelot or existing table-detection logic to identify genuine tables.

For each table:

Determine bounding box.

Extract cell content into a logical grid (rows × columns).

Multi-page tables:

If a table clearly spans multiple pages, it must be output as one logical <table> containing all rows across pages.

Implement a heuristic to detect continuity (same column structure, continuing header, etc.).

Text inside table areas:

Should not influence column detection or reading order for normal paragraphs.

Should be attached to table content only.

DocBook representation:

<table>
  <title>...</title>
  <tgroup cols="N">
    <thead>
      <row> <entry>...</entry> ... </row>
    </thead>
    <tbody>
      <row> <entry>...</entry> ... </row>
      ...
    </tbody>
  </tgroup>
</table>

1.4.3 Forms / Assessment Pages

Form-like pages (e.g., nursing assessments with blanks and lines) should be detected as form pages.

For V1:

The entire page (or designated form region) must be rendered as an image.

Output as a <figure> with:

A generic <title> (e.g., “Form page” or similar).

<mediaobject> referencing the rendered PNG.

No field-level structured XML for forms is required at this stage.

1.5 Reading Order & Columns

Column detection should be based on text blocks only, not on figures/tables.

Images and tables act only as:

Exclusion zones (for removing internal text from main flow),

Anchorable blocks in the final reading order.

Rules:

On each page:

Cluster text blocks into columns based on x-positions and widths.

Within each column, sort blocks top-to-bottom.

Across columns:

Reading order per page is:

Column 1 top-to-bottom, then column 2 top-to-bottom, etc.

Some refinements may be necessary for full-width headings or figures; handle those by assigning them a special column or treating them as full-width with their own ordering rules.

Figures/tables:

Are later inserted into the flow based on their y-position and proximity to their anchor paragraphs, not used as evidence for where columns are.

1.6 Cross-Page Paragraph Merging

Requirement:

If a paragraph continues across pages, we want a single logical <para>.

Use geometry & punctuation-based heuristics:

If:

The last block on page N does not end with sentence-ending punctuation (., !, ?, :, ;),

AND the first block on page N+1:

Has similar font and style,

Has a similar indent,

Is not a heading or new list item,

Then treat the two blocks as a single paragraph.

Implementation strategy:

Record a link between blocks: e.g., block.flags.append("continues_with:<block-id>").

DocBook writer merges their text into a single <para> (no extra paragraph break).

Note: This is the only logical merging beyond within-page geometry.

1.7 Error Handling & QA

The pipeline should not abort on problematic pages if at all possible.

On ambiguous or low-confidence structures:

Output a <remark> element with diagnostic text, e.g.:

<remark role="warning">Ambiguous heading level at page 37, block b_p37_0012.</remark>


Types of issues to mark with <remark>:

Uncertain heading vs paragraph.

Table failed to parse correctly.

Column detection failure on a page.

Unsupported layout patterns.

A separate reporting tool can later parse DocBook and aggregate <remark> entries into QA reports.

2. Non-Functional Requirements

Deterministic: The same PDF must produce identical XML across runs.

Performance:

Typical size: 800–1000 pages / book.

Volume: ~500 books/month.

It’s acceptable to process books in batch mode; parallelism by book is highly recommended.

Code Quality:

Modular,

Testable,

Minimal coupling between components,

Clear public interfaces.

Logging:

Significant steps and key decisions should be logged for debugging.

3. Package Structure

Create a new package (or module) in the repo, e.g. pdf2semantic/:

pdf2semantic/
  __init__.py

  config.py
  logging_utils.py

  models/
    layout.py         # Layout-level dataclasses
    semantic.py       # Semantic structures (sections, paras, figures, tables)

  ingestion/
    media_extractor.py    # Refactor of Multipage_Image_Extractor
    text_extractor.py     # pdftohtml wrapper + XML parser
    coord_utils.py        # Coordinate system helpers

  pipeline/
    layout_builder.py     # fragments -> lines -> blocks -> columns & reading order
    flow_builder.py       # merge blocks + media/tables into linear flow
    heuristics.py         # headings, lists, captions, equations, forms, etc.
    docbook_writer.py     # create DocBook 4.3 XML from layout + flow
    orchestrator.py       # top-level orchestration

  cli/
    pdf_to_docbook.py     # CLI entry point

  tests/
    data/
      sample1.pdf
      sample1.expected.layout.json
      sample1.expected.docbook.xml
    test_layout_builder.py
    test_flow_builder.py
    test_docbook_writer.py
    test_end_to_end.py


Existing files (for reference and reuse):

Multipage_Image_Extractor.py

pdf_to_excel_columns.py

pdf_to_unified_xml.py

These should be refactored into the new modules but not deleted until the new pipeline is stable.

4. Data Models
4.1 Layout Model – models/layout.py
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Literal

Coord = float

@dataclass
class BBox:
    x0: Coord
    y0: Coord
    x1: Coord
    y1: Coord

@dataclass
class Fragment:
    id: str
    page_index: int
    bbox: BBox
    text: str
    font_family: str
    font_size: float
    font_weight: Literal["normal", "bold"]
    font_style: Literal["normal", "italic"]
    color: str
    col_id: Optional[int] = None
    script_type: Optional[Literal["superscript", "subscript"]] = None
    flags: List[str] = field(default_factory=list)

@dataclass
class Line:
    id: str
    page_index: int
    bbox: BBox
    fragment_ids: List[str]
    baseline: float
    col_id: Optional[int] = None

@dataclass
class Block:
    id: str
    page_index: int
    bbox: BBox
    line_ids: List[str]
    fragment_ids: List[str]
    col_id: Optional[int] = None
    reading_order_index: Optional[int] = None
    indent_level: int = 0
    alignment: Literal["left","right","center","justify"] = "left"
    flags: List[str] = field(default_factory=list)
    role_hint: Optional[str] = None  # e.g. "heading_level_1", "list_item", "equation_block"

@dataclass
class Media:
    id: str
    page_index: int
    bbox: BBox
    type: Literal["raster", "vector"]
    file_name: str  # relative path to extracted image
    flags: List[str] = field(default_factory=list)

@dataclass
class Table:
    id: str
    page_index: int
    bbox: BBox
    num_rows: int
    num_cols: int
    flags: List[str] = field(default_factory=list)
    caption_block_ids: List[str] = field(default_factory=list)
    # Optionally: add a cell matrix for full table structure.

@dataclass
class PageLayout:
    index: int
    width: float
    height: float
    rotation: int
    flags: List[str] = field(default_factory=list)  # e.g. ["form_page"]
    fragments: Dict[str, Fragment] = field(default_factory=dict)
    lines: Dict[str, Line] = field(default_factory=dict)
    blocks: Dict[str, Block] = field(default_factory=dict)
    media: Dict[str, Media] = field(default_factory=dict)
    tables: Dict[str, Table] = field(default_factory=dict)

@dataclass
class LayoutModel:
    pdf_path: str
    isbn: Optional[str]
    num_pages: int
    pages: Dict[int, PageLayout] = field(default_factory=dict)

4.2 Semantic Model – models/semantic.py

For future richer semantics, but optional in V1 (DocBook writer can work directly off LayoutModel + heuristics):

from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class ParaNode:
    id: str
    block_id: str
    page_index: int

@dataclass
class FigureNode:
    id: str
    media_id: str
    caption_block_id: Optional[str]
    page_index: int

@dataclass
class TableNode:
    id: str
    table_id: str
    caption_block_id: Optional[str]
    page_index: int

@dataclass
class SectionNode:
    id: str
    level: int
    title_block_id: str
    page_index: int
    child_sections: List["SectionNode"] = field(default_factory=list)
    para_ids: List[str] = field(default_factory=list)
    figure_ids: List[str] = field(default_factory=list)
    table_ids: List[str] = field(default_factory=list)

@dataclass
class SemanticDocument:
    isbn: Optional[str]
    title: Optional[str]
    sections: List[SectionNode]

5. Module Specifications
5.1 ingestion/media_extractor.py

Goal: Refactor Multipage_Image_Extractor.py into a clean, reusable module.

Signature:

def extract_media_and_tables(pdf_path: str, dpi: int = 150) -> dict[int, dict]:
    """
    Returns:
      media_info: {
        page_index: {
          "page_width": float,
          "page_height": float,
          "media": List[Media],
          "tables": List[Table],
          "exclusion_rects": List[BBox],
          "flags": List[str],  # e.g. ["form_page"]
        },
        ...
      }
    """


Responsibilities:

Open PDF with PyMuPDF.

For each page:

Detect raster images and vector diagrams.

Detect tables via Camelot or existing logic.

Prepare:

Media objects,

Table objects,

exclusion_rects:

Typically the bounding boxes of tables and key diagram areas where text should not be part of the main flow.

flags, including "form_page" when appropriate.

For "form_page":

Render the page into a PNG form_page_{index}.png.

5.2 ingestion/text_extractor.py

Goal: Wrap pdftohtml -xml, parse resulting XML, map text fragments into canonical coordinates, and apply early filtering.

Key functions:

def run_pdftohtml_xml(pdf_path: str, work_dir: str) -> str:
    """Runs pdftohtml -xml and returns the path to the generated XML file."""

def parse_pdftohtml_xml(
    pdf_path: str,
    xml_path: str,
    media_info: dict[int, dict],
    isbn: str | None = None,
) -> LayoutModel:
    """
    Parse pdftohtml XML, map <text> nodes to PyMuPDF coordinate space,
    apply exclusion zones from media_info, and run existing header/footer
    and noise filtering logic to populate LayoutModel.fragments.
    """


Details:

For each <page>:

Read width, height from XML.

Read corresponding page_width, page_height from media_info.

Compute scale_x and scale_y to map pdftohtml coordinates → PyMuPDF coordinate system.

For each <text>:

Compute BBox in media-space.

Drop if inside exclusion_rects (overlap / IoU threshold).

Apply existing logic from current pipeline:

Remove headers and footers ( repeated text at stable y across pages ),

Remove page numbers and tiny noise fragments,

Mark superscripts/subscripts if needed.

Set LayoutModel.pages[page_index].fragments.

5.3 pipeline/layout_builder.py

Goal: Build lines, blocks, and assign columns & reading order using text blocks only.

API:

def build_lines(layout: LayoutModel) -> None:
    """Group fragments into lines based on y-baselines and x proximity."""

def build_blocks(layout: LayoutModel) -> None:
    """Group lines into blocks (paragraphs, headings, list items) based on vertical gaps, indent and style."""

def detect_columns_and_reading_order(layout: LayoutModel) -> None:
    """Detect column boundaries using block x-positions; assign col_id and reading_order_index per block."""


Implementation outline:

build_lines:

Sort fragments by (y0, x0).

Cluster by baseline with a tolerance (reusing logic from pdf_to_excel_columns.py).

build_blocks:

Sort lines by y.

Merge lines into blocks when:

Vertical gap is below a threshold,

Indentation matches,

Font-family/size/weight is consistent.

detect_columns_and_reading_order:

Cluster Block.bbox.x0 positions into 1..N columns (K-means or simple gap-based clustering).

Assign col_id to each block.

Compute reading_order_index per block: sort by column then y.

5.4 pipeline/flow_builder.py

Goal: Merge blocks, figures, tables, and form pages into a linear flow per page.

Types:

from dataclasses import dataclass

@dataclass
class FlowItem:
    kind: str  # "block" | "figure" | "table" | "form"
    id: str
    page_index: int
    y0: float


Function:

def build_flow(layout: LayoutModel) -> dict[int, list[FlowItem]]:
    """
    For each page:
      - Add FlowItems for each block, media, and table.
      - If "form_page" flag is set, replace items with a single 'form' item.
      - Sort items by y0 (top to bottom) for each page.
    Return a mapping: page_index -> ordered list of FlowItems.
    """


Figures and tables will be anchored to paragraphs later in the DocBook writer, using column-aware proximity logic.

5.5 pipeline/heuristics.py

Goal: Apply deterministic heuristics to annotate blocks with role hints and handle structures like headings, lists, captions, equations, and cross-page continuation.

API:

def apply_heuristics(layout: LayoutModel) -> None:
    """
    Enrich blocks and tables in layout with:
      - role_hint: heading levels, list items, equation blocks, captions.
      - flags for cross-page paragraph continuation.
    """


Sub-tasks:

_tag_headings(page: PageLayout):

Use:

Font size > surrounding median,

Bold/italic,

Short line length,

Extra spacing above,

Set block.role_hint to heading_level_1, heading_level_2, etc.

_tag_lists(page: PageLayout):

Detect bullet or number markers at start of lines and consistent indentation.

Mark these blocks as role_hint = "list_item".

Optionally assign a list-id via flags to group items.

_tag_captions(page: PageLayout):

Identify blocks near media/table bounding boxes with patterns:

“Figure N”, “Fig. N”, “Table N”.

Mark role_hint = "caption_figure" or "caption_table".

Attach caption block IDs to the corresponding Media/Table.

_tag_equations(page: PageLayout):

Recognize math blocks (presence of =, Greek letters, superscripts/subscripts).

Mark role_hint = "equation_block".

Cross-page continuation:

Between last block of page N and first block of page N+1:

If heuristics indicate continuation, add flags like:

block.flags.append("continues_with:<block-id>").

All of this must be deterministic and rule-based.

5.6 pipeline/docbook_writer.py

Goal: Build DocBook 4.3 <book> XML from LayoutModel + flow.

API:

from .flow_builder import FlowItem

def layout_to_docbook(
    layout: LayoutModel,
    flow: dict[int, list[FlowItem]],
    output_path: str,
) -> str:
    """
    Build a DocBook 4.3 <book> element with nested sections, paras, figures, tables, equations.
    Write XML to output_path and return output_path.
    """


Behavior:

Root:

<book>
  <bookinfo>
    <isbn>...</isbn> <!-- optional -->
  </bookinfo>
  <sect1>
    <title>Document</title> <!-- or derived from first heading -->
    <!-- content -->
  </sect1>
</book>


For each FlowItem in order (by page_index, y0):

kind == "block":

If block.role_hint is a heading level:

Open or adjust <sect1>/<sect2>/<sect3>... hierarchy.

Use block text as <title>.

Else:

Emit <para> with reconstructed text and inline emphasis (based on fragment font styles).

Merge text from continuation blocks if continues_with flags exist.

kind == "figure":

Emit <figure>:

<title> from caption block if available, else fallback (“Figure”).

<mediaobject><imageobject><imagedata fileref="..."/></imageobject></mediaobject>.

kind == "table":

Emit <table>:

<title> from table caption if available, else fallback (“Table”).

<tgroup cols="..."><thead>...</thead><tbody>...</tbody></tgroup>.

kind == "form":

Emit <figure> with <title>Form page and imagedata pointing to form_page_{index}.png.

For equation blocks (role_hint == "equation_block"):

Emit:

<equation>
  <mathphrase>...original equation text...</mathphrase>
</equation>


On low-confidence elements (e.g., ambiguous heading detection):

Emit <remark role="warning">...</remark> adjacent to the relevant node.

5.7 pipeline/orchestrator.py

Goal: Provide a single high-level function to run the pipeline.

API:

def pdf_to_docbook(
    pdf_path: str,
    output_dir: str,
    isbn: str | None = None,
) -> str:
    """
    Orchestrates the full deterministic pipeline:

      1. extract_media_and_tables()
      2. run_pdftohtml_xml()
      3. parse_pdftohtml_xml() -> LayoutModel
      4. build_lines(), build_blocks(), detect_columns_and_reading_order()
      5. build_flow()
      6. apply_heuristics()
      7. layout_to_docbook()

    Returns the path to the DocBook XML file.
    """


Implementation outline:

def pdf_to_docbook(pdf_path: str, output_dir: str, isbn: str | None = None) -> str:
    os.makedirs(output_dir, exist_ok=True)

    media_info = extract_media_and_tables(pdf_path)
    xml_path = run_pdftohtml_xml(pdf_path, work_dir=output_dir)
    layout = parse_pdftohtml_xml(pdf_path, xml_path, media_info, isbn=isbn)

    build_lines(layout)
    build_blocks(layout)
    detect_columns_and_reading_order(layout)

    flow = build_flow(layout)
    apply_heuristics(layout)

    base = isbn or os.path.splitext(os.path.basename(pdf_path))[0]
    out_path = os.path.join(output_dir, f"{base}_semantic.docbook.xml")
    layout_to_docbook(layout, flow, out_path)
    return out_path

6. CLI – cli/pdf_to_docbook.py

Create a simple CLI wrapper:

import argparse
from pdf2semantic.pipeline.orchestrator import pdf_to_docbook

def main():
    parser = argparse.ArgumentParser(description="Convert PDF to DocBook 4.3 semantic XML")
    parser.add_argument("pdf_path")
    parser.add_argument("-o", "--output-dir", default="output")
    parser.add_argument("--isbn", default=None)
    args = parser.parse_args()

    result = pdf_to_docbook(args.pdf_path, args.output_dir, isbn=args.isbn)
    print(f"DocBook XML written to {result}")

if __name__ == "__main__":
    main()

7. Integration With Existing Pipeline

Existing modules:

Multipage_Image_Extractor.py

pdf_to_excel_columns.py

pdf_to_unified_xml.py

Integration plan:

Refactor & reuse:

Move core image/figure/table logic from Multipage_Image_Extractor.py into ingestion/media_extractor.py.

Move baseline grouping, hyphenation, script detection, and column detection from pdf_to_excel_columns.py into pipeline/layout_builder.py and pipeline/heuristics.py.

Deprecate old entry points:

Keep pdf_to_unified_xml.py temporarily for backward compatibility.

Gradually transition consumers to the new pdf_to_docbook entry point.

Maintain parity:

Ensure that the new semantic pipeline can handle all document types currently handled by the old pipeline, and that conversions are equal or better in quality.

8. Testing & QA

Under tests/data/, add representative PDFs:

Single-column prose book.

Two-column scientific/technical book (e.g., with lots of figures, tables, equations).

Nursing/assessment content with forms.

Table-heavy reference.

For each sample:

sampleX.expected.layout.json:

Serialized LayoutModel (or a subset) for structural validation.

sampleX.expected.docbook.xml:

Canonical expected DocBook 4.3 output.

Tests:

test_layout_builder.py:

Number of blocks, lines.

Column counts and assignments.

No fragments in main flow whose bbox is inside table/figure exclusion zones.

test_flow_builder.py:

Validate flow order.

Ensure figures/tables appear near expected anchor paragraphs.

test_docbook_writer.py:

Validate well-formed DocBook 4.3 (e.g. with xmllint/DTD).

Correct use of <table>, <figure>, <equation>, <remark>.

test_end_to_end.py:

Run pdf_to_docbook on test PDFs.

Compare normalized XML to expected.docbook.xml (whitespace-insensitive).

9. Task List for Implementation (for Cursor / coding assistants)

Create package skeleton:

Add pdf2semantic/ with the directory structure and empty files per this spec.

Implement models/layout.py and models/semantic.py:

Define dataclasses exactly as specified.

Refactor Multipage_Image_Extractor.py into ingestion/media_extractor.py:

Implement extract_media_and_tables().

Ensure it populates Media, Table, exclusion_rects, and page flags.

Implement ingestion/text_extractor.py:

run_pdftohtml_xml wrapper.

parse_pdftohtml_xml that maps to LayoutModel.fragments and applies existing filters.

Implement pipeline/layout_builder.py:

build_lines, build_blocks, detect_columns_and_reading_order.

Reuse baseline and column logic from pdf_to_excel_columns.py.

Implement pipeline/flow_builder.py:

build_flow creating ordered FlowItems per page.

Implement pipeline/heuristics.py:

Heading detection.

List detection.

Caption association.

Equation detection.

Cross-page continuation flags.

Implement pipeline/docbook_writer.py:

layout_to_docbook, creating a valid DocBook 4.3 <book> with sections, paras, figures, tables, equations, and remarks.

Implement pipeline/orchestrator.py and cli/pdf_to_docbook.py:

Provide a single entry point function and CLI as specified.

Add tests and golden data:

Create test PDFs and expected outputs.

Implement unit tests and end-to-end tests.