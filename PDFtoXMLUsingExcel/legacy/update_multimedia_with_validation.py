#!/usr/bin/env python3
"""
Update multimedia.xml by removing table entries outside validated drawing boundaries.

This script:
1. Reads an existing multimedia.xml file
2. For each table, finds the actual drawn structure using get_drawings()
3. Removes rows/entries that fall outside the validated rect
4. Writes the updated multimedia.xml
"""

import fitz  # PyMuPDF
import xml.etree.ElementTree as ET
from typing import List, Tuple, Optional
import sys
import os


def extract_table_lines(page: fitz.Page, bbox: Tuple[float, float, float, float], 
                        margin: float = 5.0) -> Tuple[List[Tuple], List[Tuple]]:
    """
    Extract horizontal and vertical lines within a bbox that could be table borders.
    Returns (horizontal_lines, vertical_lines).
    """
    x1, y1, x2, y2 = bbox
    drawings = page.get_drawings()
    
    horizontal_lines = []
    vertical_lines = []
    
    for drawing in drawings:
        # Skip background fills
        if drawing.get('fill') is not None:
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
                              min_lines: int = 2) -> Optional[Tuple[float, float, float, float]]:
    """
    Determine the actual table structure bbox from grid lines.
    Returns (x1, y1, x2, y2) or None if insufficient lines.
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


def is_entry_in_bbox(entry_elem: ET.Element, bbox: Tuple[float, float, float, float]) -> bool:
    """Check if a table entry's center is within the bbox."""
    ex1 = float(entry_elem.get('x1', 0))
    ey1 = float(entry_elem.get('y1', 0))
    ex2 = float(entry_elem.get('x2', 0))
    ey2 = float(entry_elem.get('y2', 0))
    
    # Check center point
    center_x = (ex1 + ex2) / 2
    center_y = (ey1 + ey2) / 2
    
    bx1, by1, bx2, by2 = bbox
    return bx1 <= center_x <= bx2 and by1 <= center_y <= by2


