# Start Here: Phrase Combining Solution

## Your Question

> "Unified.xml seems to have <para> tags and within a <para> a bunch of individual <phrase> 
> we need those phrases within a para combined so it all flows together..But even while 
> combining the phrases in a para we need to retain the font formatting of individual 
> fragments..can you do that?"

## Answer: YES! ✅

I've created a complete solution that combines phrases within para tags while preserving font formatting.

## What You Need to Do

### 1. Run This Command

```bash
python3 phrase_combiner.py Unified.xml Unified_Combined.xml --wrap-in-text
```

That's it! Your phrases will be combined with formatting preserved.

## What Happens

### Before (Your Current XML):
```xml
<para col_id="1" reading_block="1">
  <phrase font="Arial" size="12">Hello </phrase>
  <phrase font="Arial" size="12">there, </phrase>
  <phrase font="Arial-Bold" size="12">this is bold</phrase>
  <phrase font="Arial" size="12"> text.</phrase>
</para>
```

### After (Combined & Formatted):
```xml
<para col_id="1" reading_block="1">
  <text>Hello there, <emphasis role="bold">this is bold</emphasis> text.</text>
</para>
```

## The Magic ✨

The script:
1. **Combines phrases** - Merges adjacent text with same formatting
2. **Preserves formatting** - Converts font names to semantic elements
3. **Handles special cases** - Keeps subscripts, superscripts intact
4. **Creates clean XML** - Easy to read and process

### Font Detection

The script automatically detects and converts:
- **Arial-Bold**, Helvetica-Bold → `<emphasis role="bold">`
- **Times-Italic**, Courier-Oblique → `<emphasis role="italic">`
- **Times-BoldItalic** → `<emphasis role="bold-italic">`
- **Subscript/Superscript** → Preserved as-is
- **Regular fonts** → Plain text

## Files Created for You

### Main Tools (Pick One)

1. **`phrase_combiner.py`** ⭐ **RECOMMENDED**
   - Most flexible
   - Can be used as library or command-line tool
   - Best for production

2. `combine_phrases_in_para.py`
   - Simple version
   - Quick and easy

3. `combine_phrases_in_para_v2.py`
   - Enhanced version
   - More options

### Documentation

1. **`README_PHRASE_COMBINING.md`** - Quick reference
2. **`PHRASE_COMBINING_SUMMARY.md`** - Complete overview
3. **`PHRASE_COMBINING_GUIDE.md`** - Detailed usage guide
4. **`INTEGRATION_EXAMPLE.md`** - How to integrate into your pipeline

### Test Files

- `test_unified_sample.xml` - Sample input
- `test_output_flat.xml` - Sample output (flat mode)
- `test_output_wrapped.xml` - Sample output (wrapped mode)

## Quick Test

Try it with the sample file:

```bash
# See the sample input
cat test_unified_sample.xml

# Run the combiner
python3 phrase_combiner.py test_unified_sample.xml my_test_output.xml --wrap-in-text

# See the result
cat my_test_output.xml
```

## Options

### Basic (Flat Mode)
```bash
python3 phrase_combiner.py input.xml output.xml
```
- Inline elements directly in `<para>`
- Good for display/rendering
- Clean structure

### With Text Wrapper (Recommended)
```bash
python3 phrase_combiner.py input.xml output.xml --wrap-in-text
```
- Wraps content in `<text>` element
- Compatible with heuristics code
- Better for downstream processing

### Preserve Position Info
```bash
python3 phrase_combiner.py input.xml output.xml --preserve-position
```
- Keeps position attributes if needed

## Full Pipeline Example

```bash
# Step 1: Generate Unified.xml from PDF (your existing step)
python3 pdf_to_unified_xml.py input.pdf Unified.xml

# Step 2: Combine phrases (NEW STEP)
python3 phrase_combiner.py Unified.xml Unified_Combined.xml --wrap-in-text

# Step 3: Process with heuristics (your existing step)
python3 heuristics_Nov3.py Unified_Combined.xml output/
```

## Real Examples

### Example 1: Bold Text
**Input:**
```xml
<para>
  <phrase font="Arial">This is </phrase>
  <phrase font="Arial-Bold">important</phrase>
  <phrase font="Arial">!</phrase>
</para>
```

**Output:**
```xml
<para>
  <text>This is <emphasis role="bold">important</emphasis>!</text>
</para>
```

### Example 2: Chemical Formula
**Input:**
```xml
<para>
  <phrase font="Helvetica">Water is H</phrase>
  <subscript font="Helvetica">2</subscript>
  <phrase font="Helvetica">O</phrase>
</para>
```

**Output:**
```xml
<para>
  <text>Water is H<subscript>2</subscript>O</text>
</para>
```

### Example 3: Mixed Formatting
**Input:**
```xml
<para>
  <phrase font="Times">Normal </phrase>
  <phrase font="Times-Italic">italic </phrase>
  <phrase font="Times-BoldItalic">bold-italic </phrase>
  <phrase font="Times">normal</phrase>
</para>
```

**Output:**
```xml
<para>
  <text>Normal <emphasis role="italic">italic </emphasis><emphasis role="bold-italic">bold-italic </emphasis>normal</text>
</para>
```

## Verification

To verify it's working:

```bash
# 1. Check structure before
grep -c "<phrase" Unified.xml

# 2. Run combiner
python3 phrase_combiner.py Unified.xml Unified_Combined.xml --wrap-in-text

# 3. Check structure after (should be fewer or zero)
grep -c "<phrase" Unified_Combined.xml
```

## Troubleshooting

### Problem: "File not found"
**Solution:** Check that Unified.xml exists in current directory

### Problem: "No para elements processed"
**Solution:** Your XML might have a different structure. Check:
```bash
grep "<para" Unified.xml | head -5
```

### Problem: "Font formatting not working"
**Solution:** Check font names in your XML:
```bash
grep 'font=' Unified.xml | head -10
```

If they don't follow "FontName-Bold" or "FontName-Italic" pattern, you may need to customize the font detection.

## Need Help?

1. **Quick reference**: `README_PHRASE_COMBINING.md`
2. **Detailed guide**: `PHRASE_COMBINING_GUIDE.md`
3. **Integration**: `INTEGRATION_EXAMPLE.md`
4. **Full summary**: `PHRASE_COMBINING_SUMMARY.md`

## Summary

You asked if I can combine phrases in para while retaining font formatting.

**Answer: YES, and it's ready to use!**

Just run:
```bash
python3 phrase_combiner.py Unified.xml Unified_Combined.xml --wrap-in-text
```

The text will flow naturally while keeping bold, italic, subscript, superscript, and all other formatting intact.

---

**Ready to go!** 🚀

Try it with your Unified.xml file and see the results.
