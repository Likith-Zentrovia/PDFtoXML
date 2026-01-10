# RittDoc Web Editor

A professional, browser-based workspace for inspecting PDFs alongside the generated RittDoc XML. It ships with the pipeline and can be launched either automatically (`--edit-mode`) or manually (`launch_editor.py`).

## Feature Highlights
- **Side-by-side PDF + XML/HTML** with resizable panes.
- **Monaco Editor** for syntax highlighting, auto-formatting, validation, and search.
- **Dual HTML modes**: read-only preview or rich-text editing with toolbar controls.
- **Screenshot workflow** to capture a region from the PDF and drop it into `_MultiMedia`.
- **One-click Save & Process** that re-runs the DocBook compliance pipeline and returns status.
- **Automatic asset discovery**: the launcher finds your PDF, `_unified.xml`, and media folder.

## Directory Layout
```
editor_ui/
  index.html      # Layout + toolbar markup
  app.js          # Monaco hooks, PDF.js integration, UI logic
  styles.css      # Layout and theme
editor_server.py  # Flask API for PDF/media streaming and XML persistence
launch_editor.py  # Convenience CLI wrapper
```

## Server Responsibilities
- Serve SPA assets (`/` → `editor_ui/index.html`).
- Stream the source PDF (`/api/pdf`) and media (`/api/media/<path>`).
- Return both the raw XML and rendered HTML snapshot (`/api/init`).
- Accept saves, validate XML, optionally re-run the pipeline, and push updated HTML back (`/api/save`).
- Persist screenshots to the media folder (`/api/screenshot`).
- Convert WYSIWYG edits back into DocBook-flavored XML.

## Launching
| Scenario | Command |
| --- | --- |
| Run the semantic DocBook pipeline | `python -m pdf2semantic.cli.pdf_to_docbook book.pdf -o output_dir --edit-mode` |
| Run as part of the full pipeline | `python3 pdf_to_rittdoc.py book.pdf --edit-mode` |
| Launch manually with auto-detection | `python3 launch_editor.py` |
| Specify everything manually | `python3 launch_editor.py --pdf book.pdf --xml book_unified.xml --multimedia book_MultiMedia --port 7000` |

## Workflow Tips
1. Use the thumbnail strip to jump quickly between pages; the PDF view updates instantly.
2. Toggle between XML, HTML Preview, and HTML Edit via the header buttons.
3. When editing HTML, the toolbar injects semantic tags; math symbols open a modal picker.
4. Saving validates the XML server-side using `lxml`. Errors are surfaced in the UI notification tray.
5. Save & Process triggers `RittDocCompliancePipeline` again, so you can immediately verify downstream packaging.

## Configuration Points
- **Port**: `--editor-port` in `pdf_to_rittdoc.py` or `python -m pdf2semantic.cli.pdf_to_docbook`, or `--port` in `launch_editor.py`.
- **DTD**: Pass `--dtd /path/to/RittDocBook.dtd` if you need an alternate schema.
- **Multimedia folder**: Auto-inferred from XML name, but you can override using CLI flags.

## Extending the UI
- Add new REST endpoints in `editor_server.py` and consume them via `app.js` `fetch()` calls.
- Hook additional toolbar actions by extending `setupRichTextToolbar()` in `app.js`.
- Update `styles.css` for theming; layout uses CSS grid + flexbox for simplicity.

## Testing Checklist
- Launch editor; verify `/api/init` loads without errors.
- Scroll PDF, switch views, take a screenshot, and confirm it appears under `_MultiMedia`.
- Make an edit in HTML mode, save, and inspect the XML diff.
- Use Save & Process to ensure pipeline reruns end-to-end.

With these pieces in place, the editor is fully documented and ready for teammates to use without spelunking through the code first.
