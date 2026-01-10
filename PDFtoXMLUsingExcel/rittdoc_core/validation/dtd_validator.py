"""
DTD Validator
=============

DTD-specific validation implementation with entity tracking
for accurate error reporting in document packages.
"""

import re
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional
import logging

try:
    from lxml import etree
    LXML_AVAILABLE = True
except ImportError:
    LXML_AVAILABLE = False

from rittdoc_core.validation.base import BaseValidator, ValidationResult
from rittdoc_core.validation.report import ValidationReportGenerator, ValidationError

logger = logging.getLogger(__name__)


class DTDValidator(BaseValidator):
    """
    Basic DTD validator for single XML files.

    Example:
        validator = DTDValidator(Path("schema.dtd"))
        result = validator.validate_file(Path("document.xml"))
        if not result.is_valid:
            print(result.summary())
    """

    def __init__(self, dtd_path: Path):
        """
        Initialize DTD validator.

        Args:
            dtd_path: Path to the DTD file

        Raises:
            FileNotFoundError: If DTD file doesn't exist
            ImportError: If lxml is not available
        """
        if not LXML_AVAILABLE:
            raise ImportError(
                "lxml is required for DTD validation. "
                "Install with: pip install lxml"
            )

        if not dtd_path.exists():
            raise FileNotFoundError(f"DTD file not found: {dtd_path}")

        self._dtd_path = dtd_path
        self._dtd = etree.DTD(str(dtd_path))

    @property
    def schema_type(self) -> str:
        return "DTD"

    @property
    def schema_path(self) -> Path:
        return self._dtd_path

    def validate_file(self, file_path: Path, **kwargs) -> ValidationResult:
        """
        Validate a single XML file against the DTD.

        Args:
            file_path: Path to XML file
            **kwargs: Additional options (not used)

        Returns:
            ValidationResult with validation outcome
        """
        result = ValidationResult()
        file_context = file_path.name

        try:
            parser = etree.XMLParser(dtd_validation=False, resolve_entities=False)
            tree = etree.parse(str(file_path), parser)

            if not self._dtd.validate(tree):
                for error in self._dtd.error_log:
                    result.add_error(
                        file=file_context,
                        message=self._make_readable(str(error.message)),
                        error_type=self._categorize_error(str(error.message)),
                        line=error.line if hasattr(error, 'line') else None,
                        column=error.column if hasattr(error, 'column') else None,
                        severity="Error"
                    )

        except etree.XMLSyntaxError as e:
            result.add_error(
                file=file_context,
                message=str(e),
                error_type="XML Syntax Error",
                line=e.lineno if hasattr(e, 'lineno') else None,
                severity="Error"
            )
        except Exception as e:
            result.add_error(
                file=file_context,
                message=f"Validation error: {str(e)}",
                error_type="Validation Error",
                severity="Error"
            )

        return result

    def validate_package(self, package_path: Path, **kwargs) -> ValidationResult:
        """
        Validate all XML files in a package.

        For packages without entity declarations, validates each XML file independently.

        Args:
            package_path: Path to ZIP package
            **kwargs: Additional options

        Returns:
            ValidationResult with combined validation outcome
        """
        result = ValidationResult()

        with tempfile.TemporaryDirectory() as tmpdir:
            extract_dir = Path(tmpdir)

            with zipfile.ZipFile(package_path, 'r') as zf:
                zf.extractall(extract_dir)

            # Find all XML files
            xml_files = list(extract_dir.rglob("*.xml")) + list(extract_dir.rglob("*.XML"))

            for xml_file in xml_files:
                file_result = self.validate_file(xml_file)
                # Update file context to be relative to package
                for error in file_result.errors:
                    error['file'] = xml_file.name
                result.merge(file_result)

        return result

    def validate_element(self, element, file_context: str = "element") -> ValidationResult:
        """Validate an lxml Element against the DTD."""
        result = ValidationResult()

        if not self._dtd.validate(element):
            for error in self._dtd.error_log:
                result.add_error(
                    file=file_context,
                    message=self._make_readable(str(error.message)),
                    error_type=self._categorize_error(str(error.message)),
                    line=error.line if hasattr(error, 'line') else None,
                    severity="Error"
                )

        return result

    def _categorize_error(self, message: str) -> str:
        """Categorize DTD error based on message content."""
        message_lower = message.lower()

        if 'no declaration' in message_lower or 'not declared' in message_lower:
            return 'Undeclared Element'
        elif 'does not follow' in message_lower or 'content model' in message_lower:
            return 'Invalid Content Model'
        elif 'not allowed' in message_lower or 'unexpected' in message_lower:
            return 'Invalid Element'
        elif 'required attribute' in message_lower or 'missing' in message_lower:
            return 'Missing Attribute'
        elif 'invalid attribute' in message_lower:
            return 'Invalid Attribute'
        elif 'empty' in message_lower:
            return 'Empty Element Error'
        else:
            return 'DTD Validation Error'

    def _make_readable(self, message: str) -> str:
        """Make DTD error message more readable."""
        replacements = {
            r'Element (\w+): ': r'Element <\1> ',
            r'No declaration for element (\w+)': r'Element <\1> is not declared in the DTD',
            r'does not follow the DTD': r'does not match what the DTD expects',
        }

        readable = message
        for pattern, replacement in replacements.items():
            readable = re.sub(pattern, replacement, readable)

        return readable


