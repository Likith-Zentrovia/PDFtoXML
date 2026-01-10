#!/usr/bin/env python3
"""
Complete RittDoc DTD Compliance Pipeline

This script orchestrates the entire process of converting XML DocBook packages
to fully RittDoc DTD-compliant format with automatic validation and fixing.

Process:
1. Initial validation (pre-fix assessment)
2. Apply comprehensive DTD fixes
3. Post-fix validation  
4. Generate validation reports
5. Create final compliant ZIP package

Usage:
    python rittdoc_compliance_pipeline.py <input.zip>
    python rittdoc_compliance_pipeline.py <input.zip> --output <output.zip>
"""

import argparse
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, Optional

from lxml import etree

# Import all the tools we need
from comprehensive_dtd_fixer import ComprehensiveDTDFixer, process_zip_package as fix_zip_package
from add_toc_to_book import add_toc_to_book_xml

# Use rittdoc_core validator (cleaner architecture with base classes)
try:
    from rittdoc_core.validation.dtd_validator import EntityTrackingValidator
    from rittdoc_core.validation.report import ValidationReportGenerator, VerificationItem
    RITTDOC_CORE_AVAILABLE = True
except ImportError:
    # Fall back to standalone modules
    from validate_with_entity_tracking import EntityTrackingValidator
    from validation_report import ValidationReportGenerator, VerificationItem
    RITTDOC_CORE_AVAILABLE = False


