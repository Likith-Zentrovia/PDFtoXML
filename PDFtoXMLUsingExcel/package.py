from __future__ import annotations

import csv
import hashlib
import logging
import re
import shutil
import string
import tempfile
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from PIL import Image
from lxml import etree

# Import reference mapper for tracking resource transformations
try:
    from reference_mapper import ReferenceMapper, get_mapper
    HAS_REFERENCE_MAPPER = True
except ImportError:
    HAS_REFERENCE_MAPPER = False

logger = logging.getLogger(__name__)

BOOK_DOCTYPE_PUBLIC_DEFAULT = "-//RIS Dev//DTD DocBook V4.3 -Based Variant V1.1//EN"
BOOK_DOCTYPE_SYSTEM_DEFAULT = "http://LOCALHOST/dtd/V1.1/RittDocBook.dtd"


MediaFetcher = Callable[[str], Optional[bytes]]


@dataclass
class ChapterFragment:
    """Representation of an extracted chapter fragment."""

    entity: str
    filename: str
    element: etree._Element
    kind: str = "chapter"
    title: str = ""
    section_type: str = ""


@dataclass
class ImageMetadata:
    """Captured metadata for a content image."""

    filename: str
    original_filename: str
    chapter: str
    figure_number: str
    caption: str
    alt_text: str
    referenced_in_text: bool
    width: int
    height: int
    file_size: str
    format: str



def _local_name(element: etree._Element) -> str:
    tag = element.tag
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _is_chapter_node(element: etree._Element) -> bool:
    tag = _local_name(element)
    return tag in {
        "chapter",
        "appendix",
        "part",
        "article",
        "index",
    }


def _is_toc_node(element: etree._Element) -> bool:
    if _local_name(element) != "chapter":
        return False
    role = (element.get("role") or "").lower()
    if role.startswith("toc"):
        return True
    title = element.find("title")
    if title is not None:
        text = "".join(title.itertext()).strip().lower()
        if text == "table of contents":
            return True
    return False


def _extract_isbn(root: etree._Element) -> Optional[str]:
    isbn_elements = root.xpath(".//isbn")
    for node in isbn_elements:
        if isinstance(node, etree._Element):
            text = (node.text or "").strip()
            if text:
                cleaned = re.sub(r"[^0-9A-Za-z]", "", text)
                if cleaned:
                    return cleaned
    return None


def _sanitise_basename(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_-]", "", name)
    return cleaned or "book"


def _extract_title_text(element: etree._Element) -> str:
    title = element.find("title")
    if title is not None:
        text = "".join(title.itertext()).strip()
        if text:
            return text
    return ""

def _extract_bookinfo(root: etree._Element) -> Dict:
    """
    Extract book metadata from XML for BookInfo section.
    
    Returns dict with ISBN, title, authors, publisher, date, edition, copyright.
    Uses placeholders for missing data to ensure validation passes.
    """
    bookinfo = {
        'isbn': None,
        'title': None,
        'subtitle': None,
        'authors': [],
        'publisher': None,
        'pubdate': None,
        'edition': None,
        'copyright_holder': None,
        'copyright_year': None
    }
    
    # Look for existing bookinfo or info element
    info_elem = root.find('.//bookinfo') or root.find('.//info')
    
    if info_elem is not None:
        # Extract ISBN
        isbn_elem = info_elem.find('.//isbn')
        if isbn_elem is not None and isbn_elem.text:
            isbn_clean = re.sub(r'[^0-9X]', '', isbn_elem.text.strip())
            if isbn_clean:
                bookinfo['isbn'] = isbn_clean
        
        # Extract Title
        title_elem = info_elem.find('.//title')
        if title_elem is not None:
            bookinfo['title'] = ''.join(title_elem.itertext()).strip()
        
        # Extract Subtitle
        subtitle_elem = info_elem.find('.//subtitle')
        if subtitle_elem is not None:
            bookinfo['subtitle'] = ''.join(subtitle_elem.itertext()).strip()
        
        # Extract Authors - Multiple formats supported
        author_elems = info_elem.findall('.//authorgroup/author') or info_elem.findall('.//author')
        for author_elem in author_elems:
            personname_elem = author_elem.find('.//personname')
            if personname_elem is not None:
                firstname = ''
                surname = ''
                firstname_elem = personname_elem.find('.//firstname')
                if firstname_elem is not None and firstname_elem.text:
                    firstname = firstname_elem.text.strip()
                surname_elem = personname_elem.find('.//surname')
                if surname_elem is not None and surname_elem.text:
                    surname = surname_elem.text.strip()
                if firstname or surname:
                    bookinfo['authors'].append(f"{firstname} {surname}".strip())
        
        # Collaborative authors (collab/collabname)
        collab_elems = info_elem.findall('.//collab/collabname')
        for collab_elem in collab_elems:
            if collab_elem.text:
                bookinfo['authors'].append(collab_elem.text.strip())
        
        # Fallback: Check for editor if no authors
        if not bookinfo['authors']:
            editor_elems = info_elem.findall('.//editor')
            for editor_elem in editor_elems:
                personname_elem = editor_elem.find('.//personname')
                if personname_elem is not None:
                    firstname = ''
                    surname = ''
                    firstname_elem = personname_elem.find('.//firstname')
                    if firstname_elem is not None and firstname_elem.text:
                        firstname = firstname_elem.text.strip()
                    surname_elem = personname_elem.find('.//surname')
                    if surname_elem is not None and surname_elem.text:
                        surname = surname_elem.text.strip()
                    if firstname or surname:
                        bookinfo['authors'].append(f"{firstname} {surname} (Editor)".strip())
        
        # Extract Publisher
        publisher_elem = info_elem.find('.//publisher/publishername')
        if publisher_elem is not None and publisher_elem.text:
            bookinfo['publisher'] = publisher_elem.text.strip()
        
        # Extract Publication Date
        pubdate_elem = info_elem.find('.//pubdate')
        if pubdate_elem is not None and pubdate_elem.text:
            bookinfo['pubdate'] = pubdate_elem.text.strip()
        
        # Extract Edition
        edition_elem = info_elem.find('.//edition')
        if edition_elem is not None and edition_elem.text:
            bookinfo['edition'] = edition_elem.text.strip()
        
        # Extract Copyright
        copyright_elem = info_elem.find('.//copyright')
        if copyright_elem is not None:
            year_elem = copyright_elem.find('.//year')
            if year_elem is not None and year_elem.text:
                bookinfo['copyright_year'] = year_elem.text.strip()
            holder_elem = copyright_elem.find('.//holder')
            if holder_elem is not None and holder_elem.text:
                bookinfo['copyright_holder'] = holder_elem.text.strip()
    
    # If no authors, use publisher as fallback
    if not bookinfo['authors'] and bookinfo['publisher']:
        bookinfo['authors'].append(bookinfo['publisher'])
    
    return bookinfo


def _create_bookinfo_element(bookinfo: Dict) -> etree._Element:
    """
    Create a complete <bookinfo> element with all metadata.
    Uses placeholders for missing fields to ensure validation passes.
    """
    bookinfo_elem = etree.Element('bookinfo')
    
    # ISBN (use placeholder if not found)
    isbn_elem = etree.SubElement(bookinfo_elem, 'isbn')
    isbn_elem.text = bookinfo.get('isbn') or '0000000000000'
    
    # Title (use placeholder if not found)
    title_elem = etree.SubElement(bookinfo_elem, 'title')
    title_elem.text = bookinfo.get('title') or 'Untitled Book'
    
    # Subtitle (optional - only add if exists)
    if bookinfo.get('subtitle'):
        subtitle_elem = etree.SubElement(bookinfo_elem, 'subtitle')
        subtitle_elem.text = bookinfo['subtitle']
    
    # Authors
    authorgroup_elem = etree.SubElement(bookinfo_elem, 'authorgroup')
    authors = bookinfo.get('authors', [])
    if not authors:
        authors = ['Unknown Author']
    
    for author_name in authors:
        author_elem = etree.SubElement(authorgroup_elem, 'author')
        personname_elem = etree.SubElement(author_elem, 'personname')
        
        # Try to split name into firstname/surname
        parts = author_name.split()
        if len(parts) >= 2:
            firstname_elem = etree.SubElement(personname_elem, 'firstname')
            firstname_elem.text = ' '.join(parts[:-1])
            surname_elem = etree.SubElement(personname_elem, 'surname')
            surname_elem.text = parts[-1]
        else:
            surname_elem = etree.SubElement(personname_elem, 'surname')
            surname_elem.text = author_name
    
    # Publisher
    publisher_elem = etree.SubElement(bookinfo_elem, 'publisher')
    publishername_elem = etree.SubElement(publisher_elem, 'publishername')
    publishername_elem.text = bookinfo.get('publisher') or 'Unknown Publisher'
    
    # Publication Date
    pubdate_elem = etree.SubElement(bookinfo_elem, 'pubdate')
    pubdate_elem.text = bookinfo.get('pubdate') or '2024'
    
    # Edition
    edition_elem = etree.SubElement(bookinfo_elem, 'edition')
    edition_elem.text = bookinfo.get('edition') or '1st Edition'
    
    # Copyright
    copyright_elem = etree.SubElement(bookinfo_elem, 'copyright')
    year_elem = etree.SubElement(copyright_elem, 'year')
    year_elem.text = bookinfo.get('copyright_year') or bookinfo.get('pubdate') or '2024'
    holder_elem = etree.SubElement(copyright_elem, 'holder')
    holder_elem.text = bookinfo.get('copyright_holder') or bookinfo.get('publisher') or 'Copyright Holder'
    
    return bookinfo_elem

