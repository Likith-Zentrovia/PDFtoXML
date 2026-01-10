#!/usr/bin/env python3
"""
Comprehensive DTD Fixer for RittDoc

This script applies comprehensive fixes to resolve ALL common DTD validation errors:
1. Invalid content models (wrap/reorder elements)
2. Missing required elements (add defaults)
3. Invalid/undeclared elements (remove or convert)
4. Empty elements (add minimal content or remove)
5. Missing required attributes (add defaults)
6. Invalid attribute values (fix or remove)

The fixer runs validation before and after fixes to show improvement.
"""

import re
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import List, Tuple, Dict, Set
from lxml import etree
from copy import deepcopy

try:
    from validation_report import ValidationReportGenerator, VerificationItem, ValidationError
    VALIDATION_REPORT_AVAILABLE = True
except ImportError:
    VALIDATION_REPORT_AVAILABLE = False

try:
    from validate_with_entity_tracking import EntityTrackingValidator
    VALIDATION_AVAILABLE = True
except ImportError:
    VALIDATION_AVAILABLE = False


class ComprehensiveDTDFixer:
    """Comprehensive DTD fixer that handles all common validation errors"""

    def __init__(self, dtd_path: Path):
        self.dtd_path = dtd_path
        self.dtd = etree.DTD(str(dtd_path))
        self.fixes_applied = []
        self.verification_items = []

    @staticmethod
    def _local_name(element: etree._Element) -> str:
        """Extract local name from element tag."""
        tag = element.tag
        if not isinstance(tag, str):
            return ""
        if tag.startswith("{"):
            return tag.split("}", 1)[1]
        return tag

    @staticmethod
    def _is_inline_only(element: etree._Element) -> bool:
        """
        Check if an element contains only inline content (no block elements).

        Block elements include: itemizedlist, orderedlist, figure, table, sect*, etc.
        Inline elements include: emphasis, ulink, subscript, superscript, anchor, etc.
        """
        BLOCK_ELEMENTS = {
            'itemizedlist', 'orderedlist', 'variablelist', 'simplelist',
            'figure', 'informalfigure', 'table', 'informaltable',
            'example', 'informalexample',
            'programlisting', 'screen', 'literallayout',
            'blockquote', 'note', 'warning', 'caution', 'important', 'tip',
            'sect1', 'sect2', 'sect3', 'sect4', 'sect5', 'section',
            'para'  # Nested para is also a block element
        }

        # Check all child elements
        for child in element:
            if isinstance(child.tag, str):
                local = ComprehensiveDTDFixer._local_name(child)
                if local in BLOCK_ELEMENTS:
                    return False
                # Recursively check children
                if not ComprehensiveDTDFixer._is_inline_only(child):
                    return False

        return True

    def fix_chapter_file(self, chapter_path: Path, chapter_filename: str) -> Tuple[int, List[str]]:
        """
        Apply comprehensive fixes to a chapter XML file.

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
            # NOTE: Image filtering disabled - all filtering done in Multipage_Image_Extractor
            # fixes.extend(self._remove_empty_mediaobjects(root, chapter_filename))
            # Convert informalfigure to figure (RittDoc DTD excludes informalfigure)
            fixes.extend(self._fix_informalfigure_to_figure(root, chapter_filename))
            # Convert informaltable to table (RittDoc DTD excludes informaltable)
            fixes.extend(self._fix_informaltable_to_table(root, chapter_filename))
            # Fix imagedata elements that need to be wrapped in mediaobject/imageobject
            fixes.extend(self._fix_unwrapped_imagedata(root, chapter_filename))
            # Convert section elements to sect1 at chapter level
            fixes.extend(self._fix_section_to_sect1(root, chapter_filename))
            # Convert HTML-style tables to CALS format
            fixes.extend(self._fix_html_tables_to_cals(root, chapter_filename))
            # Handle figures with no media content (e.g., caption-only figures)
            fixes.extend(self._remove_misclassified_table_figures(root, chapter_filename))
            # Remove empty table rows
            fixes.extend(self._remove_empty_rows(root, chapter_filename))
            # Fix nested para elements (important for preserving links)
            fixes.extend(self._fix_nested_para_elements(root, chapter_filename))
            # Convert bridgehead elements to proper section structure (BEFORE other fixes)
            fixes.extend(self._fix_bridgehead_to_sections(root, chapter_filename))
            # Then apply other fixes
            fixes.extend(self._fix_missing_titles(root, chapter_filename))
            # Fix figure content model (must have title before content)
            fixes.extend(self._fix_figure_content_model(root, chapter_filename))
            fixes.extend(self._fix_invalid_content_models(root, chapter_filename))
            fixes.extend(self._fix_empty_elements(root, chapter_filename))
            fixes.extend(self._fix_missing_required_attributes(root, chapter_filename))
            # Add ID to chapter element if missing (must be before sect IDs so they can use chapter ID as prefix)
            fixes.extend(self._fix_chapter_missing_ids(root, chapter_filename))
            # Add IDs to sect1-5 elements that are missing them
            fixes.extend(self._fix_sect_missing_ids(root, chapter_filename))
            fixes.extend(self._fix_invalid_elements(root, chapter_filename))
            # Remove malformed sections with single-character symbol titles (PDF conversion artifacts)
            fixes.extend(self._fix_malformed_symbol_sections(root, chapter_filename))
            # Fix invalid link references (page_XXX links pointing to non-existent IDs)
            fixes.extend(self._fix_invalid_link_references(root, chapter_filename))
            # Fix nested sect1 elements (sect1 inside sect1 is invalid - convert to sect2 or unnest)
            fixes.extend(self._fix_nested_sect1_elements(root, chapter_filename))
            # NOTE: Empty figure removal disabled - all filtering done in Multipage_Image_Extractor
            # fixes.extend(self._remove_empty_figures(root, chapter_filename))
            fixes.extend(self._normalize_whitespace(root, chapter_filename))

            # Always write back to ensure XML declaration is present
            # (even if no other fixes were needed)
            tree.write(
                str(chapter_path),
                encoding='utf-8',
                xml_declaration=True,
                pretty_print=True
            )

            return len(fixes), fixes

        except Exception as e:
            print(f"  ✗ Error fixing {chapter_filename}: {e}")
            return 0, []

    def _fix_missing_titles(self, root: etree._Element, filename: str) -> List[str]:
        """Fix elements that require <title> but are missing it"""
        fixes = []

        # Elements that require titles in RittDoc DTD
        title_required = ['chapter', 'sect1', 'sect2', 'sect3', 'sect4', 'sect5',
                          'figure', 'table', 'example', 'appendix']

        for elem_name in title_required:
            for elem in root.iter(elem_name):
                # Check if ANY <title> exists among children (not just first)
                # This handles cases where invalid elements like <label> come before <title>
                has_title = any(child.tag == 'title' for child in elem)

                if not has_title:
                    # Create and insert empty title - DTD supports empty <title/> elements
                    title = etree.Element('title')
                    # Leave title.text as None for empty <title/>

                    # Insert as first child
                    elem.insert(0, title)

                    fixes.append(f"Added missing empty <title> to <{elem_name}> in {filename}")

                    # Add verification item
                    if VALIDATION_REPORT_AVAILABLE:
                        self.verification_items.append(VerificationItem(
                            xml_file=filename,
                            line_number=elem.sourceline if hasattr(elem, 'sourceline') else None,
                            fix_type="Missing Title Fix",
                            fix_description=f"Added empty <title/> element",
                            verification_reason="Title element was missing. Empty title added for DTD compliance.",
                            suggestion="Consider adding a descriptive title if appropriate."
                        ))

        return fixes

    def _fix_informalfigure_to_figure(self, root: etree._Element, filename: str) -> List[str]:
        """
        Convert informalfigure elements to figure elements.

        The RittDoc DTD explicitly excludes informalfigure:
        <!ENTITY % informalfigure.module "IGNORE">

        This method converts all <informalfigure> elements to <figure> with an empty title.
        """
        fixes = []

        # Find all informalfigure elements
        informalfigures = list(root.iter('informalfigure'))

        for informal in informalfigures:
            # Create new figure element with same attributes
            figure = etree.Element('figure')
            for key, value in informal.attrib.items():
                figure.set(key, value)

            # Add empty title as first child (required by DTD)
            title = etree.SubElement(figure, 'title')

            # Move all children from informalfigure to figure
            for child in list(informal):
                figure.append(child)

            # Replace informalfigure with figure
            parent = informal.getparent()
            if parent is not None:
                index = list(parent).index(informal)
                parent.remove(informal)
                parent.insert(index, figure)

                fixes.append(f"{filename}: Converted <informalfigure> to <figure> (RittDoc DTD excludes informalfigure)")

        return fixes

    def _fix_informaltable_to_table(self, root: etree._Element, filename: str) -> List[str]:
        """
        Convert informaltable elements to table elements.

        The RittDoc DTD explicitly excludes informaltable:
        <!ENTITY % informaltable.module "IGNORE">

        This method converts all <informaltable> elements to <table> with an empty title.
        """
        fixes = []

        # Find all informaltable elements
        informaltables = list(root.iter('informaltable'))

        for informal in informaltables:
            # Create new table element with same attributes
            table = etree.Element('table')
            for key, value in informal.attrib.items():
                table.set(key, value)

            # Add empty title as first child (required by DTD)
            title = etree.SubElement(table, 'title')

            # Move all children from informaltable to table
            for child in list(informal):
                table.append(child)

            # Replace informaltable with table
            parent = informal.getparent()
            if parent is not None:
                index = list(parent).index(informal)
                parent.remove(informal)
                parent.insert(index, table)

                fixes.append(f"{filename}: Converted <informaltable> to <table> (RittDoc DTD excludes informaltable)")

        return fixes

    def _fix_figure_content_model(self, root: etree._Element, filename: str) -> List[str]:
        """
        Fix figure elements with invalid content model.

        The DTD expects figure content to be:
        (blockinfo?, (title, titleabbrev?), (content elements)+)

        This means:
        1. Optional blockinfo first
        2. Title is REQUIRED and must come before content (followed by optional titleabbrev)
        3. At least one content element (mediaobject, etc.)

        This method:
        - Removes invalid <label> elements (not allowed in figure)
        - Removes duplicate <title> elements, keeping only the first meaningful one
        - Ensures title comes before other content
        - Adds missing title if needed
        - Ensures figure has at least one valid content element
        """
        fixes = []

        # Valid figure content elements (after title)
        figure_content_tags = {
            'mediaobject', 'literallayout', 'programlisting', 'screen',
            'synopsis', 'graphic', 'informalequation', 'link', 'ulink',
            'indexterm', 'beginpage', 'blockquote', 'address'
        }

        for figure in list(root.iter('figure')):
            children = list(figure)
            if not children:
                continue

            # Step 1: Handle invalid <label> elements
            # <label> is NOT a valid child of <figure> in DocBook
            label_elems = [c for c in children if c.tag == 'label']
            label_text = None
            for label_elem in label_elems:
                label_text = "".join(label_elem.itertext()).strip()
                figure.remove(label_elem)
                fixes.append(f"Removed invalid <label> element from figure in {filename}")

            # Refresh children list after removing labels
            children = list(figure)

            # Step 2: Handle duplicate <title> elements
            title_elems = [c for c in children if c.tag == 'title']
            if len(title_elems) > 1:
                # Keep the title with the most meaningful content
                # Prefer non-auto-generated titles (not starting with "Figure page")
                best_title = None
                best_title_text = ""
                for title_elem in title_elems:
                    title_text = "".join(title_elem.itertext()).strip()
                    # Prefer titles that don't look auto-generated
                    is_auto = title_text.startswith("Figure page") or title_text.startswith("Figure Ch")
                    if best_title is None:
                        best_title = title_elem
                        best_title_text = title_text
                    elif is_auto and not best_title_text.startswith("Figure page"):
                        # Keep existing best_title (it's better)
                        pass
                    elif not is_auto and best_title_text.startswith("Figure page"):
                        # New title is better (not auto-generated)
                        best_title = title_elem
                        best_title_text = title_text
                    elif len(title_text) > len(best_title_text):
                        # Prefer longer titles (more descriptive)
                        best_title = title_elem
                        best_title_text = title_text

                # Remove all titles except the best one
                for title_elem in title_elems:
                    if title_elem is not best_title:
                        figure.remove(title_elem)
                        fixes.append(f"Removed duplicate <title> from figure in {filename}")

            # Refresh children list after handling titles
            children = list(figure)

            # Step 3: Find title position and check ordering
            title_elem = None
            title_index = -1
            blockinfo_elem = None

            for i, child in enumerate(children):
                if child.tag == 'title':
                    title_elem = child
                    title_index = i
                    break  # Found first (and now only) title
                elif child.tag == 'blockinfo':
                    blockinfo_elem = child

            # Check if there's content before title that needs to be moved
            needs_reorder = False
            if title_elem is not None and title_index > 0:
                # Check if there's non-blockinfo content before title
                for i in range(title_index):
                    if children[i].tag != 'blockinfo':
                        needs_reorder = True
                        break

            if needs_reorder:
                # Reorder: move content that's before title to after title
                content_before_title = []
                new_children = []

                for i, child in enumerate(children):
                    if child.tag == 'blockinfo':
                        new_children.append(child)
                    elif child.tag == 'title':
                        new_children.append(child)
                    elif i < title_index:
                        content_before_title.append(child)
                    else:
                        new_children.append(child)

                # Now add the moved content after title
                title_pos = next((i for i, c in enumerate(new_children) if c.tag == 'title'), -1)
                if title_pos >= 0:
                    for elem in reversed(content_before_title):
                        new_children.insert(title_pos + 1, elem)

                # Clear figure and re-add in correct order
                for child in children:
                    figure.remove(child)
                for child in new_children:
                    figure.append(child)

                fixes.append(f"Reordered figure content (moved content after title) in {filename}")

            # Ensure figure has at least one valid content element
            has_valid_content = False
            for child in figure:
                if child.tag in figure_content_tags or child.tag == 'title':
                    continue
                # Check for mediaobject descendants
                if figure.find('.//mediaobject') is not None:
                    has_valid_content = True
                    break
                if figure.find('.//imagedata') is not None:
                    has_valid_content = True
                    break

            # If no valid content, check if it should be removed or converted
            # This is already handled by _remove_misclassified_table_figures

        return fixes

    def _fix_unwrapped_imagedata(self, root: etree._Element, filename: str) -> List[str]:
        """
        Fix imagedata elements that are direct children of figure without proper wrapping.

        The DTD requires figure content to include mediaobject, which contains imageobject,
        which contains imagedata. So the structure should be:
        <figure>
            <title>...</title>
            <mediaobject>
                <imageobject>
                    <imagedata fileref="..."/>
                </imageobject>
            </mediaobject>
        </figure>

        This method finds imagedata elements that are direct children of figure and wraps
        them properly in mediaobject/imageobject.
        """
        fixes = []

        for figure in list(root.iter('figure')):
            # Find imagedata elements that are direct children of figure
            for imagedata in list(figure):
                if imagedata.tag == 'imagedata':
                    # Create proper wrapper structure
                    mediaobject = etree.Element('mediaobject')
                    imageobject = etree.Element('imageobject')

                    # Get position of imagedata
                    imagedata_index = list(figure).index(imagedata)

                    # Remove imagedata from figure and add to imageobject
                    figure.remove(imagedata)
                    imageobject.append(imagedata)
                    mediaobject.append(imageobject)
                    # Ensure width and scalefit attributes are set
                    imagedata.set('width', '100%')
                    imagedata.set('scalefit', '1')

                    # Insert mediaobject at the same position
                    figure.insert(imagedata_index, mediaobject)

                    fileref = imagedata.get('fileref', 'unknown')
                    fixes.append(f"Wrapped <imagedata fileref='{fileref}'> in <mediaobject><imageobject> in {filename}")

                    if VALIDATION_REPORT_AVAILABLE:
                        self.verification_items.append(VerificationItem(
                            xml_file=filename,
                            line_number=figure.sourceline if hasattr(figure, 'sourceline') else None,
                            fix_type="Figure Content Model Fix",
                            fix_description=f"Wrapped imagedata in mediaobject/imageobject structure",
                            verification_reason="DTD requires imagedata to be inside mediaobject/imageobject",
                            suggestion="Verify figure displays correctly"
                        ))

        return fixes

    def _fix_section_to_sect1(self, root: etree._Element, filename: str) -> List[str]:
        """
        Convert <section> elements to <sect1> (and nested sections appropriately).

        DocBook has two section models:
        1. Numbered sections: sect1, sect2, sect3, sect4, sect5 (hierarchical)
        2. Recursive sections: section (can nest indefinitely)

        The RittDoc DTD uses numbered sections. This method converts:
        - section directly in chapter -> sect1
        - section inside sect1 -> sect2
        - section inside sect2 -> sect3
        - etc.
        """
        fixes = []
        section_counter = {'sect1': 0, 'sect2': 0, 'sect3': 0, 'sect4': 0, 'sect5': 0}

        def ensure_section_id(section: etree._Element, section_type: str, parent_id: str = '') -> None:
            """Ensure section has an id attribute, generate one if missing.

            IDs are limited to 24 characters maximum.
            """
            if section.get('id') is None:
                section_counter[section_type] += 1
                # Use short section type prefix (s1, s2, etc. instead of sect1, sect2)
                short_type = section_type.replace('sect', 's')
                counter = section_counter[section_type]

                # Try to get title text for meaningful ID (but keep it short)
                title_elem = section.find('title')
                if title_elem is not None and title_elem.text:
                    # Limit title to 8 chars to leave room for parent_id and type
                    title_text = self._sanitize_id(title_elem.text[:8])
                    if parent_id:
                        new_id = f"{parent_id}{short_type}-{title_text}"
                    else:
                        new_id = f"{short_type}-{title_text}"
                else:
                    if parent_id:
                        new_id = f"{parent_id}{short_type}-{counter:02d}"
                    else:
                        new_id = f"{short_type}-{counter:02d}"

                # Truncate to 24 chars max
                if len(new_id) > 24:
                    new_id = new_id[:24].rstrip('-')
                section.set('id', new_id)

        # First pass: convert top-level sections (direct children of chapter) to sect1
        for chapter in root.iter('chapter'):
            chapter_id = chapter.get('id', '')
            for section in list(chapter):
                if section.tag == 'section':
                    section.tag = 'sect1'
                    ensure_section_id(section, 'sect1', chapter_id + '-' if chapter_id else '')
                    fixes.append(f"Converted <section> to <sect1> in chapter in {filename}")

        # Second pass: convert nested sections
        # Keep converting until no more sections remain
        max_iterations = 10  # Prevent infinite loops
        iteration = 0

        while iteration < max_iterations:
            converted_any = False
            iteration += 1

            # Convert sections inside sect1 to sect2
            for sect1 in list(root.iter('sect1')):
                parent_id = sect1.get('id', '') + '-' if sect1.get('id') else ''
                for section in list(sect1):
                    if section.tag == 'section':
                        section.tag = 'sect2'
                        ensure_section_id(section, 'sect2', parent_id)
                        converted_any = True
                        fixes.append(f"Converted <section> to <sect2> inside sect1 in {filename}")

            # Convert sections inside sect2 to sect3
            for sect2 in list(root.iter('sect2')):
                parent_id = sect2.get('id', '') + '-' if sect2.get('id') else ''
                for section in list(sect2):
                    if section.tag == 'section':
                        section.tag = 'sect3'
                        ensure_section_id(section, 'sect3', parent_id)
                        converted_any = True
                        fixes.append(f"Converted <section> to <sect3> inside sect2 in {filename}")

            # Convert sections inside sect3 to sect4
            for sect3 in list(root.iter('sect3')):
                parent_id = sect3.get('id', '') + '-' if sect3.get('id') else ''
                for section in list(sect3):
                    if section.tag == 'section':
                        section.tag = 'sect4'
                        ensure_section_id(section, 'sect4', parent_id)
                        converted_any = True
                        fixes.append(f"Converted <section> to <sect4> inside sect3 in {filename}")

            # Convert sections inside sect4 to sect5
            for sect4 in list(root.iter('sect4')):
                parent_id = sect4.get('id', '') + '-' if sect4.get('id') else ''
                for section in list(sect4):
                    if section.tag == 'section':
                        section.tag = 'sect5'
                        ensure_section_id(section, 'sect5', parent_id)
                        converted_any = True
                        fixes.append(f"Converted <section> to <sect5> inside sect4 in {filename}")

            # Sections inside sect5 stay as sect5 (deepest level)
            for sect5 in list(root.iter('sect5')):
                parent_id = sect5.get('id', '') + '-' if sect5.get('id') else ''
                for section in list(sect5):
                    if section.tag == 'section':
                        section.tag = 'sect5'
                        ensure_section_id(section, 'sect5', parent_id)
                        converted_any = True
                        fixes.append(f"Converted nested <section> to <sect5> in {filename}")

            if not converted_any:
                break

        return fixes

    def _fix_html_tables_to_cals(self, root: etree._Element, filename: str) -> List[str]:
        """
        Convert HTML-style tables to CALS table format.

        HTML-style:
        <table>
            <thead>
                <tr><th>Header</th></tr>
            </thead>
            <tbody>
                <tr><td>Data</td></tr>
            </tbody>
        </table>

        CALS format (required by DocBook):
        <table>
            <title>...</title>
            <tgroup cols="N">
                <thead>
                    <row><entry>Header</entry></row>
                </thead>
                <tbody>
                    <row><entry>Data</entry></row>
                </tbody>
            </tgroup>
        </table>
        """
        fixes = []

        for table in list(root.iter('table')):
            # Check if this is an HTML-style table (has tr, th, td elements)
            has_tr = table.find('.//tr') is not None
            has_tgroup = table.find('tgroup') is not None

            # Only convert if it has HTML elements and no tgroup
            if has_tr and not has_tgroup:
                # Collect existing elements
                title_elem = table.find('title')
                thead_elem = table.find('thead')
                tbody_elem = table.find('tbody')
                tfoot_elem = table.find('tfoot')

                # Count columns from first row
                num_cols = 0
                first_row = table.find('.//tr')
                if first_row is not None:
                    for cell in first_row:
                        if cell.tag in ('th', 'td'):
                            colspan = cell.get('colspan', '1')
                            try:
                                num_cols += int(colspan)
                            except ValueError:
                                num_cols += 1

                if num_cols == 0:
                    num_cols = 1

                # Create new tgroup structure
                tgroup = etree.Element('tgroup')
                tgroup.set('cols', str(num_cols))

                # Process thead
                if thead_elem is not None:
                    new_thead = etree.Element('thead')
                    for tr in thead_elem.findall('tr'):
                        row = self._convert_tr_to_row(tr)
                        new_thead.append(row)
                    if len(new_thead) > 0:
                        tgroup.append(new_thead)
                    table.remove(thead_elem)

                # Process tbody
                if tbody_elem is not None:
                    new_tbody = etree.Element('tbody')
                    for tr in tbody_elem.findall('tr'):
                        row = self._convert_tr_to_row(tr)
                        new_tbody.append(row)
                    if len(new_tbody) > 0:
                        tgroup.append(new_tbody)
                    table.remove(tbody_elem)
                else:
                    # Check for tr elements directly in table
                    new_tbody = etree.Element('tbody')
                    for tr in list(table.findall('tr')):
                        row = self._convert_tr_to_row(tr)
                        new_tbody.append(row)
                        table.remove(tr)
                    if len(new_tbody) > 0:
                        tgroup.append(new_tbody)

                # Process tfoot (convert to additional tbody rows or ignore)
                if tfoot_elem is not None:
                    # CALS supports tfoot but it's optional - add rows to tbody
                    if tgroup.find('tbody') is not None:
                        existing_tbody = tgroup.find('tbody')
                        for tr in tfoot_elem.findall('tr'):
                            row = self._convert_tr_to_row(tr)
                            existing_tbody.append(row)
                    table.remove(tfoot_elem)

                # Insert tgroup after title (or at beginning if no title)
                if title_elem is not None:
                    title_index = list(table).index(title_elem)
                    table.insert(title_index + 1, tgroup)
                else:
                    # Add a title element (required by DTD)
                    title = etree.Element('title')
                    table.insert(0, title)
                    table.insert(1, tgroup)
                    fixes.append(f"Added missing <title> to table in {filename}")

                fixes.append(f"Converted HTML-style table to CALS format in {filename}")

                if VALIDATION_REPORT_AVAILABLE:
                    self.verification_items.append(VerificationItem(
                        xml_file=filename,
                        line_number=table.sourceline if hasattr(table, 'sourceline') else None,
                        fix_type="Table Format Conversion",
                        fix_description=f"Converted HTML-style table (tr/th/td) to CALS format (row/entry)",
                        verification_reason="DocBook DTD requires CALS table format",
                        suggestion="Verify table structure and content are preserved"
                    ))

        return fixes

    def _convert_tr_to_row(self, tr: etree._Element) -> etree._Element:
        """
        Convert an HTML <tr> element to a CALS <row> element.

        - <th> and <td> become <entry>
        - colspan attribute is preserved (but converted to valid values)
        - rowspan becomes morerows attribute
        """
        row = etree.Element('row')

        for cell in tr:
            if cell.tag in ('th', 'td'):
                entry = etree.Element('entry')

                # Copy text and children
                entry.text = cell.text
                for child in cell:
                    entry.append(deepcopy(child))
                entry.tail = cell.tail

                # Handle colspan -> namest/nameend would require colspec
                # For simple conversion, we can use morecolsep but CALS doesn't have direct colspan
                # The proper way is to use namest and nameend with defined columns
                # For now, we'll drop colspan and note it
                colspan = cell.get('colspan')
                if colspan:
                    # Try to parse colspan
                    try:
                        if colspan.startswith('c') and colspan[1:].isdigit():
                            # Handle "c1" format - this indicates a column name, not span
                            # This is likely meant to span across that column
                            pass  # Drop invalid format
                        elif colspan.isdigit() and int(colspan) > 1:
                            # Valid colspan > 1 - would need colspec to properly handle
                            # Store as attribute for potential manual review
                            entry.set('_colspan', colspan)
                    except ValueError:
                        pass

                # Handle rowspan -> morerows
                rowspan = cell.get('rowspan')
                if rowspan:
                    try:
                        rowspan_val = int(rowspan)
                        if rowspan_val > 1:
                            # morerows is "number of additional rows" so subtract 1
                            entry.set('morerows', str(rowspan_val - 1))
                    except ValueError:
                        pass

                # Copy other attributes except HTML-specific ones
                for attr in cell.attrib:
                    if attr not in ('colspan', 'rowspan', 'scope', 'headers', 'abbr'):
                        entry.set(attr, cell.get(attr))

                row.append(entry)

        return row

    def _fix_nested_para_elements(self, root: etree._Element, filename: str) -> List[str]:
        """
        Fix nested para elements by intelligently unwrapping or flattening them.

        Strategy:
        1. If nested para contains ONLY inline elements (links, emphasis, etc.):
           -> Unwrap it: merge its content into the parent para
        2. If nested para contains block elements (lists, tables, etc.):
           -> Flatten it: create sibling para elements

        This preserves links and formatting while fixing DTD violations.
        """
        fixes = []
        # Find all para elements that contain other para elements
        for para in list(root.iter('para')):
            nested_paras = [child for child in para if isinstance(child.tag, str) and self._local_name(child) == "para"]

            if not nested_paras:
                continue

            parent = para.getparent()
            if parent is None:
                continue

            # Check if we can unwrap (inline-only) or need to flatten (has block content)
            for nested_para in nested_paras:
                if self._is_inline_only(nested_para):
                    # UNWRAP: Nested para has only inline content (links, emphasis, etc.)
                    # Move its content directly into parent para, preserving all inline elements

                    # Get position of nested para within parent para
                    nested_index = list(para).index(nested_para)

                    # Insert nested para's children before it
                    for i, child in enumerate(list(nested_para)):
                        para.insert(nested_index + i, child)

                    # Handle text content
                    if nested_para.text:
                        if nested_index > 0:
                            # Add to previous sibling's tail
                            prev = para[nested_index - 1]
                            prev.tail = (prev.tail or '') + nested_para.text
                        else:
                            # Add to parent para's text
                            para.text = (para.text or '') + nested_para.text

                    # Handle tail text (text after nested para)
                    if nested_para.tail:
                        # Find last inserted child and append to its tail
                        if len(nested_para) > 0:
                            last_child = para[nested_index + len(nested_para) - 1]
                            last_child.tail = (last_child.tail or '') + nested_para.tail
                        elif nested_index > 0:
                            prev = para[nested_index - 1]
                            prev.tail = (prev.tail or '') + nested_para.tail
                        else:
                            para.text = (para.text or '') + nested_para.tail

                    # Remove the nested para element itself
                    para.remove(nested_para)
                    fixes.append(f"Unwrapped inline-only nested para in {filename}")

                else:
                    # FLATTEN: Nested para has block content - create sibling paras
                    para_index = list(parent).index(para)

                    # Create new para element at parent level
                    new_para = etree.Element("para")
                    # Copy all attributes from nested para (id, role, etc.)
                    for attr, value in nested_para.attrib.items():
                        new_para.set(attr, value)

                    # Copy text and children
                    new_para.text = nested_para.text
                    for child in nested_para:
                        new_para.append(deepcopy(child))

                    # Insert after current para
                    para_index += 1
                    parent.insert(para_index, new_para)

                    # Handle tail text: if there's text after the nested para,
                    # wrap it in a new para element
                    if nested_para.tail and nested_para.tail.strip():
                        tail_para = etree.Element("para")
                        tail_para.text = nested_para.tail
                        para_index += 1
                        parent.insert(para_index, tail_para)

                    # Remove from original para
                    para.remove(nested_para)
                    fixes.append(f"Flattened block-content nested para in {filename}")

                    if VALIDATION_REPORT_AVAILABLE:
                        self.verification_items.append(VerificationItem(
                            xml_file=filename,
                            line_number=para.sourceline if hasattr(para, 'sourceline') else None,
                            fix_type="Nested Para Fix",
                            fix_description="Fixed nested para element",
                            verification_reason="Nested para elements are not allowed per DTD.",
                            suggestion="Verify that content and links are preserved correctly."
                        ))

        return fixes

    def _fix_bridgehead_to_sections(self, root: etree._Element, filename: str) -> List[str]:
        """
        Convert bridgehead elements to proper section structure.

        Bridgehead elements are used as visual section headers but don't create
        proper document structure. The RittDoc DTD requires proper sect1/sect2
        hierarchy. This method:

        1. For chapters (like Index): converts bridgehead to sect1 and groups
           following content into that section
        2. For sect1: converts bridgehead renderas="sect2" to actual sect2
        3. Handles nested cases appropriately

        This is especially important for Index chapters which have:
        - bridgehead for each letter (A, B, C...)
        - para elements for index entries
        """
        fixes = []

        # Process chapters with direct bridgehead children
        for chapter in root.iter('chapter'):
            fixes.extend(self._convert_bridgeheads_in_container(
                chapter, 'sect1', filename, 'chapter'
            ))

        # Process sect1 elements with bridgehead children
        for sect1 in root.iter('sect1'):
            fixes.extend(self._convert_bridgeheads_in_container(
                sect1, 'sect2', filename, 'sect1'
            ))

        # Process sect2 elements with bridgehead children
        for sect2 in root.iter('sect2'):
            fixes.extend(self._convert_bridgeheads_in_container(
                sect2, 'sect3', filename, 'sect2'
            ))

        # Process sect3 elements with bridgehead children
        for sect3 in root.iter('sect3'):
            fixes.extend(self._convert_bridgeheads_in_container(
                sect3, 'sect4', filename, 'sect3'
            ))

        return fixes

    def _convert_bridgeheads_in_container(
        self,
        container: etree._Element,
        target_section_type: str,
        filename: str,
        container_type: str
    ) -> List[str]:
        """
        Convert bridgehead elements within a container to proper sections.

        Args:
            container: The parent element (chapter, sect1, sect2, etc.)
            target_section_type: What to convert bridgeheads to (sect1, sect2, etc.)
            filename: For logging
            container_type: For logging (chapter, sect1, etc.)

        Returns:
            List of fix descriptions
        """
        fixes = []

        # Find all bridgehead children (direct children only)
        bridgeheads = [child for child in container if child.tag == 'bridgehead']

        if not bridgeheads:
            return fixes

        # Get container ID for generating section IDs
        container_id = container.get('id', container_type)

        # We need to restructure: group content between bridgeheads into sections
        # Strategy:
        # 1. Find all children of the container
        # 2. When we hit a bridgehead, start a new section
        # 3. Add subsequent content to that section until next bridgehead

        children = list(container)

        # Find the title element (should stay at container level)
        title_elem = None
        title_index = -1
        for i, child in enumerate(children):
            if child.tag == 'title':
                title_elem = child
                title_index = i
                break

        # Also preserve chapterinfo, sect1info, etc. at container level
        info_tag = f"{container_type}info" if container_type != 'chapter' else 'chapterinfo'
        info_elem = None
        for child in children:
            if child.tag == info_tag:
                info_elem = child
                break

        # Collect elements to process (everything after title/info)
        start_index = 0
        for i, child in enumerate(children):
            if child.tag in ['title', 'subtitle', 'titleabbrev', info_tag]:
                start_index = i + 1
            else:
                break

        elements_to_process = children[start_index:]

        if not elements_to_process:
            return fixes

        # Check if we have any bridgeheads to process
        has_bridgeheads = any(elem.tag == 'bridgehead' for elem in elements_to_process)
        if not has_bridgeheads:
            return fixes

        # Remove all elements we're going to restructure
        for elem in elements_to_process:
            container.remove(elem)

        # Now rebuild the structure
        current_section = None
        section_counter = 0
        pre_bridgehead_content = []  # Content before first bridgehead

        for elem in elements_to_process:
            if elem.tag == 'bridgehead':
                # Start a new section
                section_counter += 1

                # Get bridgehead text for section title
                bridgehead_text = ''.join(elem.itertext()).strip()
                if not bridgehead_text:
                    bridgehead_text = f"Section {section_counter}"

                # Create the section element
                current_section = etree.Element(target_section_type)

                # Generate ID from bridgehead text (sanitize for XML ID, max 24 chars)
                sanitized_text = self._sanitize_id(bridgehead_text[:10])
                section_id = f"{container_id}-{sanitized_text}"
                if len(section_id) > 24:
                    section_id = section_id[:24].rstrip('-')
                current_section.set('id', section_id)

                # Create title from bridgehead content
                title = etree.Element('title')
                # Copy all content from bridgehead (including child elements)
                title.text = elem.text
                for child in elem:
                    title.append(deepcopy(child))
                current_section.append(title)

                # Add the section to the container
                container.append(current_section)

                fixes.append(
                    f"Converted <bridgehead> '{bridgehead_text[:30]}' to <{target_section_type}> in {filename}"
                )

                if VALIDATION_REPORT_AVAILABLE:
                    self.verification_items.append(VerificationItem(
                        xml_file=filename,
                        line_number=elem.sourceline if hasattr(elem, 'sourceline') else None,
                        fix_type="Bridgehead Conversion",
                        fix_description=f"Converted bridgehead to {target_section_type}: '{bridgehead_text[:50]}'",
                        verification_reason="Bridgehead elements don't create proper document structure required by DTD.",
                        suggestion="Verify section hierarchy is correct."
                    ))
            else:
                # Add content to current section, or collect pre-bridgehead content
                if current_section is not None:
                    current_section.append(elem)
                else:
                    pre_bridgehead_content.append(elem)

        # Handle pre-bridgehead content: wrap in an introductory section
        if pre_bridgehead_content:
            # Create an intro section for content before first bridgehead
            intro_section = etree.Element(target_section_type)
            intro_id = f"{container_id}-intro"
            if len(intro_id) > 24:
                intro_id = intro_id[:24].rstrip('-')
            intro_section.set('id', intro_id)

            intro_title = etree.Element('title')
            # Leave empty - DTD supports empty <title/> elements
            intro_section.append(intro_title)

            for elem in pre_bridgehead_content:
                intro_section.append(elem)

            # Insert at the beginning (after title/info elements)
            insert_pos = start_index
            container.insert(insert_pos, intro_section)

            fixes.append(
                f"Wrapped {len(pre_bridgehead_content)} pre-bridgehead elements in <{target_section_type}> in {filename}"
            )

        return fixes

    @staticmethod
    def _sanitize_id(text: str, max_length: int = 15) -> str:
        """
        Sanitize text to be used as an XML ID.

        - Lowercase
        - Replace spaces and special chars with hyphens
        - Remove consecutive hyphens
        - Limit length to max_length (default 15 to help keep total IDs under 24 chars)
        """
        import re
        # Lowercase and replace non-alphanumeric with hyphen
        sanitized = re.sub(r'[^a-zA-Z0-9]+', '-', text.lower())
        # Remove leading/trailing hyphens
        sanitized = sanitized.strip('-')
        # Collapse multiple hyphens
        sanitized = re.sub(r'-+', '-', sanitized)
        # Limit length
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length].rstrip('-')
        # Ensure it doesn't start with a number
        if sanitized and sanitized[0].isdigit():
            sanitized = 'sec-' + sanitized
        return sanitized or 'section'

    def _remove_empty_mediaobjects(self, root: etree._Element, filename: str) -> List[str]:
        """Remove empty/placeholder mediaobjects from anywhere in the document"""
        fixes = []

        # Find all mediaobjects
        for mediaobj in list(root.iter('mediaobject')):
            # Check if this is an empty/placeholder mediaobject
            is_placeholder = False

            # Check for "Image not available" or similar placeholder text
            for textobj in mediaobj.iter('textobject'):
                for phrase in textobj.iter('phrase'):
                    if phrase.text and ('not available' in phrase.text.lower() or
                                        'no image' in phrase.text.lower() or
                                        phrase.text.strip() in ['', 'N/A', 'n/a']):
                        is_placeholder = True
                        break

            # Also check if mediaobject has no real content (no imagedata/videodata/audiodata)
            has_real_media = (mediaobj.find('.//imagedata') is not None or
                            mediaobj.find('.//videodata') is not None or
                            mediaobj.find('.//audiodata') is not None)

            if is_placeholder or not has_real_media:
                parent = mediaobj.getparent()
                if parent is not None:
                    # Check context before removing
                    parent_tag = parent.tag

                    # Remove the empty mediaobject
                    parent.remove(mediaobj)
                    fixes.append(f"Removed empty/placeholder mediaobject from <{parent_tag}> in {filename}")

                    # If parent is now a figure with no content (no mediaobject/graphic),
                    # we'll handle that in another fix
                    if VALIDATION_REPORT_AVAILABLE:
                        self.verification_items.append(VerificationItem(
                            xml_file=filename,
                            line_number=parent.sourceline if hasattr(parent, 'sourceline') else None,
                            fix_type="Empty Mediaobject Removal",
                            fix_description=f"Removed placeholder mediaobject with no real media content",
                            verification_reason="Mediaobject contained only placeholder text or no media.",
                            suggestion="Verify that removing this mediaobject didn't affect document structure."
                        ))

        return fixes

    def _remove_misclassified_table_figures(self, root: etree._Element, filename: str) -> List[str]:
        """Convert or remove invalid figure elements"""
        fixes = []

        # Process all figures
        for figure in list(root.iter('figure')):
            title_elem = figure.find('title')

            # Check if figure has real media content
            has_real_image = (figure.find('.//imagedata') is not None or
                             figure.find('.//videodata') is not None or
                             figure.find('.//audiodata') is not None)

            # Check for placeholder text
            has_placeholder = False
            for phrase in figure.iter('phrase'):
                if phrase.text and 'not available' in phrase.text.lower():
                    has_placeholder = True
                    break

            # Skip figures that have real media content
            if has_real_image and not has_placeholder:
                continue

            # Get title text (if any)
            title_text = ''
            if title_elem is not None:
                title_text = ''.join(title_elem.itertext()).strip()

            # Case 1: Figure with no title or empty/meaningless title - REMOVE completely
            if not title_text or title_text.lower() in ['untitled', 'no title', 'n/a']:
                parent = figure.getparent()
                if parent is not None:
                    parent.remove(figure)
                    fixes.append(f"Removed empty figure with no meaningful title in {filename}")

                    if VALIDATION_REPORT_AVAILABLE:
                        self.verification_items.append(VerificationItem(
                            xml_file=filename,
                            line_number=figure.sourceline if hasattr(figure, 'sourceline') else None,
                            fix_type="Empty Figure Removal",
                            fix_description=f"Removed figure with no media and no meaningful title",
                            verification_reason="Figure had no real media content and title was empty or 'Untitled'.",
                            suggestion="Verify this empty figure was not needed."
                        ))

            # Case 2: Figure with "table" in title - CONVERT to para
            elif 'table' in title_text.lower():
                parent = figure.getparent()
                if parent is not None:
                    # Get figure's position in parent
                    fig_index = list(parent).index(figure)

                    # Create a new para element with the figure's title content
                    para = etree.Element('para')

                    # Copy all attributes from figure (like id)
                    for attr, value in figure.attrib.items():
                        para.set(attr, value)

                    # Copy all content from title into para
                    # Preserve all nested elements (emphasis, phrase, ulink, etc.)
                    if title_elem.text:
                        para.text = title_elem.text
                    for child in title_elem:
                        para.append(child)

                    # Insert para at figure's position
                    parent.insert(fig_index, para)

                    # Remove the figure
                    parent.remove(figure)

                    fixes.append(f"Converted misclassified figure (table label) to para in {filename}")

                    if VALIDATION_REPORT_AVAILABLE:
                        self.verification_items.append(VerificationItem(
                            xml_file=filename,
                            line_number=figure.sourceline if hasattr(figure, 'sourceline') else None,
                            fix_type="Misclassified Figure Conversion",
                            fix_description=f"Converted figure with 'table' in title to para: '{title_text[:60]}'",
                            verification_reason="Figure had 'table' in title but no real image content.",
                            suggestion="Verify the table caption is preserved correctly."
                        ))

            # Case 3: Figure with other title but no media - REMOVE (these are likely errors)
            else:
                parent = figure.getparent()
                if parent is not None:
                    parent.remove(figure)
                    fixes.append(f"Removed empty figure '{title_text[:40]}' with no media in {filename}")

                    if VALIDATION_REPORT_AVAILABLE:
                        self.verification_items.append(VerificationItem(
                            xml_file=filename,
                            line_number=figure.sourceline if hasattr(figure, 'sourceline') else None,
                            fix_type="Empty Figure Removal",
                            fix_description=f"Removed figure with no media: '{title_text[:60]}'",
                            verification_reason="Figure had no real media content.",
                            suggestion="Verify this figure was not needed or check if media is missing."
                        ))

        return fixes

    def _fix_invalid_content_models(self, root: etree._Element, filename: str) -> List[str]:
        """Fix elements with invalid content model (wrong child sequence)"""
        fixes = []

        # Elements that are NOT allowed as direct children of chapter
        # (they must be inside sect1)
        chapter_violating_tags = [
            'para', 'figure', 'table', 'orderedlist', 'itemizedlist',
            'variablelist', 'example', 'equation', 'informalfigure',
            'informaltable', 'bridgehead', 'blockquote', 'note',
            'warning', 'caution', 'important', 'tip', 'sidebar',
            'mediaobject', 'programlisting', 'screen', 'literallayout'
        ]

        # Fix chapters with para/figure/table as direct children (need sect1 wrapper)
        for chapter in root.iter('chapter'):
            violating_elements = []
            has_sections = False

            # Check all direct children after title
            past_title = False
            for child in chapter:
                if child.tag == 'title':
                    past_title = True
                    continue

                if not past_title:
                    continue

                # Check for section elements
                if child.tag in ['sect1', 'section', 'toc', 'lot', 'index', 'glossary', 'bibliography']:
                    has_sections = True

                # Check for violating elements
                if child.tag in chapter_violating_tags:
                    violating_elements.append(child)

            # If we have violating elements, wrap them in sect1
            if violating_elements:
                chapter_id = chapter.get('id', 'chapter')

                # Create wrapper sect1
                sect1 = etree.Element('sect1')
                sect1_id = f"{chapter_id}-intro"
                if len(sect1_id) > 24:
                    sect1_id = sect1_id[:24].rstrip('-')
                sect1.set('id', sect1_id)

                # Add empty title - DTD supports empty <title/> elements
                title = etree.Element('title')
                sect1.append(title)

                # Move violating elements into sect1
                for elem in violating_elements:
                    chapter.remove(elem)
                    sect1.append(elem)

                # Insert sect1 after title (and any meta elements)
                insert_index = 0
                for i, child in enumerate(chapter):
                    if child.tag in ['title', 'chapterinfo']:
                        insert_index = i + 1

                chapter.insert(insert_index, sect1)

                fixes.append(f"Wrapped {len(violating_elements)} violating elements in <sect1> in chapter {chapter_id}")

                if VALIDATION_REPORT_AVAILABLE:
                    self.verification_items.append(VerificationItem(
                        xml_file=filename,
                        line_number=chapter.sourceline if hasattr(chapter, 'sourceline') else None,
                        fix_type="Content Model Fix",
                        fix_description=f"Wrapped {len(violating_elements)} elements in <sect1 id=\"{chapter_id}-intro\">",
                        verification_reason="Section with empty title was auto-created for DTD compliance.",
                        suggestion="Consider adding a descriptive section title if appropriate."
                    ))

        # Fix sect1/sect2/sect3/sect4 content ordering
        # DTD requires: divcomponent.mix content BEFORE nested sections
        fixes.extend(self._fix_section_content_ordering(root, filename))

        # Fix lists that need listitem children
        for list_elem in root.iter('orderedlist', 'itemizedlist'):
            # Check if list has any non-listitem children
            for child in list_elem:
                if child.tag not in ['listitem', 'title']:
                    # Wrap in listitem
                    listitem = etree.Element('listitem')
                    list_elem.remove(child)
                    listitem.append(child)
                    list_elem.append(listitem)
                    fixes.append(f"Wrapped <{child.tag}> in <listitem> in {filename}")

        return fixes

    def _fix_section_content_ordering(self, root: etree._Element, filename: str) -> List[str]:
        """
        Fix content ordering within sect1/sect2/sect3/sect4 elements.

        The DTD requires that divcomponent.mix content (para, figure, table, etc.)
        must come BEFORE any nested sections (sect2 inside sect1, etc.).

        This method reorders content to comply with the DTD.
        """
        fixes = []

        # Define section hierarchy
        section_hierarchy = [
            ('sect1', 'sect2'),
            ('sect2', 'sect3'),
            ('sect3', 'sect4'),
            ('sect4', 'sect5'),
        ]

        # Content elements that must come before nested sections
        divcomponent_tags = {
            'para', 'figure', 'table', 'orderedlist', 'itemizedlist',
            'variablelist', 'example', 'equation', 'informalfigure',
            'informaltable', 'blockquote', 'note', 'warning', 'caution',
            'important', 'tip', 'sidebar', 'mediaobject', 'programlisting',
            'screen', 'literallayout', 'simpara', 'formalpara'
        }

        # Elements that should stay at the beginning
        header_tags = {'title', 'subtitle', 'titleabbrev', 'sect1info',
                       'sect2info', 'sect3info', 'sect4info', 'sect5info',
                       'sectioninfo'}

        for parent_tag, child_section_tag in section_hierarchy:
            for section in list(root.iter(parent_tag)):
                children = list(section)

                if not children:
                    continue

                # Separate children into categories
                header_elements = []
                content_elements = []
                nested_sections = []
                trailing_content = []  # Content that appears after nested sections

                seen_nested_section = False

                for child in children:
                    tag = child.tag
                    if tag in header_tags:
                        header_elements.append(child)
                    elif tag == child_section_tag or tag == 'simplesect':
                        nested_sections.append(child)
                        seen_nested_section = True
                    elif tag in divcomponent_tags:
                        if seen_nested_section:
                            # This content is AFTER a nested section - needs to be moved
                            trailing_content.append(child)
                        else:
                            content_elements.append(child)
                    else:
                        # Other elements (nav, etc.)
                        if seen_nested_section:
                            trailing_content.append(child)
                        else:
                            content_elements.append(child)

                # If there's trailing content after nested sections, we need to fix it
                if trailing_content and nested_sections:
                    # Remove all children and rebuild in correct order
                    for child in children:
                        section.remove(child)

                    # Add back in correct order:
                    # 1. Header elements (title, info)
                    for elem in header_elements:
                        section.append(elem)

                    # 2. Content elements (para, figure, etc.) - BEFORE nested sections
                    for elem in content_elements:
                        section.append(elem)

                    # 3. Trailing content moved to be with other content
                    for elem in trailing_content:
                        section.append(elem)

                    # 4. Nested sections - AFTER all content
                    for elem in nested_sections:
                        section.append(elem)

                    fixes.append(
                        f"Reordered {len(trailing_content)} element(s) before nested sections in <{parent_tag}> in {filename}"
                    )

                    if VALIDATION_REPORT_AVAILABLE:
                        self.verification_items.append(VerificationItem(
                            xml_file=filename,
                            line_number=section.sourceline if hasattr(section, 'sourceline') else None,
                            fix_type="Content Ordering Fix",
                            fix_description=f"Moved {len(trailing_content)} elements before {child_section_tag} sections",
                            verification_reason="DTD requires divcomponent content before nested sections.",
                            suggestion="Verify content order is logically correct."
                        ))

        return fixes

    def _fix_empty_elements(self, root: etree._Element, filename: str) -> List[str]:
        """Fix elements that shouldn't be empty"""
        fixes = []

        # Elements that shouldn't be empty
        non_empty = {
            'title': 'Untitled',
            'para': 'Content not available',
            'entry': ' ',  # Table cells can have single space
            'term': 'Term',
        }

        for elem_name, default_text in non_empty.items():
            for elem in root.iter(elem_name):
                # Check if element is truly empty (no text and no children)
                if elem.text is None and len(elem) == 0:
                    elem.text = default_text
                    fixes.append(f"Added default text to empty <{elem_name}> in {filename}")
                # Check if element only has whitespace
                elif elem.text and elem.text.strip() == '' and len(elem) == 0:
                    elem.text = default_text
                    fixes.append(f"Replaced whitespace-only content in <{elem_name}> in {filename}")

        # Fix empty sections (sect1, sect2, sect3, sect4)
        # DTD requires sections to have at least one divcomponent.mix element after title
        fixes.extend(self._fix_empty_sections(root, filename))

        return fixes

    def _fix_empty_sections(self, root: etree._Element, filename: str) -> List[str]:
        """
        Fix sect1/sect2/sect3/sect4 elements that have only a title and no content.

        The DTD requires sections to have at least one of:
        - divcomponent.mix content (para, figure, table, etc.)
        - nested sections (sect2 in sect1, etc.)
        - simplesect elements

        This method REMOVES empty sections rather than adding placeholder content,
        as empty sections serve no purpose and clutter the document.
        """
        fixes = []

        # Content elements that satisfy the DTD requirement
        content_tags = {
            'para', 'figure', 'table', 'orderedlist', 'itemizedlist',
            'variablelist', 'example', 'equation', 'informalfigure',
            'informaltable', 'blockquote', 'note', 'warning', 'caution',
            'important', 'tip', 'sidebar', 'mediaobject', 'programlisting',
            'screen', 'literallayout', 'simpara', 'formalpara',
            'sect2', 'sect3', 'sect4', 'sect5', 'simplesect'
        }

        # Check each section type (process in reverse order: sect4 -> sect1)
        # This ensures nested empty sections are removed before parent sections
        section_types = ['sect4', 'sect3', 'sect2', 'sect1']

        for section_type in section_types:
            for section in list(root.iter(section_type)):
                # Check if section has any valid content
                has_content = False

                for child in section:
                    if child.tag in content_tags:
                        has_content = True
                        break

                if not has_content:
                    # Section has no valid content - REMOVE it
                    parent = section.getparent()
                    if parent is not None:
                        section_id = section.get('id', section_type)
                        section_title = ''
                        title_elem = section.find('title')
                        if title_elem is not None:
                            section_title = ''.join(title_elem.itertext()).strip()

                        parent.remove(section)

                        fixes.append(f"Removed empty <{section_type} id='{section_id}'> '{section_title[:30]}' in {filename}")

                        if VALIDATION_REPORT_AVAILABLE:
                            self.verification_items.append(VerificationItem(
                                xml_file=filename,
                                line_number=section.sourceline if hasattr(section, 'sourceline') else None,
                                fix_type="Empty Section Removal",
                                fix_description=f"Removed empty {section_type}: '{section_title[:50]}'",
                                verification_reason="Section had only a title with no content - serves no purpose.",
                                suggestion="Verify this section was intentionally empty in the source."
                            ))

        return fixes

    def _remove_empty_rows(self, root: etree._Element, filename: str) -> List[str]:
        """Remove empty row elements from tables"""
        fixes = []

        # Find all row elements
        for row in list(root.iter('row')):
            # Check if row has no entry children (completely empty)
            entries = list(row.iter('entry'))
            if len(entries) == 0:
                parent = row.getparent()
                if parent is not None:
                    parent.remove(row)
                    fixes.append(f"Removed empty <row/> element in {filename}")

                    if VALIDATION_REPORT_AVAILABLE:
                        self.verification_items.append(VerificationItem(
                            xml_file=filename,
                            line_number=row.sourceline if hasattr(row, 'sourceline') else None,
                            fix_type="Empty Row Removal",
                            fix_description="Removed row element with no entry children",
                            verification_reason="Row elements must contain at least one entry element per DTD.",
                            suggestion="Check source EPUB for empty table rows."
                        ))

        return fixes

    def _fix_missing_required_attributes(self, root: etree._Element, filename: str) -> List[str]:
        """Fix elements missing required attributes"""
        fixes = []

        # Table tgroup requires cols attribute
        for tgroup in root.iter('tgroup'):
            if 'cols' not in tgroup.attrib:
                # Count actual columns from first row
                cols = 0
                for tbody in tgroup.iter('tbody'):
                    for row in tbody.iter('row'):
                        cols = len(list(row.iter('entry')))
                        break
                    break

                if cols == 0:
                    # Try thead
                    for thead in tgroup.iter('thead'):
                        for row in thead.iter('row'):
                            cols = len(list(row.iter('entry')))
                            break
                        break

                if cols == 0:
                    cols = 1  # Default

                tgroup.set('cols', str(cols))
                fixes.append(f"Added cols=\"{cols}\" to <tgroup> in {filename}")

        # Imagedata should have fileref
        for imagedata in root.iter('imagedata'):
            if 'fileref' not in imagedata.attrib:
                # Try to find nearby file reference or use placeholder
                imagedata.set('fileref', 'image-placeholder.png')
                imagedata.set('width', '100%')
                imagedata.set('scalefit', '1')
                fixes.append(f"Added placeholder fileref to <imagedata> in {filename}")

        return fixes

    def _fix_chapter_missing_ids(self, root: etree._Element, filename: str) -> List[str]:
        """Add ID to chapter element if missing. Uses filename (e.g., ch0001.xml) to derive ID."""
        fixes = []

        # Check if root is a chapter element
        local_name = root.tag.split('}')[-1] if '}' in root.tag else root.tag
        if local_name == 'chapter' and root.get('id') is None:
            # Derive ID from filename (e.g., ch0001.xml -> ch0001)
            chapter_id = filename.replace('.xml', '') if filename.endswith('.xml') else filename
            # Sanitize to ensure it's a valid XML ID
            chapter_id = self._sanitize_id(chapter_id)
            root.set('id', chapter_id)
            fixes.append(f"Added id '{chapter_id}' to <chapter> in {filename}")

        return fixes

    def _fix_sect_missing_ids(self, root: etree._Element, filename: str) -> List[str]:
        """Add IDs to sect1-5 elements that are missing them."""
        fixes = []
        section_counter = {'sect1': 0, 'sect2': 0, 'sect3': 0, 'sect4': 0, 'sect5': 0}

        for section_type in ['sect1', 'sect2', 'sect3', 'sect4', 'sect5']:
            for section in root.iter(section_type):
                if section.get('id') is None:
                    section_counter[section_type] += 1
                    # Try to get parent ID for context
                    parent = section.getparent()
                    parent_id = ''
                    if parent is not None:
                        parent_id = parent.get('id', '')
                        if parent_id:
                            parent_id = parent_id + '-'

                    # Try to get title text for meaningful ID (max 24 chars total)
                    title_elem = section.find('title')
                    short_type = section_type.replace('sect', 's')
                    if title_elem is not None and title_elem.text:
                        title_text = self._sanitize_id(title_elem.text[:8])
                        new_id = f"{parent_id}{short_type}-{title_text}"
                    else:
                        new_id = f"{parent_id}{short_type}-{section_counter[section_type]:02d}"
                    # Truncate to 24 chars max
                    if len(new_id) > 24:
                        new_id = new_id[:24].rstrip('-')
                    section.set('id', new_id)

                    fixes.append(f"Added id to <{section_type}> in {filename}")

        return fixes

    def _fix_invalid_elements(self, root: etree._Element, filename: str) -> List[str]:
        """Remove or convert invalid/undeclared elements"""
        fixes = []

        # Elements excluded by RittDoc DTD (from rittexclusions.mod)
        # These need to be removed or converted to valid elements

        # Elements to remove completely (unwrap, keeping content)
        # These are structural/semantic elements that don't have a direct equivalent
        elements_to_unwrap = {
            # HTML elements
            'html', 'body', 'div', 'span', 'br', 'hr', 'style', 'script',
            # DocBook elements excluded by RittDoc
            'accel', 'action', 'application', 'arg', 'classname',
            'code', 'command', 'computeroutput', 'constant', 'envar',
            'errorcode', 'errorname', 'errortext', 'errortype',
            'exceptionname', 'function', 'guibutton', 'guiicon',
            'guilabel', 'guimenu', 'guimenuitem', 'guisubmenu',
            'hardware', 'interface', 'interfacename', 'keycap',
            'keycode', 'keycombo', 'keysym', 'markup', 'methodname',
            'mousebutton', 'option', 'optional', 'parameter',
            'prompt', 'property', 'replaceable', 'returnvalue',
            'shortcut', 'structfield', 'structname', 'symbol',
            'systemitem', 'token', 'userinput', 'varname',
            # Container elements to unwrap
            'callout', 'menuchoice', 'ooclass', 'ooexception', 'oointerface',
            # PDF-to-XML conversion elements not in RittDoc DTD
            'texts', 'text',
        }

        # Elements to convert to <emphasis> with role attribute
        # b -> <emphasis role="bold">, i -> <emphasis role="italic">
        elements_to_emphasis = {
            'b': 'bold',
            'i': 'italic',
        }

        # Elements to convert to <para>
        elements_to_para = {
            'p', 'simpara', 'remark',
        }

        # Elements to convert to <phrase> (inline content)
        elements_to_phrase = {
            'citebiblioid', 'citerefentry', 'lineannotation',
            'medialabel', 'modifier', 'nonterminal', 'sgmltag',
        }

        # Elements to remove entirely (including content) - rarely used technical elements
        elements_to_remove_entirely = {
            'areaspec', 'area', 'areaset', 'co', 'coref',
            'constraintdef', 'constraint', 'graphicco', 'imageobjectco',
            'mediaobjectco', 'programlistingco', 'screenco',
            'svg', 'modespec', 'sbr',
        }

        # Complex structures to simplify
        # These have specific handling

        # Step 1: Remove elements entirely (including content)
        for elem_name in elements_to_remove_entirely:
            for elem in list(root.iter(elem_name)):
                parent = elem.getparent()
                if parent is not None:
                    parent.remove(elem)
                    fixes.append(f"Removed excluded element <{elem_name}> entirely in {filename}")

        # Step 2: Unwrap elements (keep content, remove wrapper)
        for elem_name in elements_to_unwrap:
            for elem in list(root.iter(elem_name)):
                parent = elem.getparent()
                if parent is None:
                    continue

                index = list(parent).index(elem)

                # Handle text content
                if elem.text:
                    if index > 0:
                        prev = parent[index - 1]
                        prev.tail = (prev.tail or '') + elem.text
                    else:
                        parent.text = (parent.text or '') + elem.text

                # Move children up to parent
                children = list(elem)
                for i, child in enumerate(children):
                    elem.remove(child)
                    parent.insert(index + i, child)

                # Handle tail text
                if elem.tail:
                    if children:
                        last_child = parent[index + len(children) - 1]
                        last_child.tail = (last_child.tail or '') + elem.tail
                    elif index > 0:
                        prev = parent[index - 1]
                        prev.tail = (prev.tail or '') + elem.tail
                    else:
                        parent.text = (parent.text or '') + elem.tail

                parent.remove(elem)
                fixes.append(f"Unwrapped excluded element <{elem_name}> in {filename}")

        # Step 3: Convert elements to <para>
        for elem_name in elements_to_para:
            for elem in list(root.iter(elem_name)):
                elem.tag = 'para'
                fixes.append(f"Converted <{elem_name}> to <para> in {filename}")

        # Step 4: Convert elements to <phrase>
        for elem_name in elements_to_phrase:
            for elem in list(root.iter(elem_name)):
                elem.tag = 'phrase'
                fixes.append(f"Converted <{elem_name}> to <phrase> in {filename}")

        # Step 4b: Convert <b> and <i> to <emphasis role="bold/italic">
        for elem_name, role in elements_to_emphasis.items():
            for elem in list(root.iter(elem_name)):
                elem.tag = 'emphasis'
                elem.set('role', role)
                fixes.append(f"Converted <{elem_name}> to <emphasis role=\"{role}\"> in {filename}")

        # Step 4c: Remove invalid attributes from elements
        # These attributes are from PDF-to-XML conversion and not in the DTD
        pdf_conversion_attrs = ['font', 'size', 'color', 'reading_order', 'reading_block',
                                'col_id', 'top', 'left', 'width', 'height', 'bold', 'italic',
                                'src', 'type', 'page', 'stream_index', 'bbox']
        # HTML-specific attributes not valid in DocBook
        html_only_attrs = ['loading', 'decoding', 'fetchpriority', 'srcset', 'sizes']
        invalid_attrs_for_elements = {
            'phrase': pdf_conversion_attrs,
            'subscript': pdf_conversion_attrs,
            'superscript': pdf_conversion_attrs,
            'para': ['reading_order', 'reading_block', 'col_id'],
            'mediaobject': ['reading_order', 'reading_block', 'top', 'left', 'width', 'height', 'src', 'type', 'page'],
            'imagedata': html_only_attrs,
            'emphasis': ['bold', 'italic'],  # bold/italic should be role attribute only
        }
        for elem_name, invalid_attrs in invalid_attrs_for_elements.items():
            for elem in list(root.iter(elem_name)):
                for attr in invalid_attrs:
                    if attr in elem.attrib:
                        del elem.attrib[attr]
                        fixes.append(f"Removed invalid '{attr}' attribute from <{elem_name}> in {filename}")

        # Step 4d: Fix invalid colspan values on table cells (td/th/entry)
        # HTML colspan must be numeric, not column names like "c1"
        for td in list(root.iter('td')) + list(root.iter('th')) + list(root.iter('entry')):
            colspan = td.get('colspan')
            if colspan and not colspan.isdigit():
                # Invalid colspan value - remove it or extract number if possible
                if colspan.startswith('c') and colspan[1:].isdigit():
                    # Convert "c1" to "1" (but span of 1 can be omitted)
                    span_val = int(colspan[1:])
                    if span_val > 1:
                        td.set('colspan', str(span_val))
                        fixes.append(f"Fixed invalid colspan='{colspan}' to colspan='{span_val}' in {filename}")
                    else:
                        del td.attrib['colspan']
                        fixes.append(f"Removed invalid colspan='{colspan}' (single column) in {filename}")
                else:
                    del td.attrib['colspan']
                    fixes.append(f"Removed invalid colspan='{colspan}' in {filename}")

        # Step 4e: Fix HTML-style tables missing required caption element
        # HTML tables in DocBook require: caption, (col*|colgroup*), thead?, tfoot?, (tbody+|tr+)
        # Note: caption can be empty per DTD: (#PCDATA | %textobject.mix;)*
        for table in list(root.iter('table')):
            # Check if this is an HTML-style table (has tr/thead/tbody directly)
            first_child = table[0] if len(table) > 0 else None
            if first_child is not None:
                first_tag = self._local_name(first_child)
                # If first child is thead, tbody, tr, col, or colgroup, it's HTML-style and missing caption
                if first_tag in ('thead', 'tbody', 'tr', 'col', 'colgroup'):
                    # Insert empty caption at the beginning (empty is valid per DTD)
                    caption = etree.Element('caption')
                    table.insert(0, caption)
                    fixes.append(f"Added missing caption to HTML-style table in {filename}")

        # Step 5: Handle special complex structures

        # Convert <simplelist> to <itemizedlist>
        for simplelist in list(root.iter('simplelist')):
            simplelist.tag = 'itemizedlist'
            # Convert member children to listitem/para
            for member in list(simplelist.iter('member')):
                member.tag = 'listitem'
                # Wrap content in para if not already
                if not any(child.tag == 'para' for child in member):
                    para = etree.Element('para')
                    para.text = member.text
                    member.text = None
                    for child in list(member):
                        member.remove(child)
                        para.append(child)
                    member.append(para)
            fixes.append(f"Converted <simplelist> to <itemizedlist> in {filename}")

        # Convert <variablelist> entries if variablelist is excluded
        # Note: variablelist is excluded in rittexclusions.mod
        for varlist in list(root.iter('variablelist')):
            # Convert to itemizedlist
            new_list = etree.Element('itemizedlist')
            new_list.attrib.update(varlist.attrib)

            for varentry in varlist.iter('varlistentry'):
                listitem = etree.Element('listitem')

                # Get term and listitem content
                terms = list(varentry.iter('term'))
                term_text = ' '.join(''.join(t.itertext()) for t in terms)

                # Get listitem content from varlistentry
                for li in varentry.iter('listitem'):
                    # Create a para with term as bold/emphasis
                    if term_text:
                        term_para = etree.Element('para')
                        emphasis = etree.SubElement(term_para, 'emphasis')
                        emphasis.set('role', 'bold')
                        emphasis.text = term_text + ": "
                        listitem.append(term_para)

                    # Add the rest of the content
                    for child in li:
                        listitem.append(deepcopy(child))

                if len(listitem) > 0:
                    new_list.append(listitem)

            # Replace variablelist with new itemizedlist
            parent = varlist.getparent()
            if parent is not None:
                index = list(parent).index(varlist)
                parent.remove(varlist)
                parent.insert(index, new_list)
                fixes.append(f"Converted <variablelist> to <itemizedlist> in {filename}")

        # Handle procedure -> orderedlist conversion
        for procedure in list(root.iter('procedure')):
            procedure.tag = 'orderedlist'
            # Convert step to listitem
            for step in list(procedure.iter('step')):
                step.tag = 'listitem'
            fixes.append(f"Converted <procedure> to <orderedlist> in {filename}")

        # Handle note/warning/caution/important/tip if excluded
        # These are excluded in rittexclusions.mod
        admon_elements = ['note', 'warning', 'caution', 'important', 'tip']
        for admon_name in admon_elements:
            for admon in list(root.iter(admon_name)):
                # Convert to a para with emphasis showing the admonition type
                parent = admon.getparent()
                if parent is None:
                    continue

                index = list(parent).index(admon)

                # Create wrapper para with admonition label
                label_para = etree.Element('para')
                emphasis = etree.SubElement(label_para, 'emphasis')
                emphasis.set('role', 'bold')
                emphasis.text = f"[{admon_name.upper()}] "

                # Insert label para
                parent.insert(index, label_para)

                # Move admonition children to parent (after label)
                children = list(admon)
                for i, child in enumerate(children):
                    admon.remove(child)
                    parent.insert(index + 1 + i, child)

                # Remove empty admonition element
                parent.remove(admon)
                fixes.append(f"Converted <{admon_name}> to labeled paragraphs in {filename}")

        # Handle example -> figure conversion (example is excluded)
        for example in list(root.iter('example')):
            # Check if it has a title - if so, we can keep structure
            title = example.find('title')
            if title is not None:
                # Convert to a blockquote or just unwrap
                parent = example.getparent()
                if parent is not None:
                    index = list(parent).index(example)

                    # Add title as a para with emphasis
                    title_para = etree.Element('para')
                    emphasis = etree.SubElement(title_para, 'emphasis')
                    emphasis.set('role', 'bold')
                    emphasis.text = "Example: " + ''.join(title.itertext())
                    parent.insert(index, title_para)

                    # Move other children
                    children = [c for c in example if c.tag != 'title']
                    for i, child in enumerate(children):
                        example.remove(child)
                        parent.insert(index + 1 + i, child)

                    parent.remove(example)
                    fixes.append(f"Converted <example> to labeled content in {filename}")

        # Handle informalexample - just unwrap
        for infex in list(root.iter('informalexample')):
            parent = infex.getparent()
            if parent is not None:
                index = list(parent).index(infex)
                children = list(infex)
                for i, child in enumerate(children):
                    infex.remove(child)
                    parent.insert(index + i, child)
                parent.remove(infex)
                fixes.append(f"Unwrapped <informalexample> in {filename}")

        # Handle screen/programlisting -> literallayout or para
        # Note: these are excluded in rittexclusions.mod
        for code_elem_name in ['screen', 'programlisting']:
            for code_elem in list(root.iter(code_elem_name)):
                # Check if literallayout is available (it's not excluded)
                code_elem.tag = 'literallayout'
                fixes.append(f"Converted <{code_elem_name}> to <literallayout> in {filename}")

        return fixes

    def _normalize_whitespace(self, root: etree._Element, filename: str) -> List[str]:
        """Normalize whitespace in text content"""
        fixes = []

        # Elements where we should normalize whitespace
        normalize_in = ['title', 'para', 'term', 'entry']

        for elem_name in normalize_in:
            for elem in root.iter(elem_name):
                if elem.text:
                    # Normalize whitespace (collapse multiple spaces, trim)
                    normalized = ' '.join(elem.text.split())
                    if normalized != elem.text and elem.text.strip() == normalized.strip():
                        elem.text = normalized
                        # Don't report this as it's minor

        return fixes

    def _fix_malformed_symbol_sections(self, root: etree._Element, filename: str) -> List[str]:
        """
        Remove malformed section elements with single-character symbol titles.

        During PDF-to-XML conversion, mathematical symbols like Ø, ∅, ∑, etc.
        are sometimes incorrectly parsed as section headings, creating:
        - <sect1><title>Ø</title></sect1>
        - <sect1><title>M</title></sect1>

        These are clearly errors and should be removed. The content (if any)
        is typically empty or should be merged with adjacent sections.
        """
        fixes = []

        # Mathematical symbols and single characters that are likely conversion errors
        # when used as section titles
        symbol_chars = set('ØøÆæ∅∑∏∫∂∇≈≠≤≥±×÷αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΦΨΩ∞°′″')

        # Also detect single Latin characters (except I, A, V, X, L, C, D, M which could be Roman numerals)
        # and single uppercase letters that don't make sense as section titles
        valid_single_chars = {'A', 'I', 'V', 'X', 'L', 'C', 'D', 'M'}  # Roman numeral chars - might be valid

        # Section types to check (process in reverse order to handle nested)
        section_types = ['sect4', 'sect3', 'sect2', 'sect1']

        for section_type in section_types:
            for section in list(root.iter(section_type)):
                title_elem = section.find('title')
                if title_elem is None:
                    continue

                title_text = ''.join(title_elem.itertext()).strip()

                # Check if this is a malformed symbol section
                is_malformed = False

                # Case 1: Title is a single symbol character
                if len(title_text) == 1:
                    if title_text in symbol_chars:
                        is_malformed = True
                    elif title_text.isupper() and title_text not in valid_single_chars:
                        # Single uppercase letter that's not a likely Roman numeral
                        # could still be valid for index (A, B, C...) but check context
                        # If the section has no real content, it's likely malformed
                        has_content = False
                        for child in section:
                            if child.tag not in ['title', 'subtitle', 'titleabbrev',
                                                 'sect1info', 'sect2info', 'sect3info', 'sect4info']:
                                has_content = True
                                break
                        if not has_content:
                            is_malformed = True

                # Case 2: Title is just a few symbol characters
                elif len(title_text) <= 3 and all(c in symbol_chars or c.isspace() for c in title_text):
                    is_malformed = True

                # Case 3: Title starts/ends with common artifact patterns
                elif title_text.startswith('_') or title_text.endswith('_'):
                    # Underscores often indicate OCR/conversion artifacts
                    if len(title_text) <= 5:
                        is_malformed = True

                if is_malformed:
                    parent = section.getparent()
                    if parent is not None:
                        section_id = section.get('id', section_type)

                        # Check if section has any content worth preserving
                        content_children = [
                            child for child in section
                            if child.tag not in ['title', 'subtitle', 'titleabbrev',
                                                'sect1info', 'sect2info', 'sect3info', 'sect4info']
                        ]

                        if content_children:
                            # Move content to parent (before removing section)
                            section_index = list(parent).index(section)
                            for i, child in enumerate(content_children):
                                section.remove(child)
                                parent.insert(section_index + i, child)

                        # Remove the malformed section
                        parent.remove(section)

                        fixes.append(
                            f"Removed malformed <{section_type}> with symbol title '{title_text}' in {filename}"
                        )

                        if VALIDATION_REPORT_AVAILABLE:
                            self.verification_items.append(VerificationItem(
                                xml_file=filename,
                                line_number=section.sourceline if hasattr(section, 'sourceline') else None,
                                fix_type="Malformed Section Removal",
                                fix_description=f"Removed {section_type} with symbol/artifact title: '{title_text}'",
                                verification_reason="Section title appears to be a PDF conversion artifact.",
                                suggestion="Verify no important content was lost."
                            ))

        return fixes

    def _remove_empty_figures(self, root: etree._Element, filename: str) -> List[str]:
        """
        Final pass to remove any figure elements that have no valid content.

        A valid figure must have:
        - A title (required by DTD)
        - At least one of: mediaobject, graphic, or other content element

        Figures with only a title (no actual media) are removed.
        """
        fixes = []

        # Valid content elements for figures (after title)
        figure_content_tags = {
            'mediaobject', 'graphic', 'literallayout', 'programlisting',
            'screen', 'synopsis', 'informalequation'
        }

        for figure in list(root.iter('figure')):
            # Check if figure has any valid content (not just title)
            has_valid_content = False

            for child in figure:
                if child.tag in figure_content_tags:
                    has_valid_content = True
                    break
                # Also check for nested mediaobject/imagedata
                if child.tag == 'mediaobject':
                    if child.find('.//imagedata') is not None:
                        has_valid_content = True
                        break

            # Also check descendants for imagedata
            if not has_valid_content:
                if figure.find('.//imagedata') is not None:
                    has_valid_content = True
                elif figure.find('.//videodata') is not None:
                    has_valid_content = True
                elif figure.find('.//audiodata') is not None:
                    has_valid_content = True

            if not has_valid_content:
                parent = figure.getparent()
                if parent is not None:
                    figure_id = figure.get('id', 'figure')
                    title_elem = figure.find('title')
                    title_text = ''
                    if title_elem is not None:
                        title_text = ''.join(title_elem.itertext()).strip()[:40]

                    parent.remove(figure)

                    fixes.append(
                        f"Removed figure with no content (id='{figure_id}', title='{title_text}') in {filename}"
                    )

                    if VALIDATION_REPORT_AVAILABLE:
                        self.verification_items.append(VerificationItem(
                            xml_file=filename,
                            line_number=figure.sourceline if hasattr(figure, 'sourceline') else None,
                            fix_type="Empty Figure Removal",
                            fix_description=f"Removed figure with no media content: '{title_text}'",
                            verification_reason="Figure had title but no mediaobject, graphic, or other content.",
                            suggestion="Check if source PDF/EPUB had an image that wasn't extracted."
                        ))

        return fixes

    def _fix_invalid_link_references(self, root: etree._Element, filename: str) -> List[str]:
        """
        Remove or fix link elements with invalid IDREF linkend attributes.

        This handles:
        - Links referencing non-existent page_XXX IDs (from index entries)
        - Links with empty or invalid linkend values
        """
        fixes = []

        # Collect all valid IDs in the document
        valid_ids = set()
        for elem in root.iter():
            elem_id = elem.get('id')
            if elem_id:
                valid_ids.add(elem_id)

        # Find all link elements with linkend attributes
        for link in list(root.iter('link')):
            linkend = link.get('linkend')

            if not linkend:
                # Empty linkend - remove the link wrapper but keep text
                self._unwrap_element(link)
                fixes.append(f"Removed link with empty linkend in {filename}")
                continue

            # Check if linkend references a valid ID
            if linkend not in valid_ids:
                # Invalid reference - check if it's a page_XXX reference
                if linkend.startswith('page_'):
                    # Page reference that doesn't exist - unwrap the link keeping text
                    self._unwrap_element(link)
                    fixes.append(f"Removed invalid page link (linkend='{linkend}') in {filename}")

                    if VALIDATION_REPORT_AVAILABLE:
                        self.verification_items.append(VerificationItem(
                            xml_file=filename,
                            line_number=link.sourceline if hasattr(link, 'sourceline') else None,
                            fix_type="Invalid Link Removal",
                            fix_description=f"Removed link to non-existent page ID: '{linkend}'",
                            verification_reason="Link referenced a page_XXX ID that doesn't exist in the document.",
                            suggestion="Page links in index entries have been converted to plain text."
                        ))
                else:
                    # Other invalid reference - could be cross-chapter reference
                    # For now, unwrap these too
                    self._unwrap_element(link)
                    fixes.append(f"Removed link with invalid linkend='{linkend}' in {filename}")

        return fixes

    def _unwrap_element(self, element: etree._Element) -> None:
        """
        Remove an element but preserve its text content and children.
        The text/tail and children are moved to the parent.
        """
        parent = element.getparent()
        if parent is None:
            return

        # Get position of element in parent
        index = list(parent).index(element)

        # Handle text content
        element_text = element.text or ''
        element_tail = element.tail or ''

        # If there's text before this element (previous sibling's tail or parent's text)
        if index > 0:
            prev_sibling = parent[index - 1]
            prev_sibling.tail = (prev_sibling.tail or '') + element_text
        else:
            parent.text = (parent.text or '') + element_text

        # Move children to parent
        for i, child in enumerate(list(element)):
            parent.insert(index + i, child)

        # Handle tail - attach to last moved child or previous position
        if len(element) > 0:
            last_child = element[-1]
            last_child.tail = (last_child.tail or '') + element_tail
        elif index > 0:
            prev_sibling = parent[index - 1]
            prev_sibling.tail = (prev_sibling.tail or '') + element_tail
        else:
            parent.text = (parent.text or '') + element_tail

        # Remove the element
        parent.remove(element)

    def _fix_nested_sect1_elements(self, root: etree._Element, filename: str) -> List[str]:
        """
        Fix nested sect1 elements by converting them to sect2.

        In DocBook, sect1 cannot contain another sect1. The hierarchy must be:
        sect1 > sect2 > sect3 > sect4 > sect5

        This method finds sect1 elements nested inside other sect1 elements
        and converts them to sect2 (with their nested sections renumbered accordingly).
        """
        fixes = []

        def renumber_sections(element, current_level):
            """Recursively renumber sections starting from current_level."""
            section_map = {
                1: 'sect1', 2: 'sect2', 3: 'sect3', 4: 'sect4', 5: 'sect5',
                6: 'sect6', 7: 'sect7', 8: 'sect8', 9: 'sect9', 10: 'sect10'
            }

            # Process children first (depth-first)
            for child in list(element):
                if child.tag in section_map.values():
                    # Determine what level this section should be
                    child_level = current_level + 1
                    if child_level <= 10:
                        new_tag = section_map.get(child_level, 'sect10')
                        if child.tag != new_tag:
                            old_tag = child.tag
                            child.tag = new_tag
                            fixes.append(f"Converted nested <{old_tag}> to <{new_tag}> in {filename}")
                        # Recursively fix nested sections
                        renumber_sections(child, child_level)

        # Find all sect1 elements
        for sect1 in list(root.iter('sect1')):
            # Check if this sect1 contains nested sect1 elements
            nested_sect1s = sect1.findall('.//sect1')
            if nested_sect1s:
                # This sect1 has nested sect1 elements - fix them
                # The current sect1 is at level 1, so nested sections should start at level 2
                for child in list(sect1):
                    if child.tag == 'sect1':
                        # Convert to sect2
                        child.tag = 'sect2'
                        fixes.append(f"Converted nested <sect1> to <sect2> in {filename}")
                        # Recursively renumber any deeper nested sections
                        renumber_sections(child, 2)

        # Also handle sect1 nested inside chapter's sect1 (the specific case shown)
        # Look for sect1 elements that are direct children of sect1
        for sect1 in list(root.iter('sect1')):
            for child in list(sect1):
                if child.tag == 'sect1':
                    # This shouldn't happen anymore after the above, but just in case
                    child.tag = 'sect2'
                    fixes.append(f"Converted remaining nested <sect1> to <sect2> in {filename}")

        return fixes


