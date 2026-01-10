# PDF to XML Conversion Pipeline (RittDoc)

A production-grade PDF-to-DocBook XML conversion pipeline using Claude Vision AI. This module converts PDF documents to validated RittDoc DTD-compliant DocBook XML packages with support for tables, images, and complex layouts.

## Features

- **Claude Vision AI Integration**: Uses Claude Sonnet/Opus for accurate text extraction with zero hallucinations (temperature 0.0)
- **Table Detection**: Automatic table extraction with proper DocBook formatting
- **Image Extraction**: High-DPI image extraction with proper referencing
- **DTD Validation**: Automated RittDoc DTD compliance with comprehensive fixing
- **Web Editor**: Professional side-by-side PDF and XML editing interface
- **REST API**: FastAPI-based API for UI integration
- **Batch Processing**: Support for large PDFs (1000+ pages) with progress saving

## Quick Start

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd PDFtoXMLUsingExcel

# Install Python dependencies
pip install -r requirements.txt

# Install system dependencies (Ubuntu/Debian)
sudo apt-get install poppler-utils ghostscript pandoc
```

### Environment Setup

```bash
# Required: Set your Anthropic API key
export ANTHROPIC_API_KEY="your-api-key-here"
```

### Basic Usage (CLI)

```bash
# Basic conversion
python pdf_orchestrator.py input.pdf --out ./output

# With web editor for manual corrections
python pdf_orchestrator.py input.pdf --out ./output --edit-mode

# Using Claude Opus for best accuracy
python pdf_orchestrator.py input.pdf --out ./output --model claude-opus-4-5-20251101
```

### REST API Usage

```bash
# Start the API server
uvicorn api:app --host 0.0.0.0 --port 8000

# Or use Python directly
python api.py
```

API Endpoints:
- `POST /api/v1/convert` - Upload and convert a PDF
- `GET /api/v1/jobs/{job_id}` - Get job status
- `GET /api/v1/jobs` - List all jobs
- `GET /api/v1/dashboard` - Get conversion statistics
- `GET /api/v1/jobs/{job_id}/files` - List output files
- `GET /api/v1/jobs/{job_id}/files/{filename}` - Download a file

### Python Module Usage

```python
from config import PipelineConfig, get_config

# Get current configuration
config = get_config()
print(f"Using model: {config.model}")
print(f"DPI: {config.dpi}")

# Or create custom configuration
config = PipelineConfig()
config.ai.model = "claude-opus-4-5-20251101"
config.rendering.dpi = 400
config.save("my_config.json")
```

## Project Structure

```
PDFtoXMLUsingExcel/
├── pdf_orchestrator.py        # Main CLI entry point
├── ai_pdf_conversion_service.py  # Claude Vision AI service
├── editor_server.py           # Web-based XML editor
├── api.py                     # FastAPI REST API
├── config.py                  # Configuration management
├── __init__.py               # Package initialization
│
├── rittdoc_core/             # Core library
│   ├── validation/           # DTD validation
│   ├── fixing/               # Error fixing
│   ├── packaging/            # ZIP packaging
│   ├── tracking/             # Conversion tracking
│   ├── mapping/              # Reference mapping
│   └── transform/            # XSLT transforms
│
├── editor_ui/                # Web editor frontend
│   ├── index.html
│   ├── app.js
│   └── styles.css
│
├── RITTDOCdtd/               # DTD schema files
│   └── v1.1/RittDocBook.dtd
│
├── docs/                     # Documentation
│   ├── EDITOR_README.md
│   ├── AI_PIPELINE.md
│   ├── RITTDOC_DTD_COMPATIBILITY.md
│   └── development/          # Development docs
│
├── samples/                  # Sample PDFs and test files
├── legacy/                   # Deprecated code (archived)
├── tests/                    # Test files
│
├── requirements.txt          # Python dependencies
└── README.md                # This file
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | (required) | Claude API key |
| `PDFTOXML_MODEL` | `claude-sonnet-4-20250514` | Claude model to use |
| `PDFTOXML_DPI` | `300` | PDF rendering DPI |
| `PDFTOXML_TEMPERATURE` | `0.0` | AI temperature (0 = no hallucinations) |
| `PDFTOXML_BATCH_SIZE` | `10` | Pages per batch |
| `PDFTOXML_OUTPUT_DIR` | `./output` | Output directory |
| `PDFTOXML_DTD_PATH` | `RITTDOCdtd/v1.1/RittDocBook.dtd` | DTD file path |