def _split_root(root: etree._Element) -> Tuple[etree._Element, List[ChapterFragment]]:
    root_copy = etree.Element(root.tag, attrib=dict(root.attrib), nsmap=root.nsmap)
    root_copy.text = root.text
    fragments: List[ChapterFragment] = []
    special_entities: Dict[str, str] = {}
    # front_matter_wrapper: Optional[etree._Element] = None
    # front_matter_entity_id: Optional[str] = None
    chapter_index = 1
    toc_counter = 0

    def _next_entity_id() -> str:
        nonlocal chapter_index
        entity_id = f"ch{chapter_index:04d}"
        chapter_index += 1
        return entity_id

    def _process_children(parent_copy: etree._Element, source_parent: etree._Element) -> None:
        nonlocal chapter_index, fragments, toc_counter # , front_matter_wrapper, front_matter_entity_id

        for child in source_parent:
            if not isinstance(child.tag, str):
                parent_copy.append(deepcopy(child))
                continue

            if _is_toc_node(child):
                toc_counter += 1
                title_text = _extract_title_text(child)
                role = (child.get("role") or "").lower()
                normalized_title = title_text.strip().lower()
                if role == "toc" or normalized_title == "table of contents":
                    special_key = "toc_primary"
                elif "detailed" in normalized_title:
                    special_key = "toc_detailed"
                else:
                    special_key = f"toc_{toc_counter:02d}"

                entity_id = special_entities.get(special_key)
                if entity_id is None:
                    entity_id = _next_entity_id()
                    special_entities[special_key] = entity_id
                    filename = f"{entity_id}.xml"
                    fragments.append(
                        ChapterFragment(
                            entity_id,
                            filename,
                            deepcopy(child),
                            kind="toc",
                            title=title_text,
                            section_type="toc",
                        )
                    )
                entity_node = etree.Entity(entity_id)
                entity_node.tail = child.tail
                parent_copy.append(entity_node)
                continue

            local_name = _local_name(child)
            if local_name == "part":
                # Create a chapter fragment for the part itself (for navigation/TOC links)
                # The fragment will contain the part's title and partintro content
                entity_id = _next_entity_id()
                filename = f"{entity_id}.xml"

                # Build the chapter element for the part fragment
                part_chapter = etree.Element("chapter", nsmap=child.nsmap)
                part_chapter.set("id", entity_id)
                part_chapter.set("role", "part")

                # Copy the title from the part
                part_title = child.find("title") if child.find("title") is not None else child.find("{*}title")
                if part_title is not None:
                    part_chapter.append(deepcopy(part_title))
                else:
                    # Create empty title if none exists
                    empty_title = etree.Element("title")
                    part_chapter.append(empty_title)

                # Copy partintro content if it exists
                partintro = child.find("partintro") if child.find("partintro") is not None else child.find("{*}partintro")
                has_content = False
                if partintro is not None:
                    # Copy all children from partintro into the chapter
                    for partintro_child in partintro:
                        part_chapter.append(deepcopy(partintro_child))
                        has_content = True

                # If no partintro or empty partintro, add a blank sect1 for DTD compliance
                if not has_content:
                    sect1 = etree.Element("sect1")
                    sect1.set("id", f"{entity_id}-s01")
                    sect1_title = etree.Element("title")
                    sect1.append(sect1_title)
                    # Add empty para for DTD compliance
                    sect1_para = etree.Element("para")
                    sect1.append(sect1_para)
                    part_chapter.append(sect1)

                # Create the fragment for the part
                fragments.append(
                    ChapterFragment(
                        entity_id,
                        filename,
                        part_chapter,
                        kind="chapter",
                        title=_extract_title_text(child),
                        section_type="part",
                    )
                )

                # Create the part wrapper in Book.XML
                part_copy = etree.Element(child.tag, nsmap=child.nsmap)
                # Assign the entity ID to the part element
                part_copy.set("id", entity_id)
                # Copy other attributes except id
                for attr_name, attr_value in child.attrib.items():
                    if attr_name != "id":
                        part_copy.set(attr_name, attr_value)

                # Add entity reference for the part fragment first
                part_entity_node = etree.Entity(entity_id)
                part_copy.append(part_entity_node)

                # Process remaining children (chapters inside the part)
                # Skip title and partintro as they're already in the part fragment
                for part_child in child:
                    part_child_name = _local_name(part_child)
                    if part_child_name in ("title", "partintro"):
                        continue
                    # Process chapter-like elements
                    if _is_chapter_node(part_child):
                        # Recursively handle as chapter
                        ch_entity_id = _next_entity_id()
                        ch_filename = f"{ch_entity_id}.xml"
                        ch_section_type = part_child_name or "chapter"
                        # Create a copy and ensure it has the unique ID
                        ch_element = deepcopy(part_child)
                        ch_element.set("id", ch_entity_id)
                        fragments.append(
                            ChapterFragment(
                                ch_entity_id,
                                ch_filename,
                                ch_element,
                                kind="chapter",
                                title=_extract_title_text(part_child),
                                section_type=ch_section_type,
                            )
                        )
                        ch_entity_node = etree.Entity(ch_entity_id)
                        ch_entity_node.tail = part_child.tail
                        part_copy.append(ch_entity_node)
                    else:
                        # Copy non-chapter elements as-is
                        part_copy.append(deepcopy(part_child))

                part_copy.tail = child.tail
                parent_copy.append(part_copy)
                continue

            if _is_chapter_node(child):
                is_index_chapter = False
                if local_name == "chapter":
                    role = (child.get("role") or "").lower()
                    """
                    if role == "front-matter":
                        special_key = "front_matter"
                        entity_id = special_entities.get(special_key)
                        if entity_id is None:
                            entity_id = _next_entity_id()
                            special_entities[special_key] = entity_id
                            front_matter_entity_id = entity_id
                            front_matter_wrapper = etree.Element(
                                child.tag, attrib=dict(child.attrib), nsmap=child.nsmap
                            )
                            front_matter_wrapper.text = child.text
                            for descendant in child:
                                front_matter_wrapper.append(deepcopy(descendant))
                            filename = "FrontMatter.xml"
                            fragments.append(
                                ChapterFragment(
                                    entity_id,
                                    filename,
                                    front_matter_wrapper,
                                    kind="chapter",
                                    title=_extract_title_text(child) or "Front Matter",
                                    section_type="front-matter",
                                )
                            )
                            entity_node = etree.Entity(entity_id)
                            entity_node.tail = child.tail
                            parent_copy.append(entity_node)
                        else:
                            if front_matter_wrapper is not None:
                                for descendant in child:
                                    front_matter_wrapper.append(deepcopy(descendant))
                            if parent_copy:
                                parent_copy[-1].tail = child.tail
                        continue
                    """
                    if role == "index":
                        is_index_chapter = True
                    else:
                        title_text = _extract_title_text(child).strip().lower()
                        if title_text == "index":
                            is_index_chapter = True
                elif local_name == "index":
                    is_index_chapter = True

                section_type = local_name or "chapter"
                if is_index_chapter:
                    special_key = "index"
                    entity_id = special_entities.get(special_key)
                    if entity_id is None:
                        entity_id = _next_entity_id()
                        special_entities[special_key] = entity_id
                        filename = f"{entity_id}.xml"
                        # Create a copy and ensure it has the unique ID
                        chapter_element = deepcopy(child)
                        chapter_element.set("id", entity_id)
                        fragments.append(
                            ChapterFragment(
                                entity_id,
                                filename,
                                chapter_element,
                                kind="chapter",
                                title=_extract_title_text(child) or "Index",
                                section_type="index",
                            )
                        )
                else:
                    entity_id = _next_entity_id()
                    filename = f"{entity_id}.xml"
                    # Create a copy and ensure it has the unique ID
                    chapter_element = deepcopy(child)
                    chapter_element.set("id", entity_id)
                    fragments.append(
                        ChapterFragment(
                            entity_id,
                            filename,
                            chapter_element,
                            kind="chapter",
                            title=_extract_title_text(child),
                            section_type=section_type,
                        )
                    )
                entity_node = etree.Entity(entity_id)
                entity_node.tail = child.tail
                parent_copy.append(entity_node)
                continue

            parent_copy.append(deepcopy(child))

    _process_children(root_copy, root)

    if not fragments:
        # Fallback: treat non-metadata children as a single chapter to ensure
        # downstream consumers receive at least one fragment.
        preserved = []
        extracted = []
        for child in root:
            if not isinstance(child.tag, str):
                preserved.append(deepcopy(child))
                continue
            if _local_name(child) in {"bookinfo", "info"}:
                preserved.append(deepcopy(child))
            else:
                extracted.append(deepcopy(child))

        entity_id = _next_entity_id()
        filename = f"{entity_id}.xml"
        wrapper = etree.Element("chapter")
        # Ensure the fallback chapter has a unique ID
        wrapper.set("id", entity_id)
        for node in extracted:
            wrapper.append(node)
        fragments.append(
            ChapterFragment(entity_id, filename, wrapper, title="", section_type="chapter")
        )

        root_copy[:] = []
        root_copy.text = root.text
        for node in preserved:
            root_copy.append(node)
        entity_node = etree.Entity(entity_id)
        root_copy.append(entity_node)

    root_copy.tail = root.tail
    return root_copy, fragments


def _ensure_toc_element(root: etree._Element) -> etree._Element:
    """
    Ensure a TOC element exists in the book, creating one if necessary.

    RittDocDTD-compliant structure:
    <toc>
      <title>Table of Contents</title>
      <tocchap>
        <tocentry><ulink url="ch0001.xml">Chapter Title</ulink></tocentry>
      </tocchap>
      ...
    </toc>
    """
    # Check for existing TOC node
    for child in root:
        if isinstance(child.tag, str) and _is_toc_node(child):
            return child

    # Create new <toc> element (RittDocDTD compliant)
    toc = etree.Element("toc")
    title_el = etree.SubElement(toc, "title")
    title_el.text = "Table of Contents"

    # Find insertion point after bookinfo
    insert_at = 0
    for idx, child in enumerate(root):
        if not isinstance(child.tag, str):
            continue
        if _local_name(child) in BOOKINFO_NODES:
            insert_at = idx + 1
        else:
            break

    root.insert(insert_at, toc)
    return toc


def _populate_toc_element(
    toc_element: etree._Element, chapter_fragments: Sequence[ChapterFragment]
) -> None:
    """
    Populate TOC element with chapter links in RittDocDTD-compliant format.

    Creates structure:
    <toc>
      <title>Table of Contents</title>
      <tocchap>
        <tocentry><ulink url="ch0001.xml">Chapter 1 Title</ulink></tocentry>
      </tocchap>
      ...
    </toc>
    """
    # Ensure title exists
    title_el = toc_element.find("title")
    if title_el is None:
        title_el = etree.SubElement(toc_element, "title")
    desired_title = "".join(title_el.itertext()).strip() or "Table of Contents"
    title_el.text = desired_title

    # Remove any existing tocchap elements (but keep title)
    for child in list(toc_element):
        if child is title_el:
            continue
        toc_element.remove(child)

    # Add tocchap entries for each chapter with proper structure
    for fragment in chapter_fragments:
        chapter_title = fragment.title or fragment.filename

        # Create tocchap element
        tocchap = etree.SubElement(toc_element, "tocchap")

        # Create tocentry with ulink
        tocentry = etree.SubElement(tocchap, "tocentry")
        ulink = etree.SubElement(tocentry, "ulink")
        ulink.set("url", fragment.filename)
        ulink.text = chapter_title


