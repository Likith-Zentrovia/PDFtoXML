#!/usr/bin/env python3
"""
Shared Configuration for PDF to XML Pipeline

This module provides shareable configuration definitions that can be used by:
- The PDF processing pipeline (this repo)
- UI projects that integrate with the pipeline API

Usage in UI Project:
    Copy this file to your UI project, or install this repo as a package.

    from shared_config import (
        CONVERSION_CONFIG_OPTIONS,
        DEFAULT_CONVERSION_CONFIG,
        ConversionConfigSchema,
    )

Usage in API calls:
    The configuration keys match the API form parameters.
    Pass these as form data to POST /api/v1/convert
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from enum import Enum
import json


# ============================================================================
# ENUMS FOR DROPDOWN OPTIONS
# ============================================================================

class AIModel(str, Enum):
    """Available Claude AI models for conversion."""
    SONNET_4 = "claude-sonnet-4-20250514"
    OPUS_4_5 = "claude-opus-4-5-20251101"
    HAIKU_3_5 = "claude-haiku-3-5-20241022"

    @property
    def label(self) -> str:
        labels = {
            self.SONNET_4: "Claude Sonnet 4 (Recommended)",
            self.OPUS_4_5: "Claude Opus 4.5 (Highest Quality)",
            self.HAIKU_3_5: "Claude Haiku 3.5 (Fastest)",
        }
        return labels.get(self, self.value)

    @property
    def description(self) -> str:
        descriptions = {
            self.SONNET_4: "Best balance of speed and quality. Recommended for most documents.",
            self.OPUS_4_5: "Highest accuracy for complex documents. Slower and more expensive.",
            self.HAIKU_3_5: "Fastest processing. Good for simple documents or quick previews.",
        }
        return descriptions.get(self, "")


class DPI(int, Enum):
    """PDF rendering resolution options."""
    LOW = 150
    MEDIUM = 200
    STANDARD = 300
    HIGH = 400
    MAXIMUM = 600

    @property
    def label(self) -> str:
        labels = {
            self.LOW: "150 DPI (Fast, Lower Quality)",
            self.MEDIUM: "200 DPI (Balanced)",
            self.STANDARD: "300 DPI (Recommended)",
            self.HIGH: "400 DPI (High Quality)",
            self.MAXIMUM: "600 DPI (Maximum Quality, Slower)",
        }
        return labels.get(self, f"{self.value} DPI")


class Temperature(float, Enum):
    """AI temperature settings."""
    DETERMINISTIC = 0.0
    MINIMAL = 0.1
    SLIGHT = 0.2
    CREATIVE = 0.3

    @property
    def label(self) -> str:
        labels = {
            self.DETERMINISTIC: "0.0 - Deterministic (Recommended)",
            self.MINIMAL: "0.1 - Minimal Variation",
            self.SLIGHT: "0.2 - Slight Variation",
            self.CREATIVE: "0.3 - More Creative",
        }
        return labels.get(self, str(self.value))


class BatchSize(int, Enum):
    """Pages per processing batch."""
    SMALL = 5
    MEDIUM = 10
    LARGE = 15
    XLARGE = 20
    XXLARGE = 25

    @property
    def label(self) -> str:
        labels = {
            self.SMALL: "5 pages (More API calls, lower memory)",
            self.MEDIUM: "10 pages (Recommended)",
            self.LARGE: "15 pages",
            self.XLARGE: "20 pages",
            self.XXLARGE: "25 pages (Fewer API calls, higher memory)",
        }
        return labels.get(self, f"{self.value} pages")


class TOCDepth(int, Enum):
    """Table of contents depth levels."""
    LEVEL_1 = 1
    LEVEL_2 = 2
    LEVEL_3 = 3
    LEVEL_4 = 4
    LEVEL_5 = 5

    @property
    def label(self) -> str:
        labels = {
            self.LEVEL_1: "Level 1 only (Chapters)",
            self.LEVEL_2: "Levels 1-2 (Chapters + Sections)",
            self.LEVEL_3: "Levels 1-3 (Recommended)",
            self.LEVEL_4: "Levels 1-4 (Detailed)",
            self.LEVEL_5: "Levels 1-5 (Maximum Detail)",
        }
        return labels.get(self, f"Level {self.value}")


class TemplateType(str, Enum):
    """Document layout template types."""
    AUTO = "auto"
    SINGLE_COLUMN = "single_column"
    DOUBLE_COLUMN = "double_column"
    MIXED = "mixed"

    @property
    def label(self) -> str:
        labels = {
            self.AUTO: "Auto-detect (Recommended)",
            self.SINGLE_COLUMN: "Single Column",
            self.DOUBLE_COLUMN: "Double Column",
            self.MIXED: "Mixed Layout",
        }
        return labels.get(self, self.value)


# ============================================================================
# CONFIGURATION OPTIONS FOR UI DROPDOWNS
# ============================================================================

CONVERSION_CONFIG_OPTIONS = {
    "model": {
        "label": "AI Model",
        "description": "Claude AI model used for document analysis and conversion",
        "type": "dropdown",
        "required": True,
        "default": AIModel.SONNET_4.value,
        "options": [
            {
                "value": m.value,
                "label": m.label,
                "description": m.description,
                "default": m == AIModel.SONNET_4,
            }
            for m in AIModel
        ],
    },
    "dpi": {
        "label": "Resolution (DPI)",
        "description": "PDF rendering resolution. Higher DPI = better quality but slower processing",
        "type": "dropdown",
        "required": True,
        "default": DPI.STANDARD.value,
        "options": [
            {"value": d.value, "label": d.label, "default": d == DPI.STANDARD}
            for d in DPI
        ],
    },
    "temperature": {
        "label": "AI Temperature",
        "description": "Controls AI creativity. Use 0.0 for consistent, deterministic output",
        "type": "dropdown",
        "required": True,
        "default": Temperature.DETERMINISTIC.value,
        "options": [
            {"value": t.value, "label": t.label, "default": t == Temperature.DETERMINISTIC}
            for t in Temperature
        ],
    },
    "batch_size": {
        "label": "Batch Size",
        "description": "Number of pages processed per API call",
        "type": "dropdown",
        "required": True,
        "default": BatchSize.MEDIUM.value,
        "options": [
            {"value": b.value, "label": b.label, "default": b == BatchSize.MEDIUM}
            for b in BatchSize
        ],
    },
    "toc_depth": {
        "label": "Table of Contents Depth",
        "description": "How many heading levels to include in the TOC",
        "type": "dropdown",
        "required": True,
        "default": TOCDepth.LEVEL_3.value,
        "options": [
            {"value": t.value, "label": t.label, "default": t == TOCDepth.LEVEL_3}
            for t in TOCDepth
        ],
    },
    "template_type": {
        "label": "Document Template",
        "description": "Expected document layout. Auto-detect works for most documents",
        "type": "dropdown",
        "required": True,
        "default": TemplateType.AUTO.value,
        "options": [
            {"value": t.value, "label": t.label, "default": t == TemplateType.AUTO}
            for t in TemplateType
        ],
    },
    "create_docx": {
        "label": "Generate Word Document",
        "description": "Create a .docx file from the converted content",
        "type": "checkbox",
        "required": False,
        "default": True,
    },
    "create_rittdoc": {
        "label": "Generate RittDoc Package",
        "description": "Create a DTD-compliant RittDoc ZIP package",
        "type": "checkbox",
        "required": False,
        "default": True,
    },
    "skip_extraction": {
        "label": "Skip Image Extraction",
        "description": "Skip extracting images (faster processing, no images in output)",
        "type": "checkbox",
        "required": False,
        "default": False,
    },
    "include_toc": {
        "label": "Include Table of Contents",
        "description": "Generate a table of contents in the output",
        "type": "checkbox",
        "required": False,
        "default": True,
    },
    "use_hybrid": {
        "label": "Hybrid Conversion Mode",
        "description": "Route complex pages (with tables/images) to AI pipeline, simple text-only pages to faster non-AI pipeline. Recommended for most documents.",
        "type": "checkbox",
        "required": False,
        "default": True,
    },
}


# ============================================================================
# DEFAULT CONFIGURATION
# ============================================================================

@dataclass
class ConversionConfig:
    """
    User-configurable conversion settings.

    These settings can be passed to the API for each conversion job.
    Use DEFAULT_CONVERSION_CONFIG for default values.
    """
    # Dropdown selections
    model: str = AIModel.SONNET_4.value
    dpi: int = DPI.STANDARD.value
    temperature: float = Temperature.DETERMINISTIC.value
    batch_size: int = BatchSize.MEDIUM.value
    toc_depth: int = TOCDepth.LEVEL_3.value
    template_type: str = TemplateType.AUTO.value

    # Checkbox options
    create_docx: bool = True
    create_rittdoc: bool = True
    skip_extraction: bool = False
    include_toc: bool = True
    use_hybrid: bool = True  # Enable hybrid mode by default for optimal performance

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API calls."""
        return asdict(self)

    def to_form_data(self) -> Dict[str, str]:
        """Convert to form data format for multipart requests."""
        data = self.to_dict()
        # Convert booleans to strings for form data
        return {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in data.items()}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversionConfig":
        """Create from dictionary (e.g., from UI form submission)."""
        # Handle type conversions
        if "dpi" in data and isinstance(data["dpi"], str):
            data["dpi"] = int(data["dpi"])
        if "temperature" in data and isinstance(data["temperature"], str):
            data["temperature"] = float(data["temperature"])
        if "batch_size" in data and isinstance(data["batch_size"], str):
            data["batch_size"] = int(data["batch_size"])
        if "toc_depth" in data and isinstance(data["toc_depth"], str):
            data["toc_depth"] = int(data["toc_depth"])

        # Handle boolean conversions from form data
        bool_fields = ["create_docx", "create_rittdoc", "skip_extraction", "include_toc", "use_hybrid"]
        for field in bool_fields:
            if field in data and isinstance(data[field], str):
                data[field] = data[field].lower() in ("true", "1", "yes", "on")

        # Filter to only valid fields
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}

        return cls(**filtered_data)

    def validate(self) -> List[str]:
        """Validate configuration values. Returns list of errors."""
        errors = []

        # Validate model
        valid_models = [m.value for m in AIModel]
        if self.model not in valid_models:
            errors.append(f"Invalid model: {self.model}. Must be one of: {valid_models}")

        # Validate DPI
        valid_dpi = [d.value for d in DPI]
        if self.dpi not in valid_dpi:
            errors.append(f"Invalid DPI: {self.dpi}. Must be one of: {valid_dpi}")

        # Validate temperature
        valid_temp = [t.value for t in Temperature]
        if self.temperature not in valid_temp:
            errors.append(f"Invalid temperature: {self.temperature}. Must be one of: {valid_temp}")

        # Validate batch_size
        valid_batch = [b.value for b in BatchSize]
        if self.batch_size not in valid_batch:
            errors.append(f"Invalid batch_size: {self.batch_size}. Must be one of: {valid_batch}")

        # Validate toc_depth
        valid_toc = [t.value for t in TOCDepth]
        if self.toc_depth not in valid_toc:
            errors.append(f"Invalid toc_depth: {self.toc_depth}. Must be one of: {valid_toc}")

        # Validate template_type
        valid_template = [t.value for t in TemplateType]
        if self.template_type not in valid_template:
            errors.append(f"Invalid template_type: {self.template_type}. Must be one of: {valid_template}")

        return errors


