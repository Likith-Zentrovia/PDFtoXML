#!/usr/bin/env python3
"""
Table Structure Validator - Validates Camelot table extraction against actual PDF drawing structures.

This script checks if tables detected by Camelot have corresponding drawing structures (borders/lines)
in the PDF, and filters out content that falls outside the actual drawn table boundaries.
"""

import fitz  # PyMuPDF
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import List, Tuple, Dict, Optional
import sys


def parse_multimedia_xml(xml_content: str) -> Dict[int, List[Dict]]:
    """Parse multimedia.xml and extract tables by page."""
    root = ET.fromstring(xml_content)
    tables_by_page = defaultdict(list)
    
    for page_elem in root.findall('.//page'):
        page_idx = int(page_elem.get('index'))
        
        for table_elem in page_elem.findall('.//table'):
            table_info = {
                'id': table_elem.get('id'),
                'x1': float(table_elem.get('x1')),
                'y1': float(table_elem.get('y1')),
                'x2': float(table_elem.get('x2')),
                'y2': float(table_elem.get('y2')),
                'title': table_elem.find('.//title').text if table_elem.find('.//title') is not None else '',
                'rows': []
            }
            
            # Extract row information
            for row_elem in table_elem.findall('.//row'):
                row_entries = []
                for entry_elem in row_elem.findall('.//entry'):
                    entry_info = {
                        'x1': float(entry_elem.get('x1')),
                        'y1': float(entry_elem.get('y1')),
                        'x2': float(entry_elem.get('x2')),
                        'y2': float(entry_elem.get('y2')),
                        'text': ''.join(entry_elem.itertext()).strip()
                    }
                    row_entries.append(entry_info)
                table_info['rows'].append(row_entries)
            
            tables_by_page[page_idx].append(table_info)
    
    return dict(tables_by_page)


def is_background_rect(drawing: Dict) -> bool:
    """Check if drawing is a background fill rectangle (not a table border)."""
    # Has fill color = it's a background
    if drawing.get('fill') is not None:
        return True
    return False


def extract_table_lines(page: fitz.Page, bbox: Tuple[float, float, float, float], 
                        margin: float = 5.0) -> Tuple[List[Tuple], List[Tuple]]:
    """
    Extract horizontal and vertical lines within a bbox that could be table borders.
    Returns (horizontal_lines, vertical_lines) where each line is (x1, x2, y) or (y1, y2, x).
    """
    x1, y1, x2, y2 = bbox
    drawings = page.get_drawings()
    
    horizontal_lines = []
    vertical_lines = []
    
    for drawing in drawings:
        # Skip background rectangles
        if is_background_rect(drawing):
            continue
        
        # Check if drawing intersects with bbox
        draw_rect = drawing.get('rect')
        if not draw_rect:
            continue
            
        dx1, dy1, dx2, dy2 = draw_rect
        if dx2 < x1 - margin or dx1 > x2 + margin or dy2 < y1 - margin or dy1 > y2 + margin:
            continue
        
        # Process line items
        items = drawing.get('items', [])
        for item in items:
            cmd = item[0]
            
            if cmd == 'l' and len(item) >= 3:
                try:
                    p1, p2 = item[1], item[2]
                    lx1, ly1 = float(p1.x), float(p1.y)
                    lx2, ly2 = float(p2.x), float(p2.y)
                    
                    # Check if line is within bbox
                    line_in_bbox = (
                        (x1 - margin <= lx1 <= x2 + margin and y1 - margin <= ly1 <= y2 + margin) or
                        (x1 - margin <= lx2 <= x2 + margin and y1 - margin <= ly2 <= y2 + margin)
                    )
                    
                    if not line_in_bbox:
                        continue
                    
                    # Classify as horizontal or vertical
                    if abs(ly2 - ly1) < 2:  # horizontal line
                        horizontal_lines.append((min(lx1, lx2), max(lx1, lx2), (ly1 + ly2) / 2))
                    elif abs(lx2 - lx1) < 2:  # vertical line
                        vertical_lines.append((min(ly1, ly2), max(ly1, ly2), (lx1 + lx2) / 2))
                except (AttributeError, IndexError, ValueError):
                    pass
    
    return horizontal_lines, vertical_lines