def _iter_imagedata(element: etree._Element) -> Iterable[etree._Element]:
    for node in element.iter():
        if isinstance(node.tag, str) and _local_name(node) in {"imagedata", "graphic"}:
            if node.get("fileref"):
                yield node


def _extract_caption_text(figure: Optional[etree._Element]) -> str:
    if figure is None:
        return ""
    caption = figure.find("caption")
    if caption is not None:
        text = "".join(caption.itertext()).strip()
        if text:
            return text
    title = figure.find("title")
    if title is not None:
        text = "".join(title.itertext()).strip()
        if text:
            return text
    return ""


def _has_caption_or_label(
    figure: Optional[etree._Element], image_node: etree._Element
) -> bool:
    if figure is not None:
        if _extract_caption_text(figure):
            return True
        for attr in ("label", "id"):
            value = (figure.get(attr) or "").strip()
            if value:
                return True
        label_node = figure.find("label")
        if label_node is not None:
            text = "".join(label_node.itertext()).strip()
            if text:
                return True

    mediaobject = next(
        (ancestor for ancestor in image_node.iterancestors() if _local_name(ancestor) == "mediaobject"),
        None,
    )
    if mediaobject is not None:
        caption_node = mediaobject.find("caption")
        if caption_node is not None:
            text = "".join(caption_node.itertext()).strip()
            if text:
                return True

    for attr in ("label", "id"):
        value = (image_node.get(attr) or "").strip()
        if value:
            return True

    return False


def _extract_alt_text(image_node: etree._Element) -> str:
    alt = image_node.get("alt") or image_node.get("xlink:title")
    if alt:
        return alt.strip()

    mediaobject = next(
        (ancestor for ancestor in image_node.iterancestors() if _local_name(ancestor) == "mediaobject"),
        None,
    )
    if mediaobject is not None:
        for textobject in mediaobject.findall("textobject"):
            text = "".join(textobject.itertext()).strip()
            if text:
                return text
    return ""


DECORATIVE_KEYWORDS = {"logo", "watermark", "copyright", "trademark", "tm", "brand", "icon"}
BACKGROUND_KEYWORDS = {"background", "texture", "gradient", "border", "pattern", "header", "footer"}
BOOKINFO_NODES = {"bookinfo", "info", "titlepage"}
# Keywords for cover images (treated specially - always saved as decorative)
COVER_KEYWORDS = {"cover"}


def _is_full_page_image(image_node: etree._Element, image_data: Optional[bytes] = None) -> bool:
    """
    Detect if an image is a full-page image without meaningful text.
    
    Full-page images are typically:
    - Very large dimensions (close to page size)
    - Have no or minimal caption
    - Not part of a figure with meaningful label
    - Used as section dividers or decorative pages
    """
    # Check dimensions if we have the image data
    if image_data:
        width, height, _ = _inspect_image_bytes(image_data, ".jpg")
        if width > 0 and height > 0:
            # Typical page is ~600-800 pixels wide, ~800-1000 tall at 72dpi
            # Full page would be close to these dimensions
            is_large = (width > 500 and height > 700)
            aspect_ratio = height / width if width > 0 else 0
            # Page aspect ratio is typically 1.2-1.4 (letter/A4)
            is_page_like = 1.1 < aspect_ratio < 1.5
            
            if is_large and is_page_like:
                return True
    
    # Check if it's alone on a page (no siblings with substantial content)
    parent = image_node.getparent()
    if parent is not None:
        # Count text content near this image
        text_content = "".join(parent.itertext()).strip()
        # If very little text (< 100 chars), might be full page
        if len(text_content) < 100:
            return True
    
    return False


def _classify_image(
    image_node: etree._Element, 
    figure: Optional[etree._Element],
    image_data: Optional[bytes] = None
) -> str:
    """
    Classify image as 'content', 'decorative', or 'background'.
    
    USER REQUIREMENT: Only filter full-page decorative images, keep everything else as content.
    NO filtering by keywords, size, ancestry, or role.
    """
    original = image_node.get("fileref", "")
    name = Path(original).name.lower()
    
    # Images in figures are always content (they have captions/labels)
    if figure is not None:
        return "content"

    # ONLY FILTER: Check for full-page images without meaningful content
    if _is_full_page_image(image_node, image_data):
        logger.info(f"Detected full-page image: {name} - treating as decorative")
        return "decorative"

    # ═══════════════════════════════════════════════════════════════════════════
    # ALL OTHER FILTERS REMOVED PER USER REQUIREMENT
    # ═══════════════════════════════════════════════════════════════════════════
    # Previously filtered:
    # - Images in metadata sections (bookinfo) → NOW KEPT
    # - Cover images → NOW KEPT
    # - Decorative keywords (logo, watermark, etc.) → NOW KEPT
    # - Background keywords → NOW KEPT
    # - Role attributes → NOW KEPT
    # - Small images (< 50px) → NOW KEPT
    #
    # RATIONALE: If an image made it to unified.xml, it should be in the final package.
    # Only full-page decorative images should be filtered out.
    # ═══════════════════════════════════════════════════════════════════════════

    # Everything else is content
    return "content"


def _format_file_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f}KB"
    return f"{size_bytes}B"


def _inspect_image_bytes(data: bytes, fallback_suffix: str) -> Tuple[int, int, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width = int.from_bytes(data[16:20], "big", signed=False)
        height = int.from_bytes(data[20:24], "big", signed=False)
        return width, height, "PNG"

    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        if len(data) >= 10:
            width = int.from_bytes(data[6:8], "little", signed=False)
            height = int.from_bytes(data[8:10], "little", signed=False)
            return width, height, "GIF"

    if data.startswith(b"\xff\xd8"):
        offset = 2
        length = len(data)
        while offset + 1 < length:
            if data[offset] != 0xFF:
                break
            marker = data[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9}:  # SOI/EOI
                continue
            if offset + 1 >= length:
                break
            block_length = int.from_bytes(data[offset : offset + 2], "big", signed=False)
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if offset + 7 <= length:
                    height = int.from_bytes(data[offset + 3 : offset + 5], "big", signed=False)
                    width = int.from_bytes(data[offset + 5 : offset + 7], "big", signed=False)
                    return width, height, "JPEG"
                break
            offset += block_length

    suffix = fallback_suffix.lstrip(".")
    return 0, 0, suffix.upper() if suffix else ""


def _ensure_jpeg_bytes(data: bytes) -> bytes:
    """Convert arbitrary image bytes to baseline JPEG."""
    with Image.open(BytesIO(data)) as img:
        if img.mode not in ("RGB",):
            img = img.convert("RGB")
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=92)
        return buffer.getvalue()


def _chapter_code(fragment: ChapterFragment) -> Tuple[str, str]:
    section_type = (fragment.section_type or "").lower()
    if fragment.kind == "toc" or section_type == "toc":
        return "TOC", "TOC"
    if section_type == "index":
        return "Index", "Index"
    # if section_type == "front-matter":ßß
    #    return "FrontMatter", "Front Matter"

    if section_type == "appendix":
        title = fragment.title or ""
        match = re.search(r"appendix\s+([A-Z])", title, re.IGNORECASE)
        letter = match.group(1).upper() if match else "A"
        return f"Appendix{letter}", f"Appendix {letter}"

    match = re.match(r"ch(\d+)", fragment.entity, re.IGNORECASE)
    if match:
        chapter_num = int(match.group(1))
        return f"Ch{chapter_num:04d}", str(chapter_num)

    return "Ch0001", "1"


def extract_bookinfo(root):
    """Extract book metadata from XML."""
    bookinfo = {
        'isbn': None,
        'title': None,
        'authors': [],
        'publisher': None,
        'pubdate': None,
        'edition': None,
        'copyright_holder': None,
        'copyright_year': None
    }
    
    info_elem = root.find('.//bookinfo') or root.find('.//info')
    
    if info_elem is not None:
        # ISBN
        isbn_elem = info_elem.find('.//isbn')
        if isbn_elem is not None and isbn_elem.text:
            isbn_clean = re.sub(r'[^0-9X]', '', isbn_elem.text.strip())
            if isbn_clean:
                bookinfo['isbn'] = isbn_clean
        
        # Title
        title_elem = info_elem.find('.//title')
        if title_elem is not None:
            bookinfo['title'] = ''.join(title_elem.itertext()).strip()
        
        # Authors
        author_elems = info_elem.findall('.//authorgroup/author') or info_elem.findall('.//author')
        for author_elem in author_elems:
            personname_elem = author_elem.find('.//personname')
            if personname_elem is not None:
                firstname = ''
                surname = ''
                firstname_elem = personname_elem.find('.//firstname')
                if firstname_elem is not None and firstname_elem.text:
                    firstname = firstname_elem.text.strip()
                surname_elem = personname_elem.find('.//surname')
                if surname_elem is not None and surname_elem.text:
                    surname = surname_elem.text.strip()
                if firstname or surname:
                    bookinfo['authors'].append(f"{firstname} {surname}".strip())
        
        # Publisher
        publisher_elem = info_elem.find('.//publisher/publishername')
        if publisher_elem is not None and publisher_elem.text:
            bookinfo['publisher'] = publisher_elem.text.strip()
        
        # Date
        pubdate_elem = info_elem.find('.//pubdate')
        if pubdate_elem is not None and pubdate_elem.text:
            bookinfo['pubdate'] = pubdate_elem.text.strip()
        
        # Edition
        edition_elem = info_elem.find('.//edition')
        if edition_elem is not None and edition_elem.text:
            bookinfo['edition'] = edition_elem.text.strip()
        
        # Copyright
        copyright_elem = info_elem.find('.//copyright')
        if copyright_elem is not None:
            year_elem = copyright_elem.find('.//year')
            if year_elem is not None and year_elem.text:
                bookinfo['copyright_year'] = year_elem.text.strip()
            holder_elem = copyright_elem.find('.//holder')
            if holder_elem is not None and holder_elem.text:
                bookinfo['copyright_holder'] = holder_elem.text.strip()
    
    return bookinfo

