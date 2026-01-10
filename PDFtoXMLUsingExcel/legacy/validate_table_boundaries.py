#!/usr/bin/env python3
"""
Validate table boundaries by comparing Camelot extraction with actual PDF drawing structures.
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


def get_drawings_in_bbox(page: fitz.Page, bbox: Tuple[float, float, float, float], 
                         tolerance: float = 5.0) -> List[Dict]:
    """Get all drawings (lines, rects) within a bounding box with small tolerance."""
    x1, y1, x2, y2 = bbox
    drawings = page.get_drawings()
    
    filtered_drawings = []
    for drawing in drawings:
        # Check if drawing intersects with bbox (with minimal tolerance)
        draw_rect = drawing.get('rect', None)
        if draw_rect:
            dx1, dy1, dx2, dy2 = draw_rect
            # Only include if drawing overlaps with bbox + small tolerance
            if not (dx2 < x1 - tolerance or dx1 > x2 + tolerance or 
                   dy2 < y1 - tolerance or dy1 > y2 + tolerance):
                filtered_drawings.append(drawing)
    
    return filtered_drawings


def analyze_table_structure(drawings: List[Dict], table_bbox: Tuple[float, float, float, float],
                           debug: bool = False) -> Optional[Tuple[float, float, float, float]]:
    """
    Analyze drawings to find the actual table structure rectangle.
    Only considers lines strictly within or very close to the Camelot-detected table bbox.
    Returns the bounding box of the table structure (lines/rects).
    """
    if not drawings:
        return None
    
    tx1, ty1, tx2, ty2 = table_bbox
    
    # Collect all line segments and rectangles ONLY within the Camelot bbox
    all_points = []
    horizontal_lines = []
    vertical_lines = []
    line_count = 0
    
    # Very small margin to catch lines that might be just at the edge
    margin = 5.0
    
    for drawing in drawings:
        items = drawing.get('items', [])
        for item in items:
            cmd = item[0]
            
            if cmd == 'l':  # line - format is ('l', Point(x1,y1), Point(x2,y2))
                if len(item) >= 3:
                    try:
                        p1, p2 = item[1], item[2]
                        x1, y1 = float(p1.x), float(p1.y)
                        x2, y2 = float(p2.x), float(p2.y)
                        
                        # Check if BOTH endpoints are within the Camelot bbox (with small margin)
                        p1_in = (tx1 - margin <= x1 <= tx2 + margin and 
                                ty1 - margin <= y1 <= ty2 + margin)
                        p2_in = (tx1 - margin <= x2 <= tx2 + margin and 
                                ty1 - margin <= y2 <= ty2 + margin)
                        
                        # Only include line if at least one endpoint is within bbox
                        # (this catches lines that cross the bbox boundary)
                        if p1_in or p2_in:
                            # Clip line endpoints to be within the extended bbox
                            x1_clipped = max(tx1 - margin, min(x1, tx2 + margin))
                            x2_clipped = max(tx1 - margin, min(x2, tx2 + margin))
                            y1_clipped = max(ty1 - margin, min(y1, ty2 + margin))
                            y2_clipped = max(ty1 - margin, min(y2, ty2 + margin))
                            
                            all_points.extend([(x1_clipped, y1_clipped), (x2_clipped, y2_clipped)])
                            line_count += 1
                            
                            # Classify as horizontal or vertical
                            if abs(y2 - y1) < 2:  # horizontal line (tolerance of 2 points)
                                horizontal_lines.append((min(x1_clipped, x2_clipped), 
                                                        max(x1_clipped, x2_clipped), 
                                                        (y1_clipped + y2_clipped) / 2))
                            elif abs(x2 - x1) < 2:  # vertical line
                                vertical_lines.append((min(y1_clipped, y2_clipped), 
                                                      max(y1_clipped, y2_clipped), 
                                                      (x1_clipped + x2_clipped) / 2))
                    except (AttributeError, IndexError, ValueError):
                        pass
                        
            elif cmd == 're':  # rectangle
                if len(item) >= 2:
                    try:
                        rect = item[1]
                        x1, y1, x2, y2 = rect.x0, rect.y0, rect.x1, rect.y1
                        
                        # Check if rectangle overlaps with table bbox
                        if not (x2 < tx1 or x1 > tx2 or y2 < ty1 or y1 > ty2):
                            # Clip rectangle to table bbox
                            x1_clipped = max(tx1, x1)
                            x2_clipped = min(tx2, x2)
                            y1_clipped = max(ty1, y1)
                            y2_clipped = min(ty2, y2)
                            all_points.extend([(x1_clipped, y1_clipped), (x2_clipped, y2_clipped)])
                    except (AttributeError, IndexError, ValueError):
                        pass
    
    if debug:
        print(f"  Debug: Found {line_count} lines within Camelot bbox + {margin}pt margin")
        print(f"  Debug: {len(horizontal_lines)} horizontal, {len(vertical_lines)} vertical")
    
    if not all_points:
        return None
    
    # Find bounding box of all drawing elements
    xs = [p[0] for p in all_points]
    ys = [p[1] for p in all_points]
    
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    
    # If we have horizontal and vertical lines, use them to define the table structure
    if horizontal_lines and vertical_lines:
        # Use the outermost lines
        h_ys = [line[2] for line in horizontal_lines]
        v_xs = [line[2] for line in vertical_lines]
        
        if debug:
            print(f"  Debug: H-line Y positions: {sorted(h_ys)}")
            print(f"  Debug: V-line X positions: {sorted(v_xs)}")
        
        # Get extent of horizontal and vertical lines
        h_min_x = min(line[0] for line in horizontal_lines)
        h_max_x = max(line[1] for line in horizontal_lines)
        v_min_y = min(line[0] for line in vertical_lines)
        v_max_y = max(line[1] for line in vertical_lines)
        
        # Use the grid formed by the outermost lines
        refined_x1 = min(v_xs) if v_xs else h_min_x
        refined_x2 = max(v_xs) if v_xs else h_max_x
        refined_y1 = min(h_ys) if h_ys else v_min_y
        refined_y2 = max(h_ys) if h_ys else v_max_y
        
        return (refined_x1, refined_y1, refined_x2, refined_y2)
    
    return (min_x, min_y, max_x, max_y)


def check_entry_in_bbox(entry: Dict, bbox: Tuple[float, float, float, float]) -> bool:
    """Check if a table entry's bounding box is within the validated bbox."""
    ex1, ey1, ex2, ey2 = entry['x1'], entry['y1'], entry['x2'], entry['y2']
    bx1, by1, bx2, by2 = bbox
    
    # Check if entry center is within bbox
    center_x = (ex1 + ex2) / 2
    center_y = (ey1 + ey2) / 2
    
    return bx1 <= center_x <= bx2 and by1 <= center_y <= by2