# Default configuration instance
DEFAULT_CONVERSION_CONFIG = ConversionConfig()


# ============================================================================
# JSON SCHEMA FOR UI VALIDATION
# ============================================================================

CONVERSION_CONFIG_JSON_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "PDF Conversion Configuration",
    "description": "Configuration options for PDF to XML conversion",
    "type": "object",
    "properties": {
        "model": {
            "type": "string",
            "enum": [m.value for m in AIModel],
            "default": AIModel.SONNET_4.value,
            "description": "Claude AI model for conversion",
        },
        "dpi": {
            "type": "integer",
            "enum": [d.value for d in DPI],
            "default": DPI.STANDARD.value,
            "description": "PDF rendering resolution",
        },
        "temperature": {
            "type": "number",
            "enum": [t.value for t in Temperature],
            "default": Temperature.DETERMINISTIC.value,
            "description": "AI temperature (0 = deterministic)",
        },
        "batch_size": {
            "type": "integer",
            "enum": [b.value for b in BatchSize],
            "default": BatchSize.MEDIUM.value,
            "description": "Pages per processing batch",
        },
        "toc_depth": {
            "type": "integer",
            "enum": [t.value for t in TOCDepth],
            "default": TOCDepth.LEVEL_3.value,
            "description": "Table of contents depth",
        },
        "template_type": {
            "type": "string",
            "enum": [t.value for t in TemplateType],
            "default": TemplateType.AUTO.value,
            "description": "Document layout template",
        },
        "create_docx": {
            "type": "boolean",
            "default": True,
            "description": "Generate Word document",
        },
        "create_rittdoc": {
            "type": "boolean",
            "default": True,
            "description": "Generate RittDoc package",
        },
        "skip_extraction": {
            "type": "boolean",
            "default": False,
            "description": "Skip image/table extraction",
        },
        "include_toc": {
            "type": "boolean",
            "default": True,
            "description": "Include table of contents",
        },
        "use_hybrid": {
            "type": "boolean",
            "default": True,
            "description": "Enable hybrid mode: route complex pages (tables/images) to AI, simple pages to non-AI",
        },
    },
    "additionalProperties": False,
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_config_options_json() -> str:
    """Get configuration options as JSON for frontend consumption."""
    return json.dumps(CONVERSION_CONFIG_OPTIONS, indent=2)


