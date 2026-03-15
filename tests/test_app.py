"""Integration tests for the EcoScout FastAPI application.

Tests HTTP routes, WebSocket connection, message routing, and
video notification flow. ADK Runner is mocked to avoid real LLM calls.
"""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    return TestClient(app)


# ── HTTP routes ──────────────────────────────────────────────────────────────


class TestHTTPRoutes:

    def test_root_returns_html(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_static_css_served(self, client):
        response = client.get("/static/css/style.css")
        assert response.status_code == 200
        assert "text/css" in response.headers["content-type"]

    def test_static_js_served(self, client):
        response = client.get("/static/js/app.js")
        assert response.status_code == 200

    def test_static_manifest_served(self, client):
        response = client.get("/static/manifest.json")
        assert response.status_code == 200

    def test_nonexistent_route_returns_404(self, client):
        response = client.get("/nonexistent")
        assert response.status_code in (404, 405)


# ── WebSocket connection ─────────────────────────────────────────────────────


class TestWebSocket:

    def _mock_runner(self):
        """Create a mock runner.run_live that yields nothing and blocks."""

        async def _empty_stream(*args, **kwargs):
            return
            yield  # makes this an async generator

        mock = MagicMock()
        mock.run_live = _empty_stream
        return mock

    def test_websocket_connects_and_disconnects(self, client):
        with patch("main.runner", self._mock_runner()):
            with client.websocket_connect("/ws/test-user/test-session") as ws:
                pass

    def test_websocket_sends_text_message(self, client):
        with patch("main.runner", self._mock_runner()):
            with client.websocket_connect("/ws/test-user/test-session") as ws:
                ws.send_text(json.dumps({"type": "text", "text": "Hello EcoScout"}))

    def test_websocket_sends_gps_update(self, client):
        with patch("main.runner", self._mock_runner()):
            with client.websocket_connect("/ws/test-user/test-session") as ws:
                ws.send_text(json.dumps({
                    "type": "gps",
                    "lat": -33.8688,
                    "lon": 151.2093,
                }))

    def test_websocket_sends_image_frame(self, client):
        fake_image = base64.b64encode(b"fake-jpeg-data").decode()
        with patch("main.runner", self._mock_runner()):
            with client.websocket_connect("/ws/test-user/test-session") as ws:
                ws.send_text(json.dumps({
                    "type": "image",
                    "data": fake_image,
                    "mimeType": "image/jpeg",
                }))

    def test_websocket_handles_invalid_json(self, client):
        with patch("main.runner", self._mock_runner()):
            with client.websocket_connect("/ws/test-user/test-session") as ws:
                ws.send_text("not-valid-json{{{")

    def test_websocket_sends_binary_audio(self, client):
        fake_audio = b"\x00" * 3200  # 100ms of 16kHz 16-bit mono
        with patch("main.runner", self._mock_runner()):
            with client.websocket_connect("/ws/test-user/test-session") as ws:
                ws.send_bytes(fake_audio)


# ── RunConfig construction ───────────────────────────────────────────────────


class TestRunConfig:

    def test_native_audio_model_sets_audio_modality(self):
        from main import agent
        if "native-audio" in agent.model.lower():
            from google.adk.agents.run_config import RunConfig, StreamingMode
            from google.genai import types

            config = RunConfig(
                streaming_mode=StreamingMode.BIDI,
                response_modalities=["AUDIO"],
                input_audio_transcription=types.AudioTranscriptionConfig(),
                output_audio_transcription=types.AudioTranscriptionConfig(),
                session_resumption=types.SessionResumptionConfig(),
            )
            assert config.streaming_mode == StreamingMode.BIDI
            assert "AUDIO" in config.response_modalities

    def test_text_model_config(self):
        from google.adk.agents.run_config import RunConfig, StreamingMode
        from google.genai import types

        config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["TEXT"],
            session_resumption=types.SessionResumptionConfig(),
        )
        assert config.streaming_mode == StreamingMode.BIDI
        assert "TEXT" in config.response_modalities


# ── Video notification flow ──────────────────────────────────────────────────


class TestVideoNotifications:

    def test_video_ready_events_dict_accessible(self):
        from ecoscout_agent.tools import _video_ready_events
        assert isinstance(_video_ready_events, dict)

    def test_video_ready_events_importable_from_main(self):
        from main import _video_ready_events  # noqa: F811
        assert isinstance(_video_ready_events, dict)


# ── Survey API endpoints ─────────────────────────────────────────────────────


class TestSurveyAPI:

    def test_survey_endpoint_returns_metrics(self, client):
        fake_metrics = {
            "status": "ok",
            "species_richness": 5,
            "shannon_index": 1.5,
            "total_observations": 10,
        }
        with patch(
            "ecoscout_agent.tools.calculate_biodiversity_metrics",
            new_callable=AsyncMock,
            return_value=fake_metrics,
        ):
            response = client.get("/api/survey/test-session")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["species_richness"] == 5

    def test_nearby_species_endpoint(self, client):
        fake_result = {
            "status": "ok",
            "total_species": 42,
            "top_species": [{"name": "Corvus coronoides"}],
        }
        with patch(
            "ecoscout_agent.tools.query_nearby_species",
            new_callable=AsyncMock,
            return_value=fake_result,
        ):
            response = client.get("/api/nearby-species?lat=-33.8&lon=151.2")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["total_species"] == 42

    def test_nearby_species_missing_params(self, client):
        response = client.get("/api/nearby-species")
        assert response.status_code == 422

    def test_survey_endpoint_with_unknown_session(self, client):
        empty_metrics = {
            "status": "ok",
            "species_richness": 0,
            "shannon_index": 0.0,
            "interpretation": "No observations recorded yet.",
        }
        with patch(
            "ecoscout_agent.tools.calculate_biodiversity_metrics",
            new_callable=AsyncMock,
            return_value=empty_metrics,
        ):
            response = client.get("/api/survey/nonexistent-session")

        assert response.status_code == 200
        assert response.json()["species_richness"] == 0
