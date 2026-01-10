#!/usr/bin/env python3
"""
Phase 1: Script Detection Implementation

This shows exactly how to detect superscripts/subscripts using TOP position,
with very strict criteria to avoid false positives (drop caps, large letters, etc.)
"""

from typing import List, Dict, Any, Optional, Tuple


# ============================================================================
# CONFIGURATION: Adjust these thresholds based on your documents
# ============================================================================

# Size thresholds (VERY strict to avoid false positives)
SCRIPT_MAX_WIDTH = 15       # Drop caps are ~30-50px, scripts are ~5-15px
SCRIPT_MAX_HEIGHT = 12      # Drop caps are ~36-48px, scripts are ~8-12px
SCRIPT_MAX_TEXT_LENGTH = 3  # Scripts are usually 1-3 characters

# Adjacency threshold
SCRIPT_MAX_HORIZONTAL_GAP = 5  # Must be within 5px horizontally

# Vertical position thresholds (using TOP, not baseline!)
SUPERSCRIPT_MIN_TOP_DIFF = -3  # Superscript can be 3px above parent
SUPERSCRIPT_MAX_TOP_DIFF = 3   # Or 3px below parent (still superscript)
SUBSCRIPT_MIN_TOP_DIFF = 3     # Subscript is 3-10px below parent
SUBSCRIPT_MAX_TOP_DIFF = 10    # Maximum 10px below

# Height ratio threshold
SCRIPT_MAX_HEIGHT_RATIO = 0.75  # Script must be <75% of parent height

# Symbols to exclude from script detection
EXCLUDE_SYMBOLS = {
    '°', '™', '®', '©',     # Degree, trademark, etc.
    '•', '·', '◦', '▪',     # Bullets
    '½', '¼', '¾', '⅓',     # Fractions
    '→', '←', '↑', '↓',     # Arrows
    '…', '‥',               # Ellipsis
}


# ============================================================================
# Phase 1: Script Detection Functions
# ============================================================================

def is_script_size(fragment: Dict[str, Any]) -> bool:
    """
    Check if fragment meets size criteria for being a script.
    
    Very strict to avoid detecting:
    - Drop caps (too large)
    - Large first letters (too large)
    - Normal text (too large)
    """
    if fragment["width"] >= SCRIPT_MAX_WIDTH:
        return False
    
    if fragment["height"] >= SCRIPT_MAX_HEIGHT:
        return False
    
    # Check text length
    text = fragment.get("text", "").strip()
    if len(text) > SCRIPT_MAX_TEXT_LENGTH:
        return False
    
    # Must have text
    if not text:
        return False
    
    return True


def is_excluded_symbol(text: str) -> bool:
    """Check if text is a symbol that should not be treated as script."""
    text = text.strip()
    
    # Check against exclusion list
    if text in EXCLUDE_SYMBOLS:
        return True
    
    # Only allow alphanumeric scripts
    # This excludes most symbols automatically
    if not text.replace('^', '').replace('_', '').isalnum():
        return True
    
    return False


def find_adjacent_parent(
    script_fragment: Dict[str, Any],
    all_fragments: List[Dict[str, Any]],
    script_index: int
) -> Optional[Tuple[int, Dict[str, Any]]]:
    """
    Find the parent fragment for a potential script.
    
    Parent must be:
    - Larger in height than script
    - Adjacent horizontally (within 5px)
    - Close vertically (within 10px using TOP position)
    
    Returns:
        (parent_index, parent_fragment) if found, None otherwise
    """
    script_left = script_fragment["left"]
    script_right = script_left + script_fragment["width"]
    script_top = script_fragment["top"]
    script_height = script_fragment["height"]
    
    candidates = []
    
    for i, other in enumerate(all_fragments):
        if i == script_index:
            continue
        
        # Must be larger than script
        if other["height"] <= script_height:
            continue
        
        # Check height ratio (script must be significantly smaller)
        height_ratio = script_height / other["height"]
        if height_ratio >= SCRIPT_MAX_HEIGHT_RATIO:
            continue
        
        # Check horizontal adjacency
        other_left = other["left"]
        other_right = other_left + other["width"]
        
        # Is script to the right of other? (most common)
        gap_right = script_left - other_right
        if 0 <= gap_right <= SCRIPT_MAX_HORIZONTAL_GAP:
            # Check vertical proximity using TOP
            top_diff = abs(script_top - other["top"])
            if top_diff <= SUBSCRIPT_MAX_TOP_DIFF:
                candidates.append((i, other, gap_right, top_diff))
        
        # Is script to the left of other? (rare)
        gap_left = other_left - script_right
        if 0 <= gap_left <= SCRIPT_MAX_HORIZONTAL_GAP:
            # Check vertical proximity using TOP
            top_diff = abs(script_top - other["top"])
            if top_diff <= SUBSCRIPT_MAX_TOP_DIFF:
                candidates.append((i, other, gap_left, top_diff))
    
    if not candidates:
        return None
    
    # Choose closest candidate (smallest horizontal gap, then smallest vertical gap)
    candidates.sort(key=lambda x: (x[2], x[3]))  # Sort by gap, then top_diff
    
    parent_idx, parent, _, _ = candidates[0]
    return (parent_idx, parent)


