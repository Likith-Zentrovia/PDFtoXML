"""
API Endpoint Tests for PDF-to-XML Pipeline

Run with: pytest tests/test_api.py -v
"""

import pytest
from fastapi.testclient import TestClient

# Import the app
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from api import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


class TestHealthEndpoint:
    """Tests for /api/v1/health endpoint."""

    def test_health_returns_200(self, client):
        """Health endpoint should return 200."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200

    def test_health_returns_status(self, client):
        """Health endpoint should include status field."""
        response = client.get("/api/v1/health")
        data = response.json()
        assert "status" in data
        assert data["status"] in ["healthy", "degraded", "unhealthy"]


class TestInfoEndpoint:
    """Tests for /api/v1/info endpoint."""

    def test_info_returns_200(self, client):
        """Info endpoint should return 200."""
        response = client.get("/api/v1/info")
        assert response.status_code == 200

    def test_info_contains_name(self, client):
        """Info should contain service name."""
        response = client.get("/api/v1/info")
        data = response.json()
        assert "name" in data

    def test_info_contains_version(self, client):
        """Info should contain version."""
        response = client.get("/api/v1/info")
        data = response.json()
        assert "version" in data


class TestConfigEndpoint:
    """Tests for /api/v1/config/options endpoint."""

    def test_config_options_returns_200(self, client):
        """Config options endpoint should return 200."""
        response = client.get("/api/v1/config/options")
        assert response.status_code == 200

    def test_config_options_contains_options(self, client):
        """Config should contain options object."""
        response = client.get("/api/v1/config/options")
        data = response.json()
        assert "options" in data

    def test_config_options_contains_defaults(self, client):
        """Config should contain defaults object."""
        response = client.get("/api/v1/config/options")
        data = response.json()
        assert "defaults" in data

    def test_config_options_model_dropdown(self, client):
        """Config should include model dropdown options."""
        response = client.get("/api/v1/config/options")
        data = response.json()
        assert "model" in data["options"]
        assert "options" in data["options"]["model"]
        assert len(data["options"]["model"]["options"]) > 0

    def test_config_options_dpi_dropdown(self, client):
        """Config should include DPI dropdown options."""
        response = client.get("/api/v1/config/options")
        data = response.json()
        assert "dpi" in data["options"]
        dpi_values = [opt["value"] for opt in data["options"]["dpi"]["options"]]
        assert 300 in dpi_values


class TestModelsEndpoint:
    """Tests for /api/v1/models endpoint."""

    def test_models_returns_200(self, client):
        """Models endpoint should return 200."""
        response = client.get("/api/v1/models")
        assert response.status_code == 200

    def test_models_contains_list(self, client):
        """Models should contain models list."""
        response = client.get("/api/v1/models")
        data = response.json()
        assert "models" in data
        assert isinstance(data["models"], list)

    def test_models_contains_default(self, client):
        """Models should contain default model."""
        response = client.get("/api/v1/models")
        data = response.json()
        assert "default" in data


class TestJobsEndpoint:
    """Tests for /api/v1/jobs endpoint."""

    def test_list_jobs_returns_200(self, client):
        """List jobs endpoint should return 200."""
        response = client.get("/api/v1/jobs")
        assert response.status_code == 200

    def test_list_jobs_returns_array(self, client):
        """List jobs should return an array."""
        response = client.get("/api/v1/jobs")
        data = response.json()
        assert isinstance(data, list)

    def test_get_nonexistent_job_returns_404(self, client):
        """Getting a non-existent job should return 404."""
        response = client.get("/api/v1/jobs/nonexistent-job-id")
        assert response.status_code == 404


class TestDashboardEndpoint:
    """Tests for /api/v1/dashboard endpoint."""

    def test_dashboard_returns_200(self, client):
        """Dashboard endpoint should return 200."""
        response = client.get("/api/v1/dashboard")
        assert response.status_code == 200

    def test_dashboard_contains_stats(self, client):
        """Dashboard should contain statistics."""
        response = client.get("/api/v1/dashboard")
        data = response.json()
        assert "total_conversions" in data
        assert "successful" in data
        assert "failed" in data


class TestConvertEndpoint:
    """Tests for /api/v1/convert endpoint."""

    def test_convert_without_file_returns_422(self, client):
        """Convert without file should return 422."""
        response = client.post("/api/v1/convert")
        assert response.status_code == 422

    def test_convert_with_invalid_file_returns_400(self, client):
        """Convert with non-PDF file should return 400."""
        response = client.post(
            "/api/v1/convert",
            files={"file": ("test.txt", b"not a pdf", "text/plain")}
        )
        assert response.status_code == 400


class TestOpenAPISpec:
    """Tests for OpenAPI specification."""

    def test_openapi_json_returns_200(self, client):
        """OpenAPI JSON should return 200."""
        response = client.get("/openapi.json")
        assert response.status_code == 200

    def test_openapi_contains_info(self, client):
        """OpenAPI spec should contain info section."""
        response = client.get("/openapi.json")
        data = response.json()
        assert "info" in data

    def test_openapi_contains_paths(self, client):
        """OpenAPI spec should contain paths."""
        response = client.get("/openapi.json")
        data = response.json()
        assert "paths" in data
        assert "/api/v1/convert" in data["paths"]
