# Table Header Cleaning Feature

## Overview

Tables extracted by Camelot sometimes incorrectly include content that isn't part of the actual table:
- ❌ Table captions ("Table 1. Description...")
- ❌ Paragraph text above the table
- ❌ Page headers/footers
- ❌ Long sentences spanning the table

The **TableHeaderCleaner** automatically detects and removes these extra rows, keeping only the real table data.

---

## Problem Example

### Before Cleaning:
```
Row 0: "Table 1. Sample data showing measurements" | "" | ""
Row 1: "Name" | "Age" | "Score"        ← REAL HEADER
Row 2: "Alice" | "25" | "95"
Row 3: "Bob" | "30" | "87"
```

### After Cleaning:
```
Row 0: "Name" | "Age" | "Score"        ← REAL HEADER
Row 1: "Alice" | "25" | "95"
Row 2: "Bob" | "30" | "87"
```

**Result:** Caption removed, table starts with real header!

---

## How It Works

### Detection Algorithm

The `TableHeaderCleaner.find_real_header_row()` function identifies the real header by checking:

1. **Fill Ratio** - At least 70% of cells must be non-empty
   - Real headers: `["Name", "Age", "Score"]` → 100% filled ✓
   - Captions: `["Table 1. Description...", "", ""]` → 33% filled ✗

2. **Cell Length** - Maximum cell length < 80 characters
   - Real headers: Short column names ✓
   - Paragraphs: Long sentences ✗

3. **Multiple Cells** - At least 2 cells or 50% of columns filled
   - Real headers: Multiple column names ✓
   - Single-cell captions: Only one cell ✗

### Cleaning Process

```python
# 1. Extract dataframe from Camelot table
df_original = table.df

# 2. Find the real header row
header_row_idx = find_real_header_row(df)
# Returns: 0 (no extra rows) or N (skip first N rows)

# 3. Remove extra rows
df_cleaned = df.iloc[header_row_idx:]

# 4. Skip these rows when creating XML
for r_idx in range(nrows):
    if r_idx < skip_first_n_rows:
        continue  # Skip extra rows
    # ... process cell ...
```

---

## Implementation Details

### Files Modified

**`Multipage_Image_Extractor.py`:**

1. **Added Import** (line 7):
   ```python
   import pandas as pd  # For table data cleaning
   ```

2. **Added Class** (lines 996-1111):
   ```python
   class TableHeaderCleaner:
       @staticmethod
       def find_real_header_row(df: pd.DataFrame, verbose: bool = False) -> int
       
       @staticmethod
       def clean_table_data(df: pd.DataFrame, verbose: bool = False) -> Tuple[pd.DataFrame, int]
   ```

3. **Integrated Cleaning** in `add_tables_for_page()` (lines 2454-2464):
   ```python
   # Extract and clean table data
   df_original = t.df
   df_cleaned, rows_removed = TableHeaderCleaner.clean_table_data(df_original, verbose=True)
   skip_first_n_rows = rows_removed
   ```

4. **Applied Filtering** (lines 2621-2625):
   ```python
   for r_idx in range(nrows):
       # Skip rows identified as extra content
       if r_idx < skip_first_n_rows:
           rows_filtered_header_cleaning += 1
           continue
   ```

5. **Updated Reporting** (lines 2699-2710):
   ```python
   if rows_filtered_header_cleaning > 0:
       reasons.append(f"header cleaning: {rows_filtered_header_cleaning}")
   print(f"Table {idx}: Filtered {total_rows_filtered}/{nrows} rows [{reason_str}]")
   ```

---

## Test Results

All 5 tests pass! ✅

### Test 1: Caption Above Header
- **Input:** Caption + Header + 3 data rows
- **Output:** Removed 1 row (caption)
- **Result:** ✓ PASS

### Test 2: Paragraph Above Header
- **Input:** Long paragraph + Header + 3 data rows
- **Output:** Removed 1 row (paragraph)
- **Result:** ✓ PASS

### Test 3: Clean Table
- **Input:** Header + 3 data rows (already clean)
- **Output:** Removed 0 rows
- **Result:** ✓ PASS

### Test 4: Multiple Extra Rows
- **Input:** Caption + Blank + Paragraph + Header + 2 data rows
- **Output:** Removed 3 rows
- **Result:** ✓ PASS

### Test 5: Sparse First Row
- **Input:** "Page 1" (sparse) + Header + 2 data rows
- **Output:** Detected header at row 1 (not row 0)
- **Result:** ✓ PASS

---

## Usage

**No configuration needed!** The feature works automatically.

When processing PDFs:
```bash
python3 pdf_to_unified_xml.py your_document.pdf
```

### Console Output Example

