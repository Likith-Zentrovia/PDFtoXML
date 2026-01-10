"""
XML Processing Utilities
========================

Common XML manipulation functions used across document conversion pipelines.
"""

from rittdoc_core.xml.utils import (
    local_name,
    qualified_tag,
    is_chapter_node,
    is_toc_node,
    is_inline_only,
    extract_title_text,
    has_non_media_content,
    iter_imagedata,
    prune_empty_media_branch,
    remove_image_node,
    create_element,
    safe_get_text,
    normalize_whitespace,
    get_element_path,
    BLOCK_ELEMENTS,
    INLINE_ELEMENTS,
)

__all__ = [
    "local_name",
    "qualified_tag",
    "is_chapter_node",
    "is_toc_node",
    "is_inline_only",
    "extract_title_text",
    "has_non_media_content",
    "iter_imagedata",
    "prune_empty_media_branch",
    "remove_image_node",
    "create_element",
    "safe_get_text",
    "normalize_whitespace",
    "get_element_path",
    "BLOCK_ELEMENTS",
    "INLINE_ELEMENTS",
]
