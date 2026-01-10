# Editor Implementation Summary

## Goals
1. Provide a first-class browser UI to review PDFs, XML, and HTML in sync.
2. Enable inline edits (code or WYSIWYG) with immediate validation and feedback.
3. Integrate with the RittDoc packaging pipeline so manual fixes roll straight into deliverables.

## Architecture Overview
| Layer | Responsibilities |
| --- | --- |
| `launch_editor.py` | Detect input assets, parse CLI flags, and invoke the server. |
| `editor_server.py` (Flask) | Serve SPA assets, expose REST endpoints for PDF/media, persist XML, convert HTML↔XML, call `RittDocCompliancePipeline`. |
| `editor_ui/` (HTML/JS/CSS) | Render the UI, manage Monaco Editor, communicate with API, orchestrate PDF.js rendering and toolbar actions. |

## Key Components
### Monaco + XML workflow
- Monaco editor is initialized with the server-provided XML from `/api/init`.
- Format & Validate buttons call local helpers; saves go through `/api/save`.
- Scroll sync hooks exist and can be toggled in `app.js`.

### HTML preview & edit
- Server renders HTML in `XMLToHTMLRenderer` so preview mode matches DocBook semantics.
- HTML edit mode reuses the same markup but enables `contenteditable`, adds a toolbar, and tracks dirty state.
- Upon save, the HTML is converted back to XML via `html_to_xml()` and revalidated.

### PDF rendering
- `pdf.js` streams the binary from `/api/pdf` and supports continuous scrolling, zooming, thumbnails, and screenshot capture.
- Screenshots are base64 uploads that land inside `_MultiMedia` via `/api/screenshot`.

### Save & Process flow
1. User clicks **Save & Process**.
2. Client sends `reprocess: true` to `/api/save`.
3. Server writes XML, re-renders HTML, then invokes `RittDocCompliancePipeline` (same object used in the CLI).
4. Response includes success flag, package path, and validation report link if applicable.

## Notable Implementation Details
- The server keeps state in `EDITOR_STATE` (PDF, XML, multimedia folder, DTD path) so requests remain stateless beyond initial setup.
- All media lookups try both the root `_MultiMedia` directory and its `SharedImages` subfolder.
- HTML→XML conversion strips `data-*` attributes, maps bold/italic roles, preserves table structures, and translates inline styles back to DocBook attributes.
- Notifications and loading spinners are controlled centrally in `app.js` so UX remains responsive during longer pipeline runs.

## Future Enhancements
- Re-enable synchronized scrolling now that the plumbing exists.
- Expand HTML→XML mapping for edge-case DocBook elements (admonitions, footnotes, etc.).
- Add automated tests for `/api/save`, `/api/screenshot`, and `/api/media-list` endpoints.

This summary captures how the editor pieces fit together so maintainers can modify or extend the experience confidently.
