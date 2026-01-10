"""
PDF to Word Conversion Service

This service handles the conversion of PDF files to Word documents using the pdf2docx library.
It includes comprehensive error handling, temporary file management, and conversion quality options.

Supports both S3-based and local file operations.
"""

import logging
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
import asyncio
from concurrent.futures import ThreadPoolExecutor

from pdf2docx import Converter
import fitz  # PyMuPDF for PDF validation

# Core imports - made optional for standalone usage
try:
    from app.core.config import settings
    HAS_APP_CONFIG = True
except ImportError:
    HAS_APP_CONFIG = False
    settings = None

try:
    from app.services.s3_service import s3_service
    HAS_S3 = True
except ImportError:
    HAS_S3 = False
    s3_service = None

try:
    from app.services.ai_pdf_conversion_service import ai_pdf_conversion_service
    HAS_AI_SERVICE = True
except ImportError:
    HAS_AI_SERVICE = False
    ai_pdf_conversion_service = None

try:
    from app.services.qc_highlight_service import qc_highlight_service
    HAS_QC = True
except ImportError:
    HAS_QC = False
    qc_highlight_service = None

logger = logging.getLogger(__name__)

class ConversionError(Exception):
    """Custom exception for PDF conversion errors."""
    pass

class ConversionQuality:
    """Conversion quality settings."""
    STANDARD = "standard"
    HIGH = "high"

