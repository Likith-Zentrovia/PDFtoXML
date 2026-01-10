"""
Phrase Combiner Module

Utilities for combining phrase elements within para tags while preserving
font formatting. Can be used as a library or standalone script.

Usage as library:
    from phrase_combiner import combine_phrases_in_para
    
    new_para = combine_phrases_in_para(para_element, wrap_in_text=False)

Usage as script:
    python3 phrase_combiner.py input.xml output.xml [--wrap-in-text]
"""

from typing import Optional, List, Dict
from xml.etree import ElementTree as ET


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
    
    Args:
        font_name: Font name string (e.g., "Arial-Bold", "Times-Italic")
        
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


def normalize_attributes(attrs: dict, keep_position: bool = False) -> dict:
    """
    Normalize attributes for comparison.
    
    Args:
        attrs: Dictionary of element attributes
        keep_position: If True, keep position attributes (top, left)
        
    Returns:
        Normalized attribute dictionary
    """
    keys = ['font', 'size', 'color']
    if keep_position:
        keys.extend(['top', 'left'])
    
    normalized = {}
    for key in keys:
        if key in attrs:
            normalized[key] = attrs[key]
    return normalized


def combine_phrases_in_para(
    para_elem: ET.Element,
    wrap_in_text: bool = False,
    preserve_position: bool = False
) -> ET.Element:
    """
    Combine multiple phrase elements within a para into flowing text
    while preserving font formatting.
    
    This function:
    1. Collects all inline elements (phrase, emphasis, subscript, superscript)
    2. Converts font names to semantic emphasis roles
    3. Merges consecutive elements with identical formatting
    4. Builds a new para with combined, flowing text
    
    Args:
        para_elem: The <para> element to process
        wrap_in_text: If True, wrap combined content in <text> element
        preserve_position: If True, keep position attributes on phrases
        
    Returns:
        A new <para> element with combined, flowing text
        
    Example:
        Input:
            <para>
                <phrase font="Arial">Hello</phrase>
                <phrase font="Arial-Bold">world</phrase>
            </para>
            
        Output:
            <para>Hello<emphasis role="bold">world</emphasis></para>
    """
    # Create new para element with same attributes
    new_para = ET.Element("para", para_elem.attrib)
    
    # Collect all inline elements
    inline_elements = []
    
    for child in para_elem:
        if child.tag in ('phrase', 'emphasis', 'subscript', 'superscript'):
            # Extract font and role information
            font_name = child.get('font', '')
            role = child.get('role', get_emphasis_role(font_name))
            text = child.text or ''
            attrs = normalize_attributes(child.attrib, keep_position=preserve_position)
            
            inline_elements.append({
                'tag': child.tag,
                'attrs': attrs,
                'text': text,
                'role': role,
                'font': font_name
            })
    
    if not inline_elements:
        # No inline elements, preserve any direct text content
        if para_elem.text:
            new_para.text = para_elem.text
        return new_para
    
    # Group consecutive elements with same formatting
    merged_groups = []
    current_group = None
    
    for elem in inline_elements:
        # Determine the element type for this text
        if elem['tag'] in ('subscript', 'superscript'):
            elem_type = elem['tag']
            elem_role = None
        elif elem['tag'] == 'emphasis' and elem['role']:
            elem_type = 'emphasis'
            elem_role = elem['role']
        elif elem['role']:
            elem_type = 'emphasis'
            elem_role = elem['role']
        else:
            elem_type = 'text'
            elem_role = None
        
        # Check if we can merge with current group
        can_merge = (
            current_group is not None and
            current_group['type'] == elem_type and
            current_group['role'] == elem_role and
            current_group['attrs'] == elem['attrs']
        )
        
        if can_merge:
            current_group['text'] += elem['text']
        else:
            if current_group:
                merged_groups.append(current_group)
            current_group = {
                'type': elem_type,
                'role': elem_role,
                'text': elem['text'],
                'attrs': elem['attrs']
            }
    
    # Add the last group
    if current_group:
        merged_groups.append(current_group)
    
    # Build the content
    if wrap_in_text:
        text_wrapper = ET.SubElement(new_para, "text")
        target_elem = text_wrapper
    else:
        target_elem = new_para
    
    # Add merged content to target element
    if merged_groups:
        first = merged_groups[0]
        if first['type'] == 'text':
            target_elem.text = first['text']
            start_idx = 1
        else:
            target_elem.text = ''
            start_idx = 0
        
        prev_elem = None
        for group in merged_groups[start_idx:]:
            if group['type'] == 'text':
                # Plain text goes in tail of previous element
                if prev_elem is not None:
                    prev_elem.tail = (prev_elem.tail or '') + group['text']
                else:
                    target_elem.text = (target_elem.text or '') + group['text']
            else:
                # Create inline element
                if group['type'] == 'emphasis':
                    elem = ET.SubElement(target_elem, 'emphasis')
                    if group['role']:
                        elem.set('role', group['role'])
                    # Optionally add font attributes
                    if preserve_position and group['attrs']:
                        for key, value in group['attrs'].items():
                            elem.set(key, value)
                elif group['type'] in ('subscript', 'superscript'):
                    elem = ET.SubElement(target_elem, group['type'])
                    if preserve_position and group['attrs']:
                        for key, value in group['attrs'].items():
                            elem.set(key, value)
                
                elem.text = group['text']
                elem.tail = ''
                prev_elem = elem
    
    return new_para


