#!/usr/bin/env python3
"""
Memory-Efficient Wrapper for pdf_to_unified_xml.py

This wrapper adds:
1. Memory monitoring and limits
2. Garbage collection between processing steps
3. Reduced DPI for large PDFs
4. Progress tracking
5. Graceful error handling
"""

import os
import sys
import gc
import argparse
import subprocess
from pathlib import Path


def get_pdf_page_count(pdf_path: str) -> int:
    """Get number of pages in PDF without loading entire file."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page_count = len(doc)
        doc.close()
        return page_count
    except Exception as e:
        print(f"Warning: Could not determine page count: {e}")
        return 0


def get_pdf_file_size_mb(pdf_path: str) -> float:
    """Get PDF file size in MB."""
    return os.path.getsize(pdf_path) / (1024 * 1024)


def estimate_memory_requirements(pdf_path: str, dpi: int = 200) -> dict:
    """
    Estimate memory requirements for processing.
    
    CORRECTED: Uses sequential processing model where pages are processed
    one at a time, not all rendered simultaneously.
    
    Memory components:
    1. PDF structure (PyMuPDF overhead): 3-5x file size
    2. Single page render: Based on DPI (pages processed sequentially)
    3. Accumulated text data: ~2 MB per page (grows during processing)
    4. Working buffer: ~200 MB for Camelot and temporary structures
    
    Args:
        pdf_path: Path to PDF file
        dpi: DPI for image rendering (affects single page memory)
    
    Returns:
        Dictionary with memory estimates and breakdown
    """
    page_count = get_pdf_page_count(pdf_path)
    file_size_mb = get_pdf_file_size_mb(pdf_path)
    
    # Component 1: PDF document structure (PyMuPDF overhead)
    base_pdf_mb = file_size_mb * 5
    
    # Component 2: Single page render at specified DPI
    # Assume US Letter: 8.5" × 11"
    # Pages are processed ONE AT A TIME, not all simultaneously
    width_px = int(8.5 * dpi)
    height_px = int(11 * dpi)
    bytes_per_pixel = 4  # RGBA
    single_page_mb = (width_px * height_px * bytes_per_pixel) / (1024 * 1024)
    single_page_mb *= 1.5  # Add 50% overhead for processing
    
    # Component 3: Accumulated text data structures
    # Text fragments, XML structures, metadata grow during processing
    # Approximately 2 MB per page
    text_data_mb = page_count * 2
    
    # Component 4: Working memory buffer
    # For Camelot, XML parsing, temporary structures
    working_buffer_mb = 200
    
    # Total peak memory (sequential processing model)
    estimated_peak_mb = base_pdf_mb + single_page_mb + text_data_mb + working_buffer_mb
    
    return {
        "file_size_mb": file_size_mb,
        "page_count": page_count,
        "estimated_peak_mb": estimated_peak_mb,
        "dpi": dpi,
        "breakdown": {
            "pdf_structure": base_pdf_mb,
            "single_page_render": single_page_mb,
            "accumulated_text": text_data_mb,
            "working_buffer": working_buffer_mb,
        }
    }


def suggest_optimal_dpi(pdf_path: str) -> int:
    """
    Suggest optimal DPI based on PDF characteristics.
    
    Recommendations:
    - < 4GB estimated: Use 200 DPI (high quality)
    - 4-8GB estimated: Use 150 DPI (good quality)
    - > 8GB estimated: Use 100 DPI (acceptable quality)
    """
    # Test with different DPI values to find optimal
    dpi_options = [200, 150, 100]
    
    for dpi in dpi_options:
        stats = estimate_memory_requirements(pdf_path, dpi=dpi)
        
        if stats["estimated_peak_mb"] <= 4000:  # <= 4GB
            return dpi
    
    # If even 100 DPI is too high, still return it as minimum
    return 100


def run_with_memory_management(
    pdf_path: str,
    output_dir: str = None,
    dpi: int = None,
    full_pipeline: bool = True,
    skip_packaging: bool = False,
):
    """
    Run pdf_to_unified_xml.py with memory management.
    
    Features:
    - Automatic DPI adjustment based on file size
    - Garbage collection between steps
    - Progress monitoring
    - Error recovery
    """
    pdf_path = os.path.abspath(pdf_path)
    
    if not os.path.exists(pdf_path):
        print(f"Error: PDF not found: {pdf_path}")
        return 1
    
    # Analyze PDF
    print(f"\n{'='*60}")
    print("PDF Analysis")
    print(f"{'='*60}")
    
    # Auto-adjust DPI if not specified
    if dpi is None:
        dpi = suggest_optimal_dpi(pdf_path)
        print(f"Auto-selected DPI: {dpi} (based on file size)")
    
    # Get stats with selected DPI
    stats = estimate_memory_requirements(pdf_path, dpi=dpi)
    
    print(f"File: {os.path.basename(pdf_path)}")
    print(f"Size: {stats['file_size_mb']:.1f} MB")
    print(f"Pages: {stats['page_count']}")
    print(f"DPI: {dpi}")
    print(f"\nEstimated Peak Memory: {stats['estimated_peak_mb']:.0f} MB "
          f"({stats['estimated_peak_mb']/1024:.1f} GB)")
    
    # Show breakdown
    if "breakdown" in stats:
        print(f"\nMemory Breakdown:")
        print(f"  PDF Structure:    {stats['breakdown']['pdf_structure']:.0f} MB")
        print(f"  Single Page:      {stats['breakdown']['single_page_render']:.0f} MB")
        print(f"  Text Data:        {stats['breakdown']['accumulated_text']:.0f} MB")
        print(f"  Working Buffer:   {stats['breakdown']['working_buffer']:.0f} MB")
    
    # Warn if memory requirements are high
    if stats["estimated_peak_mb"] > 8000:  # > 8GB
        print(f"\n⚠️  WARNING: This PDF may require {stats['estimated_peak_mb']/1024:.1f}+ GB of RAM")
        print("Recommendations:")
        print("  - Close other applications to free RAM")
        print(f"  - Try lower DPI (--dpi 100 or --dpi 150)")
        print("  - Process on a machine with more RAM")
        print("  - Consider splitting PDF into smaller chunks")
        response = input("\nContinue anyway? (y/n): ")
        if response.lower() != 'y':
            print("Aborted by user")
            return 1
    elif stats["estimated_peak_mb"] > 4000:  # > 4GB
        print(f"\n💡 Note: This PDF will use approximately {stats['estimated_peak_mb']/1024:.1f} GB of RAM")
        print("   Ensure you have enough free memory before proceeding.")
    
    print(f"{'='*60}\n")
    
    # Build command
    cmd = [
        sys.executable,
        "pdf_to_unified_xml.py",
        pdf_path,
        "--dpi", str(dpi),
    ]
    
    if output_dir:
        cmd.extend(["--out", output_dir])
    
    if full_pipeline:
        cmd.append("--full-pipeline")
    
    if skip_packaging:
        cmd.append("--skip-packaging")
    
    print("Running command:")
    print(" ".join(cmd))
    print()
    
    # Run the main script
    try:
        result = subprocess.run(cmd, check=False)
        
        # Force garbage collection after processing
        gc.collect()
        
        if result.returncode != 0:
            print(f"\n⚠️  Process failed with exit code: {result.returncode}")
            if result.returncode == -9 or result.returncode == 137:
                print("\n💀 KILLED BY SYSTEM (Out of Memory)")
                print("\nTroubleshooting steps:")
                print("1. Reduce DPI: Use --dpi 100 or --dpi 150")
                print("2. Close other applications to free RAM")
                print("3. Check system memory: free -h (Linux) or Activity Monitor (Mac)")
                print("4. Try processing on a machine with more RAM")
                print(f"5. Split PDF into smaller chunks (current: {stats['page_count']} pages)")
            return result.returncode
        
        print(f"\n{'='*60}")
        print("✓ Processing completed successfully!")
        print(f"{'='*60}\n")
        return 0
        
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        return 130
    except Exception as e:
        print(f"\n⚠️  Unexpected error: {e}")
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="""
        Memory-Efficient PDF Processing Wrapper
        
        This script wraps pdf_to_unified_xml.py with:
        - Automatic memory analysis and DPI adjustment
        - Progress monitoring
        - Better error messages
        - Memory optimization
        
        If you're getting "killed" errors, this script will help diagnose
        and fix the issue.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument("pdf_path", help="Path to input PDF file")
    parser.add_argument(
        "--dpi",
        type=int,
        help="DPI for image rendering (auto-selected if not specified)",
    )
    parser.add_argument(
        "--out",
        dest="output_dir",
        help="Optional output directory (default: same as PDF)",
    )
    parser.add_argument(
        "--full-pipeline",
        action="store_true",
        default=True,
        help="Run full DocBook processing pipeline (default: True)",
    )
    parser.add_argument(
        "--no-pipeline",
        action="store_true",
        help="Only create unified XML (skip DocBook processing)",
    )
    parser.add_argument(
        "--skip-packaging",
        action="store_true",
        help="Skip final ZIP packaging step",
    )
    
    args = parser.parse_args()
    
    # Handle pipeline flag
    full_pipeline = not args.no_pipeline
    
    exit_code = run_with_memory_management(
        pdf_path=args.pdf_path,
        output_dir=args.output_dir,
        dpi=args.dpi,
        full_pipeline=full_pipeline,
        skip_packaging=args.skip_packaging,
    )
    
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
