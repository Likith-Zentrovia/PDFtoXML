#!/usr/bin/env python3
"""
MongoDB GridFS Storage for PDF Conversion Pipeline

This module provides file storage using MongoDB GridFS, enabling:
- Persistent file storage across container restarts
- ISBN-based file organization
- Streaming file uploads and downloads
- Stateless container operation

All files are stored with ISBN as the primary key, making it easy to
retrieve all files related to a specific book conversion.

Configuration:
    Uses the same MongoDB connection as mongodb_store.py:
    - MONGODB_URI: MongoDB connection string
    - MONGODB_DATABASE: Database name (default: pdftoxml)

Usage:
    from gridfs_store import get_gridfs_store

    store = get_gridfs_store()

    # Upload a file
    file_id = store.upload_file(isbn="9798275082845", filename="book.pdf", data=pdf_bytes)

    # Download a file
    data = store.download_file(isbn="9798275082845", filename="book.pdf")

    # List files for an ISBN
    files = store.list_files(isbn="9798275082845")
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Dict, Generator, List, Optional, Union

logger = logging.getLogger(__name__)

# Check for pymongo/gridfs availability
try:
    from pymongo import MongoClient
    from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
    import gridfs
    from gridfs import GridFS, GridFSBucket
    from bson import ObjectId
    GRIDFS_AVAILABLE = True
except ImportError:
    GRIDFS_AVAILABLE = False
    GridFS = None
    GridFSBucket = None
    logger.warning("pymongo/gridfs not installed. GridFS features will be disabled.")


# ============================================================================
# GRIDFS STORE
# ============================================================================

class GridFSStore:
    """
    MongoDB GridFS storage for conversion files.

    Provides methods to:
    - Upload files with ISBN-based organization
    - Download files by ISBN and filename
    - List all files for an ISBN
    - Delete files
    - Stream large files efficiently
    """

    # File type categories for organization
    FILE_TYPES = {
        '.pdf': 'source',
        '.xml': 'output',
        '.docx': 'output',
        '.zip': 'package',
        '.xlsx': 'report',
        '.json': 'metadata',
        '.png': 'image',
        '.jpg': 'image',
        '.jpeg': 'image',
        '.gif': 'image',
        '.svg': 'image',
        '.tif': 'image',
        '.tiff': 'image',
    }

    def __init__(self, uri: str = None, database: str = None):
        """
        Initialize GridFS store.

        Args:
            uri: MongoDB connection string. Defaults to MONGODB_URI env var.
            database: Database name. Defaults to MONGODB_DATABASE env var.
        """
        self.uri = uri or os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
        self.database_name = database or os.environ.get("MONGODB_DATABASE", "pdftoxml")
        self._client: Optional[MongoClient] = None
        self._db = None
        self._fs: Optional[GridFS] = None
        self._bucket: Optional[GridFSBucket] = None
        self._connected = False

    def connect(self) -> bool:
        """
        Establish connection to MongoDB and initialize GridFS.

        Returns:
            True if connected successfully, False otherwise.
        """
        if not GRIDFS_AVAILABLE:
            logger.error("pymongo/gridfs is not installed. Run: pip install pymongo")
            return False

        try:
            timeout_ms = int(os.environ.get("MONGODB_TIMEOUT_MS", "5000"))

            self._client = MongoClient(
                self.uri,
                serverSelectionTimeoutMS=timeout_ms,
                retryWrites=True,
            )
            # Test connection
            self._client.admin.command('ping')

            self._db = self._client[self.database_name]
            self._fs = GridFS(self._db)
            self._bucket = GridFSBucket(self._db)

            # Create indexes for efficient queries
            self._create_indexes()

            self._connected = True
            logger.info(f"GridFS connected to MongoDB: {self.database_name}")
            return True

        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            logger.error(f"Failed to connect to MongoDB for GridFS: {e}")
            self._connected = False
            return False
        except Exception as e:
            logger.error(f"Unexpected error connecting to GridFS: {e}")
            self._connected = False
            return False

    def _create_indexes(self):
        """Create indexes for efficient file queries."""
        if self._db is not None:
            # Create compound index on isbn + filename for fast lookups
            self._db.fs.files.create_index([("metadata.isbn", 1), ("filename", 1)])
            self._db.fs.files.create_index("metadata.isbn")
            self._db.fs.files.create_index("metadata.file_type")
            self._db.fs.files.create_index("uploadDate")
            logger.debug("GridFS indexes created")

    def disconnect(self):
        """Close MongoDB connection."""
        if self._client:
            self._client.close()
            self._connected = False
            logger.info("GridFS disconnected from MongoDB")

    @property
    def is_connected(self) -> bool:
        """Check if connected to MongoDB."""
        return self._connected and self._client is not None

    def ensure_connected(self) -> bool:
        """Ensure connection is established, reconnecting if needed."""
        if not self.is_connected:
            return self.connect()
        return True

    # ========================================================================
    # FILE OPERATIONS
    # ========================================================================

    def _get_file_type(self, filename: str) -> str:
        """Determine file type category from filename."""
        ext = Path(filename).suffix.lower()
        return self.FILE_TYPES.get(ext, 'other')

    def upload_file(
        self,
        isbn: str,
        filename: str,
        data: Union[bytes, BinaryIO],
        content_type: str = None,
        metadata: Dict[str, Any] = None,
    ) -> Optional[str]:
        """
        Upload a file to GridFS.

        Args:
            isbn: ISBN number (used as primary key)
            filename: Name of the file
            data: File content as bytes or file-like object
            content_type: MIME type (auto-detected if not provided)
            metadata: Additional metadata to store

        Returns:
            File ID as string, or None if failed
        """
        if not self.ensure_connected():
            logger.error("Cannot upload file: GridFS not connected")
            return None

        try:
            # Delete existing file with same isbn/filename (upsert behavior)
            self.delete_file(isbn, filename)

            # Prepare metadata
            file_metadata = {
                "isbn": isbn,
                "file_type": self._get_file_type(filename),
                "uploaded_at": datetime.utcnow(),
            }
            if metadata:
                file_metadata.update(metadata)

            # Determine content type
            if not content_type:
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
                    '.gif': 'image/gif',
                    '.svg': 'image/svg+xml',
                }
                content_type = content_types.get(ext, 'application/octet-stream')

            # Upload file
            if isinstance(data, bytes):
                file_id = self._fs.put(
                    data,
                    filename=filename,
                    content_type=content_type,
                    metadata=file_metadata,
                )
            else:
                # File-like object
                file_id = self._fs.put(
                    data,
                    filename=filename,
                    content_type=content_type,
                    metadata=file_metadata,
                )

            logger.info(f"Uploaded file to GridFS: {isbn}/{filename} ({file_id})")
            return str(file_id)

        except Exception as e:
            logger.error(f"Failed to upload file to GridFS: {e}")
            return None

    def upload_from_path(
        self,
        isbn: str,
        file_path: Path,
        target_filename: str = None,
        metadata: Dict[str, Any] = None,
    ) -> Optional[str]:
        """
        Upload a file from filesystem path to GridFS.

        Args:
            isbn: ISBN number
            file_path: Path to the file on disk
            target_filename: Name to store as (defaults to file_path.name)
            metadata: Additional metadata

        Returns:
            File ID as string, or None if failed
        """
        if not file_path.exists():
            logger.error(f"File not found: {file_path}")
            return None

        filename = target_filename or file_path.name

        with open(file_path, 'rb') as f:
            return self.upload_file(isbn, filename, f, metadata=metadata)

    def download_file(self, isbn: str, filename: str) -> Optional[bytes]:
        """
        Download a file from GridFS.

        Args:
            isbn: ISBN number
            filename: Name of the file

        Returns:
            File content as bytes, or None if not found
        """
        if not self.ensure_connected():
            logger.error("Cannot download file: GridFS not connected")
            return None

        try:
            # Find file by isbn and filename
            file_doc = self._db.fs.files.find_one({
                "metadata.isbn": isbn,
                "filename": filename,
            })

            if not file_doc:
                logger.warning(f"File not found in GridFS: {isbn}/{filename}")
                return None

            # Download file content
            grid_out = self._fs.get(file_doc["_id"])
            data = grid_out.read()

            logger.debug(f"Downloaded file from GridFS: {isbn}/{filename} ({len(data)} bytes)")
            return data

        except Exception as e:
            logger.error(f"Failed to download file from GridFS: {e}")
            return None

    def download_to_path(
        self,
        isbn: str,
        filename: str,
        target_path: Path,
    ) -> bool:
        """
        Download a file from GridFS to filesystem.

        Args:
            isbn: ISBN number
            filename: Name of the file in GridFS
            target_path: Path to save the file

        Returns:
            True if successful, False otherwise
        """
        data = self.download_file(isbn, filename)
        if data is None:
            return False

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(data)
            logger.debug(f"Downloaded to: {target_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to write file to disk: {e}")
            return False

    def stream_file(self, isbn: str, filename: str) -> Optional[BinaryIO]:
        """
        Get a streaming file handle for reading.

        Args:
            isbn: ISBN number
            filename: Name of the file

        Returns:
            File-like object for streaming, or None if not found
        """
        if not self.ensure_connected():
            return None

        try:
            file_doc = self._db.fs.files.find_one({
                "metadata.isbn": isbn,
                "filename": filename,
            })

            if not file_doc:
                return None

            return self._fs.get(file_doc["_id"])

        except Exception as e:
            logger.error(f"Failed to stream file from GridFS: {e}")
            return None

    @contextmanager
    def temp_file(self, isbn: str, filename: str) -> Generator[Optional[Path], None, None]:
        """
        Context manager that downloads a file to a temporary location.

        Useful for processing files that require filesystem access.
        The temporary file is automatically cleaned up after use.

        Usage:
            with store.temp_file("9798275082845", "book.pdf") as temp_path:
                if temp_path:
                    process_pdf(temp_path)
        """
        temp_path = None
        try:
            data = self.download_file(isbn, filename)
            if data is None:
                yield None
                return

            # Create temp file with proper extension
            suffix = Path(filename).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                f.write(data)
                temp_path = Path(f.name)

            yield temp_path

        finally:
            # Clean up temp file
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

    @contextmanager
    def temp_directory(self, isbn: str, file_types: List[str] = None) -> Generator[Optional[Path], None, None]:
        """
        Context manager that downloads all files for an ISBN to a temp directory.

        Args:
            isbn: ISBN number
            file_types: Optional list of file types to download (e.g., ['source', 'output'])

        Yields:
            Path to temporary directory containing the files
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
            # Clean up temp directory
            if temp_dir and temp_dir.exists():
                import shutil
                try:
                    shutil.rmtree(temp_dir)
                except Exception:
                    pass

    def delete_file(self, isbn: str, filename: str) -> bool:
        """
        Delete a file from GridFS.

        Args:
            isbn: ISBN number
            filename: Name of the file

        Returns:
            True if deleted, False if not found or error
        """
        if not self.ensure_connected():
            return False

        try:
            file_doc = self._db.fs.files.find_one({
                "metadata.isbn": isbn,
                "filename": filename,
            })

            if file_doc:
                self._fs.delete(file_doc["_id"])
                logger.info(f"Deleted file from GridFS: {isbn}/{filename}")
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to delete file from GridFS: {e}")
            return False

    def delete_all_files(self, isbn: str) -> int:
        """
        Delete all files for an ISBN.

        Args:
            isbn: ISBN number

        Returns:
            Number of files deleted
        """
        if not self.ensure_connected():
            return 0

        try:
            files = list(self._db.fs.files.find({"metadata.isbn": isbn}))
            count = 0

            for file_doc in files:
                self._fs.delete(file_doc["_id"])
                count += 1

            if count > 0:
                logger.info(f"Deleted {count} files from GridFS for ISBN: {isbn}")

            return count

        except Exception as e:
            logger.error(f"Failed to delete files from GridFS: {e}")
            return 0

    def list_files(
        self,
        isbn: str,
        file_types: List[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List all files for an ISBN.

        Args:
            isbn: ISBN number
            file_types: Optional filter by file type (source, output, package, etc.)

        Returns:
            List of file info dictionaries
        """
        if not self.ensure_connected():
            return []

        try:
            query = {"metadata.isbn": isbn}
            if file_types:
                query["metadata.file_type"] = {"$in": file_types}

            files = []
            for doc in self._db.fs.files.find(query).sort("uploadDate", -1):
                files.append({
                    "file_id": str(doc["_id"]),
                    "filename": doc["filename"],
                    "size": doc["length"],
                    "content_type": doc.get("contentType", "application/octet-stream"),
                    "file_type": doc.get("metadata", {}).get("file_type", "other"),
                    "uploaded_at": doc.get("uploadDate"),
                    "metadata": doc.get("metadata", {}),
                })

            return files

        except Exception as e:
            logger.error(f"Failed to list files from GridFS: {e}")
            return []

    def file_exists(self, isbn: str, filename: str) -> bool:
        """Check if a file exists in GridFS."""
        if not self.ensure_connected():
            return False

        try:
            return self._db.fs.files.count_documents({
                "metadata.isbn": isbn,
                "filename": filename,
            }) > 0
        except Exception:
            return False

    def get_file_info(self, isbn: str, filename: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific file."""
        if not self.ensure_connected():
            return None

        try:
            doc = self._db.fs.files.find_one({
                "metadata.isbn": isbn,
                "filename": filename,
            })

            if not doc:
                return None

            return {
                "file_id": str(doc["_id"]),
                "filename": doc["filename"],
                "size": doc["length"],
                "content_type": doc.get("contentType", "application/octet-stream"),
                "file_type": doc.get("metadata", {}).get("file_type", "other"),
                "uploaded_at": doc.get("uploadDate"),
                "metadata": doc.get("metadata", {}),
            }

        except Exception as e:
            logger.error(f"Failed to get file info from GridFS: {e}")
            return None

    # ========================================================================
    # BULK OPERATIONS
    # ========================================================================

    def upload_directory(
        self,
        isbn: str,
        directory: Path,
        prefix: str = "",
        patterns: List[str] = None,
    ) -> int:
        """
        Upload all files from a directory to GridFS.

        Args:
            isbn: ISBN number
            directory: Directory path to upload from
            prefix: Optional prefix for filenames in GridFS
            patterns: Optional glob patterns to filter files (e.g., ["*.xml", "*.zip"])

        Returns:
            Number of files uploaded
        """
        if not directory.exists():
            return 0

        count = 0
        files_to_upload = []

        if patterns:
            for pattern in patterns:
                files_to_upload.extend(directory.glob(pattern))
        else:
            files_to_upload = [f for f in directory.iterdir() if f.is_file()]

        for file_path in files_to_upload:
            target_name = f"{prefix}{file_path.name}" if prefix else file_path.name
            if self.upload_from_path(isbn, file_path, target_name):
                count += 1

        return count

    def download_directory(
        self,
        isbn: str,
        target_dir: Path,
        file_types: List[str] = None,
    ) -> int:
        """
        Download all files for an ISBN to a directory.

        Args:
            isbn: ISBN number
            target_dir: Directory to download files to
            file_types: Optional filter by file type

        Returns:
            Number of files downloaded
        """
        target_dir.mkdir(parents=True, exist_ok=True)

        files = self.list_files(isbn, file_types=file_types)
        count = 0

        for file_info in files:
            filename = file_info['filename']
            if self.download_to_path(isbn, filename, target_dir / filename):
                count += 1

        return count

    # ========================================================================
    # STATISTICS
    # ========================================================================

    def get_storage_stats(self) -> Dict[str, Any]:
        """Get overall storage statistics."""
        if not self.ensure_connected():
            return {}

        try:
            pipeline = [
                {
                    "$group": {
                        "_id": None,
                        "total_files": {"$sum": 1},
                        "total_size": {"$sum": "$length"},
                        "unique_isbns": {"$addToSet": "$metadata.isbn"},
                    }
                }
            ]

            result = list(self._db.fs.files.aggregate(pipeline))

            if result:
                data = result[0]
                return {
                    "total_files": data.get("total_files", 0),
                    "total_size_bytes": data.get("total_size", 0),
                    "total_size_mb": round(data.get("total_size", 0) / (1024 * 1024), 2),
                    "unique_isbns": len(data.get("unique_isbns", [])),
                }

            return {
                "total_files": 0,
                "total_size_bytes": 0,
                "total_size_mb": 0,
                "unique_isbns": 0,
            }

        except Exception as e:
            logger.error(f"Failed to get storage stats: {e}")
            return {}

    def get_isbn_stats(self, isbn: str) -> Dict[str, Any]:
        """Get storage statistics for a specific ISBN."""
        if not self.ensure_connected():
            return {}

        try:
            pipeline = [
                {"$match": {"metadata.isbn": isbn}},
                {
                    "$group": {
                        "_id": "$metadata.file_type",
                        "count": {"$sum": 1},
                        "size": {"$sum": "$length"},
                    }
                }
            ]

            result = list(self._db.fs.files.aggregate(pipeline))

            by_type = {item["_id"]: {"count": item["count"], "size": item["size"]} for item in result}
            total_files = sum(item["count"] for item in result)
            total_size = sum(item["size"] for item in result)

            return {
                "isbn": isbn,
                "total_files": total_files,
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "by_type": by_type,
            }

        except Exception as e:
            logger.error(f"Failed to get ISBN stats: {e}")
            return {}


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_gridfs_store: Optional[GridFSStore] = None


def get_gridfs_store() -> GridFSStore:
    """Get or create the singleton GridFS store instance."""
    global _gridfs_store
    if _gridfs_store is None:
        _gridfs_store = GridFSStore()
    return _gridfs_store


def init_gridfs() -> bool:
    """
    Initialize GridFS connection.

    Call this at application startup.

    Returns:
        True if connected successfully
    """
    store = get_gridfs_store()
    return store.connect()