class RittDocCompliancePipeline:
    """Complete pipeline for RittDoc DTD compliance"""
    
    def __init__(self, dtd_path: Path):
        self.dtd_path = dtd_path
        self.stats = {
            'initial_errors': 0,
            'final_errors': 0,
            'total_fixes_applied': 0,
            'files_processed': 0,
            'files_fixed': 0,
            'validation_passes': 0
        }
        
    def run(self, 
            input_zip: Path, 
            output_zip: Optional[Path] = None,
            max_iterations: int = 3) -> bool:
        """
        Run the complete compliance pipeline.
        
        Args:
            input_zip: Input ZIP package
            output_zip: Output ZIP package (default: <input>_rittdoc_compliant.zip)
            max_iterations: Maximum number of fix/validate iterations
            
        Returns:
            True if fully compliant, False otherwise
        """
        
        if output_zip is None:
            output_zip = input_zip.parent / f"{input_zip.stem}_rittdoc_compliant.zip"
            
        print("=" * 80)
        print("RITTDOC DTD COMPLIANCE PIPELINE")
        print("=" * 80)
        print(f"Input:  {input_zip}")
        print(f"Output: {output_zip}")
        print(f"DTD:    {self.dtd_path}")
        print("=" * 80)
        
        # Step 1: Initial validation
        print("\n" + "=" * 80)
        print("STEP 1: INITIAL VALIDATION (PRE-FIX)")
        print("=" * 80)
        
        validator = EntityTrackingValidator(self.dtd_path)
        pre_report = validator.validate_zip_package(input_zip, output_report_path=None)
        self.stats['initial_errors'] = pre_report.get_error_count()
        
        print(f"\n✗ Found {self.stats['initial_errors']} validation errors before fixes")
        
        if self.stats['initial_errors'] == 0:
            print("\n✓ Package is already DTD-compliant!")
            # Just copy to output
            import shutil
            shutil.copy2(input_zip, output_zip)
            return True
        
        # Show error breakdown
        self._print_error_breakdown(pre_report)
        
        # Step 2: Apply fixes (iterative)
        working_zip = input_zip
        
        for iteration in range(1, max_iterations + 1):
            print("\n" + "=" * 80)
            print(f"STEP 2.{iteration}: APPLYING COMPREHENSIVE DTD FIXES (Iteration {iteration})")
            print("=" * 80)
            
            # Create temporary output for this iteration
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
                iteration_output = Path(tmp.name)
            
            # Apply comprehensive fixes
            fixer = ComprehensiveDTDFixer(self.dtd_path)
            fix_stats = self._apply_comprehensive_fixes(working_zip, iteration_output, fixer)
            
            self.stats['total_fixes_applied'] += fix_stats.get('total_fixes', 0)
            self.stats['files_processed'] = fix_stats.get('files_processed', 0)
            self.stats['files_fixed'] = fix_stats.get('files_fixed', 0)
            
            print(f"\nIteration {iteration} Summary:")
            print(f"  Files processed: {fix_stats.get('files_processed', 0)}")
            print(f"  Files fixed: {fix_stats.get('files_fixed', 0)}")
            print(f"  Fixes applied: {fix_stats.get('total_fixes', 0)}")
            
            # Step 3: Post-fix validation
            print("\n" + "=" * 80)
            print(f"STEP 3.{iteration}: POST-FIX VALIDATION (Iteration {iteration})")
            print("=" * 80)
            
            post_report = validator.validate_zip_package(iteration_output, output_report_path=None)
            errors_after = post_report.get_error_count()
            
            print(f"\nValidation after iteration {iteration}:")
            print(f"  Errors remaining: {errors_after}")
            print(f"  Errors fixed: {self.stats['initial_errors'] - errors_after}")
            print(f"  Improvement: {((self.stats['initial_errors'] - errors_after) / self.stats['initial_errors'] * 100):.1f}%")
            
            # If no errors, we're done!
            if errors_after == 0:
                print("\n✓ Package is now fully DTD-compliant!")
                # Copy to final output
                import shutil
                shutil.copy2(iteration_output, output_zip)
                iteration_output.unlink()
                self.stats['final_errors'] = 0
                self.stats['validation_passes'] = iteration
                break
            
            # If no improvement, stop iterating
            if errors_after >= self.stats['initial_errors']:
                print(f"\n⚠ No improvement after iteration {iteration}. Stopping.")
                import shutil
                shutil.copy2(iteration_output, output_zip)
                iteration_output.unlink()
                self.stats['final_errors'] = errors_after
                self.stats['validation_passes'] = iteration
                break
            
            # Update working zip for next iteration
            working_zip = iteration_output
            self.stats['initial_errors'] = errors_after  # Update baseline
            
            if iteration == max_iterations:
                # Last iteration - save as output
                import shutil
                shutil.copy2(iteration_output, output_zip)
                iteration_output.unlink()
                self.stats['final_errors'] = errors_after
                self.stats['validation_passes'] = iteration
        
        # Step 4: Add TOC to Book.XML (if not present)
        print("\n" + "=" * 80)
        print("STEP 4: ADDING TABLE OF CONTENTS")
        print("=" * 80)
        
        self._add_toc_to_package(output_zip)
        
        # Step 5: Final validation and report
        print("\n" + "=" * 80)
        print("STEP 5: FINAL VALIDATION & REPORT GENERATION")
        print("=" * 80)
        
        final_report = validator.validate_zip_package(
            output_zip, 
            output_report_path=output_zip.parent / f"{output_zip.stem}_validation_report.xlsx"
        )
        
        self.stats['final_errors'] = final_report.get_error_count()
        
        # Print final summary
        print("\n" + "=" * 80)
        print("PIPELINE COMPLETE - FINAL SUMMARY")
        print("=" * 80)
        print(f"Files processed:        {self.stats['files_processed']}")
        print(f"Files fixed:            {self.stats['files_fixed']}")
        print(f"Total fixes applied:    {self.stats['total_fixes_applied']}")
        print(f"Validation iterations:  {self.stats['validation_passes']}")
        print()
        print(f"Initial errors:         {pre_report.get_error_count()}")
        print(f"Final errors:           {self.stats['final_errors']}")
        print(f"Errors fixed:           {pre_report.get_error_count() - self.stats['final_errors']}")
        
        if self.stats['final_errors'] == 0:
            improvement = 100.0
        else:
            improvement = ((pre_report.get_error_count() - self.stats['final_errors']) / pre_report.get_error_count() * 100)
        
        print(f"Improvement:            {improvement:.1f}%")
        print()
        print(f"Output package:         {output_zip}")
        
        if self.stats['final_errors'] > 0:
            print(f"Validation report:      {output_zip.parent / f'{output_zip.stem}_validation_report.xlsx'}")
        
        print("=" * 80)
        
        if self.stats['final_errors'] == 0:
            print("\n✓ SUCCESS: Package is fully RittDoc DTD compliant!")
            return True
        else:
            print(f"\n⚠ WARNING: {self.stats['final_errors']} validation error(s) remain")
            print("Review the validation report for details.")
            self._print_error_breakdown(final_report)
            return False
    
    def _print_error_breakdown(self, report: ValidationReportGenerator):
        """Print a breakdown of errors by type"""
        if not report.has_errors():
            return
        
        # Group by error type
        error_types = {}
        for error in report.errors:
            error_types[error.error_type] = error_types.get(error.error_type, 0) + 1
        
        print("\nError breakdown by type:")
        for error_type, count in sorted(error_types.items(), key=lambda x: -x[1])[:10]:
            print(f"  {error_type}: {count}")
        
        # Show sample errors
        if len(report.errors) > 0:
            print("\nSample errors (first 5):")
            for i, error in enumerate(report.errors[:5], 1):
                location = f"line {error.line_number}" if error.line_number else "unknown location"
                print(f"  {i}. {error.xml_file} ({location}): {error.error_description[:80]}")
    
    def _apply_comprehensive_fixes(
        self, 
        input_zip: Path, 
        output_zip: Path,
        fixer: ComprehensiveDTDFixer
    ) -> Dict:
        """Apply comprehensive DTD fixes to a package"""
        
        stats = {
            'files_processed': 0,
            'files_fixed': 0,
            'total_fixes': 0
        }
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            extract_dir = tmp_path / "extracted"
            extract_dir.mkdir()
            
            # Extract ZIP
            print(f"Extracting {input_zip.name}...")
            with zipfile.ZipFile(input_zip, 'r') as zf:
                zf.extractall(extract_dir)
            
            # Find all chapter XML files
            chapter_files = list(extract_dir.rglob("ch*.xml"))
            print(f"Found {len(chapter_files)} chapter files to fix")
            
            for chapter_file in sorted(chapter_files):
                stats['files_processed'] += 1
                
                num_fixes, fix_descriptions = fixer.fix_chapter_file(chapter_file, chapter_file.name)
                
                if num_fixes > 0:
                    stats['files_fixed'] += 1
                    stats['total_fixes'] += num_fixes
                    print(f"  ✓ {chapter_file.name}: Applied {num_fixes} fix(es)")
            
            # Recreate ZIP
            print(f"\nCreating fixed ZIP: {output_zip.name}...")
            with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file_path in extract_dir.rglob('*'):
                    if file_path.is_file():
                        arcname = file_path.relative_to(extract_dir)
                        zf.write(file_path, arcname)
        
        return stats
    
    def _add_toc_to_package(self, zip_path: Path):
        """Add TOC to Book.XML in the package"""
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            extract_dir = tmp_path / "extracted"
            extract_dir.mkdir()
            
            # Extract ZIP
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(extract_dir)
            
            # Find Book.XML
            book_xml_files = list(extract_dir.rglob("Book.XML"))
            if not book_xml_files:
                print("  ⚠ Book.XML not found - skipping TOC addition")
                return
            
            book_xml_path = book_xml_files[0]
            
            # Check if TOC already exists
            try:
                tree = etree.parse(str(book_xml_path))
                root = tree.getroot()
                if root.find('.//toc') is not None:
                    print("  ℹ TOC already exists in Book.XML - skipping")
                    return
            except Exception as e:
                print(f"  ⚠ Error checking for existing TOC: {e}")
                return
            
            # Add TOC
            try:
                success = add_toc_to_book_xml(book_xml_path, book_xml_path.parent)
                if success:
                    print("  ✓ Added TOC to Book.XML")
                else:
                    print("  ⚠ Failed to add TOC to Book.XML")
                    return
            except Exception as e:
                print(f"  ⚠ Error adding TOC: {e}")
                return
            
            # Recreate ZIP with updated Book.XML
            print(f"  Updating ZIP package...")
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file_path in extract_dir.rglob('*'):
                    if file_path.is_file():
                        arcname = file_path.relative_to(extract_dir)
                        zf.write(file_path, arcname)


