"""
XSLT Transformer
================

XSLT transformation utilities for document conversion pipelines.
Supports loading, applying, and chaining XSLT transformations.
"""

import logging
from pathlib import Path
from typing import Optional, Union, List

try:
    from lxml import etree
    LXML_AVAILABLE = True
except ImportError:
    LXML_AVAILABLE = False

logger = logging.getLogger(__name__)


def load_xslt_transform(xslt_path: Path) -> 'etree.XSLT':
    """
    Load an XSLT stylesheet from file.

    Args:
        xslt_path: Path to the XSLT stylesheet file

    Returns:
        Compiled XSLT transform

    Raises:
        FileNotFoundError: If XSLT file doesn't exist
        ImportError: If lxml is not available
        etree.XSLTParseError: If XSLT is malformed
    """
    if not LXML_AVAILABLE:
        raise ImportError("lxml is required for XSLT transformations")

    if not xslt_path.exists():
        raise FileNotFoundError(f"XSLT stylesheet not found: {xslt_path}")

    logger.info(f"Loading XSLT stylesheet: {xslt_path}")
    xslt_doc = etree.parse(str(xslt_path))
    transform = etree.XSLT(xslt_doc)
    logger.info("XSLT stylesheet loaded successfully")
    return transform


def apply_xslt_transform(
    xml_input: Union[Path, 'etree._Element', 'etree._ElementTree'],
    xslt_transform: 'etree.XSLT',
    output_path: Optional[Path] = None,
    **params
) -> 'etree._Element':
    """
    Apply an XSLT transformation to an XML document.

    Args:
        xml_input: Path to XML file, lxml Element, or ElementTree
        xslt_transform: Compiled XSLT transform
        output_path: Optional path to write transformed XML
        **params: XSLT parameters to pass to the transformation

    Returns:
        Transformed XML as lxml Element

    Raises:
        etree.XSLTApplyError: If transformation fails
    """
    if not LXML_AVAILABLE:
        raise ImportError("lxml is required for XSLT transformations")

    # Parse input
    if isinstance(xml_input, Path):
        logger.info(f"Parsing XML input: {xml_input}")
        xml_doc = etree.parse(str(xml_input))
    elif isinstance(xml_input, etree._Element):
        xml_doc = etree.ElementTree(xml_input)
    elif isinstance(xml_input, etree._ElementTree):
        xml_doc = xml_input
    else:
        raise TypeError("xml_input must be Path, lxml Element, or ElementTree")

    # Convert params to XSLT string params
    xslt_params = {k: etree.XSLT.strparam(str(v)) for k, v in params.items()}

    # Apply transformation
    logger.info("Applying XSLT transformation...")
    try:
        result = xslt_transform(xml_doc, **xslt_params)
    except etree.XSLTApplyError as e:
        logger.error(f"XSLT transformation failed: {e}")
        logger.error(f"Error log: {xslt_transform.error_log}")
        raise

    # Check for transformation warnings
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


class XSLTTransformer:
    """
    XSLT transformation utility class.

    Supports loading multiple stylesheets and chaining transformations.

    Example:
        transformer = XSLTTransformer()
        transformer.load("normalize.xslt")
        transformer.load("compliance.xslt")
        result = transformer.transform(xml_path, output_path)
    """

    def __init__(self):
        """Initialize transformer with empty stylesheet list."""
        if not LXML_AVAILABLE:
            raise ImportError("lxml is required for XSLT transformations")

        self._transforms: List[tuple] = []  # List of (name, transform) tuples

    def load(self, xslt_path: Path, name: Optional[str] = None) -> 'XSLTTransformer':
        """
        Load an XSLT stylesheet.

        Args:
            xslt_path: Path to XSLT file
            name: Optional name for the transform

        Returns:
            Self for method chaining
        """
        transform = load_xslt_transform(xslt_path)
        name = name or xslt_path.stem
        self._transforms.append((name, transform))
        return self

    def load_string(self, xslt_string: str, name: str = "inline") -> 'XSLTTransformer':
        """
        Load XSLT from a string.

        Args:
            xslt_string: XSLT content as string
            name: Name for the transform

        Returns:
            Self for method chaining
        """
        xslt_doc = etree.fromstring(xslt_string.encode('utf-8'))
        transform = etree.XSLT(xslt_doc)
        self._transforms.append((name, transform))
        return self

    def clear(self) -> 'XSLTTransformer':
        """
        Clear all loaded transforms.

        Returns:
            Self for method chaining
        """
        self._transforms.clear()
        return self

    def transform(self,
                  xml_input: Union[Path, 'etree._Element'],
                  output_path: Optional[Path] = None,
                  **params) -> 'etree._Element':
        """
        Apply all loaded transformations in sequence.

        Args:
            xml_input: Path to XML file or lxml Element
            output_path: Optional path to write final result
            **params: Parameters to pass to all transformations

        Returns:
            Final transformed XML as lxml Element
        """
        if not self._transforms:
            raise ValueError("No XSLT transforms loaded")

        # Parse initial input
        if isinstance(xml_input, Path):
            current = etree.parse(str(xml_input)).getroot()
        else:
            current = xml_input

        # Apply each transform in sequence
        for name, transform in self._transforms:
            logger.info(f"Applying transform: {name}")
            result = transform(etree.ElementTree(current), **{
                k: etree.XSLT.strparam(str(v)) for k, v in params.items()
            })
            current = result.getroot()

        # Write final output
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            tree = etree.ElementTree(current)
            tree.write(
                str(output_path),
                encoding="UTF-8",
                xml_declaration=True,
                pretty_print=True
            )
            logger.info(f"Final output written to: {output_path}")

        return current

    def validate_and_transform(self,
                              xml_path: Path,
                              output_path: Path,
                              dtd_path: Optional[Path] = None) -> tuple:
        """
        Transform XML and validate against DTD.

        Args:
            xml_path: Path to input XML file
            output_path: Path to write transformed XML
            dtd_path: Optional path to DTD file for validation

        Returns:
            Tuple of (is_valid, report_message)
        """
        logger.info(f"Validating and transforming: {xml_path}")

        # Apply transformations
        try:
            transformed = self.transform(xml_path, output_path)
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

    @property
    def transform_count(self) -> int:
        """Return number of loaded transforms."""
        return len(self._transforms)

    @property
    def transform_names(self) -> List[str]:
        """Return list of loaded transform names."""
        return [name for name, _ in self._transforms]
