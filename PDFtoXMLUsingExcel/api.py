#!/usr/bin/env python3
"""
PDF Conversion Pipeline REST API

This module provides a FastAPI-based REST API for integrating the PDF conversion
pipeline with external user interfaces. It supports:

- Uploading PDFs for conversion
- Tracking conversion progress
- Launching editor on demand (not blocking)
- Dashboard data for monitoring conversions

API Flow:
1. POST /api/v1/convert - Upload PDF and start conversion
2. GET /api/v1/jobs/{job_id} - Poll until status is "completed"
   - Conversion now produces the final zip package immediately
   - No separate finalization step required
3. (Optional) POST /api/v1/jobs/{job_id}/editor - Launch editor for corrections
   - When user saves in the editor, the package is regenerated automatically
   - Updated zip package and DOCX are created with the edits

Usage:
    # Start the API server
    uvicorn api:app --host 0.0.0.0 --port 8000

    # Or programmatically
    from api import create_app
    app = create_app()
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import requests
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

# Import rittdoc_core for tracking
try:
    from rittdoc_core import (
        ConversionMetadata,
        ConversionStatus,
        ConversionTracker,
        ConversionType,
        TemplateType,
    )
    TRACKING_AVAILABLE = True
except ImportError:
    TRACKING_AVAILABLE = False
    ConversionStatus = None

# Import MongoDB store for persistent dashboard
try:
    from mongodb_store import get_mongodb_store, init_mongodb, MongoDBStore
    MONGODB_AVAILABLE = True
except ImportError:
    MONGODB_AVAILABLE = False
    get_mongodb_store = None
    init_mongodb = None

# Import storage abstraction (GridFS/S3/Local)
try:
    from storage import get_storage, init_storage, StorageBackend
    STORAGE_AVAILABLE = True
except ImportError:
    STORAGE_AVAILABLE = False
    get_storage = None
    init_storage = None


# ============================================================================
# CONFIGURATION
# ============================================================================

class APIConfig:
    """API Configuration settings."""

    # Storage backend: "gridfs" (MongoDB), "s3" (AWS), or "local" (filesystem)
    STORAGE_BACKEND: str = os.environ.get("STORAGE_BACKEND", "gridfs")

    # Base directories (used for local storage fallback and temp files)
    UPLOAD_DIR: Path = Path(os.environ.get("PDFTOXML_UPLOAD_DIR", "./uploads"))
    OUTPUT_DIR: Path = Path(os.environ.get("PDFTOXML_OUTPUT_DIR", "./output"))
    TEMP_DIR: Path = Path(os.environ.get("PDFTOXML_TEMP_DIR", tempfile.gettempdir()))

    # Processing settings
    DEFAULT_MODEL: str = os.environ.get("PDFTOXML_MODEL", "claude-sonnet-4-20250514")
    DEFAULT_DPI: int = int(os.environ.get("PDFTOXML_DPI", "300"))
    DEFAULT_TEMPERATURE: float = float(os.environ.get("PDFTOXML_TEMPERATURE", "0.0"))
    DEFAULT_BATCH_SIZE: int = int(os.environ.get("PDFTOXML_BATCH_SIZE", "10"))
    MAX_CONCURRENT_JOBS: int = int(os.environ.get("PDFTOXML_MAX_CONCURRENT", "3"))

    # Editor settings
    EDITOR_PORT_START: int = int(os.environ.get("PDFTOXML_EDITOR_PORT_START", "5100"))

    # DTD configuration
    DTD_PATH: Path = Path(os.environ.get(
        "PDFTOXML_DTD_PATH",
        "RITTDOCdtd/v1.1/RittDocBook.dtd"
    ))

    # Cleanup settings
    CLEANUP_TEMP_FILES: bool = os.environ.get("PDFTOXML_CLEANUP_TEMP", "true").lower() == "true"
    CLEANUP_INTERMEDIATE_FILES: bool = os.environ.get("PDFTOXML_CLEANUP_INTERMEDIATE", "true").lower() == "true"
    RESULT_RETENTION_HOURS: int = int(os.environ.get("PDFTOXML_RETENTION_HOURS", "24"))

    # Webhook settings - notify UI backend when conversion completes
    WEBHOOK_URL: Optional[str] = os.environ.get("PDFTOXML_WEBHOOK_URL", "http://demo-ui-backend:3001/api/files/webhook/complete")

    # Base URL for this API server (used for constructing download URLs in webhooks)
    # Default uses Docker service name; override with PDFTOXML_API_BASE_URL for other setups
    API_BASE_URL: str = os.environ.get("PDFTOXML_API_BASE_URL", "http://pdf-api:8000")

    @classmethod
    def ensure_directories(cls):
        """Ensure all required directories exist."""
        cls.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        cls.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# OUTPUT FILE HELPERS
# ============================================================================

# File patterns to keep in output (all others are considered intermediate)
FINAL_OUTPUT_PATTERNS = [
    '_rittdoc.zip',      # Final RittDoc package
    '_docbook.zip',      # DocBook package (if no RittDoc)
    '.docx',             # Word document
    '_docbook42.xml',    # DocBook XML
    '_validation_report.xlsx',  # Validation report
    '_reference_mapping.json',  # Reference mapping
    'conversion_dashboard.xlsx',  # Conversion dashboard
]

# Patterns for intermediate files to clean up
INTERMEDIATE_PATTERNS = [
    '_intermediate',     # Intermediate markdown files
    '_batch',            # Batch processing files
    '_debug.md',         # Debug files
    'pre_fixes_',        # Pre-fix packages
]


def collect_output_files(output_dir: Path, cleanup_intermediate: bool = None) -> List[str]:
    """
    Collect final output files, filtering out intermediate files.

    Args:
        output_dir: Directory to scan for output files
        cleanup_intermediate: If True, delete intermediate files.
                            If None, uses APIConfig.CLEANUP_INTERMEDIATE_FILES

    Returns:
        List of final output filenames (not full paths)
    """
    if cleanup_intermediate is None:
        cleanup_intermediate = APIConfig.CLEANUP_INTERMEDIATE_FILES

    if not output_dir.exists():
        return []

    final_files = []
    intermediate_files = []

    for f in output_dir.iterdir():
        if not f.is_file():
            continue

        filename = f.name

        # Check if it's an intermediate file
        is_intermediate = any(pattern in filename for pattern in INTERMEDIATE_PATTERNS)

        # Check if it's a final output file
        is_final = any(filename.endswith(pattern) for pattern in FINAL_OUTPUT_PATTERNS)

        if is_intermediate:
            intermediate_files.append(f)
        elif is_final:
            final_files.append(filename)
        # Skip other files (like MultiMedia images - they're in the ZIP)

    # Optionally clean up intermediate files
    if cleanup_intermediate:
        for f in intermediate_files:
            try:
                f.unlink()
                print(f"  Cleaned up intermediate file: {f.name}")
            except Exception as e:
                print(f"  Warning: Could not delete {f.name}: {e}")

    return final_files


# ============================================================================
# MODELS
# ============================================================================

class JobStatus(str, Enum):
    """Conversion job status."""
    PENDING = "pending"
    PROCESSING = "processing"
    EXTRACTING = "extracting"
    CONVERTING = "converting"
    # New status: conversion done, ready for optional editing
    READY_FOR_REVIEW = "ready_for_review"
    # Editor is currently open
    EDITING = "editing"
    # Running final packaging (after optional edit)
    FINALIZING = "finalizing"
    VALIDATING = "validating"
    PACKAGING = "packaging"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ConversionOptions(BaseModel):
    """Options for PDF conversion."""
    model: str = Field(default=APIConfig.DEFAULT_MODEL, description="Claude model to use")
    dpi: int = Field(default=APIConfig.DEFAULT_DPI, ge=72, le=600, description="DPI for rendering")
    temperature: float = Field(default=0.0, ge=0.0, le=1.0, description="AI temperature (0 = no hallucinations)")
    batch_size: int = Field(default=APIConfig.DEFAULT_BATCH_SIZE, ge=1, le=50, description="Pages per batch")
    skip_extraction: bool = Field(default=False, description="Skip image extraction")
    skip_rittdoc: bool = Field(default=False, description="Skip RittDoc packaging in finalize step")


class JobInfo(BaseModel):
    """Information about a conversion job."""
    job_id: str
    status: JobStatus
    progress: float = Field(ge=0, le=100)
    filename: str
    created_at: str
    updated_at: str
    error: Optional[str] = None
    output_files: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    # Editor info
    editor_url: Optional[str] = None
    can_edit: bool = False
    can_finalize: bool = False


class EditorInfo(BaseModel):
    """Information about the editor session."""
    job_id: str
    editor_url: str
    pdf_path: str
    xml_path: str
    status: str


class FinalizeOptions(BaseModel):
    """Options for finalization step."""
    skip_rittdoc: bool = Field(default=False, description="Skip RittDoc packaging")
    skip_docx: bool = Field(default=False, description="Skip DOCX generation")


class ConversionResult(BaseModel):
    """Result of a conversion job."""
    job_id: str
    status: JobStatus
    output_dir: str
    files: List[str]
    metrics: Dict[str, Any]
    duration_seconds: float


class DashboardStats(BaseModel):
    """Dashboard statistics."""
    total_conversions: int = 0
    successful: int = 0
    failed: int = 0
    in_progress: int = 0
    ready_for_review: int = 0
    total_pages_processed: int = 0
    total_images_extracted: int = 0
    average_duration_seconds: float = 0.0
    recent_conversions: List[Dict[str, Any]] = Field(default_factory=list)


# ============================================================================
# JOB MANAGER
# ============================================================================

@dataclass
class ConversionJob:
    """Internal representation of a conversion job."""
    job_id: str
    filename: str
    pdf_path: Path
    output_dir: Path
    options: ConversionOptions
    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    output_files: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    # Editor tracking
    editor_port: Optional[int] = None
    editor_process: Optional[subprocess.Popen] = None
    xml_path: Optional[Path] = None
    multimedia_dir: Optional[Path] = None
    # Package editing (from finalized ZIP)
    package_zip_path: Optional[Path] = None  # Original ZIP being edited
    editor_xml_path: Optional[Path] = None   # Extracted XML in working dir
    editor_multimedia_path: Optional[Path] = None  # Extracted MultiMedia folder

    def to_info(self) -> JobInfo:
        """Convert to API model."""
        editor_url = None
        if self.editor_port and self.status == JobStatus.EDITING:
            editor_url = f"http://localhost:{self.editor_port}"

        can_edit = self.status == JobStatus.READY_FOR_REVIEW
        can_finalize = self.status in (JobStatus.READY_FOR_REVIEW, JobStatus.EDITING)

        return JobInfo(
            job_id=self.job_id,
            status=self.status,
            progress=self.progress,
            filename=self.filename,
            created_at=self.created_at.isoformat(),
            updated_at=self.updated_at.isoformat(),
            error=self.error,
            output_files=self.output_files,
            metrics=self.metrics,
            editor_url=editor_url,
            can_edit=can_edit,
            can_finalize=can_finalize,
        )


class JobManager:
    """Manages conversion jobs with file-based persistence."""

    # Jobs are persisted to this file
    JOBS_FILE = "jobs.json"

    def __init__(self):
        self.jobs: Dict[str, ConversionJob] = {}
        self.executor = ThreadPoolExecutor(max_workers=APIConfig.MAX_CONCURRENT_JOBS)
        self._next_editor_port = APIConfig.EDITOR_PORT_START
        self._jobs_file = APIConfig.UPLOAD_DIR / self.JOBS_FILE
        self._load_jobs()

    def _get_jobs_file_path(self) -> Path:
        """Get the path to the jobs persistence file."""
        APIConfig.ensure_directories()
        return APIConfig.UPLOAD_DIR / self.JOBS_FILE

    def _save_jobs(self):
        """Save jobs to JSON file for persistence across restarts."""
        try:
            jobs_data = []
            for job_id, job in self.jobs.items():
                job_data = {
                    "job_id": job.job_id,
                    "filename": job.filename,
                    "pdf_path": str(job.pdf_path),
                    "output_dir": str(job.output_dir),
                    "status": job.status.value,
                    "progress": job.progress,
                    "created_at": job.created_at.isoformat(),
                    "updated_at": job.updated_at.isoformat(),
                    "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                    "error": job.error,
                    "output_files": job.output_files,
                    "metrics": job.metrics,
                    "xml_path": str(job.xml_path) if job.xml_path else None,
                    "multimedia_dir": str(job.multimedia_dir) if job.multimedia_dir else None,
                    "editor_port": job.editor_port,
                    # Options
                    "options": {
                        "model": job.options.model,
                        "dpi": job.options.dpi,
                        "temperature": job.options.temperature,
                        "batch_size": job.options.batch_size,
                        "skip_extraction": job.options.skip_extraction,
                        "skip_rittdoc": job.options.skip_rittdoc,
                    }
                }
                jobs_data.append(job_data)

            jobs_file = self._get_jobs_file_path()
            with open(jobs_file, 'w') as f:
                json.dump(jobs_data, f, indent=2)

        except Exception as e:
            print(f"Warning: Failed to save jobs to file: {e}")

    def _load_jobs(self):
        """Load jobs from JSON file on startup."""
        jobs_file = self._get_jobs_file_path()
        if not jobs_file.exists():
            return

        try:
            with open(jobs_file, 'r') as f:
                jobs_data = json.load(f)

            for job_data in jobs_data:
                options = ConversionOptions(
                    model=job_data["options"]["model"],
                    dpi=job_data["options"]["dpi"],
                    temperature=job_data["options"]["temperature"],
                    batch_size=job_data["options"]["batch_size"],
                    skip_extraction=job_data["options"].get("skip_extraction", False),
                    skip_rittdoc=job_data["options"].get("skip_rittdoc", False),
                )

                job = ConversionJob(
                    job_id=job_data["job_id"],
                    filename=job_data["filename"],
                    pdf_path=Path(job_data["pdf_path"]),
                    output_dir=Path(job_data["output_dir"]),
                    options=options,
                    status=JobStatus(job_data["status"]),
                    progress=job_data["progress"],
                    created_at=datetime.fromisoformat(job_data["created_at"]),
                    updated_at=datetime.fromisoformat(job_data["updated_at"]),
                    completed_at=datetime.fromisoformat(job_data["completed_at"]) if job_data["completed_at"] else None,
                    error=job_data.get("error"),
                    output_files=job_data.get("output_files", []),
                    metrics=job_data.get("metrics", {}),
                    xml_path=Path(job_data["xml_path"]) if job_data.get("xml_path") else None,
                    multimedia_dir=Path(job_data["multimedia_dir"]) if job_data.get("multimedia_dir") else None,
                    editor_port=job_data.get("editor_port"),
                    editor_process=None,  # Cannot restore process handles
                )

                # Reset editing status - editor processes don't survive restart
                if job.status == JobStatus.EDITING:
                    job.status = JobStatus.COMPLETED
                    job.editor_port = None

                # Reset in-progress jobs that were interrupted
                if job.status in (JobStatus.PROCESSING, JobStatus.CONVERTING,
                                   JobStatus.EXTRACTING, JobStatus.VALIDATING,
                                   JobStatus.PACKAGING, JobStatus.FINALIZING):
                    job.status = JobStatus.FAILED
                    job.error = "Job interrupted by server restart"

                self.jobs[job.job_id] = job

            print(f"Loaded {len(self.jobs)} jobs from persistence file")

        except Exception as e:
            print(f"Warning: Failed to load jobs from file: {e}")

    def _get_next_editor_port(self) -> int:
        """Get next available editor port."""
        port = self._next_editor_port
        self._next_editor_port += 1
        if self._next_editor_port > 5200:  # Reset after 100 ports
            self._next_editor_port = APIConfig.EDITOR_PORT_START
        return port

    def _extract_isbn_from_filename(self, filename: str) -> str:
        """
        Extract ISBN from filename.

        Examples:
            9798275082845.pdf -> 9798275082845
            9798275082845_something.pdf -> 9798275082845
            my-book-9798275082845.pdf -> 9798275082845
        """
        import re
        # Remove extension
        stem = Path(filename).stem

        # Try to find ISBN-13 (starts with 978 or 979) or ISBN-10
        isbn_pattern = r'(97[89]\d{10}|\d{9}[\dXx])'
        match = re.search(isbn_pattern, stem)

        if match:
            return match.group(1)

        # If no ISBN found, use the stem as-is (cleaned of special chars)
        # This handles cases where filename might not contain a standard ISBN
        clean_stem = re.sub(r'[^\w\-]', '', stem)
        return clean_stem if clean_stem else str(uuid.uuid4())[:8]

    def create_job(
        self,
        filename: str,
        pdf_path: Path,
        output_dir: Path,
        options: ConversionOptions
    ) -> ConversionJob:
        """
        Create a new conversion job using ISBN as job ID.

        The ISBN is extracted from the filename and used as the job identifier,
        making it easy to track and reference jobs by ISBN.
        """
        # Extract ISBN from filename to use as job ID
        job_id = self._extract_isbn_from_filename(filename)

        # If job with this ISBN already exists, check its status
        existing_job = self.jobs.get(job_id)
        if existing_job:
            # If existing job is still processing, return it
            if existing_job.status in (JobStatus.PROCESSING, JobStatus.CONVERTING,
                                        JobStatus.EXTRACTING, JobStatus.VALIDATING,
                                        JobStatus.PACKAGING, JobStatus.FINALIZING):
                return existing_job
            # Otherwise, we'll replace the old job with a new conversion

        job = ConversionJob(
            job_id=job_id,
            filename=filename,
            pdf_path=pdf_path,
            output_dir=output_dir,
            options=options,
        )
        self.jobs[job_id] = job
        self._save_jobs()  # Persist to file
        return job

    def get_job(self, job_id: str) -> Optional[ConversionJob]:
        """Get a job by ID."""
        return self.jobs.get(job_id)

    def update_job(
        self,
        job_id: str,
        status: Optional[JobStatus] = None,
        progress: Optional[float] = None,
        error: Optional[str] = None,
        output_files: Optional[List[str]] = None,
        metrics: Optional[Dict[str, Any]] = None,
        xml_path: Optional[Path] = None,
        multimedia_dir: Optional[Path] = None,
    ):
        """Update job status."""
        job = self.jobs.get(job_id)
        if job:
            if status:
                job.status = status
            if progress is not None:
                job.progress = progress
            if error:
                job.error = error
            if output_files:
                job.output_files = output_files
            if metrics:
                job.metrics.update(metrics)
            if xml_path:
                job.xml_path = xml_path
            if multimedia_dir:
                job.multimedia_dir = multimedia_dir
            job.updated_at = datetime.now()
            if status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                job.completed_at = datetime.now()
                # Auto-push to MongoDB on job completion
                self._push_to_mongodb(job)
            # Persist to file after each update
            self._save_jobs()

    def list_jobs(
        self,
        status: Optional[JobStatus] = None,
        limit: int = 50
    ) -> List[ConversionJob]:
        """List jobs, optionally filtered by status."""
        jobs = list(self.jobs.values())
        if status:
            jobs = [j for j in jobs if j.status == status]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def get_dashboard_stats(self) -> DashboardStats:
        """Get dashboard statistics."""
        jobs = list(self.jobs.values())

        completed_jobs = [j for j in jobs if j.status == JobStatus.COMPLETED]
        failed_jobs = [j for j in jobs if j.status == JobStatus.FAILED]
        ready_for_review = [j for j in jobs if j.status == JobStatus.READY_FOR_REVIEW]
        in_progress = [j for j in jobs if j.status in (
            JobStatus.PROCESSING, JobStatus.EXTRACTING,
            JobStatus.CONVERTING, JobStatus.VALIDATING,
            JobStatus.PACKAGING, JobStatus.FINALIZING,
            JobStatus.EDITING
        )]

        # Calculate metrics
        total_pages = sum(j.metrics.get("pages", 0) for j in jobs)
        total_images = sum(j.metrics.get("images", 0) for j in jobs)

        durations = []
        for j in completed_jobs:
            if j.completed_at and j.created_at:
                durations.append((j.completed_at - j.created_at).total_seconds())

        avg_duration = sum(durations) / len(durations) if durations else 0.0

        # Recent conversions
        recent = [
            {
                "job_id": j.job_id,
                "filename": j.filename,
                "status": j.status.value,
                "created_at": j.created_at.isoformat(),
                "duration": (j.completed_at - j.created_at).total_seconds() if j.completed_at else None,
            }
            for j in sorted(jobs, key=lambda x: x.created_at, reverse=True)[:10]
        ]

        return DashboardStats(
            total_conversions=len(jobs),
            successful=len(completed_jobs),
            failed=len(failed_jobs),
            in_progress=len(in_progress),
            ready_for_review=len(ready_for_review),
            total_pages_processed=total_pages,
            total_images_extracted=total_images,
            average_duration_seconds=avg_duration,
            recent_conversions=recent,
        )

    def _push_to_mongodb(self, job: ConversionJob):
        """
        Push job data to MongoDB on completion.

        Called automatically when job status changes to COMPLETED, FAILED, or CANCELLED.
        The job_id (which is the ISBN) is used as the unique identifier in MongoDB.
        """
        if not MONGODB_AVAILABLE:
            return

        try:
            store = get_mongodb_store()

            # Calculate duration
            duration = None
            if job.completed_at and job.created_at:
                duration = (job.completed_at - job.created_at).total_seconds()

            # Build document - job_id is the ISBN
            data = {
                "_id": job.job_id,  # Use ISBN as MongoDB document ID
                "isbn": job.job_id,  # Explicit ISBN field for queries
                "job_id": job.job_id,  # Keep for backwards compatibility
                "filename": job.filename,
                "status": job.status.value if hasattr(job.status, 'value') else str(job.status),
                "progress_percent": int(job.progress),
                "error_message": job.error,
                "start_time": job.created_at,
                "end_time": job.completed_at,
                "duration_seconds": duration,
                "conversion_type": "PDF",
                "num_pages": job.metrics.get("pages", 0),
                "num_raster_images": job.metrics.get("images", 0),
                "num_tables": job.metrics.get("tables", 0),
                "output_path": str(job.output_dir) if job.output_dir else None,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
            }

            # Add output file paths
            for f in job.output_files:
                if f.endswith('.docx'):
                    data["docx_path"] = f
                elif f.endswith('.zip'):
                    data["package_path"] = f

            store.push_conversion(data)

        except Exception as e:
            # Don't fail the job if MongoDB push fails
            import logging
            logging.getLogger(__name__).warning(f"Failed to push to MongoDB: {e}")


# Global job manager instance
job_manager = JobManager()


# ============================================================================
# CONVERSION WORKERS
# ============================================================================

def run_initial_conversion(job: ConversionJob):
    """
    Run the complete conversion pipeline including packaging.

    This runs:
    - Image/table extraction
    - Claude Vision AI conversion
    - Font analysis
    - TOC generation
    - RittDoc packaging and validation
    - DOCX generation

    After completion, status is set to COMPLETED with final zip package ready.
    If user later edits in the editor, the package will be regenerated on save.
    """
    try:
        job_manager.update_job(job.job_id, status=JobStatus.PROCESSING, progress=5)

        # Build command with --api-mode flag
        cmd = [
            sys.executable,
            str(Path(__file__).parent / "pdf_orchestrator.py"),
            str(job.pdf_path),
            "--out", str(job.output_dir),
            "--model", job.options.model,
            "--dpi", str(job.options.dpi),
            "--temperature", str(job.options.temperature),
            "--batch-size", str(job.options.batch_size),
            "--api-mode",  # Skip editor - we'll run packaging here
        ]

        if job.options.skip_extraction:
            cmd.append("--skip-extraction")

        # Run the conversion
        job_manager.update_job(job.job_id, status=JobStatus.CONVERTING, progress=20)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent),
        )

        if result.returncode != 0:
            raise RuntimeError(f"Conversion failed: {result.stderr}")

        # Find the produced XML and multimedia directory
        pdf_stem = job.pdf_path.stem
        xml_candidates = sorted(job.output_dir.glob(f"{pdf_stem}*_docbook42.xml"))
        if not xml_candidates:
            xml_candidates = sorted(job.output_dir.glob("*.xml"))

        xml_path = xml_candidates[0] if xml_candidates else None
        multimedia_dir = job.output_dir / f"{pdf_stem}_MultiMedia"

        # Update job with xml_path and multimedia_dir for later use
        job_manager.update_job(
            job.job_id,
            xml_path=xml_path,
            multimedia_dir=multimedia_dir,
        )

        # Count images in multimedia folder
        image_count = 0
        if multimedia_dir.exists():
            image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.svg', '.tif', '.tiff'}
            image_count = sum(1 for f in multimedia_dir.iterdir()
                            if f.is_file() and f.suffix.lower() in image_extensions)

        # Run packaging immediately (bypassing finalize step)
        if xml_path and xml_path.exists():
            job_manager.update_job(job.job_id, status=JobStatus.PACKAGING, progress=60)

            dtd_path = Path(__file__).parent / APIConfig.DTD_PATH
            if dtd_path.exists():
                try:
                    from lxml import etree
                    from rittdoc_compliance_pipeline import RittDocCompliancePipeline
                    from package import (
                        BOOK_DOCTYPE_SYSTEM_DEFAULT,
                        package_docbook,
                        make_file_fetcher,
                    )

                    # Parse the XML
                    root = etree.parse(str(xml_path)).getroot()

                    # Create media fetcher
                    search_paths = []
                    if multimedia_dir and multimedia_dir.exists():
                        search_paths.append(multimedia_dir)
                        shared_images = multimedia_dir / "SharedImages"
                        if shared_images.exists():
                            search_paths.append(shared_images)
                    search_paths.append(job.output_dir)

                    # Try to load reference mapper
                    reference_mapper = None
                    mapper_path = job.output_dir / f"{pdf_stem}_reference_mapping_phase1.json"
                    if mapper_path.exists():
                        try:
                            from reference_mapper import ReferenceMapper
                            reference_mapper = ReferenceMapper()
                            reference_mapper.import_from_json(mapper_path)
                        except Exception:
                            pass

                    media_fetcher = make_file_fetcher(search_paths, reference_mapper)

                    # Create intermediate DocBook package
                    intermediate_zip = job.output_dir / f"{pdf_stem}_docbook.zip"
                    package_docbook(
                        root=root,
                        root_name=(root.tag.split('}', 1)[-1] if root.tag.startswith('{') else root.tag),
                        dtd_system=BOOK_DOCTYPE_SYSTEM_DEFAULT,
                        zip_path=str(intermediate_zip),
                        processing_instructions=[],
                        assets=[],
                        media_fetcher=media_fetcher,
                        book_doctype_system=BOOK_DOCTYPE_SYSTEM_DEFAULT,
                        metadata_dir=job.output_dir,
                    )

                    # Apply RittDoc compliance
                    job_manager.update_job(job.job_id, status=JobStatus.VALIDATING, progress=75)
                    out_rittdoc_zip = job.output_dir / f"{pdf_stem}_rittdoc.zip"
                    pipeline = RittDocCompliancePipeline(dtd_path)
                    pipeline.run(
                        input_zip=intermediate_zip,
                        output_zip=out_rittdoc_zip,
                        max_iterations=3
                    )

                except Exception as e:
                    # Log but don't fail - continue to DOCX
                    print(f"RittDoc packaging warning: {e}")

            # Run DOCX conversion
            job_manager.update_job(job.job_id, progress=90)

            out_docx = job.output_dir / f"{pdf_stem}.docx"
            resource_path = f"{multimedia_dir}:{job.output_dir}" if multimedia_dir else str(job.output_dir)

            pandoc_cmd = [
                "pandoc",
                "-f", "docbook",
                "-t", "docx",
                "--toc",
                "--toc-depth=3",
                f"--resource-path={resource_path}",
                "-o", str(out_docx),
                str(xml_path),
            ]

            try:
                subprocess.run(
                    pandoc_cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                    cwd=str(Path(__file__).parent),
                )
            except subprocess.CalledProcessError as e:
                print(f"DOCX generation warning: {e.stderr}")
            except FileNotFoundError:
                print("DOCX generation warning: pandoc not found")

        # Collect final output files (filters out intermediate files and optionally cleans up)
        output_files = collect_output_files(job.output_dir)

        # Upload all output files to storage (GridFS/S3)
        if STORAGE_AVAILABLE:
            storage = get_storage()
            isbn = job.job_id  # job_id is the ISBN

            for filename in output_files:
                file_path = job.output_dir / filename
                if file_path.exists():
                    storage.upload_from_path(
                        isbn=isbn,
                        file_path=file_path,
                        metadata={"type": "output", "job_id": job.job_id}
                    )

            # Also upload images from multimedia directory
            if multimedia_dir and multimedia_dir.exists():
                for img_file in multimedia_dir.iterdir():
                    if img_file.is_file():
                        storage.upload_from_path(
                            isbn=isbn,
                            file_path=img_file,
                            target_filename=f"MultiMedia/{img_file.name}",
                            metadata={"type": "image", "job_id": job.job_id}
                        )

            print(f"  Uploaded {len(output_files)} files to storage for ISBN: {isbn}")

        # Calculate metrics
        metrics = {
            "pages": 0,  # Would be extracted from conversion output
            "images": image_count,
            "has_xml": xml_path is not None,
            "has_docx": any(f.endswith('.docx') for f in output_files),
            "has_zip": any(f.endswith('.zip') for f in output_files),
            "phase": "complete",
        }

        job_manager.update_job(
            job.job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            output_files=output_files,
            metrics=metrics,
            xml_path=xml_path,
            multimedia_dir=multimedia_dir,
        )

        # Send completion webhook
        updated_job = job_manager.get_job(job.job_id)
        if updated_job:
            send_completion_webhook(updated_job, 'completed')
        else:
            # Refresh job object with updated files
            job.output_files = output_files
            job.metrics = metrics
            send_completion_webhook(job, 'completed')

    except Exception as e:
        job_manager.update_job(
            job.job_id,
            status=JobStatus.FAILED,
            error=str(e),
        )
        send_completion_webhook(job, 'failed', error=str(e))


def send_completion_webhook(job: ConversionJob, status: str, error: Optional[str] = None):
    """
    Send webhook notification to UI backend when conversion completes.

    Args:
        job: The conversion job
        status: 'completed' or 'failed'
        error: Error message if failed
    """
    if not APIConfig.WEBHOOK_URL:
        return

    try:
        base_url = APIConfig.API_BASE_URL.rstrip('/')
        job_files_base = f"{base_url}/api/v1/jobs/{job.job_id}/files"

        payload = {
            'jobId': job.job_id,
            'status': status,
            'fileType': 'pdf',
            'filename': job.filename,
            # Include API base URL for constructing additional URLs
            'apiBaseUrl': base_url,
            # Direct links to common endpoints
            'links': {
                'job': f"{base_url}/api/v1/jobs/{job.job_id}",
                'files': job_files_base,
            }
        }

        if status == 'completed':
            output_files = job.output_files or []

            # Build detailed file list with download URLs
            files_with_urls = []
            for filename in output_files:
                file_info = {
                    'name': filename,
                    'downloadUrl': f"{job_files_base}/{filename}",
                }
                # Categorize by file type
                if filename.endswith('_rittdoc.zip'):
                    file_info['type'] = 'rittdoc_package'
                elif filename.endswith('_docbook.zip'):
                    file_info['type'] = 'docbook_package'
                elif filename.endswith('.docx'):
                    file_info['type'] = 'word_document'
                elif filename.endswith('_validation_report.xlsx'):
                    file_info['type'] = 'validation_report'
                elif filename.endswith('_docbook42.xml'):
                    file_info['type'] = 'docbook_xml'
                elif filename.endswith('.xml'):
                    file_info['type'] = 'xml'
                else:
                    file_info['type'] = 'other'
                files_with_urls.append(file_info)

            payload['outputFiles'] = files_with_urls

            # Find and include direct links to key files
            zip_files = [f for f in output_files if f.endswith('_rittdoc.zip')]
            docx_files = [f for f in output_files if f.endswith('.docx')]
            xlsx_files = [f for f in output_files if f.endswith('_validation_report.xlsx')]
            xml_files = [f for f in output_files if f.endswith('_docbook42.xml')]

            if zip_files:
                payload['links']['rittdocPackage'] = f"{job_files_base}/{zip_files[0]}"
                payload['outputPackage'] = zip_files[0]
            if docx_files:
                payload['links']['wordDocument'] = f"{job_files_base}/{docx_files[0]}"
            if xlsx_files:
                payload['links']['validationReport'] = f"{job_files_base}/{xlsx_files[0]}"
            if xml_files:
                payload['links']['docbookXml'] = f"{job_files_base}/{xml_files[0]}"

        elif error:
            payload['error'] = error

        response = requests.post(
            APIConfig.WEBHOOK_URL,
            json=payload,
            timeout=10
        )

        if response.ok:
            print(f"  Webhook sent successfully: {status} for job {job.job_id}")
        else:
            print(f"  Webhook failed ({response.status_code}): {response.text}")

    except requests.exceptions.RequestException as e:
        # Log but don't fail - webhook is non-critical
        print(f"  Webhook error (non-critical): {e}")


def run_finalization(job: ConversionJob, options: FinalizeOptions):
    """
    Run the finalization pipeline (Phase 2).

    This runs:
    - RittDoc packaging (unless skipped)
    - DOCX generation (unless skipped)

    After completion, status is set to COMPLETED.
    """
    try:
        job_manager.update_job(job.job_id, status=JobStatus.FINALIZING, progress=65)

        # Stop editor if running
        if job.editor_process:
            try:
                job.editor_process.terminate()
                job.editor_process.wait(timeout=5)
            except Exception:
                pass
            job.editor_process = None
            job.editor_port = None

        if not job.xml_path or not job.xml_path.exists():
            raise RuntimeError(f"XML file not found: {job.xml_path}")

        pdf_stem = job.pdf_path.stem
        out_dir = job.output_dir

        # Run RittDoc packaging unless skipped
        if not options.skip_rittdoc and not job.options.skip_rittdoc:
            job_manager.update_job(job.job_id, status=JobStatus.PACKAGING, progress=70)

            dtd_path = Path(__file__).parent / APIConfig.DTD_PATH
            if dtd_path.exists():
                try:
                    from lxml import etree
                    from rittdoc_compliance_pipeline import RittDocCompliancePipeline
                    from package import (
                        BOOK_DOCTYPE_SYSTEM_DEFAULT,
                        package_docbook,
                        make_file_fetcher,
                    )

                    # Parse the XML
                    root = etree.parse(str(job.xml_path)).getroot()

                    # Create media fetcher
                    search_paths = []
                    if job.multimedia_dir and job.multimedia_dir.exists():
                        search_paths.append(job.multimedia_dir)
                        shared_images = job.multimedia_dir / "SharedImages"
                        if shared_images.exists():
                            search_paths.append(shared_images)
                    search_paths.append(out_dir)

                    # Try to load reference mapper
                    reference_mapper = None
                    mapper_path = out_dir / f"{pdf_stem}_reference_mapping_phase1.json"
                    if mapper_path.exists():
                        try:
                            from reference_mapper import ReferenceMapper
                            reference_mapper = ReferenceMapper()
                            reference_mapper.import_from_json(mapper_path)
                        except Exception:
                            pass

                    media_fetcher = make_file_fetcher(search_paths, reference_mapper)

                    # Create intermediate DocBook package
                    intermediate_zip = out_dir / f"{pdf_stem}_docbook.zip"
                    package_docbook(
                        root=root,
                        root_name=(root.tag.split('}', 1)[-1] if root.tag.startswith('{') else root.tag),
                        dtd_system=BOOK_DOCTYPE_SYSTEM_DEFAULT,
                        zip_path=str(intermediate_zip),
                        processing_instructions=[],
                        assets=[],
                        media_fetcher=media_fetcher,
                        book_doctype_system=BOOK_DOCTYPE_SYSTEM_DEFAULT,
                        metadata_dir=out_dir,
                    )

                    # Apply RittDoc compliance
                    job_manager.update_job(job.job_id, status=JobStatus.VALIDATING, progress=80)
                    out_rittdoc_zip = out_dir / f"{pdf_stem}_rittdoc.zip"
                    pipeline = RittDocCompliancePipeline(dtd_path)
                    pipeline.run(
                        input_zip=intermediate_zip,
                        output_zip=out_rittdoc_zip,
                        max_iterations=3
                    )

                except Exception as e:
                    # Log but don't fail - continue to DOCX
                    print(f"RittDoc packaging warning: {e}")

        # Run DOCX conversion unless skipped
        if not options.skip_docx:
            job_manager.update_job(job.job_id, progress=90)

            out_docx = out_dir / f"{pdf_stem}.docx"
            resource_path = f"{job.multimedia_dir}:{out_dir}" if job.multimedia_dir else str(out_dir)

            pandoc_cmd = [
                "pandoc",
                "-f", "docbook",
                "-t", "docx",
                "--toc",
                "--toc-depth=3",
                f"--resource-path={resource_path}",
                "-o", str(out_docx),
                str(job.xml_path),
            ]

            try:
                subprocess.run(
                    pandoc_cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                    cwd=str(Path(__file__).parent),
                )
            except subprocess.CalledProcessError as e:
                print(f"DOCX generation warning: {e.stderr}")

        # Collect final output files (filters out intermediate files and optionally cleans up)
        output_files = collect_output_files(out_dir)

        # Update metrics
        metrics = job.metrics.copy()
        metrics["has_docx"] = any(f.endswith('.docx') for f in output_files)
        metrics["has_zip"] = any(f.endswith('.zip') for f in output_files)
        metrics["phase"] = "finalization_complete"

        job_manager.update_job(
            job.job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            output_files=output_files,
            metrics=metrics,
        )

        # Refresh job to get updated output_files
        updated_job = job_manager.get_job(job.job_id)
        if updated_job:
            send_completion_webhook(updated_job, 'completed')
        else:
            send_completion_webhook(job, 'completed')

    except Exception as e:
        job_manager.update_job(
            job.job_id,
            status=JobStatus.FAILED,
            error=str(e),
        )
        send_completion_webhook(job, 'failed', error=str(e))


def extract_package_for_editing(job: ConversionJob) -> tuple[Path, Path, Path]:
    """
    Extract the finalized ZIP package for editing.

    Finds the _rittdoc.zip (or _docbook.zip as fallback), extracts it to
    a temp directory, merges entity references into a unified XML,
    and returns paths to the main XML and MultiMedia folder.

    The packaged Book.xml uses external entity references like:
        <!ENTITY ch0001 SYSTEM "ch0001.xml">
        &ch0001;

    This function merges all chapters into a single unified XML for editing.

    Returns:
        Tuple of (xml_path, multimedia_path, zip_path)

    Raises:
        FileNotFoundError: If no ZIP package is found
    """
    import re
    from lxml import etree

    # Find the final ZIP package
    zip_path = None

    # First look for _rittdoc.zip (preferred)
    for output_file in job.output_files:
        if output_file.endswith('_rittdoc.zip'):
            candidate = job.output_dir / output_file if job.output_dir else Path(output_file)
            if candidate.exists():
                zip_path = candidate
                break

    # Fallback to _docbook.zip
    if not zip_path:
        for output_file in job.output_files:
            if output_file.endswith('_docbook.zip'):
                candidate = job.output_dir / output_file if job.output_dir else Path(output_file)
                if candidate.exists():
                    zip_path = candidate
                    break

    # Last resort: search output directory for any zip
    if not zip_path and job.output_dir and job.output_dir.exists():
        for f in job.output_dir.glob('*_rittdoc.zip'):
            zip_path = f
            break
        if not zip_path:
            for f in job.output_dir.glob('*_docbook.zip'):
                zip_path = f
                break

    if not zip_path or not zip_path.exists():
        raise FileNotFoundError(f"No ZIP package found for job {job.job_id}")

    # Create extraction directory inside job's output dir (persists for re-save)
    extract_dir = job.output_dir / "editor_working"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    # Extract the ZIP
    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(extract_dir)

    # Find the main XML file (Book.xml or similar)
    book_xml_path = None
    for candidate in ['Book.xml', 'book.xml', 'Book.XML']:
        if (extract_dir / candidate).exists():
            book_xml_path = extract_dir / candidate
            break

    # If no Book.xml, look for any .xml file at root level
    if not book_xml_path:
        for f in extract_dir.glob('*.xml'):
            if not f.name.startswith('ch'):  # Skip chapter files
                book_xml_path = f
                break

    if not book_xml_path:
        raise FileNotFoundError(f"No XML file found in extracted package for job {job.job_id}")

    # Read the Book.xml content
    book_content = book_xml_path.read_text(encoding='utf-8')

    # Check if it uses entity references (has internal subset with ENTITY declarations)
    entity_pattern = re.compile(r'<!ENTITY\s+(\w+)\s+SYSTEM\s+"([^"]+)">')
    entities = entity_pattern.findall(book_content)

    if entities:
        # Merge entity references with their content
        # First, remove the internal subset (DOCTYPE with entities)
        # Keep just the XML declaration and root element

        # Parse to get the structure (without entity resolution)
        # We'll manually inline the entities

        # Find and read all chapter files
        entity_contents = {}
        for entity_name, entity_file in entities:
            entity_path = extract_dir / entity_file
            if entity_path.exists():
                # Read the chapter content (skip XML declaration if present)
                chapter_content = entity_path.read_text(encoding='utf-8')
                # Remove XML declaration from chapter files
                chapter_content = re.sub(r'<\?xml[^?]*\?>\s*', '', chapter_content)
                entity_contents[entity_name] = chapter_content

        # Replace entity references with actual content
        for entity_name, content in entity_contents.items():
            # Replace &entity_name; with the actual content
            book_content = re.sub(
                rf'&{entity_name};',
                content,
                book_content
            )

        # Remove the internal subset (ENTITY declarations)
        # Transform: <!DOCTYPE book ... [entities]> to <!DOCTYPE book ...>
        book_content = re.sub(
            r'(<!DOCTYPE\s+\w+\s+PUBLIC\s+"[^"]*"\s+"[^"]*")\s*\[[^\]]*\]>',
            r'\1>',
            book_content,
            flags=re.DOTALL
        )

    # Write the unified XML for editing
    unified_xml_path = extract_dir / "unified_for_editing.xml"
    unified_xml_path.write_text(book_content, encoding='utf-8')

    # Find MultiMedia folder
    multimedia_path = extract_dir / "MultiMedia"
    if not multimedia_path.exists():
        # Try lowercase
        multimedia_path = extract_dir / "multimedia"
        if not multimedia_path.exists():
            # Create empty one
            multimedia_path = extract_dir / "MultiMedia"
            multimedia_path.mkdir(exist_ok=True)

    return unified_xml_path, multimedia_path, zip_path


def launch_editor_process(job: ConversionJob, port: int, use_package: bool = True) -> subprocess.Popen:
    """
    Launch the editor server as a subprocess.

    Args:
        job: The conversion job
        port: Port number for the editor server
        use_package: If True, extract and edit from the final ZIP package.
                     If False, use the raw XML path (legacy behavior).
    """
    if use_package:
        try:
            xml_path, multimedia_path, zip_path = extract_package_for_editing(job)
            # Store the zip path on the job for repackaging later
            job.package_zip_path = zip_path
            job.editor_xml_path = xml_path
            job.editor_multimedia_path = multimedia_path
        except FileNotFoundError:
            # Fall back to raw XML if no package found
            xml_path = job.xml_path
            multimedia_path = job.multimedia_dir
            zip_path = None
    else:
        xml_path = job.xml_path
        multimedia_path = job.multimedia_dir
        zip_path = None

    cmd = [
        sys.executable,
        str(Path(__file__).parent / "editor_server.py"),
        str(job.pdf_path),
        str(xml_path),
        "--multimedia", str(multimedia_path) if multimedia_path else "",
        "--port", str(port),
        "--no-browser",  # Don't auto-open browser - UI will handle this
        "--job-id", job.job_id,
    ]

    # Pass the original ZIP path for repackaging after save
    if zip_path:
        cmd.extend(["--package-zip", str(zip_path)])

    # Add webhook URL if configured
    if APIConfig.WEBHOOK_URL:
        cmd.extend(["--webhook-url", APIConfig.WEBHOOK_URL])

    # Add API base URL for download links in webhooks
    if APIConfig.API_BASE_URL:
        cmd.extend(["--api-base-url", APIConfig.API_BASE_URL])

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(Path(__file__).parent),
    )

    return process


# ============================================================================
# API ENDPOINTS
# ============================================================================

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title="PDF to XML Conversion API",
        description="""
