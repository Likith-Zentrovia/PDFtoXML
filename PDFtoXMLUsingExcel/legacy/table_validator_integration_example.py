#!/usr/bin/env python3
"""
Example: How to integrate table structure validation into your PDF processing pipeline.

This shows how to use the validation approach to improve Camelot table extraction accuracy.
"""

import fitz  # PyMuPDF
from typing import List, Tuple, Dict, Optional


class TableStructureValidator:
    """Validates Camelot table extraction against actual PDF drawing structures."""
    
    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.doc = fitz.open(pdf_path)
    
    def __del__(self):
        if hasattr(self, 'doc'):
            self.doc.close()
    
    def validate_table(self, page_num: int, table_bbox: Tuple[float, float, float, float],
                      table_rows: List[Dict], min_lines: int = 2) -> Dict:
        """
        Validate a single table against its drawn structure.
        
        Args:
            page_num: PDF page number (0-indexed)
            table_bbox: Camelot detected bounding box (x1, y1, x2, y2)
            table_rows: List of row dicts with 'entries' containing cell info
            min_lines: Minimum lines required to consider it a drawn table
            
        Returns:
            Dict with validation results and recommendations
        """
        page = self.doc[page_num]
        
        # Extract lines within table bbox
        h_lines, v_lines = self._extract_table_lines(page, table_bbox)
        
        # Check if we have a real table structure
        if len(h_lines) >= min_lines and len(v_lines) >= min_lines:
            # Calculate actual structure bbox from grid lines
            structure_bbox = self._calculate_structure_bbox(h_lines, v_lines)
            
            # Validate rows against structure
            rows_inside, rows_outside = self._validate_rows(table_rows, structure_bbox)
            
            return {
                'status': 'validated',
                'has_structure': True,
                'structure_bbox': structure_bbox,
                'camelot_bbox': table_bbox,
                'rows_inside': rows_inside,
                'rows_outside': rows_outside,
                'recommendation': 'trim' if rows_outside else 'accept',
                'confidence': len(rows_inside) / (len(rows_inside) + len(rows_outside)) if rows_inside or rows_outside else 1.0
            }
        else:
            return {
                'status': 'text_only',
                'has_structure': False,
                'camelot_bbox': table_bbox,
                'lines_found': {'horizontal': len(h_lines), 'vertical': len(v_lines)},
                'recommendation': 'accept',  # Trust Camelot for text-only tables
                'confidence': 0.8  # Lower confidence without structural validation
            }
    
    def _extract_table_lines(self, page: fitz.Page, bbox: Tuple[float, float, float, float],
                            margin: float = 5.0) -> Tuple[List[Tuple], List[Tuple]]:
        """Extract horizontal and vertical lines within table bbox."""
        x1, y1, x2, y2 = bbox
        drawings = page.get_drawings()
        
        h_lines, v_lines = [], []
        
        for drawing in drawings:
            # Skip background fills
            if drawing.get('fill') is not None:
                continue
            
            # Check drawing is near table
            draw_rect = drawing.get('rect')
            if not draw_rect:
                continue
            
            dx1, dy1, dx2, dy2 = draw_rect
            if dx2 < x1 - margin or dx1 > x2 + margin or dy2 < y1 - margin or dy1 > y2 + margin:
                continue
            
            # Extract line items
            for item in drawing.get('items', []):
                if item[0] == 'l' and len(item) >= 3:
                    try:
                        p1, p2 = item[1], item[2]
                        lx1, ly1 = float(p1.x), float(p1.y)
                        lx2, ly2 = float(p2.x), float(p2.y)
                        
                        # Check if line is in bbox
                        if not ((x1 - margin <= lx1 <= x2 + margin and y1 - margin <= ly1 <= y2 + margin) or
                               (x1 - margin <= lx2 <= x2 + margin and y1 - margin <= ly2 <= y2 + margin)):
                            continue
                        
                        # Classify line
                        if abs(ly2 - ly1) < 2:  # horizontal
                            h_lines.append((min(lx1, lx2), max(lx1, lx2), (ly1 + ly2) / 2))
                        elif abs(lx2 - lx1) < 2:  # vertical
                            v_lines.append((min(ly1, ly2), max(ly1, ly2), (lx1 + lx2) / 2))
                    except (AttributeError, IndexError, ValueError):
                        pass
        
        return h_lines, v_lines
    
    def _calculate_structure_bbox(self, h_lines: List[Tuple], v_lines: List[Tuple]) -> Tuple[float, float, float, float]:
        """Calculate bounding box from grid lines."""
        v_xs = [line[2] for line in v_lines]
        h_ys = [line[2] for line in h_lines]
        
        return (min(v_xs), min(h_ys), max(v_xs), max(h_ys))
    
    def _validate_rows(self, table_rows: List[Dict], structure_bbox: Tuple[float, float, float, float]) -> Tuple[List[int], List[int]]:
        """Check which rows are inside vs outside the structure bbox."""
        bx1, by1, bx2, by2 = structure_bbox
        rows_inside, rows_outside = [], []
        
        for row_idx, row in enumerate(table_rows):
            # Check if any cell in row is within bbox
            row_in_bbox = False
            for entry in row.get('entries', []):
                center_x = (entry['x1'] + entry['x2']) / 2
                center_y = (entry['y1'] + entry['y2']) / 2
                if bx1 <= center_x <= bx2 and by1 <= center_y <= by2:
                    row_in_bbox = True
                    break
            
            if row_in_bbox:
                rows_inside.append(row_idx)
            else:
                rows_outside.append(row_idx)
        
        return rows_inside, rows_outside


