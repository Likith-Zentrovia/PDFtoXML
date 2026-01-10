/**
 * PDF Conversion Configuration Types and Options
 *
 * This file is auto-generated from the pipeline's shared_config.py
 * Copy this file to your UI project for type-safe configuration.
 *
 * Usage:
 *   import {
 *     ConversionConfig,
 *     DEFAULT_CONVERSION_CONFIG,
 *     CONVERSION_CONFIG_OPTIONS,
 *   } from './conversion-config';
 */

// ============================================================================
// TYPE DEFINITIONS
// ============================================================================

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
}

// ============================================================================
// DEFAULT CONFIGURATION
// ============================================================================

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
};

// ============================================================================
// DROPDOWN OPTIONS
// ============================================================================

export interface ConfigOption<T = string | number | boolean> {
  value: T;
  label: string;
  description?: string;
  default?: boolean;
}

export interface ConfigField<T = string | number | boolean> {
  label: string;
  description: string;
  type: "dropdown" | "checkbox";
  required: boolean;
  default: T;
  options?: ConfigOption<T>[];
}

export const CONVERSION_CONFIG_OPTIONS = {
  model: {
    label: "AI Model",
    description: "Claude AI model used for document analysis and conversion",
    type: "dropdown" as const,
    required: true,
    default: "claude-sonnet-4-20250514",
    options: [
      {
        value: "claude-sonnet-4-20250514",
        label: "Claude Sonnet 4 (Recommended)",
        description: "Best balance of speed and quality. Recommended for most documents.",
        default: true,
      },
      {
        value: "claude-opus-4-5-20251101",
        label: "Claude Opus 4.5 (Highest Quality)",
        description: "Highest accuracy for complex documents. Slower and more expensive.",
        default: false,
      },
      {
        value: "claude-haiku-3-5-20241022",
        label: "Claude Haiku 3.5 (Fastest)",
        description: "Fastest processing. Good for simple documents or quick previews.",
        default: false,
      },
    ],
  },
  dpi: {
    label: "Resolution (DPI)",
    description: "PDF rendering resolution. Higher DPI = better quality but slower processing",
    type: "dropdown" as const,
    required: true,
    default: 300,
    options: [
      { value: 150, label: "150 DPI (Fast, Lower Quality)", default: false },
      { value: 200, label: "200 DPI (Balanced)", default: false },
      { value: 300, label: "300 DPI (Recommended)", default: true },
      { value: 400, label: "400 DPI (High Quality)", default: false },
      { value: 600, label: "600 DPI (Maximum Quality, Slower)", default: false },
    ],
  },
  temperature: {
    label: "AI Temperature",
    description: "Controls AI creativity. Use 0.0 for consistent, deterministic output",
    type: "dropdown" as const,
    required: true,
    default: 0.0,
    options: [
      { value: 0.0, label: "0.0 - Deterministic (Recommended)", default: true },
      { value: 0.1, label: "0.1 - Minimal Variation", default: false },
      { value: 0.2, label: "0.2 - Slight Variation", default: false },
      { value: 0.3, label: "0.3 - More Creative", default: false },
    ],
  },
  batch_size: {
    label: "Batch Size",
    description: "Number of pages processed per API call",
    type: "dropdown" as const,
    required: true,
    default: 10,
    options: [
      { value: 5, label: "5 pages (More API calls, lower memory)", default: false },
      { value: 10, label: "10 pages (Recommended)", default: true },
      { value: 15, label: "15 pages", default: false },
      { value: 20, label: "20 pages", default: false },
      { value: 25, label: "25 pages (Fewer API calls, higher memory)", default: false },
    ],
  },
  toc_depth: {
    label: "Table of Contents Depth",
    description: "How many heading levels to include in the TOC",
    type: "dropdown" as const,
    required: true,
    default: 3,
    options: [
      { value: 1, label: "Level 1 only (Chapters)", default: false },
      { value: 2, label: "Levels 1-2 (Chapters + Sections)", default: false },
      { value: 3, label: "Levels 1-3 (Recommended)", default: true },
      { value: 4, label: "Levels 1-4 (Detailed)", default: false },
      { value: 5, label: "Levels 1-5 (Maximum Detail)", default: false },
    ],
  },
  template_type: {
    label: "Document Template",
    description: "Expected document layout. Auto-detect works for most documents",
    type: "dropdown" as const,
    required: true,
    default: "auto",
    options: [
      { value: "auto", label: "Auto-detect (Recommended)", default: true },
      { value: "single_column", label: "Single Column", default: false },
      { value: "double_column", label: "Double Column", default: false },
      { value: "mixed", label: "Mixed Layout", default: false },
    ],
  },
  create_docx: {
    label: "Generate Word Document",
    description: "Create a .docx file from the converted content",
    type: "checkbox" as const,
    required: false,
    default: true,
  },
  create_rittdoc: {
    label: "Generate RittDoc Package",
    description: "Create a DTD-compliant RittDoc ZIP package",
    type: "checkbox" as const,
    required: false,
    default: true,
  },
  skip_extraction: {
    label: "Skip Image Extraction",
    description: "Skip extracting images (faster processing, no images in output)",
    type: "checkbox" as const,
    required: false,
    default: false,
  },
  include_toc: {
    label: "Include Table of Contents",
    description: "Generate a table of contents in the output",
    type: "checkbox" as const,
    required: false,
    default: true,
  },
} as const;

