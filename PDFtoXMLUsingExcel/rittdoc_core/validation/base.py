"""
Base Validation Classes
=======================

Abstract base classes for validation framework. Extend these classes
to create validators for different schema types (DTD, XSD, Schematron, etc.).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """
    Container for validation results.

    Attributes:
        is_valid: Whether validation passed
        error_count: Total number of errors
        warning_count: Total number of warnings
        errors: List of error dictionaries with keys:
            - file: Source file name
            - line: Line number (optional)
            - column: Column number (optional)
            - type: Error type/category
            - message: Error description
            - severity: 'Error', 'Warning', or 'Info'
        metadata: Additional validation metadata
    """
    is_valid: bool = True
    error_count: int = 0
    warning_count: int = 0
    errors: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_error(self,
                  file: str,
                  message: str,
                  error_type: str = "Validation Error",
                  line: Optional[int] = None,
                  column: Optional[int] = None,
                  severity: str = "Error") -> None:
        """
        Add an error to the result.

        Args:
            file: Source file name
            message: Error description
            error_type: Error type/category
            line: Line number (optional)
            column: Column number (optional)
            severity: 'Error', 'Warning', or 'Info'
        """
        self.errors.append({
            'file': file,
            'line': line,
            'column': column,
            'type': error_type,
            'message': message,
            'severity': severity,
        })

        if severity == "Error":
            self.error_count += 1
            self.is_valid = False
        elif severity == "Warning":
            self.warning_count += 1

    def merge(self, other: 'ValidationResult') -> None:
        """Merge another result into this one."""
        self.errors.extend(other.errors)
        self.error_count += other.error_count
        self.warning_count += other.warning_count
        if not other.is_valid:
            self.is_valid = False
        self.metadata.update(other.metadata)

    def get_errors_by_file(self) -> Dict[str, List[Dict]]:
        """Group errors by file."""
        by_file: Dict[str, List[Dict]] = {}
        for error in self.errors:
            file = error['file']
            if file not in by_file:
                by_file[file] = []
            by_file[file].append(error)
        return by_file

    def get_errors_by_type(self) -> Dict[str, int]:
        """Get error counts by type."""
        by_type: Dict[str, int] = {}
        for error in self.errors:
            error_type = error['type']
            by_type[error_type] = by_type.get(error_type, 0) + 1
        return by_type

    def summary(self) -> str:
        """Generate a text summary of validation results."""
        if self.is_valid:
            return "Validation PASSED - No errors found"

        lines = [
            f"Validation FAILED - {self.error_count} error(s), {self.warning_count} warning(s)",
            "",
            "Errors by type:",
        ]

        for error_type, count in sorted(self.get_errors_by_type().items(), key=lambda x: -x[1]):
            lines.append(f"  {error_type}: {count}")

        lines.extend(["", "Errors by file:"])
        for file, file_errors in sorted(self.get_errors_by_file().items()):
            lines.append(f"  {file}: {len(file_errors)} error(s)")

        return "\n".join(lines)


class BaseValidator(ABC):
    """
    Abstract base class for validators.

    Subclass this to create validators for different schema types
    (DTD, XSD, Schematron, custom rules, etc.).

    Example:
        class MyDTDValidator(BaseValidator):
            def __init__(self, dtd_path: Path):
                self.dtd_path = dtd_path
                self._load_schema()

            def _load_schema(self):
                from lxml import etree
                self.dtd = etree.DTD(str(self.dtd_path))

            def validate_file(self, file_path: Path) -> ValidationResult:
                result = ValidationResult()
                # ... validation logic ...
                return result

            def validate_package(self, package_path: Path) -> ValidationResult:
                # ... package validation logic ...
                pass
    """

    @abstractmethod
    def validate_file(self, file_path: Path, **kwargs) -> ValidationResult:
        """
        Validate a single file.

        Args:
            file_path: Path to the file to validate
            **kwargs: Additional validation options

        Returns:
            ValidationResult with validation outcome
        """
        pass

    @abstractmethod
    def validate_package(self, package_path: Path, **kwargs) -> ValidationResult:
        """
        Validate a package (ZIP file or directory).

        Args:
            package_path: Path to the package
            **kwargs: Additional validation options

        Returns:
            ValidationResult with validation outcome
        """
        pass

    def validate_element(self, element: Any, file_context: str = "element") -> ValidationResult:
        """
        Validate an XML element (optional to implement).

        Args:
            element: XML element to validate
            file_context: Context string for error reporting

        Returns:
            ValidationResult with validation outcome
        """
        # Default implementation - subclasses can override
        raise NotImplementedError("Element validation not supported by this validator")

    def validate_string(self, xml_string: str, file_context: str = "string") -> ValidationResult:
        """
        Validate XML from a string (optional to implement).

        Args:
            xml_string: XML content as string
            file_context: Context string for error reporting

        Returns:
            ValidationResult with validation outcome
        """
        # Default implementation - subclasses can override
        raise NotImplementedError("String validation not supported by this validator")

    @property
    def schema_type(self) -> str:
        """Return the type of schema this validator uses (e.g., 'DTD', 'XSD')."""
        return "Unknown"

    @property
    def schema_path(self) -> Optional[Path]:
        """Return the path to the schema file (if applicable)."""
        return None
