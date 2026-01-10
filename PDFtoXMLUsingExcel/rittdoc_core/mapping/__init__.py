"""
Reference Mapping Module
========================

Tracks resource transformations throughout document conversion pipelines.
Provides persistent mapping of original -> intermediate -> final resource names.
"""

from rittdoc_core.mapping.reference_mapper import (
    ReferenceMapper,
    ResourceReference,
    LinkReference,
    get_mapper,
    reset_mapper,
)

__all__ = [
    "ReferenceMapper",
    "ResourceReference",
    "LinkReference",
    "get_mapper",
    "reset_mapper",
]
