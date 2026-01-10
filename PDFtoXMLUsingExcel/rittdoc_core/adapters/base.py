"""
Base Adapter Classes
====================

Abstract base classes for format-specific adapters.
Extend these classes to create adapters for different input formats
(EPUB, PDF, DOCX, HTML, etc.).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class AdapterResult:
    """
    Container for adapter results.

    Attributes:
        success: Whether conversion succeeded
        output_path: Path to the generated structured XML
        chapters_extracted: Number of chapters extracted
        images_extracted: Number of images extracted
        metadata: Extracted document metadata
        errors: List of error messages
        warnings: List of warning messages
        statistics: Conversion statistics
    """
    success: bool = True
    output_path: Optional[Path] = None
    chapters_extracted: int = 0
    images_extracted: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    statistics: Dict[str, Any] = field(default_factory=dict)

    def add_error(self, error: str) -> None:
        """Add an error message."""
        self.errors.append(error)
        self.success = False

    def add_warning(self, warning: str) -> None:
        """Add a warning message."""
        self.warnings.append(warning)

    def summary(self) -> str:
        """Generate a text summary of adapter results."""
        status = "SUCCESS" if self.success else "FAILED"
        lines = [
            f"Conversion: {status}",
            f"Output: {self.output_path}",
            f"Chapters: {self.chapters_extracted}",
            f"Images: {self.images_extracted}",
        ]

        if self.metadata:
            lines.append("\nMetadata:")
            for key, value in list(self.metadata.items())[:5]:
                lines.append(f"  {key}: {value}")

        if self.warnings:
            lines.append(f"\nWarnings ({len(self.warnings)}):")
            for warning in self.warnings[:3]:
                lines.append(f"  - {warning}")

        if self.errors:
            lines.append(f"\nErrors ({len(self.errors)}):")
            for error in self.errors[:3]:
                lines.append(f"  - {error}")

        return "\n".join(lines)


class BaseAdapter(ABC):
    """
    Abstract base class for format adapters.

    Subclass this to create adapters for specific input formats.
    Each adapter is responsible for:
    1. Extracting document structure (chapters, sections)
    2. Extracting metadata (title, author, ISBN, etc.)
    3. Extracting and processing media (images, tables)
    4. Generating structured XML output

    Example:
        class EPUBAdapter(BaseAdapter):
            @property
            def supported_formats(self) -> List[str]:
                return ['.epub']

            def convert(self, input_path: Path, output_dir: Path) -> AdapterResult:
                result = AdapterResult()
                # ... conversion logic ...
                return result

            def extract_metadata(self, input_path: Path) -> Dict[str, Any]:
                # ... metadata extraction ...
                return metadata
    """

    @property
    @abstractmethod
    def supported_formats(self) -> List[str]:
        """
        Return list of supported file extensions.

        Example:
            return ['.epub', '.epub3']
        """
        pass

    @property
    def adapter_name(self) -> str:
        """Return adapter name (default: class name)."""
        return self.__class__.__name__

    def supports_format(self, file_path: Path) -> bool:
        """
        Check if this adapter supports the given file format.

        Args:
            file_path: Path to check

        Returns:
            True if format is supported
        """
        suffix = file_path.suffix.lower()
        return suffix in [ext.lower() for ext in self.supported_formats]

    @abstractmethod
    def convert(self,
                input_path: Path,
                output_dir: Path,
                **kwargs) -> AdapterResult:
        """
        Convert input file to structured XML.

        Args:
            input_path: Path to the input file
            output_dir: Directory for output files
            **kwargs: Additional conversion options

        Returns:
            AdapterResult with conversion outcome
        """
        pass

    @abstractmethod
    def extract_metadata(self, input_path: Path) -> Dict[str, Any]:
        """
        Extract metadata from input file without full conversion.

        Args:
            input_path: Path to the input file

        Returns:
            Dictionary of extracted metadata
        """
        pass

    def validate_input(self, input_path: Path) -> tuple:
        """
        Validate input file before conversion.

        Args:
            input_path: Path to the input file

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not input_path.exists():
            return False, f"Input file not found: {input_path}"

        if not self.supports_format(input_path):
            supported = ", ".join(self.supported_formats)
            return False, f"Unsupported format. Supported: {supported}"

        return True, ""

    def pre_convert(self, input_path: Path, output_dir: Path) -> None:
        """
        Hook called before conversion starts.
        Override to add pre-processing logic.

        Args:
            input_path: Path to the input file
            output_dir: Directory for output files
        """
        pass

    def post_convert(self, result: AdapterResult) -> None:
        """
        Hook called after conversion completes.
        Override to add post-processing logic.

        Args:
            result: AdapterResult from conversion
        """
        pass