def find_table_structure_bbox(h_lines: List[Tuple], v_lines: List[Tuple], 
                              camelot_bbox: Tuple[float, float, float, float],
                              min_lines: int = 3) -> Optional[Tuple[float, float, float, float]]:
    """
    Determine the actual table structure bbox from grid lines.
    Requires at least min_lines horizontal and vertical lines to be considered a real table.
    """
    if len(h_lines) < min_lines or len(v_lines) < min_lines:
        return None
    
    # Get the outermost lines to define table boundary
    h_ys = [line[2] for line in h_lines]
    v_xs = [line[2] for line in v_lines]
    
    # Get X extent from horizontal lines
    h_min_x = min(line[0] for line in h_lines)
    h_max_x = max(line[1] for line in h_lines)
    
    # Get Y extent from vertical lines
    v_min_y = min(line[0] for line in v_lines)
    v_max_y = max(line[1] for line in v_lines)
    
    # Use the grid formed by the outermost lines
    structure_x1 = min(v_xs) if v_xs else h_min_x
    structure_x2 = max(v_xs) if v_xs else h_max_x
    structure_y1 = min(h_ys) if h_ys else v_min_y
    structure_y2 = max(h_ys) if h_ys else v_max_y
    
    return (structure_x1, structure_y1, structure_x2, structure_y2)


def check_row_in_bbox(row: List[Dict], bbox: Tuple[float, float, float, float]) -> bool:
    """Check if any entry in a row has its center within the bbox."""
    bx1, by1, bx2, by2 = bbox
    
    for entry in row:
        center_x = (entry['x1'] + entry['x2']) / 2
        center_y = (entry['y1'] + entry['y2']) / 2
        
        if bx1 <= center_x <= bx2 and by1 <= center_y <= by2:
            return True
    
    return False


def validate_table_with_structure(pdf_path: str, tables_by_page: Dict[int, List[Dict]], 
                                  verbose: bool = True) -> Dict:
    """
    Validate tables against actual PDF drawing structures.
    Returns validation results with recommendations.
    """
    doc = fitz.open(pdf_path)
    results = {}
    
    for page_idx, tables in tables_by_page.items():
        if page_idx >= len(doc):
            if verbose:
                print(f"Warning: Page {page_idx} not found in PDF")
            continue
            
        page = doc[page_idx]
        page_results = []
        
        for table in tables:
            table_bbox = (table['x1'], table['y1'], table['x2'], table['y2'])
            
            if verbose:
                print(f"\n{'='*80}")
                print(f"Page {page_idx}, Table {table['id']}")
                print(f"Camelot bbox: ({table['x1']:.1f}, {table['y1']:.1f}, {table['x2']:.1f}, {table['y2']:.1f})")
                title_preview = table['title'][:70] + "..." if len(table['title']) > 70 else table['title']
                print(f"Title: {title_preview}")
            
            # Extract table lines
            h_lines, v_lines = extract_table_lines(page, table_bbox)
            
            if verbose:
                print(f"Found {len(h_lines)} horizontal lines, {len(v_lines)} vertical lines")
            
            # Try to find actual table structure
            structure_bbox = find_table_structure_bbox(h_lines, v_lines, table_bbox, min_lines=2)
            
            if structure_bbox:
                if verbose:
                    print(f"✓ Table HAS drawn structure!")
                    print(f"  Structure bbox: ({structure_bbox[0]:.1f}, {structure_bbox[1]:.1f}, "
                          f"{structure_bbox[2]:.1f}, {structure_bbox[3]:.1f})")
                
                # Check which rows fall outside
                rows_to_remove = []
                rows_inside = []
                
                for row_idx, row in enumerate(table['rows']):
                    if check_row_in_bbox(row, structure_bbox):
                        rows_inside.append(row_idx)
                    else:
                        rows_to_remove.append(row_idx)
                
                improvement = len(rows_to_remove) / len(table['rows']) * 100 if table['rows'] else 0
                
                if verbose:
                    print(f"  Rows inside structure: {len(rows_inside)}/{len(table['rows'])}")
                    print(f"  Rows outside structure: {len(rows_to_remove)}/{len(table['rows'])}")
                    
                    if rows_to_remove:
                        print(f"  ⚠ RECOMMENDATION: Remove {len(rows_to_remove)} rows ({improvement:.1f}% reduction)")
                        for row_idx in rows_to_remove[:3]:
                            row_text = ' | '.join([e['text'][:25] for e in table['rows'][row_idx]])
                            print(f"    Row {row_idx}: {row_text}")
                        if len(rows_to_remove) > 3:
                            print(f"    ... and {len(rows_to_remove) - 3} more rows")
                    else:
                        print(f"  ✓ All rows are within the table structure")
                
                page_results.append({
                    'table_id': table['id'],
                    'has_structure': True,
                    'camelot_bbox': table_bbox,
                    'structure_bbox': structure_bbox,
                    'rows_inside': rows_inside,
                    'rows_to_remove': rows_to_remove,
                    'improvement_pct': improvement
                })
            else:
                if verbose:
                    if h_lines or v_lines:
                        print(f"  ⚠ Found some lines but not enough for a table grid")
                        print(f"    (Need at least 2 horizontal AND 2 vertical lines)")
                    else:
                        print(f"  ℹ No drawn table structure (text-only table)")
                
                page_results.append({
                    'table_id': table['id'],
                    'has_structure': False,
                    'camelot_bbox': table_bbox,
                    'reason': 'insufficient_lines' if (h_lines or v_lines) else 'no_lines'
                })
        
        results[page_idx] = page_results
    
    doc.close()
    return results


