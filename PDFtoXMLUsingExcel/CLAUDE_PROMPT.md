# Claude Integration Prompt for PDF Pipeline

Copy and paste the section below into your Claude conversation to integrate the PDF-to-XML conversion pipeline into your UI project.

---

## Prompt for Claude

```
I need to integrate with an external PDF-to-XML conversion microservice in my UI project.

## CRITICAL RULES

1. The PDF Pipeline runs as an INDEPENDENT CONTAINER - DO NOT copy any files from it
2. ALL communication is via REST API only
3. Use environment variables for service URLs - NEVER hardcode
4. Fetch configuration options from the API - DO NOT hardcode dropdown values
5. **ANTHROPIC_API_KEY is SERVER-SIDE ONLY** - The UI does NOT need it and should NOT send it

## API Key Configuration (Server-Side Only)

**IMPORTANT:** The Anthropic API key is configured on the PDF Pipeline SERVER, not in the UI.

- The UI only sends: `file`, `model`, `dpi`, `temperature`, `batch_size`, `skip_extraction`, `skip_rittdoc`
- The server uses its own `ANTHROPIC_API_KEY` environment variable
- The UI should NEVER ask users for or transmit the API key

## Service Configuration

Set this environment variable in your project:

```env
PDF_PIPELINE_URL=http://localhost:8000
```

Environment-specific URLs:
| Environment | URL |
|-------------|-----|
| Local Dev | http://localhost:8000 |
| Docker Compose | http://pdf-pipeline:8000 |
| Kubernetes | http://pdf-pipeline.default.svc:8000 |
| Staging | https://pdf-api.stage.company.com |
| Production | https://pdf-api.company.com |

## API Discovery

The service provides self-documenting APIs:

```typescript
// Interactive Swagger UI
GET ${PDF_PIPELINE_URL}/docs

// OpenAPI 3.0 JSON spec (for code generation)
GET ${PDF_PIPELINE_URL}/openapi.json

// Form dropdown options (FETCH THIS - don't hardcode)
GET ${PDF_PIPELINE_URL}/api/v1/config/options

// Health check
GET ${PDF_PIPELINE_URL}/api/v1/health
```

## Core API Endpoints

### Conversion Workflow

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /api/v1/convert | Upload PDF + config, returns job_id |
| GET | /api/v1/jobs/{job_id} | Poll job status (status, progress) |
| POST | /api/v1/jobs/{job_id}/editor | Launch web editor, returns editor_url |
| DELETE | /api/v1/jobs/{job_id}/editor | Stop editor session |
| POST | /api/v1/jobs/{job_id}/finalize | Finalize without editing |
| GET | /api/v1/jobs/{job_id}/files | List output files |
| GET | /api/v1/jobs/{job_id}/files/{name} | Download specific file |

### Configuration

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /api/v1/config/options | All dropdown options for forms |
| GET | /api/v1/config/schema | JSON Schema for validation |
| GET | /api/v1/models | Available AI models |

### Dashboard (MongoDB)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /api/v1/mongodb/dashboard | Dashboard statistics |
| GET | /api/v1/mongodb/conversions | Paginated conversion list |
| GET | /api/v1/mongodb/stats/daily | Daily stats for charts |
| GET | /api/v1/mongodb/stats/publishers | Stats by publisher |

## Job Status Values

```typescript
type JobStatus =
  | 'pending'           // Job created
  | 'processing'        // Starting
  | 'extracting'        // Extracting images
  | 'converting'        // AI conversion
  | 'ready_for_review'  // Can edit or finalize
  | 'editing'           // Editor is open
  | 'finalizing'        // Creating outputs
  | 'completed'         // Done - files available
  | 'failed';           // Error occurred
```

## Implementation Requirements

### 1. Create API Configuration

```typescript
// src/config/api.ts
export const PDF_PIPELINE_URL = process.env.PDF_PIPELINE_URL || 'http://localhost:8000';

export function getPipelineUrl(endpoint: string): string {
  return `${PDF_PIPELINE_URL}/api/v1${endpoint}`;
}
```

### 2. Create Pipeline API Service

```typescript
// src/services/pipelineApi.ts
import { getPipelineUrl } from '../config/api';

export interface Job {
  job_id: string;
  status: string;
  progress: number;
  filename: string;
  error?: string;
  editor_url?: string;
  can_edit: boolean;
  can_finalize: boolean;
  output_files: string[];
}

export interface ConfigOptions {
  options: Record<string, {
    label: string;
    description: string;
    options?: Array<{ value: string | number; label: string }>;
    default?: boolean;
  }>;
  defaults: Record<string, any>;
}