def get_config_schema_json() -> str:
    """Get JSON schema for frontend validation."""
    return json.dumps(CONVERSION_CONFIG_JSON_SCHEMA, indent=2)


def get_default_config_json() -> str:
    """Get default configuration as JSON."""
    return json.dumps(DEFAULT_CONVERSION_CONFIG.to_dict(), indent=2)


# ============================================================================
# TYPESCRIPT/JAVASCRIPT EXPORT
# ============================================================================

def generate_typescript_types() -> str:
    """Generate TypeScript type definitions for UI projects."""
    return '''// Auto-generated from shared_config.py
// Do not edit manually - regenerate using: python shared_config.py --typescript

export type AIModel =
  | "claude-sonnet-4-20250514"
  | "claude-opus-4-5-20251101"
  | "claude-haiku-3-5-20241022";

export type DPI = 150 | 200 | 300 | 400 | 600;

export type Temperature = 0.0 | 0.1 | 0.2 | 0.3;

export type BatchSize = 5 | 10 | 15 | 20 | 25;

export type TOCDepth = 1 | 2 | 3 | 4 | 5;

export type TemplateType = "auto" | "single_column" | "double_column" | "mixed";

export interface ConversionConfig {
  model: AIModel;
  dpi: DPI;
  temperature: Temperature;
  batch_size: BatchSize;
  toc_depth: TOCDepth;
  template_type: TemplateType;
  create_docx: boolean;
  create_rittdoc: boolean;
  skip_extraction: boolean;
  include_toc: boolean;
  use_hybrid: boolean;
}

export const DEFAULT_CONVERSION_CONFIG: ConversionConfig = {
  model: "claude-sonnet-4-20250514",
  dpi: 300,
  temperature: 0.0,
  batch_size: 10,
  toc_depth: 3,
  template_type: "auto",
  create_docx: true,
  create_rittdoc: true,
  skip_extraction: false,
  include_toc: true,
  use_hybrid: true,
};

export interface ConfigOption {
  value: string | number | boolean;
  label: string;
  description?: string;
  default?: boolean;
}

export interface ConfigField {
  label: string;
  description: string;
  type: "dropdown" | "checkbox";
  required: boolean;
  default: string | number | boolean;
  options?: ConfigOption[];
}

export type ConversionConfigOptions = Record<keyof ConversionConfig, ConfigField>;
'''


