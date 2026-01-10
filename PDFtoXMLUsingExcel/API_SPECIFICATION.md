# PDF-to-XML Pipeline API Specification

## Service Identity

| Property | Value |
|----------|-------|
| **Service Name** | `pdf-pipeline` |
| **Description** | PDF to RittDoc DocBook XML conversion service |
| **Default Port** | `8000` |
| **API Version** | `v1` |
| **Base Path** | `/api/v1` |

---

## For External Services & UI Projects

This document defines the API contract for the PDF-to-XML conversion pipeline.
Use this specification to integrate with this service from any environment.

### OpenAPI Specification

The full OpenAPI 3.0 spec is available at runtime:
```
GET {SERVICE_URL}/openapi.json
```

Interactive documentation:
```
GET {SERVICE_URL}/docs      # Swagger UI
GET {SERVICE_URL}/redoc     # ReDoc
```

---

## Environment Configuration

### How to Configure Service URL

The URL of this service changes per environment. External services should:

1. **Use environment variables** (recommended)
2. **Use service discovery** (Kubernetes/Docker)
3. **Use API Gateway** (AWS API Gateway, Kong, etc.)

#### Environment Variable Convention

```bash
# Recommended naming convention
PDF_PIPELINE_URL=http://pdf-pipeline:8000

# Per-environment examples
PDF_PIPELINE_URL=http://localhost:8000              # Local development
PDF_PIPELINE_URL=http://pdf-pipeline:8000           # Docker Compose
PDF_PIPELINE_URL=http://pdf-pipeline.default.svc:8000  # Kubernetes
PDF_PIPELINE_URL=https://pdf-api.stage.company.com  # Staging
PDF_PIPELINE_URL=https://pdf-api.company.com        # Production
PDF_PIPELINE_URL=https://pdf-api.customer.aws.com   # Customer AWS
```

#### Service Discovery (Docker/Kubernetes)

```yaml
# Docker Compose - use service name
services:
  pdf-pipeline:
    image: pdf-pipeline:latest
    # Other services reference as: http://pdf-pipeline:8000

# Kubernetes - use service DNS
# http://pdf-pipeline.{namespace}.svc.cluster.local:8000
```

---

## API Endpoints

### Health & Discovery

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/health` | Health check with dependency status |
| `GET` | `/api/v1/info` | Service info and capabilities |
| `GET` | `/api/v1/config/options` | Configuration options for UI forms |
| `GET` | `/api/v1/config/schema` | JSON Schema for validation |

#### Health Check Response

```json
GET /api/v1/health

{
  "status": "healthy",
  "timestamp": "2025-01-15T10:30:00Z",
  "tracking_available": true,
  "mongodb_available": true,
  "mongodb_status": "connected"
}
```

#### Service Info Response

```json
GET /api/v1/info

{
  "version": "2.2.0",
  "service": "pdf-pipeline",
  "config": {
    "default_model": "claude-sonnet-4-20250514",
    "default_dpi": 300,
    "max_concurrent_jobs": 3
  },
  "capabilities": {
    "tracking": true,
    "editor": true,
    "rittdoc_packaging": true,
    "docx_output": true
  }
}
```

---

### Configuration (For Building UI Forms)

#### Get Dropdown Options

```json
GET /api/v1/config/options

{
  "options": {
    "model": {
      "label": "AI Model",
      "type": "dropdown",
      "default": "claude-sonnet-4-20250514",
      "options": [
        {"value": "claude-sonnet-4-20250514", "label": "Claude Sonnet 4 (Recommended)", "default": true},
        {"value": "claude-opus-4-5-20251101", "label": "Claude Opus 4.5 (Highest Quality)"},
        {"value": "claude-haiku-3-5-20241022", "label": "Claude Haiku 3.5 (Fastest)"}
      ]
    },
    "dpi": {
      "label": "Resolution (DPI)",
      "type": "dropdown",
      "default": 300,
      "options": [
        {"value": 150, "label": "150 DPI (Fast)"},
        {"value": 200, "label": "200 DPI (Balanced)"},
        {"value": 300, "label": "300 DPI (Recommended)", "default": true},
        {"value": 400, "label": "400 DPI (High Quality)"},
        {"value": 600, "label": "600 DPI (Maximum)"}
      ]
    },
    "temperature": { ... },
    "batch_size": { ... },
    "toc_depth": { ... },
    "template_type": { ... },
    "create_docx": {"type": "checkbox", "default": true},
    "create_rittdoc": {"type": "checkbox", "default": true},
    "skip_extraction": {"type": "checkbox", "default": false},
    "include_toc": {"type": "checkbox", "default": true}
  },
  "defaults": {
    "model": "claude-sonnet-4-20250514",
    "dpi": 300,
    "temperature": 0.0,
    "batch_size": 10,
    "toc_depth": 3,
    "template_type": "auto",
    "create_docx": true,
    "create_rittdoc": true,
    "skip_extraction": false,
    "include_toc": true
  }
}
```

---

### Conversion Workflow

#### 1. Start Conversion

```
POST /api/v1/convert
Content-Type: multipart/form-data

