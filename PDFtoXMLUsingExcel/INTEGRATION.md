# PDF-to-XML Pipeline - Microservices Integration Guide

## For Developer Agents (Claude, Cursor, etc.)

This document provides instructions for integrating with the PDF-to-XML conversion pipeline as a **microservice**. This service runs as an independent container and communicates with other services via REST APIs only.

> **Important:** Do NOT copy files from this repo into other projects. All communication is via HTTP APIs.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Service Discovery](#service-discovery)
3. [API Reference](#api-reference)
4. [Building the UI](#building-the-ui)
5. [Conversion Workflow](#conversion-workflow)
6. [Dashboard Integration](#dashboard-integration)
7. [Editor Integration](#editor-integration)
8. [Testing](#testing)
9. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

### Microservices Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         MICROSERVICES                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────────┐         ┌──────────────┐                             │
│  │  Fullstack   │  HTTP   │  PDF-to-XML  │                             │
│  │     UI       │ ◀─────▶ │   Pipeline   │                             │
│  │  Container   │  REST   │  Container   │                             │
│  └──────────────┘         └──────────────┘                             │
│         │                        │                                      │
│         │                        │                                      │
│         ▼                        ▼                                      │
│  ┌─────────────────────────────────────────┐                           │
│  │              MongoDB                     │                           │
│  │         (Shared Database)                │                           │
│  └─────────────────────────────────────────┘                           │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Key Principles

1. **No file sharing** - Services communicate via HTTP only
2. **Environment-based URLs** - Service URLs configured via environment variables
3. **API-first** - All functionality exposed via REST endpoints
4. **Independent deployment** - Each service can be deployed/scaled separately

---

## Service Discovery

### How to Connect to This Service

The UI (or any external service) should **never hardcode URLs**. Use environment variables:

```bash
# In your UI service's environment
PDF_PIPELINE_URL=http://pdf-pipeline:8000
```

### URL by Environment

| Environment | PDF_PIPELINE_URL |
|-------------|------------------|
| Local Dev | `http://localhost:8000` |
| Docker Compose | `http://pdf-pipeline:8000` |
| Kubernetes | `http://pdf-pipeline.default.svc:8000` |
| Staging | `https://pdf-api.stage.company.com` |
| Production | `https://pdf-api.company.com` |

### UI Service Configuration

```typescript
// In your UI project: src/config/services.ts
export const PDF_PIPELINE_URL = process.env.PDF_PIPELINE_URL || 'http://localhost:8000';

export const getPdfApiUrl = (endpoint: string): string => {
  return `${PDF_PIPELINE_URL}/api/v1${endpoint}`;
};
```

---

## API Reference

### Step 1: Fetch Configuration Options from API

The UI should **fetch dropdown options dynamically** from the API (not copy files):

```typescript
// Fetch all configuration options for building forms
const response = await fetch(getPdfApiUrl('/config/options'));
const { options, defaults } = await response.json();

// 'options' contains all dropdown definitions with labels
// 'defaults' contains default values
```

### Step 2: Review Available Endpoints

```bash
# View interactive API documentation
open ${PDF_PIPELINE_URL}/docs

# Get OpenAPI spec
curl ${PDF_PIPELINE_URL}/openapi.json

# Or view Swagger UI
open http://localhost:8000/docs
```

### Core Endpoints

#### Configuration Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/config/options` | Get all dropdown options for UI forms |
| `GET` | `/api/v1/config/schema` | Get JSON schema for validation |
| `GET` | `/api/v1/models` | List available AI models |
| `GET` | `/api/v1/info` | Get API info and capabilities |
| `GET` | `/api/v1/health` | Health check with MongoDB status |

#### Conversion Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/convert` | Upload PDF and start conversion |
| `GET` | `/api/v1/jobs` | List all jobs |
| `GET` | `/api/v1/jobs/{job_id}` | Get job status and details |
| `POST` | `/api/v1/jobs/{job_id}/editor` | Launch web editor (optional) |
| `DELETE` | `/api/v1/jobs/{job_id}/editor` | Stop web editor |
| `POST` | `/api/v1/jobs/{job_id}/finalize` | Re-run packaging (optional, rarely needed) |
| `GET` | `/api/v1/jobs/{job_id}/files` | List output files |
| `GET` | `/api/v1/jobs/{job_id}/files/{filename}` | Download file |

#### Dashboard Endpoints (MongoDB)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/mongodb/dashboard` | Get dashboard statistics |
| `GET` | `/api/v1/mongodb/conversions` | List all conversions (paginated) |
| `GET` | `/api/v1/mongodb/conversions/{job_id}` | Get conversion details |
| `GET` | `/api/v1/mongodb/stats/daily` | Daily stats for charts |
| `GET` | `/api/v1/mongodb/stats/publishers` | Stats by publisher |
| `POST` | `/api/v1/mongodb/sync-excel` | Sync Excel data to MongoDB |

---

## Configuration UI Implementation

### Step 1: Create Configuration Form Component

```typescript
// src/components/ConversionConfigForm.tsx
import React, { useState, useEffect } from 'react';
import {
  CONVERSION_CONFIG_OPTIONS,
  DEFAULT_CONVERSION_CONFIG,
  ConversionConfig,
  validateConfig
} from '../config/conversion-config';

interface Props {
  onConfigChange: (config: ConversionConfig) => void;
  initialConfig?: Partial<ConversionConfig>;
}

export function ConversionConfigForm({ onConfigChange, initialConfig }: Props) {
  const [config, setConfig] = useState<ConversionConfig>({
    ...DEFAULT_CONVERSION_CONFIG,
    ...initialConfig,
  });
  const [errors, setErrors] = useState<string[]>([]);

  useEffect(() => {
    const validationErrors = validateConfig(config);
    setErrors(validationErrors);
    if (validationErrors.length === 0) {
      onConfigChange(config);
    }
  }, [config, onConfigChange]);

  const handleChange = <K extends keyof ConversionConfig>(
    key: K,
    value: ConversionConfig[K]
  ) => {
    setConfig(prev => ({ ...prev, [key]: value }));
  };

  return (
    <div className="conversion-config-form">
      <h3>Conversion Settings</h3>

      {/* AI Model Dropdown */}
      <div className="form-group">
        <label>{CONVERSION_CONFIG_OPTIONS.model.label}</label>
        <select
          value={config.model}
          onChange={(e) => handleChange('model', e.target.value as any)}
        >
          {CONVERSION_CONFIG_OPTIONS.model.options.map(opt => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <small>{CONVERSION_CONFIG_OPTIONS.model.description}</small>
      </div>

      {/* DPI Dropdown */}
      <div className="form-group">
        <label>{CONVERSION_CONFIG_OPTIONS.dpi.label}</label>
        <select
          value={config.dpi}
          onChange={(e) => handleChange('dpi', parseInt(e.target.value) as any)}
        >
          {CONVERSION_CONFIG_OPTIONS.dpi.options.map(opt => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      {/* Temperature Dropdown */}
      <div className="form-group">
        <label>{CONVERSION_CONFIG_OPTIONS.temperature.label}</label>
        <select
          value={config.temperature}
          onChange={(e) => handleChange('temperature', parseFloat(e.target.value) as any)}
        >
          {CONVERSION_CONFIG_OPTIONS.temperature.options.map(opt => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      {/* Batch Size Dropdown */}
      <div className="form-group">
        <label>{CONVERSION_CONFIG_OPTIONS.batch_size.label}</label>
        <select
          value={config.batch_size}
          onChange={(e) => handleChange('batch_size', parseInt(e.target.value) as any)}
        >
          {CONVERSION_CONFIG_OPTIONS.batch_size.options.map(opt => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      {/* TOC Depth Dropdown */}
      <div className="form-group">
        <label>{CONVERSION_CONFIG_OPTIONS.toc_depth.label}</label>
        <select
          value={config.toc_depth}
          onChange={(e) => handleChange('toc_depth', parseInt(e.target.value) as any)}
        >
          {CONVERSION_CONFIG_OPTIONS.toc_depth.options.map(opt => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      {/* Template Type Dropdown */}
      <div className="form-group">
        <label>{CONVERSION_CONFIG_OPTIONS.template_type.label}</label>
        <select
          value={config.template_type}
          onChange={(e) => handleChange('template_type', e.target.value as any)}
        >
          {CONVERSION_CONFIG_OPTIONS.template_type.options.map(opt => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      {/* Checkboxes */}
      <div className="form-group checkboxes">
        <label>
          <input
            type="checkbox"
            checked={config.create_docx}
            onChange={(e) => handleChange('create_docx', e.target.checked)}
          />
          {CONVERSION_CONFIG_OPTIONS.create_docx.label}
        </label>

        <label>
          <input
            type="checkbox"
            checked={config.create_rittdoc}
            onChange={(e) => handleChange('create_rittdoc', e.target.checked)}
          />
          {CONVERSION_CONFIG_OPTIONS.create_rittdoc.label}
        </label>

        <label>
          <input
            type="checkbox"
            checked={config.include_toc}
            onChange={(e) => handleChange('include_toc', e.target.checked)}
          />
          {CONVERSION_CONFIG_OPTIONS.include_toc.label}
        </label>

        <label>
          <input
            type="checkbox"
            checked={config.skip_extraction}
            onChange={(e) => handleChange('skip_extraction', e.target.checked)}
          />
          {CONVERSION_CONFIG_OPTIONS.skip_extraction.label}
        </label>
      </div>

      {errors.length > 0 && (
        <div className="errors">
          {errors.map((err, i) => <p key={i} className="error">{err}</p>)}
        </div>
      )}
    </div>
  );
}
```

### Step 2: Alternative - Dynamic Form from API

If you prefer fetching options dynamically:

```typescript
// src/hooks/useConfigOptions.ts
import { useState, useEffect } from 'react';
import { getApiUrl } from '../config/api';

export function useConfigOptions() {
  const [options, setOptions] = useState(null);
  const [defaults, setDefaults] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch(getApiUrl('/config/options'))
      .then(res => res.json())
      .then(data => {
        setOptions(data.options);
        setDefaults(data.defaults);
        setLoading(false);
      })
      .catch(err => {
        setError(err);
        setLoading(false);
      });
  }, []);

  return { options, defaults, loading, error };
}
```

---

## Conversion Workflow Implementation

### Important: API Key is Server-Side Only

**The `ANTHROPIC_API_KEY` is configured on the PDF Pipeline server, NOT sent from the UI.**

The UI only sends these fields to `/api/v1/convert`:
- `file` (required) - The PDF file
- `model` - AI model (optional, has default)
- `dpi` - Resolution (optional, has default)
- `temperature` - AI temperature (optional, has default)
- `batch_size` - Pages per batch (optional, has default)
- `skip_extraction` - Skip images (optional, default false)
- `skip_rittdoc` - Skip packaging (optional, default false)

### Step 1: Create API Service

```typescript
// src/services/pipelineApi.ts
import { ConversionConfig, configToFormData } from '../config/conversion-config';
import { getApiUrl } from '../config/api';

export interface Job {
  job_id: string;
  status: 'pending' | 'processing' | 'extracting' | 'converting' |
          'packaging' | 'validating' | 'editing' | 'completed' | 'failed';
  progress: number;
  filename: string;
  error?: string;
  editor_url?: string;
  files?: string[];
}

export const pipelineApi = {
  // Start conversion
  async convert(file: File, config: ConversionConfig): Promise<Job> {
    const formData = configToFormData(config, file);
    const response = await fetch(getApiUrl('/convert'), {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  },

  // Get job status
  async getJob(jobId: string): Promise<Job> {
    const response = await fetch(getApiUrl(`/jobs/${jobId}`));
    if (!response.ok) throw new Error('Job not found');
    return response.json();
  },

  // Poll until completed
  async pollJob(jobId: string, intervalMs = 2000): Promise<Job> {
    while (true) {
      const job = await this.getJob(jobId);
      if (job.status === 'completed' || job.status === 'failed') {
        return job;
      }
      await new Promise(resolve => setTimeout(resolve, intervalMs));
    }
  },

  // Launch editor (optional - for making corrections after conversion)
  async launchEditor(jobId: string): Promise<{ editor_url: string }> {
    const response = await fetch(getApiUrl(`/jobs/${jobId}/editor`), {
      method: 'POST',
    });
    if (!response.ok) throw new Error('Failed to launch editor');
    return response.json();
  },

  // Stop editor
  async stopEditor(jobId: string): Promise<void> {
    await fetch(getApiUrl(`/jobs/${jobId}/editor`), {
      method: 'DELETE',
    });
  },

  // List output files
  async getFiles(jobId: string): Promise<{ files: Array<{ name: string; size: number; download_url: string }> }> {
    const response = await fetch(getApiUrl(`/jobs/${jobId}/files`));
    return response.json();
  },

  // Download file
  getDownloadUrl(jobId: string, filename: string): string {
    return getApiUrl(`/jobs/${jobId}/files/${filename}`);
  },
};
```

### Step 2: Create Conversion Component

```typescript
// src/components/ConversionFlow.tsx
import React, { useState, useCallback } from 'react';
import { ConversionConfigForm } from './ConversionConfigForm';
import { pipelineApi, Job } from '../services/pipelineApi';
import { ConversionConfig, DEFAULT_CONVERSION_CONFIG } from '../config/conversion-config';

type Step = 'upload' | 'configure' | 'processing' | 'complete' | 'editing';

export function ConversionFlow() {
  const [step, setStep] = useState<Step>('upload');
  const [file, setFile] = useState<File | null>(null);
  const [config, setConfig] = useState<ConversionConfig>(DEFAULT_CONVERSION_CONFIG);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0];
    if (selectedFile && selectedFile.type === 'application/pdf') {
      setFile(selectedFile);
      setStep('configure');
    }
  };

  const handleStartConversion = async () => {
    if (!file) return;

    try {
      setStep('processing');
      setError(null);

      // Start conversion
      const newJob = await pipelineApi.convert(file, config);
      setJob(newJob);

      // Poll until completed - zip package is created automatically
      const completedJob = await pipelineApi.pollJob(newJob.job_id);

      setJob(completedJob);

      if (completedJob.status === 'failed') {
        setError(completedJob.error || 'Conversion failed');
        setStep('upload');
      } else {
        // Conversion complete with zip package ready!
        setStep('complete');
      }
    } catch (err) {
      setError(err.message);
      setStep('upload');
    }
  };

  // Optional: Launch editor for corrections
  const handleLaunchEditor = async () => {
    if (!job) return;

    try {
      const { editor_url } = await pipelineApi.launchEditor(job.job_id);
      setStep('editing');
      // Open editor in new tab
      window.open(editor_url, '_blank');
    } catch (err) {
      setError(err.message);
    }
  };

  // Refresh files after editing (package is regenerated on save)
  const handleDoneEditing = async () => {
    if (!job) return;
    // Refresh job to get updated files
    const updatedJob = await pipelineApi.getJob(job.job_id);
    setJob(updatedJob);
    setStep('complete');
  };

  return (
    <div className="conversion-flow">
      {error && <div className="error-banner">{error}</div>}

      {step === 'upload' && (
        <div className="upload-step">
          <h2>Upload PDF</h2>
          <input
            type="file"
            accept=".pdf"
            onChange={handleFileSelect}
          />
        </div>
      )}

      {step === 'configure' && file && (
        <div className="configure-step">
          <h2>Configure Conversion: {file.name}</h2>
          <ConversionConfigForm
            onConfigChange={setConfig}
            initialConfig={config}
          />
          <button onClick={handleStartConversion}>
            Start Conversion
          </button>
        </div>
      )}

      {step === 'processing' && job && (
        <div className="processing-step">
          <h2>Processing...</h2>
          <p>Status: {job.status}</p>
          <progress value={job.progress} max="100" />
          <p>{job.progress}% complete</p>
        </div>
      )}

      {step === 'complete' && job && (
        <div className="complete-step">
          <h2>Conversion Complete!</h2>
          <p>Your files are ready for download.</p>
          <DownloadFiles jobId={job.job_id} />
          <div className="actions" style={{ marginTop: '1rem' }}>
            <button onClick={handleLaunchEditor}>
              Edit XML (Optional)
            </button>
          </div>
          <p className="hint">
            <small>If you edit and save, a new package will be generated with your changes.</small>
          </p>
        </div>
      )}

      {step === 'editing' && (
        <div className="editing-step">
          <h2>Editor Open</h2>
          <p>The editor is open in a new tab.</p>
          <p>When you save in the editor, a new package is generated automatically.</p>
          <button onClick={handleDoneEditing}>
            Done Editing
          </button>
        </div>
      )}
    </div>
  );
}

function DownloadFiles({ jobId }: { jobId: string }) {
  const [files, setFiles] = useState<any[]>([]);

  useEffect(() => {
    pipelineApi.getFiles(jobId).then(data => setFiles(data.files));
  }, [jobId]);

  return (
    <div className="download-files">
      <h3>Output Files</h3>
      <ul>
        {files.map(file => (
          <li key={file.name}>
            <a href={pipelineApi.getDownloadUrl(jobId, file.name)} download>
              {file.name} ({(file.size / 1024).toFixed(1)} KB)
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

---

## Dashboard Implementation

### Step 1: Create Dashboard Service

```typescript
// src/services/dashboardApi.ts
import { getApiUrl } from '../config/api';

export interface DashboardStats {
  total_conversions: number;
  successful: number;
  failed: number;
  in_progress: number;
  total_pages_processed: number;
  total_images_extracted: number;
  average_duration_seconds: number;
  conversions_today: number;
  conversions_this_week: number;
  by_status: Record<string, number>;
  by_type: Record<string, number>;
  recent_conversions: Array<{
    job_id: string;
    filename: string;
    status: string;
    created_at: string;
    duration_seconds?: number;
  }>;
}

export const dashboardApi = {
  async getStats(): Promise<DashboardStats> {
    const response = await fetch(getApiUrl('/mongodb/dashboard'));
    if (!response.ok) {
      // Fallback to in-memory dashboard if MongoDB not available
      const fallback = await fetch(getApiUrl('/dashboard'));
      return fallback.json();
    }
    return response.json();
  },

  async getDailyStats(days = 30): Promise<any[]> {
    const response = await fetch(getApiUrl(`/mongodb/stats/daily?days=${days}`));
    return response.json();
  },

  async getConversions(status?: string, limit = 50, skip = 0): Promise<any> {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    params.set('limit', limit.toString());
    params.set('skip', skip.toString());

    const response = await fetch(getApiUrl(`/mongodb/conversions?${params}`));
    return response.json();
  },
};
```

### Step 2: Create Dashboard Component

```typescript
// src/components/Dashboard.tsx
import React, { useState, useEffect } from 'react';
import { dashboardApi, DashboardStats } from '../services/dashboardApi';

export function Dashboard() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    dashboardApi.getStats()
      .then(setStats)
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div>Loading dashboard...</div>;
  if (!stats) return <div>Failed to load dashboard</div>;

  return (
    <div className="dashboard">
      <h1>Conversion Dashboard</h1>

      {/* Stats Cards */}
      <div className="stats-grid">
        <StatCard title="Total Conversions" value={stats.total_conversions} />
        <StatCard title="Successful" value={stats.successful} color="green" />
        <StatCard title="Failed" value={stats.failed} color="red" />
        <StatCard title="In Progress" value={stats.in_progress} color="blue" />
        <StatCard title="Today" value={stats.conversions_today} />
        <StatCard title="This Week" value={stats.conversions_this_week} />
        <StatCard
          title="Avg. Duration"
          value={`${stats.average_duration_seconds.toFixed(1)}s`}
        />
        <StatCard title="Pages Processed" value={stats.total_pages_processed} />
      </div>

      {/* Recent Conversions */}
      <div className="recent-conversions">
        <h2>Recent Conversions</h2>
        <table>
          <thead>
            <tr>
              <th>Filename</th>
              <th>Status</th>
              <th>Date</th>
              <th>Duration</th>
            </tr>
          </thead>
          <tbody>
            {stats.recent_conversions.map(conv => (
              <tr key={conv.job_id}>
                <td>{conv.filename}</td>
                <td>
                  <StatusBadge status={conv.status} />
                </td>
                <td>{new Date(conv.created_at).toLocaleString()}</td>
                <td>
                  {conv.duration_seconds
                    ? `${conv.duration_seconds.toFixed(1)}s`
                    : '-'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function StatCard({ title, value, color }: {
  title: string;
  value: number | string;
  color?: string;
}) {
  return (
    <div className={`stat-card ${color || ''}`}>
      <h3>{title}</h3>
      <p className="value">{value}</p>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    success: 'green',
    completed: 'green',
    failed: 'red',
    failure: 'red',
    in_progress: 'blue',
    processing: 'blue',
    pending: 'gray',
  };
  return (
    <span className={`status-badge ${colors[status] || 'gray'}`}>
      {status}
    </span>
  );
}
```

---

## Editor Integration

### Important Notes

1. The web editor runs as a separate Flask server on a dynamic port
2. Saving in the editor automatically triggers full finalization
3. The editor should be opened in a new tab/window

### Editor Flow

```typescript
// Launch editor
const { editor_url } = await pipelineApi.launchEditor(jobId);
// editor_url = "http://localhost:5100" (or similar port)

// Open in new tab
window.open(editor_url, '_blank');

// When user saves in editor:
// - Full finalization runs automatically
// - RittDoc package is created
// - DOCX is generated
// - Files are available via /api/v1/jobs/{job_id}/files
```

### Embedding Editor (iframe)

If you want to embed the editor instead of opening a new tab:

```typescript
// src/components/EmbeddedEditor.tsx
export function EmbeddedEditor({ editorUrl, onClose }: {
  editorUrl: string;
  onClose: () => void;
}) {
  return (
    <div className="editor-modal">
      <div className="editor-header">
        <h2>XML Editor</h2>
        <button onClick={onClose}>Close</button>
      </div>
      <iframe
        src={editorUrl}
        style={{ width: '100%', height: 'calc(100vh - 60px)', border: 'none' }}
        title="XML Editor"
      />
    </div>
  );
}
```

---

## Testing & Validation

### Step 1: Verify API Connectivity

```typescript
// src/utils/healthCheck.ts
export async function checkPipelineHealth(): Promise<{
  healthy: boolean;
  details: any;
}> {
  try {
    const response = await fetch(getApiUrl('/health'));
    const data = await response.json();
    return {
      healthy: data.status === 'healthy',
      details: data,
    };
  } catch (error) {
    return {
      healthy: false,
      details: { error: error.message },
    };
  }
}
```

### Step 2: Test Conversion Flow

```typescript
// Manual test sequence
async function testConversionFlow() {
  // 1. Check health
  const health = await checkPipelineHealth();
  console.log('Health:', health);

  // 2. Get config options
  const config = await fetch(getApiUrl('/config/options')).then(r => r.json());
  console.log('Config options:', config);

  // 3. Start a test conversion
  const formData = new FormData();
  formData.append('file', testPdfFile);
  formData.append('model', 'claude-sonnet-4-20250514');
  formData.append('dpi', '300');

  const job = await fetch(getApiUrl('/convert'), {
    method: 'POST',
    body: formData,
  }).then(r => r.json());
  console.log('Job started:', job);

  // 4. Poll for completion (zip package is created automatically)
  let status;
  do {
    await new Promise(r => setTimeout(r, 2000));
    status = await fetch(getApiUrl(`/jobs/${job.job_id}`)).then(r => r.json());
    console.log('Status:', status.status, status.progress);
  } while (!['completed', 'failed'].includes(status.status));

  // 5. Check files (zip package is ready immediately)
  const files = await fetch(getApiUrl(`/jobs/${job.job_id}/files`)).then(r => r.json());
  console.log('Files:', files);
}
```

---

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| CORS errors | Ensure API has CORS enabled for your UI origin |
| MongoDB connection failed | Check MONGODB_URI, MongoDB may be optional |
| Editor not opening | Check firewall, editor runs on dynamic port |
| Conversion stuck | Check ANTHROPIC_API_KEY is set correctly |
| Files not downloading | Ensure job is in 'completed' status |

### Debug Mode

Enable debug logging in the pipeline:

```bash
export PDFTOXML_DEBUG=true
uvicorn api:app --host 0.0.0.0 --port 8000 --log-level debug
```

---

## Developer Agent Prompt

Use this prompt when asking a developer agent to integrate the pipeline:

```
I need to integrate the PDF-to-XML conversion pipeline into my UI project.

The pipeline runs as an independent microservice. DO NOT copy files from the pipeline repo.

Please:
1. Set the environment variable: PDF_PIPELINE_URL={API_URL}
2. Create an API service to connect to the pipeline (see examples below)
3. Fetch configuration options dynamically: GET /api/v1/config/options
4. Review all available endpoints at {API_URL}/docs
5. Implement the conversion configuration form using fetched dropdown options
6. Implement the conversion workflow (upload → configure → convert → complete)
   - Zip package is created automatically, no separate finalize step needed
   - Optional: Add "Edit" button to launch editor for corrections
7. Implement the dashboard using MongoDB endpoints
8. Test the complete flow end-to-end

Environment URLs:
- Local:      PDF_PIPELINE_URL=http://localhost:8000
- Docker:     PDF_PIPELINE_URL=http://pdf-pipeline:8000
- Staging:    PDF_PIPELINE_URL=https://pdf-api.stage.company.com
- Production: PDF_PIPELINE_URL=https://pdf-api.company.com

See API_SPECIFICATION.md and ENVIRONMENT_CONFIG.md for full details.
```

---

## Quick Reference

### API Base URL
```
http://localhost:8000/api/v1
```

### Key Endpoints
```
POST /convert              - Start conversion (returns completed with zip)
GET  /jobs/{id}            - Get status
POST /jobs/{id}/editor     - Launch editor (optional, for corrections)
GET  /jobs/{id}/files      - List files (available immediately after completion)
GET  /config/options       - Dropdown options
GET  /mongodb/dashboard    - Dashboard stats
```

### Configuration Fields
```
model          - AI model (dropdown)
dpi            - Resolution (dropdown)
temperature    - AI temp (dropdown)
batch_size     - Batch size (dropdown)
toc_depth      - TOC depth (dropdown)
template_type  - Template (dropdown)
create_docx    - DOCX output (checkbox)
create_rittdoc - ZIP output (checkbox)
skip_extraction - Skip images (checkbox)
include_toc    - Include TOC (checkbox)
```

---

## Webhook Integration

The PDF Pipeline sends webhook notifications when conversions complete. This enables real-time updates without polling.

### Environment Configuration

```bash
# In the PDF Pipeline service
PDFTOXML_WEBHOOK_URL=http://demo-ui-backend:3001/api/files/webhook/complete
PDFTOXML_API_BASE_URL=http://ritt-pdf-api:8000
```

### Webhook Endpoint (Implement in Your UI Backend)

```typescript
// POST /api/files/webhook/complete
app.post('/api/files/webhook/complete', (req, res) => {
  const payload = req.body;
  // Handle the webhook payload
  console.log('Conversion completed:', payload.jobId);
  res.status(200).json({ received: true });
});
```

### Webhook Payload: Success

```json
{
  "jobId": "abc123-def456",
  "status": "completed",
  "fileType": "pdf",
  "filename": "document.pdf",
  "apiBaseUrl": "http://ritt-pdf-api:8000",
  "links": {
    "job": "http://ritt-pdf-api:8000/api/v1/jobs/abc123-def456",
    "files": "http://ritt-pdf-api:8000/api/v1/jobs/abc123-def456/files",
    "rittdocPackage": "http://ritt-pdf-api:8000/api/v1/jobs/abc123-def456/files/document_rittdoc.zip",
    "wordDocument": "http://ritt-pdf-api:8000/api/v1/jobs/abc123-def456/files/document.docx",
    "validationReport": "http://ritt-pdf-api:8000/api/v1/jobs/abc123-def456/files/document_validation_report.xlsx",
    "docbookXml": "http://ritt-pdf-api:8000/api/v1/jobs/abc123-def456/files/document_docbook42.xml"
  },
  "outputFiles": [
    {
      "name": "document_rittdoc.zip",
      "type": "rittdoc_package",
      "downloadUrl": "http://ritt-pdf-api:8000/api/v1/jobs/abc123-def456/files/document_rittdoc.zip"
    },
    {
      "name": "document.docx",
      "type": "word_document",
      "downloadUrl": "http://ritt-pdf-api:8000/api/v1/jobs/abc123-def456/files/document.docx"
    },
    {
      "name": "document_validation_report.xlsx",
      "type": "validation_report",
      "downloadUrl": "http://ritt-pdf-api:8000/api/v1/jobs/abc123-def456/files/document_validation_report.xlsx"
    },
    {
      "name": "document_docbook42.xml",
      "type": "docbook_xml",
      "downloadUrl": "http://ritt-pdf-api:8000/api/v1/jobs/abc123-def456/files/document_docbook42.xml"
    }
  ],
  "outputPackage": "document_rittdoc.zip"
}
```

### Webhook Payload: Failure

```json
{
  "jobId": "abc123-def456",
  "status": "failed",
  "fileType": "pdf",
  "filename": "document.pdf",
  "apiBaseUrl": "http://ritt-pdf-api:8000",
  "links": {
    "job": "http://ritt-pdf-api:8000/api/v1/jobs/abc123-def456",
    "files": "http://ritt-pdf-api:8000/api/v1/jobs/abc123-def456/files"
  },
  "error": "Conversion failed: Invalid PDF structure"
}
```

### File Types in Webhook

| Type | Description | Extension |
|------|-------------|-----------|
| `rittdoc_package` | Final validated RittDoc ZIP package | `*_rittdoc.zip` |
| `docbook_package` | Intermediate DocBook ZIP package | `*_docbook.zip` |
| `word_document` | Word document for review | `*.docx` |
| `validation_report` | Excel validation report | `*_validation_report.xlsx` |
| `docbook_xml` | DocBook 4.2 XML source | `*_docbook42.xml` |
| `xml` | Other XML files | `*.xml` |
| `other` | Other files (images, etc.) | `*` |

### Handling Webhooks in React/Next.js

```typescript
// pages/api/files/webhook/complete.ts (Next.js API Route)
import type { NextApiRequest, NextApiResponse } from 'next';

interface WebhookPayload {
  jobId: string;
  status: 'completed' | 'failed';
  fileType: string;
  filename?: string;
  apiBaseUrl: string;
  links: {
    job: string;
    files: string;
    rittdocPackage?: string;
    wordDocument?: string;
    validationReport?: string;
    docbookXml?: string;
  };
  outputFiles?: Array<{
    name: string;
    type: string;
    downloadUrl: string;
  }>;
  outputPackage?: string;
  error?: string;
}

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse
) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const payload: WebhookPayload = req.body;

  if (payload.status === 'completed') {
    // Update your database/state with the completed job
    console.log(`Job ${payload.jobId} completed successfully`);
    console.log(`Download package: ${payload.links.rittdocPackage}`);

    // Notify connected clients via WebSocket/SSE
    // await notifyClients(payload.jobId, 'completed', payload.links);

  } else if (payload.status === 'failed') {
    console.error(`Job ${payload.jobId} failed: ${payload.error}`);

    // Notify connected clients of failure
    // await notifyClients(payload.jobId, 'failed', { error: payload.error });
  }

  res.status(200).json({ received: true });
}
```

### Direct Download from Webhook URLs

```typescript
// Download file directly using webhook URL
async function downloadFile(downloadUrl: string, filename: string) {
  const response = await fetch(downloadUrl);
  const blob = await response.blob();

  // Create download link
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

// Usage with webhook payload
function handleWebhookPayload(payload: WebhookPayload) {
  if (payload.status === 'completed' && payload.links.rittdocPackage) {
    // Offer download button
    const packageFile = payload.outputFiles?.find(f => f.type === 'rittdoc_package');
    if (packageFile) {
      downloadFile(packageFile.downloadUrl, packageFile.name);
    }
  }
}
```

---

## File Download APIs

### List All Output Files

```
GET /api/v1/jobs/{job_id}/files
```

**Response:**
```json
{
  "files": [
    {
      "name": "document_rittdoc.zip",
      "size": 5242880,
      "download_url": "/api/v1/jobs/abc123/files/document_rittdoc.zip"
    },
    {
      "name": "document.docx",
      "size": 2097152,
      "download_url": "/api/v1/jobs/abc123/files/document.docx"
    },
    {
      "name": "document_validation_report.xlsx",
      "size": 51200,
      "download_url": "/api/v1/jobs/abc123/files/document_validation_report.xlsx"
    },
    {
      "name": "document_docbook42.xml",
      "size": 1048576,
      "download_url": "/api/v1/jobs/abc123/files/document_docbook42.xml"
    },
    {
      "name": "MultiMedia/image_0001.png",
      "size": 204800,
      "download_url": "/api/v1/jobs/abc123/files/MultiMedia/image_0001.png"
    }
  ]
}
```

### Download Individual File

```
GET /api/v1/jobs/{job_id}/files/{filename}
```

**Examples:**
```bash
# Download the final RittDoc package
curl -O http://localhost:8000/api/v1/jobs/abc123/files/document_rittdoc.zip

# Download the Word document
curl -O http://localhost:8000/api/v1/jobs/abc123/files/document.docx

# Download the validation report
curl -O http://localhost:8000/api/v1/jobs/abc123/files/document_validation_report.xlsx

# Download the DocBook XML
curl -O http://localhost:8000/api/v1/jobs/abc123/files/document_docbook42.xml

# Download a multimedia file
curl -O http://localhost:8000/api/v1/jobs/abc123/files/MultiMedia/image_0001.png
```

**Response:** Binary file with appropriate `Content-Type` header

### Output Files Summary

| File | Description | Available After |
|------|-------------|-----------------|
| `{name}_docbook42.xml` | DocBook 4.2 XML source | Completed |
| `{name}_MultiMedia/*` | Extracted images | Completed |
| `{name}_rittdoc.zip` | Final validated package | Completed |
| `{name}_docbook.zip` | Intermediate DocBook package | Completed |
| `{name}.docx` | Word document | Completed |
| `{name}_validation_report.xlsx` | Excel validation report | Completed |

**Note:** All files including the final zip package are available immediately when
status changes to `completed`. No separate finalize step is required.

---

## Docker Compose Configuration

```yaml
version: '3.8'

services:
  # PDF-to-XML Pipeline Service
  ritt-pdf-api:
    build: ./PDFtoXMLUsingExcel
    ports:
      - "8000:8000"
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - PDFTOXML_WEBHOOK_URL=http://demo-ui-backend:3001/api/files/webhook/complete
      - PDFTOXML_API_BASE_URL=http://ritt-pdf-api:8000
      - MONGODB_URI=mongodb://mongodb:27017/pdf_pipeline
    volumes:
      - pdf_uploads:/app/uploads
      - pdf_output:/app/output
    depends_on:
      - mongodb

  # Your UI Backend
  demo-ui-backend:
    build: ./demo-ui/backend
    ports:
      - "3001:3001"
    environment:
      - PDF_PIPELINE_URL=http://ritt-pdf-api:8000
      - MONGODB_URI=mongodb://mongodb:27017/demo_ui
    depends_on:
      - ritt-pdf-api
      - mongodb

  # Your UI Frontend
  demo-ui-frontend:
    build: ./demo-ui/frontend
    ports:
      - "3000:3000"
    environment:
      - NEXT_PUBLIC_API_URL=http://localhost:3001

  # Shared MongoDB
  mongodb:
    image: mongo:6
    ports:
      - "27017:27017"
    volumes:
      - mongodb_data:/data/db

volumes:
  pdf_uploads:
  pdf_output:
  mongodb_data:
```

---

## Complete API Reference for UI Integration

### Conversion Workflow APIs

| Method | Endpoint | Description | Request | Response |
|--------|----------|-------------|---------|----------|
| `POST` | `/api/v1/convert` | Upload PDF and start (zip created automatically) | `multipart/form-data` with `file` | `JobInfo` |
| `GET` | `/api/v1/jobs/{id}` | Get job status | - | `JobInfo` |
| `GET` | `/api/v1/jobs` | List all jobs | `?status=&limit=&skip=` | `JobInfo[]` |
| `DELETE` | `/api/v1/jobs/{id}` | Cancel/delete job | - | `{"success": true}` |

### Editor APIs (Optional - for corrections after conversion)

| Method | Endpoint | Description | Response |
|--------|----------|-------------|----------|
| `POST` | `/api/v1/jobs/{id}/editor` | Launch editor for corrections | `{"editor_url": "http://..."}` |
| `DELETE` | `/api/v1/jobs/{id}/editor` | Stop editor | `{"success": true}` |

**Note:** When user saves in the editor, a new zip package is generated automatically.

### File APIs

| Method | Endpoint | Description | Response |
|--------|----------|-------------|----------|
| `GET` | `/api/v1/jobs/{id}/files` | List output files | `{"files": [...]}` |
| `GET` | `/api/v1/jobs/{id}/files/{name}` | Download file | Binary file |

### Dashboard APIs

| Method | Endpoint | Description | Response |
|--------|----------|-------------|----------|
| `GET` | `/api/v1/dashboard` | In-memory stats | `DashboardStats` |
| `GET` | `/api/v1/mongodb/dashboard` | Persistent stats | `DashboardStats` |
| `GET` | `/api/v1/mongodb/conversions` | List conversions | Paginated list |
| `GET` | `/api/v1/mongodb/conversions/{id}` | Get conversion | Conversion document |
| `GET` | `/api/v1/mongodb/stats/daily` | Daily stats | Time series data |

### Configuration APIs

| Method | Endpoint | Description | Response |
|--------|----------|-------------|----------|
| `GET` | `/api/v1/config/options` | Dropdown options | Options + defaults |
| `GET` | `/api/v1/models` | Available AI models | Model list |
| `GET` | `/api/v1/health` | Health check | Status + MongoDB |
| `GET` | `/api/v1/info` | API info | Version + capabilities |

### Job Status Values

```typescript
type JobStatus =
  | 'pending'           // Just created, waiting to start
  | 'processing'        // Starting up
  | 'extracting'        // Extracting images/tables
  | 'converting'        // AI conversion in progress
  | 'packaging'         // Creating RittDoc package
  | 'validating'        // DTD validation
  | 'completed'         // All done, zip package ready for download
  | 'editing'           // Editor is running (optional step)
  | 'failed'            // Error occurred
  | 'cancelled';        // User cancelled
```

**Note:** The conversion now goes directly to `completed` with the zip package ready.
No separate finalize step is required. The `editing` status only occurs if the user
chooses to launch the editor for corrections.

