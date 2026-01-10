"""
Base Fixer Classes
==================

Abstract base classes for the fixing framework. Extend these classes
to create fixers for different validation rule sets.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


@dataclass
class FixResult:
    """
    Container for fix results.

    Attributes:
        files_processed: Number of files processed
        files_fixed: Number of files that had fixes applied
        total_fixes: Total number of individual fixes applied
        fixes_by_type: Count of fixes by type
        fix_descriptions: Detailed descriptions of all fixes
        verification_items: Items requiring manual verification
        metadata: Additional metadata about the fixing process
    """
    files_processed: int = 0
    files_fixed: int = 0
    total_fixes: int = 0
    fixes_by_type: Dict[str, int] = field(default_factory=dict)
    fix_descriptions: List[str] = field(default_factory=list)
    verification_items: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_fix(self, fix_type: str, description: str,
                verification_needed: bool = False,
                verification_reason: str = "",
                suggestion: str = "",
                file_context: str = "",
                line_number: Optional[int] = None) -> None:
        """
        Record a fix that was applied.

        Args:
            fix_type: Type/category of fix
            description: Description of what was fixed
            verification_needed: Whether manual verification is needed
            verification_reason: Why verification is needed
            suggestion: Suggested action for verification
            file_context: File where fix was applied
            line_number: Line number of fix
        """
        self.total_fixes += 1
        self.fix_descriptions.append(description)
        self.fixes_by_type[fix_type] = self.fixes_by_type.get(fix_type, 0) + 1

        if verification_needed:
            self.verification_items.append({
                'file': file_context,
                'line': line_number,
                'fix_type': fix_type,
                'description': description,
                'reason': verification_reason,
                'suggestion': suggestion,
            })

    def merge(self, other: 'FixResult') -> None:
        """Merge another result into this one."""
        self.files_processed += other.files_processed
        self.files_fixed += other.files_fixed
        self.total_fixes += other.total_fixes
        self.fix_descriptions.extend(other.fix_descriptions)
        self.verification_items.extend(other.verification_items)

        for fix_type, count in other.fixes_by_type.items():
            self.fixes_by_type[fix_type] = self.fixes_by_type.get(fix_type, 0) + count

        self.metadata.update(other.metadata)

    def summary(self) -> str:
        """Generate a text summary of fix results."""
        lines = [
            f"Files processed: {self.files_processed}",
            f"Files with fixes: {self.files_fixed}",
            f"Total fixes applied: {self.total_fixes}",
        ]

        if self.fixes_by_type:
            lines.append("\nFixes by type:")
            for fix_type, count in sorted(self.fixes_by_type.items(), key=lambda x: -x[1]):
                lines.append(f"  {fix_type}: {count}")

        if self.verification_items:
            lines.append(f"\nItems requiring verification: {len(self.verification_items)}")

        return "\n".join(lines)


class BaseFixer(ABC):
    """
    Abstract base class for fixers.

    Subclass this to create fixers for different validation rule sets
    (DTD, XSD, custom rules, etc.).

    Example:
        class MyDTDFixer(BaseFixer):
            def __init__(self, dtd_path: Path):
                self.dtd_path = dtd_path

            def fix_file(self, file_path: Path) -> FixResult:
                result = FixResult()
                # ... fixing logic ...
                return result

            def fix_package(self, package_path: Path, output_path: Path) -> FixResult:
                # ... package fixing logic ...
                pass
    """

    @abstractmethod
    def fix_file(self, file_path: Path, **kwargs) -> FixResult:
        """
        Apply fixes to a single file.

        Args:
            file_path: Path to the file to fix
            **kwargs: Additional fixing options

        Returns:
            FixResult with fixing outcome
        """
        pass

    @abstractmethod
    def fix_package(self, package_path: Path, output_path: Path, **kwargs) -> FixResult:
        """
        Apply fixes to all files in a package.

        Args:
            package_path: Path to the input package
            output_path: Path for the fixed output package
            **kwargs: Additional fixing options

        Returns:
            FixResult with fixing outcome
        """
        pass

    def fix_element(self, element: Any, file_context: str = "element") -> Tuple[Any, FixResult]:
        """
        Apply fixes to an XML element (optional to implement).

        Args:
            element: XML element to fix
            file_context: Context string for reporting

        Returns:
            Tuple of (fixed_element, FixResult)
        """
        raise NotImplementedError("Element fixing not supported by this fixer")

    @property
    def fix_categories(self) -> List[str]:
        """Return list of fix categories this fixer handles."""
        return []

    @property
    def schema_type(self) -> str:
        """Return the type of schema this fixer targets (e.g., 'DTD', 'XSD')."""
        return "Unknown"