def update_multimedia_xml(multimedia_xml_path: str, pdf_path: str, 
                         output_path: Optional[str] = None,
                         min_lines: int = 2,
                         margin: float = 5.0,
                         create_backup: bool = True,
                         create_intermediate: bool = True) -> dict:
    """
    Update multimedia.xml by removing table content outside validated boundaries.
    
    Args:
        multimedia_xml_path: Path to input multimedia.xml
        pdf_path: Path to corresponding PDF
        output_path: Path for updated XML (default: adds '_validated' suffix)
        min_lines: Minimum lines required for valid table structure
        margin: Margin for line detection (pts)
        create_backup: If True, creates a backup of the original multimedia.xml
        create_intermediate: If True, creates an intermediate XML after validation
    
    Returns:
        Dictionary with statistics including backup and intermediate file paths
    """
    if not os.path.exists(multimedia_xml_path):
        raise FileNotFoundError(f"multimedia.xml not found: {multimedia_xml_path}")
    
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    
    # Create backup of original multimedia.xml
    backup_path = None
    if create_backup:
        import shutil
        from datetime import datetime
        base, ext = os.path.splitext(multimedia_xml_path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{base}_backup_{timestamp}{ext}"
        shutil.copy2(multimedia_xml_path, backup_path)
        print(f"\n{'='*80}")
        print(f"✓ BACKUP CREATED: {backup_path}")
        print(f"{'='*80}")
    
    # Default output path
    if output_path is None:
        base, ext = os.path.splitext(multimedia_xml_path)
        output_path = f"{base}_validated{ext}"
    
    # Intermediate path for table validation results
    intermediate_path = None
    if create_intermediate:
        base, ext = os.path.splitext(multimedia_xml_path)
        intermediate_path = f"{base}_intermediate_after_validation{ext}"
    
    # Parse XML
    tree = ET.parse(multimedia_xml_path)
    root = tree.getroot()
    
    # Open PDF
    doc = fitz.open(pdf_path)
    
    # Statistics
    stats = {
        'total_tables': 0,
        'tables_with_structure': 0,
        'tables_text_only': 0,
        'total_rows_before': 0,
        'total_rows_after': 0,
        'total_entries_before': 0,
        'total_entries_after': 0,
        'tables_modified': []
    }
    
    # Process each table
    for page_elem in root.findall('.//page'):
        page_idx = int(page_elem.get('index'))
        
        if page_idx >= len(doc):
            print(f"Warning: Page {page_idx} not found in PDF")
            continue
        
        page = doc[page_idx]
        
        for table_elem in page_elem.findall('.//table'):
            stats['total_tables'] += 1
            table_id = table_elem.get('id', 'unknown')
            
            # Get table bbox from XML
            tx1 = float(table_elem.get('x1'))
            ty1 = float(table_elem.get('y1'))
            tx2 = float(table_elem.get('x2'))
            ty2 = float(table_elem.get('y2'))
            table_bbox = (tx1, ty1, tx2, ty2)
            
            print(f"\nProcessing {table_id} on page {page_idx}")
            print(f"  Original bbox: ({tx1:.1f}, {ty1:.1f}, {tx2:.1f}, {ty2:.1f})")
            
            # Extract lines and find structure
            h_lines, v_lines = extract_table_lines(page, table_bbox, margin=margin)
            print(f"  Found {len(h_lines)} horizontal lines, {len(v_lines)} vertical lines")
            
            structure_bbox = find_table_structure_bbox(h_lines, v_lines, min_lines=min_lines)
            
            if structure_bbox:
                stats['tables_with_structure'] += 1
                print(f"  ✓ Has drawn structure: ({structure_bbox[0]:.1f}, {structure_bbox[1]:.1f}, "
                      f"{structure_bbox[2]:.1f}, {structure_bbox[3]:.1f})")
                
                # Add validation attributes to table element
                table_elem.set('validation_status', 'has_structure')
                table_elem.set('validated_x1', f"{structure_bbox[0]:.4f}")
                table_elem.set('validated_y1', f"{structure_bbox[1]:.4f}")
                table_elem.set('validated_x2', f"{structure_bbox[2]:.4f}")
                table_elem.set('validated_y2', f"{structure_bbox[3]:.4f}")
                
                # Filter rows based on structure
                rows_before = 0
                rows_after = 0
                entries_before = 0
                entries_after = 0
                
                tgroup = table_elem.find('.//tgroup')
                if tgroup is not None:
                    tbody = tgroup.find('.//tbody')
                    if tbody is not None:
                        rows_to_remove = []
                        
                        for row_elem in tbody.findall('.//row'):
                            rows_before += 1
                            entries_in_row_before = len(row_elem.findall('.//entry'))
                            entries_before += entries_in_row_before
                            
                            # Check if any entry in this row is within structure
                            entries_in_structure = []
                            for entry_elem in row_elem.findall('.//entry'):
                                if is_entry_in_bbox(entry_elem, structure_bbox):
                                    entries_in_structure.append(entry_elem)
                            
                            # If NO entries are within structure, mark row for removal
                            if not entries_in_structure:
                                rows_to_remove.append(row_elem)
                            else:
                                rows_after += 1
                                entries_after += len(entries_in_structure)
                        
                        # Remove rows outside structure
                        for row_elem in rows_to_remove:
                            tbody.remove(row_elem)
                        
                        if rows_to_remove:
                            print(f"  ⚠ Removed {len(rows_to_remove)}/{rows_before} rows "
                                  f"({len(rows_to_remove)/rows_before*100:.1f}%) outside structure")
                            stats['tables_modified'].append({
                                'id': table_id,
                                'page': page_idx,
                                'rows_removed': len(rows_to_remove),
                                'rows_kept': rows_after
                            })
                        else:
                            print(f"  ✓ All {rows_before} rows within structure")
                
                stats['total_rows_before'] += rows_before
                stats['total_rows_after'] += rows_after
                stats['total_entries_before'] += entries_before
                stats['total_entries_after'] += entries_after
                
            else:
                stats['tables_text_only'] += 1
                print(f"  ℹ Text-only table (no drawn structure) - keeping as-is")
                table_elem.set('validation_status', 'text_only')
                
                # Count rows/entries for stats
                tgroup = table_elem.find('.//tgroup')
                if tgroup is not None:
                    tbody = tgroup.find('.//tbody')
                    if tbody is not None:
                        rows = tbody.findall('.//row')
                        entries = tbody.findall('.//entry')
                        stats['total_rows_before'] += len(rows)
                        stats['total_rows_after'] += len(rows)
                        stats['total_entries_before'] += len(entries)
                        stats['total_entries_after'] += len(entries)
    
    doc.close()
    
    # Write intermediate XML (if requested)
    if create_intermediate and intermediate_path:
        tree.write(intermediate_path, encoding='UTF-8', xml_declaration=True)
        print(f"\n{'='*80}")
        print(f"✓ INTERMEDIATE XML CREATED: {intermediate_path}")
        print(f"  (Contains validated table structure with attributes)")
        print(f"{'='*80}")
        stats['intermediate_path'] = intermediate_path
    
    # Write final validated XML
    tree.write(output_path, encoding='UTF-8', xml_declaration=True)
    print(f"\n{'='*80}")
    print(f"✓ FINAL VALIDATED XML: {output_path}")
    print(f"{'='*80}")
    
    # Add file paths to stats
    stats['backup_path'] = backup_path
    stats['output_path'] = output_path
    
    return stats


def print_statistics(stats: dict):
    """Print summary statistics."""
    print(f"\n{'='*80}")
    print("VALIDATION SUMMARY")
    print(f"{'='*80}")
    print(f"Total tables processed: {stats['total_tables']}")
    print(f"  Tables with drawn structure: {stats['tables_with_structure']}")
    print(f"  Text-only tables: {stats['tables_text_only']}")
    print(f"\nRows:")
    print(f"  Before validation: {stats['total_rows_before']}")
    print(f"  After validation: {stats['total_rows_after']}")
    if stats['total_rows_before'] > 0:
        print(f"  Removed: {stats['total_rows_before'] - stats['total_rows_after']} "
              f"({(stats['total_rows_before'] - stats['total_rows_after'])/stats['total_rows_before']*100:.1f}%)")
    print(f"\nEntries:")
    print(f"  Before validation: {stats['total_entries_before']}")
    print(f"  After validation: {stats['total_entries_after']}")
    if stats['total_entries_before'] > 0:
        print(f"  Removed: {stats['total_entries_before'] - stats['total_entries_after']} "
              f"({(stats['total_entries_before'] - stats['total_entries_after'])/stats['total_entries_before']*100:.1f}%)")
    
    if stats['tables_modified']:
        print(f"\n{'='*80}")
        print(f"MODIFIED TABLES ({len(stats['tables_modified'])} total)")
        print(f"{'='*80}")
        for mod in stats['tables_modified']:
            print(f"  {mod['id']} (page {mod['page']}): "
                  f"Removed {mod['rows_removed']} rows, kept {mod['rows_kept']} rows")
    
    # Print file paths
    print(f"\n{'='*80}")
    print("OUTPUT FILES")
    print(f"{'='*80}")
    if stats.get('backup_path'):
        print(f"  Backup:       {stats['backup_path']}")
    if stats.get('intermediate_path'):
        print(f"  Intermediate: {stats['intermediate_path']}")
    if stats.get('output_path'):
        print(f"  Final output: {stats['output_path']}")
    
    print(f"\n{'='*80}")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ['--help', '-h', 'help']:
        print("="*80)
        print("UPDATE MULTIMEDIA.XML WITH DRAWING VALIDATION")
        print("="*80)
        print("\nThis script updates an existing multimedia.xml by:")
        print("  1. Creating a BACKUP of the original multimedia.xml (with timestamp)")
        print("  2. Using get_drawings() to find actual table structure rect")
        print("  3. Removing rows/entries that fall outside this rect")
        print("  4. Creating an INTERMEDIATE XML after validation (with metadata)")
        print("  5. Writing the final validated XML with cleaned table content")
        print("\nUsage:")
        print("  python3 update_multimedia_with_validation.py <multimedia.xml> <pdf_path> [output.xml]")
        print("\nExamples:")
        print("  # Basic usage (creates backup + intermediate + validated XMLs)")
        print("  python3 update_multimedia_with_validation.py output_MultiMedia.xml document.pdf")
        print("\n  # Specify custom output path")
        print("  python3 update_multimedia_with_validation.py input.xml doc.pdf cleaned.xml")
        print("\nArguments:")
        print("  multimedia.xml  Input multimedia.xml file to validate")
        print("  pdf_path        Corresponding PDF file")
        print("  output.xml      Optional output path (default: adds '_validated' suffix)")
        print("\nWhat it does:")
        print("  - For tables WITH drawn borders: Removes rows outside structure")
        print("  - For text-only tables: Keeps as-is (can't validate without structure)")
        print("  - Adds validation_status metadata to each table")
        print("\nOutput Files Created:")
        print("  1. <name>_backup_YYYYMMDD_HHMMSS.xml  - Original file backup")
        print("  2. <name>_intermediate_after_validation.xml - After validation (with metadata)")
        print("  3. <name>_validated.xml (or custom)   - Final cleaned output")
        print("\nValidation Metadata Added:")
        print("    • validation_status='has_structure' or 'text_only'")
        print("    • validated_x1/y1/x2/y2 attributes (if has structure)")
        print("    • Rows outside get_drawings() rect REMOVED")
        print("\nVerify it worked:")
        print("  diff <name>_backup_*.xml <name>_validated.xml")
        print("  grep -c '<row>' <name>_backup_*.xml <name>_validated.xml")
        print("\n" + "="*80)
        print("Documentation: See START_HERE_MULTIMEDIA_UPDATE.md")
        print("="*80)
        sys.exit(0)
    
    if len(sys.argv) < 3:
        print("Error: Missing required arguments")
        print("Usage: python3 update_multimedia_with_validation.py <multimedia.xml> <pdf_path> [output.xml]")
        print("Run with --help for more information")
        sys.exit(1)
    
    multimedia_xml_path = sys.argv[1]
    pdf_path = sys.argv[2]
    output_path = sys.argv[3] if len(sys.argv) > 3 else None
    
    print(f"\n{'='*80}")
    print("STARTING TABLE VALIDATION PIPELINE")
    print(f"{'='*80}")
    print(f"Input multimedia.xml: {multimedia_xml_path}")
    print(f"PDF file: {pdf_path}")
    print(f"Output will be: {output_path or 'auto-generated'}")
    print(f"\nThis will create:")
    print(f"  1. Backup of original multimedia.xml")
    print(f"  2. Intermediate XML after validation")
    print(f"  3. Final validated XML")
    print(f"{'='*80}")
    
    stats = update_multimedia_xml(
        multimedia_xml_path=multimedia_xml_path,
        pdf_path=pdf_path,
        output_path=output_path,
        min_lines=2,
        margin=5.0,
        create_backup=True,
        create_intermediate=True
    )
    
    print_statistics(stats)
    
    print(f"\n{'='*80}")
    print("✓ TABLE VALIDATION COMPLETE")
    print(f"{'='*80}")
    print("\nNext steps:")
    print("  1. Review the intermediate XML to see validation metadata")
    print("  2. Compare backup vs. validated XML to see changes")
    print("  3. Use the validated XML in your pipeline")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
