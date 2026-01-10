#!/usr/bin/env python3
"""
Font Roles Auto-Derivation Script

This script analyzes a unified XML document to:
1. Extract comprehensive font statistics (family, size, counts, page distribution)
2. Auto-derive font roles (book.title, chapter, section, subsection, paragraph)
3. Extract Table of Contents section into a separate TOC.xml file

The output JSON contains:
- sizes_asc: All sizes in ascending order
- roles_by_size: Role assignments and statistics for each size
- font_families: Statistics grouped by font family
- font_statistics: Detailed per-size statistics with page info
- toc_info: Information about the extracted TOC (if found)
"""

import argparse
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from typing import Dict, List, Any, Optional, Tuple


def _f(x) -> float:
    """Safely convert to float, returning nan on failure."""
    try:
        return float(x)
    except:
        return math.nan


def get_font_family(font_name: str) -> str:
    """
    Extract font family from font name.

    Examples:
        "TimesNewRoman-Bold" -> "TimesNewRoman"
        "Arial-BoldItalic" -> "Arial"
        "Helvetica" -> "Helvetica"
    """
    if not font_name:
        return "Unknown"

    # Remove common suffixes like -Bold, -Italic, -BoldItalic, etc.
    family = re.sub(r'[-_]?(Bold|Italic|BoldItalic|Light|Medium|Regular|Oblique|Black|Thin|Semi|Extra|Cond|Condensed)+$', '', font_name, flags=re.IGNORECASE)

    # Clean up any trailing dashes or underscores
    family = family.rstrip('-_')

    return family if family else font_name


