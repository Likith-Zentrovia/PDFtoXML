#!/usr/bin/env python3
"""
Add Table of Contents to Book.XML in DTD-Compliant Way

This script adds a proper <toc> element to Book.XML that conforms to the
RittDoc DTD and R2 DocBook XML Conversion Specification.

DTD Structure for TOC:
  <!ELEMENT toc (beginpage?, title?, tocfront*, (tocpart|tocchap)*, tocback*)>
  <!ELEMENT tocchap (tocentry+, toclevel1*)>
  <!ELEMENT tocentry (#PCDATA)>  -- with linkend attribute per R2 spec
  <!ELEMENT toclevel1 (tocentry+, toclevel2*)>

R2 Spec Requirements:
  - tocentry MUST use linkend attribute (not <ulink>)
  - tocchap MUST have label attribute
  - Section hierarchy uses toclevel1, toclevel2, etc.
"""

import re
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Optional


def extract_chapter_entities(book_xml_content: str) -> List[Tuple[str, str]]:
    """
    Extract chapter entity declarations from Book.XML.

    Returns:
        List of tuples: (entity_name, filename)
    """
    chapters = []

    # Find DOCTYPE declaration
    doctype_match = re.search(r'<!DOCTYPE[^>]+\[(.*?)\]>', book_xml_content, re.DOTALL)
    if not doctype_match:
        return chapters

    doctype_content = doctype_match.group(1)

    # Extract entity declarations: <!ENTITY ch0001 SYSTEM "ch0001.xml">
    entity_pattern = r'<!ENTITY\s+(ch\d+)\s+SYSTEM\s+"([^"]+)">'
    for match in re.finditer(entity_pattern, doctype_content):
        entity_name = match.group(1)
        filename = match.group(2)
        chapters.append((entity_name, filename))

    return chapters


def extract_preface_entities(book_xml_content: str) -> List[Tuple[str, str]]:
    """Extract preface entity declarations from Book.XML."""
    prefaces = []
    doctype_match = re.search(r'<!DOCTYPE[^>]+\[(.*?)\]>', book_xml_content, re.DOTALL)
    if doctype_match:
        doctype_content = doctype_match.group(1)
        entity_pattern = r'<!ENTITY\s+(pr\d+)\s+SYSTEM\s+"([^"]+)">'
        for match in re.finditer(entity_pattern, doctype_content):
            prefaces.append((match.group(1), match.group(2)))
    return prefaces


def extract_appendix_entities(book_xml_content: str) -> List[Tuple[str, str]]:
    """Extract appendix entity declarations from Book.XML."""
    appendices = []
    doctype_match = re.search(r'<!DOCTYPE[^>]+\[(.*?)\]>', book_xml_content, re.DOTALL)
    if doctype_match:
        doctype_content = doctype_match.group(1)
        entity_pattern = r'<!ENTITY\s+(ap\d+)\s+SYSTEM\s+"([^"]+)">'
        for match in re.finditer(entity_pattern, doctype_content):
            appendices.append((match.group(1), match.group(2)))
    return appendices


def read_chapter_info(chapter_path: Path) -> Dict:
    """
    Extract title, id, label, and sections from a chapter XML file.

    Returns:
        Dict with 'id', 'label', 'title', and 'sections' (list of sect1 info)
    """
    info = {'id': '', 'label': '', 'title': '', 'sections': []}

    try:
        content = chapter_path.read_text(encoding='utf-8')

        # Extract chapter id and label
        chapter_match = re.search(r'<chapter\s+id="([^"]+)"(?:\s+label="([^"]+)")?', content)
        if chapter_match:
            info['id'] = chapter_match.group(1)
            info['label'] = chapter_match.group(2) or ''

        # Extract chapter title
        title_match = re.search(r'<chapter[^>]*>.*?<title[^>]*>(.*?)</title>', content, re.DOTALL)
        if title_match:
            title_text = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
            info['title'] = title_text

        # Extract sect1 elements
        sect1_pattern = re.compile(r'<sect1\s+id="([^"]+)"[^>]*>.*?<title[^>]*>(.*?)</title>', re.DOTALL)
        for match in sect1_pattern.finditer(content):
            sect1_id = match.group(1)
            sect1_title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            if sect1_title:  # Only include sections with titles
                info['sections'].append({'id': sect1_id, 'title': sect1_title})

    except Exception as e:
        print(f"Warning: Could not read from {chapter_path}: {e}")

    return info


def read_chapter_title(chapter_path: Path) -> str:
    """
    Extract title from a chapter XML file.

    Returns:
        Chapter title text, or empty string if not found
    """
    info = read_chapter_info(chapter_path)
    return info.get('title', '')


def _generate_chapter_label(chapter_num: int) -> str:
    """Generate chapter label per R2 spec."""
    if chapter_num == 0:
        return "intro"
    return str(chapter_num)


