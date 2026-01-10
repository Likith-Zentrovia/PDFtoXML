#!/usr/bin/env python3
"""
Abstract Storage Interface for PDF Conversion Pipeline

This module provides a pluggable storage abstraction that supports:
- MongoDB GridFS (current implementation)
- Amazon S3 (future implementation)
- Local filesystem (fallback/development)

All storage backends implement the same interface, making it easy
to switch between them by changing configuration.

Configuration:
    STORAGE_BACKEND: "gridfs" | "s3" | "local" (default: "gridfs")

    For GridFS:
        MONGODB_URI, MONGODB_DATABASE

    For S3 (future):
        AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET

    For Local:
        STORAGE_LOCAL_PATH (default: ./storage)

Usage:
    from storage import get_storage

    storage = get_storage()

    # Upload
    storage.upload(isbn="9798275082845", filename="book.pdf", data=content)

    # Download
    data = storage.download(isbn="9798275082845", filename="book.pdf")

    # List
    files = storage.list_files(isbn="9798275082845")
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Dict, Generator, List, Optional, Union

logger = logging.getLogger(__name__)


# ============================================================================
# ABSTRACT STORAGE INTERFACE
# ============================================================================

class StorageBackend(ABC):
    """
    Abstract base class for storage backends.

    All storage implementations (GridFS, S3, Local) must implement these methods.
    This allows easy switching between storage backends.
    """

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection to storage backend."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to storage."""
        pass

    @abstractmethod
    def upload(
        self,
        isbn: str,
        filename: str,
        data: Union[bytes, BinaryIO],
        content_type: str = None,
        metadata: Dict[str, Any] = None,
    ) -> Optional[str]:
        """
        Upload a file to storage.

        Args:
            isbn: ISBN number (primary key)
            filename: Name of the file
            data: File content
            content_type: MIME type
            metadata: Additional metadata

        Returns:
            File identifier or None if failed
        """
        pass

    @abstractmethod
    def upload_from_path(
        self,
        isbn: str,
        file_path: Path,
        target_filename: str = None,
        metadata: Dict[str, Any] = None,
    ) -> Optional[str]:
        """Upload a file from filesystem path."""
        pass

    @abstractmethod
    def download(self, isbn: str, filename: str) -> Optional[bytes]:
        """
        Download a file from storage.

        Returns:
            File content as bytes or None if not found
        """
        pass

    @abstractmethod
    def download_to_path(
        self,
        isbn: str,
        filename: str,
        target_path: Path,
    ) -> bool:
        """Download a file to filesystem path."""
        pass

    @abstractmethod
    def delete(self, isbn: str, filename: str) -> bool:
        """Delete a file from storage."""
        pass

    @abstractmethod
    def delete_all(self, isbn: str) -> int:
        """Delete all files for an ISBN. Returns count deleted."""
        pass

    @abstractmethod
    def list_files(
        self,
        isbn: str,
        file_types: List[str] = None,
    ) -> List[Dict[str, Any]]:
        """List all files for an ISBN."""
        pass

    @abstractmethod
    def exists(self, isbn: str, filename: str) -> bool:
        """Check if a file exists."""
        pass

    @abstractmethod
    def get_file_info(self, isbn: str, filename: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a file."""
        pass

    @contextmanager
    def temp_file(self, isbn: str, filename: str) -> Generator[Optional[Path], None, None]:
        """
        Context manager that downloads a file to a temporary location.
        Automatically cleans up when done.
        """
        temp_path = None
        try:
            data = self.download(isbn, filename)
            if data is None:
                yield None
                return

            suffix = Path(filename).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                f.write(data)
                temp_path = Path(f.name)

            yield temp_path

        finally:
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

    @contextmanager
    def temp_directory(self, isbn: str, file_types: List[str] = None) -> Generator[Optional[Path], None, None]:
        """
        Context manager that downloads all files for an ISBN to a temp directory.
        Automatically cleans up when done.
        """
        temp_dir = None
        try:
            temp_dir = Path(tempfile.mkdtemp(prefix=f"isbn_{isbn}_"))

            files = self.list_files(isbn, file_types=file_types)
            for file_info in files:
                filename = file_info['filename']
                self.download_to_path(isbn, filename, temp_dir / filename)

            yield temp_dir

        finally:
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir)
                except Exception:
                    pass


# ============================================================================
# GRIDFS IMPLEMENTATION
# ============================================================================

class GridFSBackend(StorageBackend):
    """MongoDB GridFS storage backend."""

    def __init__(self, uri: str = None, database: str = None):
        self.uri = uri or os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
        self.database_name = database or os.environ.get("MONGODB_DATABASE", "pdftoxml")
        self._store = None

    def connect(self) -> bool:
        from gridfs_store import get_gridfs_store
        self._store = get_gridfs_store()
        return self._store.connect()

    def is_connected(self) -> bool:
        return self._store is not None and self._store.is_connected

    def _ensure_store(self):
        if self._store is None:
            from gridfs_store import get_gridfs_store
            self._store = get_gridfs_store()
        if not self._store.is_connected:
            self._store.connect()
        return self._store

    def upload(
        self,
        isbn: str,
        filename: str,
        data: Union[bytes, BinaryIO],
        content_type: str = None,
        metadata: Dict[str, Any] = None,
    ) -> Optional[str]:
        store = self._ensure_store()
        return store.upload_file(isbn, filename, data, content_type, metadata)

    def upload_from_path(
        self,
        isbn: str,
        file_path: Path,
        target_filename: str = None,
        metadata: Dict[str, Any] = None,
    ) -> Optional[str]:
        store = self._ensure_store()
        return store.upload_from_path(isbn, file_path, target_filename, metadata)

    def download(self, isbn: str, filename: str) -> Optional[bytes]:
        store = self._ensure_store()
        return store.download_file(isbn, filename)

    def download_to_path(
        self,
        isbn: str,
        filename: str,
        target_path: Path,
    ) -> bool:
        store = self._ensure_store()
        return store.download_to_path(isbn, filename, target_path)

    def delete(self, isbn: str, filename: str) -> bool:
        store = self._ensure_store()
        return store.delete_file(isbn, filename)

    def delete_all(self, isbn: str) -> int:
        store = self._ensure_store()
        return store.delete_all_files(isbn)

    def list_files(
        self,
        isbn: str,
        file_types: List[str] = None,
    ) -> List[Dict[str, Any]]:
        store = self._ensure_store()
        return store.list_files(isbn, file_types)

    def exists(self, isbn: str, filename: str) -> bool:
        store = self._ensure_store()
        return store.file_exists(isbn, filename)

    def get_file_info(self, isbn: str, filename: str) -> Optional[Dict[str, Any]]:
        store = self._ensure_store()
        return store.get_file_info(isbn, filename)


# ============================================================================
# S3 IMPLEMENTATION (Placeholder for future)
# ============================================================================

class S3Backend(StorageBackend):
    """
    Amazon S3 storage backend.

    TODO: Implement when migrating to S3.

    Configuration:
        AWS_ACCESS_KEY_ID: AWS access key
        AWS_SECRET_ACCESS_KEY: AWS secret key
        AWS_REGION: AWS region (default: us-east-1)
        S3_BUCKET: S3 bucket name
        S3_PREFIX: Optional prefix for all keys
    """

    def __init__(self):
        self.bucket = os.environ.get("S3_BUCKET", "pdftoxml-storage")
        self.prefix = os.environ.get("S3_PREFIX", "")
        self.region = os.environ.get("AWS_REGION", "us-east-1")
        self._client = None
        self._connected = False

    def connect(self) -> bool:
        try:
            import boto3
            self._client = boto3.client(
                's3',
                region_name=self.region,
            )
            # Test connection
            self._client.head_bucket(Bucket=self.bucket)
            self._connected = True
            logger.info(f"S3 connected to bucket: {self.bucket}")
            return True
        except ImportError:
            logger.error("boto3 not installed. Run: pip install boto3")
            return False
        except Exception as e:
            logger.error(f"Failed to connect to S3: {e}")
            return False

    def is_connected(self) -> bool:
        return self._connected

    def _get_key(self, isbn: str, filename: str) -> str:
        """Build S3 object key."""
        if self.prefix:
            return f"{self.prefix}/{isbn}/{filename}"
        return f"{isbn}/{filename}"

    def upload(
        self,
        isbn: str,
        filename: str,
        data: Union[bytes, BinaryIO],
        content_type: str = None,
        metadata: Dict[str, Any] = None,
    ) -> Optional[str]:
        if not self._connected:
            return None

        try:
            key = self._get_key(isbn, filename)
            extra_args = {}

            if content_type:
                extra_args['ContentType'] = content_type

            if metadata:
                extra_args['Metadata'] = {k: str(v) for k, v in metadata.items()}

            if isinstance(data, bytes):
                self._client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra_args)
            else:
                self._client.upload_fileobj(data, self.bucket, key, ExtraArgs=extra_args)

            return key

        except Exception as e:
            logger.error(f"Failed to upload to S3: {e}")
            return None

    def upload_from_path(
        self,
        isbn: str,
        file_path: Path,
        target_filename: str = None,
        metadata: Dict[str, Any] = None,
    ) -> Optional[str]:
        if not file_path.exists():
            return None

        with open(file_path, 'rb') as f:
            return self.upload(isbn, target_filename or file_path.name, f, metadata=metadata)

    def download(self, isbn: str, filename: str) -> Optional[bytes]:
        if not self._connected:
            return None

        try:
            key = self._get_key(isbn, filename)
            response = self._client.get_object(Bucket=self.bucket, Key=key)
            return response['Body'].read()
        except self._client.exceptions.NoSuchKey:
            return None
        except Exception as e:
            logger.error(f"Failed to download from S3: {e}")
            return None

    def download_to_path(
        self,
        isbn: str,
        filename: str,
        target_path: Path,
    ) -> bool:
        data = self.download(isbn, filename)
        if data is None:
            return False

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(data)
            return True
        except Exception:
            return False

    def delete(self, isbn: str, filename: str) -> bool:
        if not self._connected:
            return False

        try:
            key = self._get_key(isbn, filename)
            self._client.delete_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as e:
            logger.error(f"Failed to delete from S3: {e}")
            return False

    def delete_all(self, isbn: str) -> int:
        if not self._connected:
            return 0

        try:
            prefix = self._get_key(isbn, "")
            response = self._client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)

            if 'Contents' not in response:
                return 0

            objects = [{'Key': obj['Key']} for obj in response['Contents']]
            self._client.delete_objects(Bucket=self.bucket, Delete={'Objects': objects})

            return len(objects)

        except Exception as e:
            logger.error(f"Failed to delete all from S3: {e}")
            return 0

    def list_files(
        self,
        isbn: str,
        file_types: List[str] = None,
    ) -> List[Dict[str, Any]]:
        if not self._connected:
            return []

        try:
            prefix = self._get_key(isbn, "")
            response = self._client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)

            if 'Contents' not in response:
                return []

            files = []
            for obj in response['Contents']:
                filename = obj['Key'].split('/')[-1]
                files.append({
                    'filename': filename,
                    'size': obj['Size'],
                    'uploaded_at': obj['LastModified'],
                    'file_id': obj['Key'],
                })

            return files

        except Exception as e:
            logger.error(f"Failed to list S3 files: {e}")
            return []

    def exists(self, isbn: str, filename: str) -> bool:
        if not self._connected:
            return False

        try:
            key = self._get_key(isbn, filename)
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except:
            return False

    def get_file_info(self, isbn: str, filename: str) -> Optional[Dict[str, Any]]:
        if not self._connected:
            return None

        try:
            key = self._get_key(isbn, filename)
            response = self._client.head_object(Bucket=self.bucket, Key=key)
            return {
                'filename': filename,
                'size': response['ContentLength'],
                'content_type': response.get('ContentType'),
                'uploaded_at': response['LastModified'],
                'metadata': response.get('Metadata', {}),
            }
        except:
            return None


# ============================================================================
# LOCAL FILESYSTEM IMPLEMENTATION (Fallback/Development)
# ============================================================================

class LocalBackend(StorageBackend):
    """Local filesystem storage backend for development/fallback."""

    # File type detection (same as GridFS)
    FILE_TYPES = {
        '.pdf': 'source', '.xml': 'output', '.docx': 'output',
        '.zip': 'package', '.xlsx': 'report', '.json': 'metadata',
        '.png': 'image', '.jpg': 'image', '.jpeg': 'image',
        '.gif': 'image', '.svg': 'image', '.tif': 'image', '.tiff': 'image',
    }

    def __init__(self, base_path: str = None):
        self.base_path = Path(base_path or os.environ.get("STORAGE_LOCAL_PATH", "./storage"))
        self._connected = False

    def connect(self) -> bool:
        try:
            self.base_path.mkdir(parents=True, exist_ok=True)
            self._connected = True
            logger.info(f"Local storage initialized at: {self.base_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize local storage: {e}")
            return False

    def is_connected(self) -> bool:
        return self._connected

    def _get_path(self, isbn: str, filename: str = None) -> Path:
        """Build filesystem path."""
        if filename:
            return self.base_path / isbn / filename
        return self.base_path / isbn

    def _get_file_type(self, filename: str) -> str:
        ext = Path(filename).suffix.lower()
        return self.FILE_TYPES.get(ext, 'other')

    def upload(
        self,
        isbn: str,
        filename: str,
        data: Union[bytes, BinaryIO],
        content_type: str = None,
        metadata: Dict[str, Any] = None,
    ) -> Optional[str]:
        if not self._connected:
            self.connect()

        try:
            file_path = self._get_path(isbn, filename)
            file_path.parent.mkdir(parents=True, exist_ok=True)

            if isinstance(data, bytes):
                file_path.write_bytes(data)
            else:
                with open(file_path, 'wb') as f:
                    shutil.copyfileobj(data, f)

            logger.info(f"Saved file to local storage: {file_path}")
            return str(file_path)

        except Exception as e:
            logger.error(f"Failed to save to local storage: {e}")
            return None

    def upload_from_path(
        self,
        isbn: str,
        file_path: Path,
        target_filename: str = None,
        metadata: Dict[str, Any] = None,
    ) -> Optional[str]:
        if not file_path.exists():
            return None

        target = self._get_path(isbn, target_filename or file_path.name)
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(file_path, target)
            return str(target)
        except Exception as e:
            logger.error(f"Failed to copy to local storage: {e}")
            return None

    def download(self, isbn: str, filename: str) -> Optional[bytes]:
        file_path = self._get_path(isbn, filename)
        if file_path.exists():
            return file_path.read_bytes()
        return None

    def download_to_path(
        self,
        isbn: str,
        filename: str,
        target_path: Path,
    ) -> bool:
        file_path = self._get_path(isbn, filename)
        if not file_path.exists():
            return False

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, target_path)
            return True
        except Exception:
            return False

    def delete(self, isbn: str, filename: str) -> bool:
        file_path = self._get_path(isbn, filename)
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    def delete_all(self, isbn: str) -> int:
        isbn_dir = self._get_path(isbn)
        if not isbn_dir.exists():
            return 0

        count = sum(1 for f in isbn_dir.iterdir() if f.is_file())
        shutil.rmtree(isbn_dir)
        return count

    def list_files(
        self,
        isbn: str,
        file_types: List[str] = None,
    ) -> List[Dict[str, Any]]:
        isbn_dir = self._get_path(isbn)
        if not isbn_dir.exists():
            return []

        files = []
        for f in isbn_dir.iterdir():
            if not f.is_file():
                continue

            file_type = self._get_file_type(f.name)
            if file_types and file_type not in file_types:
                continue

            stat = f.stat()
            files.append({
                'filename': f.name,
                'size': stat.st_size,
                'file_type': file_type,
                'uploaded_at': datetime.fromtimestamp(stat.st_mtime),
                'file_id': str(f),
            })

        return files

    def exists(self, isbn: str, filename: str) -> bool:
        return self._get_path(isbn, filename).exists()

    def get_file_info(self, isbn: str, filename: str) -> Optional[Dict[str, Any]]:
        file_path = self._get_path(isbn, filename)
        if not file_path.exists():
            return None

        stat = file_path.stat()
        return {
            'filename': filename,
            'size': stat.st_size,
            'file_type': self._get_file_type(filename),
            'uploaded_at': datetime.fromtimestamp(stat.st_mtime),
            'file_id': str(file_path),
        }


# ============================================================================
# FACTORY & SINGLETON
# ============================================================================

_storage_instance: Optional[StorageBackend] = None


def get_storage() -> StorageBackend:
    """
    Get the configured storage backend instance.

    Uses STORAGE_BACKEND env var to select backend:
    - "gridfs" (default): MongoDB GridFS
    - "s3": Amazon S3
    - "local": Local filesystem
    """
    global _storage_instance

    if _storage_instance is None:
        backend = os.environ.get("STORAGE_BACKEND", "gridfs").lower()

        if backend == "s3":
            _storage_instance = S3Backend()
        elif backend == "local":
            _storage_instance = LocalBackend()
        else:  # default to gridfs
            _storage_instance = GridFSBackend()

        # Auto-connect
        _storage_instance.connect()

    return _storage_instance


def init_storage(backend: str = None) -> bool:
    """
    Initialize storage with specified backend.

    Args:
        backend: "gridfs", "s3", or "local". Uses env var if not specified.

    Returns:
        True if connected successfully
    """
    global _storage_instance

    if backend:
        os.environ["STORAGE_BACKEND"] = backend

    _storage_instance = None  # Reset
    storage = get_storage()
    return storage.is_connected()