class PDFConversionService:
    """Service for converting PDF files to Word documents."""

    def __init__(self):
        """Initialize the PDF conversion service."""
        self.temp_dir = Path(tempfile.gettempdir()) / "manuscript_processor"
        self.temp_dir.mkdir(exist_ok=True)
        
        # Thread pool for CPU-intensive conversion tasks
        self.executor = ThreadPoolExecutor(max_workers=2)
        
        logger.info(f"PDF Conversion Service initialized with temp dir: {self.temp_dir}")

    async def convert_pdf_to_docx_ai(
        self,
        pdf_s3_key: str,
        output_filename: str,
        quality: str = ConversionQuality.STANDARD,
        include_metadata: bool = True
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Convert a PDF file using AI-powered conversion (PDF → Images → Markdown → DOCX).
        
        Args:
            pdf_s3_key: S3 key of the source PDF file
            output_filename: Desired filename for the output DOCX file
            quality: Conversion quality (affects image DPI)
            include_metadata: Whether to include document metadata
            
        Returns:
            Tuple of (docx_s3_key, conversion_metadata)
            
        Raises:
            ConversionError: If conversion fails
        """
        conversion_id = str(uuid.uuid4())
        start_time = datetime.utcnow()
        
        logger.info(f"Starting AI-powered PDF conversion [{conversion_id}]: {pdf_s3_key} -> {output_filename}")
        
        try:
            # Generate S3 key for output file
            docx_s3_key = f"converted/{conversion_id}-{output_filename}"
            if not docx_s3_key.endswith('.docx'):
                docx_s3_key = docx_s3_key.replace('.pdf', '.docx')
                if not docx_s3_key.endswith('.docx'):
                    docx_s3_key += '.docx'
            
            # Use the AI-powered conversion service
            success, error_message, ai_metadata = await ai_pdf_conversion_service.convert_pdf_to_docx(
                pdf_s3_key, docx_s3_key
            )
            
            if not success:
                raise ConversionError(f"AI conversion failed: {error_message}")
            
            # Calculate processing time
            end_time = datetime.utcnow()
            processing_time = (end_time - start_time).total_seconds()
            
            # Combine metadata in the expected format
            final_metadata = {
                "conversion_id": conversion_id,
                "source_pdf_key": pdf_s3_key,
                "output_docx_key": docx_s3_key,
                "conversion_start": start_time.isoformat(),
                "conversion_end": end_time.isoformat(),
                "conversion_duration_seconds": round(processing_time, 2),
                "quality": quality,
                "include_metadata": include_metadata,
                "conversion_type": "ai_powered",
                "success": True,
                **ai_metadata  # Include AI-specific metadata
            }
            
            logger.info(f"AI-powered PDF conversion completed successfully [{conversion_id}]: {processing_time:.2f}s")
            
            return docx_s3_key, final_metadata
            
        except Exception as e:
            error_msg = f"AI-powered PDF conversion failed [{conversion_id}]: {str(e)}"
            logger.error(error_msg)
            raise ConversionError(error_msg)

    async def convert_pdf_to_docx(
        self,
        pdf_s3_key: str,
        output_filename: str,
        quality: str = ConversionQuality.STANDARD,
        include_metadata: bool = True
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Convert a PDF file from S3 to Word document and upload back to S3.
        
        Args:
            pdf_s3_key: S3 key of the source PDF file
            output_filename: Desired filename for the output DOCX file
            quality: Conversion quality ('standard' or 'high')
            include_metadata: Whether to include document metadata
            
        Returns:
            Tuple of (docx_s3_key, conversion_metadata)
            
        Raises:
            ConversionError: If conversion fails
        """
        conversion_id = str(uuid.uuid4())
        start_time = datetime.utcnow()
        
        logger.info(f"Starting PDF conversion [{conversion_id}]: {pdf_s3_key} -> {output_filename}")
        
        # Create temporary file paths
        pdf_temp_path = self.temp_dir / f"{conversion_id}_input.pdf"
        docx_temp_path = self.temp_dir / f"{conversion_id}_output.docx"
        
        try:
            # Step 1: Download PDF from S3
            logger.info(f"Downloading PDF from S3: {pdf_s3_key}")
            await self._download_from_s3(pdf_s3_key, pdf_temp_path)
            
            # Step 2: Validate PDF file
            pdf_info = await self._validate_pdf(pdf_temp_path)
            logger.info(f"PDF validation successful: {pdf_info['pages']} pages, {pdf_info['size_mb']:.2f} MB")
            
            # Step 3: Convert PDF to DOCX
            logger.info(f"Converting PDF to DOCX with quality: {quality}")
            conversion_stats = await self._convert_pdf_to_docx_async(
                pdf_temp_path, 
                docx_temp_path, 
                quality,
                include_metadata
            )
            
            # Step 4: QC pass - highlight additions/edits and append deletions section
            try:
                qc_meta = qc_highlight_service.run_qc_sync(str(pdf_temp_path), str(docx_temp_path))
            except Exception as qc_error:
                logger.warning(f"QC highlight failed: {qc_error}")
                qc_meta = {"qc": {"error": str(qc_error)}}

            # Step 5: Upload DOCX to S3
            docx_s3_key = f"converted/{uuid.uuid4()}-{output_filename}"
            if not docx_s3_key.endswith('.docx'):
                docx_s3_key = docx_s3_key.replace('.pdf', '.docx')
                if not docx_s3_key.endswith('.docx'):
                    docx_s3_key += '.docx'
            
            logger.info(f"Uploading DOCX to S3: {docx_s3_key}")
            await self._upload_to_s3(docx_temp_path, docx_s3_key)
            
            # Step 6: Prepare conversion metadata
            end_time = datetime.utcnow()
            conversion_duration = (end_time - start_time).total_seconds()
            
            metadata = {
                "conversion_id": conversion_id,
                "source_pdf_key": pdf_s3_key,
                "output_docx_key": docx_s3_key,
                "conversion_start": start_time.isoformat(),
                "conversion_end": end_time.isoformat(),
                "conversion_duration_seconds": conversion_duration,
                "quality": quality,
                "include_metadata": include_metadata,
                "pdf_info": pdf_info,
                "conversion_stats": conversion_stats,
                "success": True,
                **qc_meta
            }
            
            logger.info(f"PDF conversion completed successfully [{conversion_id}]: {conversion_duration:.2f}s")
            return docx_s3_key, metadata
            
        except Exception as e:
            error_msg = f"PDF conversion failed [{conversion_id}]: {str(e)}"
            logger.error(error_msg, exc_info=True)
            
            # Prepare error metadata
            end_time = datetime.utcnow()
            conversion_duration = (end_time - start_time).total_seconds()
            
            error_metadata = {
                "conversion_id": conversion_id,
                "source_pdf_key": pdf_s3_key,
                "conversion_start": start_time.isoformat(),
                "conversion_end": end_time.isoformat(),
                "conversion_duration_seconds": conversion_duration,
                "quality": quality,
                "error": str(e),
                "error_type": type(e).__name__,
                "success": False
            }
            
            raise ConversionError(error_msg) from e
            
        finally:
            # Cleanup temporary files
            await self._cleanup_temp_files([pdf_temp_path, docx_temp_path])

    async def _download_from_s3(self, s3_key: str, local_path: Path) -> None:
        """Download a file from S3 to local path."""
        try:
            # Use S3 service to download file
            download_url = s3_service.generate_presigned_download_url(s3_key)
            if not download_url:
                raise ConversionError(f"Failed to generate download URL for {s3_key}")
            
            # Download file using aiohttp or similar
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(download_url) as response:
                    if response.status != 200:
                        raise ConversionError(f"Failed to download file from S3: HTTP {response.status}")
                    
                    with open(local_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            f.write(chunk)
                            
            logger.debug(f"Downloaded {s3_key} to {local_path}")
            
        except Exception as e:
            raise ConversionError(f"Failed to download {s3_key} from S3: {str(e)}") from e

    async def _upload_to_s3(self, local_path: Path, s3_key: str) -> None:
        """Upload a local file to S3."""
        try:
            # Use S3 service to upload file
            success = s3_service.upload_file(str(local_path), s3_key)
            if not success:
                raise ConversionError(f"Failed to upload {local_path} to S3 key {s3_key}")
                
            logger.debug(f"Uploaded {local_path} to {s3_key}")
            
        except Exception as e:
            raise ConversionError(f"Failed to upload {local_path} to S3: {str(e)}") from e

    async def _validate_pdf(self, pdf_path: Path) -> Dict[str, Any]:
        """Validate PDF file and extract basic information."""
        try:
            # Run PDF validation in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(self.executor, self._validate_pdf_sync, pdf_path)
            
        except Exception as e:
            raise ConversionError(f"PDF validation failed: {str(e)}") from e

    def _validate_pdf_sync(self, pdf_path: Path) -> Dict[str, Any]:
        """Synchronous PDF validation using PyMuPDF."""
        try:
            doc = fitz.open(str(pdf_path))
            
            if doc.is_encrypted:
                doc.close()
                raise ConversionError("PDF is password protected and cannot be converted")
            
            page_count = doc.page_count
            if page_count == 0:
                doc.close()
                raise ConversionError("PDF has no pages")
            
            if page_count > 100:  # Configurable limit
                doc.close()
                raise ConversionError(f"PDF has too many pages ({page_count}). Maximum allowed: 100")
            
            # Get file size
            file_size = pdf_path.stat().st_size
            size_mb = file_size / (1024 * 1024)
            
            # Get basic metadata
            metadata = doc.metadata
            
            doc.close()
            
            return {
                "pages": page_count,
                "size_bytes": file_size,
                "size_mb": size_mb,
                "title": metadata.get("title", ""),
                "author": metadata.get("author", ""),
                "subject": metadata.get("subject", ""),
                "creator": metadata.get("creator", ""),
                "producer": metadata.get("producer", ""),
                "creation_date": metadata.get("creationDate", ""),
                "modification_date": metadata.get("modDate", "")
            }
            
        except Exception as e:
            raise ConversionError(f"Failed to validate PDF: {str(e)}") from e

    async def _convert_pdf_to_docx_async(
        self, 
        pdf_path: Path, 
        docx_path: Path, 
        quality: str,
        include_metadata: bool
    ) -> Dict[str, Any]:
        """Convert PDF to DOCX asynchronously."""
        try:
            # Run conversion in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                self.executor, 
                self._convert_pdf_to_docx_sync, 
                pdf_path, 
                docx_path, 
                quality,
                include_metadata
            )
            
        except Exception as e:
            raise ConversionError(f"PDF to DOCX conversion failed: {str(e)}") from e

    def _convert_pdf_to_docx_sync(
        self, 
        pdf_path: Path, 
        docx_path: Path, 
        quality: str,
        include_metadata: bool
    ) -> Dict[str, Any]:
        """Synchronous PDF to DOCX conversion using pdf2docx."""
        conversion_start = datetime.utcnow()
        
        try:
            # Configure conversion parameters based on quality
            if quality == ConversionQuality.HIGH:
                # High quality settings
                converter_params = {
                    "start": 0,  # Start page
                    "end": None,  # End page (None = all pages)
                    "pages": None,  # Specific pages (None = all pages)
                    "password": None,  # PDF password
                    "multi_processing": False,  # Disable for stability
                    "cpu_count": 1,  # Single CPU for stability
                }
            else:
                # Standard quality settings (faster)
                converter_params = {
                    "start": 0,
                    "end": None,
                    "pages": None,
                    "password": None,
                    "multi_processing": False,
                    "cpu_count": 1,
                }
            
            # Perform conversion
            converter = Converter(str(pdf_path))
            converter.convert(str(docx_path), **converter_params)
            converter.close()
            
            conversion_end = datetime.utcnow()
            conversion_duration = (conversion_end - conversion_start).total_seconds()
            
            # Get output file size
            output_size = docx_path.stat().st_size
            output_size_mb = output_size / (1024 * 1024)
            
            return {
                "conversion_duration_seconds": conversion_duration,
                "output_size_bytes": output_size,
                "output_size_mb": output_size_mb,
                "quality": quality,
                "include_metadata": include_metadata,
                "converter_params": converter_params
            }
            
        except Exception as e:
            raise ConversionError(f"pdf2docx conversion failed: {str(e)}") from e

    async def _cleanup_temp_files(self, file_paths: list[Path]) -> None:
        """Clean up temporary files."""
        for file_path in file_paths:
            try:
                if file_path.exists():
                    file_path.unlink()
                    logger.debug(f"Cleaned up temporary file: {file_path}")
            except Exception as e:
                logger.warning(f"Failed to cleanup temporary file {file_path}: {e}")

    async def get_conversion_capabilities(self) -> Dict[str, Any]:
        """Get information about conversion capabilities and limits."""
        return {
            "supported_input_formats": ["pdf"],
            "supported_output_formats": ["docx"],
            "max_pages": 100,
            "max_file_size_mb": 50,
            "quality_options": [ConversionQuality.STANDARD, ConversionQuality.HIGH],
            "features": {
                "text_extraction": True,
                "image_extraction": True,
                "table_extraction": True,
                "formatting_preservation": True,
                "metadata_preservation": True,
                "password_protected_pdfs": False
            },
            "temp_directory": str(self.temp_dir),
            "thread_pool_workers": self.executor._max_workers
        }

    async def cleanup_old_temp_files(self, max_age_hours: int = 24) -> int:
        """Clean up old temporary files."""
        cleanup_count = 0
        current_time = datetime.utcnow().timestamp()
        max_age_seconds = max_age_hours * 3600
        
        try:
            for file_path in self.temp_dir.glob("*"):
                if file_path.is_file():
                    file_age = current_time - file_path.stat().st_mtime
                    if file_age > max_age_seconds:
                        try:
                            file_path.unlink()
                            cleanup_count += 1
                            logger.debug(f"Cleaned up old temp file: {file_path}")
                        except Exception as e:
                            logger.warning(f"Failed to cleanup old temp file {file_path}: {e}")
                            
        except Exception as e:
            logger.error(f"Error during temp file cleanup: {e}")
            
        if cleanup_count > 0:
            logger.info(f"Cleaned up {cleanup_count} old temporary files")

        return cleanup_count

    async def convert_pdf_to_docx_local(
        self,
        pdf_path: str,
        output_dir: Optional[str] = None,
        output_docx: Optional[str] = None,
        quality: str = "standard",
        run_qc: bool = False
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Convert a local PDF file to DOCX using pdf2docx library (standard conversion).

        This method works entirely with local files, no S3 required.

        Args:
            pdf_path: Path to the input PDF file
            output_dir: Directory for output files. Defaults to same dir as PDF.
            output_docx: Custom path for DOCX output. Auto-generated if None.
            quality: Conversion quality ('standard' or 'high')
            run_qc: Whether to run QC highlighting (default: False)

        Returns:
            Tuple of (docx_path, conversion_metadata)

        Raises:
            ConversionError: If conversion fails
        """
        conversion_id = str(uuid.uuid4())
        start_time = datetime.utcnow()
        pdf_path = Path(pdf_path).resolve()

        if not pdf_path.exists():
            raise ConversionError(f"PDF file not found: {pdf_path}")

        # Determine output paths
        pdf_dir = pdf_path.parent
        pdf_basename = pdf_path.stem

        if output_dir is None:
            output_dir = pdf_dir
        else:
            output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if output_docx is None:
            output_docx = output_dir / f"{pdf_basename}_converted.docx"
        else:
            output_docx = Path(output_docx)

        logger.info(f"Starting local PDF conversion [{conversion_id}]: {pdf_path} -> {output_docx}")

        try:
            # Step 1: Validate PDF file
            pdf_info = await self._validate_pdf(pdf_path)
            logger.info(f"PDF validation successful: {pdf_info['pages']} pages, {pdf_info['size_mb']:.2f} MB")

            # Step 2: Convert PDF to DOCX
            logger.info(f"Converting PDF to DOCX with quality: {quality}")
            conversion_stats = await self._convert_pdf_to_docx_async(
                pdf_path,
                output_docx,
                quality,
                include_metadata=True
            )

            # Step 3: Optional QC highlighting
            qc_meta = {}
            if run_qc and HAS_QC and qc_highlight_service:
                logger.info("Running QC highlight...")
                try:
                    qc_meta = qc_highlight_service.run_qc_sync(str(pdf_path), str(output_docx))
                except Exception as qc_error:
                    logger.warning(f"QC highlight failed: {qc_error}")
                    qc_meta = {"qc": {"error": str(qc_error)}}
            else:
                logger.info("Skipping QC highlight (disabled)")

            # Prepare conversion metadata
            end_time = datetime.utcnow()
            conversion_duration = (end_time - start_time).total_seconds()

            metadata = {
                "conversion_id": conversion_id,
                "input_pdf": str(pdf_path),
                "output_docx": str(output_docx),
                "conversion_start": start_time.isoformat(),
                "conversion_end": end_time.isoformat(),
                "conversion_duration_seconds": round(conversion_duration, 2),
                "quality": quality,
                "pdf_info": pdf_info,
                "conversion_stats": conversion_stats,
                "conversion_method": "standard (pdf2docx)",
                "success": True,
                **qc_meta
            }

            logger.info(f"Local PDF conversion completed successfully [{conversion_id}]: {conversion_duration:.2f}s")
            return str(output_docx), metadata

        except Exception as e:
            error_msg = f"Local PDF conversion failed [{conversion_id}]: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise ConversionError(error_msg) from e


# Global service instance
pdf_conversion_service = PDFConversionService()