REST API for converting PDF documents to RittDoc DTD-compliant DocBook XML.

## Workflow

1. **Upload & Convert**: `POST /api/v1/convert` - Upload PDF, returns job_id
2. **Poll Status**: `GET /api/v1/jobs/{job_id}` - Wait for `ready_for_review` status
3. **Optional Edit**: `POST /api/v1/jobs/{job_id}/editor` - Launch web editor
   - Saving in the editor automatically triggers full finalization
4. **Finalize (if no edit)**: `POST /api/v1/jobs/{job_id}/finalize` - Use when skipping editor

The initial conversion produces DocBook XML. Editing is optional.
When the user saves in the editor, full finalization runs automatically
(RittDoc packaging, validation, fixing, and DOCX generation).
Use the finalize endpoint only when skipping the editor step.
        """,
        version="2.2.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Ensure directories exist on startup and initialize MongoDB
    @app.on_event("startup")
    async def startup_event():
        import logging
        logger = logging.getLogger(__name__)

        APIConfig.ensure_directories()

        # Initialize storage backend (GridFS/S3/Local)
        if STORAGE_AVAILABLE:
            if init_storage(APIConfig.STORAGE_BACKEND):
                logger.info(f"Storage backend initialized: {APIConfig.STORAGE_BACKEND}")
            else:
                logger.warning(f"Storage backend failed to initialize: {APIConfig.STORAGE_BACKEND}")
                logger.warning("Falling back to local filesystem storage")
                os.environ["STORAGE_BACKEND"] = "local"
                init_storage("local")

        # Initialize MongoDB connection (non-blocking, logs warning if unavailable)
        if MONGODB_AVAILABLE:
            if init_mongodb():
                logger.info("MongoDB connected successfully")
            else:
                logger.warning("MongoDB connection failed - dashboard persistence disabled")

    # ========================================================================
    # CONVERSION ENDPOINTS
    # ========================================================================

    @app.post("/api/v1/convert", response_model=JobInfo, tags=["Conversion"])
    async def start_conversion(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(..., description="PDF file to convert"),
        model: str = Form(default=APIConfig.DEFAULT_MODEL),
        dpi: int = Form(default=APIConfig.DEFAULT_DPI),
        temperature: float = Form(default=0.0),
        batch_size: int = Form(default=APIConfig.DEFAULT_BATCH_SIZE),
        skip_extraction: bool = Form(default=False),
        skip_rittdoc: bool = Form(default=False),
    ):
        """
        Upload a PDF file and start conversion (Phase 1).

        The conversion runs in the background and produces DocBook XML.
        Poll the job status until it reaches `ready_for_review`.

        Then optionally launch the editor, and finally call finalize.
        """
        # Validate file
        if not file.filename or not file.filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="File must be a PDF")

        # Extract ISBN from filename to use as job/directory ID
        isbn = job_manager._extract_isbn_from_filename(file.filename)

        # Check if job with this ISBN is already processing
        existing_job = job_manager.get_job(isbn)
        if existing_job and existing_job.status in (
            JobStatus.PROCESSING, JobStatus.CONVERTING,
            JobStatus.EXTRACTING, JobStatus.VALIDATING,
            JobStatus.PACKAGING, JobStatus.FINALIZING
        ):
            # Return existing job - don't start duplicate conversion
            return existing_job.to_info()

        # Read uploaded file content
        content = await file.read()

        # Save to storage backend (GridFS/S3/Local)
        if STORAGE_AVAILABLE:
            storage = get_storage()
            file_id = storage.upload(
                isbn=isbn,
                filename=file.filename,
                data=content,
                content_type="application/pdf",
                metadata={"type": "source", "original_filename": file.filename}
            )
            if not file_id:
                raise HTTPException(status_code=500, detail="Failed to save PDF to storage")

        # Create temp working directory for conversion pipeline
        # (Pipeline needs filesystem access for processing)
        job_work_dir = APIConfig.TEMP_DIR / f"job_{isbn}"
        job_work_dir.mkdir(parents=True, exist_ok=True)

        # Save PDF to temp directory for pipeline processing
        pdf_path = job_work_dir / file.filename
        pdf_path.write_bytes(content)

        # Create options
        options = ConversionOptions(
            model=model,
            dpi=dpi,
            temperature=temperature,
            batch_size=batch_size,
            skip_extraction=skip_extraction,
            skip_rittdoc=skip_rittdoc,
        )

        # Create job (uses ISBN as job_id)
        # Note: output_dir is temp location; final files saved to storage
        job = job_manager.create_job(
            filename=file.filename,
            pdf_path=pdf_path,
            output_dir=job_work_dir,
            options=options,
        )

        # Start initial conversion in background
        background_tasks.add_task(run_initial_conversion, job)

        return job.to_info()

    @app.get("/api/v1/jobs/{job_id}", response_model=JobInfo, tags=["Conversion"])
    async def get_job_status(job_id: str):
        """
        Get the status of a conversion job.

        Key statuses:
        - `processing`/`converting`: Initial conversion in progress
        - `ready_for_review`: Conversion done, can edit or finalize
        - `editing`: Editor is open
        - `finalizing`: Creating final package
        - `completed`: All done
        """
        job = job_manager.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job.to_info()

    @app.get("/api/v1/jobs", response_model=List[JobInfo], tags=["Conversion"])
    async def list_jobs(
        status: Optional[str] = None,
        limit: int = 50,
    ):
        """List all conversion jobs."""
        status_filter = None
        if status:
            try:
                status_filter = JobStatus(status)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

        jobs = job_manager.list_jobs(status=status_filter, limit=limit)
        return [j.to_info() for j in jobs]

    @app.delete("/api/v1/jobs/{job_id}", tags=["Conversion"])
    async def cancel_job(job_id: str):
        """Cancel a pending or in-progress job."""
        job = job_manager.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            raise HTTPException(status_code=400, detail="Job already finished")

        # Stop editor if running
        if job.editor_process:
            try:
                job.editor_process.terminate()
            except Exception:
                pass

        job_manager.update_job(job_id, status=JobStatus.CANCELLED)
        return {"message": "Job cancelled"}

    # ========================================================================
    # EDITOR ENDPOINTS
    # ========================================================================

    @app.post("/api/v1/jobs/{job_id}/editor", response_model=EditorInfo, tags=["Editor"])
    async def launch_editor(job_id: str):
        """
        Launch the web-based XML editor for a job.

        Available when job status is `completed` or `editing`.
        Returns the editor URL that the UI should open in an iframe or new tab.
        When user saves in the editor, the package will be regenerated.
        """
        job = job_manager.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Allow editing from COMPLETED (normal flow), READY_FOR_REVIEW (legacy), or EDITING (already open)
        if job.status not in (JobStatus.COMPLETED, JobStatus.READY_FOR_REVIEW, JobStatus.EDITING):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot edit job in status '{job.status.value}'. Must be 'completed'."
            )

        if not job.xml_path or not job.xml_path.exists():
            raise HTTPException(status_code=400, detail="XML file not found")

        # If editor already running, return existing URL
        if job.status == JobStatus.EDITING and job.editor_port:
            return EditorInfo(
                job_id=job_id,
                editor_url=f"http://localhost:{job.editor_port}",
                pdf_path=str(job.pdf_path),
                xml_path=str(job.xml_path),
                status="running",
            )

        # Launch new editor
        port = job_manager._get_next_editor_port()

        try:
            process = launch_editor_process(job, port)
            job.editor_process = process
            job.editor_port = port
            job_manager.update_job(job_id, status=JobStatus.EDITING)

            # Give the server a moment to start
            import time
            time.sleep(1)

            return EditorInfo(
                job_id=job_id,
                editor_url=f"http://localhost:{port}",
                pdf_path=str(job.pdf_path),
                xml_path=str(job.xml_path),
                status="started",
            )

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to launch editor: {e}")

    @app.delete("/api/v1/jobs/{job_id}/editor", tags=["Editor"])
    async def stop_editor(job_id: str):
        """
        Stop the editor for a job.

        Returns the job to `completed` status.
        """
        job = job_manager.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.editor_process:
            try:
                job.editor_process.terminate()
                job.editor_process.wait(timeout=5)
            except Exception:
                pass
            job.editor_process = None
            job.editor_port = None

        if job.status == JobStatus.EDITING:
            job_manager.update_job(job_id, status=JobStatus.COMPLETED)

        return {"message": "Editor stopped", "status": "completed"}

    # ========================================================================
    # FINALIZATION ENDPOINTS
    # ========================================================================

    @app.post("/api/v1/jobs/{job_id}/finalize", response_model=JobInfo, tags=["Finalization"])
    async def finalize_job(
        job_id: str,
        background_tasks: BackgroundTasks,
        skip_rittdoc: bool = Form(default=False),
        skip_docx: bool = Form(default=False),
    ):
        """
        Re-run packaging to create RittDoc package and DOCX.

        NOTE: This endpoint is now optional. The initial conversion already creates
        the final zip package. Use this only if you need to regenerate packages
        without using the editor.

        When the user saves in the editor, packaging runs automatically.
        """
        job = job_manager.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Allow re-finalization from COMPLETED, READY_FOR_REVIEW (legacy), or EDITING
        if job.status not in (JobStatus.COMPLETED, JobStatus.READY_FOR_REVIEW, JobStatus.EDITING):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot finalize job in status '{job.status.value}'."
            )

        options = FinalizeOptions(
            skip_rittdoc=skip_rittdoc,
            skip_docx=skip_docx,
        )

        # Run finalization in background
        background_tasks.add_task(run_finalization, job, options)

        # Return updated status
        job_manager.update_job(job_id, status=JobStatus.FINALIZING, progress=65)
        return job.to_info()

    # ========================================================================
    # FILE DOWNLOAD ENDPOINTS
    # ========================================================================

    @app.get("/api/v1/jobs/{job_id}/files", tags=["Files"])
    async def list_output_files(job_id: str):
        """List output files for a job (available after ready_for_review)."""
        job = job_manager.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.status in (JobStatus.PENDING, JobStatus.PROCESSING, JobStatus.CONVERTING):
            raise HTTPException(status_code=400, detail="Job not ready yet")

        isbn = job_id  # job_id is the ISBN

        # Try storage first
        if STORAGE_AVAILABLE:
            storage = get_storage()
            storage_files = storage.list_files(isbn)
            if storage_files:
                files = []
                for f in storage_files:
                    files.append({
                        "name": f['filename'],
                        "size": f['size'],
                        "file_type": f.get('file_type', 'other'),
                        "download_url": f"/api/v1/jobs/{job_id}/files/{f['filename']}",
                    })
                return {"files": files, "source": "storage"}

        # Fallback to local filesystem
        files = []
        if job.output_dir and job.output_dir.exists():
            for f in job.output_dir.iterdir():
                if f.is_file():
                    files.append({
                        "name": f.name,
                        "size": f.stat().st_size,
                        "download_url": f"/api/v1/jobs/{job_id}/files/{f.name}",
                    })

        # Also include multimedia files
        if job.multimedia_dir and job.multimedia_dir.exists():
            for f in job.multimedia_dir.iterdir():
                if f.is_file():
                    files.append({
                        "name": f"MultiMedia/{f.name}",
                        "size": f.stat().st_size,
                        "download_url": f"/api/v1/jobs/{job_id}/files/MultiMedia/{f.name}",
                    })

        return {"files": files, "source": "filesystem"}

    @app.get("/api/v1/jobs/{job_id}/files/{filename:path}", tags=["Files"])
    async def download_file(job_id: str, filename: str):
        """Download an output file from storage or filesystem."""
        from fastapi.responses import StreamingResponse

        isbn = job_id  # job_id is the ISBN

        # Try storage first
        if STORAGE_AVAILABLE:
            storage = get_storage()
            data = storage.download(isbn, filename)
            if data:
                # Determine content type
                ext = Path(filename).suffix.lower()
                content_types = {
                    '.pdf': 'application/pdf',
                    '.xml': 'application/xml',
                    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    '.zip': 'application/zip',
                    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    '.json': 'application/json',
                    '.png': 'image/png',
                    '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg',
                }
                content_type = content_types.get(ext, 'application/octet-stream')

                return StreamingResponse(
                    io.BytesIO(data),
                    media_type=content_type,
                    headers={
                        "Content-Disposition": f'attachment; filename="{Path(filename).name}"',
                        "Content-Length": str(len(data)),
                    }
                )

        # Fallback to local filesystem
        job = job_manager.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Check if it's a multimedia file
        if filename.startswith("MultiMedia/"):
            actual_filename = filename[len("MultiMedia/"):]
            if job.multimedia_dir:
                file_path = job.multimedia_dir / actual_filename
            else:
                raise HTTPException(status_code=404, detail="File not found")
        else:
            file_path = job.output_dir / filename

        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        return FileResponse(
            path=file_path,
            filename=file_path.name,
            media_type="application/octet-stream",
        )

    @app.post("/api/v1/jobs/{job_id}/metadata", tags=["Files"])
    async def upload_metadata(
        job_id: str,
        file: UploadFile = File(...),
    ):
        """
        Upload a metadata file for a job.

        Supported formats:
        - CSV (.csv)
        - Excel (.xlsx, .xls)
        - ONIX XML (.xml, .onix.xml)

        The file will be saved to the job's output directory and used
        to populate the bookinfo section during finalization.
        """
        job = job_manager.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Validate file extension
        filename = file.filename or "metadata"
        ext = Path(filename).suffix.lower()
        valid_extensions = ['.csv', '.xlsx', '.xls', '.xml']

        if ext not in valid_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type. Supported: CSV, XLSX, XLS, XML (ONIX). Got: {ext}"
            )

        # Determine target filename
        if ext == '.xml':
            # For XML files, save as metadata.xml or keep .onix.xml suffix
            if filename.lower().endswith('.onix.xml'):
                target_name = filename
            else:
                target_name = "metadata.xml"
        else:
            target_name = f"metadata{ext}"

        # Save to job's output directory
        target_path = job.output_dir / target_name
        try:
            content = await file.read()
            target_path.write_bytes(content)

            # Validate ONIX file if XML
            if ext == '.xml':
                from legacy.metadata_processor import _is_onix_file
                if not _is_onix_file(target_path):
                    target_path.unlink()  # Delete invalid file
                    raise HTTPException(
                        status_code=400,
                        detail="XML file does not appear to be valid ONIX format"
                    )

            return {
                "success": True,
                "message": f"Metadata file uploaded successfully",
                "filename": target_name,
                "path": str(target_path),
                "format": "onix" if ext == '.xml' else ext[1:],
            }

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to save metadata file: {str(e)}")

    @app.get("/api/v1/jobs/{job_id}/metadata", tags=["Files"])
    async def get_metadata_status(job_id: str):
        """
        Check if a metadata file exists for a job and get its info.
        """
        job = job_manager.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Look for metadata files
        from legacy.metadata_processor import find_metadata_file, _read_metadata_file

        metadata_file = find_metadata_file(job.output_dir)

        if metadata_file is None:
            return {
                "has_metadata": False,
                "message": "No metadata file found"
            }

        # Try to read and return metadata info
        metadata = _read_metadata_file(metadata_file)

        return {
            "has_metadata": True,
            "filename": metadata_file.name,
            "format": "onix" if metadata_file.suffix == '.xml' else metadata_file.suffix[1:],
            "metadata": {
                "isbn": metadata.get('isbn') if metadata else None,
                "title": metadata.get('title') if metadata else None,
                "authors": metadata.get('authors') if metadata else [],
                "publisher": metadata.get('publisher') if metadata else None,
                "pubdate": metadata.get('pubdate') if metadata else None,
            } if metadata else None
        }

    # ========================================================================
    # DASHBOARD ENDPOINTS
    # ========================================================================

    @app.get("/api/v1/dashboard", response_model=DashboardStats, tags=["Dashboard"])
    async def get_dashboard():
        """Get dashboard statistics for all conversions."""
        return job_manager.get_dashboard_stats()

    @app.get("/api/v1/dashboard/export", tags=["Dashboard"])
    async def export_dashboard():
        """Export dashboard data as JSON."""
        stats = job_manager.get_dashboard_stats()
        jobs = job_manager.list_jobs(limit=1000)

        export_data = {
            "exported_at": datetime.now().isoformat(),
            "statistics": stats.dict(),
            "jobs": [j.to_info().dict() for j in jobs],
        }

        return JSONResponse(content=export_data)

    # ========================================================================
    # MONGODB DASHBOARD ENDPOINTS (Persistent Storage)
    # ========================================================================

    @app.get("/api/v1/mongodb/dashboard", tags=["MongoDB Dashboard"])
    async def get_mongodb_dashboard():
        """
        Get dashboard statistics from MongoDB (persistent storage).

        This endpoint returns historical data from MongoDB, which persists
        across server restarts. Use this for the main UI dashboard.
        """
        if not MONGODB_AVAILABLE:
            raise HTTPException(
                status_code=503,
                detail="MongoDB is not available. Install pymongo: pip install pymongo"
            )

        store = get_mongodb_store()
        if not store.ensure_connected():
            raise HTTPException(
                status_code=503,
                detail="Cannot connect to MongoDB. Check MONGODB_URI environment variable."
            )

        return store.get_dashboard_stats()

    @app.get("/api/v1/mongodb/conversions", tags=["MongoDB Dashboard"])
    async def list_mongodb_conversions(
        status: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
    ):
        """
        List conversions from MongoDB with optional filtering.

        Args:
            status: Filter by status (success, failure, in_progress, etc.)
            limit: Maximum number of results (default 50)
            skip: Number of results to skip for pagination
        """
        if not MONGODB_AVAILABLE:
            raise HTTPException(status_code=503, detail="MongoDB not available")

        store = get_mongodb_store()
        if not store.ensure_connected():
            raise HTTPException(status_code=503, detail="Cannot connect to MongoDB")

        conversions = store.list_conversions(status=status, limit=limit, skip=skip)
        return {"conversions": conversions, "count": len(conversions)}

    @app.get("/api/v1/mongodb/conversions/{job_id}", tags=["MongoDB Dashboard"])
    async def get_mongodb_conversion(job_id: str):
        """Get a specific conversion record from MongoDB by job_id (ISBN)."""
        if not MONGODB_AVAILABLE:
            raise HTTPException(status_code=503, detail="MongoDB not available")

        store = get_mongodb_store()
        if not store.ensure_connected():
            raise HTTPException(status_code=503, detail="Cannot connect to MongoDB")

        conversion = store.get_conversion(job_id)
        if not conversion:
            raise HTTPException(status_code=404, detail="Conversion not found")

        return conversion

    @app.get("/api/v1/isbn/{isbn}", tags=["ISBN Lookup"])
    async def get_by_isbn(isbn: str):
        """
        Look up a conversion by ISBN.

        This endpoint searches both the in-memory job store (for active jobs)
        and MongoDB (for historical records). Returns comprehensive information
        about the conversion status and output files.

        Args:
            isbn: The ISBN number (13 or 10 digits)

        Returns:
            Job information from memory or MongoDB
        """
        # First check in-memory store for active/recent jobs
        job = job_manager.get_job(isbn)
        if job:
            return {
                "source": "active",
                "isbn": isbn,
                "job": job.to_info().dict(),
            }

        # Check MongoDB for historical records
        if MONGODB_AVAILABLE:
            store = get_mongodb_store()
            if store.ensure_connected():
                conversion = store.get_conversion_by_isbn(isbn)
                if conversion:
                    return {
                        "source": "mongodb",
                        "isbn": isbn,
                        "conversion": conversion,
                    }

        raise HTTPException(
            status_code=404,
            detail=f"No conversion found for ISBN: {isbn}"
        )

    @app.get("/api/v1/mongodb/stats/daily", tags=["MongoDB Dashboard"])
    async def get_daily_stats(days: int = 30):
        """
        Get daily conversion statistics for charts.

        Args:
            days: Number of days to include (default 30)
        """
        if not MONGODB_AVAILABLE:
            raise HTTPException(status_code=503, detail="MongoDB not available")

        store = get_mongodb_store()
        if not store.ensure_connected():
            raise HTTPException(status_code=503, detail="Cannot connect to MongoDB")

        return {"daily_stats": store.get_daily_stats(days=days)}

    @app.get("/api/v1/mongodb/stats/publishers", tags=["MongoDB Dashboard"])
    async def get_publisher_stats():
        """Get conversion statistics grouped by publisher."""
        if not MONGODB_AVAILABLE:
            raise HTTPException(status_code=503, detail="MongoDB not available")

        store = get_mongodb_store()
        if not store.ensure_connected():
            raise HTTPException(status_code=503, detail="Cannot connect to MongoDB")

        return {"publisher_stats": store.get_publisher_stats()}

    @app.post("/api/v1/mongodb/sync-excel", tags=["MongoDB Dashboard"])
    async def sync_excel_to_mongodb_endpoint(excel_path: str = Form(...)):
        """
        Sync data from Excel dashboard to MongoDB.

        Useful for initial migration or recovery from Excel backup.

        Args:
            excel_path: Path to conversion_dashboard.xlsx
        """
        if not MONGODB_AVAILABLE:
            raise HTTPException(status_code=503, detail="MongoDB not available")

        from mongodb_store import sync_excel_to_mongodb
        excel_file = Path(excel_path)

        if not excel_file.exists():
            raise HTTPException(status_code=404, detail=f"Excel file not found: {excel_path}")

        count = sync_excel_to_mongodb(excel_file)
        return {"synced_records": count, "message": f"Successfully synced {count} records to MongoDB"}

    # ========================================================================
    # HEALTH & INFO ENDPOINTS
    # ========================================================================

    @app.get("/api/v1/health", tags=["System"])
    async def health_check():
        """Health check endpoint."""
        mongodb_status = "unavailable"
        if MONGODB_AVAILABLE:
            store = get_mongodb_store()
            mongodb_status = "connected" if store.is_connected else "disconnected"

        storage_status = "unavailable"
        storage_backend = "none"
        if STORAGE_AVAILABLE:
            storage = get_storage()
            storage_status = "connected" if storage.is_connected() else "disconnected"
            storage_backend = APIConfig.STORAGE_BACKEND

        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "tracking_available": TRACKING_AVAILABLE,
            "mongodb_available": MONGODB_AVAILABLE,
            "mongodb_status": mongodb_status,
            "storage_available": STORAGE_AVAILABLE,
            "storage_backend": storage_backend,
            "storage_status": storage_status,
        }

    @app.get("/api/v1/storage/stats", tags=["System"])
    async def get_storage_stats():
        """Get storage statistics."""
        if not STORAGE_AVAILABLE:
            raise HTTPException(status_code=503, detail="Storage not available")

        storage = get_storage()
        if not storage.is_connected():
            raise HTTPException(status_code=503, detail="Storage not connected")

        # Get overall stats from GridFS
        from gridfs_store import get_gridfs_store
        gridfs = get_gridfs_store()

        return {
            "backend": APIConfig.STORAGE_BACKEND,
            "stats": gridfs.get_storage_stats() if gridfs.is_connected else {},
        }

    @app.get("/api/v1/storage/isbn/{isbn}", tags=["System"])
    async def get_isbn_storage_stats(isbn: str):
        """Get storage statistics for a specific ISBN."""
        if not STORAGE_AVAILABLE:
            raise HTTPException(status_code=503, detail="Storage not available")

        storage = get_storage()
        if not storage.is_connected():
            raise HTTPException(status_code=503, detail="Storage not connected")

        files = storage.list_files(isbn)
        total_size = sum(f.get('size', 0) for f in files)

        return {
            "isbn": isbn,
            "file_count": len(files),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "files": files,
        }

    @app.get("/api/v1/info", tags=["System"])
    async def get_info():
        """Get API configuration and capabilities."""
        return {
            "version": "2.1.0",
            "config": {
                "default_model": APIConfig.DEFAULT_MODEL,
                "default_dpi": APIConfig.DEFAULT_DPI,
                "max_concurrent_jobs": APIConfig.MAX_CONCURRENT_JOBS,
                "dtd_available": APIConfig.DTD_PATH.exists(),
            },
            "capabilities": {
                "tracking": TRACKING_AVAILABLE,
                "editor": True,
                "rittdoc_packaging": True,
                "docx_output": True,
            },
            "workflow": {
                "phase1": "Initial conversion (PDF → DocBook XML)",
                "phase2": "Optional editing via web editor",
                "phase3": "Finalization (RittDoc package + DOCX)",
            },
        }

    @app.get("/api/v1/models", tags=["System"])
    async def list_models():
        """List available Claude models."""
        return {
            "models": [
                {
                    "id": "claude-sonnet-4-20250514",
                    "name": "Claude Sonnet 4",
                    "description": "Fast, balanced model for most documents",
                    "default": True,
                },
                {
                    "id": "claude-opus-4-5-20251101",
                    "name": "Claude Opus 4.5",
                    "description": "Most accurate, best for complex layouts",
                    "default": False,
                },
            ]
        }

    @app.get("/api/v1/config/options", tags=["Configuration"])
    async def get_config_options():
        """
        Get all configuration options for UI dropdowns and forms.

        Returns dropdown options with labels, descriptions, and default values.
        Use this to dynamically build the conversion settings UI.
        """
        try:
            from shared_config import CONVERSION_CONFIG_OPTIONS, DEFAULT_CONVERSION_CONFIG
            return {
                "options": CONVERSION_CONFIG_OPTIONS,
                "defaults": DEFAULT_CONVERSION_CONFIG.to_dict(),
            }
        except ImportError:
            # Fallback if shared_config is not available
            return {
                "options": {
                    "model": {
                        "label": "AI Model",
                        "type": "dropdown",
                        "default": "claude-sonnet-4-20250514",
                        "options": [
                            {"value": "claude-sonnet-4-20250514", "label": "Claude Sonnet 4 (Recommended)", "default": True},
                            {"value": "claude-opus-4-5-20251101", "label": "Claude Opus 4.5 (Highest Quality)"},
                            {"value": "claude-haiku-3-5-20241022", "label": "Claude Haiku 3.5 (Fastest)"},
                        ],
                    },
                    "dpi": {
                        "label": "Resolution (DPI)",
                        "type": "dropdown",
                        "default": 300,
                        "options": [
                            {"value": 150, "label": "150 DPI (Fast)"},
                            {"value": 200, "label": "200 DPI (Balanced)"},
                            {"value": 300, "label": "300 DPI (Recommended)", "default": True},
                            {"value": 400, "label": "400 DPI (High Quality)"},
                            {"value": 600, "label": "600 DPI (Maximum)"},
                        ],
                    },
                },
                "defaults": {
                    "model": "claude-sonnet-4-20250514",
                    "dpi": 300,
                    "temperature": 0.0,
                    "batch_size": 10,
                },
            }

    @app.get("/api/v1/config/schema", tags=["Configuration"])
    async def get_config_schema():
        """
        Get JSON schema for configuration validation.

        Use this schema to validate configuration in your UI before submission.
        """
        try:
            from shared_config import CONVERSION_CONFIG_JSON_SCHEMA
            return CONVERSION_CONFIG_JSON_SCHEMA
        except ImportError:
            # Return basic schema as fallback
            return {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": "PDF Conversion Configuration",
                "type": "object",
                "properties": {
                    "model": {"type": "string", "default": "claude-sonnet-4-20250514"},
                    "dpi": {"type": "integer", "enum": [150, 200, 300, 400, 600], "default": 300},
                    "temperature": {"type": "number", "default": 0.0},
                    "batch_size": {"type": "integer", "default": 10},
                },
            }

    return app


# Create default app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
