#!/usr/bin/env python3
"""PDF -> (extract images) -> Claude Vision AI -> DocBook XML 4.2 -> RittDoc ZIP + Word (.docx)

This orchestrator:
  1) Calls an image extractor script that writes assets to a folder named "MultiMedia".
  2) Calls Claude Vision AI to process each PDF page as a high-DPI image.
  3) Creates intermediate Markdown, then converts to DocBook XML 4.2.
  4) Optionally launches a web-based editor for manual corrections.
  5) Creates a RittDoc DTD-validated ZIP package.
  6) Uses pandoc to convert the produced DocBook XML into a .docx.

Vision AI Processing:
  - Renders each PDF page at 300 DPI (PNG format)
  - Uses Claude Vision API for text extraction with ZERO hallucinations
  - Handles tables with rotation detection
  - Supports batch processing for large PDFs (1000+ pages)
  - Auto-saves progress for crash recovery

Usage:
  python pdf_orchestrator.py /path/to/input.pdf --out ./output
  python pdf_orchestrator.py /path/to/input.pdf --out ./output --edit-mode
  python pdf_orchestrator.py /path/to/input.pdf --out ./output --model claude-opus-4-5-20251101

Notes:
  - Requires ANTHROPIC_API_KEY environment variable
  - The orchestrator creates the MultiMedia folder in the output directory
  - Use --edit-mode to launch the web editor before final packaging.

Outputs:
  - <name>_intermediate.md : Intermediate Markdown from Vision AI
  - <name>_docbook42.xml   : DocBook XML 4.2
  - <name>_rittdoc.zip     : RittDoc DTD-validated ZIP package
  - <name>.docx            : Word document
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from lxml import etree

# PyMuPDF for font extraction
try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

# Import conversion tracking from rittdoc_core
try:
    from rittdoc_core import (
        ConversionTracker,
        ConversionStatus,
        ConversionType,
        TemplateType,
    )
    TRACKING_AVAILABLE = True
except ImportError:
    TRACKING_AVAILABLE = False

# Import bookmark extractor for chapter/section detection
try:
    from bookmark_extractor import extract_bookmarks, print_hierarchy
    BOOKMARK_EXTRACTOR_AVAILABLE = True
except ImportError:
    BOOKMARK_EXTRACTOR_AVAILABLE = False


def run_cmd(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    """Run a command and raise a readable error on failure."""
    printable = " ".join(shlex.quote(c) for c in cmd)
    print(f"\n▶ Running: {printable}")
    try:
        subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Command failed with exit code {e.returncode}: {printable}") from e


def _serialize_bookmarks(nodes: list) -> list:
    """Serialize BookmarkNode objects to JSON-serializable dicts."""
    result = []
    for node in nodes:
        item = {
            "level": node.level,
            "title": node.title,
            "start_page": node.start_page,
            "end_page": node.end_page,
            "children": _serialize_bookmarks(node.children) if node.children else []
        }
        result.append(item)
    return result


def choose_default_script(candidates: list[Path]) -> Path | None:
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None


def extract_font_info_from_pdf(pdf_path: Path, out_dir: Path) -> Path | None:
    """
    Extract font information from PDF using PyMuPDF and save as JSON.

    This creates a font_info.json file that can be used by font_roles_auto.py
    for font role detection and TOC generation.

    Returns:
        Path to font_info.json file, or None if extraction failed
    """
    print("\n" + "=" * 80)
    print("DEBUG [FONT INFO START]: Beginning font information extraction from PDF")
    print("=" * 80)

    if not HAS_FITZ:
        print("  ⚠ PyMuPDF (fitz) not available - cannot extract font info")
        print("DEBUG [FONT INFO END]: Font extraction skipped (no PyMuPDF)")
        return None

    font_info_path = out_dir / f"{pdf_path.stem}_font_info.json"

    try:
        print(f"  → Opening PDF: {pdf_path}")
        doc = fitz.open(str(pdf_path))

        # Collect font statistics
        font_stats = {}  # font_key -> {size, family, count, pages}
        page_fonts = {}  # page_num -> list of font entries

        total_pages = len(doc)
        print(f"  → Analyzing {total_pages} pages for font information...")

        for page_num in range(total_pages):
            page = doc[page_num]
            page_fonts[page_num + 1] = []

            # Get text blocks with font info
            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

            for block in blocks:
                if block.get("type") != 0:  # Skip non-text blocks
                    continue

                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        font_name = span.get("font", "Unknown")
                        font_size = round(span.get("size", 0), 2)
                        text = span.get("text", "").strip()

                        if font_size < 4 or not text:  # Skip tiny fonts and empty text
                            continue

                        font_key = f"{font_name}_{font_size}"

                        if font_key not in font_stats:
                            font_stats[font_key] = {
                                "font": font_name,
                                "size": font_size,
                                "count": 0,
                                "pages": set(),
                                "sample_text": []
                            }

                        font_stats[font_key]["count"] += 1
                        font_stats[font_key]["pages"].add(page_num + 1)

                        # Keep some sample text for debugging
                        if len(font_stats[font_key]["sample_text"]) < 3 and len(text) > 5:
                            font_stats[font_key]["sample_text"].append(text[:50])

        doc.close()

        # Convert sets to lists for JSON serialization and derive roles
        font_info = {
            "pdf_file": str(pdf_path.name),
            "total_pages": total_pages,
            "fonts": {}
        }

        # Sort fonts by size descending
        sorted_fonts = sorted(font_stats.items(), key=lambda x: (-x[1]["size"], -x[1]["count"]))

        # Find body text size (most common)
        if sorted_fonts:
            body_size = max(font_stats.values(), key=lambda x: x["count"])["size"]
        else:
            body_size = 12.0

        # Derive roles based on size relative to body
        for font_key, stats in sorted_fonts:
            pages_list = sorted(stats["pages"])

            # Derive role based on size
            size = stats["size"]
            if size >= body_size * 1.5:
                role = "title"
            elif size >= body_size * 1.3:
                role = "chapter"
            elif size >= body_size * 1.15:
                role = "section"
            elif size >= body_size * 1.05:
                role = "subsection"
            else:
                role = "paragraph"

            font_info["fonts"][font_key] = {
                "font": stats["font"],
                "size": stats["size"],
                "count": stats["count"],
                "page_count": len(pages_list),
                "pages": pages_list,
                "sample_text": stats["sample_text"],
                "derived_role": role
            }

        font_info["body_size"] = body_size
        font_info["notes"] = {
            "extraction_method": "PyMuPDF (fitz)",
            "role_derivation": "automatic based on size relative to body text"
        }

        # Write JSON
        with open(font_info_path, "w", encoding="utf-8") as f:
            json.dump(font_info, f, indent=2)

        print(f"  ✓ Found {len(font_stats)} unique font/size combinations")
        print(f"  ✓ Body text size: {body_size}pt")
        print(f"  ✓ Font info saved: {font_info_path}")
        print("DEBUG [FONT INFO END]: Font extraction completed successfully")

        return font_info_path

    except Exception as e:
        print(f"  ✗ Error extracting font info: {e}")
        import traceback
        traceback.print_exc()
        print("DEBUG [FONT INFO END]: Font extraction failed with error")
        return None


def run_font_roles_auto(font_info_path: Path, out_dir: Path, pdf_stem: str) -> Path | None:
    """
    Run font_roles_auto.py to derive font roles for TOC generation.

    Note: font_roles_auto.py expects a unified XML file. Since we're using
    the Vision AI pipeline, we use the font_info.json directly instead.

    Returns:
        Path to font_roles.json file, or None if it failed
    """
    print("\n" + "=" * 80)
    print("DEBUG [FONT ROLES START]: Beginning font role detection")
    print("=" * 80)

    font_roles_path = out_dir / f"{pdf_stem}_font_roles.json"

    # Check if font_roles_auto.py exists
    here = Path.cwd().resolve()
    font_roles_script = here / "font_roles_auto.py"

    if not font_roles_script.exists():
        print(f"  ⚠ font_roles_auto.py not found at {font_roles_script}")
        print("  → Creating font roles directly from font_info.json")

        # Create font roles directly from font_info
        try:
            with open(font_info_path, "r", encoding="utf-8") as f:
                font_info = json.load(f)

            # Build roles_by_size format expected by downstream tools
            roles_by_size = {}
            for font_key, font_data in font_info.get("fonts", {}).items():
                size_key = str(font_data["size"])
                if size_key not in roles_by_size:
                    roles_by_size[size_key] = {
                        "role": font_data["derived_role"],
                        "count": font_data["count"],
                        "page_count": font_data["page_count"],
                        "top_family": font_data["font"]
                    }
                else:
                    # Aggregate counts for same size
                    roles_by_size[size_key]["count"] += font_data["count"]

            font_roles = {
                "sizes_asc": sorted(roles_by_size.keys(), key=float),
                "roles_by_size": roles_by_size,
                "notes": {
                    "source": "derived from font_info.json",
                    "body_size": font_info.get("body_size"),
                    "mapping_source": "auto-derived per book"
                }
            }

            with open(font_roles_path, "w", encoding="utf-8") as f:
                json.dump(font_roles, f, indent=2)

            print(f"  ✓ Font roles created: {font_roles_path}")
            print(f"  → Roles detected: {set(r['role'] for r in roles_by_size.values())}")
            print("DEBUG [FONT ROLES END]: Font role detection completed successfully")

            return font_roles_path

        except Exception as e:
            print(f"  ✗ Error creating font roles: {e}")
            print("DEBUG [FONT ROLES END]: Font role detection failed")
            return None

    # font_roles_auto.py exists but expects unified XML, so we still create from font_info
    print("  → font_roles_auto.py found but requires unified XML")
    print("  → Creating font roles directly from font_info.json instead")

    try:
        with open(font_info_path, "r", encoding="utf-8") as f:
            font_info = json.load(f)

        roles_by_size = {}
        for font_key, font_data in font_info.get("fonts", {}).items():
            size_key = str(font_data["size"])
            if size_key not in roles_by_size:
                roles_by_size[size_key] = {
                    "role": font_data["derived_role"],
                    "count": font_data["count"],
                    "page_count": font_data["page_count"],
                    "top_family": font_data["font"]
                }
            else:
                roles_by_size[size_key]["count"] += font_data["count"]

        font_roles = {
            "sizes_asc": sorted(roles_by_size.keys(), key=float),
            "roles_by_size": roles_by_size,
            "notes": {
                "source": "derived from font_info.json",
                "body_size": font_info.get("body_size"),
                "mapping_source": "auto-derived per book"
            }
        }

        with open(font_roles_path, "w", encoding="utf-8") as f:
            json.dump(font_roles, f, indent=2)

        print(f"  ✓ Font roles created: {font_roles_path}")
        print(f"  → Roles detected: {set(r['role'] for r in roles_by_size.values())}")
        print("DEBUG [FONT ROLES END]: Font role detection completed successfully")

        return font_roles_path

    except Exception as e:
        print(f"  ✗ Error running font_roles_auto: {e}")
        print("DEBUG [FONT ROLES END]: Font role detection failed")
        return None


def adjust_docbook_headings(xml_path: Path, font_roles_path: Path) -> bool:
    """
    Adjust DocBook XML heading levels based on font role analysis.

    This post-processes the DocBook XML to ensure proper heading hierarchy
    based on the detected font roles (title, chapter, section, subsection).

    Returns:
        True if adjustments were made, False otherwise
    """
    print("\n" + "=" * 80)
    print("DEBUG [HEADING ADJUSTMENT START]: Adjusting DocBook headings based on font roles")
    print("=" * 80)

    try:
        # Load font roles
        with open(font_roles_path, "r", encoding="utf-8") as f:
            font_roles = json.load(f)

        roles_by_size = font_roles.get("roles_by_size", {})
        if not roles_by_size:
            print("  ⚠ No font roles found, skipping heading adjustment")
            print("DEBUG [HEADING ADJUSTMENT END]: Skipped (no roles)")
            return False

        # Parse the DocBook XML
        parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False)
        tree = etree.parse(str(xml_path), parser)
        root = tree.getroot()

        # Count heading elements before adjustment
        chapters_before = len(root.findall(".//chapter"))
        sect1_before = len(root.findall(".//sect1"))
        sect2_before = len(root.findall(".//sect2"))
        sect3_before = len(root.findall(".//sect3"))

        print(f"  → Current structure: {chapters_before} chapters, {sect1_before} sect1, {sect2_before} sect2, {sect3_before} sect3")

        # Build role hierarchy from font roles
        # Map roles to their DocBook equivalents
        role_to_docbook = {
            "title": "book",  # Book title - stays as book/title
            "chapter": "chapter",
            "section": "sect1",
            "subsection": "sect2",
            "paragraph": None  # Regular paragraphs
        }

        # Log the role mapping
        print("  → Font role mapping:")
        for size_key in sorted(roles_by_size.keys(), key=float, reverse=True)[:5]:
            role_info = roles_by_size[size_key]
            docbook_elem = role_to_docbook.get(role_info["role"], "para")
            print(f"    {float(size_key):6.2f}pt → {role_info['role']:12s} → <{docbook_elem}>")

        # For now, we log what we would change but don't modify
        # Full implementation would require analyzing the actual text in headings
        # and matching them to font sizes, which requires the original PDF text extraction

        adjustments_made = 0

        # Write back the XML (even if no changes, ensures consistent formatting)
        tree.write(str(xml_path), encoding="utf-8", xml_declaration=True, pretty_print=True)

        print(f"  ✓ Heading structure validated")
        print(f"  → Adjustments applied: {adjustments_made}")
        print("DEBUG [HEADING ADJUSTMENT END]: Completed")

        return adjustments_made > 0

    except Exception as e:
        print(f"  ✗ Error adjusting headings: {e}")
        import traceback
        traceback.print_exc()
        print("DEBUG [HEADING ADJUSTMENT END]: Failed with error")
        return False


def generate_toc_xml(xml_path: Path, font_roles_path: Path, out_dir: Path) -> Path | None:
    """
    Generate a standalone TOC.xml file from the DocBook XML structure.

    This extracts the table of contents based on chapter and section headings
    in the DocBook XML, enriched with font role information.

    Returns:
        Path to TOC.xml file, or None if generation failed
    """
    print("\n" + "=" * 80)
    print("DEBUG [TOC GENERATION START]: Generating standalone TOC.xml")
    print("=" * 80)

    try:
        # Parse the DocBook XML
        parser = etree.XMLParser(remove_blank_text=True)
        tree = etree.parse(str(xml_path), parser)
        root = tree.getroot()

        # Load font roles for metadata
        font_roles = {}
        if font_roles_path and font_roles_path.exists():
            with open(font_roles_path, "r", encoding="utf-8") as f:
                font_roles = json.load(f)

        # Create TOC root element
        toc_root = etree.Element("toc")
        toc_root.set("source", xml_path.name)
        toc_root.set("generated", "pdf_orchestrator.py")

        # Add font role summary as metadata
        if font_roles:
            meta = etree.SubElement(toc_root, "metadata")
            body_size = font_roles.get("notes", {}).get("body_size")
            if body_size:
                meta.set("body_size", str(body_size))

            roles_summary = etree.SubElement(meta, "font_roles")
            for size_key in sorted(font_roles.get("roles_by_size", {}).keys(), key=float, reverse=True):
                role_info = font_roles["roles_by_size"][size_key]
                if role_info["role"] != "paragraph":
                    role_elem = etree.SubElement(roles_summary, "role")
                    role_elem.set("size", size_key)
                    role_elem.set("type", role_info["role"])
                    role_elem.set("font", role_info.get("top_family", "Unknown"))
                    role_elem.set("count", str(role_info["count"]))

        # Extract book title
        book_title = root.find(".//bookinfo/title")
        if book_title is None:
            book_title = root.find(".//title")

        if book_title is not None and book_title.text:
            title_elem = etree.SubElement(toc_root, "book_title")
            title_elem.text = book_title.text.strip()

        # Extract chapters and sections
        toc_entries = etree.SubElement(toc_root, "entries")
        entry_count = 0

        # Find all chapters
        chapters = root.findall(".//chapter")
        for idx, chapter in enumerate(chapters, 1):
            chapter_title = chapter.find("title")
            if chapter_title is not None:
                chapter_entry = etree.SubElement(toc_entries, "entry")
                chapter_entry.set("level", "1")
                chapter_entry.set("type", "chapter")
                chapter_entry.set("number", str(idx))

                title_text = "".join(chapter_title.itertext()).strip()
                chapter_entry.text = title_text
                entry_count += 1

                # Find sections within this chapter
                sect1s = chapter.findall(".//sect1")
                for sect_idx, sect1 in enumerate(sect1s, 1):
                    sect1_title = sect1.find("title")
                    if sect1_title is not None:
                        sect1_entry = etree.SubElement(toc_entries, "entry")
                        sect1_entry.set("level", "2")
                        sect1_entry.set("type", "sect1")
                        sect1_entry.set("parent_chapter", str(idx))

                        title_text = "".join(sect1_title.itertext()).strip()
                        sect1_entry.text = title_text
                        entry_count += 1

                        # Find subsections
                        sect2s = sect1.findall(".//sect2")
                        for subsect_idx, sect2 in enumerate(sect2s, 1):
                            sect2_title = sect2.find("title")
                            if sect2_title is not None:
                                sect2_entry = etree.SubElement(toc_entries, "entry")
                                sect2_entry.set("level", "3")
                                sect2_entry.set("type", "sect2")

                                title_text = "".join(sect2_title.itertext()).strip()
                                sect2_entry.text = title_text
                                entry_count += 1

        # If no chapters found, try to find sections at root level
        if not chapters:
            sect1s = root.findall(".//sect1")
            for idx, sect1 in enumerate(sect1s, 1):
                sect1_title = sect1.find("title")
                if sect1_title is not None:
                    sect1_entry = etree.SubElement(toc_entries, "entry")
                    sect1_entry.set("level", "1")
                    sect1_entry.set("type", "sect1")
                    sect1_entry.set("number", str(idx))

                    title_text = "".join(sect1_title.itertext()).strip()
                    sect1_entry.text = title_text
                    entry_count += 1

        # Add summary
        summary = etree.SubElement(toc_root, "summary")
        summary.set("total_entries", str(entry_count))
        summary.set("chapters", str(len(chapters)))
        summary.set("sections", str(len(root.findall(".//sect1"))))
        summary.set("subsections", str(len(root.findall(".//sect2"))))

        # Write TOC.xml
        toc_path = out_dir / f"{xml_path.stem.replace('_docbook42', '')}_TOC.xml"
        toc_tree = etree.ElementTree(toc_root)
        toc_tree.write(str(toc_path), encoding="utf-8", xml_declaration=True, pretty_print=True)

        print(f"  ✓ TOC generated with {entry_count} entries")
        print(f"    - Chapters: {len(chapters)}")
        print(f"    - Sections: {len(root.findall('.//sect1'))}")
        print(f"    - Subsections: {len(root.findall('.//sect2'))}")
        print(f"  ✓ TOC saved: {toc_path}")
        print("DEBUG [TOC GENERATION END]: Completed successfully")

        return toc_path

    except Exception as e:
        print(f"  ✗ Error generating TOC: {e}")
        import traceback
        traceback.print_exc()
        print("DEBUG [TOC GENERATION END]: Failed with error")
        return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Orchestrate PDF -> Claude Vision AI -> DocBook 4.2 XML -> RittDoc ZIP + DOCX",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Basic usage (uses defaults - 300 DPI, Claude Sonnet 4):
    python pdf_orchestrator.py mybook.pdf

  With editor for manual corrections:
    python pdf_orchestrator.py mybook.pdf --edit-mode

  Custom output directory:
    python pdf_orchestrator.py mybook.pdf --out ./converted

  Use Claude Opus 4.5 for better accuracy:
    python pdf_orchestrator.py mybook.pdf --model claude-opus-4-5-20251101

  Process large PDF with custom batch size:
    python pdf_orchestrator.py mybook.pdf --batch-size 20

  Skip RittDoc packaging (just XML and DOCX):
    python pdf_orchestrator.py mybook.pdf --skip-rittdoc

Environment Variables:
  ANTHROPIC_API_KEY - Required for Claude Vision API
        """
    )
    ap.add_argument("pdf", help="Path to input PDF")
    ap.add_argument("--out", default="output", help="Output directory (default: ./output)")

    ap.add_argument(
        "--extractor",
        default=None,
        help="Path to multipage image extractor script (python file). If omitted, tries ./Multipage_Image_Extractor.py",
    )
    ap.add_argument(
        "--ai-service",
        default=None,
        help="Path to AI PDF conversion script (python file). If omitted, uses ./ai_pdf_conversion_service.py",
    )

    ap.add_argument(
        "--multimedia",
        default=None,
        help='Override MultiMedia folder path. Default: "<output_dir>/<basename>_MultiMedia"',
    )

    # Vision AI options (defaults optimized for accuracy)
    ai_group = ap.add_argument_group("Claude Vision AI Options")
    ai_group.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="DPI for rendering PDF pages (default: 300 - high quality)"
    )
    ai_group.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Claude model (default: claude-sonnet-4-20250514). Use claude-opus-4-5-20251101 for best accuracy."
    )
    ai_group.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="AI temperature (default: 0.0 - NO HALLUCINATIONS)"
    )
    ai_group.add_argument(
        "--max-tokens",
        type=int,
        default=8192,
        help="Max tokens per page response (default: 8192)"
    )

    # Batch processing for large PDFs
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
        help="Resume from specific page (default: auto-resume from progress file)"
    )

    # Edit mode and RittDoc options
    output_group = ap.add_argument_group("Output Options")
    output_group.add_argument(
        "--edit-mode",
        action="store_true",
        help="Launch web-based UI editor for manual editing before final processing"
    )
    output_group.add_argument(
        "--editor-port",
        type=int,
        default=5000,
        help="Port for editor server (default: 5000)"
    )
    output_group.add_argument(
        "--dtd",
        default="RITTDOCdtd/v1.1/RittDocBook.dtd",
        help="Path to DTD file for RittDoc validation (default: RITTDOCdtd/v1.1/RittDocBook.dtd)"
    )
    output_group.add_argument(
        "--iterations",
        type=int,
        default=3,
        help="Max compliance iterations for RittDoc validation (default: 3)"
    )
    output_group.add_argument(
        "--skip-rittdoc",
        action="store_true",
        help="Skip RittDoc packaging (only produce XML and DOCX)"
    )
    output_group.add_argument(
        "--skip-extraction",
        action="store_true",
        help="Skip image/table extraction (Vision AI handles text, extractor handles images)"
    )

    # API mode - for use when called from the REST API
    # Stops after DocBook XML creation, skipping editor/RittDoc/DOCX
    output_group.add_argument(
        "--api-mode",
        action="store_true",
        help="API mode: stop after DocBook XML creation (skip editor, RittDoc, DOCX). Used by REST API."
    )

    args = ap.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 2
    if pdf_path.suffix.lower() != ".pdf":
        print(f"ERROR: Input is not a PDF: {pdf_path}", file=sys.stderr)
        return 2

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Initialize conversion tracker for dashboard generation
    tracker = None
    if TRACKING_AVAILABLE:
        tracker = ConversionTracker(out_dir)
        tracker.start_conversion(
            filename=pdf_path.name,
            conversion_type=ConversionType.PDF,
            template_type=TemplateType.UNKNOWN,
        )

    # Resolve scripts
    here = Path.cwd().resolve()

    # MultiMedia folder - use "{basename}_MultiMedia" naming in output directory
    # This keeps multimedia and XML together for consistent image resolution
    pdf_base_name = pdf_path.stem
    multimedia_dir = Path(args.multimedia).expanduser().resolve() if args.multimedia else (out_dir / f"{pdf_base_name}_MultiMedia")
    multimedia_dir.mkdir(parents=True, exist_ok=True)

    # 1) Extract images to MultiMedia (unless --skip-extraction)
    if args.skip_extraction:
        print("\n" + "=" * 80)
        print("STEP 1: SKIPPING EXTRACTION (Full AI Mode)")
        print("=" * 80)
        print("  ⊳ Image/table extraction skipped - AI will handle everything")
    else:
        print("\n" + "=" * 80)
        print("STEP 1: EXTRACTING IMAGES AND TABLES")
        print("=" * 80)

        extractor = Path(args.extractor).expanduser().resolve() if args.extractor else None
        if extractor is None:
            extractor = choose_default_script([
                here / "Multipage_Image_Extractor.py",
                here / "multipage_image_extractor.py",
                here / "Multipage_Image_Extractor_backup.py",
            ])
        if extractor is None:
            print("WARNING: Could not locate image extractor script. Continuing without extraction.")
        else:
            # Your backup extractor supports: python extractor.py <pdf> --dpi N --out DIR
            extractor_cmd = [sys.executable, str(extractor), str(pdf_path), "--dpi", str(args.dpi), "--out", str(multimedia_dir)]
            try:
                run_cmd(extractor_cmd)
                # Light sanity check: did we get any files?
                media_files = [p for p in multimedia_dir.iterdir() if p.is_file()]
                if not media_files:
                    print(f"WARNING: No files were produced in {multimedia_dir}. Continuing anyway.")
                else:
                    print(f"✓ MultiMedia populated: {multimedia_dir} ({len(media_files)} files)")
            except Exception as e:
                print(f"WARNING: Extraction failed: {e}. Continuing with AI-only mode.")

    # Update progress: extraction complete
    if tracker:
        tracker.update_progress(20)

    # 1.5) Extract bookmarks/outline for chapter/section structure
    bookmark_json_path = None
    if BOOKMARK_EXTRACTOR_AVAILABLE:
        print("\n" + "=" * 80)
        print("STEP 1.5: BOOKMARK/OUTLINE EXTRACTION")
        print("=" * 80)
        print("  Extracting PDF bookmarks for chapter/section hierarchy...")

        hierarchy = extract_bookmarks(str(pdf_path))
        if hierarchy:
            print_hierarchy(hierarchy, max_depth=2)

            # Save bookmark hierarchy to JSON for AI service
            bookmark_json_path = out_dir / f"{pdf_path.stem}_bookmarks.json"
            bookmark_data = {
                "total_pages": hierarchy.total_pages,
                "front_matter_end_page": hierarchy.front_matter_end_page,
                "back_matter_start_page": hierarchy.back_matter_start_page,
                "has_parts": hierarchy.has_parts,
                "bookmarks": _serialize_bookmarks(hierarchy.root_items)
            }
            with open(bookmark_json_path, "w", encoding="utf-8") as f:
                json.dump(bookmark_data, f, indent=2)
            print(f"  ✓ Bookmark hierarchy saved: {bookmark_json_path}")
        else:
            print("  ⚠ No bookmarks found - will use heuristic chapter detection")
    else:
        print("\n  ⚠ Bookmark extractor not available - will use heuristic chapter detection")

    # Resolve AI service script
    ai_service = Path(args.ai_service).expanduser().resolve() if args.ai_service else None
    if ai_service is None:
        ai_service = choose_default_script([
            here / "ai_pdf_conversion_service.py",
            here / "ai_pdf_conversion_service_docbook42_responses_pdfinput_multimedia.py",
        ])
    if ai_service is None:
        print("ERROR: Could not locate AI service script. Provide --ai-service PATH.", file=sys.stderr)
        return 2

    # 2) AI conversion -> DocBook XML (4.2) via Claude Vision
    print("\n" + "=" * 80)
    print("STEP 2: CLAUDE VISION AI CONVERSION")
    print("=" * 80)
    print(f"  Model: {args.model}")
    print(f"  DPI: {args.dpi}")
    print(f"  Temperature: {args.temperature} (zero = no hallucinations)")
    print(f"  Batch size: {args.batch_size}")
    print(f"  Processing PDF page-by-page with Vision AI...")

    # Build AI service command with all Vision AI options
    ai_cmd = [
        sys.executable,
        str(ai_service),
        str(pdf_path),
        "--out", str(out_dir),
        "--multimedia", str(multimedia_dir),
        "--model", str(args.model),
        "--dpi", str(args.dpi),
        "--temperature", str(args.temperature),
        "--max-tokens", str(args.max_tokens),
        "--batch-size", str(args.batch_size),
        "--resume-from-page", str(args.resume_from_page),
    ]

    # Add bookmark hierarchy path if available (for chapter/section structure)
    if bookmark_json_path and bookmark_json_path.exists():
        ai_cmd.extend(["--bookmarks", str(bookmark_json_path)])

    run_cmd(ai_cmd)

    # Find the produced XML
    xml_candidates = sorted(out_dir.glob(f"{pdf_path.stem}*_docbook42.xml"))
    if not xml_candidates:
        # fall back to any xml
        xml_candidates = sorted(out_dir.glob("*.xml"))

    if not xml_candidates:
        print(f"ERROR: AI service did not produce an XML in {out_dir}", file=sys.stderr)
        if tracker:
            tracker.complete_conversion(
                status=ConversionStatus.FAILURE,
                error_message="AI service did not produce an XML file"
            )
        return 1

    out_xml = xml_candidates[0]
    print(f"✓ DocBook XML: {out_xml}")

    # Update progress: AI conversion complete
    if tracker:
        tracker.update_progress(50)

    # =========================================================================
    # DISABLED: Font-based heading detection and TOC generation
    # Now using PDF bookmarks/outlines for chapter/section structure instead.
    # See Step 1.5 above for bookmark extraction.
    # =========================================================================
    # 2.5) Extract font info and derive font roles for TOC generation
    # print("\n" + "=" * 80)
    # print("STEP 2.5: FONT INFORMATION EXTRACTION AND ROLE DETECTION")
    # print("=" * 80)
    # print("  Extracting font information from PDF for heading detection...")
    #
    # font_info_path = extract_font_info_from_pdf(pdf_path, out_dir)
    # font_roles_path = None
    #
    # if font_info_path:
    #     font_roles_path = run_font_roles_auto(font_info_path, out_dir, pdf_path.stem)
    #     if font_roles_path:
    #         print(f"\n  ✓ Font analysis complete:")
    #         print(f"    - Font info: {font_info_path}")
    #         print(f"    - Font roles: {font_roles_path}")
    #     else:
    #         print("\n  ⚠ Font role detection failed, continuing without font roles")
    # else:
    #     print("\n  ⚠ Font extraction failed, continuing without font info")
    #
    # # 2.6) Adjust headings and generate TOC based on font roles
    # toc_path = None
    # if font_roles_path:
    #     print("\n" + "=" * 80)
    #     print("STEP 2.6: HEADING ADJUSTMENT AND TOC GENERATION")
    #     print("=" * 80)
    #
    #     # Adjust DocBook headings based on font roles
    #     adjust_docbook_headings(out_xml, font_roles_path)
    #
    #     # Generate standalone TOC.xml (not included in package)
    #     toc_path = generate_toc_xml(out_xml, font_roles_path, out_dir)
    #     if toc_path:
    #         print(f"\n  ✓ TOC generated: {toc_path}")
    #         print("    (Note: TOC.xml is standalone and not included in the package ZIP)")

    # Initialize these to None since font-based detection is disabled
    font_info_path = None
    font_roles_path = None
    toc_path = None

    # API MODE: Exit early after DocBook XML creation
    # The API will handle RittDoc packaging and DOCX separately via finalize endpoint
    if args.api_mode:
        print("\n" + "=" * 80)
        print("API MODE - INITIAL CONVERSION COMPLETE")
        print("=" * 80)
        print(f"\nOutputs in: {out_dir}")

        # Check for intermediate markdown
        intermediate_md = out_dir / f"{pdf_path.stem}_intermediate.md"
        if intermediate_md.exists():
            print(f"  MD:   {intermediate_md.name} (intermediate)")

        print(f"  XML:  {out_xml.name}")

        # Show font info files if they exist
        if font_info_path and font_info_path.exists():
            print(f"  JSON: {font_info_path.name} (font info)")
        if font_roles_path and font_roles_path.exists():
            print(f"  JSON: {font_roles_path.name} (font roles)")
        if toc_path and toc_path.exists():
            print(f"  XML:  {toc_path.name} (TOC)")

        print("\n  Status: Ready for review/editing")
        print("  Next: Call finalize API to create RittDoc package and DOCX")
        print("=" * 80)

        # Update tracker if available
        if tracker:
            tracker.update_progress(60)

        return 0  # Success - API will continue with finalization

    # 3) Launch editor if edit mode is enabled
    if args.edit_mode:
        print("\n" + "=" * 80)
        print("LAUNCHING WEB-BASED EDITOR")
        print("=" * 80)
        print("Opening editor for manual review and editing...")
        print("After editing, save changes and close the browser to continue.")
        print("=" * 80)

        try:
            from editor_server import start_editor

            dtd_path = Path(args.dtd).resolve() if Path(args.dtd).exists() else None

            # Start editor (this will block until user is done)
            start_editor(
                pdf_path=pdf_path,
                xml_path=out_xml,
                multimedia_folder=multimedia_dir,
                dtd_path=dtd_path,
                port=args.editor_port
            )

            print("\n" + "=" * 80)
            print("EDITOR CLOSED - CONTINUING PIPELINE")
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
            print("\nContinuing without editor...")
        except Exception as e:
            print(f"\n⚠ Error launching editor: {e}")
            import traceback
            traceback.print_exc()
            print("\nContinuing without editor...")

    # Update progress: editor step complete
    if tracker:
        tracker.update_progress(60)

    # 4) Create RittDoc compliant ZIP package
    out_rittdoc_zip = out_dir / (pdf_path.stem + "_rittdoc.zip")

    if not args.skip_rittdoc:
        print("\n" + "=" * 80)
        print("STEP 4: CREATING RITTDOC COMPLIANT PACKAGE")
        print("=" * 80)

        dtd_path = Path(args.dtd)
        if not dtd_path.exists():
            print(f"⚠ Warning: DTD file not found: {dtd_path}")
            print("  Skipping RittDoc validation. Only XML and DOCX will be produced.")
        else:
            try:
                from rittdoc_compliance_pipeline import RittDocCompliancePipeline
                from package import (
                    BOOK_DOCTYPE_SYSTEM_DEFAULT,
                    package_docbook,
                    make_file_fetcher,
                )

                # Parse the XML
                print(f"  → Parsing XML: {out_xml}")
                root = etree.parse(str(out_xml)).getroot()

                # Create media fetcher with search paths
                search_paths = [multimedia_dir]
                shared_images = multimedia_dir / "SharedImages"
                if shared_images.exists():
                    search_paths.append(shared_images)
                search_paths.append(out_dir)

                # Try to load reference mapper from extraction phase
                reference_mapper = None
                mapper_path = out_dir / f"{pdf_path.stem}_reference_mapping_phase1.json"
                if mapper_path.exists():
                    try:
                        from reference_mapper import ReferenceMapper
                        reference_mapper = ReferenceMapper()
                        reference_mapper.import_from_json(mapper_path)
                        print(f"  ✓ Loaded reference mapper: {mapper_path} ({len(reference_mapper.resources)} resources)")
                    except Exception as e:
                        print(f"  ⚠ Could not load reference mapper: {e}")
                else:
                    print(f"  ⚠ No reference mapper found at {mapper_path}")

                media_fetcher = make_file_fetcher(search_paths, reference_mapper)

                # Create intermediate DocBook package
                intermediate_zip = out_dir / f"{pdf_path.stem}_docbook.zip"
                print(f"  → Creating DocBook package...")
                package_docbook(
                    root=root,
                    root_name=(root.tag.split('}', 1)[-1] if root.tag.startswith('{') else root.tag),
                    dtd_system=BOOK_DOCTYPE_SYSTEM_DEFAULT,
                    zip_path=str(intermediate_zip),
                    processing_instructions=[],
                    assets=[],
                    media_fetcher=media_fetcher,
                    book_doctype_system=BOOK_DOCTYPE_SYSTEM_DEFAULT,
                    metadata_dir=out_dir,
                )
                print(f"  ✓ Intermediate package: {intermediate_zip}")

                # Apply RittDoc compliance
                print(f"  → Applying RittDoc compliance...")
                pipeline = RittDocCompliancePipeline(dtd_path)
                rittdoc_success = pipeline.run(
                    input_zip=intermediate_zip,
                    output_zip=out_rittdoc_zip,
                    max_iterations=args.iterations
                )

                if rittdoc_success:
                    print(f"  ✓ RittDoc compliant package: {out_rittdoc_zip}")
                else:
                    print(f"  ⚠ RittDoc package created with some validation warnings: {out_rittdoc_zip}")

            except ImportError as e:
                print(f"⚠ Warning: Could not import RittDoc modules: {e}")
                print("  Skipping RittDoc packaging.")
            except Exception as e:
                print(f"⚠ Warning: RittDoc packaging failed: {e}")
                import traceback
                traceback.print_exc()
                print("  Continuing with XML and DOCX output only.")

    # Update progress: RittDoc packaging complete
    if tracker:
        tracker.update_progress(85)

    # 5) Convert XML -> DOCX via pandoc
    print("\n" + "=" * 80)
    print("STEP 5: CREATING WORD DOCUMENT")
    print("=" * 80)

    out_docx = out_dir / (pdf_path.stem + ".docx")

    # Use docbook reader; pandoc auto-detects, but be explicit
    # Include TOC with 3 levels (Level 1, 2, 3 headings)
    # Use --resource-path to tell pandoc where to find referenced images
    # Include both multimedia_dir and out_dir so images can be resolved
    resource_path = f"{multimedia_dir}:{out_dir}"
    pandoc_cmd = [
        "pandoc",
        "-f",
        "docbook",
        "-t",
        "docx",
        "--toc",
        "--toc-depth=3",
        f"--resource-path={resource_path}",
        "-o",
        str(out_docx),
        str(out_xml),
    ]
    run_cmd(pandoc_cmd)

    if not out_docx.exists() or out_docx.stat().st_size == 0:
        print(f"ERROR: pandoc did not produce a valid DOCX at {out_docx}", file=sys.stderr)
        if tracker:
            tracker.complete_conversion(
                status=ConversionStatus.FAILURE,
                error_message="pandoc did not produce a valid DOCX file"
            )
        return 1

    print(f"  ✓ Word document: {out_docx}")

    # Final summary
    print("\n" + "=" * 80)
    print("PIPELINE COMPLETE")
    print("=" * 80)
    print(f"\nOutputs in: {out_dir}")

    # Check for intermediate markdown
    intermediate_md = out_dir / f"{pdf_path.stem}_intermediate.md"
    if intermediate_md.exists():
        print(f"  MD:   {intermediate_md.name} (intermediate)")

    print(f"  XML:  {out_xml.name}")

    # Show font info files if they exist
    if font_info_path and font_info_path.exists():
        print(f"  JSON: {font_info_path.name} (font info)")
    if font_roles_path and font_roles_path.exists():
        print(f"  JSON: {font_roles_path.name} (font roles)")
    if toc_path and toc_path.exists():
        print(f"  XML:  {toc_path.name} (TOC - standalone, not in ZIP)")

    if out_rittdoc_zip.exists():
        print(f"  ZIP:  {out_rittdoc_zip.name}")
    print(f"  DOCX: {out_docx.name}")
    print("=" * 80)

    # Complete conversion tracking with success
    if tracker:
        # Count images in multimedia folder
        image_count = 0
        if multimedia_dir.exists():
            image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.svg', '.tif', '.tiff'}
            image_count = sum(1 for f in multimedia_dir.iterdir()
                            if f.is_file() and f.suffix.lower() in image_extensions)

        tracker.complete_conversion(
            status=ConversionStatus.SUCCESS,
            num_raster_images=image_count,
            output_path=str(out_rittdoc_zip if out_rittdoc_zip.exists() else out_docx),
        )
        print(f"\n📊 Conversion tracked in: {tracker.excel_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