def process_zip_package(
    zip_path: Path,
    output_path: Path,
    dtd_path: Path,
    generate_reports: bool = True
) -> dict:
    """
    Apply comprehensive DTD fixes to all chapter files in a ZIP package.

    Args:
        zip_path: Input ZIP package
        output_path: Output ZIP package
        dtd_path: Path to DTD file
        generate_reports: Generate before/after validation reports

    Returns:
        Dictionary with statistics and validation results
    """
    stats = {
        'files_processed': 0,
        'files_fixed': 0,
        'total_fixes': 0,
        'errors_before': 0,
        'errors_after': 0,
        'improvement': 0
    }

    # Run pre-fix validation if available
    if VALIDATION_AVAILABLE and generate_reports:
        print("\n=== PRE-FIX VALIDATION ===")
        validator = EntityTrackingValidator(dtd_path)
        pre_report = validator.validate_zip_package(zip_path, output_report_path=None)
        stats['errors_before'] = pre_report.get_error_count()
        print(f"Found {stats['errors_before']} validation errors before fixes")

        # Show error breakdown by type
        error_types = {}
        for error in pre_report.errors:
            error_type = error.error_type
            error_types[error_type] = error_types.get(error_type, 0) + 1

        print("\nError types:")
        for error_type, count in sorted(error_types.items(), key=lambda x: -x[1]):
            print(f"  {error_type}: {count}")

    # Initialize fixer
    fixer = ComprehensiveDTDFixer(dtd_path)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()

        # Extract ZIP
        print(f"\n=== APPLYING COMPREHENSIVE FIXES ===")
        print(f"Extracting {zip_path.name}...")
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(extract_dir)

        # Find all chapter XML files
        chapter_files = list(extract_dir.rglob("ch*.xml"))
        print(f"Found {len(chapter_files)} chapter files to fix\n")

        for chapter_file in sorted(chapter_files):
            stats['files_processed'] += 1

            num_fixes, fix_descriptions = fixer.fix_chapter_file(chapter_file, chapter_file.name)

            if num_fixes > 0:
                stats['files_fixed'] += 1
                stats['total_fixes'] += num_fixes
                print(f"  ✓ {chapter_file.name}: Applied {num_fixes} fix(es)")

        # Recreate ZIP
        print(f"\nCreating fixed ZIP: {output_path.name}...")
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file_path in extract_dir.rglob('*'):
                if file_path.is_file():
                    arcname = file_path.relative_to(extract_dir)
                    zf.write(file_path, arcname)

    # Run post-fix validation
    if VALIDATION_AVAILABLE and generate_reports:
        print("\n=== POST-FIX VALIDATION ===")
        validator = EntityTrackingValidator(dtd_path)
        post_report = validator.validate_zip_package(output_path, output_report_path=None)
        stats['errors_after'] = post_report.get_error_count()
        stats['improvement'] = stats['errors_before'] - stats['errors_after']
        stats['improvement_pct'] = (stats['improvement'] / stats['errors_before'] * 100) if stats['errors_before'] > 0 else 0

        print(f"Found {stats['errors_after']} validation errors after fixes")

        if stats['errors_after'] > 0:
            # Show remaining error breakdown by type
            error_types = {}
            for error in post_report.errors:
                error_type = error.error_type
                error_types[error_type] = error_types.get(error_type, 0) + 1

            print("\nRemaining error types:")
            for error_type, count in sorted(error_types.items(), key=lambda x: -x[1])[:10]:
                print(f"  {error_type}: {count}")

            # Show sample errors
            print("\nSample remaining errors (first 5):")
            for i, error in enumerate(post_report.errors[:5]):
                print(f"  {i+1}. {error.xml_file}:{error.line_number} - {error.error_description[:80]}")

        # Store reports for later use
        stats['pre_report'] = pre_report
        stats['post_report'] = post_report

    # Collect verification items
    stats['verification_items'] = fixer.verification_items

    return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Comprehensive DTD fixer for RittDoc packages"
    )
    parser.add_argument("input", help="Input ZIP package")
    parser.add_argument("-o", "--output", help="Output ZIP path (default: add _comprehensive_fixed suffix)")
    parser.add_argument("--dtd", default="RITTDOCdtd/v1.1/RittDocBook.dtd", help="Path to DTD file")
    parser.add_argument("--no-reports", action="store_true", help="Skip validation reports")

    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)

    dtd_path = Path(args.dtd)
    if not dtd_path.exists():
        print(f"Error: DTD file not found: {dtd_path}")
        sys.exit(1)

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.parent / f"{input_path.stem}_comprehensive_fixed{input_path.suffix}"

    # Process ZIP
    print("=" * 70)
    print("COMPREHENSIVE DTD FIXER FOR RITTDOC")
    print("=" * 70)

    stats = process_zip_package(input_path, output_path, dtd_path, generate_reports=not args.no_reports)

    # Print summary
    print("\n" + "=" * 70)
    print("FIX SUMMARY")
    print("=" * 70)
    print(f"Files processed:        {stats['files_processed']}")
    print(f"Files with fixes:       {stats['files_fixed']}")
    print(f"Total fixes applied:    {stats['total_fixes']}")

    if 'errors_before' in stats:
        print(f"\nValidation Results:")
        print(f"  Errors before:        {stats['errors_before']}")
        print(f"  Errors after:         {stats['errors_after']}")
        print(f"  Errors fixed:         {stats['improvement']}")
        if 'improvement_pct' in stats:
            print(f"  Improvement:          {stats['improvement_pct']:.1f}%")

    print(f"\nOutput: {output_path}")
    print("=" * 70)

    if stats.get('errors_after', 0) > 0:
        print(f"\n⚠ Warning: {stats['errors_after']} validation errors remain")
        print("These may require manual review or additional fixes")
        sys.exit(1)
    else:
        print("\n✓ Success: All DTD validation errors fixed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
