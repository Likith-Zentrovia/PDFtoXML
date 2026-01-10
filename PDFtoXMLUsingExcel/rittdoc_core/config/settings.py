"""
Configuration Settings
======================

Configuration dataclasses for document conversion pipelines.
"""

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, List, Dict, Any
import json
import logging

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class DTDConfig:
    """DTD-related configuration."""

    dtd_path: str = "RITTDOCdtd/v1.1/RittDocBook.dtd"
    dtd_public_id: str = "-//OASIS//DTD DocBook XML V4.5//EN"
    validate_on_package: bool = True
    fix_validation_errors: bool = True
    max_fix_passes: int = 3


@dataclass
class PackagingConfig:
    """Packaging-related configuration."""

    media_dir_name: str = "multimedia"
    chapter_prefix: str = "ch"
    chapter_padding: int = 4
    include_dtd_files: bool = True
    compression_level: int = 6  # ZIP compression level (0-9)
    supported_image_formats: List[str] = field(
        default_factory=lambda: ['.jpg', '.jpeg', '.png', '.gif', '.svg', '.tif', '.tiff']
    )


@dataclass
class TransformConfig:
    """Transformation-related configuration."""

    xslt_dir: str = "xslt"
    compliance_xslt: str = "rittdoc_compliance.xslt"
    apply_compliance_transform: bool = True


@dataclass
class TrackingConfig:
    """Tracking and dashboard configuration."""

    enable_tracking: bool = True
    dashboard_filename: str = "conversion_dashboard.xlsx"
    export_reference_mapping: bool = False
    mapping_filename: str = "reference_mapping.json"


@dataclass
class PipelineConfig:
    """
    Complete pipeline configuration.

    Contains all configuration for document conversion:
    - DTD validation and fixing
    - Packaging options
    - XSLT transformations
    - Tracking and dashboards

    Example:
        config = PipelineConfig()
        config.dtd.validate_on_package = True
        config.packaging.media_dir_name = "images"
        save_config(config, Path("config.yaml"))
    """

    dtd: DTDConfig = field(default_factory=DTDConfig)
    packaging: PackagingConfig = field(default_factory=PackagingConfig)
    transform: TransformConfig = field(default_factory=TransformConfig)
    tracking: TrackingConfig = field(default_factory=TrackingConfig)

    # General settings
    output_dir: str = "output"
    temp_dir: str = ""  # Empty means use system temp
    log_level: str = "INFO"

    # Custom extensions
    custom: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            'dtd': asdict(self.dtd),
            'packaging': asdict(self.packaging),
            'transform': asdict(self.transform),
            'tracking': asdict(self.tracking),
            'output_dir': self.output_dir,
            'temp_dir': self.temp_dir,
            'log_level': self.log_level,
            'custom': self.custom,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'PipelineConfig':
        """Create from dictionary."""
        config = cls()

        if 'dtd' in data:
            config.dtd = DTDConfig(**data['dtd'])
        if 'packaging' in data:
            config.packaging = PackagingConfig(**data['packaging'])
        if 'transform' in data:
            config.transform = TransformConfig(**data['transform'])
        if 'tracking' in data:
            config.tracking = TrackingConfig(**data['tracking'])

        if 'output_dir' in data:
            config.output_dir = data['output_dir']
        if 'temp_dir' in data:
            config.temp_dir = data['temp_dir']
        if 'log_level' in data:
            config.log_level = data['log_level']
        if 'custom' in data:
            config.custom = data['custom']

        return config


def load_config(config_path: Path) -> PipelineConfig:
    """
    Load configuration from file.

    Supports JSON and YAML formats based on file extension.

    Args:
        config_path: Path to config file

    Returns:
        PipelineConfig object

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If file format is not supported
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    suffix = config_path.suffix.lower()

    with open(config_path, 'r', encoding='utf-8') as f:
        if suffix in ['.yaml', '.yml']:
            if not YAML_AVAILABLE:
                raise ImportError("PyYAML is required for YAML config files")
            data = yaml.safe_load(f)
        elif suffix == '.json':
            data = json.load(f)
        else:
            raise ValueError(f"Unsupported config format: {suffix}")

    logger.info(f"Loaded configuration from {config_path}")
    return PipelineConfig.from_dict(data)


def save_config(config: PipelineConfig, config_path: Path) -> None:
    """
    Save configuration to file.

    Supports JSON and YAML formats based on file extension.

    Args:
        config: PipelineConfig to save
        config_path: Path to save config file

    Raises:
        ValueError: If file format is not supported
    """
    suffix = config_path.suffix.lower()
    data = config.to_dict()

    config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, 'w', encoding='utf-8') as f:
        if suffix in ['.yaml', '.yml']:
            if not YAML_AVAILABLE:
                raise ImportError("PyYAML is required for YAML config files")
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        elif suffix == '.json':
            json.dump(data, f, indent=2)
        else:
            raise ValueError(f"Unsupported config format: {suffix}")

    logger.info(f"Saved configuration to {config_path}")


def get_default_config() -> PipelineConfig:
    """Get default configuration."""
    return PipelineConfig()
