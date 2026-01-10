# Table Structure Validation Toolkit

## Overview

This toolkit validates Camelot table extraction accuracy by cross-referencing with actual PDF drawing structures (borders, grid lines) using PyMuPDF's `page.get_drawings()`.

## The Problem

Camelot sometimes extracts content that looks like a table but includes extra rows that are actually:
- Table captions/titles above the table
- Notes/sources below the table  
- Other nearby text that happens to align

When a table has **drawn borders** in the PDF, we can use those borders to validate and filter the extraction.

## The Solution

1. **Extract table bbox** from Camelot/multimedia.xml
2. **Find drawn structures** using PyMuPDF in that bbox
3. **Calculate precise table boundary** from grid lines
4. **Filter out rows** that fall outside the actual drawn structure

## Quick Start

### Installation

```bash
pip install PyMuPDF
```

### Basic Usage

```bash
# Validate all tables in a PDF
python table_structure_validator.py your_document.pdf

# Debug what drawings exist in a specific area
python debug_drawings.py your_document.pdf 21 100 200 400 500
```

### Expected Output

```
================================================================================
Page 25, Table p25_table1
Camelot bbox: (212.0, 186.3, 323.6, 282.9)
Title: Table 2. MZ recovery fractions...
Found 8 horizontal lines, 4 vertical lines
✓ Table HAS drawn structure!
  Structure bbox: (215.5, 190.2, 320.1, 278.5)
  Rows inside structure: 12/15
  Rows outside structure: 3/15
  ⚠ RECOMMENDATION: Remove 3 rows (20.0% reduction)
    Row 0: Note: This table shows...
    Row 13: Source: Internal data
    Row 14: *p < 0.05

================================================================================
VALIDATION SUMMARY
================================================================================
Total tables analyzed: 4
Tables with drawn structure: 2
Tables needing trimming: 1
Text-only tables (no borders): 2
```

## Files in This Toolkit

| File | Purpose | Use When |
|------|---------|----------|
| **table_structure_validator.py** | Main validation tool | You want to validate tables |
| **debug_drawings.py** | Diagnostic tool | You need to see what drawings exist |
| **validate_table_boundaries.py** | Original research script | You want detailed debugging |
| **table_validator_integration_example.py** | Integration examples | You want to add to your pipeline |
| **TABLE_VALIDATION_EXPERIMENT.md** | Full technical documentation | You want complete details |
| **EXPERIMENT_RESULTS_SUMMARY.md** | Quick reference guide | You want the highlights |

## API Usage

### Validate a Single Table

```python
from table_validator_integration_example import TableStructureValidator

validator = TableStructureValidator('document.pdf')

result = validator.validate_table(
    page_num=25,
    table_bbox=(212.0, 186.3, 323.6, 282.9),
    table_rows=[
        {'entries': [
            {'x1': 67.5, 'y1': 186.3, 'x2': 263.2, 'y2': 204.5, 'text': 't/T1'},
            {'x1': 263.2, 'y1': 186.3, 'x2': 466.3, 'y2': 204.5, 'text': 'MZ'}
        ]},
        # ... more rows
    ]
)

if result['recommendation'] == 'trim':
    print(f"Remove rows: {result['rows_outside']}")
    filtered_rows = [row for i, row in enumerate(table_rows) 
                     if i in result['rows_inside']]
```

### Batch Validation

```python
import xml.etree.ElementTree as ET

tree = ET.parse('multimedia.xml')
root = tree.getroot()

validator = TableStructureValidator('document.pdf')

for page_elem in root.findall('.//page'):
    for table_elem in page_elem.findall('.//table'):
        table_bbox = (
            float(table_elem.get('x1')),
            float(table_elem.get('y1')),
            float(table_elem.get('x2')),
            float(table_elem.get('y2'))
        )
        
        # Convert XML rows to list
        rows = parse_table_rows(table_elem)
        
        result = validator.validate_table(
            int(page_elem.get('index')),
            table_bbox,
            rows
        )
        
        # Add validation metadata to XML
        table_elem.set('validation_status', result['status'])
        table_elem.set('confidence', f"{result['confidence']:.2f}")
```

## How It Works

### 1. Line Extraction

```python
# Get all drawings on page
drawings = page.get_drawings()

# Filter to table area (with small margin)
for drawing in drawings:
    # Skip background fills
    if drawing.get('fill') is not None:
        continue
    
    # Extract lines
    for item in drawing.get('items', []):
        if item[0] == 'l':  # line command
            classify_as_horizontal_or_vertical(item)
```

### 2. Grid Detection

```python
# Need minimum 2x2 grid for a real table
if len(horizontal_lines) >= 2 and len(vertical_lines) >= 2:
    # Calculate boundary from outermost lines
    structure_bbox = (
        min(v_line.x for v_line in vertical_lines),
        min(h_line.y for h_line in horizontal_lines),
        max(v_line.x for v_line in vertical_lines),
        max(h_line.y for h_line in horizontal_lines)
    )
```

