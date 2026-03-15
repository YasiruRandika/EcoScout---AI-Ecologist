"""Unit tests for EcoScout tool functions.

All external services (GenAI, Firestore, GCS, iNaturalist, Open-Meteo) are mocked.
Tests validate return structures, logging, error handling, and data flow.
"""

import asyncio
import logging
import math
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ecoscout_agent.tools import (
    _poll_video_completion,
    _video_ready_events,
    create_expedition_summary,
    extend_video,
    generate_field_entry,
    generate_nature_video,
    identify_specimen,
    record_observation,
    query_nearby_species,
    get_species_info,
    get_area_species_checklist,
    get_weather_context,
    calculate_biodiversity_metrics,
    generate_survey_report,
)


# ── identify_specimen ────────────────────────────────────────────────────────


class TestIdentifySpecimen:

    async def test_returns_structured_result(self):
        result = await identify_specimen(
            description="Red cap with white spots, approximately 15cm tall",
            common_features="Red pileus, white universal veil remnants",
            habitat_context="Under European oak, damp leaf litter",
            season="autumn",
        )

        assert result["status"] == "identified"
        assert result["description"] == "Red cap with white spots, approximately 15cm tall"
        assert result["common_features"] == "Red pileus, white universal veil remnants"
        assert result["habitat_context"] == "Under European oak, damp leaf litter"
        assert result["season"] == "autumn"
        assert "note" in result

    async def test_optional_params_default_to_empty(self):
        result = await identify_specimen(
            description="A small green leaf",
            common_features="Serrated edges",
        )

        assert result["habitat_context"] == ""
        assert result["season"] == ""

    async def test_logs_invocation(self, caplog):
        with caplog.at_level(logging.INFO, logger="ecoscout_agent.tools"):
            await identify_specimen(
                description="Bright yellow lichen on granite rock surface",
                common_features="Crusty, yellow-green thallus",
            )

        assert any("[TOOL CALLED] identify_specimen" in r.message for r in caplog.records)


# ── record_observation ───────────────────────────────────────────────────────


class TestRecordObservation:

    async def test_success(self, patch_all_clients):
        result = await record_observation(
            species_name="Amanita muscaria",
            common_name="Fly Agaric",
            description="Red cap with white spots",
            ecological_notes="Mycorrhizal with birch",
            confidence_level="high",
            safety_warnings="TOXIC - do not consume",
            gps_lat=-33.8688,
            gps_lon=151.2093,
            session_id="test-session",
        )

        assert result["status"] == "recorded"
        assert result["species_name"] == "Amanita muscaria"
        assert result["common_name"] == "Fly Agaric"
        assert "observation_id" in result
        assert "timestamp" in result

        db = patch_all_clients["firestore"]
        db.collection.assert_called()

    async def test_firestore_failure_is_non_critical(self):
        mock_db = MagicMock()
        sub_doc = MagicMock()
        sub_doc.set = AsyncMock(side_effect=Exception("Firestore unavailable"))
        mock_db.collection.return_value.document.return_value \
            .collection.return_value.document.return_value = sub_doc

        with patch("ecoscout_agent.tools._get_firestore_db", return_value=mock_db):
            result = await record_observation(
                species_name="Quercus robur",
                common_name="English Oak",
                description="Large deciduous tree",
                ecological_notes="Dominant canopy species",
                confidence_level="high",
            )

        assert result["status"] == "recorded"
        assert result["species_name"] == "Quercus robur"


# ── generate_field_entry ─────────────────────────────────────────────────────


