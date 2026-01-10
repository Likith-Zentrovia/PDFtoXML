"""
Validation Framework
====================

Provides abstract validation interfaces and concrete implementations
for validating XML documents against DTDs, schemas, or custom rules.

Components:
- BaseValidator: Abstract base class for all validators
- ValidationResult: Container for validation results
- ValidationReportGenerator: Excel report generation
- DTDValidator: DTD-specific validation implementation
"""

from rittdoc_core.validation.base import (
    BaseValidator,
    ValidationResult,
)

from rittdoc_core.validation.report import (
    ValidationReportGenerator,
    ValidationError,
    VerificationItem,
    generate_validation_report,
)

from rittdoc_core.validation.dtd_validator import (
    DTDValidator,
    EntityTrackingValidator,
)

__all__ = [
    # Base classes
    "BaseValidator",
    "ValidationResult",
    # Report generation
    "ValidationReportGenerator",
    "ValidationError",
    "VerificationItem",
    "generate_validation_report",
    # DTD validation
    "DTDValidator",
    "EntityTrackingValidator",
]
