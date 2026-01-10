## AI Enrichment Layer

The deterministic pdf2semantic pipeline remains the default. The optional AI layer only runs
when **all three** of these gates are satisfied:

1. Global + feature toggles in `pdf2semantic/semantic_ai/ai_config.py`
2. An API key exported under `AI_KEY_ENV_VAR` (default `OPENAI_API_KEY`)
3. CLI flags enabling the desired passes

If any gate is missing, the deterministic DocBook output is returned untouched.

### Configuration

`pdf2semantic/semantic_ai/ai_config.py` holds the defaults:

- `AI_ENABLED` and per-feature booleans (paragraph structuring, layout validation, media anchor validation, text normalization) all default to `False`
- `PROVIDER`, `MODEL_NAME`, `API_BASE`, and `API_KEY_ENV_VAR` describe the model endpoint
- `is_ai_available()` verifies both `AI_ENABLED` and the presence of the env-var API key
- `feature_enabled(name)` re-reads the per-feature booleans at runtime

To opt in:

```python
AI_ENABLED = True
AI_PARAGRAPH_STRUCTURING_ENABLED = True
AI_LAYOUT_VALIDATION_ENABLED = True
AI_MEDIA_ANCHOR_VALIDATION_ENABLED = True
AI_TEXT_NORMALIZATION_ENABLED = True
```

Then export an API key before running the CLI, e.g.

```bash
export OPENAI_API_KEY=sk-...
```

### CLI Flags

| Flag | Description |
| --- | --- |
| `--ai-paragraph-structuring` | Re-group paragraphs and heading roles (Phase 2). Applies only if config + key permit. |
| `--ai-validate-layout` | Audit multi-column reading order; creates `<remark role="ai-layout-warning">` entries instead of mutating structure (Phase 3). |
| `--ai-validate-media-anchors` | Check if figures/tables are anchored to the right paragraph and emit `<remark role="ai-media-warning">` notes (Phase 4). |
| `--ai-fix-typography` | Deterministic cleanup of soft hyphens, ligatures, bad spaces, broken words, gated by AI approvals (Phase 5). |

Pass any combination of the flags; the orchestrator will call the AI layer sequentially after the deterministic DocBook is generated.

### Safety Guarantees

- AI **never** introduces new text. Paragraph regrouping and typography fixes run deterministic equivalence checks (`canonicalize_text`) before accepting changes.
- Any malformed AI response is ignored and logged; instead of mutating output, a `<remark role="ai-*-warning">` is appended for QA.
- Media packaging and DocBook validation still run after any AI rewrites. If AI touched text enough to regenerate DocBook, media assets are automatically re-packaged.
- Missing keys, disabled config, or absent CLI flags cause the pipeline to skip AI entirely with an info log.

### Model Integration

`semantic_ai/ai_client.py` is a thin wrapper over the OpenAI Python SDK (Responses API with chat fallback). Swap out `PROVIDER`, `MODEL_NAME`, and `API_BASE` in `ai_config.py` to use another provider, or extend `AiClient` with new `_call_*` helpers.
