#!/usr/bin/env python3
"""
Web-based UI Editor Server for RittDoc Pipeline
Provides a professional side-by-side PDF and XML/HTML editing interface
"""

import argparse
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import webbrowser
import zipfile
from pathlib import Path
from threading import Timer
from typing import Dict, List, Optional


def _ensure_dependencies():
    """Check and install missing dependencies for the editor."""
    required = {
        'flask': 'flask',
        'flask_cors': 'flask-cors',
        'PIL': 'Pillow',
    }
    missing = []
    for module, package in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)

    if missing:
        print(f"Installing missing dependencies: {', '.join(missing)}")
        subprocess.check_call([
            sys.executable, '-m', 'pip', 'install', '--quiet', *missing
        ])
        print("Dependencies installed successfully.")


_ensure_dependencies()

from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
from lxml import etree
from PIL import Image

# Import pipeline components
from rittdoc_compliance_pipeline import RittDocCompliancePipeline

app = Flask(__name__, static_folder='editor_ui', static_url_path='')
CORS(app)

# Global state
EDITOR_STATE = {
    'pdf_path': None,
    'xml_path': None,
    'multimedia_folder': None,
    'working_dir': None,
    'original_package': None,
    'dtd_path': None,
    'job_id': None,
    'webhook_url': None,
    'api_base_url': None,
}