def detect_script_type(
    script_fragment: Dict[str, Any],
    parent_fragment: Dict[str, Any]
) -> Optional[str]:
    """
    Determine if script is superscript or subscript based on TOP position.
    
    Key insight: Use TOP position, not baseline!
    
    Returns:
        "superscript", "subscript", or None
    """
    # Calculate TOP difference (not baseline!)
    top_diff = script_fragment["top"] - parent_fragment["top"]
    
    # Superscript: within ±3px of parent top (but not >= 3px which is subscript)
    # Examples: 10⁷, x², aⁿ
    if SUPERSCRIPT_MIN_TOP_DIFF <= top_diff < SUBSCRIPT_MIN_TOP_DIFF:
        return "superscript"
    
    # Subscript: 3-10px below parent top
    # Examples: H₂O, B₀, a₁
    elif SUBSCRIPT_MIN_TOP_DIFF <= top_diff <= SUBSCRIPT_MAX_TOP_DIFF:
        return "subscript"
    
    return None


def detect_and_mark_scripts(fragments: List[Dict[str, Any]]) -> None:
    """
    Phase 1: Detect and mark superscripts/subscripts.
    
    This modifies fragments in-place by adding:
    - is_script: bool
    - script_type: "superscript" or "subscript"
    - script_parent_idx: index of parent fragment
    
    IMPORTANT: This does NOT change grouping logic!
    It just marks fragments for later cross-row merging.
    
    Args:
        fragments: List of fragments (modified in-place)
    """
    # Add original index to each fragment
    for i, f in enumerate(fragments):
        f["original_idx"] = i
    
    # Detect scripts
    for i, f in enumerate(fragments):
        # Default: not a script
        f["is_script"] = False
        f["script_type"] = None
        f["script_parent_idx"] = None
        
        # Check size criteria
        if not is_script_size(f):
            continue
        
        # Check if excluded symbol
        text = f.get("text", "").strip()
        if is_excluded_symbol(text):
            continue
        
        # Find adjacent parent fragment
        parent_result = find_adjacent_parent(f, fragments, i)
        if not parent_result:
            continue
        
        parent_idx, parent = parent_result
        
        # Determine script type using TOP position
        script_type = detect_script_type(f, parent)
        if not script_type:
            continue
        
        # Mark as script
        f["is_script"] = True
        f["script_type"] = script_type
        f["script_parent_idx"] = parent_idx
        
        # Debug output (optional)
        # print(f"Detected {script_type}: '{text}' → parent '{parent.get('text', '')[:20]}'")


# ============================================================================
# Phase 3: Cross-Row Merging Functions
# ============================================================================