def create_bookinfo_element(bookinfo):
    """Create <bookinfo> element with placeholders for missing data."""
    bookinfo_elem = etree.Element('bookinfo')
    
    # ISBN
    isbn_elem = etree.SubElement(bookinfo_elem, 'isbn')
    isbn_elem.text = bookinfo.get('isbn') or '0000000000000'
    
    # Title
    title_elem = etree.SubElement(bookinfo_elem, 'title')
    title_elem.text = bookinfo.get('title') or 'Untitled Book'
    
    # Authors
    authorgroup_elem = etree.SubElement(bookinfo_elem, 'authorgroup')
    authors = bookinfo.get('authors', []) or ['Unknown Author']
    for author_name in authors:
        author_elem = etree.SubElement(authorgroup_elem, 'author')
        personname_elem = etree.SubElement(author_elem, 'personname')
        parts = author_name.split()
        if len(parts) >= 2:
            firstname_elem = etree.SubElement(personname_elem, 'firstname')
            firstname_elem.text = ' '.join(parts[:-1])
            surname_elem = etree.SubElement(personname_elem, 'surname')
            surname_elem.text = parts[-1]
        else:
            surname_elem = etree.SubElement(personname_elem, 'surname')
            surname_elem.text = author_name
    
    # Publisher
    publisher_elem = etree.SubElement(bookinfo_elem, 'publisher')
    publishername_elem = etree.SubElement(publisher_elem, 'publishername')
    publishername_elem.text = bookinfo.get('publisher') or 'Unknown Publisher'
    
    # Date
    pubdate_elem = etree.SubElement(bookinfo_elem, 'pubdate')
    pubdate_elem.text = bookinfo.get('pubdate') or '2024'
    
    # Edition
    edition_elem = etree.SubElement(bookinfo_elem, 'edition')
    edition_elem.text = bookinfo.get('edition') or '1st Edition'
    
    # Copyright
    copyright_elem = etree.SubElement(bookinfo_elem, 'copyright')
    year_elem = etree.SubElement(copyright_elem, 'year')
    year_elem.text = bookinfo.get('copyright_year') or bookinfo.get('pubdate') or '2024'
    holder_elem = etree.SubElement(copyright_elem, 'holder')
    holder_elem.text = bookinfo.get('copyright_holder') or bookinfo.get('publisher') or 'Copyright Holder'
    
    return bookinfo_elem

def _write_metadata_files(metadata_dir: Path, entries: List[ImageMetadata]) -> None:
    metadata_dir.mkdir(parents=True, exist_ok=True)

    catalog_path = metadata_dir / "image_catalog.xml"
    root = etree.Element("images")
    for entry in entries:
        image_el = etree.SubElement(root, "image")
        etree.SubElement(image_el, "filename").text = entry.filename
        etree.SubElement(image_el, "original_filename").text = entry.original_filename
        etree.SubElement(image_el, "chapter").text = entry.chapter
        etree.SubElement(image_el, "figure_number").text = entry.figure_number
        etree.SubElement(image_el, "caption").text = entry.caption
        etree.SubElement(image_el, "alt_text").text = entry.alt_text
        etree.SubElement(image_el, "referenced_in_text").text = str(entry.referenced_in_text).lower()
        etree.SubElement(image_el, "width").text = str(entry.width)
        etree.SubElement(image_el, "height").text = str(entry.height)
        etree.SubElement(image_el, "file_size").text = entry.file_size
        etree.SubElement(image_el, "format").text = entry.format

    catalog_path.write_bytes(
        etree.tostring(root, encoding="UTF-8", pretty_print=True, xml_declaration=True)
    )

    manifest_path = metadata_dir / "image_manifest.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "Filename",
                "Chapter",
                "Figure",
                "Caption",
                "Alt-Text",
                "Original_Name",
                "File_Size",
                "Format",
            ]
        )
        for entry in entries:
            writer.writerow(
                [
                    entry.filename,
                    entry.chapter,
                    entry.figure_number,
                    entry.caption,
                    entry.alt_text,
                    entry.original_filename,
                    entry.file_size,
                    entry.format,
                ]
            )


def _has_non_media_content(element: etree._Element) -> bool:
    if (element.text or "").strip():
        return True
    for child in element:
        if not isinstance(child.tag, str):
            continue
        if _local_name(child) == "mediaobject":
            continue
        if "".join(child.itertext()).strip():
            return True
    return False


def _prune_empty_media_branch(start: Optional[etree._Element]) -> None:
    current = start
    while current is not None and isinstance(current.tag, str):
        parent = current.getparent()
        local = _local_name(current)

        if local == "imageobject":
            if len(current) == 0 and not (current.text or "").strip() and not current.attrib:
                if parent is not None:
                    parent.remove(current)
                    current = parent
                    continue
            break

        if local == "mediaobject":
            has_visual_child = any(
                isinstance(child.tag, str)
                and _local_name(child) in {"imageobject", "imageobjectco", "graphic", "videoobject", "audioobject"}
                for child in current
            )
            if not has_visual_child:
                if parent is not None:
                    parent.remove(current)
                    current = parent
                    continue
            break

        if local == "figure":
            has_mediaobject = any(
                isinstance(child.tag, str) and _local_name(child) == "mediaobject" for child in current
            )
            if not has_mediaobject and not _has_non_media_content(current):
                if parent is not None:
                    parent.remove(current)
                    current = parent
                    continue
            break

        current = parent


def _remove_image_node(image_node: etree._Element) -> None:
    parent = image_node.getparent()
    if parent is not None:
        parent.remove(image_node)
        _prune_empty_media_branch(parent)