def print_summary(results: Dict):
    """Print summary of validation results."""
    print(f"\n{'='*80}")
    print("VALIDATION SUMMARY")
    print(f"{'='*80}")
    
    total_tables = sum(len(page_results) for page_results in results.values())
    tables_with_structure = sum(1 for page_results in results.values() 
                               for r in page_results if r.get('has_structure'))
    tables_need_trimming = sum(1 for page_results in results.values() 
                              for r in page_results 
                              if r.get('has_structure') and r.get('rows_to_remove'))
    
    print(f"Total tables analyzed: {total_tables}")
    print(f"Tables with drawn structure: {tables_with_structure}")
    print(f"Tables needing trimming: {tables_need_trimming}")
    print(f"Text-only tables (no borders): {total_tables - tables_with_structure}")
    
    if tables_need_trimming > 0:
        print(f"\n✓ CONCLUSION: {tables_need_trimming}/{tables_with_structure} tables with structure "
              f"have rows outside boundaries.")
        print("  This validation approach successfully identifies Camelot false positives.")
    else:
        print(f"\n✓ CONCLUSION: All tables with drawn structures match Camelot extraction well.")


def main():
    # Embedded test data from user's multimedia.xml
    multimedia_xml = """<?xml version="1.0" encoding="UTF-8"?>
<multimedia>
  <page index="21" width="549.0" height="774.0">
    <table id="p21_table1" frame="all" x1="147.5421" y1="251.49331665039062" x2="405.67460000000005" y2="349.6256">
      <title>Table 1. Frequencies associated with different types of electromagnetic radiation.</title>
      <tgroup cols="2"><tbody>
        <row>
          <entry x1="67.4641" y1="252.47540000000004" x2="254.31985" y2="270.09755">
            <para>Types of Radiation</para>
          </entry>
        </row>
      </tbody></tgroup>
    </table>
  </page>
  <page index="25" width="549.0" height="774.0">
    <table id="p25_table1" frame="all" x1="211.9633" y1="186.30142211914062" x2="323.5887" y2="282.91450000000003">
      <title>Table 2. MZ recovery fractions at different multiples of T1 times.</title>
      <tgroup cols="2"><tbody>
        <row>
          <entry x1="67.46530000000001" y1="186.2762499999999" x2="263.15125" y2="204.46820000000002">
            <para>t/T 1</para>
          </entry>
        </row>
      </tbody></tgroup>
    </table>
  </page>
  <page index="27" width="549.0" height="774.0">
    <table id="p27_table3" frame="all" x1="212.6631" y1="531.6776123046875" x2="323.6617" y2="628.957">
      <title>Table 3. MXY residual fractions at different multiples of T2 times.</title>
      <tgroup cols="2"><tbody>
        <row>
          <entry x1="137.616" y1="531.9762499999999" x2="263.22429999999997" y2="550.78075">
            <para>t/T 2</para>
          </entry>
        </row>
      </tbody></tgroup>
    </table>
  </page>
  <page index="53" width="549.0" height="774.0">
    <table id="p53_table1" frame="all" x1="105.7731" y1="346.3551940917969" x2="430.9372" y2="467.6488">
      <title>Table 1. Magnetic susceptibility and density of common materials.</title>
      <tgroup cols="3"><tbody>
        <row>
          <entry x1="67.464" y1="350.83635" x2="184.70555000000002" y2="374.8482">
            <para>Material</para>
          </entry>
        </row>
      </tbody></tgroup>
    </table>
  </page>
</multimedia>
"""
    
    if len(sys.argv) < 2:
        print("Usage: python table_structure_validator.py <pdf_path>")
        print("\nThis script validates Camelot table extraction against actual PDF drawing structures.")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    
    print("Parsing multimedia.xml...")
    tables_by_page = parse_multimedia_xml(multimedia_xml)
    print(f"Found {sum(len(tables) for tables in tables_by_page.values())} tables "
          f"across {len(tables_by_page)} pages\n")
    
    print(f"Validating against PDF: {pdf_path}")
    results = validate_table_with_structure(pdf_path, tables_by_page, verbose=True)
    
    print_summary(results)


if __name__ == "__main__":
    main()
