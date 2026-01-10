# Combining Phrases in Para Elements

## Overview

This guide explains how to combine multiple `<phrase>` elements within `<para>` tags into flowing text while preserving font formatting.

## The Problem

The current `pdf_to_unified_xml.py` creates XML with this structure:

```xml
<para col_id="1" reading_block="1">
  <phrase font="Arial" size="12">This is regular text.</phrase>
  <phrase font="Arial" size="12"> More text.</phrase>
  <phrase font="Arial-Bold" size="12"> Bold text.</phrase>
  <phrase font="Arial" size="12"> Back to regular.</phrase>
</para>
```

This structure:
- Makes text appear fragmented
- Doesn't flow naturally when displayed
- Each phrase is treated as a separate block

## The Solution

The `combine_phrases_in_para_v2.py` script combines these phrases into flowing text while preserving font formatting:

```xml
<para col_id="1" reading_block="1">
  This is regular text. More text.<emphasis role="bold"> Bold text.</emphasis> Back to regular.
</para>
```

## Features

### Font Formatting Preservation

The script automatically detects font styles and converts them to semantic elements:

- **Bold fonts** (Arial-Bold, Helvetica-Bold, etc.) → `<emphasis role="bold">`
- **Italic fonts** (Times-Italic, Courier-Oblique, etc.) → `<emphasis role="italic">`
- **Bold+Italic** (Times-BoldItalic, etc.) → `<emphasis role="bold-italic">`
- **Subscript/Superscript** → Preserved as `<subscript>` and `<superscript>`
- **Regular text** → Merged into plain text

### Two Output Modes

#### 1. Flat Mode (Default)

Inline elements directly under `<para>`:

```xml
<para>
  Regular text <emphasis role="bold">bold text</emphasis> more text.
</para>
```

**Best for:**
- Display and rendering
- Natural text reflow
- Modern XML parsers

#### 2. Wrapped Mode (`--wrap-in-text`)

Content wrapped in `<text>` element:

```xml
<para>
  <text>Regular text <emphasis role="bold">bold text</emphasis> more text.</text>
</para>
```

**Best for:**
- Compatibility with `heuristics_Nov3.py`
- Legacy systems expecting `<text>` wrapper
- Backward compatibility

## Usage

### Basic Usage (Flat Mode)

```bash
python3 combine_phrases_in_para_v2.py input.xml output.xml
```

### With Text Wrapper (Wrapped Mode)

```bash
python3 combine_phrases_in_para_v2.py input.xml output.xml --wrap-in-text
```

### Examples

#### Example 1: Simple Font Changes

**Input:**
```xml
<para col_id="1" reading_block="1">
  <phrase font="Arial" size="12">Hello </phrase>
  <phrase font="Arial-Bold" size="12">world</phrase>
  <phrase font="Arial" size="12">!</phrase>
</para>
```

**Output (Flat):**
```xml
<para col_id="1" reading_block="1">
  Hello <emphasis role="bold">world</emphasis>!
</para>
```

#### Example 2: Subscript and Superscript

**Input:**
```xml
<para col_id="1" reading_block="1">
  <phrase font="Helvetica" size="10">H</phrase>
  <subscript font="Helvetica" size="8">2</subscript>
  <phrase font="Helvetica" size="10">O</phrase>
</para>
```

**Output (Flat):**
```xml
<para col_id="1" reading_block="1">
  H<subscript>2</subscript>O
</para>
```

#### Example 3: Mixed Formatting

**Input:**
```xml
<para col_id="1" reading_block="2">
  <phrase font="Times" size="11">Normal </phrase>
  <phrase font="Times-Italic" size="11">italic </phrase>
  <phrase font="Times-BoldItalic" size="11">bold-italic </phrase>
  <phrase font="Times" size="11">text.</phrase>
</para>
```

**Output (Flat):**
```xml
<para col_id="1" reading_block="2">
  Normal <emphasis role="italic">italic </emphasis><emphasis role="bold-italic">bold-italic </emphasis>text.
</para>
```

## Integration with Pipeline

### Option 1: Post-Processing

Run after `pdf_to_unified_xml.py`:

```bash
# Step 1: Generate Unified.xml
python3 pdf_to_unified_xml.py input.pdf Unified.xml

# Step 2: Combine phrases
python3 combine_phrases_in_para_v2.py Unified.xml Unified_Combined.xml --wrap-in-text

# Step 3: Run heuristics
python3 heuristics_Nov3.py Unified_Combined.xml output/
```

### Option 2: Integrate Directly

You can integrate the phrase combining logic directly into `pdf_to_unified_xml.py` by calling the `combine_phrases_in_para()` function after creating each `<para>` element.

## Technical Details

### Attribute Normalization

The script normalizes font attributes for comparison:
- Keeps: `font`, `size`, `color`
- Removes: `top`, `left`, `reading_order` (position attributes)

This allows phrases with the same visual appearance but different positions to be merged.

### Merging Algorithm

1. **Collect** all inline elements from the para
2. **Classify** each element by type (text, emphasis, subscript, superscript)
3. **Merge** consecutive elements with identical formatting
4. **Build** new para with combined content

### Font Detection

The script uses keyword matching to detect font styles:

- **Bold**: Contains "bold", "heavy", "black", or "demi"
- **Italic**: Contains "italic", "oblique", or "slant"
- **Case-insensitive** matching

## Troubleshooting

### Issue: Formatting not preserved

**Solution:** Check that font names follow standard conventions (e.g., "Arial-Bold", "Times-Italic")

### Issue: Script says "0 para elements processed"

**Solution:** Verify that your XML has `<para>` elements with `<phrase>` children

### Issue: Output doesn't work with heuristics

**Solution:** Use `--wrap-in-text` flag to create compatible format

## Files

- `combine_phrases_in_para.py` - Original version (flat mode only)
- `combine_phrases_in_para_v2.py` - Enhanced version with both modes
- `test_unified_sample.xml` - Sample input for testing
- `test_output_flat.xml` - Sample output (flat mode)
- `test_output_wrapped.xml` - Sample output (wrapped mode)

## Benefits

✅ **Natural text flow** - Text reflows properly when displayed  
✅ **Preserved formatting** - Bold, italic, scripts maintained  
✅ **Cleaner XML** - Fewer elements, easier to read  
✅ **Better compatibility** - Works with both old and new systems  
✅ **Semantic markup** - Uses `<emphasis>` instead of raw font names  

## Next Steps

1. Test with your actual Unified.xml file
2. Choose the appropriate mode (flat or wrapped)
3. Integrate into your processing pipeline
4. Validate output with your downstream tools

## Questions?

If you have questions or encounter issues, check:
- XML structure matches expected format
- Font names follow standard conventions
- Output mode matches your downstream requirements
