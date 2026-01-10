#!/usr/bin/env python3
"""
Targeted DTD Fixer for Remaining Validation Errors

This script focuses on fixing specific error patterns that the comprehensive
fixer might have missed, particularly:
1. Figure content model issues (missing mediaobject/graphic)
2. Empty or malformed figures
3. Figure/mediaobject/textobject structure issues
4. Element ordering issues
"""

import re
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import List, Tuple, Set
from lxml import etree
from copy import deepcopy


class TargetedDTDFixer:
    """Fixes specific remaining DTD validation errors"""

    def __init__(self, dtd_path: Path):
        self.dtd_path = dtd_path
        self.dtd = etree.DTD(str(dtd_path))
        self.fixes_applied = []

    def fix_chapter_file(self, chapter_path: Path, chapter_filename: str) -> Tuple[int, List[str]]:
        """
        Apply targeted fixes to a chapter XML file.

        Returns:
            Tuple of (num_fixes, list_of_fix_descriptions)
        """
        fixes = []

        try:
            # Parse XML
            parser = etree.XMLParser(remove_blank_text=True)
            tree = etree.parse(str(chapter_path), parser)
            root = tree.getroot()

            # Apply fixes in order
            fixes.extend(self._fix_figure_content_model(root))
            fixes.extend(self._fix_empty_mediaobjects(root))
            fixes.extend(self._fix_textobject_issues(root))
            fixes.extend(self._fix_imageobject_issues(root))
            fixes.extend(self._fix_figure_ordering(root))
            fixes.extend(self._fix_missing_figure_title(root))
            fixes.extend(self._remove_problematic_figures(root))
            fixes.extend(self._fix_table_issues(root))
            fixes.extend(self._fix_sect_issues(root))

            # Save if fixes were applied
            if fixes:
                tree.write(
                    str(chapter_path),
                    encoding='utf-8',
                    xml_declaration=True,
                    pretty_print=True
                )

            return len(fixes), fixes

        except Exception as e:
            print(f"    Error processing {chapter_filename}: {e}")
            return 0, []

    def _fix_figure_content_model(self, root: etree._Element) -> List[str]:
        """
        Fix figure elements that don't match DTD content model.
        
        According to DocBook DTD, figure should contain:
        (blockinfo?, (title, titleabbrev?)?, (graphic+ | mediaobject+))
        """
        fixes = []
        
        for figure in root.xpath('.//figure'):
            # Check if figure has required content (graphic or mediaobject)
            has_graphic = len(figure.xpath('./graphic')) > 0
            has_mediaobject = len(figure.xpath('./mediaobject')) > 0
            
            if not has_graphic and not has_mediaobject:
                # Try to find imagedata and wrap it properly
                imagedata_elements = figure.xpath('.//imagedata')
                
                if imagedata_elements:
                    # Create proper structure: mediaobject > imageobject > imagedata
                    for imagedata in imagedata_elements:
                        # Check if already properly wrapped
                        parent = imagedata.getparent()
                        if parent is not None and parent.tag == 'imageobject':
                            grandparent = parent.getparent()
                            if grandparent is not None and grandparent.tag == 'mediaobject':
                                # Already properly structured
                                continue
                        
                        # Create proper wrapping
                        mediaobject = etree.Element('mediaobject')
                        imageobject = etree.SubElement(mediaobject, 'imageobject')
                        
                        # Move imagedata into imageobject
                        parent.remove(imagedata)
                        imageobject.append(imagedata)
                        # Ensure width and scalefit attributes are set
                        imagedata.set('width', '100%')
                        imagedata.set('scalefit', '1')

                        # Insert mediaobject after title (if exists) or at start
                        title_elem = figure.find('./title')
                        if title_elem is not None:
                            title_idx = list(figure).index(title_elem)
                            figure.insert(title_idx + 1, mediaobject)
                        else:
                            figure.insert(0, mediaobject)
                        
                        fixes.append(f"Wrapped imagedata in proper mediaobject/imageobject structure in figure")
                
                else:
                    # No imagedata found - check for textobject only
                    textobjects = figure.xpath('./mediaobject/textobject | ./textobject')
                    
                    if textobjects and not figure.xpath('./mediaobject/imageobject'):
                        # Figure has only textobject, no actual image - convert to para
                        parent = figure.getparent()
                        if parent is not None:
                            # Create para with figure content
                            para = etree.Element('para')
                            
                            # Add title as emphasis if it exists
                            title_elem = figure.find('./title')
                            if title_elem is not None and title_elem.text:
                                emphasis = etree.SubElement(para, 'emphasis', role='bold')
                                emphasis.text = title_elem.text
                                emphasis.tail = ": "
                            
                            # Add textobject content
                            for textobj in textobjects:
                                phrase = textobj.find('./phrase')
                                if phrase is not None and phrase.text:
                                    if para.text:
                                        para.text += " " + phrase.text
                                    elif len(para) > 0:
                                        para[-1].tail = (para[-1].tail or "") + phrase.text
                                    else:
                                        para.text = phrase.text
                            
                            # Replace figure with para
                            parent.replace(figure, para)
                            fixes.append(f"Converted figure with only textobject to para")
        
        return fixes

    def _fix_empty_mediaobjects(self, root: etree._Element) -> List[str]:
        """Remove or fix mediaobjects that are empty or contain only textobject"""
        fixes = []
        
        for mediaobject in root.xpath('.//mediaobject'):
            # Check if mediaobject has actual image content
            has_imageobject = len(mediaobject.xpath('./imageobject')) > 0
            has_videoobject = len(mediaobject.xpath('./videoobject')) > 0
            has_audioobject = len(mediaobject.xpath('./audioobject')) > 0
            
            has_content = has_imageobject or has_videoobject or has_audioobject
            
            if not has_content:
                # MediaObject has no actual media, only textobject
                parent = mediaobject.getparent()
                
                if parent is not None and parent.tag == 'figure':
                    # This is a placeholder figure - handle at figure level
                    continue
                
                # Remove empty mediaobject
                if parent is not None:
                    parent.remove(mediaobject)
                    fixes.append("Removed empty mediaobject (no image/video/audio content)")
        
        return fixes

    def _fix_textobject_issues(self, root: etree._Element) -> List[str]:
        """Fix textobject structure issues"""
        fixes = []
        
        for textobject in root.xpath('.//textobject'):
            # textobject should contain phrase or textdata, not direct text
            if textobject.text and textobject.text.strip():
                # Direct text in textobject - wrap in phrase
                phrase = etree.Element('phrase')
                phrase.text = textobject.text
                textobject.text = None
                textobject.insert(0, phrase)
                fixes.append("Wrapped direct text in textobject with phrase element")
            
            # Check if phrase exists, if not add one
            if len(textobject) == 0:
                phrase = etree.SubElement(textobject, 'phrase')
                phrase.text = "Content not available"
                fixes.append("Added missing phrase element to textobject")
        
        return fixes

    def _fix_imageobject_issues(self, root: etree._Element) -> List[str]:
        """Fix imageobject structure issues"""
        fixes = []
        
        for imageobject in root.xpath('.//imageobject'):
            # imageobject should contain imagedata
            if len(imageobject.xpath('./imagedata')) == 0:
                # No imagedata - add placeholder
                imagedata = etree.SubElement(imageobject, 'imagedata')
                imagedata.set('fileref', 'placeholder.png')
                imagedata.set('width', '100%')
                imagedata.set('scalefit', '1')
                fixes.append("Added missing imagedata to imageobject")
        
        return fixes

    def _fix_figure_ordering(self, root: etree._Element) -> List[str]:
        """
        Fix element ordering in figures.
        Correct order: (blockinfo?, (title, titleabbrev?)?, (graphic+ | mediaobject+))
        """
        fixes = []
        
        for figure in root.xpath('.//figure'):
            children = list(figure)
            if not children:
                continue
            
            # Extract elements by type
            title_elem = figure.find('./title')
            titleabbrev_elem = figure.find('./titleabbrev')
            mediaobjects = figure.xpath('./mediaobject')
            graphics = figure.xpath('./graphic')
            other_elements = [e for e in children 
                            if e.tag not in ['title', 'titleabbrev', 'mediaobject', 'graphic', 'blockinfo']]
            
            # Check if reordering is needed
            needs_reorder = False
            if title_elem is not None and children.index(title_elem) != 0:
                needs_reorder = True
            
            if needs_reorder or other_elements:
                # Clear and rebuild in correct order
                for child in children:
                    figure.remove(child)
                
                # Add in correct order
                if title_elem is not None:
                    figure.append(title_elem)
                if titleabbrev_elem is not None:
                    figure.append(titleabbrev_elem)
                
                for mediaobj in mediaobjects:
                    figure.append(mediaobj)
                
                for graphic in graphics:
                    figure.append(graphic)
                
                # Move other elements to parent (if they're valid there)
                parent = figure.getparent()
                if parent is not None:
                    fig_idx = list(parent).index(figure)
                    for elem in other_elements:
                        parent.insert(fig_idx + 1, elem)
                        fig_idx += 1
                
                fixes.append("Reordered figure children to match DTD content model")
        
        return fixes

    def _fix_missing_figure_title(self, root: etree._Element) -> List[str]:
        """Add missing titles to figures"""
        fixes = []
        
        for figure in root.xpath('.//figure'):
            title_elem = figure.find('./title')
            
            if title_elem is None:
                # Add title as first child
                title = etree.Element('title')
                title.text = "Figure"
                figure.insert(0, title)
                fixes.append("Added missing title to figure")
            elif not title_elem.text or not title_elem.text.strip():
                # Empty title
                title_elem.text = "Figure"
                fixes.append("Fixed empty figure title")
        
        return fixes

    def _remove_problematic_figures(self, root: etree._Element) -> List[str]:
        """Remove figures that can't be fixed"""
        fixes = []
        
        for figure in root.xpath('.//figure'):
            # Check if figure can be validated
            has_title = figure.find('./title') is not None
            has_content = (len(figure.xpath('./mediaobject')) > 0 or 
                          len(figure.xpath('./graphic')) > 0)
            
            # If figure still has no content after other fixes, remove it
            if not has_content:
                parent = figure.getparent()
                if parent is not None:
                    # Try to preserve title as para
                    if has_title:
                        title_elem = figure.find('./title')
                        if title_elem is not None and title_elem.text:
                            para = etree.Element('para')
                            emphasis = etree.SubElement(para, 'emphasis', role='bold')
                            emphasis.text = title_elem.text
                            
                            fig_idx = list(parent).index(figure)
                            parent.insert(fig_idx, para)
                    
                    parent.remove(figure)
                    fixes.append("Removed figure with no valid content (preserved title)")
        
        return fixes

    def _fix_table_issues(self, root: etree._Element) -> List[str]:
        """Fix common table structure issues"""
        fixes = []
        
        for table in root.xpath('.//table'):
            # Ensure table has title
            title_elem = table.find('./title')
            if title_elem is None:
                title = etree.Element('title')
                title.text = "Table"
                table.insert(0, title)
                fixes.append("Added missing title to table")
            
            # Fix tgroup issues
            for tgroup in table.xpath('./tgroup'):
                # Ensure cols attribute exists
                if 'cols' not in tgroup.attrib:
                    # Count columns from first row
                    first_row = tgroup.find('.//row')
                    if first_row is not None:
                        col_count = len(first_row.xpath('./entry'))
                        tgroup.set('cols', str(col_count))
                        fixes.append(f"Added missing cols={col_count} to tgroup")
        
        return fixes

    def _fix_sect_issues(self, root: etree._Element) -> List[str]:
        """Fix section structure issues"""
        fixes = []
        
        for sect in root.xpath('.//sect1 | .//sect2 | .//sect3 | .//sect4 | .//sect5'):
            # Ensure section has title as first child
            title_elem = sect.find('./title')
            
            if title_elem is None:
                title = etree.Element('title')
                title.text = "Section"
                sect.insert(0, title)
                fixes.append(f"Added missing title to {sect.tag}")
            elif list(sect).index(title_elem) != 0:
                # Title exists but not first - move it
                sect.remove(title_elem)
                sect.insert(0, title_elem)
                fixes.append(f"Moved title to first position in {sect.tag}")
        
        return fixes

    def fix_zip_package(self, input_zip: Path, output_zip: Path) -> Tuple[int, int, int]:
        """
        Fix all chapters in a ZIP package.
        
        Returns:
            Tuple of (files_processed, files_fixed, total_fixes)
        """
        files_processed = 0
        files_fixed = 0
        total_fixes = 0
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            # Extract ZIP
            print(f"Extracting {input_zip.name}...")
            with zipfile.ZipFile(input_zip, 'r') as zf:
                zf.extractall(tmpdir_path)
            
            # Find chapter files
            book_xml = tmpdir_path / "Book.XML"
            if not book_xml.exists():
                print("✗ Book.XML not found")
                return 0, 0, 0
            
            # Extract entity declarations
            entities = self._extract_entities(book_xml)
            print(f"Found {len(entities)} chapter files to fix\n")
            
            # Process each chapter
            for entity_name, filename in sorted(entities.items()):
                chapter_path = tmpdir_path / filename
                if not chapter_path.exists():
                    continue
                
                files_processed += 1
                num_fixes, fix_list = self.fix_chapter_file(chapter_path, filename)
                
                if num_fixes > 0:
                    files_fixed += 1
                    total_fixes += num_fixes
                    print(f"  ✓ {filename}: Applied {num_fixes} fix(es)")
                    
                    # Show first few fixes for this file
                    for fix in fix_list[:3]:
                        print(f"      - {fix}")
                    if len(fix_list) > 3:
                        print(f"      ... and {len(fix_list) - 3} more")
            
            # Create output ZIP
            print(f"\nCreating fixed ZIP: {output_zip.name}...")
            with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root_dir, dirs, files in tmpdir_path.walk():
                    for file in files:
                        file_path = root_dir / file
                        arcname = file_path.relative_to(tmpdir_path)
                        zf.write(file_path, arcname)
        
        return files_processed, files_fixed, total_fixes

    def _extract_entities(self, book_xml_path: Path) -> dict:
        """Extract entity declarations from Book.XML"""
        entities = {}
        with open(book_xml_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        doctype_match = re.search(r'<!DOCTYPE[^>]+\[(.*?)\]>', content, re.DOTALL)
        if doctype_match:
            doctype_content = doctype_match.group(1)
            entity_pattern = r'<!ENTITY\s+(\w+)\s+SYSTEM\s+"([^"]+)">'
            for match in re.finditer(entity_pattern, doctype_content):
                entities[match.group(1)] = match.group(2)
        
        return entities


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("Usage: python3 targeted_dtd_fixer.py <input.zip> [output.zip] [dtd_path]")
        print("\nExample:")
        print("  python3 targeted_dtd_fixer.py 9780989163286_rittdoc.zip 9780989163286_fixed.zip")
        sys.exit(1)
    
    input_zip = Path(sys.argv[1])
    if not input_zip.exists():
        print(f"✗ Error: Input ZIP not found: {input_zip}")
        sys.exit(1)
    
    # Determine output path
    if len(sys.argv) > 2:
        output_zip = Path(sys.argv[2])
    else:
        output_zip = input_zip.parent / f"{input_zip.stem}_targeted_fix.zip"
    
    # Determine DTD path
    if len(sys.argv) > 3:
        dtd_path = Path(sys.argv[3])
    else:
        dtd_path = Path("RITTDOCdtd/v1.1/RittDocBook.dtd")
    
    if not dtd_path.exists():
        print(f"✗ Error: DTD file not found: {dtd_path}")
        sys.exit(1)
    
    print(f"\n{'='*80}")
    print("TARGETED DTD FIXER")
    print(f"{'='*80}")
    print(f"Input:  {input_zip}")
    print(f"Output: {output_zip}")
    print(f"DTD:    {dtd_path}")
    print(f"{'='*80}\n")
    
    # Run fixer
    fixer = TargetedDTDFixer(dtd_path)
    files_processed, files_fixed, total_fixes = fixer.fix_zip_package(input_zip, output_zip)
    
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    print(f"Files processed: {files_processed}")
    print(f"Files fixed: {files_fixed}")
    print(f"Total fixes: {total_fixes}")
    print(f"\nOutput: {output_zip}")
    print(f"{'='*80}\n")
    
    if total_fixes > 0:
        print("✓ Fixes applied successfully!")
        print("\nNext steps:")
        print("  1. Re-run validation to check remaining errors")
        print("  2. If errors persist, use analyze_remaining_errors.py for details")
    else:
        print("⚠ No fixes were applied. The files may already be compliant.")


if __name__ == "__main__":
    main()