def generate_toc_element(
    chapters: List[Tuple[str, str, str]],
    chapter_dir: Path,
    prefaces: Optional[List[Tuple[str, str]]] = None,
    appendices: Optional[List[Tuple[str, str]]] = None,
    include_sections: bool = True
) -> str:
    """
    Generate a DTD-compliant <toc> element per R2 spec.

    Args:
        chapters: List of (entity_name, filename, title) tuples
        chapter_dir: Directory containing chapter XML files
        prefaces: Optional list of (entity_name, filename) for preface files
        appendices: Optional list of (entity_name, filename) for appendix files
        include_sections: Whether to include sect1 entries as toclevel1

    Returns:
        XML string for the <toc> element
    """
    toc_lines = [
        '  <toc>',
        '    <title>Table of Contents</title>',
    ]

    # Add tocfront for prefaces
    if prefaces:
        for entity_name, filename in prefaces:
            preface_path = chapter_dir / filename
            if preface_path.exists():
                info = read_chapter_info(preface_path)
                title = info.get('title') or 'Preface'
                preface_id = info.get('id') or entity_name
                toc_lines.append(f'    <tocfront label="Preface" linkend="{preface_id}">{title}</tocfront>')

    # Add tocchap for each chapter with proper linkend (not ulink)
    for idx, (entity_name, filename, title) in enumerate(chapters):
        if not title:
            title = filename.replace('.xml', '')  # Fallback to filename if no title found

        # Get full chapter info including sections
        chapter_path = chapter_dir / filename
        chapter_info = read_chapter_info(chapter_path) if chapter_path.exists() else {}

        # Use chapter ID from file, or derive from entity name
        chapter_id = chapter_info.get('id') or entity_name

        # Get label from file or generate
        label = chapter_info.get('label')
        if not label:
            # Derive label from entity name (ch0000 -> intro, ch0001 -> 1)
            try:
                chapter_num = int(entity_name.replace('ch', ''))
                label = _generate_chapter_label(chapter_num)
            except ValueError:
                label = str(idx + 1)

        # Create tocchap element with label and tocentry using linkend
        toc_lines.append(f'    <tocchap label="{label}">')
        toc_lines.append(f'      <tocentry linkend="{chapter_id}">{title}</tocentry>')

        # Add toclevel1 for each sect1 if requested
        if include_sections and chapter_info.get('sections'):
            for section in chapter_info['sections']:
                sect_id = section['id']
                sect_title = section['title']
                toc_lines.append(f'      <toclevel1>')
                toc_lines.append(f'        <tocentry linkend="{sect_id}">{sect_title}</tocentry>')
                toc_lines.append(f'      </toclevel1>')

        toc_lines.append(f'    </tocchap>')

    # Add tocback for appendices
    if appendices:
        for idx, (entity_name, filename) in enumerate(appendices):
            appendix_path = chapter_dir / filename
            if appendix_path.exists():
                info = read_chapter_info(appendix_path)
                title = info.get('title') or f'Appendix {chr(65 + idx)}'
                appendix_id = info.get('id') or entity_name
                label = info.get('label') or chr(65 + idx)  # A, B, C...
                toc_lines.append(f'    <tocback label="{label}" linkend="{appendix_id}">{title}</tocback>')

    toc_lines.append('  </toc>')

    return '\n'.join(toc_lines)


def add_toc_to_book_xml(
    book_xml_path: Path,
    chapter_dir: Path,
    output_path: Path = None
) -> bool:
    """
    Add TOC element to Book.XML in a DTD-compliant way.

    Args:
        book_xml_path: Path to Book.XML file
        chapter_dir: Directory containing chapter XML files
        output_path: Optional output path (default: overwrite input)

    Returns:
        True if successful, False otherwise
    """
    if output_path is None:
        output_path = book_xml_path

    # Read Book.XML
    content = book_xml_path.read_text(encoding='utf-8')

    # Extract chapter entities
    chapter_entities = extract_chapter_entities(content)
    if not chapter_entities:
        print("Error: No chapter entities found in Book.XML")
        return False

    print(f"Found {len(chapter_entities)} chapter references")

    # Extract preface and appendix entities
    preface_entities = extract_preface_entities(content)
    appendix_entities = extract_appendix_entities(content)

    if preface_entities:
        print(f"Found {len(preface_entities)} preface references")
    if appendix_entities:
        print(f"Found {len(appendix_entities)} appendix references")

    # Read chapter titles
    chapters_with_titles = []
    for entity_name, filename in chapter_entities:
        chapter_path = chapter_dir / filename
        title = read_chapter_title(chapter_path) if chapter_path.exists() else ""
        chapters_with_titles.append((entity_name, filename, title))
        if title:
            print(f"  {filename}: {title}")
        else:
            print(f"  {filename}: (no title found)")

    # Generate TOC element with full R2 spec compliance
    toc_xml = generate_toc_element(
        chapters=chapters_with_titles,
        chapter_dir=chapter_dir,
        prefaces=preface_entities,
        appendices=appendix_entities,
        include_sections=True
    )

    # Find insertion point (after <bookinfo> and before first &ch reference)
    # Pattern: </bookinfo> ... &ch0001;
    insertion_pattern = r'(</bookinfo>.*?)(  &ch\d+;)'

    match = re.search(insertion_pattern, content, re.DOTALL)
    if not match:
        print("Error: Could not find insertion point for TOC")
        print("Looking for pattern: </bookinfo> ... &ch0001;")
        return False

    # Insert TOC
    new_content = content[:match.end(1)] + '\n' + toc_xml + '\n' + content[match.start(2):]

    # Write output
    output_path.write_text(new_content, encoding='utf-8')
    print(f"\n✓ TOC added successfully to {output_path}")
    print(f"  Added {len(chapters_with_titles)} chapter entries")

    return True


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Add DTD-compliant TOC to Book.XML"
    )
    parser.add_argument(
        "package_dir",
        help="Directory containing Book.XML and chapter files (e.g., extracted ZIP)"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output Book.XML path (default: overwrite input)"
    )

    args = parser.parse_args()

    package_dir = Path(args.package_dir)
    if not package_dir.exists():
        print(f"Error: Directory not found: {package_dir}")
        sys.exit(1)

    book_xml_path = package_dir / "Book.XML"
    if not book_xml_path.exists():
        print(f"Error: Book.XML not found in {package_dir}")
        sys.exit(1)

    output_path = Path(args.output) if args.output else book_xml_path

    success = add_toc_to_book_xml(book_xml_path, package_dir, output_path)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
