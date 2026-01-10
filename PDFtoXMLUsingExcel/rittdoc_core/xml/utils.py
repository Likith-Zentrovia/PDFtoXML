"""
XML Utility Functions
=====================

Common XML manipulation utilities for document conversion pipelines.
These functions work with lxml elements and provide consistent handling
of namespaces, content extraction, and tree manipulation.
"""

from typing import Iterator, Optional, Set, List, Any
import logging

logger = logging.getLogger(__name__)

# Try to import lxml, but allow graceful degradation
try:
    from lxml import etree
    LXML_AVAILABLE = True
except ImportError:
    LXML_AVAILABLE = False
    etree = None  # type: ignore


# Common element categories
BLOCK_ELEMENTS: Set[str] = {
    'itemizedlist', 'orderedlist', 'variablelist', 'simplelist',
    'figure', 'informalfigure', 'table', 'informaltable',
    'example', 'informalexample',
    'programlisting', 'screen', 'literallayout',
    'blockquote', 'note', 'warning', 'caution', 'important', 'tip',
    'sect1', 'sect2', 'sect3', 'sect4', 'sect5', 'section',
    'para', 'formalpara', 'simpara',
    'mediaobject', 'inlinemediaobject',
}

INLINE_ELEMENTS: Set[str] = {
    'emphasis', 'ulink', 'link', 'xref',
    'subscript', 'superscript', 'anchor',
    'phrase', 'citetitle', 'quote', 'foreignphrase',
    'firstterm', 'glossterm', 'acronym', 'abbrev',
    'trademark', 'productname', 'corpname',
    'literal', 'command', 'option', 'parameter',
    'filename', 'envar', 'prompt', 'userinput',
    'computeroutput', 'replaceable', 'markup',
    'footnote', 'footnoteref',
    'inlineequation', 'inlinemediaobject',
}


def local_name(element: Any) -> str:
    """
    Extract local name from element tag, stripping any namespace prefix.

    Args:
        element: XML element

    Returns:
        Local tag name without namespace

    Example:
        >>> elem = etree.Element("{http://docbook.org}para")
        >>> local_name(elem)
        'para'
    """
    tag = element.tag
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def qualified_tag(element: Any, tag_name: str) -> str:
    """
    Get qualified tag name matching element's namespace.

    Args:
        element: Reference element for namespace
        tag_name: Local tag name

    Returns:
        Qualified tag name with namespace (if any)
    """
    ref_tag = element.tag
    if not isinstance(ref_tag, str):
        return tag_name
    if ref_tag.startswith("{"):
        ns = ref_tag.split("}", 1)[0] + "}"
        return ns + tag_name
    return tag_name


def is_chapter_node(element: Any) -> bool:
    """
    Check if element represents a chapter-level node.

    Args:
        element: XML element to check

    Returns:
        True if element is a chapter, appendix, or preface
    """
    name = local_name(element)
    return name in ('chapter', 'appendix', 'preface', 'glossary', 'bibliography', 'index')


def is_toc_node(element: Any) -> bool:
    """
    Check if element represents a table of contents node.

    Args:
        element: XML element to check

    Returns:
        True if element is a TOC-related element
    """
    name = local_name(element)
    return name in ('toc', 'tocchap', 'tocentry', 'tocpart', 'tocback', 'tocfront')


def is_inline_only(element: Any) -> bool:
    """
    Check if an element contains only inline content (no block elements).

    Block elements include: itemizedlist, orderedlist, figure, table, sect*, etc.
    Inline elements include: emphasis, ulink, subscript, superscript, anchor, etc.

    Args:
        element: XML element to check

    Returns:
        True if element has only inline content
    """
    for child in element:
        if isinstance(child.tag, str):
            name = local_name(child)
            if name in BLOCK_ELEMENTS:
                return False
            # Recursively check children
            if not is_inline_only(child):
                return False
    return True


def extract_title_text(element: Any) -> str:
    """
    Extract title text from an element's <title> child.

    Args:
        element: Parent element containing <title>

    Returns:
        Title text or empty string if not found
    """
    title_elem = element.find('title')
    if title_elem is None:
        # Try with namespace
        for child in element:
            if local_name(child) == 'title':
                title_elem = child
                break

    if title_elem is None:
        return ""

    return ''.join(title_elem.itertext()).strip()


def has_non_media_content(element: Any) -> bool:
    """
    Check if an element has substantive content beyond media objects.

    Args:
        element: XML element to check

    Returns:
        True if element has text or non-media children
    """
    # Check direct text
    if element.text and element.text.strip():
        return True

    # Check children
    for child in element:
        name = local_name(child)
        # Skip media-related elements
        if name in ('mediaobject', 'inlinemediaobject', 'imageobject',
                    'imagedata', 'textobject', 'videoobject', 'audioobject'):
            continue
        # Has non-media child
        return True

    # Check tail text on children
    for child in element:
        if child.tail and child.tail.strip():
            return True

    return False


def iter_imagedata(element: Any) -> Iterator[Any]:
    """
    Iterate over all imagedata elements in a tree.

    Args:
        element: Root element to search

    Yields:
        imagedata elements
    """
    for img in element.iter():
        if local_name(img) == 'imagedata':
            yield img


