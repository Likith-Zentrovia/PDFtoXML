#!/usr/bin/env python3
"""
Configuration Management for PDF to XML Pipeline

This module provides centralized configuration management for the entire
PDF conversion pipeline. It supports:

- Environment variable configuration
- Configuration file loading (JSON/YAML)
- Default values with override capability
- Validation of configuration values

Example Usage:
    from config import get_config, PipelineConfig

    # Get current configuration
    config = get_config()
    print(config.model)  # claude-sonnet-4-20250514

    # Override specific values
    config = PipelineConfig(model="claude-opus-4-5-20251101", dpi=400)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


# ============================================================================
# CONFIGURATION DATACLASSES
# ============================================================================

@dataclass
class AIModelConfig:
    """Configuration for AI model settings."""
    model: str = "claude-sonnet-4-20250514"
    temperature: float = 0.0  # 0 = no hallucinations
    max_tokens: int = 8192
    confidence_threshold: float = 0.85
    enable_second_pass: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RenderingConfig:
    """Configuration for PDF rendering."""
    dpi: int = 300  # High DPI for better OCR
    crop_header_pct: float = 0.06  # Crop top 6%
    crop_footer_pct: float = 0.06  # Crop bottom 6%
    fallback_dpi_levels: List[int] = field(default_factory=lambda: [200, 150, 100])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProcessingConfig:
    """Configuration for batch processing."""
    batch_size: int = 10
    save_intermediate: bool = True
    resume_from_page: int = 1
    parallel_workers: int = 1  # Be careful with rate limits
    max_image_size_bytes: int = 5 * 1024 * 1024  # 5MB

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationConfig:
    """Configuration for DTD validation."""
    dtd_path: Path = field(default_factory=lambda: Path("RITTDOCdtd/v1.1/RittDocBook.dtd"))
    max_iterations: int = 3
    generate_reports: bool = True
    report_format: str = "xlsx"  # xlsx or json

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["dtd_path"] = str(self.dtd_path)
        return d


@dataclass
class OutputConfig:
    """Configuration for output settings."""
    output_dir: Path = field(default_factory=lambda: Path("output"))
    create_docx: bool = True
    create_rittdoc_zip: bool = True
    include_toc: bool = True
    toc_depth: int = 3
    cleanup_intermediate: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["output_dir"] = str(self.output_dir)
        return d


@dataclass
class EditorConfig:
    """Configuration for web editor."""
    enabled: bool = True
    port: int = 5000
    auto_open_browser: bool = True
    debug_mode: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class APIConfig:
    """Configuration for REST API."""
    host: str = "0.0.0.0"
    port: int = 8000
    max_concurrent_jobs: int = 3
    upload_dir: Path = field(default_factory=lambda: Path("uploads"))
    result_retention_hours: int = 24
    cors_origins: List[str] = field(default_factory=lambda: ["*"])

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["upload_dir"] = str(self.upload_dir)
        return d


@dataclass
class ComplexityConfig:
    """
    Configuration for page complexity detection and hybrid routing.

    This controls how pages are analyzed for complexity and routed
    between AI and non-AI conversion pipelines.
    """
    # Enable/disable hybrid mode
    enabled: bool = False

    # Table thresholds
    table_count_simple: int = 0       # 0 tables = simple
    table_count_moderate: int = 1     # 1 table = moderate
    table_count_complex: int = 2      # 2+ tables = complex

    # Image thresholds
    image_count_simple: int = 1       # 0-1 images = simple
    image_count_moderate: int = 3     # 2-3 images = moderate
    image_count_complex: int = 4      # 4+ images = complex
    image_area_threshold: float = 0.3 # 30% of page covered by images = complex

    # Layout thresholds
    column_count_complex: int = 3     # 3+ columns = complex

    # Text density thresholds
    min_chars_per_page: int = 100     # Pages with < 100 chars might be mostly images

    # Mixed content scoring
    mixed_content_score_complex: int = 5  # Combined score threshold for complexity

    # Routing overrides
    force_ai_pages: List[int] = field(default_factory=list)    # Always use AI for these pages
    force_nonai_pages: List[int] = field(default_factory=list) # Always use non-AI for these pages

    # Fallback behavior
    ai_fallback_enabled: bool = True  # Fall back to AI if non-AI fails

    # Performance settings
    parallel_nonai: bool = True       # Process non-AI pages in parallel
    max_workers: int = 4              # Max parallel workers for non-AI

    # Output settings
    generate_complexity_report: bool = True  # Generate JSON complexity report

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineConfig:
    """
    Complete configuration for the PDF to XML pipeline.

    This is the main configuration class that aggregates all sub-configurations.
    """
    # Sub-configurations
    ai: AIModelConfig = field(default_factory=AIModelConfig)
    rendering: RenderingConfig = field(default_factory=RenderingConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    editor: EditorConfig = field(default_factory=EditorConfig)
    api: APIConfig = field(default_factory=APIConfig)
    complexity: ComplexityConfig = field(default_factory=ComplexityConfig)

    # Convenience properties for common settings
    @property
    def model(self) -> str:
        return self.ai.model

    @property
    def dpi(self) -> int:
        return self.rendering.dpi

    @property
    def temperature(self) -> float:
        return self.ai.temperature

    @property
    def dtd_path(self) -> Path:
        return self.validation.dtd_path

    @property
    def output_dir(self) -> Path:
        return self.output.output_dir

    def to_dict(self) -> Dict[str, Any]:
        """Convert entire configuration to dictionary."""
        return {
            "ai": self.ai.to_dict(),
            "rendering": self.rendering.to_dict(),
            "processing": self.processing.to_dict(),
            "validation": self.validation.to_dict(),
            "output": self.output.to_dict(),
            "editor": self.editor.to_dict(),
            "api": self.api.to_dict(),
            "complexity": self.complexity.to_dict(),
        }

    def to_json(self, indent: int = 2) -> str:
        """Convert configuration to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PipelineConfig":
        """Create configuration from dictionary."""
        config = cls()

        if "ai" in data:
            config.ai = AIModelConfig(**data["ai"])
        if "rendering" in data:
            config.rendering = RenderingConfig(**data["rendering"])
        if "processing" in data:
            config.processing = ProcessingConfig(**data["processing"])
        if "validation" in data:
            val_data = data["validation"].copy()
            if "dtd_path" in val_data:
                val_data["dtd_path"] = Path(val_data["dtd_path"])
            config.validation = ValidationConfig(**val_data)
        if "output" in data:
            out_data = data["output"].copy()
            if "output_dir" in out_data:
                out_data["output_dir"] = Path(out_data["output_dir"])
            config.output = OutputConfig(**out_data)
        if "editor" in data:
            config.editor = EditorConfig(**data["editor"])
        if "api" in data:
            api_data = data["api"].copy()
            if "upload_dir" in api_data:
                api_data["upload_dir"] = Path(api_data["upload_dir"])
            config.api = APIConfig(**api_data)
        if "complexity" in data:
            config.complexity = ComplexityConfig(**data["complexity"])

        return config

    @classmethod
    def from_json(cls, json_str: str) -> "PipelineConfig":
        """Create configuration from JSON string."""
        return cls.from_dict(json.loads(json_str))

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "PipelineConfig":
        """Load configuration from a JSON file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            return cls.from_json(f.read())

    def save(self, path: Union[str, Path]):
        """Save configuration to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())

    @classmethod
    def from_env(cls) -> "PipelineConfig":
        """
        Create configuration from environment variables.

        Environment variable naming:
        - PDFTOXML_MODEL
        - PDFTOXML_DPI
        - PDFTOXML_TEMPERATURE
        - PDFTOXML_BATCH_SIZE
        - PDFTOXML_DTD_PATH
        - PDFTOXML_OUTPUT_DIR
        - etc.
        """
        config = cls()

        # AI settings
        if env_model := os.environ.get("PDFTOXML_MODEL"):
            config.ai.model = env_model
        if env_temp := os.environ.get("PDFTOXML_TEMPERATURE"):
            config.ai.temperature = float(env_temp)
        if env_tokens := os.environ.get("PDFTOXML_MAX_TOKENS"):
            config.ai.max_tokens = int(env_tokens)

        # Rendering settings
        if env_dpi := os.environ.get("PDFTOXML_DPI"):
            config.rendering.dpi = int(env_dpi)
        if env_crop_header := os.environ.get("PDFTOXML_CROP_HEADER"):
            config.rendering.crop_header_pct = float(env_crop_header)
        if env_crop_footer := os.environ.get("PDFTOXML_CROP_FOOTER"):
            config.rendering.crop_footer_pct = float(env_crop_footer)

        # Processing settings
        if env_batch := os.environ.get("PDFTOXML_BATCH_SIZE"):
            config.processing.batch_size = int(env_batch)
        if env_workers := os.environ.get("PDFTOXML_WORKERS"):
            config.processing.parallel_workers = int(env_workers)

        # Validation settings
        if env_dtd := os.environ.get("PDFTOXML_DTD_PATH"):
            config.validation.dtd_path = Path(env_dtd)
        if env_iters := os.environ.get("PDFTOXML_MAX_ITERATIONS"):
            config.validation.max_iterations = int(env_iters)

        # Output settings
        if env_output := os.environ.get("PDFTOXML_OUTPUT_DIR"):
            config.output.output_dir = Path(env_output)
        if env_docx := os.environ.get("PDFTOXML_CREATE_DOCX"):
            config.output.create_docx = env_docx.lower() in ("true", "1", "yes")
        if env_rittdoc := os.environ.get("PDFTOXML_CREATE_RITTDOC"):
            config.output.create_rittdoc_zip = env_rittdoc.lower() in ("true", "1", "yes")

        # Editor settings
        if env_port := os.environ.get("PDFTOXML_EDITOR_PORT"):
            config.editor.port = int(env_port)
        if env_debug := os.environ.get("PDFTOXML_DEBUG"):
            config.editor.debug_mode = env_debug.lower() in ("true", "1", "yes")

        # API settings
        if env_api_host := os.environ.get("PDFTOXML_API_HOST"):
            config.api.host = env_api_host
        if env_api_port := os.environ.get("PDFTOXML_API_PORT"):
            config.api.port = int(env_api_port)
        if env_concurrent := os.environ.get("PDFTOXML_MAX_CONCURRENT"):
            config.api.max_concurrent_jobs = int(env_concurrent)

        return config