class PipelineRunner:
    """
    Runs a complete conversion pipeline using adapters and core components.

    Example:
        from rittdoc_core.adapters import PipelineRunner, EPUBAdapter
        from rittdoc_core.validation import DTDValidator
        from rittdoc_core.fixing import ComprehensiveDTDFixer

        runner = PipelineRunner()
        runner.register_adapter(EPUBAdapter())
        runner.set_validator(DTDValidator(dtd_path))
        runner.set_fixer(ComprehensiveDTDFixer(dtd_path))

        result = runner.run(input_path, output_path)
    """

    def __init__(self):
        """Initialize empty pipeline runner."""
        self._adapters: List[BaseAdapter] = []
        self._validator = None
        self._fixer = None
        self._packager = None
        self._transformer = None

    def register_adapter(self, adapter: BaseAdapter) -> 'PipelineRunner':
        """
        Register a format adapter.

        Args:
            adapter: Adapter to register

        Returns:
            Self for method chaining
        """
        self._adapters.append(adapter)
        return self

    def set_validator(self, validator) -> 'PipelineRunner':
        """Set the validator for the pipeline."""
        self._validator = validator
        return self

    def set_fixer(self, fixer) -> 'PipelineRunner':
        """Set the fixer for the pipeline."""
        self._fixer = fixer
        return self

    def set_packager(self, packager) -> 'PipelineRunner':
        """Set the packager for the pipeline."""
        self._packager = packager
        return self

    def set_transformer(self, transformer) -> 'PipelineRunner':
        """Set the XSLT transformer for the pipeline."""
        self._transformer = transformer
        return self

    def get_adapter_for_file(self, file_path: Path) -> Optional[BaseAdapter]:
        """
        Find an adapter that supports the given file.

        Args:
            file_path: Path to check

        Returns:
            Adapter if found, None otherwise
        """
        for adapter in self._adapters:
            if adapter.supports_format(file_path):
                return adapter
        return None

    def run(self, input_path: Path, output_path: Path, **kwargs) -> Dict[str, Any]:
        """
        Run the complete conversion pipeline.

        Args:
            input_path: Path to input file
            output_path: Path for output package
            **kwargs: Additional options

        Returns:
            Dictionary with pipeline results
        """
        results = {
            'success': False,
            'adapter_result': None,
            'validation_result': None,
            'fix_result': None,
            'package_result': None,
            'errors': [],
        }

        # Find adapter
        adapter = self.get_adapter_for_file(input_path)
        if adapter is None:
            results['errors'].append(f"No adapter found for: {input_path}")
            return results

        logger.info(f"Using adapter: {adapter.adapter_name}")

        # Run conversion
        output_dir = output_path.parent
        adapter_result = adapter.convert(input_path, output_dir, **kwargs)
        results['adapter_result'] = adapter_result

        if not adapter_result.success:
            results['errors'].extend(adapter_result.errors)
            return results

        # Apply transformations
        if self._transformer and adapter_result.output_path:
            try:
                transformed_path = adapter_result.output_path.with_suffix('.compliant.xml')
                self._transformer.transform(adapter_result.output_path, transformed_path)
                adapter_result.output_path = transformed_path
            except Exception as e:
                logger.error(f"Transformation failed: {e}")
                results['errors'].append(f"Transformation failed: {e}")

        # Validate
        if self._validator and adapter_result.output_path:
            validation_result = self._validator.validate_file(adapter_result.output_path)
            results['validation_result'] = validation_result

        # Package
        if self._packager and adapter_result.output_path:
            package_result = self._packager.package(
                adapter_result.output_path,
                output_path
            )
            results['package_result'] = package_result

            # Fix if needed
            if self._fixer and self._validator and not results['validation_result'].is_valid:
                fix_result = self._fixer.fix_package(output_path, output_path)
                results['fix_result'] = fix_result

        results['success'] = all([
            adapter_result.success,
            results.get('package_result') is None or results['package_result'].success,
        ])

        return results

    @property
    def supported_formats(self) -> List[str]:
        """Return all supported formats from registered adapters."""
        formats = []
        for adapter in self._adapters:
            formats.extend(adapter.supported_formats)
        return list(set(formats))