# ============================================================================
# Integration Examples
# ============================================================================

def example_1_validate_single_table():
    """Example 1: Validate a single Camelot-extracted table."""
    
    # Simulate Camelot extraction
    camelot_table = {
        'bbox': (147.54, 251.49, 405.67, 349.63),
        'rows': [
            {'entries': [
                {'x1': 67.46, 'y1': 207.80, 'x2': 254.32, 'y2': 227.65, 'text': 'Header 1'},
                {'x1': 254.32, 'y1': 207.80, 'x2': 433.19, 'y2': 227.65, 'text': 'Header 2'},
            ]},
            # ... more rows
        ]
    }
    
    # Validate
    validator = TableStructureValidator('9780803694958-1-100.pdf')
    result = validator.validate_table(
        page_num=21,
        table_bbox=camelot_table['bbox'],
        table_rows=camelot_table['rows']
    )
    
    # Act on result
    if result['recommendation'] == 'trim':
        print(f"⚠ Table has {len(result['rows_outside'])} rows outside structure")
        print(f"   Remove rows: {result['rows_outside']}")
        
        # Filter table
        filtered_rows = [row for i, row in enumerate(camelot_table['rows']) 
                        if i in result['rows_inside']]
        print(f"   Kept {len(filtered_rows)}/{len(camelot_table['rows'])} rows")
    else:
        print(f"✓ Table validated successfully (confidence: {result['confidence']:.2%})")


def example_2_batch_validation():
    """Example 2: Validate all tables from multimedia.xml."""
    
    import xml.etree.ElementTree as ET
    
    # Load multimedia.xml (your existing output)
    tree = ET.parse('multimedia.xml')
    root = tree.getroot()
    
    validator = TableStructureValidator('document.pdf')
    validation_report = []
    
    for page_elem in root.findall('.//page'):
        page_num = int(page_elem.get('index'))
        
        for table_elem in page_elem.findall('.//table'):
            table_bbox = (
                float(table_elem.get('x1')),
                float(table_elem.get('y1')),
                float(table_elem.get('x2')),
                float(table_elem.get('y2'))
            )
            
            # Convert XML rows to dict format
            rows = []
            for row_elem in table_elem.findall('.//row'):
                entries = []
                for entry_elem in row_elem.findall('.//entry'):
                    entries.append({
                        'x1': float(entry_elem.get('x1')),
                        'y1': float(entry_elem.get('y1')),
                        'x2': float(entry_elem.get('x2')),
                        'y2': float(entry_elem.get('y2')),
                        'text': ''.join(entry_elem.itertext()).strip()
                    })
                rows.append({'entries': entries})
            
            # Validate
            result = validator.validate_table(page_num, table_bbox, rows)
            
            validation_report.append({
                'page': page_num,
                'table_id': table_elem.get('id'),
                'result': result
            })
    
    # Generate report
    print("\nVALIDATION REPORT")
    print("=" * 80)
    
    for report in validation_report:
        status_icon = "✓" if report['result']['recommendation'] == 'accept' else "⚠"
        print(f"{status_icon} Page {report['page']}, {report['table_id']}")
        print(f"   Status: {report['result']['status']}")
        print(f"   Confidence: {report['result']['confidence']:.1%}")
        
        if report['result']['recommendation'] == 'trim':
            print(f"   Action: Remove {len(report['result']['rows_outside'])} rows")