def validate_tables_in_pdf(pdf_path: str, tables_by_page: Dict[int, List[Dict]]) -> Dict:
    """
    Validate tables against actual PDF drawing structures.
    """
    doc = fitz.open(pdf_path)
    results = {}
    
    for page_idx, tables in tables_by_page.items():
        if page_idx >= len(doc):
            print(f"Warning: Page {page_idx} not found in PDF")
            continue
            
        page = doc[page_idx]
        page_results = []
        
        for table in tables:
            table_bbox = (table['x1'], table['y1'], table['x2'], table['y2'])
            
            print(f"\n{'='*80}")
            print(f"Page {page_idx}, Table {table['id']}")
            print(f"Camelot bounding box: {table_bbox}")
            print(f"Title: {table['title'][:80]}..." if len(table['title']) > 80 else f"Title: {table['title']}")
            
            # Get drawings in the table bbox
            drawings = get_drawings_in_bbox(page, table_bbox)
            print(f"Found {len(drawings)} drawing elements in table bbox")
            
            if drawings:
                # Analyze to find actual table structure
                structure_bbox = analyze_table_structure(drawings, table_bbox, debug=True)
                
                if structure_bbox:
                    print(f"Actual table structure bbox: {structure_bbox}")
                    
                    # Calculate how much the bboxes differ
                    original_area = (table['x2'] - table['x1']) * (table['y2'] - table['y1'])
                    structure_area = (structure_bbox[2] - structure_bbox[0]) * (structure_bbox[3] - structure_bbox[1])
                    area_ratio = structure_area / original_area if original_area > 0 else 0
                    
                    print(f"Area ratio (structure/original): {area_ratio:.2%}")
                    
                    # Check which entries fall outside the structure bbox
                    total_entries = sum(len(row) for row in table['rows'])
                    entries_outside = 0
                    rows_to_remove = []
                    
                    for row_idx, row in enumerate(table['rows']):
                        entries_in_row_outside = 0
                        for entry in row:
                            if not check_entry_in_bbox(entry, structure_bbox):
                                entries_outside += 1
                                entries_in_row_outside += 1
                        
                        if entries_in_row_outside == len(row):
                            rows_to_remove.append(row_idx)
                    
                    print(f"Entries outside structure: {entries_outside}/{total_entries} ({entries_outside/total_entries*100:.1f}%)")
                    print(f"Rows completely outside: {len(rows_to_remove)}/{len(table['rows'])}")
                    
                    if rows_to_remove:
                        print(f"Rows to remove: {rows_to_remove}")
                        # Show which rows
                        for row_idx in rows_to_remove[:3]:  # Show first 3
                            row = table['rows'][row_idx]
                            row_text = ' | '.join([e['text'][:30] for e in row])
                            print(f"  Row {row_idx}: {row_text}")
                    
                    page_results.append({
                        'table_id': table['id'],
                        'original_bbox': table_bbox,
                        'structure_bbox': structure_bbox,
                        'area_ratio': area_ratio,
                        'entries_outside': entries_outside,
                        'total_entries': total_entries,
                        'rows_to_remove': rows_to_remove,
                        'has_structure': True
                    })
                else:
                    print("Could not determine table structure from drawings")
                    page_results.append({
                        'table_id': table['id'],
                        'original_bbox': table_bbox,
                        'has_structure': False
                    })
            else:
                print("No drawing elements found - table might not have visible borders")
                page_results.append({
                    'table_id': table['id'],
                    'original_bbox': table_bbox,
                    'has_structure': False
                })
        
        results[page_idx] = page_results
    
    doc.close()
    return results