class XMLToHTMLRenderer:
    """Converts RittDoc XML to properly formatted HTML for viewing/editing"""

    # Class-level fontspec lookup (populated during render)
    _fontspec_lookup = {}

    @staticmethod
    def _safe_tag_name(elem) -> str:
        """Safely extract tag name from element, handling Cython/lxml compatibility."""
        if elem is None:
            return 'unknown'
        raw_tag = elem.tag
        if raw_tag is None:
            return 'unknown'
        # Convert to string in case it's a Cython method
        tag_str = str(raw_tag) if not isinstance(raw_tag, str) else raw_tag
        # Remove namespace prefix if present
        if '}' in tag_str:
            return tag_str.split('}')[-1]
        return tag_str

    @staticmethod
    def render(xml_content: str) -> str:
        """Render XML to HTML with proper formatting for tables, images, etc."""
        try:
            root = etree.fromstring(xml_content.encode('utf-8'))

            # Build fontspec lookup from document
            XMLToHTMLRenderer._fontspec_lookup = XMLToHTMLRenderer._build_fontspec_lookup(root)

            return XMLToHTMLRenderer._element_to_html(root)
        except Exception as e:
            return f"<div class='error'>Error rendering XML: {e}</div>"

    @staticmethod
    def _build_fontspec_lookup(root) -> dict:
        """Build a lookup dictionary from fontspec elements.

        Returns:
            dict: font_id -> {family, size, color, face, bold, italic}
        """
        lookup = {}

        # Find all fontspec elements (they're typically at document root level)
        for fontspec in root.iter('fontspec'):
            font_id = fontspec.get('id')
            if font_id:
                family = fontspec.get('family', '') or ''
                face = fontspec.get('face', '') or ''
                # Use face if family is empty, or family otherwise
                font_name = str(family or face or '')
                
                # Safely check for bold/italic in font name
                font_name_lower = font_name.lower() if isinstance(font_name, str) else ''

                lookup[font_id] = {
                    'family': font_name,
                    'size': fontspec.get('size', '') or '',
                    'color': fontspec.get('color', '') or '',
                    'face': face,
                    # Detect bold/italic from font name
                    'bold': 'bold' in font_name_lower if font_name_lower else False,
                    'italic': ('italic' in font_name_lower or 'oblique' in font_name_lower) if font_name_lower else False,
                }

        return lookup
    
    @staticmethod
    def _extract_page_number(elem) -> str:
        """Extract page number from element attributes or ID"""
        import re

        # Check explicit page attribute
        page_num = elem.get('page', '')
        if page_num:
            return page_num

        # Try to extract from ID (e.g., "p98_table1" -> "98")
        elem_id = elem.get('id', '')
        if elem_id:
            match = re.search(r'p(\d+)_', elem_id)
            if match:
                return match.group(1)

        return ''

    @staticmethod
    def _extract_block_info(elem) -> dict:
        """Extract reading block, reading order, and col_id from element attributes"""
        return {
            'reading_block': elem.get('reading_block', elem.get('reading-block', '')),
            'reading_order': elem.get('reading_order', elem.get('reading-order', '')),
            'col_id': elem.get('col_id', elem.get('col-id', ''))
        }

    @staticmethod
    def _build_data_attrs(page_num: str, block_info: dict) -> str:
        """Build data attribute string for HTML elements"""
        attrs = []
        if page_num:
            attrs.append(f'data-page="{page_num}"')
        if block_info.get('reading_block'):
            attrs.append(f'data-reading-block="{block_info["reading_block"]}"')
        if block_info.get('reading_order'):
            attrs.append(f'data-reading-order="{block_info["reading_order"]}"')
        if block_info.get('col_id'):
            attrs.append(f'data-col-id="{block_info["col_id"]}"')
        return ' ' + ' '.join(attrs) if attrs else ''
    
    @staticmethod
    def _extract_font_style(elem) -> str:
        """Extract font styling from element attributes and return as inline CSS.

        This method resolves font IDs against the fontspec lookup to get actual
        font family, size, color, and style information.
        """
        styles = []

        # Get font attribute - could be an ID reference or actual font name
        font_attr = elem.get('font') or elem.get('font-family') or elem.get('fontfamily')

        # Try to resolve font ID against fontspec lookup
        fontspec_info = None
        if font_attr and font_attr in XMLToHTMLRenderer._fontspec_lookup:
            fontspec_info = XMLToHTMLRenderer._fontspec_lookup[font_attr]

        # Font family - from fontspec lookup or direct attribute
        if fontspec_info and fontspec_info.get('family'):
            font_family = fontspec_info['family']
            # Clean up font family name for CSS (remove style suffixes for cleaner display)
            base_family = font_family.replace('-Bold', '').replace('-Italic', '').replace('-BoldItalic', '')
            base_family = base_family.replace('Bold', '').replace('Italic', '').strip('-').strip()
            if base_family:
                styles.append(f"font-family: '{base_family}'")
        elif font_attr and not font_attr.isdigit():
            # Direct font name (not an ID)
            styles.append(f"font-family: '{font_attr}'")

        # Font size - from fontspec lookup or direct attribute
        font_size = None
        if fontspec_info and fontspec_info.get('size'):
            font_size = fontspec_info['size']
        else:
            font_size = elem.get('font-size') or elem.get('fontsize') or elem.get('size')

        if font_size:
            # Handle both point sizes and pixel sizes
            if str(font_size).replace('.', '').isdigit():
                styles.append(f"font-size: {font_size}pt")
            else:
                styles.append(f"font-size: {font_size}")

        # Font weight (bold) - from fontspec lookup, direct attribute, or font name
        is_bold = False
        if fontspec_info and fontspec_info.get('bold'):
            is_bold = True
        elif elem.get('bold') == 'true' or elem.get('weight') == 'bold':
            is_bold = True
        elif fontspec_info and fontspec_info.get('family'):
            family = str(fontspec_info['family'] or '')
            is_bold = 'bold' in family.lower() if family else False

        if is_bold:
            styles.append("font-weight: bold")

        # Font style (italic) - from fontspec lookup, direct attribute, or font name
        is_italic = False
        if fontspec_info and fontspec_info.get('italic'):
            is_italic = True
        elif elem.get('italic') == 'true' or elem.get('style') == 'italic':
            is_italic = True
        elif fontspec_info and fontspec_info.get('family'):
            family = str(fontspec_info['family'] or '')
            family_lower = family.lower() if family else ''
            is_italic = ('italic' in family_lower or 'oblique' in family_lower) if family_lower else False

        if is_italic:
            styles.append("font-style: italic")

        # Color - from fontspec lookup or direct attribute
        color = None
        if fontspec_info and fontspec_info.get('color'):
            color = fontspec_info['color']
        else:
            color = elem.get('color')

        if color and color != '#000000' and color != 'black':
            # Only add color if it's not default black
            styles.append(f"color: {color}")

        # Role-based styling (emphasis role attribute)
        role = elem.get('role')
        if role:
            role_str = str(role or '')
            role_lower = role_str.lower() if role_str else ''
            if role_lower and 'bold' in role_lower and not is_bold:
                styles.append("font-weight: bold")
            if role_lower and 'italic' in role_lower and not is_italic:
                styles.append("font-style: italic")

        return '; '.join(styles) if styles else ''
    
    @staticmethod
    def _element_to_html(elem, level=0) -> str:
        """Recursively convert XML element to HTML"""
        tag = XMLToHTMLRenderer._safe_tag_name(elem)
        
        # Handle specific DocBook/RittDoc elements
        if tag == 'chapter':
            return XMLToHTMLRenderer._render_chapter(elem, level)
        elif tag == 'section' or tag == 'sect1' or tag == 'sect2' or tag == 'sect3' or tag == 'sect4' or tag == 'sect5':
            return XMLToHTMLRenderer._render_section(elem, level)
        elif tag == 'title':
            return XMLToHTMLRenderer._render_title(elem, level)
        elif tag == 'para' or tag == 'p':
            return XMLToHTMLRenderer._render_para(elem)
        elif tag == 'table':
            return XMLToHTMLRenderer._render_table(elem)
        elif tag == 'figure' or tag == 'mediaobject' or tag == 'imageobject':
            return XMLToHTMLRenderer._render_figure(elem)
        elif tag == 'media':
            return XMLToHTMLRenderer._render_media(elem)
        elif tag == 'itemizedlist' or tag == 'orderedlist':
            return XMLToHTMLRenderer._render_list(elem, tag)
        elif tag == 'listitem':
            return XMLToHTMLRenderer._render_listitem(elem)
        elif tag == 'emphasis':
            return XMLToHTMLRenderer._render_emphasis(elem)
        elif tag == 'superscript':
            return XMLToHTMLRenderer._render_superscript(elem)
        elif tag == 'subscript':
            return XMLToHTMLRenderer._render_subscript(elem)
        elif tag == 'link':
            return XMLToHTMLRenderer._render_link(elem)
        elif tag == 'programlisting' or tag == 'code':
            return f"<pre><code>{elem.text or ''}</code></pre>"
        elif tag in ['book', 'article', 'bookinfo', 'info', 'part']:
            # Container elements
            html = '<div class="' + tag + '">'
            for child in elem:
                html += XMLToHTMLRenderer._element_to_html(child, level)
            html += '</div>'
            return html
        else:
            # Generic element
            style_attr = XMLToHTMLRenderer._extract_font_style(elem)
            style_html = f' style="{style_attr}"' if style_attr else ''
            html = f'<div class="xml-{tag}" data-tag="{tag}"{style_html}>'
            if elem.text and elem.text.strip():
                html += elem.text
            for child in elem:
                html += XMLToHTMLRenderer._element_to_html(child, level + 1)
                if child.tail and child.tail.strip():
                    html += child.tail
            html += '</div>'
            return html
    
    @staticmethod
    def _render_chapter(elem, level):
        chapter_id = elem.get('id', '')
        page_num = XMLToHTMLRenderer._extract_page_number(elem)
        block_info = XMLToHTMLRenderer._extract_block_info(elem)
        data_attrs = f'data-chapter-id="{chapter_id}"'
        if page_num:
            data_attrs += f' data-page="{page_num}"'
        if block_info.get('reading_block'):
            data_attrs += f' data-reading-block="{block_info["reading_block"]}"'
        if block_info.get('reading_order'):
            data_attrs += f' data-reading-order="{block_info["reading_order"]}"'
        if block_info.get('col_id'):
            data_attrs += f' data-col-id="{block_info["col_id"]}"'
        html = f'<div class="chapter content-block" {data_attrs}>'
        for child in elem:
            html += XMLToHTMLRenderer._element_to_html(child, level + 1)
        html += '</div>'
        return html

    @staticmethod
    def _render_section(elem, level):
        section_id = elem.get('id', '')
        page_num = XMLToHTMLRenderer._extract_page_number(elem)
        block_info = XMLToHTMLRenderer._extract_block_info(elem)
        data_attrs = f'data-section-id="{section_id}"'
        if page_num:
            data_attrs += f' data-page="{page_num}"'
        if block_info.get('reading_block'):
            data_attrs += f' data-reading-block="{block_info["reading_block"]}"'
        if block_info.get('reading_order'):
            data_attrs += f' data-reading-order="{block_info["reading_order"]}"'
        if block_info.get('col_id'):
            data_attrs += f' data-col-id="{block_info["col_id"]}"'
        html = f'<div class="section section-level-{level} content-block" {data_attrs}>'
        for child in elem:
            html += XMLToHTMLRenderer._element_to_html(child, level + 1)
        html += '</div>'
        return html
    
    @staticmethod
    def _render_title(elem, level):
        heading_level = min(level + 1, 6)
        style_attr = XMLToHTMLRenderer._extract_font_style(elem)
        style_html = f' style="{style_attr}"' if style_attr else ''
        html = f'<h{heading_level}{style_html}>'
        if elem.text:
            html += elem.text
        for child in elem:
            html += XMLToHTMLRenderer._element_to_html(child, 0)
            if child.tail:
                html += child.tail
        html += f'</h{heading_level}>'
        return html
    
    @staticmethod
    def _render_para(elem):
        style_attr = XMLToHTMLRenderer._extract_font_style(elem)
        style_html = f' style="{style_attr}"' if style_attr else ''

        # Add page number and block info
        page_num = XMLToHTMLRenderer._extract_page_number(elem)
        block_info = XMLToHTMLRenderer._extract_block_info(elem)
        data_attrs = XMLToHTMLRenderer._build_data_attrs(page_num, block_info)

        html = f'<p class="content-block"{style_html}{data_attrs}>'
        if elem.text:
            html += elem.text
        for child in elem:
            html += XMLToHTMLRenderer._element_to_html(child, 0)
            if child.tail:
                html += child.tail
        html += '</p>'
        return html
    
    @staticmethod
    def _render_emphasis(elem):
        """Render emphasis with font styling"""
        style_attr = XMLToHTMLRenderer._extract_font_style(elem)
        role = elem.get('role', '') or ''
        role_str = str(role) if role else ''
        lower = role_str.lower() if role_str else ''

        # Determine which styles to apply
        is_bold = ('bold' in lower or 'strong' in lower) if lower else False
        is_italic = 'italic' in lower if lower else False

        style_html = f' style="{style_attr}"' if style_attr else ''

        # Build content first
        content = ''
        if elem.text:
            content += elem.text
        for child in elem:
            content += XMLToHTMLRenderer._element_to_html(child, 0)
            if child.tail:
                content += child.tail

        # Apply tags - nest if both bold and italic
        if is_bold and is_italic:
            # Both bold and italic - use nested tags
            html = f'<strong{style_html}><em>{content}</em></strong>'
        elif is_bold:
            html = f'<strong{style_html}>{content}</strong>'
        elif is_italic:
            html = f'<em{style_html}>{content}</em>'
        else:
            html = f'<span{style_html}>{content}</span>'

        return html

    @staticmethod
    def _render_superscript(elem):
        """Render superscript element as <sup> HTML tag"""
        html = '<sup>'
        if elem.text:
            html += elem.text
        for child in elem:
            html += XMLToHTMLRenderer._element_to_html(child, 0)
            if child.tail:
                html += child.tail
        html += '</sup>'
        return html

    @staticmethod
    def _render_subscript(elem):
        """Render subscript element as <sub> HTML tag"""
        html = '<sub>'
        if elem.text:
            html += elem.text
        for child in elem:
            html += XMLToHTMLRenderer._element_to_html(child, 0)
            if child.tail:
                html += child.tail
        html += '</sub>'
        return html
    
    @staticmethod
    def _render_table(elem):
        """Render table with proper structure and styling"""
        # Get table caption if present
        # Check multiple sources in order of priority:
        # 1. <title> child element (DocBook standard)
        # 2. <caption> child element
        # 3. title attribute on table element
        caption_text = ''

        # Check for <title> child element (DocBook standard - used by Multipage_Image_Extractor)
        title_elem = elem.find('.//{*}title') or elem.find('.//title')
        if title_elem is not None:
            caption_text = ''.join(title_elem.itertext()).strip()

        # Check for <caption> child element
        if not caption_text:
            caption_elem = elem.find('.//{*}caption') or elem.find('.//caption')
            if caption_elem is not None:
                caption_text = ''.join(caption_elem.itertext()).strip()

        # Fallback to title attribute
        if not caption_text:
            caption_text = elem.get('title', '')

        # Extract page number and block info
        page_num = XMLToHTMLRenderer._extract_page_number(elem)
        block_info = XMLToHTMLRenderer._extract_block_info(elem)
        data_attrs = XMLToHTMLRenderer._build_data_attrs(page_num, block_info)

        # Add table ID if present
        table_id = elem.get('id', '')
        id_attr = f' id="{table_id}"' if table_id else ''

        html = f'<table class="docbook-table content-block" border="1"{id_attr}{data_attrs}>'
        
        # Add caption if present
        if caption_text:
            html += f'<caption>{caption_text}</caption>'
        
        # Handle tgroup (DocBook table structure)
        tgroup = elem.find('.//{*}tgroup') or elem.find('.//tgroup')
        if tgroup is not None:
            # Get thead
            thead = tgroup.find('.//{*}thead') or tgroup.find('.//thead')
            if thead is not None:
                html += '<thead>'
                for row in thead.findall('.//{*}row') or thead.findall('.//row'):
                    html += '<tr>'
                    for entry in row.findall('.//{*}entry') or row.findall('.//entry'):
                        style_attr = XMLToHTMLRenderer._extract_font_style(entry)
                        style_html = f' style="{style_attr}"' if style_attr else ''
                        # Handle colspan/rowspan
                        colspan = entry.get('colspan', entry.get('namest'))
                        rowspan = entry.get('rowspan', entry.get('morerows'))
                        attrs = []
                        if colspan:
                            attrs.append(f'colspan="{colspan}"')
                        if rowspan:
                            attrs.append(f'rowspan="{rowspan}"')
                        attrs_html = ' ' + ' '.join(attrs) if attrs else ''
                        
                        html += f'<th{attrs_html}{style_html}>'
                        if entry.text:
                            html += entry.text
                        for child in entry:
                            html += XMLToHTMLRenderer._element_to_html(child, 0)
                            if child.tail:
                                html += child.tail
                        html += '</th>'
                    html += '</tr>'
                html += '</thead>'
            
            # Get tbody
            tbody = tgroup.find('.//{*}tbody') or tgroup.find('.//tbody')
            if tbody is not None:
                html += '<tbody>'
                for row in tbody.findall('.//{*}row') or tbody.findall('.//row'):
                    html += '<tr>'
                    for entry in row.findall('.//{*}entry') or row.findall('.//entry'):
                        style_attr = XMLToHTMLRenderer._extract_font_style(entry)
                        style_html = f' style="{style_attr}"' if style_attr else ''
                        # Handle colspan/rowspan
                        colspan = entry.get('colspan', entry.get('namest'))
                        rowspan = entry.get('rowspan', entry.get('morerows'))
                        attrs = []
                        if colspan:
                            attrs.append(f'colspan="{colspan}"')
                        if rowspan:
                            attrs.append(f'rowspan="{rowspan}"')
                        attrs_html = ' ' + ' '.join(attrs) if attrs else ''
                        
                        html += f'<td{attrs_html}{style_html}>'
                        if entry.text:
                            html += entry.text
                        for child in entry:
                            html += XMLToHTMLRenderer._element_to_html(child, 0)
                            if child.tail:
                                html += child.tail
                        html += '</td>'
                    html += '</tr>'
                html += '</tbody>'
        else:
            # Check for <rows> container (custom structure from PDF extraction)
            rows_container = elem.find('.//{*}rows') or elem.find('.//rows')
            # Also check for direct <row> children without a <rows> container
            direct_rows = elem.findall('.//{*}row') or elem.findall('.//row') if rows_container is None else []

            if rows_container is not None or direct_rows:
                rows_to_process = (rows_container.findall('.//{*}row') or rows_container.findall('.//row')) if rows_container is not None else direct_rows
                html += '<tbody>'
                for row in rows_to_process:
                    html += '<tr>'
                    # Get all cells in this row
                    cells = row.findall('.//{*}cell') or row.findall('.//cell')
                    for cell in cells:
                        # Extract cell attributes
                        colspan = cell.get('colspan', '')
                        rowspan = cell.get('rowspan', '')
                        attrs = []
                        if colspan:
                            attrs.append(f'colspan="{colspan}"')
                        if rowspan:
                            attrs.append(f'rowspan="{rowspan}"')
                        attrs_html = ' ' + ' '.join(attrs) if attrs else ''

                        # Render cell content
                        cell_content = ''
                        if cell.text:
                            cell_content += cell.text

                        # Process chunks within the cell
                        chunks = cell.findall('.//{*}chunk') or cell.findall('.//chunk')
                        for chunk in chunks:
                            # Extract chunk styling
                            chunk_style = XMLToHTMLRenderer._extract_font_style(chunk)
                            if chunk_style:
                                cell_content += f'<span style="{chunk_style}">{chunk.text or ""}</span>'
                            else:
                                cell_content += chunk.text or ""
                            if chunk.tail:
                                cell_content += chunk.tail

                        # Also handle other child elements
                        for child in cell:
                            child_tag = XMLToHTMLRenderer._safe_tag_name(child)
                            if child_tag != 'chunk':  # Already processed chunks
                                cell_content += XMLToHTMLRenderer._element_to_html(child, 0)

                        # Use th for first row (assumed header), td for others
                        row_index = row.get('index', '999')
                        cell_tag = 'th' if row_index == '0' else 'td'

                        html += f'<{cell_tag}{attrs_html}>{cell_content}</{cell_tag}>'
                    html += '</tr>'
                html += '</tbody>'
            else:
                # Handle simple HTML-style table (no tgroup or rows)
                for child in elem:
                    child_tag = XMLToHTMLRenderer._safe_tag_name(child)
                    if child_tag == 'thead':
                        html += '<thead>'
                        for row in child.findall('.//{*}tr') or child.findall('.//tr'):
                            html += '<tr>'
                            for cell in row:
                                cell_tag = XMLToHTMLRenderer._safe_tag_name(cell)
                                if cell_tag in ['th', 'td']:
                                    style_attr = XMLToHTMLRenderer._extract_font_style(cell)
                                    style_html = f' style="{style_attr}"' if style_attr else ''
                                    html += f'<th{style_html}>{cell.text or ""}</th>'
                            html += '</tr>'
                        html += '</thead>'
                    elif child_tag == 'tbody':
                        html += '<tbody>'
                        for row in child.findall('.//{*}tr') or child.findall('.//tr'):
                            html += '<tr>'
                            for cell in row:
                                cell_tag = XMLToHTMLRenderer._safe_tag_name(cell)
                                if cell_tag in ['th', 'td']:
                                    style_attr = XMLToHTMLRenderer._extract_font_style(cell)
                                    style_html = f' style="{style_attr}"' if style_attr else ''
                                    html += f'<td{style_html}>{cell.text or ""}</td>'
                            html += '</tr>'
                        html += '</tbody>'
        
        html += '</table>'
        return html
    
    @staticmethod
    def _render_figure(elem):
        """Render figure/image with proper path resolution"""
        # Find imagedata or graphic
        imagedata = elem.find('.//{*}imagedata') or elem.find('.//imagedata')
        if imagedata is None:
            imagedata = elem.find('.//{*}graphic') or elem.find('.//graphic')
        
        if imagedata is not None:
            fileref = imagedata.get('fileref', '')
            if not fileref:
                fileref = imagedata.get('href', '')
            
            # Clean up fileref path - remove any leading directory separators
            fileref = fileref.lstrip('./')
            
            # Get image width/height if specified
            width = imagedata.get('width', '')
            height = imagedata.get('height', '')
            style_parts = []
            if width:
                style_parts.append(f'max-width: {width}')
            if height:
                style_parts.append(f'max-height: {height}')
            style_attr = '; '.join(style_parts) if style_parts else ''
            style_html = f' style="{style_attr}"' if style_attr else ''

            # Add page number and block info
            page_num = XMLToHTMLRenderer._extract_page_number(elem)
            block_info = XMLToHTMLRenderer._extract_block_info(elem)
            data_attrs = XMLToHTMLRenderer._build_data_attrs(page_num, block_info)

            # Add figure ID if present
            fig_id = elem.get('id', '')
            id_attr = f' id="{fig_id}"' if fig_id else ''

            html = f'<figure class="docbook-figure content-block"{id_attr}{data_attrs}><img src="/api/media/{fileref}" alt="Image" class="docbook-image" data-fileref="{fileref}"{style_html} onerror="this.onerror=null; this.src=\'/api/placeholder-image\'; this.alt=\'Image not found: {fileref}\';"/>'
            
            # Add caption if present
            caption = elem.find('.//{*}caption') or elem.find('.//caption')
            if caption is not None:
                caption_text = caption.text or ''
                for child in caption:
                    if child.text:
                        caption_text += child.text
                    if child.tail:
                        caption_text += child.tail
                if caption_text.strip():
                    html += f'<figcaption>{caption_text}</figcaption>'
            
            # Also check for title element
            if not caption:
                title = elem.find('.//{*}title') or elem.find('.//title')
                if title is not None and title.text:
                    html += f'<figcaption>{title.text}</figcaption>'
            
            html += '</figure>'
            return html
        
        return '<div class="figure-placeholder">[Figure without image reference]</div>'
    
    @staticmethod
    def _render_media(elem):
        """Render custom media element with image and caption"""
        media_id = elem.get('id', '')
        media_type = elem.get('type', '')
        file_attr = elem.get('file', '')
        alt_text = elem.get('alt', 'Image')
        title_text = elem.get('title', '')

        if not file_attr:
            return '<div class="media-placeholder">[Media element without file reference]</div>'

        # Clean up file path - remove any leading directory separators
        file_attr = file_attr.lstrip('./')

        # Get page number and block info
        page_num = XMLToHTMLRenderer._extract_page_number(elem)
        block_info = XMLToHTMLRenderer._extract_block_info(elem)
        data_attrs = XMLToHTMLRenderer._build_data_attrs(page_num, block_info)

        # Get positioning attributes if present
        x1 = elem.get('x1', '')
        y1 = elem.get('y1', '')
        x2 = elem.get('x2', '')
        y2 = elem.get('y2', '')

        # Build the figure with image
        html = f'<figure class="media-figure content-block" data-media-id="{media_id}" data-media-type="{media_type}"{data_attrs}>'
        
        # Build image tag
        img_html = f'<img src="/api/media/{file_attr}" alt="{alt_text}" class="media-image" data-fileref="{file_attr}"'
        
        # Add title attribute if present
        if title_text:
            img_html += f' title="{title_text}"'
        
        # Add positioning data attributes
        if x1 and y1 and x2 and y2:
            img_html += f' data-x1="{x1}" data-y1="{y1}" data-x2="{x2}" data-y2="{y2}"'
        
        # Add error handler for missing images
        img_html += f' onerror="this.onerror=null; this.src=\'/api/placeholder-image\'; this.alt=\'Image not found: {file_attr}\';"'
        img_html += ' />'
        
        html += img_html
        
        # Add caption if present
        caption = elem.find('.//{*}caption')
        if caption is None:
            caption = elem.find('.//caption')
        if caption is not None:
            caption_html = '<figcaption class="media-caption">'
            if caption.text:
                caption_html += caption.text
            for child in caption:
                caption_html += XMLToHTMLRenderer._element_to_html(child, 0)
                if child.tail:
                    caption_html += child.tail
            caption_html += '</figcaption>'
            html += caption_html
        
        html += '</figure>'
        return html
    
    @staticmethod
    def _render_list(elem, tag):
        list_tag = 'ul' if tag == 'itemizedlist' else 'ol'
        html = f'<{list_tag}>'
        for child in elem:
            html += XMLToHTMLRenderer._element_to_html(child, 0)
        html += f'</{list_tag}>'
        return html
    
    @staticmethod
    def _render_listitem(elem):
        html = '<li>'
        for child in elem:
            html += XMLToHTMLRenderer._element_to_html(child, 0)
        html += '</li>'
        return html
    
    @staticmethod
    def _render_link(elem):
        href = elem.get('href', '#')
        return f'<a href="{href}">{elem.text or ""}</a>'