def _handle_decorative_image(
    image_node: etree._Element,
    decor_dir: Path,
    shared_dir: Path,
    decor_cache: Dict[str, Path],
    media_fetcher: Optional[MediaFetcher],
    hash_index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> None:
    """
    Handle images that are not part of figure elements.

    Saves images to the MultiMedia folder and detects duplicates using SHA-256 hash
    to avoid storing the same image multiple times.

    Args:
        image_node: The image element to process
        decor_dir: Directory for images (MultiMedia folder)
        shared_dir: Same as decor_dir (kept for API compatibility)
        decor_cache: Cache mapping original filename to saved path
        media_fetcher: Function to fetch image bytes
        hash_index: Optional dict mapping image hash to metadata (for duplicate detection)
    """
    original = image_node.get("fileref", "")
    if not original:
        return
    
    filename = Path(original).name or original
    decor_dir.mkdir(parents=True, exist_ok=True)
    shared_dir.mkdir(parents=True, exist_ok=True)
    
    # Check if we already processed this exact filename
    target_path = decor_cache.get(filename)
    if target_path is not None:
        # Already cached - ensure we point at current location
        rel = target_path.relative_to(decor_dir.parent)
        # Just use the filename without MultiMedia/ prefix
        image_node.set("fileref", rel.name)
        logger.debug(f"Reusing cached decorative image: {filename}")
        return
    
    # Fetch the image data
    data = media_fetcher(original) if media_fetcher else None

    # If not found by full path, try just the filename in MultiMedia folder
    if data is None and media_fetcher:
        # Try fetching with just MultiMedia/filename
        alt_path = f"MultiMedia/{filename}"
        if alt_path != original:
            data = media_fetcher(alt_path)
            if data:
                logger.debug(f"Found image via alternate path: {alt_path}")

    if data is None or len(data) == 0:
        logger.warning("Skipping decorative image %s because it is missing or empty", original)
        _remove_image_node(image_node)
        return
    
    # Calculate SHA-256 hash for duplicate detection
    if hash_index is not None:
        image_hash = hashlib.sha256(data).hexdigest()
        entry = hash_index.get(image_hash)
        if entry:
            # Move original asset into SharedImages if this is the first duplicate encountered.
            if not entry["stored_in_shared"]:
                shared_path = shared_dir / entry["filename"]
                try:
                    entry["path"].rename(shared_path)
                except OSError:
                    shared_path.write_bytes(entry["path"].read_bytes())
                    entry["path"].unlink(missing_ok=True)
                entry["path"] = shared_path
                entry["stored_in_shared"] = True
                for node in entry["nodes"]:
                    # Just use filename without MultiMedia/SharedImages/ prefix
                    node.set("fileref", entry['filename'])
                decor_cache[entry["filename"]] = shared_path
            entry["nodes"].append(image_node)
            # Just use filename without MultiMedia/SharedImages/ prefix
            image_node.set("fileref", entry['filename'])
            decor_cache[filename] = entry["path"]
            logger.info(
                "Detected duplicate image: %s → reusing %s",
                filename,
                entry["filename"],
            )
            return

        hash_index[image_hash] = {
            "filename": filename,
            "path": decor_dir / filename,
            "nodes": [image_node],
            "stored_in_shared": False,
        }

    # Save the image to decorative directory
    target_path = decor_dir / filename
    target_path.write_bytes(data)
    decor_cache[filename] = target_path
    # Just use filename without MultiMedia/Decorative/ prefix
    image_node.set("fileref", filename)

    # Track mapping in reference mapper (decorative images keep original name)
    if HAS_REFERENCE_MAPPER:
        try:
            mapper = get_mapper()
            for orig_path, ref in mapper.resources.items():
                if ref.intermediate_name == filename:
                    mapper.update_final_name(orig_path, final_name)
                    break
        except Exception as e:
            logger.debug(f"Could not update reference mapper for decorative image: {e}")
    logger.debug("Saved decorative image: %s", filename)

def _cleanup_book_structure(book_root: etree._Element) -> None:
    """
    Clean up the book element structure to ensure DTD compliance.

    Fixes:
    1. Removes standalone <title> elements (title should be inside <bookinfo>)
    2. Wraps standalone <mediaobject> elements in <preface> (for cover images)
    3. Ensures correct element ordering

    Args:
        book_root: The book element to clean up (modified in place)
    """
    # Find elements to fix
    titles_to_remove = []
    mediaobjects_to_wrap = []

    for child in book_root:
        if not isinstance(child.tag, str):
            continue
        local_name = _local_name(child)

        # Standalone title at book level should be removed
        # (title should be inside bookinfo only)
        if local_name == 'title':
            titles_to_remove.append(child)

        # Standalone mediaobject at book level needs to be wrapped in preface
        elif local_name == 'mediaobject':
            mediaobjects_to_wrap.append(child)

    # Remove duplicate standalone titles
    for title in titles_to_remove:
        book_root.remove(title)
        logger.info("Removed standalone <title> from book root (should be in bookinfo)")

    # Wrap standalone mediaobjects in preface elements (for cover images)
    for mediaobject in mediaobjects_to_wrap:
        # Create preface wrapper
        preface = etree.Element('preface')
        preface_title = etree.SubElement(preface, 'title')
        preface_title.text = 'Cover'

        # Get mediaobject's position
        position = list(book_root).index(mediaobject)

        # Remove mediaobject from book and add to preface
        book_root.remove(mediaobject)
        preface.append(mediaobject)

        # Insert preface at the same position
        book_root.insert(position, preface)
        logger.info("Wrapped standalone <mediaobject> in <preface> element")


def _write_book_xml(
    target: Path,
    root_element: etree._Element,
    root_name: str,
    dtd_system: str,
    fragments: Sequence[ChapterFragment],
    *,
    processing_instructions: Sequence[Tuple[str, str]] = (),
    book_doctype_public: str = BOOK_DOCTYPE_PUBLIC_DEFAULT,
    book_doctype_system: Optional[str] = BOOK_DOCTYPE_SYSTEM_DEFAULT,
) -> None:
    header_lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    for target_name, data in processing_instructions:
        header_lines.append(f"<?{target_name} {data}?>")

    doctype_system = book_doctype_system or dtd_system
    header_lines.append(
        f'<!DOCTYPE {root_name} PUBLIC "{book_doctype_public}" "{doctype_system}" ['  # noqa: E501
    )
    for fragment in fragments:
        header_lines.append(f'\t<!ENTITY {fragment.entity} SYSTEM "{fragment.filename}">')
    header_lines.append("]>")
    header_text = "\n".join(header_lines) + "\n\n"

    body = etree.tostring(root_element, encoding="UTF-8", pretty_print=True, xml_declaration=False)
    target.write_text(header_text + body.decode("utf-8"), encoding="utf-8")


def _write_fragment_xml(
    target: Path,
    element: etree._Element,
    *,
    processing_instructions: Sequence[Tuple[str, str]] = (),
) -> None:
    header_lines: List[str] = []

    # Add XML declaration
    header_lines.append('<?xml version="1.0" encoding="UTF-8"?>')

    for target_name, data in processing_instructions:
        header_lines.append(f"<?{target_name} {data}?>")

    header = "\n".join(header_lines) + "\n\n"

    body = etree.tostring(element, encoding="UTF-8", pretty_print=True, xml_declaration=False)
    target.write_text(header + body.decode("utf-8"), encoding="utf-8")


def package_docbook(
    root: etree._Element,
    root_name: str,
    dtd_system: str,
    zip_path: str,
    *,
    processing_instructions: Sequence[Tuple[str, str]] = (),
    assets: Sequence[Tuple[str, Path]] = (),
    media_fetcher: Optional[MediaFetcher] = None,
    book_doctype_public: str = BOOK_DOCTYPE_PUBLIC_DEFAULT,
    book_doctype_system: str = BOOK_DOCTYPE_SYSTEM_DEFAULT,
    source_format: str = "pdf",
    metadata_dir: Optional[Path] = None,
) -> Path:
    """Package the DocBook tree into a chapterised ZIP bundle.

    Args:
        source_format: Format of the source document ('pdf' or 'epub').
                      For PDF and ePub sources, ALL images are retained without filtering or categorization
                      since the upstream extractors (Multipage_Image_Extractor.py) already handle filtering.
    """
    
    print("\n" + "="*70)
    print("STEP 5: Packaging DocBook to ZIP")
    print("="*70)
    
    # CRITICAL: Load reference mapping from Phase 1 if available
    # This allows us to map intermediate image names → final chapter names
    if HAS_REFERENCE_MAPPER:
        zip_dir = Path(zip_path).parent
        # Try to find reference mapping JSON from Phase 1
        possible_mapping_files = [
            zip_dir / f"{Path(zip_path).stem}_reference_mapping_phase1.json",
            zip_dir / f"{Path(zip_path).stem.replace('_structured', '')}_reference_mapping_phase1.json",
        ]
        
        # Also try without pre_fixes prefix
        base_stem = Path(zip_path).stem.replace('pre_fixes_', '').replace('_structured', '')
        possible_mapping_files.extend([
            zip_dir / f"{base_stem}_reference_mapping_phase1.json",
            zip_dir / f"{base_stem}_reference_mapping.json",
        ])
        
        loaded_mapping = False
        for mapping_file in possible_mapping_files:
            if mapping_file.exists():
                try:
                    mapper = get_mapper()
                    mapper.import_from_json(mapping_file)
                    loaded_mapping = True
                    print(f"  ✓ Loaded reference mapping: {mapping_file.name}")
                    print(f"     - {len(mapper.resources)} images tracked")
                    break
                except Exception as e:
                    logger.warning(f"Failed to load reference mapping from {mapping_file}: {e}")
        
        if not loaded_mapping:
            print(f"  ⚠ No reference mapping found - will use fallback image resolution")
            logger.warning(f"Checked for mapping files: {[str(f) for f in possible_mapping_files]}")

    # Check if we should bypass image filtering
    # For PDF sources: Multipage_Image_Extractor.py already does all necessary filtering
    # For ePub sources: All images should be retained
    # Therefore, bypass filtering for BOTH PDF and ePub
    bypass_filtering = source_format.lower() in ["epub", "pdf"]
    if bypass_filtering:
        print(f"  ✓ {source_format.upper()} source detected: ALL images will be retained without additional filtering")
        print(f"     NOTE: Images are expected in MultiMedia folder - no classification, no caption checks")
        logger.info(f"{source_format.upper()} mode: Bypassing all image filtering and categorization (upstream extractor already filtered)")

    print("  → Splitting book into chapters...")
    book_root, fragments = _split_root(root)
    print(f"  → Found {len(fragments)} fragments")

    # Generate TOC with chapter titles and links (RittDocDTD compliant)
    toc_element = _ensure_toc_element(book_root)
    chapter_fragments = [fragment for fragment in fragments if fragment.kind == "chapter"]
    _populate_toc_element(toc_element, chapter_fragments)
    print(f"  → Generated TOC with {len(chapter_fragments)} chapter links")

    print("  → Extracting book metadata...")
    
    # CRITICAL: Try to get ISBN13 from metadata file FIRST (primary source)
    isbn_from_metadata = None
    if metadata_dir is not None:
        try:
            from metadata_processor import get_isbn_from_metadata
            isbn_from_metadata = get_isbn_from_metadata(Path(metadata_dir))
            if isbn_from_metadata:
                print(f"  → Found ISBN13 from metadata file: {isbn_from_metadata}")
                logger.info(f"Using ISBN from metadata file: {isbn_from_metadata}")
        except Exception as e:
            logger.warning(f"Could not read ISBN from metadata file: {e}")
    
    # Fallback: Extract ISBN from XML if not in metadata
    isbn_from_xml = _extract_isbn(root)
    if not isbn_from_metadata and isbn_from_xml:
        print(f"  → Using ISBN from XML: {isbn_from_xml}")
        logger.info(f"Using ISBN from XML (metadata not available): {isbn_from_xml}")
    
    # Use ISBN from metadata if available, otherwise from XML
    isbn = isbn_from_metadata or isbn_from_xml
    
    # Extract book metadata from original root
    bookinfo_data = _extract_bookinfo(root)
    
    # CRITICAL: Override with ISBN from metadata if available
    if isbn_from_metadata:
        bookinfo_data['isbn'] = isbn_from_metadata
    elif not bookinfo_data['isbn']:
        bookinfo_data['isbn'] = isbn
    
    # Log extracted metadata for verification
    logger.info("Book Metadata Extracted:")
    logger.info(f"  ISBN: {bookinfo_data.get('isbn') or '[Using placeholder]'}")
    logger.info(f"  Title: {bookinfo_data.get('title') or '[Using placeholder]'}")
    if bookinfo_data.get('authors'):
        logger.info(f"  Authors: {', '.join(bookinfo_data['authors'])}")
    else:
        logger.info("  Authors: [Using placeholder]")
    logger.info(f"  Publisher: {bookinfo_data.get('publisher') or '[Using placeholder]'}")
    logger.info(f"  Date: {bookinfo_data.get('pubdate') or '[Using placeholder]'}")
    logger.info(f"  Edition: {bookinfo_data.get('edition') or '[Using placeholder]'}")
    
    # Remove any existing bookinfo/info elements from book_root
    for elem in list(book_root.findall('.//bookinfo')):
        book_root.remove(elem)
    for elem in list(book_root.findall('.//info')):
        book_root.remove(elem)
    
    # Create new bookinfo element with all metadata
    bookinfo_elem = _create_bookinfo_element(bookinfo_data)
    
    # Insert bookinfo at the beginning of book_root
    # (after any existing title if present, otherwise at position 0)
    title_elem = book_root.find('.//title')
    if title_elem is not None and title_elem.getparent() is book_root:
        # Insert after title
        title_index = list(book_root).index(title_elem)
        book_root.insert(title_index + 1, bookinfo_elem)
    else:
        # Insert at beginning
        book_root.insert(0, bookinfo_elem)

    # CRITICAL: Add id attribute to book element for R2 DocBook spec compliance
    # The id should be the ISBN (or sanitized book identifier)
    if book_root.get("id") is None:
        book_id = isbn or bookinfo_data.get('isbn') or Path(zip_path).stem or "book"
        # Sanitize the book_id to be a valid XML ID (no spaces, starts with letter)
        book_id = _sanitise_basename(book_id)
        book_root.set("id", book_id)
        print(f"  → Set book id=\"{book_id}\"")
        logger.info(f"Set book id attribute: {book_id}")

    base = _sanitise_basename(isbn or Path(zip_path).stem or "book")
    # Create two zip files: one with pre_fixes prefix and one with just ISBN
    pre_fixes_zip_path = Path(zip_path).parent / f"pre_fixes_{base}.zip"
    final_zip_path = Path(zip_path).parent / f"{base}.zip"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        book_path = tmp_path / "Book.XML"

        multi_media_dir = tmp_path / "MultiMedia"
        multi_media_dir.mkdir(parents=True, exist_ok=True)
        # All images go directly in MultiMedia folder - no subfolders
        decorative_dir = multi_media_dir
        shared_dir = multi_media_dir

        asset_paths: List[Tuple[str, Path]] = []
        for href, source in assets:
            try:
                data = Path(source).read_bytes()
            except OSError as exc:
                logger.warning("Failed to read stylesheet asset %s: %s", source, exc)
                continue
            target_path = (tmp_path / href).resolve()
            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning("Failed to create directory for stylesheet %s: %s", href, exc)
                continue
            target_path.write_bytes(data)
            asset_paths.append((href, target_path))

        chapter_paths: List[Tuple[ChapterFragment, Path]] = []
        metadata_entries: List[ImageMetadata] = []
        decor_cache: Dict[str, Path] = {}
        decor_hash_index: Dict[str, Dict[str, Any]] = {}  # SHA-256 hash → metadata for duplicate detection

        # Track intermediate → final name mapping to avoid duplicate image extraction
        intermediate_to_final: Dict[str, str] = {}
        duplicate_images_skipped = 0
        
        # Track image processing statistics
        images_found_in_xml = 0
        images_successfully_copied = 0
        images_skipped_missing = 0
        images_skipped_classification = 0
        images_skipped_no_caption = 0
        images_skipped_duplicate = 0

        logger.info("Starting image processing with duplicate detection...")
        print("  → Processing images and media...")
        
        # Build page-to-chapter mapping using reference mapper
        if HAS_REFERENCE_MAPPER:
            try:
                mapper = get_mapper()
                print("  → Building page-to-chapter mapping...")
                
                # Map pages to chapters by scanning chapter fragments
                for fragment in fragments:
                    chapter_id = fragment.entity
                    
                    # Find all page references in this chapter
                    for page_elem in fragment.element.findall(".//page[@id]"):
                        page_id = page_elem.get("id")  # e.g., "page_5" or "p5"
                        if page_id:
                            mapper.register_chapter(page_id, chapter_id)
                            logger.debug(f"Mapped {page_id} → {chapter_id}")
                    
                    # Also check for page attributes or page number attributes
                    for elem in fragment.element.iter():
                        if not isinstance(elem.tag, str):
                            continue
                        # Check for page_num, page_id, etc.
                        for attr in ['page', 'page_id', 'page_num', 'page_number']:
                            page_ref = elem.get(attr)
                            if page_ref:
                                # Normalize to "page_X" format
                                page_id = f"page_{page_ref}" if not page_ref.startswith('page') else page_ref
                                mapper.register_chapter(page_id, chapter_id)
                                logger.debug(f"Mapped {page_id} → {chapter_id}")
                
                print(f"  → Mapped {len(mapper.chapter_map)} pages to chapters")
            except Exception as e:
                logger.warning(f"Failed to build page-to-chapter mapping: {e}")
                print(f"  ⚠ Warning: Page-to-chapter mapping failed: {e}")
        
        for frag_idx, fragment in enumerate(fragments, 1):
            if frag_idx % 10 == 0 or frag_idx == len(fragments):
                print(f"     Progress: {frag_idx}/{len(fragments)} fragments processed...")
            
            chapter_path = tmp_path / fragment.filename
            chapter_code, chapter_label = _chapter_code(fragment)
            figure_counter = 1
            # Track processed images by their original fileref, not Python object ID
            # (lxml may return different proxy objects for the same XML element)
            processed_filerefs: Set[str] = set()

            section_index: Dict[int, List[int]] = {}

            def _index_sections(node: etree._Element, prefix: List[int]) -> None:
                counter = 0
                for child in node:
                    if not isinstance(child.tag, str):
                        continue
                    if _local_name(child) == "section":
                        counter += 1
                        path = prefix + [counter]
                        section_index[id(child)] = path
                        _index_sections(child, path)
                    else:
                        _index_sections(child, prefix)

            _index_sections(fragment.element, [])

            def _section_suffix_for(node: etree._Element) -> str:
                ancestor = next(
                    (ancestor for ancestor in node.iterancestors() if _local_name(ancestor) == "section"),
                    None,
                )
                if ancestor is None:
                    return ""
                path = section_index.get(id(ancestor))
                if not path:
                    return ""
                return "s" + "".join(f"{value:02d}" for value in path)

            # Log all figures found in this fragment BEFORE processing
            all_figures = fragment.element.findall(".//figure")
            logger.info(f"Chapter {chapter_code}: Found {len(all_figures)} figures BEFORE processing")
            for fig in all_figures:
                fig_id = fig.get("id", "unknown")
                fig_images = list(_iter_imagedata(fig))
                filerefs = [img.get("fileref", "none") for img in fig_images]
                has_mediaobject = fig.find(".//mediaobject") is not None
                logger.info(f"  - Figure id={fig_id}, has_mediaobject={has_mediaobject}, images: {filerefs}")

            for figure in fragment.element.findall(".//figure"):
                figure_id = figure.get("id", "unknown")
                caption_text = _extract_caption_text(figure)
                images = list(_iter_imagedata(figure))
                logger.debug(f"Processing figure id={figure_id} with {len(images)} images, caption='{caption_text[:50] if caption_text else ''}'")
                if not images:
                    logger.debug(f"  Skipping figure id={figure_id} - no imagedata elements with fileref")
                    continue
                if len(images) == 1:
                    suffixes = [""]
                else:
                    suffixes = [
                        string.ascii_lowercase[idx]
                        if idx < len(string.ascii_lowercase)
                        else f"_{idx}"
                        for idx in range(len(images))
                    ]
                current_index = figure_counter
                saved_any = False
                for idx, image_node in enumerate(images):
                    original = image_node.get("fileref")
                    if original:
                        logger.debug(f"Figure loop: adding fileref={original} to processed_filerefs")
                        processed_filerefs.add(original)
                    images_found_in_xml += 1
                    if not original:
                        continue

                    data = media_fetcher(original) if media_fetcher else None

                    # Extract intermediate name for deduplication check
                    intermediate_name = Path(original).name if 'MultiMedia/' in original else original

                    # For PDF/ePub sources, bypass ALL filtering and include every image
                    if not bypass_filtering:
                        classification = _classify_image(image_node, figure, data)

                        if classification == "background":
                            parent = image_node.getparent()
                            if parent is not None:
                                parent.remove(image_node)
                            logger.debug(f"Removed background image: {original}")
                            images_skipped_classification += 1
                            continue

                        if classification == "decorative":
                            _handle_decorative_image(
                                image_node,
                                decorative_dir,
                                shared_dir,
                                decor_cache,
                                media_fetcher,
                                decor_hash_index,
                            )
                            images_skipped_classification += 1
                            continue

                        # Check if image is referenced in mapper - if so, keep it even without caption
                        is_referenced = False
                        if HAS_REFERENCE_MAPPER:
                            try:
                                mapper = get_mapper()
                                # Check if this image is in the reference mapper
                                intermediate_name = Path(original).name if 'MultiMedia/' in original else original
                                for orig_path, ref in mapper.resources.items():
                                    if ref.intermediate_name == intermediate_name and ref.referenced_in:
                                        is_referenced = True
                                        logger.info(f"Image {intermediate_name} is referenced in chapters: {ref.referenced_in}")
                                        break
                            except:
                                pass

                        # ═══════════════════════════════════════════════════════════════
                        # CAPTION REQUIREMENT REMOVED PER USER REQUEST
                        # ═══════════════════════════════════════════════════════════════
                        # Previously: Images without captions were skipped
                        # Now: Keep ALL images from unified.xml, caption or not
                        # 
                        # if not _has_caption_or_label(figure, image_node) and not is_referenced:
                        #     logger.warning("Skipping media asset for %s because it lacks caption or label", original)
                        #     _remove_image_node(image_node)
                        #     images_skipped_no_caption += 1
                        #     continue
                        # ═══════════════════════════════════════════════════════════════

                    # Check if we've already processed this image (deduplication)
                    if intermediate_name in intermediate_to_final:
                        existing_final_name = intermediate_to_final[intermediate_name]
                        new_fileref = existing_final_name  # Just filename, no MultiMedia/ prefix
                        image_node.set("fileref", new_fileref)
                        image_node.set("width", "100%")
                        image_node.set("scalefit", "1")
                        processed_filerefs.add(new_fileref)  # Track new fileref as processed
                        duplicate_images_skipped += 1
                        images_skipped_duplicate += 1
                        logger.info(f"Reusing existing image: {intermediate_name} → {existing_final_name}")

                        # Also update mapper for this duplicate reference
                        if HAS_REFERENCE_MAPPER:
                            try:
                                mapper = get_mapper()
                                for orig_path, ref in mapper.resources.items():
                                    if ref.intermediate_name == intermediate_name:
                                        # Update final name if not already set
                                        if not ref.final_name:
                                            mapper.update_final_name(orig_path, existing_final_name)
                                        break
                            except Exception as e:
                                logger.debug(f"Could not update mapper for duplicate {intermediate_name}: {e}")

                        saved_any = True
                        continue

                    extension = ".jpg"
                    letter = suffixes[idx]
                    section_suffix = _section_suffix_for(image_node)
                    name_base = f"{chapter_code}{section_suffix}f{current_index:02d}{letter}"
                    new_filename = f"{name_base}{extension}"
                    target_path = multi_media_dir / new_filename

                    if data is None:
                        if bypass_filtering:
                            # For PDF/ePub, try alternate paths before giving up
                            logger.warning("Missing media asset for %s - trying alternate paths", original)
                            # Try just the filename in MultiMedia
                            alt_name = Path(original).name
                            data = media_fetcher(f"MultiMedia/{alt_name}") if media_fetcher else None
                            if data is None:
                                logger.error("CRITICAL: Could not find media asset %s even after trying alternate paths", original)
                                logger.error("  This image was extracted by multipage_media_extractor but cannot be found now")
                                logger.error("  Skipping this image - check media fetcher search paths")
                                _remove_image_node(image_node)
                                images_skipped_missing += 1
                                continue
                        else:
                            logger.warning("Missing media asset for %s; skipping", original)
                            _remove_image_node(image_node)
                            images_skipped_missing += 1
                            continue

                    if len(data) == 0:
                        logger.warning("Skipping media asset for %s because it is empty", original)
                        _remove_image_node(image_node)
                        continue

                    try:
                        jpeg_bytes = _ensure_jpeg_bytes(data)
                        target_path.write_bytes(jpeg_bytes)
                        width, height, fmt = _inspect_image_bytes(jpeg_bytes, extension)
                        images_successfully_copied += 1
                        
                        # Update reference mapper with final name
                        if HAS_REFERENCE_MAPPER:
                            try:
                                mapper = get_mapper()
                                # Find the resource by intermediate name and update final name
                                found_in_mapper = False
                                for orig_path, ref in mapper.resources.items():
                                    if ref.intermediate_name == intermediate_name:
                                        mapper.update_final_name(orig_path, new_filename)
                                        # Also update chapter metadata
                                        if not ref.chapter_id:
                                            mapper.update_figure_metadata(
                                                orig_path,
                                                chapter_id=fragment.entity,
                                                image_number=current_index
                                            )
                                        logger.debug(f"Updated mapper: {intermediate_name} → {new_filename} (chapter {fragment.entity})")
                                        found_in_mapper = True
                                        break
                                if not found_in_mapper:
                                    logger.warning(f"Image {intermediate_name} not found in reference mapper - mapping may be incomplete")
                            except Exception as e:
                                logger.warning(f"Failed to update mapper for {intermediate_name}: {e}")
                    except Exception as e:
                        logger.error(f"Failed to process/write image {original}: {e}")
                        _remove_image_node(image_node)
                        continue
                    file_size = _format_file_size(len(jpeg_bytes))
                    if width and height and (width < 72 or height < 72):
                        logger.warning(
                            "Low resolution image %s detected (%dx%d)", original, width, height
                        )

                    alt_text = _extract_alt_text(image_node)
                    if not alt_text:
                        logger.warning("Missing alt text for image %s", original)
                    referenced = bool((figure.get("id") or "").strip())
                    if not referenced and caption_text:
                        if re.search(r"figure\s+\d", caption_text, re.IGNORECASE):
                            referenced = True

                    metadata_entries.append(
                        ImageMetadata(
                            filename=new_filename,
                            original_filename=Path(original).name or original,
                            chapter=chapter_label,
                            figure_number=f"{current_index}{letter}",
                            caption=caption_text or "",
                            alt_text=alt_text,
                            referenced_in_text=referenced,
                            width=width,
                            height=height,
                            file_size=file_size,
                            format=fmt,
                        )
                    )
                    new_fileref = new_filename  # Just filename, no MultiMedia/ prefix
                    image_node.set("fileref", new_fileref)
                    image_node.set("width", "100%")
                    image_node.set("scalefit", "1")
                    # Also track the new fileref as processed (lxml may use different proxy objects)
                    processed_filerefs.add(new_fileref)
                    logger.info(f"  Saved image in figure id={figure_id}: {original} → {new_filename}")

                    # Track intermediate → final mapping for deduplication
                    intermediate_to_final[intermediate_name] = new_filename

                    saved_any = True
                if saved_any:
                    logger.info(f"  Figure id={figure_id} processed successfully, figure_counter now {figure_counter + 1}")
                    figure_counter += 1

            for image_node in _iter_imagedata(fragment.element):
                original = image_node.get("fileref")
                # Check by fileref, not Python object ID (lxml may use different proxy objects)
                if original in processed_filerefs:
                    logger.debug(f"Standalone loop: skipping already-processed fileref={original}")
                    continue
                images_found_in_xml += 1
                if not original:
                    continue

                logger.debug(f"Standalone loop: processing image: {original}")
                data = media_fetcher(original) if media_fetcher else None

                # Extract intermediate name for deduplication check
                intermediate_name = Path(original).name if 'MultiMedia/' in original else original

                # For PDF/ePub sources, bypass ALL filtering and include every image
                if not bypass_filtering:
                    classification = _classify_image(image_node, None, data)

                    if classification == "background":
                        parent = image_node.getparent()
                        if parent is not None:
                            parent.remove(image_node)
                        logger.debug(f"Removed background image: {original}")
                        images_skipped_classification += 1
                        continue

                    if classification == "decorative":
                        _handle_decorative_image(
                            image_node,
                            decorative_dir,
                            shared_dir,
                            decor_cache,
                            media_fetcher,
                            decor_hash_index,
                        )
                        images_skipped_classification += 1
                        continue

                    # ═══════════════════════════════════════════════════════════════
                    # CAPTION REQUIREMENT REMOVED PER USER REQUEST  
                    # ═══════════════════════════════════════════════════════════════
                    # Previously: Images without captions were skipped
                    # Now: Keep ALL images from unified.xml, caption or not
                    #
                    # if not _has_caption_or_label(None, image_node):
                    #     logger.warning("Skipping media asset for %s because it lacks caption or label", original)
                    #     _remove_image_node(image_node)
                    #     images_skipped_no_caption += 1
                    #     continue
                    # ═══════════════════════════════════════════════════════════════

                # Check if we've already processed this image (deduplication)
                if intermediate_name in intermediate_to_final:
                    existing_final_name = intermediate_to_final[intermediate_name]
                    new_fileref = existing_final_name  # Just filename, no MultiMedia/ prefix
                    image_node.set("fileref", new_fileref)
                    image_node.set("width", "100%")
                    image_node.set("scalefit", "1")
                    processed_filerefs.add(new_fileref)  # Track new fileref as processed
                    duplicate_images_skipped += 1
                    images_skipped_duplicate += 1
                    logger.info(f"Reusing existing image: {intermediate_name} → {existing_final_name}")
                    continue

                extension = ".jpg"
                current_index = figure_counter
                section_suffix = _section_suffix_for(image_node)
                name_base = f"{chapter_code}{section_suffix}f{current_index:02d}"
                new_filename = f"{name_base}{extension}"
                target_path = multi_media_dir / new_filename

                if data is None:
                    if bypass_filtering:
                        # For PDF/ePub, try alternate paths before giving up
                        logger.warning("Missing media asset for %s - trying alternate paths", original)
                        # Try just the filename in MultiMedia
                        alt_name = Path(original).name
                        data = media_fetcher(f"MultiMedia/{alt_name}") if media_fetcher else None
                        if data is None:
                            logger.error("CRITICAL: Could not find media asset %s even after trying alternate paths", original)
                            logger.error("  This image was extracted by multipage_media_extractor but cannot be found now")
                            logger.error("  Skipping this image - check media fetcher search paths")
                            _remove_image_node(image_node)
                            images_skipped_missing += 1
                            continue
                        # If alternate path succeeded, fall through to process the image
                        logger.info("Found media asset via alternate path: %s", alt_name)
                    else:
                        logger.warning("Missing media asset for %s; skipping", original)
                        _remove_image_node(image_node)
                        images_skipped_missing += 1
                        continue

                if len(data) == 0:
                    logger.warning("Skipping media asset for %s because it is empty", original)
                    _remove_image_node(image_node)
                    continue

                try:
                    jpeg_bytes = _ensure_jpeg_bytes(data)
                    target_path.write_bytes(jpeg_bytes)
                    width, height, fmt = _inspect_image_bytes(jpeg_bytes, extension)
                    images_successfully_copied += 1
                except Exception as e:
                    logger.error(f"Failed to process/write image {original}: {e}")
                    _remove_image_node(image_node)
                    continue
                file_size = _format_file_size(len(jpeg_bytes))
                if width and height and (width < 72 or height < 72):
                    logger.warning(
                        "Low resolution image %s detected (%dx%d)", original, width, height
                    )
                alt_text = _extract_alt_text(image_node)
                if not alt_text:
                    logger.warning("Missing alt text for image %s", original)
                placeholder_caption = f"Figure {chapter_label}.{current_index:02d} (Unlabeled)"
                metadata_entries.append(
                    ImageMetadata(
                        filename=new_filename,
                        original_filename=Path(original).name or original,
                        chapter=chapter_label,
                        figure_number=str(current_index),
                        caption=placeholder_caption,
                        alt_text=alt_text,
                        referenced_in_text=False,
                        width=width,
                        height=height,
                        file_size=file_size,
                        format=fmt,
                    )
                )
                new_fileref = new_filename  # Just filename, no MultiMedia/ prefix
                image_node.set("fileref", new_fileref)
                image_node.set("width", "100%")
                image_node.set("scalefit", "1")
                processed_filerefs.add(new_fileref)  # Track new fileref as processed

                # Track intermediate → final mapping for deduplication
                intermediate_to_final[intermediate_name] = new_filename

                figure_counter += 1

            _write_fragment_xml(
                chapter_path,
                fragment.element,
                processing_instructions=processing_instructions,
            )
            # Log final state of figures in this chapter
            final_figures = fragment.element.findall(".//figure")
            logger.info(f"Chapter {chapter_code}: Writing {len(final_figures)} figures to {fragment.filename}")
            for fig in final_figures:
                fig_id = fig.get("id", "unknown")
                fig_images = list(_iter_imagedata(fig))
                filerefs = [img.get("fileref", "none") for img in fig_images]
                logger.info(f"  - Figure id={fig_id}, images: {filerefs}")
            chapter_paths.append((fragment, chapter_path))

        for image_node in _iter_imagedata(book_root):
            original = image_node.get("fileref")
            if not original:
                continue

            # Fetch data for classification
            data = media_fetcher(original) if media_fetcher else None

            # For PDF/ePub sources, bypass ALL filtering - handle all root images as decorative to preserve them
            if bypass_filtering:
                _handle_decorative_image(
                    image_node,
                    decorative_dir,
                    shared_dir,
                    decor_cache,
                    media_fetcher,
                    decor_hash_index,
                )
            else:
                classification = _classify_image(image_node, None, data)

                if classification == "background":
                    parent = image_node.getparent()
                    if parent is not None:
                        parent.remove(image_node)
                    logger.debug(f"Removed background image from root: {original}")
                    continue

                if classification == "decorative":
                    _handle_decorative_image(
                        image_node,
                        decorative_dir,
                        shared_dir,
                        decor_cache,
                        media_fetcher,
                        decor_hash_index,
                    )
                else:
                    logger.warning(
                        "Unexpected content image in root document: %s; treating as decorative",
                        original,
                    )
                    _handle_decorative_image(
                        image_node,
                        decorative_dir,
                        shared_dir,
                        decor_cache,
                        media_fetcher,
                        decor_hash_index,
                    )

        # Clean up book structure before writing
        # This removes invalid elements and ensures correct element order
        _cleanup_book_structure(book_root)

        _write_book_xml(
            book_path,
            book_root,
            root_name,
            dtd_system,
            fragments,
            processing_instructions=processing_instructions,
            book_doctype_public=book_doctype_public,
            book_doctype_system=book_doctype_system,
        )
        
        # Populate BookInfo from metadata file if available
        # This will override the bookinfo element with data from metadata.xls/csv/ONIX
        print("  → Populating Book.xml from metadata file...")
        try:
            from metadata_processor import populate_bookinfo_from_metadata
            
            # Search for metadata file in multiple locations
            search_dirs = []
            
            # 1. Explicitly provided metadata directory
            if metadata_dir is not None:
                search_dirs.append(Path(metadata_dir))
            
            # 2. Output directory (where ZIP will be created)
            search_dirs.append(Path(zip_path).parent)
            
            # 3. Current working directory
            search_dirs.append(Path.cwd())
            
            # Try each search directory
            metadata_found = False
            for search_dir in search_dirs:
                if not search_dir.exists():
                    continue
                    
                # Try to find and populate metadata
                from metadata_processor import find_metadata_file
                metadata_file = find_metadata_file(search_dir)
                
                if metadata_file is not None:
                    logger.info(f"Found metadata file in: {search_dir}")
                    metadata_found = populate_bookinfo_from_metadata(
                        book_path, 
                        search_dir, 
                        backup=False
                    )
                    if metadata_found:
                        print(f"  ✓ Book.xml populated from metadata file: {metadata_file.name}")
                        logger.info(f"Book.xml successfully populated with ISBN13 and metadata from {metadata_file.name}")
                        break
            
            if not metadata_found:
                print("  ⚠ No metadata file found - using placeholder values")
                logger.warning(f"No metadata file found - Book.xml will use placeholder values")
                logger.info(f"Searched for metadata in: {[str(d) for d in search_dirs]}")
                
        except ImportError:
            logger.warning("metadata_processor module not available")
        except Exception as e:
            logger.warning(f"Could not populate metadata from file: {e}")
        
        # Log image processing summary
        content_images = len(metadata_entries)
        other_images = len(set(decor_cache.values()))
        total_images = content_images + other_images

        logger.info(f"\n{'='*60}")
        logger.info("IMAGE PROCESSING SUMMARY")
        logger.info(f"{'='*60}")
        logger.info(f"Content images (in chapters): {content_images}")
        logger.info(f"Content image duplicates skipped: {duplicate_images_skipped}")
        logger.info(f"Other images: {other_images}")
        logger.info(f"Total images in MultiMedia: {total_images}")
        logger.info(f"{'='*60}\n")

        print(f"  → Content images: {content_images}")
        if duplicate_images_skipped > 0:
            print(f"  → Content image duplicates skipped: {duplicate_images_skipped}")
        print(f"  → Other images: {other_images}")
        print(f"  → Total images in MultiMedia: {total_images}")

        print("  → Creating ZIP archives...")
        pre_fixes_zip_path.parent.mkdir(parents=True, exist_ok=True)
        # First create the pre_fixes ZIP
        with zipfile.ZipFile(pre_fixes_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(book_path, "Book.XML")
            print(f"     Added: Book.XML")
            
            for fragment, chapter_path in chapter_paths:
                zf.write(chapter_path, fragment.filename)
            print(f"     Added: {len(chapter_paths)} chapter files")
            
            zf.writestr("multimedia/", "")

            media_count = 0
            media_errors = 0
            if multi_media_dir.exists():
                for media_file in sorted(multi_media_dir.rglob("*")):
                    if media_file.is_dir():
                        continue
                    rel_path = media_file.relative_to(multi_media_dir)
                    arcname = f"multimedia/{rel_path.as_posix()}"
                    try:
                        # Verify file is readable before adding to ZIP
                        if not media_file.exists() or media_file.stat().st_size == 0:
                            logger.warning(f"Skipping invalid media file: {arcname}")
                            media_errors += 1
                            continue
                        zf.write(media_file, arcname)
                        media_count += 1
                        logger.debug(f"Added media file to ZIP: {arcname}")
                    except Exception as e:
                        logger.error(f"Failed to add media file {arcname} to ZIP: {e}")
                        media_errors += 1
            print(f"     Added: {media_count} media files")
            if media_errors > 0:
                print(f"     Warning: {media_errors} media file(s) failed to add to ZIP")
            
            for href, asset_path in asset_paths:
                arcname = Path(href).as_posix()
                zf.write(asset_path, arcname)
            if asset_paths:
                print(f"     Added: {len(asset_paths)} asset files")

            # DTD files are NOT included in the package - they will be hosted on the production server
            # The DOCTYPE declaration points to http://LOCALHOST/dtd/V1.1/RittDocBook.dtd
            logger.info("DTD files excluded from package (hosted on production server)")
            print(f"     ℹ DTD files excluded (production server will provide them)")

        # Create a copy of the ZIP with just the ISBN name
        print(f"  → Creating second ZIP (clean package)...")
        shutil.copy2(pre_fixes_zip_path, final_zip_path)

        print(f"✓ Created pre_fixes ZIP → {pre_fixes_zip_path}")
        print(f"✓ Created final ZIP → {final_zip_path}")

    # Export reference mapping and validate
    if HAS_REFERENCE_MAPPER:
        try:
            mapper = get_mapper()
            # Export mapping to JSON
            mapping_path = final_zip_path.parent / f"{base}_reference_mapping.json"
            mapper.export_to_json(mapping_path)
            print(f"✓ Exported reference mapping → {mapping_path}")

            # Validate references
            is_valid, errors = mapper.validate(final_zip_path.parent)
            if not is_valid:
                print(f"⚠ Reference validation warnings ({len(errors)} issues):")
                for error in errors[:10]:  # Show first 10 errors
                    print(f"    - {error}")
                if len(errors) > 10:
                    print(f"    ... and {len(errors) - 10} more")
            else:
                print(f"✓ Reference validation passed")

            # Generate report
            report = mapper.generate_report()
            logger.info(f"\n{report}")
        except Exception as e:
            logger.warning(f"Could not export/validate reference mapping: {e}")

    # Return the final (clean) ZIP path - the pre_fixes ZIP is also created but this returns the clean one
    return final_zip_path


def make_file_fetcher(search_paths: Sequence[Path], reference_mapper=None) -> MediaFetcher:
    paths = [Path(p) for p in search_paths]
    # Log search paths at WARNING level so it's always visible
    print(f"  MediaFetcher search paths:")
    for p in paths:
        exists = p.exists()
        file_count = len(list(p.glob("*"))) if exists and p.is_dir() else 0
        print(f"    - {p} ({'EXISTS' if exists else 'NOT FOUND'}, {file_count} files)")
    logger.info(f"Media fetcher search paths: {[str(p) for p in paths]}")

    # Log mapper status for debugging
    if reference_mapper is None:
        logger.warning("MediaFetcher: reference_mapper is None! Will not be able to resolve final→intermediate names")
    elif not HAS_REFERENCE_MAPPER:
        logger.warning("MediaFetcher: HAS_REFERENCE_MAPPER is False! Reference mapper module not available")
    else:
        logger.info(f"MediaFetcher: Reference mapper has {len(reference_mapper.resources)} resources")
        # Log first few mappings for verification
        sample = list(reference_mapper.resources.items())[:3]
        for orig_path, ref in sample:
            logger.info(f"  Sample mapping: {ref.intermediate_name} → {ref.final_name or 'NOT_SET'}")

    def _fetch(name: str) -> Optional[bytes]:
        # Build list of candidate paths
        candidates = []

        # First, try to resolve through reference mapper if available
        # This handles the case where name is a final name (e.g., Ch0017s0201f01.jpg)
        # but the actual file has an intermediate name (e.g., img_0000.png)
        search_name = name
        resolved_via_mapper = False
        if reference_mapper is not None and HAS_REFERENCE_MAPPER:
            # Remove MultiMedia/ prefix if present for mapping lookup
            lookup_name = name
            if name.startswith('MultiMedia/') or name.startswith('MultiMedia\\'):
                lookup_name = name.split('/', 1)[1] if '/' in name else name.split('\\', 1)[1]

            # Check if this is a final name in the mapping
            for orig_path, ref in reference_mapper.resources.items():
                if ref.final_name == lookup_name:
                    # Found it! Use the intermediate name instead
                    search_name = ref.intermediate_name
                    resolved_via_mapper = True
                    logger.debug(f"MediaFetcher: Resolved {name} → {search_name} via reference mapper")
                    break

            # If we didn't find it as a final name, check if it's already an intermediate name
            if not resolved_via_mapper:
                for orig_path, ref in reference_mapper.resources.items():
                    if ref.intermediate_name == lookup_name:
                        # It's already an intermediate name
                        # Preserve MultiMedia/ prefix if original name had it
                        if name.startswith('MultiMedia/') or name.startswith('MultiMedia\\'):
                            search_name = name  # Keep original with prefix
                        else:
                            search_name = lookup_name  # Use without prefix
                        logger.debug(f"MediaFetcher: {name} is an intermediate name, using {search_name}")
                        break

        # If absolute path, try it directly
        if Path(search_name).is_absolute():
            candidates.append(Path(search_name))

        # Try each base path
        for base in paths:
            candidates.append(base / search_name)

            # Also try without MultiMedia prefix if present
            if search_name.startswith('MultiMedia/') or search_name.startswith('MultiMedia\\'):
                name_without_prefix = search_name.split('/', 1)[1] if '/' in search_name else search_name.split('\\', 1)[1]
                candidates.append(base / 'MultiMedia' / name_without_prefix)
                # Also try directly in base without MultiMedia subdirectory
                candidates.append(base / name_without_prefix)
            else:
                # For intermediate names (e.g., img_0000.png) that were resolved via mapper,
                # also try in MultiMedia subdirectory since ePub images are stored there
                if resolved_via_mapper or not Path(search_name).is_absolute():
                    candidates.append(base / 'MultiMedia' / search_name)

        # Try to read from each candidate
        for candidate in candidates:
            if candidate.exists():
                try:
                    data = candidate.read_bytes()
                    if len(data) > 0:
                        logger.debug(f"Media fetcher found: {name} → {candidate}")
                        return data
                    else:
                        logger.warning(f"Media file is empty: {candidate}")
                except OSError as exc:
                    logger.warning("Failed reading media %s: %s", candidate, exc)

        # If not found, log all attempted paths for debugging
        logger.warning(f"Media fetcher could not find: {name}")
        logger.debug(f"  Original name: {name}")
        logger.debug(f"  Search name after mapper: {search_name}")
        logger.debug(f"  Resolved via mapper: {resolved_via_mapper}")
        logger.debug(f"  Total candidates tried: {len(candidates)}")
        logger.debug(f"  Attempted paths:")
        for idx, candidate in enumerate(candidates, 1):
            exists_status = "EXISTS" if candidate.exists() else "NOT FOUND"
            logger.debug(f"    {idx}. {exists_status}: {candidate}")
        return None

    return _fetch