def main():
    # Read the multimedia XML content provided by user
    multimedia_xml = """<?xml version="1.0" encoding="UTF-8"?>
<multimedia>
  <page index="19" width="549.0" height="774.0">
    <media id="p19_img1" type="raster" file="p19_img1.png" x1="190.58099365234375" y1="489.198974609375" x2="313.46600341796875" y2="622.1959838867188" alt="" title="" />
    <media id="p19_img2" type="raster" file="p19_img2.png" x1="334.65301513671875" y1="489.198974609375" x2="457.53802490234375" y2="622.1959838867188" alt="" title="" />
  </page>
  <page index="20" width="549.0" height="774.0">
    <media id="p20_img1" type="raster" file="p20_img1.png" x1="83.89700317382812" y1="626.0780029296875" x2="442.3860168457031" y2="682.89501953125" alt="" title="" />
    <media id="p20_img2" type="raster" file="p20_img2.png" x1="253.49600219726562" y1="221.4099884033203" x2="480.572021484375" y2="312.0539855957031" alt="" title="" />
  </page>
  <page index="21" width="549.0" height="774.0">
    <media id="p21_img1" type="raster" file="p21_img1.png" x1="271.49700927734375" y1="531.0" x2="462.5369873046875" y2="700.02001953125" alt="" title="" />
    <table id="p21_table1" frame="all" x1="147.5421" y1="251.49331665039062" x2="405.67460000000005" y2="349.6256">
      <title>Table 1. Frequencies associated with different types of electromagnetic radiation.</title>
      <tgroup cols="2">
        <tbody>
          <row>
            <entry x1="67.4641" y1="207.80270000000007" x2="254.31985" y2="227.64800000000002">
              <para font-family="TimesNewRomanPSMT" font-size="11.0">of the large number of protons found in the body, primarily in water and fat.</para>
            </entry>
            <entry x1="254.31985" y1="207.80270000000007" x2="433.1945999999998" y2="227.64800000000002">
              <para />
            </entry>
          </row>
          <row>
            <entry x1="67.4641" y1="227.64800000000002" x2="254.31985" y2="252.47540000000004">
              <para font-family="TimesNewRomanPS-BoldMT" font-size="10.0">Table 1.</para>
            </entry>
            <entry x1="254.31985" y1="227.64800000000002" x2="433.1945999999998" y2="252.47540000000004">
              <para font-family="TimesNewRomanPSMT" font-size="10.0">Frequencies associated with different types of electromagnetic radiation.</para>
            </entry>
          </row>
          <row>
            <entry x1="67.4641" y1="252.47540000000004" x2="254.31985" y2="270.09755">
              <para font-family="TimesNewRomanPS-BoldMT" font-size="9.0">Types of Radiation</para>
            </entry>
            <entry x1="254.31985" y1="252.47540000000004" x2="433.1945999999998" y2="270.09755">
              <para font-family="TimesNewRomanPS-BoldMT" font-size="9.0">Approximate Frequency in Hz</para>
            </entry>
          </row>
          <row>
            <entry x1="67.4641" y1="270.09755" x2="254.31985" y2="284.52895">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">Radio Waves</para>
            </entry>
            <entry x1="254.31985" y1="270.09755" x2="433.1945999999998" y2="284.52895">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">10 7</para>
            </entry>
          </row>
          <row>
            <entry x1="67.4641" y1="284.52895" x2="254.31985" y2="298.96405000000004">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">Visible Light</para>
            </entry>
            <entry x1="254.31985" y1="284.52895" x2="433.1945999999998" y2="298.96405000000004">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">10 14</para>
            </entry>
          </row>
          <row>
            <entry x1="67.4641" y1="298.96405000000004" x2="254.31985" y2="313.39914999999996">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">Ultraviolet</para>
            </entry>
            <entry x1="254.31985" y1="298.96405000000004" x2="433.1945999999998" y2="313.39914999999996">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">10 16</para>
            </entry>
          </row>
          <row>
            <entry x1="67.4641" y1="313.39914999999996" x2="254.31985" y2="327.83425">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">X-Rays</para>
            </entry>
            <entry x1="254.31985" y1="313.39914999999996" x2="433.1945999999998" y2="327.83425">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">10 18</para>
            </entry>
          </row>
          <row>
            <entry x1="67.4641" y1="327.83425" x2="254.31985" y2="339.6256">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">Gamma Rays</para>
            </entry>
            <entry x1="254.31985" y1="327.83425" x2="433.1945999999998" y2="339.6256">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">&gt;10 19</para>
            </entry>
          </row>
        </tbody>
      </tgroup>
    </table>
  </page>
  <page index="25" width="549.0" height="774.0">
    <media id="p25_img1" type="raster" file="p25_img1.png" x1="284.0270080566406" y1="484.7989807128906" x2="464.9410095214844" y2="624.333984375" alt="" title="" />
    <table id="p25_table1" frame="all" x1="211.9633" y1="186.30142211914062" x2="323.5887" y2="282.91450000000003">
      <title>Table 2. MZ recovery fractions at different multiples of T1 times.</title>
      <tgroup cols="2">
        <tbody>
          <row>
            <entry x1="67.46530000000001" y1="128.6028" x2="263.15125" y2="155.3441499999999">
              <para font-family="TimesNewRomanPSMT" font-size="11.0">63.2% of the magnetization has recovered alignment with  is considered to occur at a time  ≥  5 T 1  ( Table 2</para>
            </entry>
            <entry x1="263.15125" y1="128.6028" x2="466.2864" y2="155.3441499999999">
              <para font-family="TimesNewRomanPS-BoldMT" font-size="11.0">B Ø . Full recovery of  M Z  to  M Ø ).</para>
            </entry>
          </row>
          <row>
            <entry x1="67.46530000000001" y1="155.3441499999999" x2="263.15125" y2="186.2762499999999">
              <para font-family="TimesNewRomanPS-BoldMT" font-size="10.0">Table 2.  M Z</para>
            </entry>
            <entry x1="263.15125" y1="155.3441499999999" x2="466.2864" y2="186.2762499999999">
              <para font-family="TimesNewRomanPSMT" font-size="10.0">recovery fractions at different multiples of T 1  times.</para>
            </entry>
          </row>
          <row>
            <entry x1="67.46530000000001" y1="186.2762499999999" x2="263.15125" y2="204.46820000000002">
              <para font-family="TimesNewRomanPS-BoldMT" font-size="9.0">t/T 1</para>
            </entry>
            <entry x1="263.15125" y1="186.2762499999999" x2="466.2864" y2="204.46820000000002">
              <para font-family="TimesNewRomanPS-BoldMT" font-size="9.0">M Z</para>
            </entry>
          </row>
          <row>
            <entry x1="67.46530000000001" y1="204.46820000000002" x2="263.15125" y2="217.9679">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">-1</para>
            </entry>
            <entry x1="263.15125" y1="204.46820000000002" x2="466.2864" y2="217.9679">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">0.6321</para>
            </entry>
          </row>
          <row>
            <entry x1="67.46530000000001" y1="217.9679" x2="263.15125" y2="232.38114999999993">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">-2</para>
            </entry>
            <entry x1="263.15125" y1="217.9679" x2="466.2864" y2="232.38114999999993">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">0.8646</para>
            </entry>
          </row>
          <row>
            <entry x1="67.4653" y1="232.38114999999993" x2="263.15125" y2="246.79455000000007">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">-3</para>
            </entry>
            <entry x1="263.15125" y1="232.38114999999993" x2="466.2864" y2="246.79455000000007">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">0.9502</para>
            </entry>
          </row>
          <row>
            <entry x1="67.46530000000001" y1="246.79455000000007" x2="263.15125" y2="261.2079">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">-4</para>
            </entry>
            <entry x1="263.15125" y1="246.79455000000007" x2="466.2864" y2="261.2079">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">0.9816</para>
            </entry>
          </row>
          <row>
            <entry x1="67.46530000000001" y1="261.2079" x2="263.15125" y2="272.91450000000003">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">-5</para>
            </entry>
            <entry x1="263.15125" y1="261.2079" x2="466.2864" y2="272.91450000000003">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">0.9932</para>
            </entry>
          </row>
        </tbody>
      </tgroup>
    </table>
  </page>
  <page index="27" width="549.0" height="774.0">
    <table id="p27_table3" frame="all" x1="212.6631" y1="531.6776123046875" x2="323.6617" y2="628.957">
      <title>Table 3. MXY residual fractions at different multiples of T2 times.</title>
      <tgroup cols="2">
        <tbody>
          <row>
            <entry x1="137.616" y1="514.4696" x2="263.22429999999997" y2="531.9762499999999">
              <para font-family="TimesNewRomanPS-BoldMT" font-size="10.0">Table 3. M XY</para>
            </entry>
            <entry x1="263.22429999999997" y1="514.4696" x2="402.3895" y2="531.9762499999999">
              <para font-family="TimesNewRomanPSMT" font-size="10.0">residual fractions at different multiples of T 2  times.</para>
            </entry>
          </row>
          <row>
            <entry x1="137.616" y1="531.9762499999999" x2="263.22429999999997" y2="550.78075">
              <para font-family="TimesNewRomanPS-BoldMT" font-size="9.0">t/T 2</para>
            </entry>
            <entry x1="263.22429999999997" y1="531.9762499999999" x2="402.3895" y2="550.78075">
              <para font-family="TimesNewRomanPS-BoldMT" font-size="9.0">M xy</para>
            </entry>
          </row>
          <row>
            <entry x1="137.616" y1="550.78075" x2="263.22429999999997" y2="564.4993999999999">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">-0.5</para>
            </entry>
            <entry x1="263.22429999999997" y1="550.78075" x2="402.3895" y2="564.4993999999999">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">0.6065</para>
            </entry>
          </row>
          <row>
            <entry x1="137.616" y1="564.4993999999999" x2="263.22429999999997" y2="578.773">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">-1</para>
            </entry>
            <entry x1="263.22429999999997" y1="564.4993999999999" x2="402.3895" y2="578.773">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">0.3679</para>
            </entry>
          </row>
          <row>
            <entry x1="137.616" y1="578.773" x2="263.22429999999997" y2="593.0466">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">-2</para>
            </entry>
            <entry x1="263.22429999999997" y1="578.773" x2="402.3895" y2="593.0466">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">0.1354</para>
            </entry>
          </row>
          <row>
            <entry x1="137.616" y1="593.0466" x2="263.22429999999997" y2="607.3202">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">-3</para>
            </entry>
            <entry x1="263.22429999999997" y1="593.0466" x2="402.3895" y2="607.3202">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">0.0498</para>
            </entry>
          </row>
          <row>
            <entry x1="137.616" y1="607.3202" x2="263.22429999999997" y2="618.957">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">-4</para>
            </entry>
            <entry x1="263.22429999999997" y1="607.3202" x2="402.3895" y2="618.957">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">0.0184</para>
            </entry>
          </row>
        </tbody>
      </tgroup>
    </table>
  </page>
  <page index="53" width="549.0" height="774.0">
    <table id="p53_table1" frame="all" x1="105.7731" y1="346.3551940917969" x2="430.9372" y2="467.6488">
      <title>Table 1. Magnetic susceptibility and density of common materials.</title>
      <tgroup cols="3">
        <tbody>
          <row>
            <entry x1="67.464" y1="330.35519999999997" x2="184.70555000000002" y2="350.83635">
              <para font-family="TimesNewRomanPS-BoldMT" font-size="11.0">Table 1.</para>
            </entry>
            <entry x1="184.70555000000002" y1="330.35519999999997" x2="332.5358" y2="350.83635">
              <para font-family="TimesNewRomanPSMT" font-size="11.0">Magnetic susceptibility and density of common materials.</para>
            </entry>
            <entry x1="332.5358" y1="330.35519999999997" x2="420.9372" y2="350.83635">
              <para />
            </entry>
          </row>
          <row>
            <entry x1="67.464" y1="350.83635" x2="184.70555000000002" y2="374.8482">
              <para font-family="TimesNewRomanPS-BoldMT" font-size="9.0">Material</para>
            </entry>
            <entry x1="184.70555000000002" y1="350.83635" x2="332.5358" y2="374.8482">
              <para font-family="TimesNewRomanPS-BoldMT" font-size="9.0">Volume Susceptibility</para>
            </entry>
            <entry x1="332.5358" y1="350.83635" x2="420.9372" y2="374.8482">
              <para font-family="TimesNewRomanPS-BoldMT" font-size="9.0">Density (kg/m 3 )</para>
            </entry>
          </row>
          <row>
            <entry x1="67.464" y1="374.8482" x2="184.70555000000002" y2="391.59275">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">Water</para>
            </entry>
            <entry x1="184.70555000000002" y1="374.8482" x2="332.5358" y2="391.59275">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">-9.0 x 10 -6</para>
            </entry>
            <entry x1="332.5358" y1="374.8482" x2="420.9372" y2="391.59275">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">1000</para>
            </entry>
          </row>
          <row>
            <entry x1="67.464" y1="391.59275" x2="184.70555000000002" y2="405.3645">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">PVC</para>
            </entry>
            <entry x1="184.70555000000002" y1="391.59275" x2="332.5358" y2="405.3645">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">-1.1 x 10 -5</para>
            </entry>
            <entry x1="332.5358" y1="391.59275" x2="420.9372" y2="405.3645">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">1400</para>
            </entry>
          </row>
          <row>
            <entry x1="67.464" y1="405.3645" x2="184.70555000000002" y2="419.33275000000003">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">Copper</para>
            </entry>
            <entry x1="184.70555000000002" y1="405.3645" x2="332.5358" y2="419.33275000000003">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">-9.6 x 10 -6</para>
            </entry>
            <entry x1="332.5358" y1="405.3645" x2="420.9372" y2="419.33275000000003">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">9000</para>
            </entry>
          </row>
          <row>
            <entry x1="67.464" y1="419.33275000000003" x2="184.70555000000002" y2="433.17845">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">Titanium</para>
            </entry>
            <entry x1="184.70555000000002" y1="419.33275000000003" x2="332.5358" y2="433.17845">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">1.8 x 10 -4</para>
            </entry>
            <entry x1="332.5358" y1="419.33275000000003" x2="420.9372" y2="433.17845">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">4500</para>
            </entry>
          </row>
          <row>
            <entry x1="67.464" y1="433.17845" x2="184.70555000000002" y2="446.5575">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">Nickel</para>
            </entry>
            <entry x1="184.70555000000002" y1="433.17845" x2="332.5358" y2="446.5575">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">600</para>
            </entry>
            <entry x1="332.5358" y1="433.17845" x2="420.9372" y2="446.5575">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">8900</para>
            </entry>
          </row>
          <row>
            <entry x1="67.464" y1="446.5575" x2="184.70555000000002" y2="457.6488">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">Iron</para>
            </entry>
            <entry x1="184.70555000000002" y1="446.5575" x2="332.5358" y2="457.6488">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">200,000</para>
            </entry>
            <entry x1="332.5358" y1="446.5575" x2="420.9372" y2="457.6488">
              <para font-family="TimesNewRomanPSMT" font-size="9.0">7900</para>
            </entry>
          </row>
        </tbody>
      </tgroup>
    </table>
  </page>
</multimedia>
"""
    
    # Parse the XML
    print("Parsing multimedia.xml...")
    tables_by_page = parse_multimedia_xml(multimedia_xml)
    print(f"Found {sum(len(tables) for tables in tables_by_page.values())} tables across {len(tables_by_page)} pages")
    
    # Ask user for PDF path
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
    else:
        print("\nPlease provide the PDF path as an argument:")
        print("  python validate_table_boundaries.py <pdf_path>")
        print("\nAvailable PDFs in workspace:")
        import os
        for f in os.listdir('/workspace'):
            if f.endswith('.pdf'):
                print(f"  - {f}")
        return
    
    # Validate tables
    print(f"\nValidating tables against PDF: {pdf_path}")
    results = validate_tables_in_pdf(pdf_path, tables_by_page)
    
    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    
    total_tables = 0
    tables_with_structure = 0
    tables_need_trimming = 0
    
    for page_idx, page_results in results.items():
        for result in page_results:
            total_tables += 1
            if result.get('has_structure'):
                tables_with_structure += 1
                if result.get('rows_to_remove'):
                    tables_need_trimming += 1
    
    print(f"Total tables analyzed: {total_tables}")
    print(f"Tables with drawing structure: {tables_with_structure}")
    print(f"Tables that need trimming: {tables_need_trimming}")
    
    if tables_need_trimming > 0:
        print(f"\nConclusion: {tables_need_trimming}/{total_tables} tables have rows outside the actual table structure.")
        print("This approach can help refine Camelot's extraction by filtering out false positives.")


if __name__ == "__main__":
    main()