class TestGenerateFieldEntry:

    async def test_success(self, patch_all_clients):
        result = await generate_field_entry(
            species_name="Amanita muscaria",
            common_name="Fly Agaric",
            description="Red cap with white spots, bulbous stem",
            habitat="Birch forest, temperate climate",
            gps_lat=-33.8688,
            gps_lon=151.2093,
            session_id="test-session",
        )

        assert result["status"] == "created"
        assert "entry_id" in result
        assert "text_content" in result
        assert "image_url" in result

        genai = patch_all_clients["genai"]
        genai.models.generate_content.assert_called_once()

        storage = patch_all_clients["storage"]
        storage.bucket.assert_called()

    async def test_api_failure_returns_error(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("API quota exceeded")

        with patch("ecoscout_agent.tools._get_genai_client", return_value=mock_client):
            result = await generate_field_entry(
                species_name="Quercus robur",
                common_name="English Oak",
                description="Large deciduous tree",
                habitat="Temperate broadleaf forest",
            )

        assert result["status"] == "error"
        assert "API quota exceeded" in result["error"]


# ── generate_nature_video ────────────────────────────────────────────────────


class TestGenerateNatureVideo:

    async def test_starts_generation(self, patch_all_clients):
        result = await generate_nature_video(
            species_name="Amanita muscaria",
            process_description="mushroom growth cycle from mycelium to mature fruiting body",
            ecological_context="beneath European oak roots, decomposing autumn leaf litter",
            visual_style="photorealistic nature documentary",
            session_id="test-session",
        )

        assert result["status"] == "generating"
        assert "video_id" in result
        assert result["estimated_wait_seconds"] == 60

        genai = patch_all_clients["genai"]
        genai.models.generate_videos.assert_called_once()

    async def test_prompt_contains_context(self, patch_all_clients, caplog):
        with caplog.at_level(logging.INFO, logger="ecoscout_agent.tools"):
            await generate_nature_video(
                species_name="Danaus plexippus",
                process_description="butterfly metamorphosis",
                ecological_context="milkweed patch in North American meadow",
            )

        prompt_logs = [r.message for r in caplog.records if "[VIDEO] Prompt:" in r.message]
        assert len(prompt_logs) == 1
        prompt = prompt_logs[0]
        assert "Danaus plexippus" in prompt
        assert "butterfly metamorphosis" in prompt
        assert "milkweed patch" in prompt

    async def test_logs_invocation(self, patch_all_clients, caplog):
        with caplog.at_level(logging.INFO, logger="ecoscout_agent.tools"):
            await generate_nature_video(
                species_name="Amanita muscaria",
                process_description="growth cycle",
                ecological_context="oak forest floor",
            )

        assert any("[TOOL CALLED] generate_nature_video" in r.message for r in caplog.records)

    async def test_api_failure_returns_error(self):
        mock_client = MagicMock()
        mock_client.models.generate_videos.side_effect = Exception("Veo 3.1 unavailable")

        mock_db = AsyncMock()

        with (
            patch("ecoscout_agent.tools._get_genai_client", return_value=mock_client),
            patch("ecoscout_agent.tools._get_firestore_db", return_value=mock_db),
        ):
            result = await generate_nature_video(
                species_name="Quercus robur",
                process_description="acorn germination",
                ecological_context="temperate forest",
            )

        assert result["status"] == "error"
        assert "Veo 3.1 unavailable" in result["error"]


# ── _poll_video_completion ───────────────────────────────────────────────────


class TestPollVideoCompletion:

    async def test_success(self):
        mock_client = MagicMock()

        op_pending = MagicMock()
        op_pending.done = False

        op_done = MagicMock()
        op_done.done = True
        video_obj = MagicMock()
        video_obj.video = MagicMock()
        # Code checks result first, then response
        op_done.result = None
        op_done.response.generated_videos = [video_obj]

        mock_client.operations.get.return_value = op_done
        mock_client.files.download.return_value = b"fake-mp4-bytes"

        blob_mock = MagicMock()
        blob_mock.generate_signed_url.return_value = "https://storage.example.com/video.mp4"
        mock_storage = MagicMock()
        mock_storage.bucket.return_value.blob.return_value = blob_mock

        mock_db = MagicMock()
        doc_ref = MagicMock()
        doc_ref.update = AsyncMock()
        mock_db.collection.return_value.document.return_value = doc_ref

        with (
            patch("ecoscout_agent.tools._get_genai_client", return_value=mock_client),
            patch("ecoscout_agent.tools._get_storage_client", return_value=mock_storage),
            patch("ecoscout_agent.tools._get_firestore_db", return_value=mock_db),
            patch("ecoscout_agent.tools.asyncio.sleep", new_callable=AsyncMock),
        ):
            await _poll_video_completion("vid-001", op_pending, "test-session")

        assert "vid-001" in _video_ready_events
        assert _video_ready_events["vid-001"]["status"] == "ready"
        assert "storage.example.com" in _video_ready_events["vid-001"]["url"]

    async def test_failure_updates_firestore_status(self):
        mock_client = MagicMock()
        mock_client.operations.get.side_effect = Exception("Polling timeout")

        op = MagicMock()
        op.done = False

        mock_db = MagicMock()
        doc_ref = MagicMock()
        doc_ref.update = AsyncMock()
        mock_db.collection.return_value.document.return_value = doc_ref

        with (
            patch("ecoscout_agent.tools._get_genai_client", return_value=mock_client),
            patch("ecoscout_agent.tools._get_firestore_db", return_value=mock_db),
            patch("ecoscout_agent.tools.asyncio.sleep", new_callable=AsyncMock),
        ):
            await _poll_video_completion("vid-fail", op, "test-session")

        doc_ref.update.assert_called()
        call_args = doc_ref.update.call_args
        assert call_args[0][0]["status"] == "failed"


# ── extend_video ─────────────────────────────────────────────────────────────


class TestExtendVideo:

    async def test_success(self, patch_all_clients):
        result = await extend_video(
            video_id="original-vid",
            extension_description="spore release and wind dispersal phase",
            session_id="test-session",
        )

        assert result["status"] == "generating"
        assert "video_id" in result
        assert result["extends"] == "original-vid"
        assert "spore release" in result["message"]

    async def test_not_found(self):
        mock_db = MagicMock()
        doc_mock = MagicMock()
        doc_mock.exists = False
        doc_ref = MagicMock()
        doc_ref.get = AsyncMock(return_value=doc_mock)
        mock_db.collection.return_value.document.return_value = doc_ref

        with patch("ecoscout_agent.tools._get_firestore_db", return_value=mock_db):
            result = await extend_video(
                video_id="nonexistent-vid",
                extension_description="next phase",
            )

        assert result["status"] == "error"
        assert "not found" in result["error"]


# ── create_expedition_summary ────────────────────────────────────────────────


class TestCreateExpeditionSummary:

    async def test_success(self, patch_all_clients):
        result = await create_expedition_summary(session_id="test-session")

        assert result["status"] == "compiled"
        assert result["session_id"] == "test-session"
        assert result["total_observations"] == 2
        assert result["total_field_entries"] == 1
        assert result["total_videos"] == 1
        assert len(result["species_observed"]) >= 1

    async def test_firestore_failure_returns_empty_summary(self):
        """When Firestore fails, return compiled summary with empty data (graceful degradation)."""
        mock_db = MagicMock()
        mock_db.collection.side_effect = Exception("Firestore connection refused")

        with patch("ecoscout_agent.tools._get_firestore_db", return_value=mock_db):
            result = await create_expedition_summary(session_id="broken")

        assert result["status"] == "compiled"
        assert result["total_observations"] == 0
        assert result["total_field_entries"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# Tests for NEW ecological intelligence tools
# ══════════════════════════════════════════════════════════════════════════════

# ── Helper: mock httpx responses ─────────────────────────────────────────────

def _mock_httpx_response(json_data, status_code=200):
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


FAKE_INAT_SPECIES_COUNTS = {
    "total_results": 42,
    "results": [
        {
            "count": 150,
            "taxon": {
                "id": 12345,
                "name": "Corvus coronoides",
                "preferred_common_name": "Australian Raven",
                "iconic_taxon_name": "Aves",
                "conservation_status": {"status_name": "Least Concern", "status": "LC"},
                "introduced": False,
            },
        },
        {
            "count": 90,
            "taxon": {
                "id": 67890,
                "name": "Rhipidura leucophrys",
                "preferred_common_name": "Willie Wagtail",
                "iconic_taxon_name": "Aves",
                "conservation_status": None,
                "introduced": False,
            },
        },
    ],
}

FAKE_INAT_TAXA_AUTOCOMPLETE = {
    "results": [
        {
            "id": 12345,
            "name": "Corvus coronoides",
            "preferred_common_name": "Australian Raven",
            "rank": "species",
            "observations_count": 98765,
            "conservation_status": {"status_name": "Least Concern", "status": "LC"},
            "is_active": True,
            "introduced": False,
            "native": True,
            "endemic": False,
            "threatened": False,
            "wikipedia_url": "https://en.wikipedia.org/wiki/Australian_raven",
            "iconic_taxon_name": "Aves",
            "ancestors": [
                {"rank": "kingdom", "name": "Animalia"},
                {"rank": "phylum", "name": "Chordata"},
                {"rank": "class", "name": "Aves"},
                {"rank": "order", "name": "Passeriformes"},
                {"rank": "family", "name": "Corvidae"},
                {"rank": "genus", "name": "Corvus"},
            ],
        }
    ]
}

FAKE_WEATHER_RESPONSE = {
    "current": {
        "temperature_2m": 22.5,
        "relative_humidity_2m": 85,
        "apparent_temperature": 21.0,
        "precipitation": 0.0,
        "rain": 0.0,
        "cloud_cover": 45,
        "wind_speed_10m": 12.0,
        "wind_direction_10m": 180,
        "uv_index": 3.5,
        "is_day": 1,
    },
    "current_units": {},
    "timezone": "Australia/Sydney",
}


# ── query_nearby_species ─────────────────────────────────────────────────────


class TestQueryNearbySpecies:

    async def test_success(self):
        mock_resp = _mock_httpx_response(FAKE_INAT_SPECIES_COUNTS)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ecoscout_agent.tools.httpx.AsyncClient", return_value=mock_client):
            result = await query_nearby_species(lat=-33.8688, lon=151.2093)

        assert result["status"] == "ok"
        assert result["total_species"] == 42
        assert len(result["top_species"]) == 2
        assert result["top_species"][0]["name"] == "Corvus coronoides"
        assert result["top_species"][0]["common_name"] == "Australian Raven"
        assert result["top_species"][0]["observation_count"] == 150
        assert result["top_species"][1]["conservation_status"] == "Not Evaluated"

    async def test_with_iconic_taxa_filter(self):
        mock_resp = _mock_httpx_response(FAKE_INAT_SPECIES_COUNTS)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ecoscout_agent.tools.httpx.AsyncClient", return_value=mock_client):
            result = await query_nearby_species(
                lat=-33.8688, lon=151.2093, iconic_taxa="Aves"
            )

        assert result["status"] == "ok"
        call_args = mock_client.get.call_args
        assert call_args[1]["params"]["iconic_taxa"] == "Aves"

    async def test_api_error_returns_error(self):
        mock_resp = _mock_httpx_response({}, status_code=500)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ecoscout_agent.tools.httpx.AsyncClient", return_value=mock_client):
            result = await query_nearby_species(lat=-33.8688, lon=151.2093)

        assert result["status"] == "error"
        assert "error" in result


# ── get_species_info ─────────────────────────────────────────────────────────


class TestGetSpeciesInfo:

    async def test_success(self):
        mock_resp = _mock_httpx_response(FAKE_INAT_TAXA_AUTOCOMPLETE)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ecoscout_agent.tools.httpx.AsyncClient", return_value=mock_client):
            result = await get_species_info("Corvus coronoides")

        assert result["status"] == "found"
        assert result["name"] == "Corvus coronoides"
        assert result["common_name"] == "Australian Raven"
        assert result["observations_count"] == 98765
        assert result["conservation_status"] == "Least Concern"
        assert result["taxonomy"]["family"] == "Corvidae"
        assert result["taxonomy"]["order"] == "Passeriformes"
        assert result["native"] is True

    async def test_not_found(self):
        mock_resp = _mock_httpx_response({"results": []})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ecoscout_agent.tools.httpx.AsyncClient", return_value=mock_client):
            result = await get_species_info("Nonexistus fakeus")

        assert result["status"] == "not_found"

    async def test_api_error(self):
        mock_resp = _mock_httpx_response({}, status_code=429)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ecoscout_agent.tools.httpx.AsyncClient", return_value=mock_client):
            result = await get_species_info("Corvus coronoides")

        assert result["status"] == "error"


# ── get_area_species_checklist ───────────────────────────────────────────────


class TestGetAreaSpeciesChecklist:

    async def test_success(self):
        mock_resp = _mock_httpx_response(FAKE_INAT_SPECIES_COUNTS)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ecoscout_agent.tools.httpx.AsyncClient", return_value=mock_client):
            result = await get_area_species_checklist(
                lat=-33.8688, lon=151.2093, month=3
            )

        assert result["status"] == "ok"
        assert result["total_documented_species"] == 42
        assert result["month"] == 3
        assert "Aves" in result["groups"]
        assert result["group_counts"]["Aves"] == 2

    async def test_all_months(self):
        mock_resp = _mock_httpx_response(FAKE_INAT_SPECIES_COUNTS)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ecoscout_agent.tools.httpx.AsyncClient", return_value=mock_client):
            result = await get_area_species_checklist(lat=-33.8688, lon=151.2093)

        assert result["month"] == "all"

    async def test_api_error(self):
        mock_resp = _mock_httpx_response({}, status_code=503)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ecoscout_agent.tools.httpx.AsyncClient", return_value=mock_client):
            result = await get_area_species_checklist(lat=-33.8688, lon=151.2093)

        assert result["status"] == "error"


# ── get_weather_context ──────────────────────────────────────────────────────


class TestGetWeatherContext:

    async def test_success(self):
        mock_resp = _mock_httpx_response(FAKE_WEATHER_RESPONSE)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ecoscout_agent.tools.httpx.AsyncClient", return_value=mock_client):
            result = await get_weather_context(lat=-33.8688, lon=151.2093)

        assert result["status"] == "ok"
        assert result["temperature_c"] == 22.5
        assert result["relative_humidity_pct"] == 85
        assert result["is_day"] is True
        assert result["timezone"] == "Australia/Sydney"

    async def test_high_humidity_hint(self):
        mock_resp = _mock_httpx_response(FAKE_WEATHER_RESPONSE)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ecoscout_agent.tools.httpx.AsyncClient", return_value=mock_client):
            result = await get_weather_context(lat=-33.8688, lon=151.2093)

        assert any("amphibian" in h for h in result["ecological_hints"])

    async def test_moderate_temp_hint(self):
        mock_resp = _mock_httpx_response(FAKE_WEATHER_RESPONSE)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ecoscout_agent.tools.httpx.AsyncClient", return_value=mock_client):
            result = await get_weather_context(lat=-33.8688, lon=151.2093)

        assert any("reptile" in h for h in result["ecological_hints"])

    async def test_api_error(self):
        mock_resp = _mock_httpx_response({}, status_code=500)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("ecoscout_agent.tools.httpx.AsyncClient", return_value=mock_client):
            result = await get_weather_context(lat=-33.8688, lon=151.2093)

        assert result["status"] == "error"


# ── calculate_biodiversity_metrics ───────────────────────────────────────────


def _make_obs_firestore_mock(observations):
    """Create a Firestore mock that streams the given observations."""
    mock_db = MagicMock()

    async def _obs_stream():
        for obs in observations:
            doc = MagicMock()
            doc.to_dict.return_value = obs
            yield doc

    sub_collection = MagicMock()
    sub_collection.stream.return_value = _obs_stream()

    doc_ref = MagicMock()
    doc_ref.collection.return_value = sub_collection

    mock_db.collection.return_value.document.return_value = doc_ref
    return mock_db


class TestCalculateBiodiversityMetrics:

    async def test_two_equal_species(self):
        observations = [
            {"species_name": "Species A"},
            {"species_name": "Species B"},
        ]
        mock_db = _make_obs_firestore_mock(observations)

        with patch("ecoscout_agent.tools._get_firestore_db", return_value=mock_db):
            result = await calculate_biodiversity_metrics("test-session")

        assert result["status"] == "ok"
        assert result["species_richness"] == 2
        assert result["total_observations"] == 2
        assert abs(result["shannon_index"] - round(math.log(2), 3)) < 0.01
        assert result["simpson_index"] == 1.0
        assert result["evenness"] == 1.0

    async def test_three_species_unequal(self):
        observations = [
            {"species_name": "Species A"},
            {"species_name": "Species A"},
            {"species_name": "Species A"},
            {"species_name": "Species B"},
            {"species_name": "Species C"},
        ]
        mock_db = _make_obs_firestore_mock(observations)

        with patch("ecoscout_agent.tools._get_firestore_db", return_value=mock_db):
            result = await calculate_biodiversity_metrics("test-session")

        assert result["species_richness"] == 3
        assert result["total_observations"] == 5
        assert result["shannon_index"] > 0
        assert result["shannon_index"] < math.log(3)
        assert result["evenness"] < 1.0
        assert len(result["accumulation_curve"]) == 5

    async def test_empty_observations(self):
        mock_db = _make_obs_firestore_mock([])

        with patch("ecoscout_agent.tools._get_firestore_db", return_value=mock_db):
            result = await calculate_biodiversity_metrics("test-session")

        assert result["status"] == "ok"
        assert result["species_richness"] == 0
        assert result["shannon_index"] == 0.0
        assert "No observations" in result["interpretation"]

    async def test_single_species(self):
        observations = [
            {"species_name": "Only One"},
            {"species_name": "Only One"},
            {"species_name": "Only One"},
        ]
        mock_db = _make_obs_firestore_mock(observations)

        with patch("ecoscout_agent.tools._get_firestore_db", return_value=mock_db):
            result = await calculate_biodiversity_metrics("test-session")

        assert result["species_richness"] == 1
        assert result["shannon_index"] == 0.0
        assert result["evenness"] == 1.0

    async def test_accumulation_curve_shape(self):
        observations = [
            {"species_name": "A"},
            {"species_name": "B"},
            {"species_name": "A"},
            {"species_name": "C"},
        ]
        mock_db = _make_obs_firestore_mock(observations)

        with patch("ecoscout_agent.tools._get_firestore_db", return_value=mock_db):
            result = await calculate_biodiversity_metrics("test-session")

        curve = result["accumulation_curve"]
        assert curve[0]["cumulative_species"] == 1
        assert curve[1]["cumulative_species"] == 2
        assert curve[2]["cumulative_species"] == 2  # repeat species
        assert curve[3]["cumulative_species"] == 3

    async def test_firestore_error(self):
        mock_db = MagicMock()
        mock_db.collection.side_effect = Exception("Connection refused")

        with patch("ecoscout_agent.tools._get_firestore_db", return_value=mock_db):
            result = await calculate_biodiversity_metrics("test-session")

        assert result["status"] == "error"


# ── generate_survey_report ───────────────────────────────────────────────────


class TestGenerateSurveyReport:

    async def test_success(self, patch_all_clients):
        result = await generate_survey_report(session_id="test-session")

        assert result["status"] == "compiled"
        assert result["session_id"] == "test-session"
        assert result["report_type"] == "Ecological Biodiversity Survey Report"
        assert "survey_metadata" in result
        assert "biodiversity_metrics" in result
        assert "species_inventory" in result
        assert "conservation_flags" in result
        assert "recommendations" in result
        assert "media_generated" in result

    async def test_detection_source_classification(self):
        observations = [
            {"species_name": "Bird A", "common_name": "Robin",
             "ecological_notes": "detected by audio", "safety_warnings": "",
             "confidence_level": "high", "timestamp": "2026-03-14T10:00:00",
             "gps": {"lat": -33.8, "lon": 151.2}},
            {"species_name": "Bug B", "common_name": "Beetle",
             "ecological_notes": "detected by visual inspection", "safety_warnings": "",
             "confidence_level": "medium", "timestamp": "2026-03-14T10:05:00",
             "gps": {"lat": -33.8, "lon": 151.2}},
            {"species_name": "Frog C", "common_name": "Tree Frog",
             "ecological_notes": "detected by visual and audio", "safety_warnings": "",
             "confidence_level": "high", "timestamp": "2026-03-14T10:10:00",
             "gps": {"lat": -33.8, "lon": 151.2}},
        ]

        mock_db = MagicMock()

        async def _obs_stream():
            for obs in observations:
                doc = MagicMock()
                doc.to_dict.return_value = obs
                yield doc

        async def _empty_stream():
            return
            yield

        async def _video_stream():
            return
            yield

        def _route_sub(name):
            sub = MagicMock()
            sub_doc = MagicMock()
            sub_doc.set = AsyncMock()
            sub.document.return_value = sub_doc
            if name == "observations":
                sub.stream.return_value = _obs_stream()
            elif name == "field_entries":
                sub.stream.return_value = _empty_stream()
            elif name == "reports":
                return sub
            return sub

        doc_ref = MagicMock()
        doc_ref.collection.side_effect = _route_sub
        mock_db.collection.return_value.document.return_value = doc_ref

        videos_query = MagicMock()
        videos_query.stream.return_value = _video_stream()
        mock_db.collection.return_value.where.return_value = videos_query

        with patch("ecoscout_agent.tools._get_firestore_db", return_value=mock_db):
            result = await generate_survey_report("test-session")

        methods = result["survey_metadata"]["detection_methods"]
        assert methods["audio"] == 1
        assert methods["visual"] == 1
        assert methods["visual+audio"] == 1

    async def test_recommendations_low_species(self):
        observations = [
            {"species_name": "Species A", "common_name": "A", "ecological_notes": "",
             "safety_warnings": "", "confidence_level": "high",
             "timestamp": "2026-03-14T10:00:00", "gps": {"lat": 0, "lon": 0}},
        ]

        mock_db = MagicMock()

        async def _obs_stream():
            for obs in observations:
                doc = MagicMock()
                doc.to_dict.return_value = obs
                yield doc

        async def _empty_stream():
            return
            yield

        def _route_sub(name):
            sub = MagicMock()
            sub_doc = MagicMock()
            sub_doc.set = AsyncMock()
            sub.document.return_value = sub_doc
            if name == "observations":
                sub.stream.return_value = _obs_stream()
            else:
                sub.stream.return_value = _empty_stream()
            return sub

        doc_ref = MagicMock()
        doc_ref.collection.side_effect = _route_sub
        mock_db.collection.return_value.document.return_value = doc_ref

        videos_query = MagicMock()
        videos_query.stream.return_value = _empty_stream()
        mock_db.collection.return_value.where.return_value = videos_query

        with patch("ecoscout_agent.tools._get_firestore_db", return_value=mock_db):
            result = await generate_survey_report("test-session")

        assert any("Low species count" in r for r in result["recommendations"])
        assert any("No audio detections" in r for r in result["recommendations"])

    async def test_firestore_error(self):
        mock_db = MagicMock()
        mock_db.collection.side_effect = Exception("Firestore down")

        with patch("ecoscout_agent.tools._get_firestore_db", return_value=mock_db):
            result = await generate_survey_report("test-session")

        assert result["status"] == "error"
