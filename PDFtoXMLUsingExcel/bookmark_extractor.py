#!/usr/bin/env python3
"""
Bookmark Extractor Module

Extracts hierarchical bookmarks/outlines from PDFs and maps them to DocBook structure:
- Level 0 (PyMuPDF level 1) → <chapter>
- Level 1 (PyMuPDF level 2) → <sect1>
- Level 2 (PyMuPDF level 3) → <sect2>
- Level 3+ → <sect3> - <sect5> (max)

Also identifies:
- Front Matter: Everything before first chapter
- Back Matter: Everything after last chapter

Usage:
    from bookmark_extractor import extract_bookmarks, BookmarkHierarchy

    hierarchy = extract_bookmarks(pdf_path)
    if hierarchy:
        print(f"Found {len(hierarchy.root_items)} top-level bookmarks")
        print(f"Front matter ends at page {hierarchy.front_matter_end_page}")
        print(f"Back matter starts at page {hierarchy.back_matter_start_page}")
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Tuple, Any
from lxml import etree

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False


@dataclass
class BookmarkNode:
    """Represents a single bookmark with hierarchy information."""
    level: int          # 0 = chapter, 1 = sect1, 2 = sect2, etc.
    title: str          # Bookmark title
    start_page: int     # 0-indexed start page
    end_page: int       # 0-indexed end page (calculated)
    children: List['BookmarkNode'] = field(default_factory=list)

    def __repr__(self):
        return f"BookmarkNode(level={self.level}, title='{self.title[:30]}...', pages={self.start_page}-{self.end_page}, children={len(self.children)})"


@dataclass
class BookmarkHierarchy:
    """Complete bookmark tree for a document."""
    root_items: List[BookmarkNode]              # Top-level items (chapters)
    total_pages: int                            # Total pages in document
    front_matter_end_page: int = -1             # Last page of front matter (-1 if none)
    back_matter_start_page: int = -1            # First page of back matter (-1 if none)
    has_parts: bool = False                     # Whether document has Part structure

    @property
    def first_chapter_page(self) -> Optional[int]:
        """Get the page where first chapter starts."""
        if self.root_items:
            return self.root_items[0].start_page
        return None

    @property
    def last_chapter_end_page(self) -> Optional[int]:
        """Get the page where last chapter ends."""
        if self.root_items:
            return self.root_items[-1].end_page
        return None

    def get_all_bookmarks_flat(self) -> List[BookmarkNode]:
        """Get all bookmarks in a flat list (depth-first order)."""
        result = []

        def collect(nodes: List[BookmarkNode]):
            for node in nodes:
                result.append(node)
                collect(node.children)

        collect(self.root_items)
        return result

    def get_bookmark_at_page(self, page_num: int) -> Optional[BookmarkNode]:
        """Get the deepest bookmark that starts at the given page."""
        result = None

        def search(nodes: List[BookmarkNode]):
            nonlocal result
            for node in nodes:
                if node.start_page == page_num:
                    result = node
                search(node.children)

        search(self.root_items)
        return result

    def get_bookmarks_starting_at_page(self, page_num: int) -> List[BookmarkNode]:
        """Get all bookmarks that start at the given page (may have multiple levels)."""
        result = []

        def search(nodes: List[BookmarkNode]):
            for node in nodes:
                if node.start_page == page_num:
                    result.append(node)
                search(node.children)

        search(self.root_items)
        return sorted(result, key=lambda x: x.level)  # Sort by level (0 first)

    def __repr__(self):
        return (f"BookmarkHierarchy(chapters={len(self.root_items)}, "
                f"total_pages={self.total_pages}, "
                f"front_matter_end={self.front_matter_end_page}, "
                f"back_matter_start={self.back_matter_start_page})")


def extract_bookmarks_from_pdf(pdf_path: str) -> Optional[BookmarkHierarchy]:
    """
    Extract hierarchical bookmarks from PDF using PyMuPDF's get_toc().

    PyMuPDF's get_toc() returns: [[level, title, page, dest], ...]
    - level is 1-indexed (1 = top level)
    - page is 1-indexed

    We convert to 0-indexed for internal use.

    Args:
        pdf_path: Path to the PDF file

    Returns:
        BookmarkHierarchy with all levels, or None if no bookmarks
    """
    if not PYMUPDF_AVAILABLE:
        print("  Warning: PyMuPDF not available, cannot extract bookmarks from PDF")
        return None

    try:
        doc = fitz.open(str(pdf_path))
        toc = doc.get_toc()  # Returns [[level, title, page], ...]
        total_pages = len(doc)
        doc.close()

        if not toc:
            print("  No bookmarks/outline found in PDF")
            return None

        print(f"  Found {len(toc)} bookmark entries in PDF")

        return _build_hierarchy_from_toc(toc, total_pages)

    except Exception as e:
        print(f"  Error extracting bookmarks from PDF: {e}")
        return None


def extract_bookmarks_from_xml(xml_path: str) -> Optional[BookmarkHierarchy]:
    """
    Extract hierarchical bookmarks from pdftohtml XML <outline> element.

    Structure of pdftohtml outline:
      <outline>
        <item page="1">Chapter 1</item>
        <outline>
          <item page="5">Section 1.1</item>
          <item page="10">Section 1.2</item>
        </outline>
        <item page="20">Chapter 2</item>
        ...
      </outline>

    Args:
        xml_path: Path to the pdftohtml XML file

    Returns:
        BookmarkHierarchy with all levels, or None if no outline
    """
    try:
        tree = etree.parse(str(xml_path))
        root = tree.getroot()

        # Find outline element
        outline_el = root.find('.//outline')
        if outline_el is None:
            print("  No <outline> element found in XML")
            return None

        # Get total pages from XML
        pages = root.findall('.//page')
        total_pages = len(pages) if pages else 0

        # Parse outline recursively
        bookmarks = _parse_outline_element(outline_el, level=0)

        if not bookmarks:
            print("  Outline element is empty")
            return None

        print(f"  Found {len(bookmarks)} top-level bookmark entries in XML outline")

        # Calculate end pages
        _calculate_end_pages(bookmarks, total_pages - 1)

        # Build hierarchy
        hierarchy = BookmarkHierarchy(
            root_items=bookmarks,
            total_pages=total_pages
        )

        # Calculate front/back matter
        _calculate_matter_boundaries(hierarchy)

        return hierarchy

    except Exception as e:
        print(f"  Error extracting bookmarks from XML: {e}")
        return None


def _build_hierarchy_from_toc(toc: List[List], total_pages: int) -> BookmarkHierarchy:
    """
    Build BookmarkHierarchy from PyMuPDF TOC list.

    Args:
        toc: List of [level, title, page, ...] from PyMuPDF
        total_pages: Total pages in document

    Returns:
        BookmarkHierarchy
    """
    if not toc:
        return None

    # Convert TOC to BookmarkNodes with proper nesting
    # PyMuPDF levels are 1-indexed, we use 0-indexed

    root_items: List[BookmarkNode] = []
    stack: List[Tuple[int, BookmarkNode]] = []  # [(level, node), ...]

    for entry in toc:
        level = entry[0] - 1  # Convert 1-indexed to 0-indexed
        title = entry[1] if len(entry) > 1 else "Untitled"
        page = (entry[2] - 1) if len(entry) > 2 else 0  # Convert 1-indexed to 0-indexed

        # Ensure page is within bounds
        page = max(0, min(page, total_pages - 1))

        # Cap level at 5 (sect5 is the maximum in DocBook)
        display_level = min(level, 5)

        node = BookmarkNode(
            level=display_level,
            title=title.strip(),
            start_page=page,
            end_page=total_pages - 1  # Will be calculated later
        )

        # Find parent based on level
        while stack and stack[-1][0] >= level:
            stack.pop()

        if stack:
            # Add as child of current parent
            parent_node = stack[-1][1]
            parent_node.children.append(node)
        else:
            # Top-level item
            root_items.append(node)

        stack.append((level, node))

    # Calculate end pages for all nodes
    _calculate_end_pages(root_items, total_pages - 1)

    # Build hierarchy
    hierarchy = BookmarkHierarchy(
        root_items=root_items,
        total_pages=total_pages
    )

    # Calculate front/back matter boundaries
    _calculate_matter_boundaries(hierarchy)

    return hierarchy


def _parse_outline_element(outline_el: etree._Element, level: int = 0) -> List[BookmarkNode]:
    """
    Recursively parse an <outline> element and its nested items.

    Args:
        outline_el: The <outline> element to parse
        level: Current nesting level (0 = chapter, 1 = sect1, etc.)

    Returns:
        List of BookmarkNode objects
    """
    bookmarks = []
    current_item = None

    for child in outline_el:
        if child.tag == 'item':
            # Get page number (1-indexed in pdftohtml, convert to 0-indexed)
            page_str = child.get('page', '1')
            try:
                page = int(page_str) - 1
            except ValueError:
                page = 0

            page = max(0, page)

            # Get title
            title = child.text.strip() if child.text else "Untitled"

            # Cap level at 5
            display_level = min(level, 5)

            current_item = BookmarkNode(
                level=display_level,
                title=title,
                start_page=page,
                end_page=page  # Will be calculated later
            )
            bookmarks.append(current_item)

        elif child.tag == 'outline' and current_item is not None:
            # Nested outline - these are children of the previous item
            children = _parse_outline_element(child, level + 1)
            current_item.children.extend(children)

    return bookmarks


def _calculate_end_pages(nodes: List[BookmarkNode], parent_end_page: int):
    """
    Calculate end pages for all bookmark nodes.

    A bookmark's end page is:
    - The page before the next sibling's start page, OR
    - The parent's end page if it's the last child

    Args:
        nodes: List of sibling BookmarkNodes
        parent_end_page: End page of the parent (or document end for root)
    """
    for i, node in enumerate(nodes):
        # Determine end page
        if i + 1 < len(nodes):
            # Next sibling exists - end before it starts
            node.end_page = nodes[i + 1].start_page - 1
        else:
            # Last sibling - inherit parent's end page
            node.end_page = parent_end_page

        # Ensure end_page >= start_page
        node.end_page = max(node.end_page, node.start_page)

        # Recursively calculate for children
        if node.children:
            _calculate_end_pages(node.children, node.end_page)


def _calculate_matter_boundaries(hierarchy: BookmarkHierarchy):
    """
    Calculate front matter and back matter page boundaries.

    Front Matter: Pages 0 to (first_chapter_page - 1)
    Back Matter: Pages after last chapter's content

    Args:
        hierarchy: The BookmarkHierarchy to update
    """
    if not hierarchy.root_items:
        return

    first_chapter_page = hierarchy.root_items[0].start_page
    last_chapter_end = hierarchy.root_items[-1].end_page

    # Front matter: everything before first chapter
    if first_chapter_page > 0:
        hierarchy.front_matter_end_page = first_chapter_page - 1
    else:
        hierarchy.front_matter_end_page = -1

    # Back matter: everything after last chapter
    # Note: This is a simple heuristic. In practice, you may want to detect
    # specific back matter sections (Index, Glossary, etc.) with same font as chapters
    if last_chapter_end < hierarchy.total_pages - 1:
        hierarchy.back_matter_start_page = last_chapter_end + 1
    else:
        hierarchy.back_matter_start_page = -1


def extract_bookmarks(pdf_path: str, xml_path: str = None) -> Optional[BookmarkHierarchy]:
    """
    Extract bookmarks from PDF or XML, preferring PDF if available.

    Args:
        pdf_path: Path to the PDF file
        xml_path: Optional path to pdftohtml XML file

    Returns:
        BookmarkHierarchy or None if no bookmarks found
    """
    # Try PDF first (more reliable)
    hierarchy = extract_bookmarks_from_pdf(pdf_path)

    if hierarchy:
        return hierarchy

    # Fall back to XML if provided
    if xml_path:
        hierarchy = extract_bookmarks_from_xml(xml_path)

    return hierarchy


def print_hierarchy(hierarchy: BookmarkHierarchy, max_depth: int = 3):
    """
    Print the bookmark hierarchy for debugging.

    Args:
        hierarchy: The BookmarkHierarchy to print
        max_depth: Maximum depth to print (default 3)
    """
    if not hierarchy:
        print("No bookmark hierarchy")
        return

    print(f"\n{'='*60}")
    print("BOOKMARK HIERARCHY")
    print(f"{'='*60}")
    print(f"Total pages: {hierarchy.total_pages}")
    print(f"Front matter: pages 0-{hierarchy.front_matter_end_page}" if hierarchy.front_matter_end_page >= 0 else "No front matter")
    print(f"Back matter: pages {hierarchy.back_matter_start_page}-{hierarchy.total_pages-1}" if hierarchy.back_matter_start_page >= 0 else "No back matter")
    print(f"Chapters: {len(hierarchy.root_items)}")
    print(f"{'='*60}")

    def print_node(node: BookmarkNode, indent: int = 0):
        if node.level > max_depth:
            return
        prefix = "  " * indent
        level_name = ["Chapter", "Sect1", "Sect2", "Sect3", "Sect4", "Sect5"][min(node.level, 5)]
        print(f"{prefix}{level_name}: '{node.title[:50]}' (pages {node.start_page+1}-{node.end_page+1})")
        for child in node.children:
            print_node(child, indent + 1)

    for node in hierarchy.root_items:
        print_node(node)

    print(f"{'='*60}\n")


# =============================================================================
# CLI for testing
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Extract and display PDF bookmark hierarchy")
    parser.add_argument("pdf", help="Path to PDF file")
    parser.add_argument("--xml", help="Optional path to pdftohtml XML file")
    parser.add_argument("--depth", type=int, default=3, help="Maximum depth to display (default: 3)")

    args = parser.parse_args()

    hierarchy = extract_bookmarks(args.pdf, args.xml)

    if hierarchy:
        print_hierarchy(hierarchy, max_depth=args.depth)
    else:
        print("No bookmarks found in PDF")
