# Phrase Combining for Unified.xml

## Overview

This solution combines multiple `<phrase>` elements within `<para>` tags into flowing text while preserving font formatting. This improves text reflow, readability, and semantic structure.

## Quick Start

```bash
# Process your Unified.xml file
python3 phrase_combiner.py Unified.xml Unified_Combined.xml --wrap-in-text
```

## What It Does

Transforms fragmented phrases:
```xml
<para>
  <phrase font="Arial">Hello </phrase>
  <phrase font="Arial-Bold">world</phrase>
</para>
```

Into flowing text with semantic formatting:
```xml
<para>Hello <emphasis role="bold">world</emphasis></para>
```

## Features

- ✅ Combines adjacent phrases with same formatting
- ✅ Converts font names to semantic elements (`<emphasis>`, etc.)
- ✅ Preserves subscript, superscript, and special formatting
- ✅ Two output modes: flat and wrapped
- ✅ No external dependencies
- ✅ Fast and memory-efficient

## Files

| File | Description |
|------|-------------|
| **`phrase_combiner.py`** | Main tool (recommended) |
| `combine_phrases_in_para.py` | Simple version |
| `combine_phrases_in_para_v2.py` | Enhanced version |
| `PHRASE_COMBINING_SUMMARY.md` | Complete summary |
| `PHRASE_COMBINING_GUIDE.md` | Detailed usage guide |
| `INTEGRATION_EXAMPLE.md` | Integration instructions |

## Usage

### Basic Usage

```bash
# Flat mode (inline elements in para)
python3 phrase_combiner.py input.xml output.xml

# Wrapped mode (with <text> wrapper for heuristics)
python3 phrase_combiner.py input.xml output.xml --wrap-in-text

# Preserve position attributes
python3 phrase_combiner.py input.xml output.xml --preserve-position
```

### As a Library

```python
from phrase_combiner import combine_phrases_in_para
from xml.etree import ElementTree as ET

para = ET.fromstring('<para><phrase>...</phrase></para>')
combined = combine_phrases_in_para(para, wrap_in_text=True)
```

## Output Modes

### Flat Mode (Default)
Best for display and modern systems:
```xml
<para>Text <emphasis role="bold">bold</emphasis> more text</para>
```

### Wrapped Mode (`--wrap-in-text`)
Compatible with heuristics_Nov3.py:
```xml
<para>
  <text>Text <emphasis role="bold">bold</emphasis> more text</text>
</para>
```

## Font Formatting Detection

| Font Pattern | Output |
|--------------|--------|
| Arial-Bold, Helvetica-Bold | `<emphasis role="bold">` |
| Times-Italic, Courier-Oblique | `<emphasis role="italic">` |
| Times-BoldItalic | `<emphasis role="bold-italic">` |
| `<subscript>` | Preserved |
| `<superscript>` | Preserved |

## Pipeline Integration

```bash
# Complete workflow
python3 pdf_to_unified_xml.py input.pdf Unified.xml
python3 phrase_combiner.py Unified.xml Unified_Combined.xml --wrap-in-text
python3 heuristics_Nov3.py Unified_Combined.xml output/
```

## Examples

### Example 1: Text with Bold
**Input:**
```xml
<para>
  <phrase font="Arial">Normal </phrase>
  <phrase font="Arial-Bold">bold</phrase>
</para>
```
**Output:**
```xml
<para>Normal <emphasis role="bold">bold</emphasis></para>
```

### Example 2: Chemical Formula
**Input:**
```xml
<para>
  <phrase font="Helvetica">H</phrase>
  <subscript>2</subscript>
  <phrase font="Helvetica">O</phrase>
</para>
```
**Output:**
```xml
<para>H<subscript>2</subscript>O</para>
```

## Testing

Test with sample data:
```bash
# Run test
python3 phrase_combiner.py test_unified_sample.xml test_output.xml --wrap-in-text

# View result
cat test_output.xml
```

## Troubleshooting

### No output changes?
- Verify input has `<para>` with `<phrase>` children
- Check file path is correct

### Formatting not detected?
- Ensure font names follow conventions (e.g., "Arial-Bold")
- Modify font detection rules in code if needed

### Heuristics compatibility issues?
- Use `--wrap-in-text` flag

## Requirements

- Python 3.6+
- Standard library only (no pip install needed)

## Documentation

- **Summary**: See `PHRASE_COMBINING_SUMMARY.md`
- **Guide**: See `PHRASE_COMBINING_GUIDE.md`
- **Integration**: See `INTEGRATION_EXAMPLE.md`

## Support

The code is well-documented with inline comments. Check the source files for detailed explanations of the algorithms and logic.

## License

Same as the main project.

---

**Ready to use!** Run the command above with your Unified.xml file to get started.