class EntityTrackingValidator(DTDValidator):
    """
    Enhanced DTD validator with entity tracking for document packages.

    Validates Book.XML and all referenced chapter files, reporting
    errors with the actual source filename and line number.

    Example:
        validator = EntityTrackingValidator(Path("RittDocBook.dtd"))
        report = validator.validate_zip_package(Path("book.zip"))
        report.generate_excel_report(Path("validation_report.xlsx"))
    """

    def extract_entity_declarations(self, book_xml_path: Path) -> Dict[str, str]:
        """
        Extract entity declarations from Book.XML DOCTYPE.

        Args:
            book_xml_path: Path to Book.XML file

        Returns:
            Dictionary mapping entity names to filenames
        """
        entities = {}

        with open(book_xml_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Find DOCTYPE declaration
        doctype_match = re.search(r'<!DOCTYPE[^>]+\[(.*?)\]>', content, re.DOTALL)
        if not doctype_match:
            return entities

        doctype_content = doctype_match.group(1)

        # Extract entity declarations: <!ENTITY ch0001 SYSTEM "ch0001.xml">
        entity_pattern = r'<!ENTITY\s+(\w+)\s+SYSTEM\s+"([^"]+)">'
        for match in re.finditer(entity_pattern, doctype_content):
            entity_name = match.group(1)
            filename = match.group(2)
            entities[entity_name] = filename

        return entities

    def validate_chapter_file(
        self,
        chapter_path: Path,
        chapter_filename: str
    ) -> List[ValidationError]:
        """
        Validate a single chapter XML file against the DTD.

        Args:
            chapter_path: Path to chapter XML file
            chapter_filename: Display name for the file

        Returns:
            List of ValidationError objects with correct filename and line numbers
        """
        errors = []

        try:
            parser = etree.XMLParser(dtd_validation=False, resolve_entities=False)
            tree = etree.parse(str(chapter_path), parser)

            if not self._dtd.validate(tree):
                for error in self._dtd.error_log:
                    line_num = error.line if hasattr(error, 'line') else None
                    col_num = error.column if hasattr(error, 'column') else None
                    message = str(error.message) if hasattr(error, 'message') else str(error)

                    errors.append(ValidationError(
                        xml_file=chapter_filename,
                        line_number=line_num,
                        column_number=col_num,
                        error_type=self._categorize_error(message),
                        error_description=self._make_readable(message),
                        severity='Error'
                    ))

        except etree.XMLSyntaxError as e:
            errors.append(ValidationError(
                xml_file=chapter_filename,
                line_number=e.lineno if hasattr(e, 'lineno') else None,
                column_number=None,
                error_type='XML Syntax Error',
                error_description=str(e),
                severity='Error'
            ))
        except Exception as e:
            errors.append(ValidationError(
                xml_file=chapter_filename,
                line_number=None,
                column_number=None,
                error_type='Validation Error',
                error_description=f"Error validating {chapter_filename}: {str(e)}",
                severity='Error'
            ))

        return errors

    def validate_zip_package(
        self,
        zip_path: Path,
        output_report_path: Optional[Path] = None,
        book_xml_name: str = "Book.XML"
    ) -> ValidationReportGenerator:
        """
        Validate all XML files in a ZIP package with entity tracking.

        Args:
            zip_path: Path to ZIP package
            output_report_path: Optional path for Excel report output
            book_xml_name: Name of the main book file (default: Book.XML)

        Returns:
            ValidationReportGenerator with all errors
        """
        report = ValidationReportGenerator()

        with tempfile.TemporaryDirectory() as tmpdir:
            extract_dir = Path(tmpdir)

            # Extract ZIP with CRC error handling
            logger.info(f"Extracting {zip_path.name}...")
            corrupted_files = []

            with zipfile.ZipFile(zip_path, 'r') as zf:
                for zip_info in zf.infolist():
                    try:
                        zf.extract(zip_info, extract_dir)
                    except zipfile.BadZipFile as e:
                        corrupted_files.append(zip_info.filename)
                        logger.warning(f"Skipping corrupted file: {zip_info.filename}")
                        report.add_error(ValidationError(
                            xml_file=zip_info.filename,
                            line_number=None,
                            column_number=None,
                            error_type='Corrupted File',
                            error_description=f'ZIP CRC-32 error: {str(e)}',
                            severity='Warning'
                        ))
                    except Exception as e:
                        corrupted_files.append(zip_info.filename)
                        logger.warning(f"Failed to extract: {zip_info.filename}")
                        report.add_error(ValidationError(
                            xml_file=zip_info.filename,
                            line_number=None,
                            column_number=None,
                            error_type='Extraction Error',
                            error_description=f'Failed to extract: {str(e)}',
                            severity='Warning'
                        ))

            if corrupted_files:
                logger.info(f"Skipped {len(corrupted_files)} corrupted file(s)")

            # Find Book.XML
            book_xml_files = list(extract_dir.rglob(book_xml_name))
            if not book_xml_files:
                logger.error(f"{book_xml_name} not found in package")
                report.add_error(ValidationError(
                    xml_file=book_xml_name,
                    line_number=None,
                    column_number=None,
                    error_type='Missing File',
                    error_description=f'{book_xml_name} not found in package',
                    severity='Error'
                ))
                return report

            book_xml_path = book_xml_files[0]
            base_dir = book_xml_path.parent

            # Extract entity declarations
            entities = self.extract_entity_declarations(book_xml_path)
            logger.info(f"Found {len(entities)} chapter entity references")

            # Validate each chapter file
            logger.info("Validating chapter files...")
            for entity_name, filename in sorted(entities.items()):
                chapter_path = base_dir / filename

                if not chapter_path.exists():
                    report.add_error(ValidationError(
                        xml_file=filename,
                        line_number=None,
                        column_number=None,
                        error_type='Missing File',
                        error_description=f'Referenced chapter file not found: {filename}',
                        severity='Error'
                    ))
                    continue

                # Validate this chapter
                chapter_errors = self.validate_chapter_file(chapter_path, filename)

                # Add errors to report
                for error in chapter_errors:
                    report.add_error(error)

                if chapter_errors:
                    logger.debug(f"{filename}: {len(chapter_errors)} error(s)")
                else:
                    logger.debug(f"{filename}: Valid")

        # Generate Excel report if requested
        if output_report_path and report.has_errors():
            report.generate_excel_report(output_report_path, zip_path.stem)
            logger.info(f"Validation report saved: {output_report_path}")

        return report

    def validate_package(self, package_path: Path, **kwargs) -> ValidationResult:
        """
        Validate a package (wrapper for validate_zip_package).

        Args:
            package_path: Path to ZIP package
            **kwargs: Additional options

        Returns:
            ValidationResult with validation outcome
        """
        report = self.validate_zip_package(package_path)

        # Convert ValidationReportGenerator to ValidationResult
        result = ValidationResult()
        for error in report.errors:
            result.add_error(
                file=error.xml_file,
                message=error.error_description,
                error_type=error.error_type,
                line=error.line_number,
                column=error.column_number,
                severity=error.severity
            )

        return result
