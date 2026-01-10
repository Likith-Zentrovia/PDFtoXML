#!/usr/bin/env python3
"""
Visual demonstration of full-page image cropping.

Creates side-by-side comparison of cropped vs uncropped images.
"""

import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont

from Multipage_Image_Extractor import render_full_page_as_image


def create_comparison(pdf_path: str, page_num: int = 0):
    """Create before/after comparison of full-page image cropping."""
    
    print(f"\n{'='*80}")
    print(f"FULL-PAGE IMAGE CROPPING DEMONSTRATION")
    print(f"{'='*80}")
    print(f"PDF: {pdf_path}")
    print(f"Page: {page_num + 1}")
    
    # Open PDF
    doc = fitz.open(pdf_path)
    if len(doc) <= page_num:
        print(f"Error: PDF has only {len(doc)} pages")
        return
    
    page = doc[page_num]
    print(f"Page size: {page.rect.width:.1f} × {page.rect.height:.1f} points")
    
    # Create temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Render UNCROPPED version
        print("\n1. Rendering UNCROPPED version...")
        page_el_uncropped = ET.Element("page", {"number": str(page_num + 1)})
        render_full_page_as_image(
            page=page,
            page_no=page_num + 1,
            media_dir=str(temp_path),
            page_el=page_el_uncropped,
            dpi=150,
            reason="Demo: Uncropped",
            crop_margins=False,
        )
        uncropped_path = temp_path / f"page{page_num + 1}_fullpage.png"
        img_uncropped = Image.open(uncropped_path)
        print(f"   Size: {img_uncropped.width} × {img_uncropped.height} pixels")
        
        # Remove and render CROPPED version
        uncropped_path.unlink()
        print("\n2. Rendering CROPPED version...")
        page_el_cropped = ET.Element("page", {"number": str(page_num + 1)})
        render_full_page_as_image(
            page=page,
            page_no=page_num + 1,
            media_dir=str(temp_path),
            page_el=page_el_cropped,
            dpi=150,
            reason="Demo: Cropped",
            crop_margins=True,
            header_margin_pct=0.08,
            footer_margin_pct=0.08,
            left_margin_pct=0.05,
            right_margin_pct=0.05,
        )
        cropped_path = temp_path / f"page{page_num + 1}_fullpage.png"
        img_cropped = Image.open(cropped_path)
        print(f"   Size: {img_cropped.width} × {img_cropped.height} pixels")
        
        # Calculate reduction
        width_reduction = (1 - img_cropped.width / img_uncropped.width) * 100
        height_reduction = (1 - img_cropped.height / img_uncropped.height) * 100
        
        print(f"\n{'='*80}")
        print("RESULTS")
        print(f"{'='*80}")
        print(f"Width reduction:  {width_reduction:.1f}%")
        print(f"Height reduction: {height_reduction:.1f}%")
        print(f"File size before: {uncropped_path.stat().st_size if uncropped_path.exists() else 'N/A'}")
        print(f"File size after:  {cropped_path.stat().st_size}")
        
        # Create side-by-side comparison
        print(f"\n3. Creating comparison image...")
        
        # Resize for display (max width 800px per image)
        max_width = 800
        if img_uncropped.width > max_width or img_cropped.width > max_width:
            scale = min(max_width / img_uncropped.width, max_width / img_cropped.width)
            new_width_u = int(img_uncropped.width * scale)
            new_height_u = int(img_uncropped.height * scale)
            new_width_c = int(img_cropped.width * scale)
            new_height_c = int(img_cropped.height * scale)
            img_uncropped = img_uncropped.resize((new_width_u, new_height_u), Image.Resampling.LANCZOS)
            img_cropped = img_cropped.resize((new_width_c, new_height_c), Image.Resampling.LANCZOS)
        
        # Create comparison canvas
        gap = 40
        label_height = 60
        canvas_width = img_uncropped.width + gap + img_cropped.width + 40
        canvas_height = max(img_uncropped.height, img_cropped.height) + label_height + 40
        
        canvas = Image.new('RGB', (canvas_width, canvas_height), 'white')
        draw = ImageDraw.Draw(canvas)
        
        # Paste images
        x1 = 20
        y1 = label_height + 20
        canvas.paste(img_uncropped, (x1, y1))
        
        x2 = x1 + img_uncropped.width + gap
        canvas.paste(img_cropped, (x2, y1))
        
        # Draw labels
        try:
            # Try to use a nice font if available
            font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        except:
            # Fallback to default font
            font_large = ImageFont.load_default()
            font_small = ImageFont.load_default()
        
        # Title
        title = "Full-Page Image Cropping: Before & After"
        title_bbox = draw.textbbox((0, 0), title, font=font_large)
        title_width = title_bbox[2] - title_bbox[0]
        draw.text(((canvas_width - title_width) // 2, 10), title, fill='black', font=font_large)
        
        # Labels for each image
        label1 = f"BEFORE (Uncropped)\n{img_uncropped.width}×{img_uncropped.height}px"
        label2 = f"AFTER (Cropped)\n{img_cropped.width}×{img_cropped.height}px\n-{width_reduction:.1f}% width, -{height_reduction:.1f}% height"
        
        # Draw red box around uncropped to show what gets cropped
        draw.rectangle([x1-2, y1-2, x1+img_uncropped.width+2, y1+img_uncropped.height+2], outline='red', width=3)
        draw.text((x1 + 10, y1 - 50), label1, fill='red', font=font_small)
        
        # Draw green box around cropped
        draw.rectangle([x2-2, y1-2, x2+img_cropped.width+2, y1+img_cropped.height+2], outline='green', width=3)
        draw.text((x2 + 10, y1 - 50), label2, fill='green', font=font_small)
        
        # Save comparison
        comparison_path = Path(f"fullpage_cropping_comparison_page{page_num+1}.png")
        canvas.save(comparison_path)
        print(f"   Saved: {comparison_path}")
        print(f"\n{'='*80}")
        print(f"✓ Comparison image created: {comparison_path}")
        print(f"{'='*80}\n")
    
    doc.close()


def main():
    # Find a PDF to demonstrate with
    pdf_files = list(Path(".").glob("*.pdf"))
    if not pdf_files:
        print("Error: No PDF files found in current directory")
        return 1
    
    # Use first PDF
    pdf_path = pdf_files[0]
    create_comparison(str(pdf_path), page_num=0)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
