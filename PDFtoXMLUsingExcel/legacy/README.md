# Legacy Code

This folder contains deprecated code from previous versions of the PDF conversion pipeline.

**Warning**: These files are no longer maintained and may not work with the current codebase.

## Why These Files Were Deprecated

The current pipeline uses `pdf_orchestrator.py` with Claude Vision AI for accurate text extraction. The legacy files used different approaches that are no longer recommended:

- **OpenAI-based pipeline** (`orchestrator.py`): Replaced with Claude Vision AI
- **Unified XML pipeline** (`pdf_to_unified_xml.py`, `pdf_to_rittdoc.py`): Replaced with semantic pipeline
- **Old extraction methods**: Replaced with AI-based extraction

## File Index

### Core Legacy Pipelines
- `orchestrator.py` - Old OpenAI-based orchestrator
- `pdf_to_unified_xml.py` - Legacy unified XML pipeline
- `pdf_to_excel_columns.py` - Old Excel column extraction
- `pdf_to_rittdoc.py` - Old RittDoc conversion
- `pdf_conversion_service.py` - Old conversion service

### Backup Files
- `Multipage_Image_Extractor_backup1.py` - Backup of image extractor
- `Multipage_Image_Extractor_1220bak.py` - Another backup

### Old Utilities
- `combine_phrases_in_para.py` - Old phrase combiner
- `combine_phrases_in_para_v2.py` - Version 2
- `phrase_combiner.py` - Another phrase combiner
- `debug_drawings.py` - Debug utilities
- `demonstrate_cropping.py` - Demo scripts
- `enhanced_word_split_fixer.py` - Word split fixing
- `heuristics_Nov3.py` - Old heuristics
- `implement_script_detection.py` - Script detection
- `link_processor.py` - Link processing
- `metadata_processor.py` - Metadata processing
- `pdf_mapper_wrapper.py` - Mapping wrapper
- `pdf_processor_memory_efficient.py` - Memory-efficient processor

### Old Validation/Fixing
- `table_structure_validator.py` - Table validation
- `table_validator_integration_example.py` - Integration example
- `targeted_dtd_fixer.py` - Targeted fixes
- `update_multimedia_with_validation.py` - Multimedia validation
- `validate_rittdoc.py` - RittDoc validation
- `validate_table_boundaries.py` - Table boundary validation
- `verify_index_fix.py` - Index verification
- `font_roles_auto.py` - Font role detection (inline in orchestrator now)

### Old Utilities
- `xslt_transformer.py` - XSLT transformation
- `launch_editor.py` - Editor launcher (use editor_server.py directly)

### Old Dependencies
- `requirements_standalone.txt` - Old requirements file

## Migration Guide

If you have scripts that depend on these files, migrate to:

1. Use `pdf_orchestrator.py` as the main entry point
2. Use `api.py` for programmatic access
3. Use `config.py` for configuration management

## Removal Policy

These files are kept for reference. They may be removed in a future version.
