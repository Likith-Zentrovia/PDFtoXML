#!/usr/bin/env python3
"""
Combine Phrases in Para Elements

This script processes a Unified.xml file and combines multiple <phrase> elements
within each <para> tag into flowing text while preserving font formatting.

The script converts font-level formatting into appropriate inline elements:
- Bold fonts → <emphasis role="bold">
- Italic fonts → <emphasis role="italic">
- Bold+Italic → <emphasis role="bold-italic">
- Subscript/Superscript → preserved as-is
- Regular phrases → merged into plain text

Usage:
    python combine_phrases_in_para.py input.xml output.xml
"""

import sys
import re
from pathlib import Path
from typing import List, Tuple, Optional
from xml.etree import ElementTree as ET
from copy import deepcopy


def is_bold_font(font_name: str) -> bool:
    """Check if font name indicates bold styling."""
    if not font_name:
        return False
    font_lower = font_name.lower()
    return any(keyword in font_lower for keyword in ['bold', 'heavy', 'black', 'demi'])


def is_italic_font(font_name: str) -> bool:
    """Check if font name indicates italic styling."""
    if not font_name:
        return False
    font_lower = font_name.lower()
    return any(keyword in font_lower for keyword in ['italic', 'oblique', 'slant'])


def get_emphasis_role(font_name: str) -> Optional[str]:
    """
    Determine the emphasis role based on font name.
    
    Returns:
        'bold', 'italic', 'bold-italic', or None for regular text
    """
    if not font_name:
        return None
    
    is_bold = is_bold_font(font_name)
    is_italic = is_italic_font(font_name)
    
    if is_bold and is_italic:
        return 'bold-italic'
    elif is_bold:
        return 'bold'
    elif is_italic:
        return 'italic'
    else:
        return None


def normalize_attributes(attrs: dict) -> dict:
    """
    Normalize attributes by removing position-only attributes.
    Keep only font-related attributes for comparison.
    """
    normalized = {}
    for key in ['font', 'size', 'color']:
        if key in attrs:
            normalized[key] = attrs[key]
    return normalized


def combine_phrases_in_para(para_elem: ET.Element) -> ET.Element:
    """
    Combine multiple phrase elements within a para into flowing text
    while preserving font formatting.
    
    Args:
        para_elem: The <para> element to process
        
    Returns:
        A new <para> element with combined, flowing text
    """
    # Create new para element with same attributes
    new_para = ET.Element("para", para_elem.attrib)
    
    # Collect all inline elements (phrase, emphasis, subscript, superscript)
    inline_elements = []
    
    for child in para_elem:
        if child.tag in ('phrase', 'emphasis', 'subscript', 'superscript'):
            # Store: (tag, attrs, text, role)
            font_name = child.get('font', '')
            role = child.get('role', get_emphasis_role(font_name))
            text = child.text or ''
            attrs = normalize_attributes(child.attrib)
            
            inline_elements.append({
                'tag': child.tag,
                'attrs': attrs,
                'text': text,
                'role': role,
                'font': font_name
            })
    
    if not inline_elements:
        # No inline elements, just copy any text
        if para_elem.text:
            new_para.text = para_elem.text
        return new_para
    
    # Group consecutive elements with same formatting
    merged_groups = []
    current_group = None
    
    for elem in inline_elements:
        # Determine the element type for this text
        if elem['tag'] in ('subscript', 'superscript'):
            # Keep subscript/superscript as-is
            elem_type = elem['tag']
            elem_role = None
        elif elem['tag'] == 'emphasis' and elem['role']:
            # Already has explicit role
            elem_type = 'emphasis'
            elem_role = elem['role']
        elif elem['role']:
            # Phrase with font-based role
            elem_type = 'emphasis'
            elem_role = elem['role']
        else:
            # Regular text
            elem_type = 'text'
            elem_role = None
        
        # Check if we can merge with current group
        can_merge = (
            current_group is not None and
            current_group['type'] == elem_type and
            current_group['role'] == elem_role and
            normalize_attributes(current_group['attrs']) == elem['attrs']
        )
        
        if can_merge:
            # Merge text into current group
            current_group['text'] += elem['text']
        else:
            # Start new group
            if current_group:
                merged_groups.append(current_group)
            current_group = {
                'type': elem_type,
                'role': elem_role,
                'text': elem['text'],
                'attrs': elem['attrs']
            }
    
    # Don't forget the last group
    if current_group:
        merged_groups.append(current_group)
    
    # Build the new para content with mixed content (text + inline elements)
    if merged_groups:
        # First group - might be plain text or inline element
        first = merged_groups[0]
        if first['type'] == 'text':
            # Plain text goes directly in para.text
            new_para.text = first['text']
            start_idx = 1
        else:
            # First element is inline - para.text is empty
            new_para.text = ''
            start_idx = 0
        
        # Add remaining groups as inline elements
        prev_elem = None
        for group in merged_groups[start_idx:]:
            if group['type'] == 'text':
                # Plain text - add as tail of previous element
                if prev_elem is not None:
                    prev_elem.tail = (prev_elem.tail or '') + group['text']
                else:
                    # Shouldn't happen if first was inline, but handle it
                    new_para.text = (new_para.text or '') + group['text']
            else:
                # Create inline element
                if group['type'] == 'emphasis':
                    elem = ET.SubElement(new_para, 'emphasis')
                    if group['role']:
                        elem.set('role', group['role'])
                elif group['type'] in ('subscript', 'superscript'):
                    elem = ET.SubElement(new_para, group['type'])
                
                elem.text = group['text']
                elem.tail = ''
                prev_elem = elem
    
    return new_para


def indent_xml(elem, level=0):
    """
    Add pretty-print indentation to XML element tree.
    Modifies the element tree in-place.
    """
    indent = "\n" + "  " * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = indent + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = indent
        for child in elem:
            indent_xml(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = indent
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = indent


def process_xml_file(input_path: Path, output_path: Path) -> None:
    """
    Process an XML file, combining phrases in all para elements.
    
    Args:
        input_path: Path to input XML file
        output_path: Path to output XML file
    """
    print(f"Reading {input_path}...")
    tree = ET.parse(input_path)
    root = tree.getroot()
    
    # Find all para elements in the document
    para_count = 0
    for para in root.iter('para'):
        # Replace para element with combined version
        parent = None
        for p in root.iter():
            if para in list(p):
                parent = p
                break
        
        if parent is not None:
            # Get the position of this para in parent
            para_index = list(parent).index(para)
            
            # Create combined para
            new_para = combine_phrases_in_para(para)
            
            # Replace in parent
            parent.remove(para)
            parent.insert(para_index, new_para)
            para_count += 1
    
    print(f"Processed {para_count} para elements")
    
    # Pretty print the XML
    indent_xml(root)
    
    # Write output
    print(f"Writing {output_path}...")
    tree.write(
        output_path,
        encoding='utf-8',
        xml_declaration=True,
        method='xml'
    )
    print("Done!")


def main():
    """Main entry point."""
    if len(sys.argv) != 3:
        print("Usage: python combine_phrases_in_para.py input.xml output.xml")
        sys.exit(1)
    
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)
    
    try:
        process_xml_file(input_path, output_path)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