// ============================================================================
// HELPER FUNCTIONS
// ============================================================================

/**
 * Validate a conversion configuration object.
 * @returns Array of error messages (empty if valid)
 */
export function validateConfig(config: Partial<ConversionConfig>): string[] {
  const errors: string[] = [];

  const validModels = ["claude-sonnet-4-20250514", "claude-opus-4-5-20251101", "claude-haiku-3-5-20241022"];
  if (config.model && !validModels.includes(config.model)) {
    errors.push(`Invalid model: ${config.model}`);
  }

  const validDPI = [150, 200, 300, 400, 600];
  if (config.dpi && !validDPI.includes(config.dpi)) {
    errors.push(`Invalid DPI: ${config.dpi}`);
  }

  const validTemp = [0.0, 0.1, 0.2, 0.3];
  if (config.temperature !== undefined && !validTemp.includes(config.temperature)) {
    errors.push(`Invalid temperature: ${config.temperature}`);
  }

  const validBatch = [5, 10, 15, 20, 25];
  if (config.batch_size && !validBatch.includes(config.batch_size)) {
    errors.push(`Invalid batch_size: ${config.batch_size}`);
  }

  const validTOC = [1, 2, 3, 4, 5];
  if (config.toc_depth && !validTOC.includes(config.toc_depth)) {
    errors.push(`Invalid toc_depth: ${config.toc_depth}`);
  }

  const validTemplate = ["auto", "single_column", "double_column", "mixed"];
  if (config.template_type && !validTemplate.includes(config.template_type)) {
    errors.push(`Invalid template_type: ${config.template_type}`);
  }

  return errors;
}

/**
 * Merge partial config with defaults.
 */
export function mergeWithDefaults(partial: Partial<ConversionConfig>): ConversionConfig {
  return { ...DEFAULT_CONVERSION_CONFIG, ...partial };
}

/**
 * Convert config to FormData for API calls.
 *
 * IMPORTANT: Only sends fields that the API accepts.
 * The API accepts: file, model, dpi, temperature, batch_size, skip_extraction, skip_rittdoc
 *
 * Note: ANTHROPIC_API_KEY is configured on the SERVER, not sent from the UI.
 */
export function configToFormData(config: ConversionConfig, file: File): FormData {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("model", config.model);
  formData.append("dpi", config.dpi.toString());
  formData.append("temperature", config.temperature.toString());
  formData.append("batch_size", config.batch_size.toString());
  formData.append("skip_extraction", config.skip_extraction.toString());
  // Note: skip_rittdoc is the API field (inverse of create_rittdoc)
  formData.append("skip_rittdoc", (!config.create_rittdoc).toString());
  return formData;
}

// ============================================================================
// REACT HOOK (Optional - for React projects)
// ============================================================================

/**
 * Example React hook for managing conversion config state.
 * Uncomment and use in React projects.
 */
/*
import { useState, useCallback } from 'react';

export function useConversionConfig(initial?: Partial<ConversionConfig>) {
  const [config, setConfig] = useState<ConversionConfig>(
    mergeWithDefaults(initial || {})
  );

  const updateConfig = useCallback(<K extends keyof ConversionConfig>(
    key: K,
    value: ConversionConfig[K]
  ) => {
    setConfig(prev => ({ ...prev, [key]: value }));
  }, []);

  const resetConfig = useCallback(() => {
    setConfig(DEFAULT_CONVERSION_CONFIG);
  }, []);

  const errors = validateConfig(config);

  return {
    config,
    updateConfig,
    resetConfig,
    errors,
    isValid: errors.length === 0,
  };
}
*/
