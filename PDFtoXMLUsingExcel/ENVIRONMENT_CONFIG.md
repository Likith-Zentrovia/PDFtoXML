# Environment Configuration Guide

## Overview

This document explains how to configure the PDF-to-XML pipeline service across different environments and how external services should connect to it.

---

## Service Configuration

### Required Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | **Yes** | Claude API key for AI processing |

### Optional Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MONGODB_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGODB_DATABASE` | `pdftoxml` | MongoDB database name |
| `MONGODB_COLLECTION` | `conversions` | MongoDB collection name |
| `PDFTOXML_MODEL` | `claude-sonnet-4-20250514` | Default AI model |
| `PDFTOXML_DPI` | `300` | Default rendering DPI |
| `PDFTOXML_TEMPERATURE` | `0.0` | AI temperature |
| `PDFTOXML_BATCH_SIZE` | `10` | Pages per batch |
| `PDFTOXML_MAX_CONCURRENT` | `3` | Max concurrent jobs |
| `PDFTOXML_OUTPUT_DIR` | `./output` | Output directory |
| `PDFTOXML_UPLOAD_DIR` | `./uploads` | Upload directory |
| `PDFTOXML_EDITOR_PORT_START` | `5100` | Editor port range start |
| `PDFTOXML_CLEANUP_TEMP` | `true` | Clean up temp files |
| `PDFTOXML_RETENTION_HOURS` | `24` | Result retention time |
| `PDFTOXML_WEBHOOK_URL` | `http://demo-ui-backend:3001/api/files/webhook/complete` | Webhook URL for completion notification |

---

## Environment-Specific Configurations

### Local Development

```bash
# .env.local
ANTHROPIC_API_KEY=sk-ant-...
MONGODB_URI=mongodb://localhost:27017
PDFTOXML_OUTPUT_DIR=./output
PDFTOXML_MAX_CONCURRENT=2
```

```bash
# Start service
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

### Docker Compose

```yaml
# docker-compose.yml
version: '3.8'

services:
  pdf-pipeline:
    build: .
    ports:
      - "8000:8000"
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - MONGODB_URI=mongodb://mongodb:27017
      - PDFTOXML_OUTPUT_DIR=/data/output
    volumes:
      - pdf_output:/data/output
    depends_on:
      - mongodb

  mongodb:
    image: mongo:7
    volumes:
      - mongo_data:/data/db

volumes:
  pdf_output:
  mongo_data:
```

### Kubernetes

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pdf-pipeline
spec:
  replicas: 2
  template:
    spec:
      containers:
        - name: pdf-pipeline
          image: pdf-pipeline:latest
          ports:
            - containerPort: 8000
          env:
            - name: ANTHROPIC_API_KEY
              valueFrom:
                secretKeyRef:
                  name: api-secrets
                  key: anthropic-key
            - name: MONGODB_URI
              value: mongodb://mongodb.database.svc:27017
          resources:
            requests:
              memory: "512Mi"
              cpu: "500m"
            limits:
              memory: "2Gi"
              cpu: "2000m"
---
apiVersion: v1
kind: Service
metadata:
  name: pdf-pipeline
spec:
  selector:
    app: pdf-pipeline
  ports:
    - port: 8000
      targetPort: 8000
```

### AWS ECS/Fargate

```json
{
  "containerDefinitions": [
    {
      "name": "pdf-pipeline",
      "image": "123456789.dkr.ecr.region.amazonaws.com/pdf-pipeline:latest",
      "portMappings": [{"containerPort": 8000}],
      "environment": [
        {"name": "MONGODB_URI", "value": "mongodb://docdb.cluster.region.docdb.amazonaws.com:27017"}
      ],
      "secrets": [
        {"name": "ANTHROPIC_API_KEY", "valueFrom": "arn:aws:secretsmanager:region:account:secret:anthropic-key"}
      ]
    }
  ]
}
```

---

## How External Services Connect

### Service Discovery Pattern

External services (like the UI) should **never hardcode URLs**. Instead:

```
┌─────────────────────────────────────────────────────────────────┐
│                    SERVICE DISCOVERY                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Option 1: Environment Variables (Simple)                      │
│  ─────────────────────────────────────────                      │
│  UI reads: PDF_PIPELINE_URL from environment                   │
│                                                                 │
│  Option 2: Service Mesh (Docker/K8s)                           │
│  ─────────────────────────────────────                          │
│  UI calls: http://pdf-pipeline:8000 (DNS resolves internally)  │
│                                                                 │
│  Option 3: API Gateway (Production)                            │
│  ─────────────────────────────────────                          │
│  UI calls: https://api.company.com/pdf/*                       │
│  Gateway routes to internal service                             │
│                                                                 │
│  Option 4: Service Registry (Consul/Eureka)                    │
│  ─────────────────────────────────────────────                  │
│  UI queries registry for pdf-pipeline endpoint                 │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Environment Variable Convention

All services should use this naming pattern:

```bash
# In UI service or any external service
PDF_PIPELINE_URL=http://pdf-pipeline:8000

