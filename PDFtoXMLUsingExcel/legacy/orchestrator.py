#!/usr/bin/env python3
"""
PDF to DOCX/XML Orchestrator

A standalone command-line tool for converting PDF documents to Word (DOCX) and
DocBook XML format using AI-powered conversion (OpenAI Vision API).

Usage:
    python orchestrator.py <pdf_path> [--pages 1,2,3] [--openai-key YOUR_KEY]

Output:
    - <filename>_converted.docx  (Word document)
    - <filename>_converted.xml   (DocBook 5 XML)
    - <filename>_converted.md    (Intermediate Markdown)

Requirements:
    - pip install -r requirements_standalone.txt
    - System: poppler-utils, pandoc
    - OpenAI API key (via --openai-key or OPENAI_API_KEY env var)
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Setup logging before imports
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("orchestrator")


def check_dependencies() -> List[str]:
    """Check for required dependencies and return list of missing ones."""
    missing = []

    # Python packages
    try:
        import pdf2image
    except ImportError:
        missing.append("pdf2image")

    try:
        import fitz
    except ImportError:
        missing.append("PyMuPDF")

    try:
        import openai
    except ImportError:
        missing.append("openai")

    try:
        import pypandoc
    except ImportError:
        missing.append("pypandoc")

    try:
        from PIL import Image
    except ImportError:
        missing.append("Pillow")

    # System dependencies
    import shutil
    if not shutil.which("pdftoppm"):
        missing.append("poppler-utils (system package)")

    if not shutil.which("pandoc"):
        missing.append("pandoc (system package)")

    return missing


def parse_page_numbers(pages_str: str) -> Optional[List[int]]:
    """
    Parse page number string into a list of integers.

    Supports:
        - Single pages: "1,2,3"
        - Ranges: "1-5"
        - Mixed: "1,3,5-10,15"

    Returns None if empty (meaning all pages).
    """
    if not pages_str or pages_str.lower() == "all":
        return None

    pages = set()
    parts = pages_str.replace(" ", "").split(",")

    for part in parts:
        if "-" in part:
            # Range: "1-5"
            try:
                start, end = part.split("-", 1)
                start, end = int(start), int(end)
                pages.update(range(start, end + 1))
            except ValueError:
                logger.warning(f"Invalid page range: {part}")
        else:
            # Single page
            try:
                pages.add(int(part))
            except ValueError:
                logger.warning(f"Invalid page number: {part}")

    return sorted(pages) if pages else None


async def run_conversion(
    pdf_path: str,
    page_numbers: Optional[List[int]],
    openai_api_key: str,
    output_dir: Optional[str] = None
) -> dict:
    """
    Run the AI-powered PDF conversion.

    Args:
        pdf_path: Path to the input PDF
        page_numbers: List of page numbers to convert (None = all)
        openai_api_key: OpenAI API key
        output_dir: Output directory (defaults to PDF's directory)

    Returns:
        Conversion metadata dict
    """
    # Import the service (after setting up the API key)
    from app.services.ai_pdf_conversion_service import AIPDFConversionService

    # Create service instance with API key
    service = AIPDFConversionService(openai_api_key=openai_api_key)

    # Run conversion
    success, error_msg, metadata = await service.convert_pdf_to_docx_local(
        pdf_path=pdf_path,
        output_dir=output_dir,
        page_numbers=page_numbers,
        run_qc=False  # Skip QC as requested
    )

    if not success:
        raise RuntimeError(f"Conversion failed: {error_msg}")

    return metadata


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Convert PDF to DOCX and DocBook XML using AI-powered conversion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Convert entire PDF
    python orchestrator.py document.pdf

    # Convert specific pages
    python orchestrator.py document.pdf --pages 1,2,3

    # Convert page range
    python orchestrator.py document.pdf --pages 1-10

    # Convert mixed pages
    python orchestrator.py document.pdf --pages 1,3,5-10,15

    # Specify API key
    python orchestrator.py document.pdf --openai-key sk-...

    # Custom output directory
    python orchestrator.py document.pdf --output-dir ./converted

Output files (in same directory as input PDF by default):
    - <filename>_converted.docx   Word document
    - <filename>_converted.xml    DocBook 5 XML
    - <filename>_converted.md     Intermediate Markdown
        """
    )

    parser.add_argument(
        "pdf_path",
        help="Path to the input PDF file"
    )

    parser.add_argument(
        "--pages", "-p",
        default=None,
        help="Page numbers to convert (e.g., '1,2,3' or '1-10' or '1,3,5-10'). Default: all pages"
    )

    parser.add_argument(
        "--openai-key", "-k",
        default=None,
        help="OpenAI API key. Can also be set via OPENAI_API_KEY environment variable"
    )

    parser.add_argument(
        "--output-dir", "-o",
        default=None,
        help="Output directory. Default: same directory as input PDF"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    parser.add_argument(
        "--check-deps",
        action="store_true",
        help="Check dependencies and exit"
    )

    args = parser.parse_args()

    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Check dependencies
    missing = check_dependencies()
    if args.check_deps:
        if missing:
            print("Missing dependencies:")
            for dep in missing:
                print(f"  - {dep}")
            sys.exit(1)
        else:
            print("All dependencies satisfied!")
            sys.exit(0)

    if missing:
        print("ERROR: Missing required dependencies:")
        for dep in missing:
            print(f"  - {dep}")
        print("\nPlease install with:")
        print("  pip install -r requirements_standalone.txt")
        print("\nFor system packages:")
        print("  Ubuntu/Debian: sudo apt-get install poppler-utils pandoc")
        print("  macOS: brew install poppler pandoc")
        sys.exit(1)

    # Validate PDF path
    pdf_path = Path(args.pdf_path).resolve()
    if not pdf_path.exists():
        print(f"ERROR: PDF file not found: {pdf_path}")
        sys.exit(1)

    if not pdf_path.suffix.lower() == ".pdf":
        print(f"ERROR: File does not appear to be a PDF: {pdf_path}")
        sys.exit(1)

    # Get OpenAI API key
    openai_key = args.openai_key or os.environ.get("OPENAI_API_KEY")
    if not openai_key:
        print("ERROR: OpenAI API key is required.")
        print("  Set via --openai-key argument or OPENAI_API_KEY environment variable")
        sys.exit(1)

    # Parse page numbers
    page_numbers = parse_page_numbers(args.pages) if args.pages else None

    # Print conversion info
    print("=" * 60)
    print("PDF to DOCX/XML Converter (AI-Powered)")
    print("=" * 60)
    print(f"Input PDF:    {pdf_path}")
    print(f"Pages:        {page_numbers if page_numbers else 'All'}")
    print(f"Output Dir:   {args.output_dir or pdf_path.parent}")
    print("=" * 60)

    # Run conversion
    start_time = datetime.now()
    try:
        metadata = asyncio.run(run_conversion(
            pdf_path=str(pdf_path),
            page_numbers=page_numbers,
            openai_api_key=openai_key,
            output_dir=args.output_dir
        ))

        duration = (datetime.now() - start_time).total_seconds()

        print("\n" + "=" * 60)
        print("CONVERSION COMPLETED SUCCESSFULLY")
        print("=" * 60)
        print(f"Duration:     {duration:.1f} seconds")
        print(f"Pages:        {metadata.get('pages_converted', 'N/A')}")
        print(f"Confidence:   {metadata.get('average_confidence_score', 'N/A')}%")
        print("-" * 60)
        print("Output Files:")
        print(f"  DOCX:       {metadata.get('output_docx', 'N/A')}")
        print(f"  XML:        {metadata.get('output_xml', 'N/A')}")
        print(f"  Markdown:   {metadata.get('output_markdown', 'N/A')}")
        print("=" * 60)

        # Save metadata to JSON
        metadata_path = Path(metadata.get('output_docx', '')).with_suffix('.metadata.json')
        if metadata_path:
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2, default=str)
            print(f"\nMetadata saved to: {metadata_path}")

    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        print("\n" + "=" * 60)
        print("CONVERSION FAILED")
        print("=" * 60)
        print(f"Duration:     {duration:.1f} seconds")
        print(f"Error:        {str(e)}")
        print("=" * 60)
        logger.exception("Conversion failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
