# Table Validation Update for multimedia.xml

## Purpose

Updates existing multimedia.xml files by removing table rows/entries that fall outside the actual drawn table boundaries detected via PyMuPDF's `get_drawings()`.

## Usage

```bash
python3 update_multimedia_with_validation.py <multimedia.xml> <pdf_path> [output.xml]
```

### Examples

```bash
# Basic usage (creates multimedia_validated.xml)
python3 update_multimedia_with_validation.py output_MultiMedia.xml document.pdf

# Specify custom output path
python3 update_multimedia_with_validation.py input.xml doc.pdf output.xml
```

## What It Does

1. **Finds actual table structure** using `page.get_drawings()` to extract horizontal and vertical lines
2. **Calculates precise rect** from the outermost grid lines
3. **Removes rows** where all entries fall outside the validated rect
4. **Adds metadata** to table elements:
   - `validation_status="has_structure"` or `"text_only"`
   - `validated_x1/y1/x2/y2` attributes (if structure found)

## Output

- Tables with drawn borders: Rows outside structure are removed
- Text-only tables: Kept as-is (no structure to validate against)
- Creates validated XML with cleaner table content

## Verification

```bash
# Check row count difference
grep -c '<row>' input.xml output_validated.xml

# See what changed
diff input.xml output_validated.xml
```

## Configuration

Edit script (line ~290) to adjust:
- `min_lines=2` - Minimum lines for valid table (1=lenient, 2=balanced, 3=strict)
- `margin=5.0` - Detection margin in points (2=strict, 5=balanced, 10=lenient)

## Integration Notes

- For **NEW** multimedia.xml: Validation is already integrated in `Multipage_Image_Extractor.py`
- For **EXISTING** multimedia.xml: Use this script to retroactively clean files