export const pipelineApi = {
  // Get configuration options for forms
  async getConfigOptions(): Promise<ConfigOptions> {
    const res = await fetch(getPipelineUrl('/config/options'));
    return res.json();
  },

  // Start conversion
  // IMPORTANT: Only send these fields - API rejects unknown fields
  async convert(file: File, config: {
    model?: string;
    dpi?: number;
    temperature?: number;
    batch_size?: number;
    skip_extraction?: boolean;
    skip_rittdoc?: boolean;
  }): Promise<Job> {
    const formData = new FormData();
    formData.append('file', file);

    // Only append API-accepted fields
    if (config.model) formData.append('model', config.model);
    if (config.dpi) formData.append('dpi', String(config.dpi));
    if (config.temperature !== undefined) formData.append('temperature', String(config.temperature));
    if (config.batch_size) formData.append('batch_size', String(config.batch_size));
    if (config.skip_extraction !== undefined) formData.append('skip_extraction', String(config.skip_extraction));
    if (config.skip_rittdoc !== undefined) formData.append('skip_rittdoc', String(config.skip_rittdoc));

    const res = await fetch(getPipelineUrl('/convert'), {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  // Get job status
  async getJob(jobId: string): Promise<Job> {
    const res = await fetch(getPipelineUrl(`/jobs/${jobId}`));
    if (!res.ok) throw new Error('Job not found');
    return res.json();
  },

  // Poll until target status
  async pollJob(jobId: string, targetStatuses: string[], interval = 2000): Promise<Job> {
    while (true) {
      const job = await this.getJob(jobId);
      if (targetStatuses.includes(job.status) || job.status === 'failed') {
        return job;
      }
      await new Promise(r => setTimeout(r, interval));
    }
  },

  // Launch editor
  async launchEditor(jobId: string): Promise<{ editor_url: string }> {
    const res = await fetch(getPipelineUrl(`/jobs/${jobId}/editor`), {
      method: 'POST',
    });
    if (!res.ok) throw new Error('Failed to launch editor');
    return res.json();
  },

  // Finalize without editor
  async finalize(jobId: string): Promise<Job> {
    const res = await fetch(getPipelineUrl(`/jobs/${jobId}/finalize`), {
      method: 'POST',
    });
    return res.json();
  },

  // List output files
  async getFiles(jobId: string): Promise<{ files: Array<{ name: string; size: number; download_url: string }> }> {
    const res = await fetch(getPipelineUrl(`/jobs/${jobId}/files`));
    return res.json();
  },

  // Get file download URL
  getDownloadUrl(jobId: string, filename: string): string {
    return getPipelineUrl(`/jobs/${jobId}/files/${filename}`);
  },

  // Dashboard stats
  async getDashboard(): Promise<any> {
    const res = await fetch(getPipelineUrl('/mongodb/dashboard'));
    if (!res.ok) {
      // Fallback to in-memory dashboard
      const fallback = await fetch(getPipelineUrl('/dashboard'));
      return fallback.json();
    }
    return res.json();
  },
};
```

### 3. Create Configuration Form Hook

```typescript
// src/hooks/useConfigOptions.ts
import { useState, useEffect } from 'react';
import { pipelineApi, ConfigOptions } from '../services/pipelineApi';

export function useConfigOptions() {
  const [options, setOptions] = useState<ConfigOptions | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    pipelineApi.getConfigOptions()
      .then(setOptions)
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  return { options, loading, error };
}
```

### 4. Build Configuration Form Component

```typescript
// src/components/ConversionConfigForm.tsx
import { useConfigOptions } from '../hooks/useConfigOptions';

export function ConversionConfigForm({ onSubmit }) {
  const { options, loading, error } = useConfigOptions();
  const [config, setConfig] = useState({});

  useEffect(() => {
    if (options?.defaults) {
      setConfig(options.defaults);
    }
  }, [options]);

  if (loading) return <div>Loading configuration...</div>;
  if (error) return <div>Error loading config: {error.message}</div>;
  if (!options) return null;

  const handleChange = (key: string, value: any) => {
    setConfig(prev => ({ ...prev, [key]: value }));
  };

  return (
    <form onSubmit={(e) => { e.preventDefault(); onSubmit(config); }}>
      {/* Dropdown fields */}
      {['model', 'dpi', 'temperature', 'batch_size', 'toc_depth', 'template_type'].map(key => {
        const opt = options.options[key];
        if (!opt?.options) return null;
        return (
          <div key={key} className="form-group">
            <label>{opt.label}</label>
            <select
              value={config[key] ?? ''}
              onChange={(e) => handleChange(key, e.target.value)}
            >
              {opt.options.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <small>{opt.description}</small>
          </div>
        );
      })}

      {/* Checkbox fields */}
      {['create_docx', 'create_rittdoc', 'include_toc', 'skip_extraction'].map(key => {
        const opt = options.options[key];
        if (!opt) return null;
        return (
          <div key={key} className="form-group">
            <label>
              <input
                type="checkbox"
                checked={config[key] ?? opt.default ?? false}
                onChange={(e) => handleChange(key, e.target.checked)}
              />
              {opt.label}
            </label>
          </div>
        );
      })}

      <button type="submit">Start Conversion</button>
    </form>
  );
}
```

### 5. Implement Conversion Flow

```typescript
// src/components/ConversionFlow.tsx
import { useState } from 'react';
import { pipelineApi, Job } from '../services/pipelineApi';
import { ConversionConfigForm } from './ConversionConfigForm';

type Step = 'upload' | 'config' | 'processing' | 'review' | 'complete';

export function ConversionFlow() {
  const [step, setStep] = useState<Step>('upload');
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f?.type === 'application/pdf') {
      setFile(f);
      setStep('config');
    }
  };

  const handleStartConversion = async (config: Record<string, any>) => {
    if (!file) return;
    try {
      setStep('processing');
      setError(null);

      // Start conversion
      const newJob = await pipelineApi.convert(file, config);
      setJob(newJob);

      // Poll until ready
      const readyJob = await pipelineApi.pollJob(
        newJob.job_id,
        ['ready_for_review', 'completed']
      );
      setJob(readyJob);

      if (readyJob.status === 'failed') {
        setError(readyJob.error || 'Conversion failed');
        setStep('upload');
      } else {
        setStep('review');
      }
    } catch (err) {
      setError(err.message);
      setStep('upload');
    }
  };

  const handleEdit = async () => {
    if (!job) return;
    try {
      const { editor_url } = await pipelineApi.launchEditor(job.job_id);
      window.open(editor_url, '_blank');
      // After editing, user saves in editor which auto-finalizes
    } catch (err) {
      setError(err.message);
    }
  };

  const handleFinalize = async () => {
    if (!job) return;
    try {
      setStep('processing');
      await pipelineApi.finalize(job.job_id);
      const completed = await pipelineApi.pollJob(job.job_id, ['completed']);
      setJob(completed);
      setStep('complete');
    } catch (err) {
      setError(err.message);
    }
  };

  // Render based on step...
}
```

### 6. Implement Dashboard

```typescript
// src/components/Dashboard.tsx
import { useState, useEffect } from 'react';
import { pipelineApi } from '../services/pipelineApi';

export function Dashboard() {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    pipelineApi.getDashboard().then(setStats);
  }, []);

  if (!stats) return <div>Loading...</div>;

  return (
    <div className="dashboard">
      <div className="stats-grid">
        <StatCard title="Total" value={stats.total_conversions} />
        <StatCard title="Successful" value={stats.successful} />
        <StatCard title="Failed" value={stats.failed} />
        <StatCard title="In Progress" value={stats.in_progress} />
      </div>
      {/* Add charts using stats.recent_conversions */}
    </div>
  );
}
```

## Files to Create

1. `src/config/api.ts` - API configuration with environment variable
2. `src/services/pipelineApi.ts` - API service functions
3. `src/hooks/useConfigOptions.ts` - Hook to fetch config options
4. `src/components/ConversionConfigForm.tsx` - Dynamic config form
5. `src/components/ConversionFlow.tsx` - Main conversion UI flow
6. `src/components/Dashboard.tsx` - Dashboard with stats

## Testing

1. Ensure PDF Pipeline is running: `curl http://localhost:8000/api/v1/health`
2. Verify config endpoint: `curl http://localhost:8000/api/v1/config/options`
3. Test conversion flow end-to-end
4. Verify file downloads work

## Important Notes

- NEVER hardcode dropdown values - always fetch from `/api/v1/config/options`
- Handle the `failed` status in all polling loops
- The editor auto-finalizes when user saves - no need to call finalize after editing
- Use MongoDB dashboard endpoints for persistent data
- Add proper error handling and loading states
```

---

## Quick Reference Card

Save this as a quick reference:

```
PDF Pipeline API Quick Reference
================================

Base URL: ${PDF_PIPELINE_URL}/api/v1

CONVERSION FLOW:
1. POST /convert (file + config) → { job_id }
2. GET /jobs/{id} (poll) → wait for "ready_for_review"
3. POST /jobs/{id}/editor → { editor_url } OR POST /jobs/{id}/finalize
4. GET /jobs/{id}/files → list files
5. GET /jobs/{id}/files/{name} → download

KEY ENDPOINTS:
- GET /config/options → dropdown values for forms
- GET /health → service status
- GET /docs → Swagger UI
- GET /mongodb/dashboard → persistent stats

JOB STATUSES:
pending → processing → extracting → converting → ready_for_review → [editing] → finalizing → completed
```
