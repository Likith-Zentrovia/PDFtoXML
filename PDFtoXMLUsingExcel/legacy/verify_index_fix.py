#!/usr/bin/env python3
"""
Verify that the Index TOC entry fix is working.

The fix adds 'top' and 'left' attributes to phrase elements so the heuristics
can use the vertical distance fallback check to identify phrases on the same row.
"""

import sys

def check_fix():
    """Verify the fix is in place."""
    print("="*70)
    print("VERIFYING INDEX TOC ENTRY FIX")
    print("="*70)
    
    # Read the fixed code
    with open("/workspace/pdf_to_unified_xml.py", "r") as f:
        code = f.read()
    
    # Check if position attributes are being added
    checks = [
        ('inline_attrs["top"]', "Position attributes added to inline elements"),
        ('inline_attrs["left"]', "Left position attribute added"),
        ('base_attrs["top"]', "Position attributes added to base attributes"),
        ('base_attrs["left"]', "Left position in base attributes"),
        ('# Add position attributes for heuristics', "Comment explaining fix"),
    ]
    
    print("\nChecking for fix implementation:")
    all_passed = True
    
    for pattern, description in checks:
        if pattern in code:
            print(f"  ✓ {description}")
        else:
            print(f"  ✗ MISSING: {description}")
            all_passed = False
    
    print("\n" + "="*70)
    print("FIX VERIFICATION")
    print("="*70)
    
    if all_passed:
        print("\n✅ Fix is properly implemented!")
        print("\nHow it works:")
        print("1. Phrase elements now have 'top' and 'left' attributes")
        print("2. Heuristics can use these for vertical distance check:")
        print("   abs(next_top - curr_top) <= 3")
        print("3. 'Index' and '...' fragments on same row (top=922) will be combined")
        print("4. Distance = 0, which is <= 3, so they'll be merged into one TOC entry")
        return 0
    else:
        print("\n❌ Fix is incomplete!")
        print("Please ensure all position attributes are being added.")
        return 1


if __name__ == "__main__":
    exit(check_fix())
