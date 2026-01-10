#!/usr/bin/env python3
"""
RittDoc DTD Validation Script

This script validates RittDoc XML packages and files against the RittDoc DTD.
It can validate:
1. Packaged ZIP files containing Book.XML
2. Individual XML files
3. Apply XSLT transformation and then validate

Usage:
    python validate_rittdoc.py package.zip
    python validate_rittdoc.py book.xml
    python validate_rittdoc.py --transform input.xml output.xml
"""

import argparse
import sys
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

# Import validation and transformation functions
from xslt_transformer import transform_to_rittdoc_compliance

ROOT = Path(__file__).resolve().parent
DTD_PATH = ROOT / "RITTDOCdtd" / "v1.1" / "RittDocBook.dtd"


def validate_xml_file(xml_path: Path, dtd_path: Path) -> tuple[bool, str]:
    """
    Validate an XML file against a DTD.

    Args:
        xml_path: Path to XML file
        dtd_path: Path to DTD file

    Returns:
        Tuple of (is_valid, report_message)
    """
    if not xml_path.exists():
        return False, f"XML file not found: {xml_path}"

    if not dtd_path.exists():
        return False, f"DTD file not found: {dtd_path}"

    try:
        # Parse XML with DTD loading enabled
        parser = etree.XMLParser(load_dtd=True, resolve_entities=True, no_network=True)
        tree = etree.parse(str(xml_path), parser)

        # Load DTD
        dtd = etree.DTD(str(dtd_path))

        # Validate
        is_valid = dtd.validate(tree)

        if is_valid:
            return True, "DTD validation passed ✓"
        else:
            error_lines = "\n".join(f"  - {err}" for err in dtd.error_log)
            return False, f"DTD validation failed:\n{error_lines}"

    except etree.XMLSyntaxError as e:
        return False, f"XML parsing failed: {e}"
    except Exception as e:
        return False, f"Validation error: {e}"


def validate_package(zip_path: Path, dtd_path: Path) -> tuple[bool, str]:
    """
    Validate Book.XML inside a ZIP package against the RittDoc DTD.

    Args:
        zip_path: Path to ZIP package
        dtd_path: Path to DTD file (falls back to package DTD if available)

    Returns:
        Tuple of (is_valid, report_message)
    """
    if not zip_path.exists():
        return False, f"Package not found: {zip_path}"

    try:
        with tempfile.TemporaryDirectory(prefix="validate_") as tmp:
            extract_dir = Path(tmp)

            # Extract ZIP
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            # Find Book.XML
            book_xml = extract_dir / "Book.XML"
            if not book_xml.exists():
                return False, "Book.XML not found in package"

            # Try to use DTD from package first
            package_dtd = extract_dir / "RITTDOCdtd" / "v1.1" / "RittDocBook.dtd"
            if package_dtd.exists():
                dtd_to_use = package_dtd
                print(f"Using DTD from package: {package_dtd}")
            else:
                dtd_to_use = dtd_path
                print(f"Using external DTD: {dtd_path}")

            # Validate
            return validate_xml_file(book_xml, dtd_to_use)

    except zipfile.BadZipFile:
        return False, f"Invalid ZIP file: {zip_path}"
    except Exception as e:
        return False, f"Package validation error: {e}"


def transform_and_validate(
    input_xml: Path,
    output_xml: Path,
    dtd_path: Path
) -> tuple[bool, str]:
    """
    Apply XSLT transformation and validate the result.

    Args:
        input_xml: Path to input XML file
        output_xml: Path to write transformed XML
        dtd_path: Path to DTD file for validation

    Returns:
        Tuple of (is_valid, report_message)
    """
    if not input_xml.exists():
        return False, f"Input XML not found: {input_xml}"

    try:
        # Apply XSLT transformation
        print(f"Transforming {input_xml} → {output_xml}")
        transform_to_rittdoc_compliance(input_xml, output_xml)
        print("✓ XSLT transformation completed")

        # Validate transformed XML
        print(f"Validating against DTD: {dtd_path}")
        is_valid, message = validate_xml_file(output_xml, dtd_path)

        if is_valid:
            return True, f"Transformation and validation successful ✓\nOutput: {output_xml}\n{message}"
        else:
            return False, f"Transformation completed but validation failed:\n{message}"

    except Exception as e:
        return False, f"Transformation error: {e}"


def main():
    parser = argparse.ArgumentParser(
        description="Validate RittDoc XML packages and files against RittDoc DTD",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Validate a ZIP package:
    python validate_rittdoc.py Output/mybook.zip

  Validate an XML file:
    python validate_rittdoc.py book.xml

  Transform and validate:
    python validate_rittdoc.py --transform input.xml output.xml

  Use custom DTD:
    python validate_rittdoc.py --dtd custom.dtd book.xml
        """
    )

    parser.add_argument(
        "input",
        help="Input file (ZIP package or XML file)"
    )
    parser.add_argument(
        "output",
        nargs="?",
        help="Output file (required with --transform)"
    )
    parser.add_argument(
        "--transform",
        action="store_true",
        help="Apply XSLT transformation before validation"
    )
    parser.add_argument(
        "--dtd",
        help="Path to DTD file (default: RITTDOCdtd/v1.1/RittDocBook.dtd)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed validation output"
    )

    args = parser.parse_args()

    # Set up DTD path
    if args.dtd:
        dtd_path = Path(args.dtd).resolve()
    else:
        dtd_path = DTD_PATH

    if not dtd_path.exists():
        print(f"Error: DTD file not found: {dtd_path}", file=sys.stderr)
        sys.exit(1)

    input_path = Path(args.input).resolve()

    # Handle transformation mode
    if args.transform:
        if not args.output:
            print("Error: --transform requires both input and output arguments", file=sys.stderr)
            sys.exit(1)

        output_path = Path(args.output).resolve()
        is_valid, message = transform_and_validate(input_path, output_path, dtd_path)

    # Handle package validation
    elif input_path.suffix.lower() == ".zip":
        print(f"Validating package: {input_path}")
        is_valid, message = validate_package(input_path, dtd_path)

    # Handle XML file validation
    else:
        print(f"Validating XML file: {input_path}")
        is_valid, message = validate_xml_file(input_path, dtd_path)

    # Print results
    print("\n" + "=" * 70)
    if is_valid:
        print("VALIDATION RESULT: PASSED ✓")
    else:
        print("VALIDATION RESULT: FAILED ✗")
    print("=" * 70)

    if args.verbose or not is_valid:
        print(message)

    sys.exit(0 if is_valid else 1)


if __name__ == "__main__":
    main()