### 3. Row Validation

```python
for row_idx, row in enumerate(table_rows):
    row_center_y = (row.y1 + row.y2) / 2
    
    if structure_y1 <= row_center_y <= structure_y2:
        rows_inside.append(row_idx)
    else:
        rows_outside.append(row_idx)  # REMOVE THIS ROW
```

## Understanding Results

### Result Status Types

1. **`validated` (has_structure=True)**
   - Table has drawn borders
   - Structure bbox calculated from grid lines
   - High confidence validation

2. **`text_only` (has_structure=False)**
   - No drawn borders found
   - Text-only table
   - Lower confidence (trust Camelot)

### Recommendation Types

1. **`accept`** - All rows are valid, keep as-is
2. **`trim`** - Some rows fall outside structure, remove them

### Confidence Scores

- **0.9-1.0**: Excellent - all/most rows validated
- **0.7-0.9**: Good - minor trimming needed
- **0.5-0.7**: Fair - significant trimming needed
- **0.8** (default): Text-only table (no structural validation)

## Real-World Applications

### Medical/Scientific PDFs

```python
# Your use case - medical textbook tables
validator = TableStructureValidator('medical_textbook.pdf')

# Many medical tables are text-only
# But when they have borders, validation is very accurate
```

### Financial Reports

```python
# Financial tables often have clear borders
# Excellent candidate for this validation approach
validator = TableStructureValidator('annual_report.pdf')
# Expect 70-90% of tables to have drawn structures
```

### Forms and Templates

```python
# Forms almost always have drawn borders
# Near-perfect validation accuracy
validator = TableStructureValidator('form.pdf')
# Expect 95%+ tables to have drawn structures
```

## Limitations

### What This Does NOT Handle

1. **Text-only tables** (no drawn borders)
   - Solution: Use text alignment/spacing analysis
   
2. **Tables with partial borders** (e.g., only horizontal lines)
   - Solution: Lower minimum line threshold
   
3. **Complex nested tables**
   - Solution: Hierarchical validation
   
4. **Tables split across pages**
   - Solution: Multi-page table detection

### Current Requirements

- Minimum 2 horizontal lines + 2 vertical lines
- Lines must be within 5pt of Camelot bbox
- Lines must form a recognizable grid pattern

## Advanced Configuration

### Adjust Line Detection Sensitivity

```python
# More lenient (catches more lines, may include noise)
h_lines, v_lines = extract_table_lines(page, table_bbox, margin=10.0)

# More strict (cleaner detection, may miss some lines)
h_lines, v_lines = extract_table_lines(page, table_bbox, margin=2.0)
```

### Change Minimum Grid Requirements

```python
# Require 3x3 grid (more strict)
structure_bbox = find_table_structure_bbox(h_lines, v_lines, min_lines=3)

# Allow 1x1 grid (more lenient, may catch non-tables)
structure_bbox = find_table_structure_bbox(h_lines, v_lines, min_lines=1)
```

## Troubleshooting

### "No drawings found"

**Cause**: PDF has text-only tables

**Solution**: This is normal. The table doesn't have drawn borders. Trust Camelot's extraction.

### "Found lines but insufficient for grid"

**Cause**: PDF has some lines (e.g., underlines) but not a full table grid

**Solution**: Lower `min_lines` parameter or investigate with `debug_drawings.py`

### "All rows marked as outside structure"

**Cause**: Coordinate system mismatch or large background rectangle detected

**Solution**: 
- Check if background rectangles are being filtered (they should be)
- Verify coordinate systems match between Camelot and PyMuPDF
- Use `debug_drawings.py` to inspect actual drawings

## Contributing

### Adding Features

Ideas for enhancement:
- [ ] Text-only table validation (alignment analysis)
- [ ] Visual diff generator (annotated PDF output)
- [ ] Confidence scoring improvements
- [ ] Support for partial borders (only horizontal or vertical)
- [ ] Multi-page table detection
- [ ] Integration with other table extractors (Tabula, PDFPlumber)

### Testing

```bash
# Test on various PDF types
python table_structure_validator.py test_pdfs/scientific.pdf
python table_structure_validator.py test_pdfs/financial.pdf
python table_structure_validator.py test_pdfs/form.pdf

# Compare before/after accuracy
# Manually verify trimmed rows were indeed false positives
```

## References

- **PyMuPDF Documentation**: https://pymupdf.readthedocs.io/
- **Camelot**: https://camelot-py.readthedocs.io/
- **PDF Drawing Commands**: PDF Reference 1.7, Section 4.4

## License

Same as parent project.

## Authors

Created as part of PDF-to-RittDoc conversion pipeline.

## Support

For issues or questions:
1. Check `TABLE_VALIDATION_EXPERIMENT.md` for detailed explanation
2. Use `debug_drawings.py` to diagnose drawing detection
3. Review examples in `table_validator_integration_example.py`
