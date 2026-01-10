#!/usr/bin/env python3
"""
Package Existing DocBook XML

This script packages an existing DocBook XML file with its multimedia folder
into a RittDoc-compliant ZIP package.

Usage:
    python package_existing_xml.py <xml_file> [--multimedia <folder>] [--output <zip>]

Examples:
    # Package Book.XML with multimedia in same directory
    python package_existing_xml.py /path/to/Book.XML

    # Specify multimedia folder explicitly
    python package_existing_xml.py /path/to/Book.XML --multimedia /path/to/multimedia

    # Specify output ZIP path
    python package_existing_xml.py /path/to/Book.XML --output /path/to/output.zip

    # Run full compliance pipeline after packaging
    python package_existing_xml.py /path/to/Book.XML --validate
"""

import argparse
import sys
from pathlib import Path

from lxml import etree

from package import (
    package_docbook,
    make_file_fetcher,
    BOOK_DOCTYPE_PUBLIC_DEFAULT,
    BOOK_DOCTYPE_SYSTEM_DEFAULT,
)


def parse_xml_file(xml_path: Path) -> etree._Element:
    """Parse an XML file and return the root element."""
    parser = etree.XMLParser(
        recover=True,
        remove_blank_text=False,
        strip_cdata=False,
        resolve_entities=False,
    )

    tree = etree.parse(str(xml_path), parser)
    return tree.getroot()


def package_existing_xml(
    xml_path: Path,
    multimedia_path: Path,
    output_path: Path,
    run_validation: bool = False,
    dtd_path: Path = None,
) -> Path:
    """
    Package an existing DocBook XML with multimedia into a ZIP.

    Args:
        xml_path: Path to the DocBook XML file (e.g., Book.XML)
        multimedia_path: Path to the multimedia folder
        output_path: Path for the output ZIP file
        run_validation: Whether to run the compliance pipeline after packaging
        dtd_path: Path to DTD file for validation (optional)

    Returns:
        Path to the created ZIP file
    """
    print("=" * 70)
    print("PACKAGE EXISTING DOCBOOK XML")
    print("=" * 70)
    print(f"XML File:    {xml_path}")
    print(f"Multimedia:  {multimedia_path}")
    print(f"Output:      {output_path}")
    print("=" * 70)

    # Verify inputs exist
    if not xml_path.exists():
        raise FileNotFoundError(f"XML file not found: {xml_path}")

    if not multimedia_path.exists():
        print(f"⚠ Warning: Multimedia folder not found: {multimedia_path}")
        print("  Continuing without multimedia...")
        search_paths = []
    else:
        search_paths = [multimedia_path]
        file_count = len(list(multimedia_path.glob("*")))
        print(f"  Found {file_count} files in multimedia folder")

    # Parse the XML
    print("\n→ Parsing XML file...")
    root = parse_xml_file(xml_path)
    root_tag = root.tag.split('}')[-1] if '}' in root.tag else root.tag
    print(f"  Root element: <{root_tag}>")

    # Create media fetcher
    media_fetcher = make_file_fetcher(search_paths) if search_paths else None

    # Package the DocBook
    result_path = package_docbook(
        root=root,
        root_name=root_tag,
        dtd_system=BOOK_DOCTYPE_SYSTEM_DEFAULT,
        zip_path=str(output_path),
        media_fetcher=media_fetcher,
        book_doctype_public=BOOK_DOCTYPE_PUBLIC_DEFAULT,
        book_doctype_system=BOOK_DOCTYPE_SYSTEM_DEFAULT,
        source_format="xml",  # Indicate this is existing XML
    )

    print(f"\n✓ Package created: {result_path}")

    # Run validation if requested
    if run_validation:
        print("\n" + "=" * 70)
        print("RUNNING COMPLIANCE VALIDATION")
        print("=" * 70)

        try:
            from rittdoc_compliance_pipeline import RittDocCompliancePipeline

            if dtd_path is None:
                # Try to find DTD in common locations
                possible_dtd_paths = [
                    Path("RittDocBook.dtd"),
                    Path("dtd/RittDocBook.dtd"),
                    Path(__file__).parent / "RittDocBook.dtd",
                    Path(__file__).parent / "dtd" / "RittDocBook.dtd",
                ]
                for p in possible_dtd_paths:
                    if p.exists():
                        dtd_path = p
                        break

            if dtd_path and dtd_path.exists():
                pipeline = RittDocCompliancePipeline(dtd_path)
                compliant_zip = output_path.parent / f"{output_path.stem}_compliant.zip"
                success = pipeline.run(result_path, compliant_zip)

                if success:
                    print(f"\n✓ Compliant package: {compliant_zip}")
                    return compliant_zip
                else:
                    print(f"\n⚠ Validation completed with issues. Check the output.")
            else:
                print("⚠ DTD file not found. Skipping validation.")
                print("  Use --dtd to specify the DTD path.")

        except ImportError as e:
            print(f"⚠ Could not import compliance pipeline: {e}")
            print("  Validation skipped.")

    return result_path


def main():
    parser = argparse.ArgumentParser(
        description="Package existing DocBook XML with multimedia into RittDoc ZIP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/Book.XML
  %(prog)s /path/to/Book.XML --multimedia /path/to/images
  %(prog)s /path/to/Book.XML --output package.zip --validate
        """
    )

    parser.add_argument(
        "xml_file",
        type=Path,
        help="Path to the DocBook XML file (e.g., Book.XML)"
    )

    parser.add_argument(
        "-m", "--multimedia",
        type=Path,
        default=None,
        help="Path to multimedia folder (default: 'multimedia' in same directory as XML)"
    )

    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output ZIP file path (default: <xml_stem>.zip in same directory)"
    )

    parser.add_argument(
        "-v", "--validate",
        action="store_true",
        help="Run RittDoc compliance validation after packaging"
    )

    parser.add_argument(
        "--dtd",
        type=Path,
        default=None,
        help="Path to DTD file for validation"
    )

    args = parser.parse_args()

    # Resolve paths
    xml_path = args.xml_file.resolve()

    # Default multimedia path
    if args.multimedia:
        multimedia_path = args.multimedia.resolve()
    else:
        # Try common multimedia folder names
        xml_dir = xml_path.parent
        for name in ["multimedia", "MultiMedia", "images", "media"]:
            candidate = xml_dir / name
            if candidate.exists():
                multimedia_path = candidate
                break
        else:
            multimedia_path = xml_dir / "multimedia"

    # Default output path
    if args.output:
        output_path = args.output.resolve()
    else:
        output_path = xml_path.parent / f"{xml_path.stem}.zip"

    try:
        result = package_existing_xml(
            xml_path=xml_path,
            multimedia_path=multimedia_path,
            output_path=output_path,
            run_validation=args.validate,
            dtd_path=args.dtd.resolve() if args.dtd else None,
        )

        print(f"\n✓ Done! Output: {result}")
        return 0

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
