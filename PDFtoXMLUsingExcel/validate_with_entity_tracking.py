#!/usr/bin/env python3
"""
Enhanced DTD Validation with Entity Tracking

This script validates Book.XML and all referenced chapter files, reporting
errors with the actual source filename and line number (not just "Book.XML").

Key improvements:
- Validates each chapter file individually
- Reports actual chapter filename in errors (e.g., ch0007.xml)
- Shows correct line numbers within each chapter file
- Aggregates all errors into a single comprehensive report
"""

import re
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from lxml import etree
    LXML_AVAILABLE = True
except ImportError:
    LXML_AVAILABLE = False
    print("Warning: lxml not available. Install with: pip install lxml")

from validation_report import ValidationReportGenerator, ValidationError


class EntityTrackingValidator:
    """Validates XML with entity tracking to report actual source files"""

    def __init__(self, dtd_path: Path):
        self.dtd_path = dtd_path
        self.dtd = None
        if LXML_AVAILABLE:
            self.dtd = etree.DTD(str(dtd_path))

    def extract_entity_declarations(self, book_xml_path: Path) -> Dict[str, str]:
        """
        Extract entity declarations from Book.XML DOCTYPE.

        Returns:
            Dictionary mapping entity names to filenames (e.g., {'ch0001': 'ch0001.xml'})
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
            chapter_filename: Display name for the file (e.g., "ch0007.xml")

        Returns:
            List of ValidationError objects with correct filename and line numbers
        """
        if not LXML_AVAILABLE:
            return []

        errors = []

        try:
            # Parse the chapter XML
            parser = etree.XMLParser(dtd_validation=False, resolve_entities=False)
            tree = etree.parse(str(chapter_path), parser)

            # Validate against DTD
            if not self.dtd.validate(tree):
                # Extract errors from DTD error log
                for error in self.dtd.error_log:
                    # Get line number (this is now accurate for the chapter file)
                    line_num = error.line if hasattr(error, 'line') else None
                    col_num = error.column if hasattr(error, 'column') else None

                    # Get error message
                    message = str(error.message) if hasattr(error, 'message') else str(error)

                    # Categorize error
                    error_type = self._categorize_error(message)

                    # Create readable description
                    description = self._make_readable(message)

                    errors.append(ValidationError(
                        xml_file=chapter_filename,  # Actual chapter filename!
                        line_number=line_num,        # Actual line in chapter file!
                        column_number=col_num,
                        error_type=error_type,
                        error_description=description,
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

    def _validate_book_xml(
        self,
        book_xml_path: Path,
        base_dir: Path
    ) -> List[ValidationError]:
        """
        Validate Book.XML with entity resolution to check overall document structure.

        This catches errors like:
        - Wrong element order (title after toc, etc.)
        - Invalid elements at book level (mediaobject as direct child of book)
        - Missing required elements

        Args:
            book_xml_path: Path to Book.XML
            base_dir: Directory containing chapter files (for entity resolution)

        Returns:
            List of ValidationError objects
        """
        if not LXML_AVAILABLE:
            return []

        errors = []

        try:
            # Parse with entity resolution enabled
            # This expands all chapter entities so we validate the full document
            parser = etree.XMLParser(
                dtd_validation=False,
                resolve_entities=True,
                load_dtd=True
            )

            # Change to base_dir so entity references resolve correctly
            import os
            original_cwd = os.getcwd()
            try:
                os.chdir(base_dir)
                tree = etree.parse(str(book_xml_path), parser)
            finally:
                os.chdir(original_cwd)

            # Validate against DTD
            if not self.dtd.validate(tree):
                for error in self.dtd.error_log:
                    line_num = error.line if hasattr(error, 'line') else None
                    message = str(error.message) if hasattr(error, 'message') else str(error)

                    # Categorize the error
                    error_type = self._categorize_error(message)

                    # Clean up the error message
                    description = message.strip()

                    errors.append(ValidationError(
                        xml_file='Book.XML',
                        line_number=line_num,
                        column_number=None,
                        error_type=error_type,
                        error_description=description,
                        severity='Error'
                    ))

        except etree.XMLSyntaxError as e:
            errors.append(ValidationError(
                xml_file='Book.XML',
                line_number=e.lineno if hasattr(e, 'lineno') else None,
                column_number=None,
                error_type='XML Syntax Error',
                error_description=str(e),
                severity='Error'
            ))
        except Exception as e:
            errors.append(ValidationError(
                xml_file='Book.XML',
                line_number=None,
                column_number=None,
                error_type='Validation Error',
                error_description=f"Error validating Book.XML: {str(e)}",
                severity='Error'
            ))

        return errors

    def validate_zip_package(
        self,
        zip_path: Path,
        output_report_path: Optional[Path] = None
    ) -> ValidationReportGenerator:
        """
        Validate all XML files in a ZIP package.

        Args:
            zip_path: Path to ZIP package
            output_report_path: Optional path for Excel report output

        Returns:
            ValidationReportGenerator with all errors
        """
        report = ValidationReportGenerator()

        with tempfile.TemporaryDirectory() as tmpdir:
            extract_dir = Path(tmpdir)

            # Extract ZIP with CRC error handling
            print(f"Extracting {zip_path.name}...")
            corrupted_files = []

            with zipfile.ZipFile(zip_path, 'r') as zf:
                # Extract files individually to handle CRC errors gracefully
                for zip_info in zf.infolist():
                    try:
                        zf.extract(zip_info, extract_dir)
                    except zipfile.BadZipFile as e:
                        # Log corrupted file but continue processing
                        corrupted_files.append(zip_info.filename)
                        print(f"  Warning: Skipping corrupted file: {zip_info.filename} ({e})")

                        # Add to validation report
                        report.add_error(ValidationError(
                            xml_file=zip_info.filename,
                            line_number=None,
                            column_number=None,
                            error_type='Corrupted File',
                            error_description=f'ZIP CRC-32 error: {str(e)}',
                            severity='Warning'
                        ))
                    except Exception as e:
                        # Handle other extraction errors
                        corrupted_files.append(zip_info.filename)
                        print(f"  Warning: Failed to extract: {zip_info.filename} ({e})")

                        report.add_error(ValidationError(
                            xml_file=zip_info.filename,
                            line_number=None,
                            column_number=None,
                            error_type='Extraction Error',
                            error_description=f'Failed to extract: {str(e)}',
                            severity='Warning'
                        ))

            if corrupted_files:
                print(f"  Note: Skipped {len(corrupted_files)} corrupted/unextractable file(s)")
                print(f"  Continuing validation with successfully extracted files...")

            # Find Book.XML
            book_xml_files = list(extract_dir.rglob("Book.XML"))
            if not book_xml_files:
                print("Error: Book.XML not found in package")
                return report

            book_xml_path = book_xml_files[0]
            base_dir = book_xml_path.parent

            # Extract entity declarations
            entities = self.extract_entity_declarations(book_xml_path)
            print(f"Found {len(entities)} chapter entity references")

            # First, validate Book.XML itself (the main document structure)
            print("Validating Book.XML structure...")
            book_errors = self._validate_book_xml(book_xml_path, base_dir)
            for error in book_errors:
                report.add_error(error)
            if book_errors:
                print(f"  Book.XML: {len(book_errors)} error(s)")
            else:
                print(f"  Book.XML: ✓ Valid")

            # Validate each chapter file
            print("Validating chapter files...")
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
                    print(f"  {filename}: {len(chapter_errors)} error(s)")
                else:
                    print(f"  {filename}: ✓ Valid")

        # Generate Excel report if requested (always generate for audit purposes)
        if output_report_path:
            report.generate_excel_report(output_report_path, "RittDoc Package")
            if report.has_errors():
                print(f"\n✓ Validation report saved: {output_report_path}")
            else:
                print(f"\n✓ Validation report saved (0 errors): {output_report_path}")

        return report

    def _categorize_error(self, message: str) -> str:
        """Categorize DTD error based on message content"""
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
        """Make DTD error message more readable"""
        replacements = {
            r'Element (\w+): ': r'Element <\1> ',
            r'No declaration for element (\w+)': r'Element <\1> is not declared in the DTD',
            r'does not follow the DTD': r'does not match what the DTD expects',
        }

        readable = message
        for pattern, replacement in replacements.items():
            readable = re.sub(pattern, replacement, readable)

        return readable


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate RittDoc XML package with entity tracking"
    )
    parser.add_argument("package", help="ZIP package to validate")
    parser.add_argument(
        "--dtd",
        default="RITTDOCdtd/v1.1/RittDocBook.dtd",
        help="Path to DTD file"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output Excel report path"
    )

    args = parser.parse_args()

    if not LXML_AVAILABLE:
        print("Error: lxml is required for DTD validation")
        print("Install with: pip install lxml")
        sys.exit(1)

    package_path = Path(args.package)
    dtd_path = Path(args.dtd)

    if not package_path.exists():
        print(f"Error: Package not found: {package_path}")
        sys.exit(1)

    if not dtd_path.exists():
        print(f"Error: DTD not found: {dtd_path}")
        sys.exit(1)

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = package_path.parent / f"{package_path.stem}_validation_report.xlsx"

    # Create validator
    validator = EntityTrackingValidator(dtd_path)

    # Validate package
    print("=" * 70)
    print("RITTDOC DTD VALIDATION WITH ENTITY TRACKING")
    print("=" * 70)

    report = validator.validate_zip_package(package_path, output_path)

    # Print summary
    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)

    if report.has_errors():
        error_count = report.get_error_count()
        print(f"✗ Found {error_count} validation error(s)")

        # Group by file
        errors_by_file = {}
        for error in report.errors:
            if error.xml_file not in errors_by_file:
                errors_by_file[error.xml_file] = []
            errors_by_file[error.xml_file].append(error)

        print(f"\nErrors by file:")
        for filename in sorted(errors_by_file.keys()):
            print(f"  {filename}: {len(errors_by_file[filename])} error(s)")

        sys.exit(1)
    else:
        print("✓ No validation errors found!")
        print("✓ Package is DTD-compliant")
        sys.exit(0)


if __name__ == "__main__":
    main()
