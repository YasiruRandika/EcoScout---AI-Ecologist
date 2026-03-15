"""Shared fixtures and mock factories for EcoScout tests."""

import os
import sys
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure the app package is importable from the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


# ── Environment ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch):
    """Provide a dummy API key so imports don't fail on missing credentials."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-real")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GCS_BUCKET_NAME", "test-bucket")


# ── Reset global singletons between tests ────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_tool_globals():
    """Clear lazy-initialized clients and shared state before each test."""
    import ecoscout_agent.tools as tools_mod

    tools_mod._genai_client = None
    tools_mod._firestore_db = None
    tools_mod._storage_client = None
    tools_mod._video_ready_events.clear()
    tools_mod._video_started_events.clear()
    yield
    tools_mod._genai_client = None
    tools_mod._firestore_db = None
    tools_mod._storage_client = None
    tools_mod._video_ready_events.clear()
    tools_mod._video_started_events.clear()


# ── Mock factories ───────────────────────────────────────────────────────────

@pytest.fixture
def mock_genai_client():
    """Return a MagicMock pretending to be google.genai.Client."""
    client = MagicMock()
    client.models.generate_content.return_value = _fake_generate_content_response()
    client.models.generate_videos.return_value = _fake_video_operation(done=False)
    client.operations.get.return_value = _fake_video_operation(done=True)
    client.files.download.return_value = b"fake-video-bytes"
    return client


@pytest.fixture
def mock_firestore_db():
    """Return a MagicMock pretending to be firestore.AsyncClient.

    Firestore chains like db.collection().document().set() use synchronous
    navigation (.collection/.document) with only the terminal operations
    (.set/.get/.update/.stream) being async.
    """
    db = MagicMock()

    doc_ref = MagicMock()

    get_result = MagicMock()
    get_result.exists = True
    get_result.to_dict.return_value = {
        "prompt": "Original prompt text",
        "species": "Amanita muscaria",
        "status": "ready",
        "url": "https://storage.example.com/video.mp4",
    }
    doc_ref.get = AsyncMock(return_value=get_result)
    doc_ref.set = AsyncMock()
    doc_ref.update = AsyncMock()

    sub_doc_ref = MagicMock()
    sub_doc_ref.set = AsyncMock()
    sub_doc_ref.get = AsyncMock(return_value=get_result)

    sub_collection = MagicMock()
    sub_collection.document.return_value = sub_doc_ref

    doc_ref.collection.return_value = sub_collection

    db.collection.return_value.document.return_value = doc_ref

    _setup_stream_mocks(db)
    return db


@pytest.fixture
def mock_storage_client():
    """Return a MagicMock pretending to be storage.Client."""
    storage = MagicMock()
    blob = MagicMock()
    blob.generate_signed_url.return_value = "https://storage.example.com/signed-url"
    storage.bucket.return_value.blob.return_value = blob
    return storage


@pytest.fixture
def patch_all_clients(mock_genai_client, mock_firestore_db, mock_storage_client):
    """Patch all three lazy client getters in tools.py at once."""
    with (
        patch("ecoscout_agent.tools._get_genai_client", return_value=mock_genai_client),
        patch("ecoscout_agent.tools._get_firestore_db", return_value=mock_firestore_db),
        patch("ecoscout_agent.tools._get_storage_client", return_value=mock_storage_client),
    ):
        yield {
            "genai": mock_genai_client,
            "firestore": mock_firestore_db,
            "storage": mock_storage_client,
        }


# ── Helpers for building mock responses ──────────────────────────────────────

def _fake_generate_content_response():
    """Build a mock genai response with text + image parts."""
    text_part = MagicMock()
    text_part.text = "A beautiful field guide entry for the specimen."
    text_part.inline_data = None

    image_part = MagicMock()
    image_part.text = None
    image_part.inline_data = MagicMock()
    image_part.inline_data.data = b"fake-png-bytes"

    response = MagicMock()
    response.candidates = [MagicMock()]
    response.candidates[0].content.parts = [text_part, image_part]
    return response


def _fake_video_operation(done: bool):
    """Build a mock Veo 3.1 operation object."""
    op = MagicMock()
    op.done = done
    op.name = "operations/fake-op-123"
    if done:
        video_obj = MagicMock()
        video_obj.video = MagicMock()
        op.response.generated_videos = [video_obj]
    return op


def _setup_stream_mocks(db):
    """Configure async iteration for Firestore collection streams.

    Used by create_expedition_summary which navigates:
      db.collection("sessions").document(sid).collection("observations").stream()
      db.collection("sessions").document(sid).collection("field_entries").stream()
      db.collection("videos").where(...).stream()
    """

    async def _obs_stream():
        for item in [
            {"species_name": "Amanita muscaria", "common_name": "Fly Agaric"},
            {"species_name": "Quercus robur", "common_name": "English Oak"},
        ]:
            mock_doc = MagicMock()
            mock_doc.to_dict.return_value = item
            yield mock_doc

    async def _entries_stream():
        for item in [
            {"species_name": "Amanita muscaria", "image_url": "https://example.com/img.png"},
        ]:
            mock_doc = MagicMock()
            mock_doc.to_dict.return_value = item
            yield mock_doc

    async def _videos_stream():
        for item in [
            {"species": "Amanita muscaria", "status": "ready", "url": "https://example.com/v.mp4"},
        ]:
            mock_doc = MagicMock()
            mock_doc.to_dict.return_value = item
            yield mock_doc

    videos_query = MagicMock()
    videos_query.stream.return_value = _videos_stream()

    def _route_subcollection(name):
        sub = MagicMock()
        sub_doc = MagicMock()
        sub_doc.set = AsyncMock()
        sub.document.return_value = sub_doc
        if name == "observations":
            sub.stream.return_value = _obs_stream()
        elif name == "field_entries":
            sub.stream.return_value = _entries_stream()
        return sub

    doc_ref = db.collection.return_value.document.return_value
    doc_ref.collection.side_effect = _route_subcollection

    db.collection.return_value.where.return_value = videos_query