Form Fields:
  - file: (required) PDF file
  - model: (optional) AI model
  - dpi: (optional) Resolution
  - temperature: (optional) AI temperature
  - batch_size: (optional) Pages per batch
  - toc_depth: (optional) TOC depth
  - template_type: (optional) Document template
  - create_docx: (optional) Generate DOCX
  - create_rittdoc: (optional) Generate RittDoc ZIP
  - skip_extraction: (optional) Skip image extraction
  - include_toc: (optional) Include TOC
```

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing",
  "progress": 0,
  "filename": "document.pdf",
  "created_at": "2025-01-15T10:30:00Z"
}
```

#### 2. Poll Job Status

```
GET /api/v1/jobs/{job_id}
```

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "progress": 100,
  "filename": "document.pdf",
  "can_edit": true,
  "files": ["document_rittdoc.zip", "document.docx", "document_docbook42.xml"]
}
```

**Status Values:**
| Status | Description | Next Action |
|--------|-------------|-------------|
| `pending` | Queued | Wait |
| `processing` | Processing | Poll |
| `extracting` | Extracting images | Poll |
| `converting` | AI conversion | Poll |
| `packaging` | Creating RittDoc package | Poll |
| `validating` | DTD validation | Poll |
| `completed` | Done, zip package ready | Download files |
| `editing` | Editor is open (optional) | Wait for save |
| `failed` | Error occurred | Check error field |

> **Note:** Conversion now goes directly to `completed` with the zip package ready.
> No separate finalize step is required.

#### 3. Launch Editor (Optional - for corrections)

```
POST /api/v1/jobs/{job_id}/editor
```

**Response:**
```json
{
  "editor_url": "http://localhost:5100",
  "message": "Editor launched"
}
```

> **Note:** When user saves in editor, a new zip package is generated automatically with the changes.

#### 4. List Output Files (Available immediately after completed)

```
GET /api/v1/jobs/{job_id}/files
```

**Response:**
```json
{
  "files": [
    {"name": "document.docx", "size": 245000, "download_url": "/api/v1/jobs/{id}/files/document.docx"},
    {"name": "document_rittdoc_final.zip", "size": 890000, "download_url": "/api/v1/jobs/{id}/files/document_rittdoc_final.zip"},
    {"name": "document_unified.xml", "size": 125000, "download_url": "/api/v1/jobs/{id}/files/document_unified.xml"}
  ]
}
```

#### 5. Download File

```
GET /api/v1/jobs/{job_id}/files/{filename}

Returns: Binary file content
Content-Disposition: attachment; filename="{filename}"
```

---

### Dashboard (MongoDB)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/mongodb/dashboard` | Dashboard statistics |
| `GET` | `/api/v1/mongodb/conversions` | List conversions (paginated) |
| `GET` | `/api/v1/mongodb/conversions/{job_id}` | Get conversion details |
| `GET` | `/api/v1/mongodb/stats/daily?days=30` | Daily statistics |
| `GET` | `/api/v1/mongodb/stats/publishers` | Stats by publisher |

#### Dashboard Response

```json
GET /api/v1/mongodb/dashboard

{
  "total_conversions": 150,
  "successful": 142,
  "failed": 5,
  "in_progress": 3,
  "total_pages_processed": 4500,
  "total_images_extracted": 890,
  "average_duration_seconds": 45.2,
  "conversions_today": 12,
  "conversions_this_week": 45,
  "by_status": {"success": 142, "failure": 5, "in_progress": 3},
  "by_type": {"PDF": 150},
  "recent_conversions": [
    {
      "job_id": "abc123",
      "filename": "report.pdf",
      "status": "success",
      "created_at": "2025-01-15T10:30:00Z",
      "duration_seconds": 32.5
    }
  ]
}
```

