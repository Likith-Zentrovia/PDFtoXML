#!/usr/bin/env python3
"""
XSLT Transformer Module for RittDoc DTD Compliance

This module provides functions to transform XML documents using XSLT stylesheets
to ensure compliance with the RittDoc DTD.
"""

import logging
from pathlib import Path
from typing import Optional, Union

from lxml import etree

logger = logging.getLogger(__name__)

# Path to the XSLT stylesheet
ROOT = Path(__file__).resolve().parent
XSLT_DIR = ROOT / "xslt"
RITTDOC_COMPLIANCE_XSLT = XSLT_DIR / "rittdoc_compliance.xslt"


def load_xslt_transform(xslt_path: Path) -> etree.XSLT:
    """
    Load an XSLT stylesheet from file.

    Args:
        xslt_path: Path to the XSLT stylesheet file

    Returns:
        Compiled XSLT transform

    Raises:
        FileNotFoundError: If XSLT file doesn't exist
        etree.XSLTParseError: If XSLT is malformed
    """
    if not xslt_path.exists():
        raise FileNotFoundError(f"XSLT stylesheet not found: {xslt_path}")

    logger.info(f"Loading XSLT stylesheet: {xslt_path}")
    xslt_doc = etree.parse(str(xslt_path))
    transform = etree.XSLT(xslt_doc)
    logger.info("XSLT stylesheet loaded successfully")
    return transform


def apply_xslt_transform(
    xml_input: Union[Path, etree._Element],
    xslt_transform: etree.XSLT,
    output_path: Optional[Path] = None
) -> etree._Element:
    """
    Apply an XSLT transformation to an XML document.

    Args:
        xml_input: Either a Path to XML file or an lxml Element
        xslt_transform: Compiled XSLT transform
        output_path: Optional path to write transformed XML

    Returns:
        Transformed XML as lxml Element

    Raises:
        etree.XSLTApplyError: If transformation fails
    """
    # Parse input
    if isinstance(xml_input, Path):
        logger.info(f"Parsing XML input: {xml_input}")
        xml_doc = etree.parse(str(xml_input))
    elif isinstance(xml_input, etree._Element):
        xml_doc = xml_input
    else:
        raise TypeError("xml_input must be Path or lxml Element")

    # Apply transformation
    logger.info("Applying XSLT transformation...")
    try:
        result = xslt_transform(xml_doc)
    except etree.XSLTApplyError as e:
        logger.error(f"XSLT transformation failed: {e}")
        logger.error(f"Error log: {xslt_transform.error_log}")
        raise

    # Check for transformation errors
    if xslt_transform.error_log:
        logger.warning("XSLT transformation completed with warnings:")
        for entry in xslt_transform.error_log:
            logger.warning(f"  {entry}")

    logger.info("XSLT transformation completed successfully")

    # Write to file if output path specified
    if output_path:
        logger.info(f"Writing transformed XML to: {output_path}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result.write(
            str(output_path),
            encoding="UTF-8",
            xml_declaration=True,
            pretty_print=True
        )
        logger.info("Transformed XML written successfully")

    return result.getroot()


def transform_to_rittdoc_compliance(
    xml_input: Union[Path, etree._Element],
    output_path: Optional[Path] = None
) -> etree._Element:
    """
    Transform XML to RittDoc DTD compliant format.

    This is a convenience function that loads the RittDoc compliance XSLT
    and applies it to the input XML.

    Args:
        xml_input: Either a Path to XML file or an lxml Element
        output_path: Optional path to write transformed XML

    Returns:
        Transformed XML as lxml Element
    """
    logger.info("Starting RittDoc DTD compliance transformation")

    # Load the XSLT transform
    xslt_transform = load_xslt_transform(RITTDOC_COMPLIANCE_XSLT)

    # Apply transformation
    result = apply_xslt_transform(xml_input, xslt_transform, output_path)

    logger.info("RittDoc DTD compliance transformation completed")
    return result


def validate_and_transform(
    xml_path: Path,
    output_path: Path,
    dtd_path: Optional[Path] = None
) -> tuple[bool, str]:
    """
    Transform XML to RittDoc compliance and validate against DTD.

    Args:
        xml_path: Path to input XML file
        output_path: Path to write transformed and validated XML
        dtd_path: Optional path to DTD file for validation

    Returns:
        Tuple of (is_valid, report_message)
    """
    logger.info(f"Validating and transforming: {xml_path}")

    # Apply XSLT transformation
    try:
        transformed = transform_to_rittdoc_compliance(xml_path, output_path)
    except Exception as e:
        error_msg = f"XSLT transformation failed: {e}"
        logger.error(error_msg)
        return False, error_msg

    # Validate against DTD if provided
    if dtd_path and dtd_path.exists():
        logger.info(f"Validating against DTD: {dtd_path}")
        try:
            dtd = etree.DTD(str(dtd_path))
            is_valid = dtd.validate(transformed)

            if is_valid:
                return True, "Transformation and DTD validation passed"
            else:
                error_lines = "\n".join(str(err) for err in dtd.error_log)
                return False, f"DTD validation failed:\n{error_lines}"
        except Exception as e:
            error_msg = f"DTD validation error: {e}"
            logger.error(error_msg)
            return False, error_msg

    return True, "Transformation completed (DTD validation skipped)"


if __name__ == "__main__":
    """
    Command-line interface for testing XSLT transformations.
    """
    import argparse
    import sys

    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s"
    )

    parser = argparse.ArgumentParser(
        description="Transform XML to RittDoc DTD compliant format"
    )
    parser.add_argument("input", help="Input XML file")
    parser.add_argument("output", help="Output XML file")
    parser.add_argument(
        "--validate",
        help="DTD file for validation",
        default=None
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    dtd_path = Path(args.validate) if args.validate else None

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    try:
        is_valid, message = validate_and_transform(input_path, output_path, dtd_path)
        print(message)
        sys.exit(0 if is_valid else 1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
