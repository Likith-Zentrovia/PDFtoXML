"""
RittDoc Core Library
====================

A reusable library for document conversion pipelines that provides:

- XML processing utilities
- Resource reference mapping and tracking
- Conversion metadata tracking and dashboard generation
- DTD/Schema validation framework
- Automated validation error fixing
- Package creation (ZIP, etc.)
- XSLT transformation utilities

Architecture
------------

The library is organized into independent, composable modules:

    rittdoc_core/
    ├── xml/           - XML processing utilities
    ├── mapping/       - Resource reference tracking
    ├── tracking/      - Conversion metadata & dashboards
    ├── validation/    - Validation framework (DTD, Schema, etc.)
    ├── fixing/        - Automated error fixing framework
    ├── packaging/     - Package creation utilities
    ├── transform/     - XSLT and other transformations
    ├── config/        - Configuration management
    └── adapters/      - Format-specific adapters (EPUB, PDF, etc.)

Usage
-----

Each module can be used independently or as part of a larger pipeline:

    from rittdoc_core.validation import DTDValidator
    from rittdoc_core.fixing import DTDFixer
    from rittdoc_core.packaging import ZipPackager
    from rittdoc_core.tracking import ConversionTracker

    # Validate XML
    validator = DTDValidator(dtd_path)
    report = validator.validate_package(zip_path)

    # Fix validation errors
    fixer = DTDFixer(dtd_path)
    stats = fixer.fix_package(zip_path, output_path)

    # Track conversion
    tracker = ConversionTracker(output_dir)
    tracker.start_conversion(filename, ConversionType.EPUB)
    ...
    tracker.complete_conversion(ConversionStatus.SUCCESS)

Extensibility
-------------

The library uses abstract base classes for key interfaces, allowing you to:

- Support different DTDs or XML schemas
- Create custom fixers for your validation rules
- Implement custom packaging formats
- Add new transformation pipelines

"""

__version__ = "1.0.0"
__author__ = "RittDocConverter Team"

# Import key classes for convenience
from rittdoc_core.mapping.reference_mapper import (
    ReferenceMapper,
    ResourceReference,
    LinkReference,
    get_mapper,
    reset_mapper,
)

from rittdoc_core.tracking.conversion_tracker import (
    ConversionTracker,
    ConversionMetadata,
    ConversionStatus,
    ConversionType,
    TemplateType,
    track_conversion,
)

from rittdoc_core.validation.base import (
    BaseValidator,
    ValidationResult,
)

from rittdoc_core.validation.report import (
    ValidationReportGenerator,
    ValidationError,
    VerificationItem,
)

from rittdoc_core.fixing.base import (
    BaseFixer,
    FixResult,
)

from rittdoc_core.packaging.base import (
    BasePackager,
    PackageResult,
)

from rittdoc_core.transform.xslt import (
    XSLTTransformer,
    load_xslt_transform,
    apply_xslt_transform,
)

__all__ = [
    # Version
    "__version__",
    # Mapping
    "ReferenceMapper",
    "ResourceReference",
    "LinkReference",
    "get_mapper",
    "reset_mapper",
    # Tracking
    "ConversionTracker",
    "ConversionMetadata",
    "ConversionStatus",
    "ConversionType",
    "TemplateType",
    "track_conversion",
    # Validation
    "BaseValidator",
    "ValidationResult",
    "ValidationReportGenerator",
    "ValidationError",
    "VerificationItem",
    # Fixing
    "BaseFixer",
    "FixResult",
    # Packaging
    "BasePackager",
    "PackageResult",
    # Transform
    "XSLTTransformer",
    "load_xslt_transform",
    "apply_xslt_transform",
]
