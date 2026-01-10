# Critical Discovery: Two-Stage Text Merging

## The Pipeline Has TWO Merging Stages

Looking at line 42 of `pdf_to_unified_xml.py`:
```python
from pdf_to_excel_columns import pdf_to_excel_with_columns
```

This means text goes through:

### Stage 1: Row-Level Merging (`pdf_to_excel_columns.py`)
**Function:** `merge_inline_fragments_in_row()`
**When:** During initial PDF extraction
**What:** Merges fragments on same row/baseline
**My fix:** Added Phase 6 - continuation word detection

### Stage 2: Paragraph Grouping (`pdf_to_unified_xml.py`)
**Function:** `should_merge_fragments()` 
**When:** Creating unified XML
**What:** Decides if fragments belong in same paragraph
**My fix:** Added continuation word detection + small gap handling

## Why This Matters

**Your issue could be caused by:**

1. **Stage 1 fails to merge** → Fragments stay separate
   - `merge_inline_fragments_in_row()` doesn't merge "including " + "Radiology"
   - They arrive at Stage 2 as separate fragments

2. **Stage 2 fails to keep them together** → Fragments split into different paragraphs
   - `should_merge_fragments()` returns False
   - They get grouped into separate `<para>` elements

## Which Stage Is The Problem?

**To find out, you need to check the intermediate output:**

```bash
# Run just Stage 1
python3 pdf_to_excel_columns.py 9780803694958.pdf
# This creates an Excel file with merged fragments

# Check if "including Radiology" is:
# - In ONE cell → Stage 1 worked, Stage 2 is the problem
# - In TWO cells → Stage 1 is the problem
```

## My Fixes Address Both Stages

✓ **Stage 1 fix** (`pdf_to_excel_columns.py` lines 1202-1215):
```python
# Phase 6: Continuation words
if not should_merge and gap > 0 and gap <= 15.0:
    if current_txt.endswith('including'):
        should_merge = True
```

✓ **Stage 2 fix** (`pdf_to_unified_xml.py` lines 1335-1365):
```python
# Check for continuation words
if prev_text.endswith('including'):
    return True  # Keep in same paragraph
```

## The Real Question

**Did I fix the right stage?**

Without seeing the intermediate output, I don't know if the problem is:
- Stage 1 not merging the fragments initially, OR
- Stage 2 splitting them into different paragraphs, OR  
- Both stages failing

## What You Should Check

1. **Process the PDF:** `python3 pdf_to_rittdoc.py 9780803694958.pdf`

2. **Check intermediate files:**
   - Excel output (if generated) - shows Stage 1 results
   - Unified XML - shows Stage 2 results

3. **Look for this pattern in unified XML:**
   ```xml
   <!-- BEFORE (broken): -->
   <para>journals including</para>
   <para>Radiology</para>
   
   <!-- AFTER (fixed): -->
   <para>journals including <emphasis role="italic">Radiology</emphasis></para>
   ```

4. **If it's STILL broken**, tell me:
   - Are they in separate `<para>` tags?
   - Are they in the SAME `<para>` but on separate lines in HTML?
   - What's the actual XML structure?

Then I can target the exact stage that's failing.