def combine_phrases_in_tree(
    tree: ET.ElementTree,
    wrap_in_text: bool = False,
    preserve_position: bool = False,
    in_place: bool = False
) -> ET.ElementTree:
    """
    Process all para elements in an ElementTree.
    
    Args:
        tree: The ElementTree to process
        wrap_in_text: If True, wrap combined content in <text> element
        preserve_position: If True, keep position attributes
        in_place: If True, modify tree in place; otherwise create a copy
        
    Returns:
        Processed ElementTree
    """
    if not in_place:
        import copy
        tree = copy.deepcopy(tree)
    
    root = tree.getroot()
    
    # Find all para elements
    for para in list(root.iter('para')):
        # Find parent
        parent = None
        for p in root.iter():
            if para in list(p):
                parent = p
                break
        
        if parent is not None:
            para_index = list(parent).index(para)
            new_para = combine_phrases_in_para(para, wrap_in_text, preserve_position)
            parent.remove(para)
            parent.insert(para_index, new_para)
    
    return tree


# Command-line interface
if __name__ == '__main__':
    import sys
    import argparse
    from pathlib import Path
    
    def indent_xml(elem, level=0):
        """Add pretty-print indentation to XML."""
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
    
    parser = argparse.ArgumentParser(
        description='Combine phrases in para elements while preserving font formatting'
    )
    parser.add_argument('input', help='Input XML file path')
    parser.add_argument('output', help='Output XML file path')
    parser.add_argument(
        '--wrap-in-text',
        action='store_true',
        help='Wrap combined content in <text> element'
    )
    parser.add_argument(
        '--preserve-position',
        action='store_true',
        help='Preserve position attributes on inline elements'
    )
    
    args = parser.parse_args()
    
    input_path = Path(args.input)
    output_path = Path(args.output)
    
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    
    try:
        print(f"Reading {input_path}...")
        tree = ET.parse(input_path)
        
        print("Processing para elements...")
        tree = combine_phrases_in_tree(
            tree,
            wrap_in_text=args.wrap_in_text,
            preserve_position=args.preserve_position,
            in_place=True
        )
        
        # Count processed paras
        para_count = sum(1 for _ in tree.getroot().iter('para'))
        mode = "wrapped" if args.wrap_in_text else "flat"
        print(f"Processed {para_count} para elements (mode: {mode})")
        
        # Pretty print
        indent_xml(tree.getroot())
        
        print(f"Writing {output_path}...")
        tree.write(
            output_path,
            encoding='utf-8',
            xml_declaration=True,
            method='xml'
        )
        print("Done!")
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
