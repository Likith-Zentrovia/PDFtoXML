"""
DTD Fixer
=========

DTD-specific fix implementations that handle common validation errors:
1. Invalid content models (wrap/reorder elements)
2. Missing required elements (add defaults)
3. Invalid/undeclared elements (remove or convert)
4. Empty elements (add minimal content or remove)
5. Missing required attributes (add defaults)
6. Invalid attribute values (fix or remove)
"""

import tempfile
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import List, Tuple, Set, Optional, Any
import logging

try:
    from lxml import etree
    LXML_AVAILABLE = True
except ImportError:
    LXML_AVAILABLE = False
    etree = None  # type: ignore

from rittdoc_core.fixing.base import BaseFixer, FixResult
from rittdoc_core.xml.utils import local_name, is_inline_only, BLOCK_ELEMENTS
from rittdoc_core.validation.report import VerificationItem

logger = logging.getLogger(__name__)


class DTDFixer(BaseFixer):
    """
    Basic DTD fixer that applies common fixes to make XML DTD-compliant.

    This is a simpler version that handles the most common issues.
    For comprehensive fixing, use ComprehensiveDTDFixer.
    """

    def __init__(self, dtd_path: Path):
        """
        Initialize DTD fixer.

        Args:
            dtd_path: Path to the DTD file

        Raises:
            FileNotFoundError: If DTD file doesn't exist
            ImportError: If lxml is not available
        """
        if not LXML_AVAILABLE:
            raise ImportError(
                "lxml is required for DTD fixing. "
                "Install with: pip install lxml"
            )

        if not dtd_path.exists():
            raise FileNotFoundError(f"DTD file not found: {dtd_path}")

        self._dtd_path = dtd_path
        self._dtd = etree.DTD(str(dtd_path))

    @property
    def schema_type(self) -> str:
        return "DTD"

    @property
    def fix_categories(self) -> List[str]:
        return [
            "Missing Title",
            "Nested Para",
            "Empty Element",
            "Invalid Content Model",
            "Missing Attribute",
        ]

    def fix_file(self, file_path: Path, **kwargs) -> FixResult:
        """
        Apply fixes to a single XML file.

        Args:
            file_path: Path to XML file to fix
            **kwargs: Additional options

        Returns:
            FixResult with fixing outcome
        """
        result = FixResult()
        result.files_processed = 1

        try:
            parser = etree.XMLParser(remove_blank_text=False)
            tree = etree.parse(str(file_path), parser)
            root = tree.getroot()

            # Apply fixes
            file_context = file_path.name
            self._apply_fixes(root, file_context, result)

            if result.total_fixes > 0:
                result.files_fixed = 1
                tree.write(
                    str(file_path),
                    encoding='utf-8',
                    xml_declaration=True,
                    pretty_print=True
                )

        except Exception as e:
            logger.error(f"Error fixing {file_path}: {e}")

        return result

    def fix_package(self, package_path: Path, output_path: Path, **kwargs) -> FixResult:
        """
        Apply fixes to all chapter files in a ZIP package.

        Args:
            package_path: Path to input ZIP
            output_path: Path for output ZIP
            **kwargs: Additional options

        Returns:
            FixResult with fixing outcome
        """
        result = FixResult()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            extract_dir = tmp_path / "extracted"
            extract_dir.mkdir()

            # Extract ZIP
            logger.info(f"Extracting {package_path.name}...")
            with zipfile.ZipFile(package_path, 'r') as zf:
                zf.extractall(extract_dir)

            # Find and fix chapter files
            chapter_files = list(extract_dir.rglob("ch*.xml"))
            logger.info(f"Found {len(chapter_files)} chapter files to fix")

            for chapter_file in sorted(chapter_files):
                file_result = self.fix_file(chapter_file)
                result.merge(file_result)

                if file_result.total_fixes > 0:
                    logger.info(f"  {chapter_file.name}: Applied {file_result.total_fixes} fix(es)")

            # Recreate ZIP
            logger.info(f"Creating fixed ZIP: {output_path.name}...")
            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file_path in extract_dir.rglob('*'):
                    if file_path.is_file():
                        arcname = file_path.relative_to(extract_dir)
                        zf.write(file_path, arcname)

        return result

    def _apply_fixes(self, root: Any, file_context: str, result: FixResult) -> None:
        """Apply all fix routines to the XML tree."""
        self._fix_missing_titles(root, file_context, result)
        self._fix_nested_para(root, file_context, result)
        self._fix_empty_rows(root, file_context, result)

    def _fix_missing_titles(self, root: Any, file_context: str, result: FixResult) -> None:
        """Add missing title elements where required."""
        title_required = ['chapter', 'sect1', 'sect2', 'sect3', 'sect4', 'sect5',
                          'figure', 'table', 'example', 'appendix']

        for elem_name in title_required:
            for elem in root.iter(elem_name):
                has_title = any(child.tag == 'title' for child in elem)

                if not has_title:
                    # Create empty title - DTD supports empty <title/> elements
                    title = etree.Element('title')
                    # Leave title.text as None for empty <title/>
                    elem.insert(0, title)

                    result.add_fix(
                        fix_type="Missing Title",
                        description=f"Added missing empty <title> to <{elem_name}> in {file_context}",
                        verification_needed=True,
                        verification_reason="Empty title added for DTD compliance",
                        suggestion="Consider adding a descriptive title if appropriate",
                        file_context=file_context
                    )

    def _fix_nested_para(self, root: Any, file_context: str, result: FixResult) -> None:
        """Fix nested para elements."""
        for para in list(root.iter('para')):
            nested_paras = [child for child in para if local_name(child) == "para"]

            for nested_para in nested_paras:
                if is_inline_only(nested_para):
                    # Unwrap inline-only nested para
                    self._unwrap_inline_para(para, nested_para)
                    result.add_fix(
                        fix_type="Nested Para",
                        description=f"Unwrapped inline-only nested para in {file_context}",
                        file_context=file_context
                    )
                else:
                    # Flatten block-content nested para
                    self._flatten_block_para(para, nested_para)
                    result.add_fix(
                        fix_type="Nested Para",
                        description=f"Flattened block-content nested para in {file_context}",
                        verification_needed=True,
                        verification_reason="Content structure was modified",
                        suggestion="Verify content and links are preserved correctly",
                        file_context=file_context
                    )

    def _unwrap_inline_para(self, parent_para: Any, nested_para: Any) -> None:
        """Unwrap a nested para with inline-only content."""
        nested_index = list(parent_para).index(nested_para)

        # Move children before nested para
        for i, child in enumerate(list(nested_para)):
            parent_para.insert(nested_index + i, child)

        # Handle text
        if nested_para.text:
            if nested_index > 0:
                prev = parent_para[nested_index - 1]
                prev.tail = (prev.tail or '') + nested_para.text
            else:
                parent_para.text = (parent_para.text or '') + nested_para.text

        # Handle tail
        if nested_para.tail:
            if len(nested_para) > 0:
                last_child = parent_para[nested_index + len(nested_para) - 1]
                last_child.tail = (last_child.tail or '') + nested_para.tail
            elif nested_index > 0:
                prev = parent_para[nested_index - 1]
                prev.tail = (prev.tail or '') + nested_para.tail
            else:
                parent_para.text = (parent_para.text or '') + nested_para.tail

        parent_para.remove(nested_para)

    def _flatten_block_para(self, parent_para: Any, nested_para: Any) -> None:
        """Flatten a nested para with block content to sibling level."""
        grandparent = parent_para.getparent()
        if grandparent is None:
            return

        para_index = list(grandparent).index(parent_para)

        # Create new para at grandparent level
        new_para = etree.Element("para")
        if nested_para.get("id"):
            new_para.set("id", nested_para.get("id"))

        new_para.text = nested_para.text
        for child in nested_para:
            new_para.append(deepcopy(child))

        grandparent.insert(para_index + 1, new_para)

        # Handle tail text
        if nested_para.tail and nested_para.tail.strip():
            tail_para = etree.Element("para")
            tail_para.text = nested_para.tail
            grandparent.insert(para_index + 2, tail_para)

        parent_para.remove(nested_para)

    def _fix_empty_rows(self, root: Any, file_context: str, result: FixResult) -> None:
        """Remove empty row elements from tables."""
        for row in list(root.iter('row')):
            entries = list(row.iter('entry'))
            if len(entries) == 0:
                parent = row.getparent()
                if parent is not None:
                    parent.remove(row)
                    result.add_fix(
                        fix_type="Empty Element",
                        description=f"Removed empty <row/> element in {file_context}",
                        file_context=file_context
                    )


