#!/usr/bin/env python3
"""
Helper script to launch the RittDoc Editor with automatic path detection
"""

import argparse
import sys
from pathlib import Path

def find_files():
    """Auto-detect PDF, XML, and multimedia folders in workspace"""
    workspace = Path('.')
    
    # Find PDF files
    pdfs = list(workspace.glob('*.pdf'))
    
    # Find unified XML files
    xmls = list(workspace.glob('*unified*.xml'))
    if not xmls:
        xmls = list(workspace.glob('*.xml'))
    
    # Find MultiMedia folders
    multimedia = list(workspace.glob('*MultiMedia*'))
    if not multimedia:
        multimedia = list(workspace.glob('*multimedia*'))
    
    return pdfs, xmls, multimedia


def main():
    parser = argparse.ArgumentParser(
        description="Launch RittDoc Editor with automatic file detection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-detect all files
  python3 launch_editor.py
  
  # Specify files manually
  python3 launch_editor.py --pdf mybook.pdf --xml mybook_unified.xml --multimedia mybook_MultiMedia
  
  # Use custom port
  python3 launch_editor.py --port 8080
        """
    )
    
    parser.add_argument('--pdf', help='PDF file path (auto-detected if not specified)')
    parser.add_argument('--xml', help='Unified XML file path (auto-detected if not specified)')
    parser.add_argument('--multimedia', help='Multimedia folder path (auto-detected if not specified)')
    parser.add_argument('--dtd', default='RITTDOCdtd/v1.1/RittDocBook.dtd', help='DTD file path')
    parser.add_argument('--port', type=int, default=5000, help='Server port (default: 5000)')
    parser.add_argument('--list', action='store_true', help='List available files and exit')
    
    args = parser.parse_args()
    
    # Auto-detect files if not specified
    if not args.pdf or not args.xml:
        print("Scanning workspace for files...")
        pdfs, xmls, multimedia = find_files()
        
        if args.list:
            print("\n" + "=" * 80)
            print("AVAILABLE FILES")
            print("=" * 80)
            
            print("\nPDF Files:")
            if pdfs:
                for i, pdf in enumerate(pdfs, 1):
                    print(f"  {i}. {pdf}")
            else:
                print("  (none found)")
            
            print("\nXML Files:")
            if xmls:
                for i, xml in enumerate(xmls, 1):
                    print(f"  {i}. {xml}")
            else:
                print("  (none found)")
            
            print("\nMultiMedia Folders:")
            if multimedia:
                for i, mm in enumerate(multimedia, 1):
                    print(f"  {i}. {mm}")
            else:
                print("  (none found)")
            
            print("\n" + "=" * 80)
            return
        
        # Select files
        if not args.pdf:
            if not pdfs:
                print("Error: No PDF files found in workspace", file=sys.stderr)
                sys.exit(1)
            elif len(pdfs) == 1:
                args.pdf = str(pdfs[0])
                print(f"✓ Auto-detected PDF: {args.pdf}")
            else:
                print("\nMultiple PDF files found:")
                for i, pdf in enumerate(pdfs, 1):
                    print(f"  {i}. {pdf}")
                choice = input("\nSelect PDF file (1-{}): ".format(len(pdfs)))
                try:
                    args.pdf = str(pdfs[int(choice) - 1])
                except (ValueError, IndexError):
                    print("Error: Invalid selection", file=sys.stderr)
                    sys.exit(1)
        
        if not args.xml:
            if not xmls:
                print("Error: No XML files found in workspace", file=sys.stderr)
                sys.exit(1)
            elif len(xmls) == 1:
                args.xml = str(xmls[0])
                print(f"✓ Auto-detected XML: {args.xml}")
            else:
                print("\nMultiple XML files found:")
                for i, xml in enumerate(xmls, 1):
                    print(f"  {i}. {xml}")
                choice = input("\nSelect XML file (1-{}): ".format(len(xmls)))
                try:
                    args.xml = str(xmls[int(choice) - 1])
                except (ValueError, IndexError):
                    print("Error: Invalid selection", file=sys.stderr)
                    sys.exit(1)
        
        if not args.multimedia:
            if multimedia:
                if len(multimedia) == 1:
                    args.multimedia = str(multimedia[0])
                    print(f"✓ Auto-detected MultiMedia: {args.multimedia}")
                else:
                    print("\nMultiple MultiMedia folders found:")
                    for i, mm in enumerate(multimedia, 1):
                        print(f"  {i}. {mm}")
                    choice = input("\nSelect MultiMedia folder (1-{}): ".format(len(multimedia)))
                    try:
                        args.multimedia = str(multimedia[int(choice) - 1])
                    except (ValueError, IndexError):
                        print("Error: Invalid selection", file=sys.stderr)
                        sys.exit(1)
            else:
                # Try to infer from XML filename
                xml_base = Path(args.xml).stem.replace('_unified', '')
                inferred = Path(f'{xml_base}_MultiMedia')
                if inferred.exists():
                    args.multimedia = str(inferred)
                    print(f"✓ Inferred MultiMedia: {args.multimedia}")
                else:
                    print(f"⚠ Warning: No MultiMedia folder found")
                    print(f"  Expected: {inferred}")
                    print("  Images may not display correctly")
    
    # Validate paths
    pdf_path = Path(args.pdf)
    xml_path = Path(args.xml)
    dtd_path = Path(args.dtd)
    
    if not pdf_path.exists():
        print(f"Error: PDF not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)
    
    if not xml_path.exists():
        print(f"Error: XML not found: {xml_path}", file=sys.stderr)
        sys.exit(1)
    
    # MultiMedia folder is optional
    multimedia_folder = Path(args.multimedia) if args.multimedia else None
    if multimedia_folder and not multimedia_folder.exists():
        print(f"Warning: MultiMedia folder not found: {multimedia_folder}")
        print("Images may not display correctly")
        multimedia_folder = None
    
    # Launch editor
    print("\n" + "=" * 80)
    print("LAUNCHING RITTDOC EDITOR")
    print("=" * 80)
    print(f"PDF:        {pdf_path.absolute()}")
    print(f"XML:        {xml_path.absolute()}")
    print(f"MultiMedia: {multimedia_folder.absolute() if multimedia_folder else 'Not specified'}")
    print(f"DTD:        {dtd_path.absolute()}")
    print(f"Port:       {args.port}")
    print("=" * 80)
    print("\nStarting server...")
    print(f"Editor will open at: http://localhost:{args.port}")
    print("Press Ctrl+C to stop the server")
    print()
    
    # Import and start editor
    try:
        from editor_server import start_editor
        start_editor(pdf_path, xml_path, multimedia_folder, dtd_path, args.port)
    except KeyboardInterrupt:
        print("\n\nServer stopped.")
    except Exception as e:
        print(f"\nError starting editor: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