def generate_typescript_options() -> str:
    """Generate TypeScript configuration options for UI dropdowns."""
    options_json = json.dumps(CONVERSION_CONFIG_OPTIONS, indent=2)
    return f'''// Auto-generated from shared_config.py
// Configuration options for UI dropdowns

export const CONVERSION_CONFIG_OPTIONS = {options_json} as const;
'''


# ============================================================================
# CLI
# ============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate configuration exports")
    parser.add_argument("--json-schema", action="store_true", help="Output JSON schema")
    parser.add_argument("--options", action="store_true", help="Output config options JSON")
    parser.add_argument("--defaults", action="store_true", help="Output default config JSON")
    parser.add_argument("--typescript", action="store_true", help="Output TypeScript types")
    parser.add_argument("--typescript-options", action="store_true", help="Output TypeScript options")
    parser.add_argument("--all", action="store_true", help="Output all formats")

    args = parser.parse_args()

    if args.all or args.json_schema:
        print("// JSON Schema")
        print(get_config_schema_json())
        print()

    if args.all or args.options:
        print("// Configuration Options")
        print(get_config_options_json())
        print()

    if args.all or args.defaults:
        print("// Default Configuration")
        print(get_default_config_json())
        print()

    if args.all or args.typescript:
        print(generate_typescript_types())
        print()

    if args.all or args.typescript_options:
        print(generate_typescript_options())
        print()

    if not any([args.json_schema, args.options, args.defaults, args.typescript, args.typescript_options, args.all]):
        parser.print_help()