class ComprehensiveDTDFixer(DTDFixer):
    """
    Comprehensive DTD fixer that handles all common validation errors.

    This extends DTDFixer with additional fix routines for:
    - Invalid content models (wrap/reorder elements)
    - Missing required elements (add defaults)
    - Invalid/undeclared elements (remove or convert)
    - Empty elements with required content
    - Missing required attributes
    - Empty mediaobjects and misclassified figures
    """

    # Elements allowed as direct children of chapter
    ALLOWED_CHAPTER_CHILDREN: Set[str] = {
        'beginpage', 'chapterinfo', 'title', 'subtitle', 'titleabbrev', 'tocchap',
        'toc', 'lot', 'index', 'glossary', 'bibliography', 'sect1', 'section'
    }

    def __init__(self, dtd_path: Path):
        """Initialize comprehensive DTD fixer."""
        super().__init__(dtd_path)
        self.verification_items: List[VerificationItem] = []

    @property
    def fix_categories(self) -> List[str]:
        return [
            "Missing Title",
            "Nested Para",
            "Empty Element",
            "Invalid Content Model",
            "Missing Attribute",
            "Empty Mediaobject",
            "Misclassified Figure",
            "Invalid Element",
        ]

    def _apply_fixes(self, root: Any, file_context: str, result: FixResult) -> None:
        """Apply comprehensive fixes to the XML tree."""
        # Order matters - apply in specific sequence
        self._remove_empty_mediaobjects(root, file_context, result)
        self._remove_misclassified_figures(root, file_context, result)
        self._fix_empty_rows(root, file_context, result)
        self._fix_nested_para(root, file_context, result)
        self._fix_missing_titles(root, file_context, result)
        self._fix_invalid_content_models(root, file_context, result)
        self._fix_missing_required_attributes(root, file_context, result)
        self._fix_invalid_elements(root, file_context, result)

    def _remove_empty_mediaobjects(self, root: Any, file_context: str, result: FixResult) -> None:
        """Remove empty/placeholder mediaobjects."""
        for mediaobj in list(root.iter('mediaobject')):
            is_placeholder = False

            # Check for placeholder text
            for textobj in mediaobj.iter('textobject'):
                for phrase in textobj.iter('phrase'):
                    if phrase.text and ('not available' in phrase.text.lower() or
                                        'no image' in phrase.text.lower() or
                                        phrase.text.strip() in ['', 'N/A', 'n/a']):
                        is_placeholder = True
                        break

            # Check for real media content
            has_real_media = (mediaobj.find('.//imagedata') is not None or
                            mediaobj.find('.//videodata') is not None or
                            mediaobj.find('.//audiodata') is not None)

            if is_placeholder or not has_real_media:
                parent = mediaobj.getparent()
                if parent is not None:
                    parent_tag = parent.tag
                    parent.remove(mediaobj)

                    result.add_fix(
                        fix_type="Empty Mediaobject",
                        description=f"Removed empty/placeholder mediaobject from <{parent_tag}> in {file_context}",
                        verification_needed=True,
                        verification_reason="Mediaobject had no real media content",
                        suggestion="Verify document structure is still correct",
                        file_context=file_context
                    )

                    # Remove empty parent if needed
                    self._cleanup_empty_parent(parent, file_context, result)

    def _cleanup_empty_parent(self, parent: Any, file_context: str, result: FixResult) -> None:
        """Remove parent element if it's now empty."""
        has_content = (
            len(parent) > 0 or
            (parent.text and parent.text.strip()) or
            any(child.tail and child.tail.strip() for child in parent)
        )

        if not has_content:
            grandparent = parent.getparent()
            if grandparent is not None:
                grandparent.remove(parent)
                result.add_fix(
                    fix_type="Empty Element",
                    description=f"Removed empty parent <{parent.tag}> in {file_context}",
                    file_context=file_context
                )

    def _remove_misclassified_figures(self, root: Any, file_context: str, result: FixResult) -> None:
        """Convert or remove invalid figure elements."""
        for figure in list(root.iter('figure')):
            title_elem = figure.find('title')

            has_real_image = (figure.find('.//imagedata') is not None or
                             figure.find('.//videodata') is not None or
                             figure.find('.//audiodata') is not None)

            has_placeholder = False
            for phrase in figure.iter('phrase'):
                if phrase.text and 'not available' in phrase.text.lower():
                    has_placeholder = True
                    break

            if has_real_image and not has_placeholder:
                continue

            title_text = ''
            if title_elem is not None:
                title_text = ''.join(title_elem.itertext()).strip()

            parent = figure.getparent()
            if parent is None:
                continue

            # Determine action based on title
            if not title_text or title_text.lower() in ['untitled', 'no title', 'n/a']:
                parent.remove(figure)
                result.add_fix(
                    fix_type="Misclassified Figure",
                    description=f"Removed empty figure with no meaningful title in {file_context}",
                    verification_needed=True,
                    verification_reason="Figure had no media and empty title",
                    suggestion="Verify this empty figure was not needed",
                    file_context=file_context
                )

            elif 'table' in title_text.lower():
                # Convert to para
                fig_index = list(parent).index(figure)
                para = etree.Element('para')
                for attr, value in figure.attrib.items():
                    para.set(attr, value)
                if title_elem.text:
                    para.text = title_elem.text
                for child in title_elem:
                    para.append(child)
                parent.insert(fig_index, para)
                parent.remove(figure)

                result.add_fix(
                    fix_type="Misclassified Figure",
                    description=f"Converted figure (table label) to para in {file_context}",
                    verification_needed=True,
                    verification_reason="Figure had 'table' in title but no image",
                    suggestion="Verify table caption is preserved correctly",
                    file_context=file_context
                )

            else:
                parent.remove(figure)
                result.add_fix(
                    fix_type="Misclassified Figure",
                    description=f"Removed empty figure '{title_text[:40]}' in {file_context}",
                    verification_needed=True,
                    verification_reason="Figure had no media content",
                    suggestion="Verify figure was not needed or check if media is missing",
                    file_context=file_context
                )

    def _fix_invalid_content_models(self, root: Any, file_context: str, result: FixResult) -> None:
        """Fix chapters with disallowed content as direct children."""
        for chapter in root.iter('chapter'):
            violating_elements = []

            past_title = False
            for child in chapter:
                if child.tag == 'title':
                    past_title = True
                    continue
                if not past_title:
                    continue
                if child.tag not in self.ALLOWED_CHAPTER_CHILDREN:
                    violating_elements.append(child)

            if violating_elements:
                chapter_id = chapter.get('id', 'chapter')

                # Create wrapper sect1 with empty title - DTD supports empty <title/> elements
                sect1 = etree.Element('sect1')
                sect1_id = f"{chapter_id}-intro"
                if len(sect1_id) > 24:
                    sect1_id = sect1_id[:24].rstrip('-')
                sect1.set('id', sect1_id)

                title = etree.Element('title')
                # Leave empty - no placeholder text needed
                sect1.append(title)

                for elem in violating_elements:
                    chapter.remove(elem)
                    sect1.append(elem)

                insert_index = 0
                for i, child in enumerate(chapter):
                    if child.tag in ['beginpage', 'chapterinfo', 'title', 'subtitle', 'titleabbrev', 'tocchap']:
                        insert_index = i + 1

                chapter.insert(insert_index, sect1)

                result.add_fix(
                    fix_type="Invalid Content Model",
                    description=f"Wrapped {len(violating_elements)} elements in <sect1> in chapter {chapter_id}",
                    verification_needed=True,
                    verification_reason="Section wrapper was auto-created",
                    suggestion="Review content structure if needed",
                    file_context=file_context
                )

    def _fix_missing_required_attributes(self, root: Any, file_context: str, result: FixResult) -> None:
        """Fix elements missing required attributes."""
        # Table tgroup requires cols attribute
        for tgroup in root.iter('tgroup'):
            if 'cols' not in tgroup.attrib:
                cols = 0
                for tbody in tgroup.iter('tbody'):
                    for row in tbody.iter('row'):
                        cols = len(list(row.iter('entry')))
                        break
                    break

                if cols == 0:
                    for thead in tgroup.iter('thead'):
                        for row in thead.iter('row'):
                            cols = len(list(row.iter('entry')))
                            break
                        break

                if cols == 0:
                    cols = 1

                tgroup.set('cols', str(cols))
                result.add_fix(
                    fix_type="Missing Attribute",
                    description=f"Added cols=\"{cols}\" to <tgroup> in {file_context}",
                    file_context=file_context
                )

    def _fix_invalid_elements(self, root: Any, file_context: str, result: FixResult) -> None:
        """Remove or convert invalid/undeclared elements."""
        invalid_to_remove = ['html', 'body', 'div', 'span', 'br', 'hr', 'style', 'script']

        for elem_name in invalid_to_remove:
            for elem in list(root.iter(elem_name)):
                parent = elem.getparent()
                if parent is None:
                    continue

                index = list(parent).index(elem)

                # Preserve text
                if elem.text:
                    if index > 0:
                        prev = parent[index - 1]
                        prev.tail = (prev.tail or '') + elem.text
                    else:
                        parent.text = (parent.text or '') + elem.text

                # Move children to parent
                for child in reversed(list(elem)):
                    elem.remove(child)
                    parent.insert(index + 1, child)

                parent.remove(elem)
                result.add_fix(
                    fix_type="Invalid Element",
                    description=f"Removed invalid element <{elem_name}> in {file_context}",
                    file_context=file_context
                )

        # Convert <p> to <para>
        for p_elem in list(root.iter('p')):
            p_elem.tag = 'para'
            result.add_fix(
                fix_type="Invalid Element",
                description=f"Converted <p> to <para> in {file_context}",
                file_context=file_context
            )

    def get_verification_items(self) -> List[VerificationItem]:
        """Get all items requiring manual verification."""
        return self.verification_items
