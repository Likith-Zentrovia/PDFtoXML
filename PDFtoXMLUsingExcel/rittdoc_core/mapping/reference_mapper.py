"""
Reference Mapping System
========================

Tracks all resource renaming (images, files) throughout the conversion pipeline
to ensure proper reference resolution and validation.

This module provides:
- Persistent mapping of original -> intermediate -> final resource names
- Reference validation across chapter XMLs
- Export/import of mapping data for debugging and validation
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import json
import logging
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class ResourceReference:
    """Tracks a single resource through all renaming stages."""

    original_path: str          # Original path in source (e.g., "OEBPS/images/fig1.png")
    original_filename: str      # Original filename only (e.g., "fig1.png")
    intermediate_name: str      # Temporary name during extraction (e.g., "img_0001.png")
    final_name: Optional[str] = None  # Final name after packaging (e.g., "Ch0001f01.jpg")

    # Context information
    resource_type: str = "image"  # "image", "link", "xhtml", "css", "font", etc.
    first_seen_in: Optional[str] = None  # Chapter/file where first referenced
    referenced_in: List[str] = field(default_factory=list)  # All chapters referencing this

    # Image-specific metadata
    is_vector: bool = False
    is_raster: bool = False
    width: Optional[int] = None
    height: Optional[int] = None
    file_size: Optional[int] = None

    # Validation
    exists_in_source: bool = True
    exists_in_output: bool = False
    all_references_updated: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'ResourceReference':
        """Create from dictionary."""
        return cls(**data)


@dataclass
class LinkReference:
    """Tracks internal document links."""

    original_href: str          # Original link (e.g., "chapter02.xhtml#section1")
    source_chapter: str         # Chapter containing the link (e.g., "ch0001")
    target_chapter: Optional[str] = None  # Target chapter (e.g., "ch0002")
    target_anchor: Optional[str] = None   # Target anchor (e.g., "section1")
    resolved: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'LinkReference':
        """Create from dictionary."""
        return cls(**data)


class ReferenceMapper:
    """
    Central reference mapping system for tracking all resource transformations
    throughout the conversion pipeline.

    Example usage:
        mapper = ReferenceMapper()

        # Register a resource
        mapper.add_resource(
            original_path="OEBPS/images/fig1.png",
            intermediate_name="img_0001.png",
            resource_type="image",
            first_seen_in="ch0001",
            is_raster=True
        )

        # Track references
        mapper.add_reference("OEBPS/images/fig1.png", "ch0002")

        # Update final name
        mapper.update_final_name("OEBPS/images/fig1.png", "Ch0001f01.jpg")

        # Export for debugging
        mapper.export_to_json(Path("mapping.json"))
    """

    def __init__(self):
        """Initialize empty mapper."""
        self.resources: Dict[str, ResourceReference] = {}  # Key: original_path
        self.links: List[LinkReference] = []
        self.chapter_map: Dict[str, str] = {}  # original_file -> chapter_id

        # Reverse lookup for efficiency
        self._intermediate_to_original: Dict[str, str] = {}

        # Statistics
        self.stats = {
            'total_images': 0,
            'vector_images': 0,
            'raster_images': 0,
            'total_links': 0,
            'broken_links': 0,
            'unreferenced_resources': 0,
        }

    def add_resource(self,
                     original_path: str,
                     intermediate_name: str,
                     resource_type: str = "image",
                     first_seen_in: Optional[str] = None,
                     **kwargs) -> ResourceReference:
        """
        Register a resource in the mapping system.

        Args:
            original_path: Original path in source document
            intermediate_name: Temporary name during extraction
            resource_type: Type of resource (image, xhtml, css, etc.)
            first_seen_in: Chapter/file where first encountered
            **kwargs: Additional metadata (is_vector, width, height, etc.)

        Returns:
            ResourceReference object
        """
        original_filename = Path(original_path).name

        ref = ResourceReference(
            original_path=original_path,
            original_filename=original_filename,
            intermediate_name=intermediate_name,
            resource_type=resource_type,
            first_seen_in=first_seen_in,
            referenced_in=[first_seen_in] if first_seen_in else [],
            **kwargs
        )

        self.resources[original_path] = ref
        self._intermediate_to_original[intermediate_name] = original_path

        if resource_type == "image":
            self.stats['total_images'] += 1
            if kwargs.get('is_vector'):
                self.stats['vector_images'] += 1
            if kwargs.get('is_raster'):
                self.stats['raster_images'] += 1

        logger.debug(f"Registered resource: {original_path} -> {intermediate_name}")
        return ref

    def update_final_name(self, original_path: str, final_name: str) -> None:
        """
        Update the final name for a resource after packaging.

        Args:
            original_path: Original path of the resource
            final_name: Final name in output package
        """
        if original_path in self.resources:
            self.resources[original_path].final_name = final_name
            logger.debug(f"Updated final name: {original_path} -> {final_name}")
        else:
            logger.warning(f"Attempted to update final name for unknown resource: {original_path}")

    def add_reference(self, original_path: str, referenced_in: str) -> None:
        """
        Record that a resource is referenced in a specific chapter.

        Args:
            original_path: Original path of the resource
            referenced_in: Chapter ID where referenced
        """
        if original_path in self.resources:
            if referenced_in not in self.resources[original_path].referenced_in:
                self.resources[original_path].referenced_in.append(referenced_in)
        else:
            logger.warning(f"Reference to unknown resource: {original_path} in {referenced_in}")

    def add_link(self,
                 original_href: str,
                 source_chapter: str,
                 target_chapter: Optional[str] = None,
                 target_anchor: Optional[str] = None) -> LinkReference:
        """
        Register an internal link.

        Args:
            original_href: Original href value
            source_chapter: Chapter containing the link
            target_chapter: Resolved target chapter (if known)
            target_anchor: Target anchor ID (if any)

        Returns:
            LinkReference object
        """
        link = LinkReference(
            original_href=original_href,
            source_chapter=source_chapter,
            target_chapter=target_chapter,
            target_anchor=target_anchor,
            resolved=target_chapter is not None
        )
        self.links.append(link)
        self.stats['total_links'] += 1

        if not link.resolved:
            self.stats['broken_links'] += 1

        return link

    def register_chapter(self, original_file: str, chapter_id: str) -> None:
        """
        Map original source file to chapter ID.

        Args:
            original_file: Original filename (e.g., "chapter02.xhtml")
            chapter_id: Output chapter ID (e.g., "ch0002")
        """
        self.chapter_map[original_file] = chapter_id
        logger.debug(f"Registered chapter mapping: {original_file} -> {chapter_id}")

    def get_final_name(self, original_path: str) -> Optional[str]:
        """Get the final name for a resource."""
        if original_path in self.resources:
            return self.resources[original_path].final_name
        return None

    def get_intermediate_name(self, original_path: str) -> Optional[str]:
        """Get the intermediate name for a resource."""
        if original_path in self.resources:
            return self.resources[original_path].intermediate_name
        return None

    def get_original_from_intermediate(self, intermediate_name: str) -> Optional[str]:
        """Get original path from intermediate name."""
        return self._intermediate_to_original.get(intermediate_name)

    def get_chapter_id(self, original_file: str) -> Optional[str]:
        """Get chapter ID for an original source file."""
        return self.chapter_map.get(original_file)

    def mark_link_resolved(self, original_href: str, source_chapter: str,
                          target_chapter: str) -> bool:
        """
        Mark a link as resolved after post-processing.

        Args:
            original_href: The original href that was tracked
            source_chapter: The source chapter where the link exists
            target_chapter: The resolved target chapter

        Returns:
            True if link was found and updated, False otherwise
        """
        for link in self.links:
            if link.original_href == original_href and link.source_chapter == source_chapter:
                if not link.resolved:
                    link.resolved = True
                    link.target_chapter = target_chapter
                    self.stats['broken_links'] = max(0, self.stats['broken_links'] - 1)
                    return True
                return True  # Already resolved
        return False

    def recalculate_link_stats(self) -> None:
        """Recalculate link statistics based on current state."""
        broken = sum(1 for link in self.links if not link.resolved)
        self.stats['broken_links'] = broken
        logger.debug(f"Recalculated link stats: {broken} broken links out of {len(self.links)} total")

    def validate(self, output_dir: Path, media_subdir: str = "MultiMedia") -> Tuple[bool, List[str]]:
        """
        Validate that all resources exist and all references are resolvable.

        Args:
            output_dir: Output directory containing the package
            media_subdir: Subdirectory for media files (default: "MultiMedia")

        Returns:
            (is_valid, list of error messages)
        """
        errors = []

        # Check that all resources have final names
        for path, ref in self.resources.items():
            if ref.final_name is None:
                errors.append(f"Resource has no final name: {path}")
            else:
                # Check if final file exists
                final_path = output_dir / media_subdir / ref.final_name
                if final_path.exists():
                    ref.exists_in_output = True
                else:
                    ref.exists_in_output = False
                    errors.append(f"Final resource not found: {final_path}")

        # Check for unreferenced resources
        for path, ref in self.resources.items():
            if not ref.referenced_in:
                self.stats['unreferenced_resources'] += 1
                logger.warning(f"Unreferenced resource: {path}")

        # Check links
        for link in self.links:
            if not link.resolved:
                errors.append(f"Unresolved link: {link.original_href} in {link.source_chapter}")

        is_valid = len(errors) == 0
        return is_valid, errors

    def export_to_json(self, output_path: Path) -> None:
        """Export complete mapping to JSON for debugging and validation."""
        data = {
            'metadata': {
                'created': datetime.now().isoformat(),
                'total_resources': len(self.resources),
                'total_links': len(self.links),
            },
            'resources': {path: ref.to_dict() for path, ref in self.resources.items()},
            'links': [link.to_dict() for link in self.links],
            'chapter_map': self.chapter_map,
            'statistics': self.stats,
        }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(f"Exported reference mapping to {output_path}")

    def import_from_json(self, input_path: Path) -> None:
        """Import mapping from JSON."""
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self.resources = {
            path: ResourceReference.from_dict(ref_data)
            for path, ref_data in data['resources'].items()
        }
        self.links = [LinkReference.from_dict(link_data) for link_data in data['links']]
        self.chapter_map = data['chapter_map']
        self.stats = data['statistics']

        # Rebuild reverse lookup
        self._intermediate_to_original = {
            ref.intermediate_name: path
            for path, ref in self.resources.items()
        }

        logger.info(f"Imported reference mapping from {input_path}")

    def export_mapping(self) -> dict:
        """Export mapping data as dictionary (for in-memory transfer)."""
        return {
            'resources': {path: ref.to_dict() for path, ref in self.resources.items()},
            'links': [link.to_dict() for link in self.links],
            'chapter_map': self.chapter_map,
            'statistics': self.stats,
        }

    def import_mapping(self, data: dict) -> None:
        """Import mapping from dictionary."""
        self.resources = {
            path: ResourceReference.from_dict(ref_data)
            for path, ref_data in data['resources'].items()
        }
        self.links = [LinkReference.from_dict(link_data) for link_data in data['links']]
        self.chapter_map = data['chapter_map']
        self.stats = data['statistics']

        # Rebuild reverse lookup
        self._intermediate_to_original = {
            ref.intermediate_name: path
            for path, ref in self.resources.items()
        }

    def get_statistics(self) -> Dict:
        """Get current statistics."""
        return self.stats.copy()

    def generate_report(self) -> str:
        """Generate a human-readable report of the mapping state."""
        lines = [
            "=" * 80,
            "REFERENCE MAPPING REPORT",
            "=" * 80,
            f"Total Resources: {len(self.resources)}",
            f"  - Images: {self.stats['total_images']}",
            f"    - Vector: {self.stats['vector_images']}",
            f"    - Raster: {self.stats['raster_images']}",
            f"Total Links: {self.stats['total_links']}",
            f"  - Broken: {self.stats['broken_links']}",
            f"Unreferenced Resources: {self.stats['unreferenced_resources']}",
            "",
            "Chapter Mappings:",
        ]

        for original, chapter_id in sorted(self.chapter_map.items()):
            lines.append(f"  {original} -> {chapter_id}")

        lines.extend([
            "",
            "Resource Mappings (first 10):",
        ])

        for i, (path, ref) in enumerate(list(self.resources.items())[:10]):
            lines.append(f"  {path}")
            lines.append(f"    -> intermediate: {ref.intermediate_name}")
            lines.append(f"    -> final: {ref.final_name or 'NOT SET'}")
            lines.append(f"    -> referenced in: {', '.join(ref.referenced_in) or 'NONE'}")

        if len(self.resources) > 10:
            lines.append(f"  ... and {len(self.resources) - 10} more")

        lines.append("=" * 80)
        return "\n".join(lines)

    def clear(self) -> None:
        """Clear all mappings (reset to empty state)."""
        self.resources.clear()
        self.links.clear()
        self.chapter_map.clear()
        self._intermediate_to_original.clear()
        self.stats = {
            'total_images': 0,
            'vector_images': 0,
            'raster_images': 0,
            'total_links': 0,
            'broken_links': 0,
            'unreferenced_resources': 0,
        }


# Global mapper instance for use across pipeline
_global_mapper: Optional[ReferenceMapper] = None


def get_mapper() -> ReferenceMapper:
    """Get or create the global reference mapper instance."""
    global _global_mapper
    if _global_mapper is None:
        _global_mapper = ReferenceMapper()
    return _global_mapper


def reset_mapper() -> None:
    """Reset the global mapper (useful for testing or new conversions)."""
    global _global_mapper
    _global_mapper = ReferenceMapper()