# Usage in code
const PDF_PIPELINE_URL = process.env.PDF_PIPELINE_URL;
fetch(`${PDF_PIPELINE_URL}/api/v1/health`);
```

### URL Patterns by Environment

| Environment | URL Pattern | Example |
|-------------|-------------|---------|
| Local Dev | `http://localhost:{port}` | `http://localhost:8000` |
| Docker Compose | `http://{service-name}:{port}` | `http://pdf-pipeline:8000` |
| Kubernetes | `http://{service}.{namespace}.svc:{port}` | `http://pdf-pipeline.default.svc:8000` |
| Staging | `https://{service}.stage.{domain}` | `https://pdf-api.stage.company.com` |
| Production | `https://{service}.{domain}` | `https://pdf-api.company.com` |
| Customer AWS | `https://{service}.{customer}.{domain}` | `https://pdf-api.acme.company.com` |

---

## Multi-Environment Setup

### Environment Files

```
├── .env.local          # Local development
├── .env.docker         # Docker compose
├── .env.staging        # Staging environment
├── .env.production     # Production environment
└── .env.customer.acme  # Customer-specific
```

### Example .env Files

```bash
# .env.local
ANTHROPIC_API_KEY=sk-ant-local-key
MONGODB_URI=mongodb://localhost:27017
PDF_PIPELINE_URL=http://localhost:8000

# .env.staging
ANTHROPIC_API_KEY=sk-ant-staging-key
MONGODB_URI=mongodb://staging-mongo.internal:27017
PDF_PIPELINE_URL=https://pdf-api.stage.company.com

# .env.production
ANTHROPIC_API_KEY=sk-ant-prod-key
MONGODB_URI=mongodb+srv://prod-cluster.mongodb.net
PDF_PIPELINE_URL=https://pdf-api.company.com
```

### Loading Environment

```bash
# Local
source .env.local && uvicorn api:app

# Docker
docker-compose --env-file .env.docker up

# Kubernetes (use ConfigMaps/Secrets)
kubectl apply -f k8s/configmap-staging.yaml
```

---

## UI Service Configuration

The UI service needs to know where to find this pipeline:

```typescript
// src/config/services.ts
export const ServiceConfig = {
  PDF_PIPELINE: {
    url: process.env.PDF_PIPELINE_URL || 'http://localhost:8000',
    version: 'v1',
    timeout: 30000,
  },
  // Add other services as needed
  // EPUB_PIPELINE: {
  //   url: process.env.EPUB_PIPELINE_URL || 'http://localhost:8001',
  //   version: 'v1',
  // },
};

// Helper to build API URLs
export const getPdfPipelineUrl = (endpoint: string): string => {
  const base = ServiceConfig.PDF_PIPELINE.url;
  const version = ServiceConfig.PDF_PIPELINE.version;
  return `${base}/api/${version}${endpoint}`;
};
```

```typescript
// Usage in UI
import { getPdfPipelineUrl } from './config/services';

// Get config options
const options = await fetch(getPdfPipelineUrl('/config/options'));

// Start conversion
const job = await fetch(getPdfPipelineUrl('/convert'), {
  method: 'POST',
  body: formData
});
```

---

## Health Checks and Monitoring

### Verify Service Connectivity

```bash
# Check if service is reachable
curl -f $PDF_PIPELINE_URL/api/v1/health

# Expected response
{
  "status": "healthy",
  "mongodb_status": "connected",
  "mongodb_available": true
}
```

### Docker Health Check

```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/api/v1/health || exit 1
```

### Kubernetes Probes

```yaml
livenessProbe:
  httpGet:
    path: /api/v1/health
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 30

readinessProbe:
  httpGet:
    path: /api/v1/health
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 10
```

---

## Secrets Management

### Local Development
```bash
# Use .env file (git-ignored)
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
```

### Docker/Compose
```yaml
# docker-compose.yml
services:
  pdf-pipeline:
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}  # From shell env
    # OR use secrets file
    env_file:
      - .env.production
```

### Kubernetes
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: api-secrets
type: Opaque
stringData:
  anthropic-key: sk-ant-...
```

### AWS Secrets Manager
```bash
aws secretsmanager create-secret \
  --name pdf-pipeline/anthropic-key \
  --secret-string "sk-ant-..."
```

---

## Summary

1. **This service** configures itself via environment variables
2. **External services** get this service's URL from their environment
3. **Never hardcode URLs** - use environment variables or service discovery
4. **Each environment** has its own configuration file/settings
5. **Secrets** are managed separately from configuration