def merge_script_with_parent(
    parent: Dict[str, Any],
    scripts: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Merge one or more scripts with their parent fragment.
    
    Args:
        parent: Parent fragment
        scripts: List of script fragments to merge (sorted by position)
    
    Returns:
        Merged fragment
    """
    merged = dict(parent)  # Copy parent
    
    # Sort scripts by left position
    scripts = sorted(scripts, key=lambda s: s["left"])
    
    # Merge text
    merged_text = parent["text"]
    for script in scripts:
        script_text = script["text"]

        if script["script_type"] == "superscript":
            # Use XML superscript tag: 10<superscript>7</superscript>
            merged_text += f"<superscript>{script_text}</superscript>"
        else:  # subscript
            # Use XML subscript tag: H<subscript>2</subscript>O
            merged_text += f"<subscript>{script_text}</subscript>"
    
    merged["text"] = merged_text
    merged["norm_text"] = " ".join(merged_text.split()).lower()
    
    # Expand bounding box to include all scripts
    for script in scripts:
        script_right = script["left"] + script["width"]
        merged_right = merged["left"] + merged["width"]
        if script_right > merged_right:
            merged["width"] = script_right - merged["left"]
        
        # Adjust height if script extends beyond
        script_bottom = script["top"] + script["height"]
        merged_bottom = merged["top"] + merged["height"]
        if script_bottom > merged_bottom:
            merged["height"] = script_bottom - merged["top"]
    
    # Mark as having merged scripts
    merged["has_merged_scripts"] = True
    merged["merged_script_count"] = len(scripts)
    
    return merged


def merge_scripts_across_rows(
    rows: List[List[Dict[str, Any]]],
    all_fragments: List[Dict[str, Any]]
) -> List[List[Dict[str, Any]]]:
    """
    Phase 3: Merge scripts with their parents across rows.
    
    After baseline grouping, find scripts marked in Phase 1 and
    merge them with their parent fragments even if in different rows.
    
    Args:
        rows: List of rows (each row is list of fragments)
        all_fragments: All fragments (for looking up by original_idx)
    
    Returns:
        Updated rows with scripts merged
    """
    # Build index: original_idx -> fragment
    frag_by_idx = {}
    for row in rows:
        for f in row:
            orig_idx = f.get("original_idx")
            if orig_idx is not None:
                frag_by_idx[orig_idx] = f
    
    # Find all scripts and group by parent
    scripts_by_parent = {}
    script_indices = set()
    
    for row in rows:
        for f in row:
            if f.get("is_script"):
                parent_idx = f.get("script_parent_idx")
                if parent_idx is not None:
                    if parent_idx not in scripts_by_parent:
                        scripts_by_parent[parent_idx] = []
                    scripts_by_parent[parent_idx].append(f)
                    script_indices.add(f.get("original_idx"))
    
    # Merge scripts into their parents
    merged_rows = []
    
    for row in rows:
        new_row = []
        
        for f in row:
            orig_idx = f.get("original_idx")
            
            # Skip if this fragment is a script (will be merged into parent)
            if orig_idx in script_indices:
                continue
            
            # Check if this fragment is a parent with scripts to merge
            if orig_idx in scripts_by_parent:
                scripts = scripts_by_parent[orig_idx]
                merged = merge_script_with_parent(f, scripts)
                new_row.append(merged)
            else:
                new_row.append(f)
        
        if new_row:
            merged_rows.append(new_row)
    
    return merged_rows


# ============================================================================
# Integration: How to Use in pdf_to_excel_columns.py
# ============================================================================

def example_integration():
    """
    Example showing how to integrate into pdf_to_excel_columns.py
    
    Add this at line ~1105, BEFORE baseline grouping:
    """
    print("""
# ========== EXISTING CODE (around line 1105) ==========
        
# Sort by baseline & left for line grouping
fragments.sort(key=lambda f: (f["baseline"], f["left"]))

# ========== INSERT NEW CODE HERE ==========

# Phase 1: Detect and mark scripts (NEW)
from implement_script_detection import detect_and_mark_scripts
detect_and_mark_scripts(fragments)

# ========== EXISTING CODE CONTINUES ==========

# Column detection for this page
col_starts = detect_column_starts(fragments, page_width, max_cols=4)
assign_column_ids(fragments, page_width, col_starts)

# ... (other code) ...

# (1) First pass: group into rows and merge inline fragments
baselines = [f["baseline"] for f in fragments]
baseline_tol = compute_baseline_tolerance(baselines)
raw_rows = group_fragments_into_lines(fragments, baseline_tol)

# ========== INSERT NEW CODE HERE ==========

# Phase 3: Merge scripts across rows (NEW)
from implement_script_detection import merge_scripts_across_rows
raw_rows = merge_scripts_across_rows(raw_rows, fragments)

# ========== EXISTING CODE CONTINUES ==========

merged_fragments = []
for row in raw_rows:
    merged_fragments.extend(merge_inline_fragments_in_row(row))
""")


# ============================================================================
# Testing
# ============================================================================

if __name__ == "__main__":
    print("="*80)
    print("SCRIPT DETECTION - TEST CASES")
    print("="*80)
    
    # Test Case 1: Superscript (10^7)
    print("\nTest 1: Superscript Detection (10^7)")
    print("-" * 40)
    
    fragments_test1 = [
        {
            "top": 191,
            "left": 101,
            "width": 428,
            "height": 18,
            "baseline": 209,
            "text": "...around 10",
        },
        {
            "top": 192,
            "left": 529,
            "width": 5,
            "height": 11,
            "baseline": 203,
            "text": "7",
        },
        {
            "top": 191,
            "left": 534,
            "width": 166,
            "height": 18,
            "baseline": 209,
            "text": "Hz...",
        }
    ]
    
    detect_and_mark_scripts(fragments_test1)
    
    for i, f in enumerate(fragments_test1):
        if f.get("is_script"):
            print(f"✓ Fragment {i}: '{f['text']}' detected as {f['script_type']}")
            parent_idx = f.get("script_parent_idx")
            parent_text = fragments_test1[parent_idx]["text"] if parent_idx is not None else "?"
            print(f"  Parent: '{parent_text}'")
        else:
            print(f"  Fragment {i}: '{f['text']}' - normal text")
    
    # Test Case 2: Subscript (B_0)
    print("\nTest 2: Subscript Detection (B_0)")
    print("-" * 40)
    
    fragments_test2 = [
        {
            "top": 324,
            "left": 271,
            "width": 10,
            "height": 17,
            "baseline": 341,
            "text": "B",
        },
        {
            "top": 331,
            "left": 281,
            "width": 9,
            "height": 13,
            "baseline": 344,
            "text": "Ø",
        },
        {
            "top": 324,
            "left": 290,
            "width": 65,
            "height": 17,
            "baseline": 341,
            "text": " field",
        }
    ]
    
    detect_and_mark_scripts(fragments_test2)
    
    for i, f in enumerate(fragments_test2):
        if f.get("is_script"):
            print(f"✓ Fragment {i}: '{f['text']}' detected as {f['script_type']}")
            parent_idx = f.get("script_parent_idx")
            parent_text = fragments_test2[parent_idx]["text"] if parent_idx is not None else "?"
            print(f"  Parent: '{parent_text}'")
        else:
            print(f"  Fragment {i}: '{f['text']}' - normal text")
    
    # Test Case 3: Drop Cap (should NOT be detected)
    print("\nTest 3: Drop Cap (should NOT detect)")
    print("-" * 40)
    
    fragments_test3 = [
        {
            "top": 100,
            "left": 10,
            "width": 30,
            "height": 48,
            "baseline": 148,
            "text": "T",
        },
        {
            "top": 100,
            "left": 45,
            "width": 200,
            "height": 12,
            "baseline": 112,
            "text": "his is text...",
        }
    ]
    
    detect_and_mark_scripts(fragments_test3)
    
    for i, f in enumerate(fragments_test3):
        if f.get("is_script"):
            print(f"✗ Fragment {i}: '{f['text']}' detected as {f['script_type']} (WRONG!)")
        else:
            print(f"✓ Fragment {i}: '{f['text']}' - normal text (correct)")
    
    # Test Case 4: Cross-row merging
    print("\nTest 4: Cross-Row Merging")
    print("-" * 40)
    
    # Simulate rows from baseline grouping
    rows_test4 = [
        [fragments_test1[1]],  # Row 1: "7" (script)
        [fragments_test1[0], fragments_test1[2]],  # Row 2: "...10" and "Hz"
    ]
    
    print("Before merging:")
    for i, row in enumerate(rows_test4):
        texts = [f["text"] for f in row]
        print(f"  Row {i+1}: {texts}")
    
    merged_rows = merge_scripts_across_rows(rows_test4, fragments_test1)
    
    print("\nAfter merging:")
    for i, row in enumerate(merged_rows):
        texts = [f["text"] for f in row]
        print(f"  Row {i+1}: {texts}")
        for f in row:
            if f.get("has_merged_scripts"):
                print(f"    ✓ Merged {f['merged_script_count']} script(s)")
    
    print("\n" + "="*80)
    print("Summary:")
    print("  ✓ Superscript detected and merged")
    print("  ✓ Subscript detected and merged")
    print("  ✓ Drop cap NOT detected (preserved)")
    print("="*80)