def example_3_integrate_with_pipeline():
    """Example 3: Full integration with PDF processing pipeline."""
    
    def process_pdf_with_validation(pdf_path: str, output_xml_path: str):
        """Process PDF with table structure validation."""
        
        import camelot
        from xml.etree.ElementTree import Element, SubElement, ElementTree
        
        validator = TableStructureValidator(pdf_path)
        
        # Root element
        root = Element('document')
        
        # Extract tables with Camelot (example for first 10 pages)
        tables = camelot.read_pdf(pdf_path, pages='1-10', flavor='lattice')
        
        for table in tables:
            page_num = table.page - 1  # Camelot uses 1-indexed
            
            # Get table data
            table_bbox = (table._bbox[0], table._bbox[1], table._bbox[2], table._bbox[3])
            table_rows = []
            
            for row_data in table.data:
                entries = []
                for cell_data in row_data:
                    # Simplified - you'd need actual cell coordinates
                    entries.append({
                        'x1': 0, 'y1': 0, 'x2': 0, 'y2': 0,  # Get from Camelot
                        'text': cell_data
                    })
                table_rows.append({'entries': entries})
            
            # VALIDATE TABLE
            validation = validator.validate_table(page_num, table_bbox, table_rows)
            
            # Create XML element
            page_elem = SubElement(root, 'page', index=str(page_num))
            table_elem = SubElement(page_elem, 'table',
                                   id=f'p{page_num}_table{table.page}',
                                   x1=str(table_bbox[0]),
                                   y1=str(table_bbox[1]),
                                   x2=str(table_bbox[2]),
                                   y2=str(table_bbox[3]))
            
            # Add validation metadata
            validation_elem = SubElement(table_elem, 'validation')
            validation_elem.set('status', validation['status'])
            validation_elem.set('confidence', f"{validation['confidence']:.2f}")
            validation_elem.set('has_structure', str(validation['has_structure']))
            
            # Filter rows based on validation
            if validation['recommendation'] == 'trim':
                rows_to_include = validation['rows_inside']
            else:
                rows_to_include = list(range(len(table_rows)))
            
            # Add only validated rows
            tbody = SubElement(table_elem, 'tbody')
            for row_idx in rows_to_include:
                row_elem = SubElement(tbody, 'row')
                for entry in table_rows[row_idx]['entries']:
                    entry_elem = SubElement(row_elem, 'entry')
                    entry_elem.text = entry['text']
        
        # Save
        tree = ElementTree(root)
        tree.write(output_xml_path, encoding='utf-8', xml_declaration=True)
        print(f"✓ Saved validated tables to {output_xml_path}")


def example_4_quality_metrics():
    """Example 4: Generate quality metrics for table extraction."""
    
    def generate_quality_report(pdf_path: str, tables: List[Dict]) -> Dict:
        """Generate quality metrics including structure validation."""
        
        validator = TableStructureValidator(pdf_path)
        
        metrics = {
            'total_tables': len(tables),
            'tables_with_structure': 0,
            'tables_text_only': 0,
            'tables_trimmed': 0,
            'total_rows_removed': 0,
            'avg_confidence': 0.0
        }
        
        confidences = []
        
        for table in tables:
            result = validator.validate_table(
                table['page_num'],
                table['bbox'],
                table['rows']
            )
            
            if result['has_structure']:
                metrics['tables_with_structure'] += 1
                if result['recommendation'] == 'trim':
                    metrics['tables_trimmed'] += 1
                    metrics['total_rows_removed'] += len(result['rows_outside'])
            else:
                metrics['tables_text_only'] += 1
            
            confidences.append(result['confidence'])
        
        metrics['avg_confidence'] = sum(confidences) / len(confidences) if confidences else 0.0
        
        # Print report
        print("\nQUALITY REPORT")
        print("=" * 60)
        print(f"Total tables: {metrics['total_tables']}")
        print(f"Tables with drawn structure: {metrics['tables_with_structure']}")
        print(f"Text-only tables: {metrics['tables_text_only']}")
        print(f"Tables requiring trimming: {metrics['tables_trimmed']}")
        print(f"Total rows removed: {metrics['total_rows_removed']}")
        print(f"Average confidence: {metrics['avg_confidence']:.1%}")
        
        return metrics


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    print("Table Structure Validation - Integration Examples")
    print("=" * 80)
    print()
    print("This file demonstrates how to integrate table structure validation")
    print("into your PDF processing pipeline.")
    print()
    print("Available examples:")
    print("  1. example_1_validate_single_table() - Validate one table")
    print("  2. example_2_batch_validation() - Validate from multimedia.xml")
    print("  3. example_3_integrate_with_pipeline() - Full pipeline integration")
    print("  4. example_4_quality_metrics() - Generate quality reports")
    print()
    print("Uncomment the example you want to run below:")
    print()
    
    # Uncomment to run examples:
    # example_1_validate_single_table()
    # example_2_batch_validation()
    # example_3_integrate_with_pipeline()
    # example_4_quality_metrics()
