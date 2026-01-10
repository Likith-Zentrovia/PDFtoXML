#!/usr/bin/env python3
"""
Debug script to see what drawings PyMuPDF finds on a page.
"""

import fitz
import sys

def debug_page_drawings(pdf_path: str, page_num: int, bbox=None):
    """Show all drawings on a page."""
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    
    print(f"Page {page_num} dimensions: {page.rect}")
    
    drawings = page.get_drawings()
    print(f"\nTotal drawings on page: {len(drawings)}")
    
    if bbox:
        x1, y1, x2, y2 = bbox
        print(f"\nFiltering for bbox: ({x1:.1f}, {y1:.1f}, {x2:.1f}, {y2:.1f})")
    
    for i, drawing in enumerate(drawings):
        draw_rect = drawing.get('rect')
        
        # Check if in bbox
        if bbox:
            dx1, dy1, dx2, dy2 = draw_rect
            if dx2 < x1 or dx1 > x2 or dy2 < y1 or dy1 > y2:
                continue
        
        print(f"\n{'='*60}")
        print(f"Drawing {i}:")
        print(f"  Rect: {draw_rect}")
        print(f"  Color: {drawing.get('color')}")
        print(f"  Fill: {drawing.get('fill')}")
        print(f"  Width: {drawing.get('width')}")
        
        items = drawing.get('items', [])
        print(f"  Items ({len(items)}):")
        
        for j, item in enumerate(items[:20]):  # Show first 20 items
            cmd = item[0]
            print(f"    [{j}] Command: {cmd}", end='')
            
            if cmd == 'l' and len(item) >= 3:
                try:
                    p1, p2 = item[1], item[2]
                    x1, y1 = float(p1.x), float(p1.y)
                    x2, y2 = float(p2.x), float(p2.y)
                    line_type = "horizontal" if abs(y2 - y1) < 2 else "vertical" if abs(x2 - x1) < 2 else "diagonal"
                    print(f" (line: {x1:.1f},{y1:.1f} -> {x2:.1f},{y2:.1f}) [{line_type}]")
                except:
                    print(f" (line - parse error)")
            elif cmd == 're' and len(item) >= 2:
                try:
                    rect = item[1]
                    print(f" (rect: {rect})")
                except:
                    print(f" (rect - parse error)")
            elif cmd == 'c':
                print(f" (curve)")
            elif cmd == 'qu':
                print(f" (quad)")
            else:
                print()
        
        if len(items) > 20:
            print(f"    ... and {len(items) - 20} more items")
    
    doc.close()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python debug_drawings.py <pdf_path> <page_num> [x1 y1 x2 y2]")
        print("\nExample tables from multimedia.xml:")
        print("  Page 21: Table at (147.54, 251.49, 405.67, 349.63)")
        print("  Page 25: Table at (211.96, 186.30, 323.59, 282.91)")
        print("  Page 27: Table at (212.66, 531.68, 323.66, 628.96)")
        print("  Page 53: Table at (105.77, 346.36, 430.94, 467.65)")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    page_num = int(sys.argv[2])
    
    bbox = None
    if len(sys.argv) >= 7:
        bbox = tuple(map(float, sys.argv[3:7]))
    
    debug_page_drawings(pdf_path, page_num, bbox)