### Configuration File

Create a `config.json` file for persistent settings:

```json
{
    "ai": {
        "model": "claude-sonnet-4-20250514",
        "temperature": 0.0,
        "max_tokens": 8192
    },
    "rendering": {
        "dpi": 300,
        "crop_header_pct": 0.06,
        "crop_footer_pct": 0.06
    },
    "processing": {
        "batch_size": 10,
        "save_intermediate": true
    },
    "output": {
        "create_docx": true,
        "create_rittdoc_zip": true,
        "include_toc": true
    }
}
```

Load with: `python -c "from config import load_config; load_config('config.json')"`

## Pipeline Steps

1. **Image/Table Extraction** (`Multipage_Image_Extractor.py`)
   - Extracts images at high DPI
   - Detects and extracts tables using Camelot

2. **Claude Vision AI Conversion** (`ai_pdf_conversion_service.py`)
   - Renders each page at 300 DPI
   - Claude Vision API processes page-by-page
   - Creates intermediate Markdown, then DocBook XML

3. **Font Analysis** (inline in `pdf_orchestrator.py`)
   - Extracts font information using PyMuPDF
   - Derives heading roles for TOC generation

4. **Web Editor** (`editor_server.py`) - Optional
   - Side-by-side PDF and XML view
   - Manual corrections and validation

5. **RittDoc Compliance** (`rittdoc_compliance_pipeline.py`)
   - DTD validation with entity tracking
   - Comprehensive error fixing
   - Multiple iteration support

6. **Final Packaging**
   - Creates RittDoc-compliant ZIP package
   - Converts to DOCX via pandoc
   - Generates validation reports

## Output Files

For `input.pdf`, the pipeline produces:
- `input_intermediate.md` - Intermediate Markdown
- `input_docbook42.xml` - DocBook XML 4.2
- `input_font_info.json` - Font analysis
- `input_font_roles.json` - Heading roles
- `input_TOC.xml` - Standalone table of contents
- `input_rittdoc.zip` - RittDoc package
- `input.docx` - Word document
- `input_MultiMedia/` - Extracted images

## API Integration

The REST API is designed for integration with external UIs:

```python
import requests

# Upload and convert a PDF
with open("document.pdf", "rb") as f:
    response = requests.post(
        "http://localhost:8000/api/v1/convert",
        files={"file": f},
        data={"model": "claude-sonnet-4-20250514", "dpi": 300}
    )
job = response.json()
job_id = job["job_id"]

# Poll for completion
while True:
    status = requests.get(f"http://localhost:8000/api/v1/jobs/{job_id}").json()
    if status["status"] in ("completed", "failed"):
        break
    time.sleep(5)

# Download results
files = requests.get(f"http://localhost:8000/api/v1/jobs/{job_id}/files").json()
for file in files["files"]:
    content = requests.get(file["download_url"]).content
    # Save file...

# Get dashboard statistics
dashboard = requests.get("http://localhost:8000/api/v1/dashboard").json()
print(f"Total conversions: {dashboard['total_conversions']}")
```

## Web Editor

Launch the web-based editor for manual corrections:

```bash
# Via orchestrator
python pdf_orchestrator.py input.pdf --out ./output --edit-mode

# Direct launch
python editor_server.py input.pdf output/input_docbook42.xml --port 5000
```

Features:
- Side-by-side PDF and XML view
- Monaco editor with XML syntax highlighting
- Real-time HTML preview
- Screenshot capture for image replacement
- Auto-reprocessing on save

## Troubleshooting

### Common Issues

1. **"PyMuPDF (fitz) not available"**
   ```bash
   pip install pymupdf
   ```

2. **"pandoc not found"**
   ```bash
   sudo apt-get install pandoc
   ```

3. **Rate limiting with Claude API**
   - Reduce `batch_size` to 5
   - Use `parallel_workers=1`

4. **Large PDFs failing**
   - Enable `save_intermediate=true`
   - Use `--resume-from-page N` to continue

### Debug Mode

Enable debug output:
```bash
export PDFTOXML_DEBUG=true
python pdf_orchestrator.py input.pdf --out ./output
```

## Legacy Code

The `legacy/` folder contains deprecated code from previous pipeline versions:
- `orchestrator.py` - Old OpenAI-based pipeline
- `pdf_to_unified_xml.py` - Old unified XML pipeline
- Various backup and experimental files

These are retained for reference but are not part of the active pipeline.

## License

[Add your license here]

## Contributing

[Add contribution guidelines here]