def extract_toc_section(
    root: ET.Element,
    toc_start_size: float,
    toc_start_page: int,
    output_path: str,
    font_info_map: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Extract Table of Contents section into a separate XML file.

    Starts from the TOC heading and continues until a text element
    with the same size or larger is encountered.

    Args:
        root: The unified XML root element
        toc_start_size: Font size of the "Table of Contents" heading
        toc_start_page: Page number where TOC starts
        output_path: Path to save the TOC.xml file
        font_info_map: Dictionary mapping font IDs to font information (size, family, color)

    Returns:
        Dictionary with TOC extraction info
    """
    toc_root = ET.Element("toc", {
        "start_page": str(toc_start_page),
        "heading_size": str(toc_start_size)
    })

    found_toc_start = False
    toc_entries = []
    end_page = toc_start_page
    end_reason = "end_of_document"

    for page_elem in root.findall(".//page"):
        page_num = int(page_elem.get("number", "0"))

        # Skip pages before TOC
        if page_num < toc_start_page:
            continue

        page_toc = ET.SubElement(toc_root, "page", {"number": str(page_num)})

        for text_elem in page_elem.findall(".//text"):
            text_content = "".join(text_elem.itertext()).strip()

            # Get font size - FIXED: Use font_info_map to look up size from font ID
            font_id = text_elem.get("font")
            if font_id in font_info_map:
                font_size = font_info_map[font_id]["size"]
                font_family = font_info_map[font_id]["family"]
            else:
                # Fallback to direct attribute (for edge cases where font ID is missing)
                font_size = _f(text_elem.get("font_size") or text_elem.get("size", "0"))
                font_family = "Unknown"

            # Check if this is the TOC heading
            if not found_toc_start:
                if "table of contents" in text_content.lower():
                    found_toc_start = True
                    entry = ET.SubElement(page_toc, "entry", {
                        "type": "heading",
                        "size": str(font_size),
                        "family": font_family
                    })
                    entry.text = text_content
                    toc_entries.append({
                        "page": page_num,
                        "text": text_content,
                        "size": font_size,
                        "family": font_family,
                        "type": "heading"
                    })
                continue

            # We're inside TOC - check if we hit the end
            # End when we find text with same size or bigger as TOC heading
            if font_size >= toc_start_size and text_content:
                # Skip if it's still part of TOC header area
                if page_num == toc_start_page:
                    # Allow some leeway on the same page
                    pass
                else:
                    # Check if this looks like a new chapter/section heading
                    # (not a page number or short reference)
                    if len(text_content) > 3 and not text_content.isdigit():
                        end_page = page_num
                        end_reason = f"found_heading: {text_content[:50]}"
                        break

            # Add to TOC
            entry = ET.SubElement(page_toc, "entry", {
                "size": str(font_size),
                "family": font_family
            })
            entry.text = text_content
            toc_entries.append({
                "page": page_num,
                "text": text_content,
                "size": font_size,
                "family": font_family
            })
        else:
            # Continue to next page
            continue
        # Break from outer loop if inner loop broke
        break

    # Write TOC.xml
    tree = ET.ElementTree(toc_root)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)

    return {
        "toc_file": output_path,
        "start_page": toc_start_page,
        "end_page": end_page,
        "heading_size": toc_start_size,
        "entry_count": len(toc_entries),
        "end_reason": end_reason
    }


def analyze_fonts(args) -> Dict[str, Any]:
    """
    Perform comprehensive font analysis on the unified XML.

    Returns dictionary with:
    - Font statistics grouped by family
    - Per-size statistics with page distribution
    - Role assignments
    - TOC extraction info
    """
    root = ET.parse(args.reading_xml).getroot()

    # Build map from font id → (size, family)
    font_info_map: Dict[str, Dict[str, Any]] = {}
    for fs in root.findall(".//fontspec"):
        fid = fs.get("id")
        size_attr = fs.get("size")
        family_attr = fs.get("family") or fs.get("name", "")
        color_attr = fs.get("color", "")

        size_val = _f(size_attr)
        if fid is not None and not math.isnan(size_val):
            font_info_map[fid] = {
                "size": round(size_val, args.size_decimals),
                "family": get_font_family(family_attr),
                "color": color_attr
            }

    # Statistics structures
    # size_key -> {count, pages: set, page_list: [], family: Counter}
    size_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "count": 0,
        "pages": set(),
        "families": Counter()
    })

    # family -> {sizes: {size -> count}, total_count, pages: set}
    family_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "sizes": Counter(),
        "total_count": 0,
        "pages": set()
    })

    # For TOC detection
    toc_info: Optional[Dict[str, Any]] = None
    toc_heading_size: Optional[float] = None
    toc_heading_page: Optional[int] = None

    # Process all text elements
    for page_elem in root.findall(".//page"):
        page_num = int(page_elem.get("number", "0"))

        for text_elem in page_elem.findall(".//text"):
            font_id = text_elem.get("font")
            text_content = "".join(text_elem.itertext()).strip()

            # Get font info
            if font_id in font_info_map:
                font_info = font_info_map[font_id]
                size = font_info["size"]
                family = font_info["family"]
            else:
                size_hint = text_elem.get("font_size") or text_elem.get("size")
                size = _f(size_hint)
                if math.isnan(size):
                    continue
                size = round(size, args.size_decimals)
                family = "Unknown"

            # Skip very small sizes
            if size < args.ignore_small:
                continue

            sz_key = str(size)

            # Update size statistics
            size_stats[sz_key]["count"] += 1
            size_stats[sz_key]["pages"].add(page_num)
            size_stats[sz_key]["families"][family] += 1

            # Update family statistics
            family_stats[family]["sizes"][sz_key] += 1
            family_stats[family]["total_count"] += 1
            family_stats[family]["pages"].add(page_num)

            # Check for Table of Contents
            if toc_heading_size is None and "table of contents" in text_content.lower():
                toc_heading_size = size
                toc_heading_page = page_num
                print(f"  Found 'Table of Contents' on page {page_num} with size {size}")

    # Convert sets to sorted lists for JSON serialization
    font_statistics = {}
    for sz_key, stats in size_stats.items():
        page_list = sorted(stats["pages"])
        font_statistics[sz_key] = {
            "count": stats["count"],
            "page_count": len(page_list),
            "pages": page_list,
            "families": dict(stats["families"].most_common())
        }

    # Convert family stats
    font_families = {}
    for family, stats in family_stats.items():
        page_list = sorted(stats["pages"])
        font_families[family] = {
            "total_count": stats["total_count"],
            "page_count": len(page_list),
            "pages": page_list,
            "sizes": dict(stats["sizes"].most_common())
        }

    # Sort sizes ascending
    tiers = sorted((float(k), font_statistics[k]["count"], k) for k in font_statistics.keys())
    sizes_sorted = [sz_key for _, _, sz_key in tiers]

    # Default all sizes to paragraph role
    roles = {
        sz_key: {
            "role": "paragraph",
            "count": font_statistics[sz_key]["count"],
            "page_count": font_statistics[sz_key]["page_count"],
            "top_family": list(font_statistics[sz_key]["families"].keys())[0] if font_statistics[sz_key]["families"] else None
        }
        for sz_key in sizes_sorted
    }

    # Derive roles if we have data
    if tiers:
        tiers_desc = sorted(tiers, key=lambda item: item[0], reverse=True)

        # Find body size (most common)
        body_size_val, _, _ = max(
            ((float(sz_key), font_statistics[sz_key]["count"], sz_key) for sz_key in font_statistics.keys()),
            key=lambda item: item[1]
        )
        body_size = body_size_val
        heading_threshold = max(body_size + 1.0, body_size * 1.2)

        # 1) Largest size → book title
        largest_size_val, _, largest_key = tiers_desc[0]
        roles[largest_key]["role"] = "book.title"

        # 2) Chapter tier
        min_chapter_count_primary = 5
        min_chapter_count_fallback = 2
        pool_size = max(args.max_roles, 6)
        heading_pool = tiers_desc[1: 1 + pool_size]

        chapter_candidates = [
            (size_val, count, sz_key)
            for size_val, count, sz_key in heading_pool
            if size_val >= heading_threshold and count >= min_chapter_count_primary
        ]
        if not chapter_candidates:
            chapter_candidates = [
                (size_val, count, sz_key)
                for size_val, count, sz_key in heading_pool
                if size_val >= heading_threshold and count >= min_chapter_count_fallback
            ]

        chapter_size_val = largest_size_val
        if chapter_candidates:
            chapter_candidates.sort(key=lambda item: (-item[0], -item[1]))
            chapter_size_val, _, chapter_key = chapter_candidates[0]
            roles[chapter_key]["role"] = "chapter"

        # 3) Section tier
        min_section_count = 5
        section_candidates = [
            (size_val, count, sz_key)
            for size_val, count, sz_key in tiers_desc
            if roles[sz_key]["role"] == "paragraph"
            and size_val < chapter_size_val
            and size_val >= max(body_size + 0.5, body_size * 1.1)
            and count >= min_section_count
        ]

        section_size_val = chapter_size_val
        if section_candidates:
            section_candidates.sort(key=lambda item: (-item[0], -item[1]))
            section_size_val, _, section_key = section_candidates[0]
            roles[section_key]["role"] = "section"

        # 4) Subsection tier
        min_subsection_count = 5
        subsection_candidates = [
            (size_val, count, sz_key)
            for size_val, count, sz_key in tiers_desc
            if roles[sz_key]["role"] == "paragraph"
            and size_val < section_size_val
            and size_val >= max(body_size, body_size * 1.05)
            and count >= min_subsection_count
        ]

        if subsection_candidates:
            subsection_candidates.sort(key=lambda item: (-item[0], -item[1]))
            _, _, subsection_key = subsection_candidates[0]
            roles[subsection_key]["role"] = "subsection"

    # Extract TOC if found
    if toc_heading_size is not None and toc_heading_page is not None:
        base_dir = os.path.dirname(args.reading_xml)
        base_name = os.path.splitext(os.path.basename(args.reading_xml))[0].replace("_unified", "")
        toc_output_path = os.path.join(base_dir, f"{base_name}_TOC.xml")

        print(f"  Extracting TOC section...")
        toc_info = extract_toc_section(root, toc_heading_size, toc_heading_page, toc_output_path, font_info_map)
        print(f"  ✓ TOC extracted: {toc_info['entry_count']} entries, pages {toc_info['start_page']}-{toc_info['end_page']}")

    # Build output
    output = {
        "sizes_asc": sizes_sorted,
        "roles_by_size": roles,
        "font_families": font_families,
        "font_statistics": font_statistics,
        "notes": {
            "largest_is_book_title": bool(tiers),
            "mapping_source": "auto-derived per book",
            "body_size": str(body_size) if tiers else None,
            "heading_threshold": str(heading_threshold) if tiers else None
        }
    }

    if toc_info:
        output["toc_info"] = toc_info

    return output


def main():
    ap = argparse.ArgumentParser(
        description="Auto-derive font roles per book with comprehensive statistics"
    )
    ap.add_argument("reading_xml", help="Path to unified XML file")
    ap.add_argument("--out", required=True, help="Output path for font roles JSON")
    ap.add_argument("--max-roles", type=int, default=5, help="Max distinct size tiers to classify")
    ap.add_argument("--size-decimals", type=int, default=2, help="Round sizes to this many decimals")
    ap.add_argument("--ignore-small", type=float, default=6.0, help="Ignore sizes below (pts)")
    args = ap.parse_args()

    print(f"Analyzing fonts in: {args.reading_xml}")

    output = analyze_fonts(args)

    # Write JSON output
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"✓ Wrote font roles → {args.out}")

    # Print summary
    print("\n=== Font Analysis Summary ===")
    print(f"Total font sizes found: {len(output['sizes_asc'])}")
    print(f"Font families found: {len(output['font_families'])}")

    print("\n--- Sizes by Role ---")
    for sz_key in reversed(output['sizes_asc']):
        role_info = output['roles_by_size'][sz_key]
        role = role_info['role']
        count = role_info['count']
        page_count = role_info['page_count']
        if role != 'paragraph':
            print(f"  {sz_key}pt: {role} (count={count}, pages={page_count})")

    print("\n--- Font Families ---")
    for family, stats in sorted(output['font_families'].items(), key=lambda x: -x[1]['total_count']):
        print(f"  {family}: {stats['total_count']} occurrences across {stats['page_count']} pages")
        print(f"    Sizes: {', '.join(f'{sz}pt({cnt})' for sz, cnt in list(stats['sizes'].items())[:5])}")

    if output.get('toc_info'):
        print(f"\n--- Table of Contents ---")
        print(f"  Extracted to: {output['toc_info']['toc_file']}")
        print(f"  Pages: {output['toc_info']['start_page']}-{output['toc_info']['end_page']}")
        print(f"  Entries: {output['toc_info']['entry_count']}")


if __name__ == "__main__":
    main()