def prune_empty_media_branch(start: Any) -> None:
    """
    Remove empty media branches from tree (figure/mediaobject with no content).

    Walks up from start element removing empty ancestors until finding
    one with other content.

    Args:
        start: Starting element to check
    """
    current = start
    while current is not None:
        parent = current.getparent()
        if parent is None:
            break

        name = local_name(current)

        # Only prune media-related elements
        if name not in ('mediaobject', 'inlinemediaobject', 'imageobject',
                        'figure', 'informalfigure'):
            break

        # Check if parent would be empty after removal
        has_other_content = False
        for sibling in parent:
            if sibling is not current:
                if isinstance(sibling.tag, str):
                    has_other_content = True
                    break

        if parent.text and parent.text.strip():
            has_other_content = True

        # Remove current if parent has other content
        parent.remove(current)

        if has_other_content:
            break

        # Continue up the tree
        current = parent


def remove_image_node(image_node: Any) -> None:
    """
    Remove an image node and its empty ancestors.

    Args:
        image_node: imagedata or similar element to remove
    """
    parent = image_node.getparent()
    if parent is not None:
        parent.remove(image_node)
        # Prune empty ancestors
        if not has_non_media_content(parent):
            prune_empty_media_branch(parent)


def create_element(tag: str, text: Optional[str] = None,
                   attrib: Optional[dict] = None,
                   nsmap: Optional[dict] = None) -> Any:
    """
    Create an XML element with optional text and attributes.

    Args:
        tag: Element tag name
        text: Optional text content
        attrib: Optional attributes dict
        nsmap: Optional namespace map

    Returns:
        New lxml Element

    Raises:
        ImportError: If lxml is not available
    """
    if not LXML_AVAILABLE:
        raise ImportError("lxml is required for create_element")
    elem = etree.Element(tag, attrib=attrib or {}, nsmap=nsmap)
    if text:
        elem.text = text
    return elem


def safe_get_text(element: Any, default: str = "") -> str:
    """
    Safely get all text content from an element.

    Args:
        element: XML element
        default: Default value if no text

    Returns:
        Concatenated text content
    """
    try:
        text = ''.join(element.itertext())
        return text.strip() if text else default
    except Exception:
        return default


def normalize_whitespace(text: str) -> str:
    """
    Normalize whitespace in text (collapse multiple spaces, trim).

    Args:
        text: Input text

    Returns:
        Normalized text
    """
    if not text:
        return ""
    return ' '.join(text.split())


def get_element_path(element: Any) -> str:
    """
    Get XPath-like path to an element for debugging.

    Args:
        element: XML element

    Returns:
        Path string like "/book/chapter[2]/para[1]"
    """
    parts = []
    current = element

    while current is not None:
        name = local_name(current)
        parent = current.getparent()

        if parent is not None:
            # Count same-named siblings
            index = 1
            for sibling in parent:
                if sibling is current:
                    break
                if local_name(sibling) == name:
                    index += 1
            parts.append(f"{name}[{index}]")
        else:
            parts.append(name)

        current = parent

    return "/" + "/".join(reversed(parts))


def find_elements_by_local_name(root: Any, name: str) -> List[Any]:
    """
    Find all elements with a given local name (ignoring namespace).

    Args:
        root: Root element to search
        name: Local name to find

    Returns:
        List of matching elements
    """
    results = []
    for elem in root.iter():
        if local_name(elem) == name:
            results.append(elem)
    return results


def copy_element_attributes(source: Any, target: Any,
                           exclude: Optional[Set[str]] = None) -> None:
    """
    Copy attributes from source to target element.

    Args:
        source: Source element
        target: Target element
        exclude: Set of attribute names to exclude
    """
    exclude = exclude or set()
    for attr, value in source.attrib.items():
        if attr not in exclude:
            target.set(attr, value)


def move_children(source: Any, target: Any) -> None:
    """
    Move all children from source to target element.

    Args:
        source: Source element (will be emptied)
        target: Target element (children appended)
    """
    for child in list(source):
        source.remove(child)
        target.append(child)


def wrap_elements(elements: List[Any], wrapper_tag: str,
                 attrib: Optional[dict] = None) -> Any:
    """
    Wrap a list of elements in a new parent element.

    Args:
        elements: List of elements to wrap
        wrapper_tag: Tag name for wrapper
        attrib: Optional attributes for wrapper

    Returns:
        Wrapper element containing the elements

    Raises:
        ImportError: If lxml is not available
    """
    if not LXML_AVAILABLE:
        raise ImportError("lxml is required for wrap_elements")
    wrapper = etree.Element(wrapper_tag, attrib=attrib or {})

    if not elements:
        return wrapper

    # Insert wrapper at position of first element
    first = elements[0]
    parent = first.getparent()
    if parent is not None:
        index = list(parent).index(first)
        parent.insert(index, wrapper)

    # Move elements into wrapper
    for elem in elements:
        if elem.getparent() is not None:
            elem.getparent().remove(elem)
        wrapper.append(elem)

    return wrapper