def main():
    parser = argparse.ArgumentParser(
        description="Complete RittDoc DTD Compliance Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Basic usage:
    python rittdoc_compliance_pipeline.py mybook.zip

  With custom output:
    python rittdoc_compliance_pipeline.py mybook.zip --output mybook_compliant.zip

  With custom DTD:
    python rittdoc_compliance_pipeline.py mybook.zip --dtd custom.dtd

  With more iterations:
    python rittdoc_compliance_pipeline.py mybook.zip --iterations 5
        """
    )
    
    parser.add_argument(
        "input",
        help="Input ZIP package"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output ZIP package (default: <input>_rittdoc_compliant.zip)"
    )
    parser.add_argument(
        "--dtd",
        default="RITTDOCdtd/v1.1/RittDocBook.dtd",
        help="Path to DTD file (default: RITTDOCdtd/v1.1/RittDocBook.dtd)"
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=3,
        help="Maximum number of fix/validate iterations (default: 3)"
    )
    
    args = parser.parse_args()
    
    # Validate inputs
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    
    dtd_path = Path(args.dtd)
    if not dtd_path.exists():
        print(f"Error: DTD file not found: {dtd_path}", file=sys.stderr)
        sys.exit(1)
    
    output_path = Path(args.output) if args.output else None
    
    # Run pipeline
    pipeline = RittDocCompliancePipeline(dtd_path)
    success = pipeline.run(input_path, output_path, max_iterations=args.iterations)
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
