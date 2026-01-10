# Integration Example

## How to Integrate Phrase Combining into pdf_to_unified_xml.py

There are two ways to integrate the phrase combining functionality:

### Option 1: Post-Processing (Recommended)

Run as a separate step after generating Unified.xml:

```bash
# Step 1: Generate Unified XML
python3 pdf_to_unified_xml.py input.pdf Unified.xml

# Step 2: Combine phrases
python3 phrase_combiner.py Unified.xml Unified_Combined.xml --wrap-in-text

# Step 3: Continue with heuristics
python3 heuristics_Nov3.py Unified_Combined.xml output/
```

**Advantages:**
- No modifications to existing code
- Easy to enable/disable
- Can be tested independently
- Keeps concerns separated

### Option 2: Inline Integration

Modify `pdf_to_unified_xml.py` to combine phrases during generation.

#### Step 1: Import the module

Add at the top of `pdf_to_unified_xml.py`:

```python
from phrase_combiner import combine_phrases_in_para
```

#### Step 2: Modify the para creation code

Find this section in `pdf_to_unified_xml.py` (around line 2115-2120):

```python
# Create inline elements directly under <para> (flattened, flowing structure)
for elem_name, attrs, text in merged_phrases:
    inline_elem = ET.SubElement(para_elem, elem_name, attrs)
    inline_elem.text = text
```

Replace with:

```python
# Create inline elements directly under <para> (flattened, flowing structure)
for elem_name, attrs, text in merged_phrases:
    inline_elem = ET.SubElement(para_elem, elem_name, attrs)
    inline_elem.text = text

# OPTIONAL: Combine phrases into flowing text with semantic formatting
# Uncomment the line below to enable phrase combining
# para_elem = combine_phrases_in_para(para_elem, wrap_in_text=True)
```

#### Step 3: Replace the para element

If you want to apply combining, you need to replace the para element in its parent. Here's a more complete modification:

```python
# Create <para> element with col_id and reading_block from first fragment
first_fragment = para_fragments[0]
para_attrs = {
    "col_id": str(first_fragment["col_id"]),
    "reading_block": str(first_fragment["reading_order_block"]),
}
para_elem = ET.SubElement(texts_elem, "para", para_attrs)

# Collect all inline phrases for this paragraph (flattened structure)
# ... (existing code) ...

# Create inline elements directly under <para>
for elem_name, attrs, text in merged_phrases:
    inline_elem = ET.SubElement(para_elem, elem_name, attrs)
    inline_elem.text = text

# NEW: Combine phrases into flowing text with semantic formatting
# This converts font names to <emphasis> elements and merges adjacent text
combined_para = combine_phrases_in_para(para_elem, wrap_in_text=True)

# Replace the para element in texts_elem
texts_elem.remove(para_elem)
texts_elem.append(combined_para)
```

### Option 3: Hybrid Approach

Keep the generation as-is but add a flag to enable combining:

```python
import argparse

parser = argparse.ArgumentParser()
# ... existing arguments ...
parser.add_argument(
    '--combine-phrases',
    action='store_true',
    help='Combine phrases within paragraphs for better text flow'
)
parser.add_argument(
    '--wrap-in-text',
    action='store_true',
    help='Wrap combined phrases in <text> element for heuristics compatibility'
)

args = parser.parse_args()

# Later in the code, when creating paragraphs:
if args.combine_phrases:
    from phrase_combiner import combine_phrases_in_para
    combined_para = combine_phrases_in_para(
        para_elem, 
        wrap_in_text=args.wrap_in_text
    )
    texts_elem.remove(para_elem)
    texts_elem.append(combined_para)
```

Then use it like:

```bash
python3 pdf_to_unified_xml.py input.pdf output.xml --combine-phrases --wrap-in-text
```

## Programmatic Usage

You can also use the phrase_combiner module in your own Python code:

```python
from phrase_combiner import combine_phrases_in_para, combine_phrases_in_tree
from xml.etree import ElementTree as ET

# Process a single para element
tree = ET.parse('input.xml')
root = tree.getroot()

for para in root.iter('para'):
    combined_para = combine_phrases_in_para(para, wrap_in_text=True)
    # Replace para with combined_para in parent
    # ... (parent replacement logic) ...

# Or process entire tree at once
tree = ET.parse('input.xml')
processed_tree = combine_phrases_in_tree(
    tree, 
    wrap_in_text=True,
    preserve_position=False,
    in_place=True
)
processed_tree.write('output.xml')
```

## Testing

Test the integration with your actual data:

```bash
# 1. Generate a small test file
python3 pdf_to_unified_xml.py test.pdf test_unified.xml

# 2. Combine phrases
python3 phrase_combiner.py test_unified.xml test_combined.xml --wrap-in-text

# 3. Compare the files
diff test_unified.xml test_combined.xml

# 4. Test with heuristics
python3 heuristics_Nov3.py test_combined.xml output/
```

## Performance Considerations

- **Post-processing** (Option 1): Adds ~5-10% to total processing time
- **Inline** (Option 2): Minimal overhead, integrated into generation
- **Memory**: Negligible impact for typical documents

For large documents (1000+ pages), post-processing is recommended to keep generation and transformation separate.

## Troubleshooting

### Issue: Combined output breaks heuristics

**Solution:** Use `--wrap-in-text` flag to ensure compatibility

### Issue: Font styling not detected

**Problem:** Non-standard font names
**Solution:** Modify `is_bold_font()` and `is_italic_font()` in `phrase_combiner.py` to match your font naming conventions

### Issue: Position information lost

**Solution:** Use `--preserve-position` flag if downstream code needs position attributes

## Comparison

### Before (Fragmented)

```xml
<para col_id="1" reading_block="1">
  <phrase font="Arial" size="12" top="100" left="50">The quick </phrase>
  <phrase font="Arial-Bold" size="12" top="100" left="120">brown fox</phrase>
  <phrase font="Arial" size="12" top="100" left="200"> jumps.</phrase>
</para>
```

### After (Combined, Flat Mode)

```xml
<para col_id="1" reading_block="1">
  The quick <emphasis role="bold">brown fox</emphasis> jumps.
</para>
```

### After (Combined, Wrapped Mode)

```xml
<para col_id="1" reading_block="1">
  <text>The quick <emphasis role="bold">brown fox</emphasis> jumps.</text>
</para>
```

## Benefits

1. **Better text flow** - Text naturally reflows when displayed
2. **Cleaner XML** - Fewer elements, more semantic
3. **Preserved styling** - Font formatting converted to semantic markup
4. **Flexible output** - Choose flat or wrapped format
5. **Easy integration** - Works as standalone or integrated tool

## Recommendations

- **For production**: Use Option 1 (post-processing) for stability
- **For development**: Use Option 3 (hybrid) for flexibility
- **For new systems**: Use flat mode for cleaner output
- **For legacy systems**: Use wrapped mode for compatibility