# API Routes

@app.route('/')
def index():
    """Serve the main editor UI"""
    return send_from_directory('editor_ui', 'index.html')


@app.route('/api/init', methods=['GET'])
def api_init():
    """Initialize editor with current state"""
    if not EDITOR_STATE['xml_path']:
        return jsonify({'error': 'No XML file loaded'}), 400
    
    try:
        # Read XML content
        with open(EDITOR_STATE['xml_path'], 'r', encoding='utf-8') as f:
            xml_content = f.read()
        
        # Render to HTML
        html_content = XMLToHTMLRenderer.render(xml_content)
        
        # Get PDF info
        pdf_info = {
            'path': str(EDITOR_STATE['pdf_path']),
            'name': EDITOR_STATE['pdf_path'].name if EDITOR_STATE['pdf_path'] else None,
        }
        
        return jsonify({
            'xml': xml_content,
            'html': html_content,
            'pdf': pdf_info,
            'multimedia_folder': str(EDITOR_STATE['multimedia_folder']) if EDITOR_STATE['multimedia_folder'] else None,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/pdf', methods=['GET'])
def api_pdf():
    """Serve the PDF file"""
    if not EDITOR_STATE['pdf_path'] or not EDITOR_STATE['pdf_path'].exists():
        return jsonify({'error': 'PDF not found'}), 404
    
    return send_file(EDITOR_STATE['pdf_path'], mimetype='application/pdf')


@app.route('/api/media/<path:filename>', methods=['GET'])
def api_media(filename):
    """Serve media files from multimedia folder"""
    if not EDITOR_STATE['multimedia_folder']:
        return jsonify({'error': 'Multimedia folder not set'}), 400

    multimedia_folder = EDITOR_STATE['multimedia_folder']

    # Strip common path prefixes that might be in the fileref
    clean_filename = filename
    for prefix in ['MultiMedia/', 'Multimedia/', 'multimedia/', '_MultiMedia/', 'SharedImages/', 'Decorative/']:
        if clean_filename.startswith(prefix):
            clean_filename = clean_filename[len(prefix):]

    # Also get just the base filename for fallback search
    base_filename = Path(clean_filename).name

    # Build list of candidate paths to check
    candidates = [
        multimedia_folder / filename,           # Original path as-is
        multimedia_folder / clean_filename,     # With prefixes stripped
        multimedia_folder / base_filename,      # Just the filename
        multimedia_folder / 'SharedImages' / filename,
        multimedia_folder / 'SharedImages' / clean_filename,
        multimedia_folder / 'SharedImages' / base_filename,
        multimedia_folder / 'Decorative' / filename,
        multimedia_folder / 'Decorative' / clean_filename,
        multimedia_folder / 'Decorative' / base_filename,
    ]

    # Try each candidate path
    for media_path in candidates:
        if media_path.exists() and media_path.is_file():
            return send_file(media_path)

    # Last resort: recursive search for the base filename
    for found_path in multimedia_folder.rglob(base_filename):
        if found_path.is_file():
            return send_file(found_path)

    return jsonify({'error': f'Media file not found: {filename}',
                    'searched_in': str(multimedia_folder),
                    'tried_names': [filename, clean_filename, base_filename]}), 404


@app.route('/api/save', methods=['POST'])
def api_save():
    """Save edited XML and automatically trigger full finalization.

    The finalization includes:
    - RittDoc XML generation
    - Chapter splitting
    - Packaging
    - Validation
    - Verification/fixing
    - DOCX generation
    """
    try:
        data = request.json
        content_type = data.get('type', 'xml')
        content = data.get('content', '')

        if content_type == 'html':
            # Convert HTML back to XML
            try:
                xml_content = html_to_xml(content)
                content = xml_content
                content_type = 'xml'
            except Exception as e:
                return jsonify({'error': f'Error converting HTML to XML: {str(e)}'}), 400

        # Validate XML
        try:
            etree.fromstring(content.encode('utf-8'))
        except etree.XMLSyntaxError as e:
            return jsonify({'error': f'Invalid XML: {str(e)}'}), 400

        # Save to XML file
        with open(EDITOR_STATE['xml_path'], 'w', encoding='utf-8') as f:
            f.write(content)

        print(f"\n✓ Saved changes to {EDITOR_STATE['xml_path']}")

        # Re-render HTML
        html_content = XMLToHTMLRenderer.render(content)

        # Always run full finalization after save
        print("\n" + "=" * 80)
        print("RUNNING FULL FINALIZATION PIPELINE")
        print("=" * 80)

        result = reprocess_pipeline()
        result['html'] = html_content
        return jsonify(result)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


def html_to_xml(html_content: str) -> str:
    """
    Convert HTML back to XML (simplified converter).
    This is a basic implementation - you may want to enhance it based on your needs.
    """
    try:
        # Parse HTML
        from lxml import html as lhtml
        
        # Wrap in a root element if needed
        if not html_content.strip().startswith('<'):
            html_content = f'<div>{html_content}</div>'
        
        # Parse the HTML
        doc = lhtml.fromstring(html_content)
        
        # Convert HTML elements back to XML
        xml_root = html_element_to_xml(doc)
        
        # Serialize to string
        xml_string = etree.tostring(xml_root, encoding='unicode', pretty_print=True)
        
        return xml_string
        
    except Exception as e:
        print(f"Error converting HTML to XML: {e}")
        raise


def html_element_to_xml(elem):
    """Convert an HTML element tree back to XML/DocBook structure"""
    
    # Get tag name safely, handling Cython/lxml compatibility
    raw_tag = elem.tag
    if raw_tag is None:
        tag = 'div'
    else:
        tag = str(raw_tag) if not isinstance(raw_tag, str) else raw_tag
    
    # Convert line breaks immediately
    if tag and tag.lower() == 'br':
        return etree.Element('linebreak')
    
    # Map HTML tags back to DocBook tags
    # NOTE: RittDoc DTD supports HTML tables, so we keep tr/th/td as-is!
    tag_map = {
        'h1': 'title',
        'h2': 'title',
        'h3': 'title',
        'h4': 'title',
        'h5': 'title',
        'h6': 'title',
        'p': 'para',
        'strong': 'emphasis',
        'em': 'emphasis',
        'b': 'emphasis',
        'i': 'emphasis',
        'ul': 'itemizedlist',
        'ol': 'orderedlist',
        'li': 'listitem',
        'table': 'table',
        'caption': 'caption',
        'thead': 'thead',
        'tbody': 'tbody',
        'tr': 'tr',          # Keep as 'tr' - HTML tables are supported!
        'th': 'th',          # Keep as 'th' - HTML tables are supported!
        'td': 'td',          # Keep as 'td' - HTML tables are supported!
        'img': 'imagedata',
        'figure': 'figure',
        'figcaption': 'caption',
        'span': 'phrase',
        'sup': 'superscript',
        'sub': 'subscript',
        'a': 'link',
        'div': 'section'
    }
    
    # Check if element has a data-tag attribute (from our XML rendering)
    original_tag = elem.get('data-tag')
    if original_tag:
        xml_tag = original_tag
    else:
        xml_tag = tag_map.get(tag, tag)
    
    # Create XML element
    xml_elem = etree.Element(xml_tag)
    
    # Copy relevant attributes - FILTER OUT data-* attributes for DTD compliance
    for attr_name, attr_value in elem.items():
        # Skip all data-* attributes (these are only for HTML rendering/sync)
        if attr_name.startswith('data-'):
            continue
        # Skip HTML-specific attributes that are not valid in DocBook
        elif attr_name in ['class', 'onerror', 'alt', 'border', 'onclick', 'onload', 'style', 'loading', 'decoding', 'fetchpriority']:
            # Handle special cases
            if attr_name == 'class' and 'docbook-' in attr_value:
                continue
            elif attr_name == 'style':
                # Parse inline styles back to attributes
                parse_style_to_attributes(attr_value, xml_elem)
            elif attr_name == 'class':
                # Preserve non-docbook classes
                continue
            else:
                continue
        elif attr_name == 'src' and tag == 'img':
            # Extract fileref from image src
            fileref = attr_value.replace('/api/media/', '')
            xml_elem.set('fileref', fileref)
            xml_elem.set('width', '100%')
            xml_elem.set('scalefit', '1')
        elif tag == 'a' and attr_name in ['href', 'data-href']:
            xml_elem.set('href', attr_value)
        else:
            # Copy other attributes (like id, colspan, rowspan, etc.)
            xml_elem.set(attr_name, attr_value)
    
    # Handle special cases
    if tag == 'strong' or tag == 'b':
        xml_elem.set('role', 'bold')
    elif tag == 'em' or tag == 'i':
        xml_elem.set('role', 'italic')
    elif tag == 'span' and 'role' not in xml_elem.attrib:
        xml_elem.set('role', 'phrase')
    
    # Add text content
    if elem.text:
        xml_elem.text = elem.text
    
    # Recursively process children
    for child in elem:
        child_xml = html_element_to_xml(child)
        xml_elem.append(child_xml)
        if child.tail:
            if len(xml_elem) > 0:
                if xml_elem[-1].tail:
                    xml_elem[-1].tail += child.tail
                else:
                    xml_elem[-1].tail = child.tail
            else:
                if xml_elem.text:
                    xml_elem.text += child.tail
                else:
                    xml_elem.text = child.tail
    
    # Ensure listitems contain para nodes
    if xml_tag == 'listitem' and len(xml_elem) == 0:
        text_content = (xml_elem.text or '').strip()
        if text_content:
            para = etree.Element('para')
            para.text = xml_elem.text
            xml_elem.text = None
            xml_elem.append(para)
    
    return xml_elem


def parse_style_to_attributes(style_str: str, elem):
    """Parse inline CSS styles back to XML attributes"""
    if not style_str:
        return
    
    styles = {}
    for part in style_str.split(';'):
        part = part.strip()
        if ':' in part:
            key, value = part.split(':', 1)
            styles[key.strip()] = value.strip()
    
    # Map CSS properties back to XML attributes
    if 'font-family' in styles:
        elem.set('font-family', styles['font-family'])
    
    if 'font-size' in styles:
        size = styles['font-size'].replace('pt', '').replace('px', '')
        elem.set('font-size', size)
    
    # Note: font-weight bold is handled via role="bold" on emphasis elements,
    # so we don't set a separate bold attribute (which is not valid in DocBook DTD)
    
    # Note: font-style italic is handled via role="italic" on emphasis elements,
    # so we don't set a separate italic attribute (which is not valid in DocBook DTD)
    
    if 'color' in styles:
        elem.set('color', styles['color'])
    
    if 'background-color' in styles:
        elem.set('background-color', styles['background-color'])


@app.route('/api/screenshot', methods=['POST'])
def api_screenshot():
    """Handle screenshot from PDF and replace image"""
    try:
        data = request.json
        image_data = data.get('imageData', '')
        target_filename = data.get('targetFilename', '')
        page_number = data.get('pageNumber', 0)
        
        if not image_data or not target_filename:
            return jsonify({'error': 'Missing image data or target filename'}), 400
        
        # Decode base64 image
        if ',' in image_data:
            image_data = image_data.split(',')[1]
        
        image_bytes = base64.b64decode(image_data)
        image = Image.open(io.BytesIO(image_bytes))
        
        # Save to multimedia folder
        if not EDITOR_STATE['multimedia_folder']:
            return jsonify({'error': 'Multimedia folder not set'}), 400
        
        # Determine save path
        save_path = EDITOR_STATE['multimedia_folder'] / target_filename
        
        # Create directory if needed
        save_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Save image
        image.save(save_path, 'PNG')
        
        print(f"\n✓ Saved screenshot to {save_path}")
        
        return jsonify({
            'success': True,
            'message': f'Screenshot saved as {target_filename}',
            'path': str(save_path)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/render-html', methods=['POST'])
def api_render_html():
    """Convert XML to HTML for preview"""
    try:
        data = request.json
        xml_content = data.get('xml', '')
        
        html_content = XMLToHTMLRenderer.render(xml_content)
        
        return jsonify({
            'success': True,
            'html': html_content
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/media-list', methods=['GET'])
def api_media_list():
    """List all media files in multimedia folder"""
    if not EDITOR_STATE['multimedia_folder'] or not EDITOR_STATE['multimedia_folder'].exists():
        return jsonify({'files': []})
    
    try:
        files = []
        for file_path in EDITOR_STATE['multimedia_folder'].rglob('*'):
            if file_path.is_file() and file_path.suffix.lower() in ['.png', '.jpg', '.jpeg', '.gif', '.svg']:
                relative_path = file_path.relative_to(EDITOR_STATE['multimedia_folder'])
                files.append({
                    'name': file_path.name,
                    'path': str(relative_path),
                    'size': file_path.stat().st_size
                })
        
        return jsonify({'files': files})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/placeholder-image', methods=['GET'])
def api_placeholder_image():
    """Generate a placeholder image for missing images"""
    # Create a simple placeholder image
    img = Image.new('RGB', (400, 300), color=(220, 220, 220))

    # Save to bytes
    img_io = io.BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)

    return send_file(img_io, mimetype='image/png')


@app.route('/api/debug-media', methods=['GET'])
def api_debug_media():
    """Debug endpoint to check multimedia folder status and compare XML refs vs available files"""
    multimedia_folder = EDITOR_STATE.get('multimedia_folder')
    xml_path = EDITOR_STATE.get('xml_path')

    result = {
        'status': 'ok',
        'multimedia_folder': str(multimedia_folder) if multimedia_folder else None,
        'xml_path': str(xml_path) if xml_path else None,
        'available_images': [],
        'xml_image_refs': [],
        'missing_images': [],
        'unused_images': []
    }

    if not multimedia_folder:
        result['status'] = 'error'
        result['message'] = 'Multimedia folder is NOT set'
        return jsonify(result)

    if not multimedia_folder.exists():
        result['status'] = 'error'
        result['message'] = f'Multimedia folder does not exist: {multimedia_folder}'
        return jsonify(result)

    # List all image files in the multimedia folder
    image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg', '.webp'}
    available_names = set()

    for file_path in multimedia_folder.rglob('*'):
        if file_path.is_file() and file_path.suffix.lower() in image_extensions:
            relative_path = file_path.relative_to(multimedia_folder)
            result['available_images'].append({
                'name': file_path.name,
                'relative_path': str(relative_path),
                'size': file_path.stat().st_size
            })
            available_names.add(file_path.name)
            available_names.add(str(relative_path))

    # Parse XML to find image references
    xml_refs = set()
    if xml_path and xml_path.exists():
        try:
            with open(xml_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # Find all fileref attributes
            import re
            for match in re.finditer(r'fileref=["\']([^"\']+)["\']', content):
                ref = match.group(1)
                xml_refs.add(ref)
                result['xml_image_refs'].append(ref)
        except Exception as e:
            result['xml_parse_error'] = str(e)

    # Find mismatches
    for ref in xml_refs:
        ref_basename = Path(ref).name
        # Check if file exists (try various paths)
        found = False
        if ref in available_names or ref_basename in available_names:
            found = True
        if not found:
            result['missing_images'].append(ref)

    for img in result['available_images']:
        name = img['name']
        if name not in xml_refs and img['relative_path'] not in xml_refs:
            result['unused_images'].append(name)

    result['message'] = f"Found {len(result['available_images'])} images, {len(result['xml_image_refs'])} XML refs, {len(result['missing_images'])} missing"
    if result['missing_images']:
        result['status'] = 'warning'

    return jsonify(result)


@app.route('/api/page-mapping', methods=['GET'])
def api_page_mapping():
    """Extract page number mapping from XML for scroll synchronization"""
    if not EDITOR_STATE['xml_path']:
        return jsonify({'error': 'No XML file loaded'}), 400

    try:
        import re

        with open(EDITOR_STATE['xml_path'], 'r', encoding='utf-8') as f:
            xml_content = f.read()

        root = etree.fromstring(xml_content.encode('utf-8'))

        # Build page mapping: page_number -> list of element info
        page_mapping = {}
        element_index = 0

        def extract_page_info(elem, parent_page=None):
            nonlocal element_index
            # Safely extract tag name, handling Cython/lxml compatibility
            raw_tag = elem.tag if elem.tag is not None else ''
            tag_str = str(raw_tag) if not isinstance(raw_tag, str) else raw_tag
            tag = tag_str.split('}')[-1] if '}' in tag_str else tag_str

            # Extract page number from various sources
            page_num = None

            # Check explicit page attribute
            page_attr = elem.get('page', '')
            if page_attr and page_attr.isdigit():
                page_num = int(page_attr)

            # Try to extract from ID (e.g., "p98_table1" -> "98")
            elem_id = elem.get('id', '')
            if not page_num and elem_id:
                match = re.search(r'p(\d+)_', elem_id)
                if match:
                    page_num = int(match.group(1))

            # Use parent's page if available
            if not page_num:
                page_num = parent_page

            # Store element info if we have a page number and it's a content element
            if page_num and tag in ['para', 'p', 'table', 'figure', 'media', 'mediaobject',
                                     'section', 'chapter', 'title', 'sect1', 'sect2', 'sect3']:
                if page_num not in page_mapping:
                    page_mapping[page_num] = []

                # Extract block info
                reading_block = elem.get('reading_block', elem.get('reading-block', ''))
                reading_order = elem.get('reading_order', elem.get('reading-order', ''))
                col_id = elem.get('col_id', elem.get('col-id', ''))

                page_mapping[page_num].append({
                    'index': element_index,
                    'tag': tag,
                    'id': elem_id,
                    'reading_block': reading_block,
                    'reading_order': reading_order,
                    'col_id': col_id
                })
                element_index += 1

            # Recurse into children
            for child in elem:
                extract_page_info(child, page_num)

        extract_page_info(root)

        # Create reverse mapping: element_index -> page_number
        element_to_page = {}
        for page_num, elements in page_mapping.items():
            for elem_info in elements:
                element_to_page[elem_info['index']] = page_num

        # Get total pages (max page number found)
        total_pages = max(page_mapping.keys()) if page_mapping else 0

        return jsonify({
            'mapping': {str(k): v for k, v in page_mapping.items()},
            'element_to_page': element_to_page,
            'total_xml_pages': total_pages,
            'page_count': len(page_mapping)
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def send_completion_webhook(status: str, package_path: str = None, error: str = None, output_files: list = None):
    """Send webhook notification to UI backend when editor save/finalization completes.

    Args:
        status: 'completed' or 'failed'
        package_path: Path to the output package (on success)
        error: Error message (on failure)
        output_files: List of output file paths (on success)
    """
    webhook_url = EDITOR_STATE.get('webhook_url')
    job_id = EDITOR_STATE.get('job_id')
    api_base_url = EDITOR_STATE.get('api_base_url', 'http://localhost:8000')

    if not webhook_url or not job_id:
        print(f"  Webhook skipped: url={webhook_url}, job_id={job_id}")
        return

    try:
        import requests

        base_url = api_base_url.rstrip('/')
        job_files_base = f"{base_url}/api/v1/jobs/{job_id}/files"

        payload = {
            'jobId': job_id,
            'status': status,
            'fileType': 'pdf',
            'apiBaseUrl': base_url,
            'links': {
                'job': f"{base_url}/api/v1/jobs/{job_id}",
                'files': job_files_base,
            }
        }

        if status == 'completed':
            # Build file list with download URLs
            files_with_urls = []
            all_files = output_files or []

            # Add package path if provided and not already in list
            if package_path:
                package_name = Path(package_path).name
                if package_name not in all_files:
                    all_files.append(package_name)

            for filename in all_files:
                if isinstance(filename, Path):
                    filename = filename.name
                elif '/' in filename or '\\' in filename:
                    filename = Path(filename).name

                file_info = {
                    'name': filename,
                    'downloadUrl': f"{job_files_base}/{filename}",
                }
                # Categorize by file type
                if filename.endswith('_rittdoc.zip') or filename.endswith('_rittdoc_final.zip'):
                    file_info['type'] = 'rittdoc_package'
                elif filename.endswith('_docbook.zip') or filename.endswith('_edited.zip'):
                    file_info['type'] = 'docbook_package'
                elif filename.endswith('.docx'):
                    file_info['type'] = 'word_document'
                elif filename.endswith('_validation_report.xlsx'):
                    file_info['type'] = 'validation_report'
                elif filename.endswith('_docbook42.xml'):
                    file_info['type'] = 'docbook_xml'
                elif filename.endswith('.xml'):
                    file_info['type'] = 'xml'
                else:
                    file_info['type'] = 'other'
                files_with_urls.append(file_info)

            payload['outputFiles'] = files_with_urls

            # Add direct links to key files
            zip_files = [f['name'] for f in files_with_urls if f['type'] == 'rittdoc_package']
            docx_files = [f['name'] for f in files_with_urls if f['type'] == 'word_document']
            xlsx_files = [f['name'] for f in files_with_urls if f['type'] == 'validation_report']
            xml_files = [f['name'] for f in files_with_urls if f['type'] == 'docbook_xml']

            if zip_files:
                payload['links']['rittdocPackage'] = f"{job_files_base}/{zip_files[0]}"
                payload['outputPackage'] = zip_files[0]
            if docx_files:
                payload['links']['wordDocument'] = f"{job_files_base}/{docx_files[0]}"
            if xlsx_files:
                payload['links']['validationReport'] = f"{job_files_base}/{xlsx_files[0]}"
            if xml_files:
                payload['links']['docbookXml'] = f"{job_files_base}/{xml_files[0]}"

        elif error:
            payload['error'] = error

        response = requests.post(
            webhook_url,
            json=payload,
            timeout=10
        )

        if response.ok:
            print(f"  ✓ Webhook sent successfully: {status} for job {job_id}")
        else:
            print(f"  Webhook failed ({response.status_code}): {response.text}")

    except Exception as e:
        # Log but don't fail - webhook is non-critical
        print(f"  Webhook error (non-critical): {e}")


def reprocess_pipeline():
    """Re-run the full processing pipeline after edits.

    This includes:
    1. Re-packaging the XML
    2. Running RittDoc compliance pipeline (validation + fixing)
    3. Generating DOCX via pandoc
    """
    try:
        # Re-package the XML
        from package import package_docbook, BOOK_DOCTYPE_SYSTEM_DEFAULT, make_file_fetcher

        # Parse the edited XML
        tree = etree.parse(str(EDITOR_STATE['xml_path']))
        root = tree.getroot()

        # Create media fetcher
        search_paths = []
        if EDITOR_STATE['multimedia_folder'] and EDITOR_STATE['multimedia_folder'].exists():
            search_paths.append(EDITOR_STATE['multimedia_folder'])
            shared_images = EDITOR_STATE['multimedia_folder'] / 'SharedImages'
            if shared_images.exists():
                search_paths.append(shared_images)

        media_fetcher = make_file_fetcher(search_paths) if search_paths else None

        # Determine output location based on whether we're editing from a package
        original_package = EDITOR_STATE.get('package_zip')
        if original_package and original_package.exists():
            # Use the original package's directory and base name
            output_dir = original_package.parent
            # Extract base name from original package (e.g., "book_rittdoc.zip" -> "book")
            base_name = original_package.stem
            for suffix in ['_rittdoc', '_docbook', '_edited', '_rittdoc_final']:
                if base_name.endswith(suffix):
                    base_name = base_name[:-len(suffix)]
                    break
            print(f"\nEditing from package: {original_package}")
            print(f"Output will replace: {original_package}")
        else:
            # Create new package in the same directory as the XML file
            # This ensures files are in the job's output directory for API access
            output_dir = EDITOR_STATE['xml_path'].parent
            base_name = EDITOR_STATE['xml_path'].stem
            if base_name.endswith('_unified'):
                base_name = base_name[:-8]
            # Remove "unified_for_editing" suffix if present
            if base_name.endswith('_for_editing'):
                base_name = base_name[:-12]
            if base_name.endswith('unified'):
                base_name = base_name[:-7].rstrip('_')

        output_dir.mkdir(parents=True, exist_ok=True)

        zip_path = output_dir / f"{base_name}_edited.zip"

        print(f"Creating package: {zip_path}")

        # Safely extract root tag name, handling Cython/lxml compatibility
        raw_root_tag = root.tag if root.tag is not None else 'book'
        root_tag_str = str(raw_root_tag) if not isinstance(raw_root_tag, str) else raw_root_tag
        root_name = root_tag_str.split('}', 1)[-1] if root_tag_str.startswith('{') else root_tag_str

        package_docbook(
            root=root,
            root_name=root_name,
            dtd_system=BOOK_DOCTYPE_SYSTEM_DEFAULT,
            zip_path=str(zip_path),
            processing_instructions=[],
            assets=[],
            media_fetcher=media_fetcher,
            book_doctype_system=BOOK_DOCTYPE_SYSTEM_DEFAULT,
            metadata_dir=EDITOR_STATE['xml_path'].parent,
        )

        print(f"✓ Created package: {zip_path}")

        # Track final package path
        final_package = zip_path
        validation_success = True
        validation_report = None

        # Run compliance pipeline
        if EDITOR_STATE['dtd_path']:
            final_output = output_dir / f"{base_name}_rittdoc_final.zip"

            print("\nRunning RittDoc compliance pipeline...")
            pipeline = RittDocCompliancePipeline(EDITOR_STATE['dtd_path'])
            validation_success = pipeline.run(
                input_zip=zip_path,
                output_zip=final_output,
                max_iterations=3
            )

            final_package = final_output

            # If editing from a package, replace the original with the new validated version
            if original_package and original_package.exists() and final_output.exists():
                import shutil
                try:
                    # Backup original (optional - could remove if not needed)
                    backup_path = original_package.with_suffix('.zip.bak')
                    if backup_path.exists():
                        backup_path.unlink()

                    # Replace original with new package
                    shutil.copy2(final_output, original_package)
                    print(f"✓ Updated original package: {original_package}")
                    final_package = original_package  # Return the original path
                except Exception as e:
                    print(f"WARNING: Could not replace original package: {e}")

            if not validation_success:
                validation_report = str(final_output.parent / f"{final_output.stem}_validation_report.xlsx")

        # Generate DOCX via pandoc
        print("\n" + "=" * 80)
        print("GENERATING WORD DOCUMENT")
        print("=" * 80)

        out_docx = output_dir / f"{base_name}.docx"
        docx_success = False
        docx_error = None

        try:
            # Build resource path for pandoc to find images
            resource_paths = [str(output_dir)]
            if EDITOR_STATE['multimedia_folder'] and EDITOR_STATE['multimedia_folder'].exists():
                resource_paths.append(str(EDITOR_STATE['multimedia_folder']))
            resource_path = ":".join(resource_paths)

            # Run pandoc to convert XML to DOCX
            pandoc_cmd = [
                "pandoc",
                "-f", "docbook",
                "-t", "docx",
                "--toc",
                "--toc-depth=3",
                f"--resource-path={resource_path}",
                "-o", str(out_docx),
                str(EDITOR_STATE['xml_path']),
            ]

            print(f"Running: {' '.join(pandoc_cmd)}")
            result = subprocess.run(pandoc_cmd, capture_output=True, text=True)

            if result.returncode != 0:
                docx_error = result.stderr or "pandoc failed with no error message"
                print(f"WARNING: pandoc returned non-zero: {docx_error}")
            elif not out_docx.exists() or out_docx.stat().st_size == 0:
                docx_error = "pandoc did not produce a valid DOCX file"
                print(f"WARNING: {docx_error}")
            else:
                docx_success = True
                print(f"✓ Word document: {out_docx}")
        except FileNotFoundError:
            docx_error = "pandoc not found. Please install pandoc to generate DOCX files."
            print(f"WARNING: {docx_error}")
        except Exception as e:
            docx_error = str(e)
            print(f"WARNING: DOCX generation failed: {docx_error}")

        # Build result
        result = {
            'success': validation_success,
            'message': 'Full finalization completed successfully' if (validation_success and docx_success) else 'Finalization completed with warnings',
            'package': str(final_package),
        }

        if validation_report:
            result['validation_report'] = validation_report

        if docx_success:
            result['docx'] = str(out_docx)
        else:
            result['docx_error'] = docx_error

        print("\n" + "=" * 80)
        print("FINALIZATION COMPLETE")
        print("=" * 80)
        print(f"  Package: {final_package}")
        if docx_success:
            print(f"  DOCX:    {out_docx}")
        else:
            print(f"  DOCX:    Failed ({docx_error})")
        print("=" * 80)

        # Send webhook notification to UI backend
        send_completion_webhook('completed', package_path=str(final_package))

        return result

    except Exception as e:
        import traceback
        traceback.print_exc()
        # Send webhook notification on failure
        send_completion_webhook('failed', error=str(e))
        return {
            'success': False,
            'error': str(e)
        }


def open_browser(port):
    """Open browser after a short delay"""
    webbrowser.open(f'http://localhost:{port}')


def start_editor(pdf_path: Path, xml_path: Path, multimedia_folder: Path, dtd_path: Path, port: int = 5000, auto_open_browser: bool = True, job_id: str = None, webhook_url: str = None, api_base_url: str = None, package_zip: str = None):
    """Start the editor server

    Args:
        pdf_path: Path to PDF file
        xml_path: Path to XML file
        multimedia_folder: Path to multimedia folder
        dtd_path: Path to DTD file
        port: Server port (default 5000)
        auto_open_browser: Whether to auto-open browser (default True). Set to False when launched from API.
        job_id: Job ID for webhook notifications
        webhook_url: URL to call when save completes
        api_base_url: Base URL for the PDF API server (for webhook download URLs)
        package_zip: Path to the original ZIP package being edited (for replacement after save)
    """

    EDITOR_STATE['pdf_path'] = pdf_path
    EDITOR_STATE['xml_path'] = xml_path
    EDITOR_STATE['multimedia_folder'] = multimedia_folder
    EDITOR_STATE['dtd_path'] = dtd_path
    EDITOR_STATE['job_id'] = job_id
    EDITOR_STATE['webhook_url'] = webhook_url
    EDITOR_STATE['api_base_url'] = api_base_url or 'http://localhost:8000'
    EDITOR_STATE['package_zip'] = Path(package_zip) if package_zip else None

    print("=" * 80)
    print("RITTDOC EDITOR - STARTING WEB UI")
    print("=" * 80)
    print(f"PDF:        {pdf_path}")
    print(f"XML:        {xml_path}")
    print(f"Media:      {multimedia_folder}")
    print(f"DTD:        {dtd_path}")
    if package_zip:
        print(f"Package:    {package_zip} (will be updated on save)")
    print(f"Server:     http://localhost:{port}")
    print("=" * 80)

    # Open browser after 1.5 seconds (unless disabled)
    if auto_open_browser:
        print("\nOpening browser...")
        Timer(1.5, open_browser, args=[port]).start()
    else:
        print("\nEditor server started (browser auto-open disabled)")
        print(f"Access the editor at: http://localhost:{port}")

    # Start Flask server
    app.run(host='0.0.0.0', port=port, debug=False)


def main():
    parser = argparse.ArgumentParser(description="RittDoc Web UI Editor")
    parser.add_argument('pdf', help='PDF file path')
    parser.add_argument('xml', help='Unified XML file path')
    parser.add_argument('--multimedia', help='Multimedia folder path')
    parser.add_argument('--dtd', default='RITTDOCdtd/v1.1/RittDocBook.dtd', help='DTD file path')
    parser.add_argument('--port', type=int, default=5000, help='Server port')
    parser.add_argument('--no-browser', action='store_true', help='Do not auto-open browser (for API use)')
    parser.add_argument('--job-id', help='Job ID for webhook notifications')
    parser.add_argument('--webhook-url', help='URL to call when save completes')
    parser.add_argument('--api-base-url', help='Base URL of PDF API server (for download URLs in webhooks)')
    parser.add_argument('--package-zip', help='Original ZIP package path (for repackaging after edits)')

    args = parser.parse_args()
    
    pdf_path = Path(args.pdf)
    xml_path = Path(args.xml)
    dtd_path = Path(args.dtd)
    
    if not pdf_path.exists():
        print(f"Error: PDF not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)
    
    if not xml_path.exists():
        print(f"Error: XML not found: {xml_path}", file=sys.stderr)
        sys.exit(1)
    
    # Auto-detect multimedia folder with multiple fallback strategies
    # Use resolve() to get canonical path (handles case sensitivity on macOS)
    if args.multimedia:
        multimedia_folder = Path(args.multimedia).resolve()
    else:
        base = xml_path.stem
        if base.endswith('_unified'):
            base = base[:-8]
        # Remove common suffixes to get PDF base name
        for suffix in ['_docbook42', '_docbook', '_unified']:
            if base.endswith(suffix):
                base = base[:-len(suffix)]
                break
        # Use resolved parent path
        xml_parent = xml_path.parent.resolve()
        multimedia_folder = xml_parent / f"{base}_MultiMedia"

    if not multimedia_folder.exists():
        # Try multiple naming patterns (case-insensitive search via multiple globs)
        search_patterns = [
            "*_MultiMedia",      # Standard pattern
            "*_Multimedia",      # Mixed case
            "*_multimedia",      # Lowercase
            "*MultiMedia",       # Without underscore
            "*Multimedia",       # Mixed case without underscore
            "*multimedia",       # Lowercase without underscore
            "_MultiMedia",       # Just prefix
            "_Multimedia",       # Mixed case prefix
            "_multimedia",       # Lowercase prefix
            "MultiMedia",        # Plain name
            "Multimedia",        # Mixed case plain
            "multimedia",        # Lowercase plain
        ]

        found_folder = None
        # Search in the resolved parent directory
        search_dir = xml_path.parent.resolve()
        for pattern in search_patterns:
            matches = list(search_dir.glob(pattern))
            # Filter to only directories
            matches = [m for m in matches if m.is_dir()]
            if matches:
                found_folder = matches[0].resolve()
                print(f"Found MultiMedia folder: {found_folder}")
                break

        if found_folder:
            multimedia_folder = found_folder
        else:
            print(f"Warning: Multimedia folder not found. Tried multiple patterns in:")
            print(f"  {search_dir}")
            print("  Patterns: *_MultiMedia, *_Multimedia, *_multimedia, MultiMedia, etc.")
            multimedia_folder = None
    else:
        # Resolve the path to canonical form
        multimedia_folder = multimedia_folder.resolve()
        print(f"Using MultiMedia folder: {multimedia_folder}")

    start_editor(
        pdf_path, xml_path, multimedia_folder, dtd_path, args.port,
        auto_open_browser=not args.no_browser,
        job_id=args.job_id,
        webhook_url=args.webhook_url,
        api_base_url=args.api_base_url,
        package_zip=args.package_zip
    )


if __name__ == '__main__':
    main()