---

## Error Handling

### Error Response Format

```json
{
  "detail": "Error message describing what went wrong"
}
```

### HTTP Status Codes

| Code | Meaning |
|------|---------|
| `200` | Success |
| `201` | Created |
| `400` | Bad request (invalid parameters) |
| `404` | Resource not found |
| `422` | Validation error |
| `500` | Internal server error |
| `503` | Service unavailable (MongoDB down) |

---

## Integration Examples

### JavaScript/TypeScript

```typescript
const PDF_PIPELINE_URL = process.env.PDF_PIPELINE_URL;

// Get config options for building form
const options = await fetch(`${PDF_PIPELINE_URL}/api/v1/config/options`)
  .then(r => r.json());

// Start conversion
const formData = new FormData();
formData.append('file', pdfFile);
formData.append('model', 'claude-sonnet-4-20250514');
formData.append('dpi', '300');

const job = await fetch(`${PDF_PIPELINE_URL}/api/v1/convert`, {
  method: 'POST',
  body: formData
}).then(r => r.json());

// Poll until completed (zip package is created automatically)
let status;
do {
  await new Promise(r => setTimeout(r, 2000));
  status = await fetch(`${PDF_PIPELINE_URL}/api/v1/jobs/${job.job_id}`)
    .then(r => r.json());
} while (!['completed', 'failed'].includes(status.status));

// Files are ready immediately - no finalize step needed
const files = await fetch(`${PDF_PIPELINE_URL}/api/v1/jobs/${job.job_id}/files`)
  .then(r => r.json());
```

### Python

```python
import os
import requests

PDF_PIPELINE_URL = os.environ.get('PDF_PIPELINE_URL', 'http://localhost:8000')

# Get config options
options = requests.get(f'{PDF_PIPELINE_URL}/api/v1/config/options').json()

# Start conversion
with open('document.pdf', 'rb') as f:
    response = requests.post(
        f'{PDF_PIPELINE_URL}/api/v1/convert',
        files={'file': f},
        data={'model': 'claude-sonnet-4-20250514', 'dpi': 300}
    )
job = response.json()

# Poll until completed (zip package is created automatically)
import time
while True:
    status = requests.get(f'{PDF_PIPELINE_URL}/api/v1/jobs/{job["job_id"]}').json()
    if status['status'] in ['completed', 'failed']:
        break
    time.sleep(2)

# Files are ready immediately - no finalize step needed
files = requests.get(f'{PDF_PIPELINE_URL}/api/v1/jobs/{job["job_id"]}/files').json()
```

### cURL

```bash
# Health check
curl $PDF_PIPELINE_URL/api/v1/health

# Get config options
curl $PDF_PIPELINE_URL/api/v1/config/options

# Start conversion
curl -X POST $PDF_PIPELINE_URL/api/v1/convert \
  -F "file=@document.pdf" \
  -F "model=claude-sonnet-4-20250514" \
  -F "dpi=300"

# Check status
curl $PDF_PIPELINE_URL/api/v1/jobs/{job_id}

# Download file
curl -O $PDF_PIPELINE_URL/api/v1/jobs/{job_id}/files/document.docx
```

---

## Required Environment Variables

### For This Service

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | **Yes** | - | Claude API key |
| `MONGODB_URI` | No | `mongodb://localhost:27017` | MongoDB connection |
| `MONGODB_DATABASE` | No | `pdftoxml` | Database name |
| `PDFTOXML_MODEL` | No | `claude-sonnet-4-20250514` | Default AI model |
| `PDFTOXML_DPI` | No | `300` | Default DPI |
| `PDFTOXML_MAX_CONCURRENT` | No | `3` | Max concurrent jobs |

### For External Services Calling This API

| Variable | Example | Description |
|----------|---------|-------------|
| `PDF_PIPELINE_URL` | `http://pdf-pipeline:8000` | This service's URL |

---

## Version History

| Version | Changes |
|---------|---------|
| 2.3.0 | Simplified flow: zip package created in initial conversion, no finalize step needed |
| 2.2.0 | Added MongoDB dashboard, auto-finalize on editor save |
| 2.1.0 | Added two-phase workflow with optional editor |
| 2.0.0 | Initial API release |