```
Processing tables on page 5...
      🧹 Removing 1 extra row(s) above table header
      ✨ Table cleaned: 5 rows → 4 rows
      Table 1: Filtered 1/5 rows (20.0%) [header cleaning: 1] - kept 4 valid rows
```

---

## Examples

### Example 1: Table Caption Removal

**Before:**
```xml
<table id="t1">
  <tgroup cols="3">
    <tbody>
      <row>  <!-- Caption row - UNWANTED -->
        <entry>Table 1. Results from experiment</entry>
        <entry></entry>
        <entry></entry>
      </row>
      <row>  <!-- Real header -->
        <entry>Variable</entry>
        <entry>Value</entry>
        <entry>Unit</entry>
      </row>
      <row>
        <entry>Temperature</entry>
        <entry>25</entry>
        <entry>°C</entry>
      </row>
    </tbody>
  </tgroup>
</table>
```

**After:**
```xml
<table id="t1">
  <tgroup cols="3">
    <tbody>
      <row>  <!-- Real header -->
        <entry>Variable</entry>
        <entry>Value</entry>
        <entry>Unit</entry>
      </row>
      <row>
        <entry>Temperature</entry>
        <entry>25</entry>
        <entry>°C</entry>
      </row>
    </tbody>
  </tgroup>
</table>
```

### Example 2: Paragraph Text Removal

**Before:** Table includes paragraph above
```
Row 0: "The following table shows comparison between methods" | "" | "" | ""
Row 1: "Method" | "Accuracy" | "Speed" | "Cost"
Row 2: "Method A" | "95%" | "Fast" | "Low"
```

**After:** Paragraph removed
```
Row 0: "Method" | "Accuracy" | "Speed" | "Cost"
Row 1: "Method A" | "95%" | "Fast" | "Low"
```

---

## Benefits

1. ✅ **Cleaner Tables** - No captions or paragraph text in table data
2. ✅ **Accurate Structure** - Real headers correctly identified
3. ✅ **Better XML** - Proper DocBook table structure
4. ✅ **Automatic** - No manual configuration required
5. ✅ **Robust** - Handles multiple types of extra content

---

## Edge Cases Handled

### 1. Empty First Row
```
Row 0: "" | "" | ""  ← Skipped (empty)
Row 1: "Name" | "Age" | "Score"  ← Detected as header
```

### 2. Single-Cell Caption
```
Row 0: "Table 1. Description..." | "" | ""  ← Removed (sparse, long text)
Row 1: "Col1" | "Col2" | "Col3"  ← Detected as header
```

### 3. Multiple Blank Rows
```
Row 0: "" | "" | ""  ← Skipped
Row 1: "" | "" | ""  ← Skipped
Row 2: "A" | "B" | "C"  ← Detected as header
```

### 4. Page Headers
```
Row 0: "Page 5" | "" | "" | ""  ← Removed (sparse)
Row 1: "Item" | "Description" | "Cost" | "Total"  ← Detected as header
```

---

## Configuration (Advanced)

If you need to adjust the detection thresholds, modify the constants in `TableHeaderCleaner.find_real_header_row()`:

```python
# Current defaults:
fill_ratio >= 0.7           # 70% of cells must be filled
max_cell_length < 80        # Cells must be < 80 chars
non_empty_count >= max(2, cols * 0.5)  # At least 2 cells or 50% filled
```

To change:
```python
# More strict (fewer false positives):
fill_ratio >= 0.8           # 80% filled
max_cell_length < 60        # Shorter cells

# More lenient (catch more cases):
fill_ratio >= 0.6           # 60% filled
max_cell_length < 100       # Longer cells allowed
```

---

## Testing

Run the test suite:
```bash
python3 test_table_header_cleaning.py
```

Expected output:
```
================================================================================
TEST SUMMARY
================================================================================
Passed: 5/5
Failed: 0/5

✓ ALL TESTS PASSED!
```

---

## Compatibility

- ✅ Works with Camelot table detection
- ✅ Compatible with structure validation
- ✅ Integrates with existing filtering
- ✅ No breaking changes to XML format

---

## Performance

- ⚡ **Fast** - Runs in O(rows × cols) time
- 💾 **Memory efficient** - No data duplication
- 🔄 **Seamless** - Integrated into existing pipeline

---

## Status

| Item | Status |
|------|--------|
| Implementation | ✅ Complete |
| Testing | ✅ Complete (5/5 tests pass) |
| Integration | ✅ Complete |
| Documentation | ✅ Complete |

**The feature is ready and working!**

---

## Summary

**Problem:** Tables sometimes include captions, paragraphs, or headers above the real table  
**Solution:** Automatic detection and removal of extra rows  
**Result:** Clean tables with correct structure  
**Tests:** 5/5 passing ✅  

---

**Last Updated:** December 5, 2025  
**Author:** Integrated based on user-provided logic  
**Status:** ✅ Production Ready