# ============================================================================
# GLOBAL CONFIGURATION
# ============================================================================

_global_config: Optional[PipelineConfig] = None


def get_config() -> PipelineConfig:
    """
    Get the global configuration instance.

    Returns the cached configuration or creates a new one from environment.
    """
    global _global_config
    if _global_config is None:
        _global_config = PipelineConfig.from_env()
    return _global_config


def set_config(config: PipelineConfig):
    """Set the global configuration instance."""
    global _global_config
    _global_config = config


def reset_config():
    """Reset the global configuration to default."""
    global _global_config
    _global_config = None


def load_config(path: Union[str, Path]) -> PipelineConfig:
    """
    Load configuration from a file and set as global.

    Args:
        path: Path to configuration JSON file

    Returns:
        The loaded configuration
    """
    config = PipelineConfig.from_file(path)
    set_config(config)
    return config


# ============================================================================
# CONFIGURATION VALIDATION
# ============================================================================

def validate_config(config: PipelineConfig) -> List[str]:
    """
    Validate configuration and return list of errors.

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    # Validate AI settings
    if config.ai.temperature < 0 or config.ai.temperature > 1:
        errors.append("AI temperature must be between 0 and 1")
    if config.ai.max_tokens < 1:
        errors.append("Max tokens must be positive")

    # Validate rendering settings
    if config.rendering.dpi < 72 or config.rendering.dpi > 600:
        errors.append("DPI must be between 72 and 600")
    if config.rendering.crop_header_pct < 0 or config.rendering.crop_header_pct > 0.5:
        errors.append("Header crop percentage must be between 0 and 0.5")
    if config.rendering.crop_footer_pct < 0 or config.rendering.crop_footer_pct > 0.5:
        errors.append("Footer crop percentage must be between 0 and 0.5")

    # Validate processing settings
    if config.processing.batch_size < 1 or config.processing.batch_size > 100:
        errors.append("Batch size must be between 1 and 100")
    if config.processing.parallel_workers < 1:
        errors.append("Parallel workers must be at least 1")

    # Validate DTD path exists
    if not config.validation.dtd_path.exists():
        errors.append(f"DTD file not found: {config.validation.dtd_path}")

    # Validate ports
    if config.editor.port < 1 or config.editor.port > 65535:
        errors.append("Editor port must be between 1 and 65535")
    if config.api.port < 1 or config.api.port > 65535:
        errors.append("API port must be between 1 and 65535")

    return errors


# ============================================================================
# EXAMPLE CONFIGURATION FILE
# ============================================================================

EXAMPLE_CONFIG = """{
    "ai": {
        "model": "claude-sonnet-4-20250514",
        "temperature": 0.0,
        "max_tokens": 8192,
        "confidence_threshold": 0.85,
        "enable_second_pass": true
    },
    "rendering": {
        "dpi": 300,
        "crop_header_pct": 0.06,
        "crop_footer_pct": 0.06,
        "fallback_dpi_levels": [200, 150, 100]
    },
    "processing": {
        "batch_size": 10,
        "save_intermediate": true,
        "resume_from_page": 1,
        "parallel_workers": 1,
        "max_image_size_bytes": 5242880
    },
    "validation": {
        "dtd_path": "RITTDOCdtd/v1.1/RittDocBook.dtd",
        "max_iterations": 3,
        "generate_reports": true,
        "report_format": "xlsx"
    },
    "output": {
        "output_dir": "output",
        "create_docx": true,
        "create_rittdoc_zip": true,
        "include_toc": true,
        "toc_depth": 3,
        "cleanup_intermediate": false
    },
    "editor": {
        "enabled": true,
        "port": 5000,
        "auto_open_browser": true,
        "debug_mode": false
    },
    "api": {
        "host": "0.0.0.0",
        "port": 8000,
        "max_concurrent_jobs": 3,
        "upload_dir": "uploads",
        "result_retention_hours": 24,
        "cors_origins": ["*"]
    }
}"""


if __name__ == "__main__":
    # Print example configuration
    print("Example configuration file:")
    print(EXAMPLE_CONFIG)

    # Test loading from environment
    print("\nConfiguration from environment:")
    config = get_config()
    print(config.to_json())
