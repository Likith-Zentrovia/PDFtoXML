# RittDoc Editor – Quick Start

This guide walks you through launching the web-based editor, reviewing a PDF + XML side by side, and pushing your changes back into the pipeline.

## 1. Launch the Editor

### Option A – Full pipeline with pause for edits
```bash
python3 pdf_to_rittdoc.py mybook.pdf --edit-mode
```
- Runs the PDF→XML stage first.
- Automatically starts the editor once the unified XML exists.
- After you close the browser tab, the CLI resumes to packaging/validation.

### Option B – Standalone editor session
```bash
python3 launch_editor.py
```
- Scans the workspace for a PDF, matching `_unified.xml`, and `_MultiMedia` directory.
- Prompts you if multiple candidates exist.
- Use `--pdf`, `--xml`, `--multimedia`, or `--port` to override detection.

Use `python3 launch_editor.py --list` to see every detected file without starting the server.

## 2. What Opens in the Browser
- **Left panel**: Continuous PDF canvas with zoom controls, page thumbnails, and a screenshot button.
- **Right panel**: Monaco-powered XML editor plus HTML preview/edit modes (tabs in header).
- **Header actions**:
  - `Save` keeps your XML changes.
  - `Save & Process` saves, then re-runs the DocBook compliance pipeline (results shown inline).

## 3. Editing Workflow
1. Select an element in the HTML view (for quick visual edits) or stay in XML for precise changes.
2. Use the rich-text toolbar in *HTML Edit* mode for emphasis, lists, tables, math symbols, etc.
3. Click **Save**. The server validates XML syntax and re-renders the preview.
4. (Optional) Click **Save & Process** to rebuild the DocBook package and validation report.

## 4. Screenshots & Image Replacement
- Hit the camera icon in the PDF toolbar to capture a region.
- Choose `-- New image --` or replace an existing asset from the dropdown.
- Saved files land inside the detected `_MultiMedia` folder so references stay valid.

## 5. Troubleshooting
| Symptom | Fix |
| --- | --- |
| Browser shows "Failed to load initial data" | Check terminal output for XML path or permissions errors. |
| PDF pane blank | Ensure the PDF path is reachable; rerun with explicit `--pdf`. |
| Images missing in HTML preview | Confirm `_MultiMedia` folder path or use `launch_editor.py --multimedia path/to/folder`. |
| Save blocked with XML error | Switch to XML tab, inspect the reported line/column, fix, and save again. |

You're ready to edit. Keep this quick start handy whenever you need to review or correct a conversion interactively.
