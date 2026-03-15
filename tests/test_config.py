"""Configuration, import, and smoke tests for EcoScout.

Validates that all modules import cleanly, function signatures
haven't drifted, lazy clients stay lazy, and defaults are correct.
"""

import inspect


class TestImports:

    def test_tools_module_importable(self):
        import ecoscout_agent.tools  # noqa: F401

    def test_agent_module_importable(self):
        import ecoscout_agent.agent  # noqa: F401

    def test_init_exports_agent(self):
        from ecoscout_agent import agent
        assert agent is not None

    def test_main_app_importable(self):
        from main import app  # noqa: F401


class TestLazyClients:

    def test_genai_client_not_initialized_at_import(self):
        import ecoscout_agent.tools as t
        assert t._genai_client is None

    def test_firestore_db_not_initialized_at_import(self):
        import ecoscout_agent.tools as t
        assert t._firestore_db is None

    def test_storage_client_not_initialized_at_import(self):
        import ecoscout_agent.tools as t
        assert t._storage_client is None


class TestDefaults:

    def test_bucket_name_default(self, monkeypatch):
        monkeypatch.delenv("GCS_BUCKET_NAME", raising=False)
        import importlib
        import ecoscout_agent.tools as t
        # Module-level default captured at import; check the constant
        assert t.BUCKET_NAME is not None

    def test_video_model_env_key(self):
        """generate_nature_video reads VIDEO_MODEL with correct default."""
        src = inspect.getsource(
            __import__("ecoscout_agent.tools", fromlist=["generate_nature_video"]).generate_nature_video
        )
        assert "veo-3.1-generate-preview" in src

    def test_image_model_env_key(self):
        """generate_field_entry reads IMAGE_MODEL with correct default."""
        src = inspect.getsource(
            __import__("ecoscout_agent.tools", fromlist=["generate_field_entry"]).generate_field_entry
        )
        assert "gemini-3-pro-image-preview" in src


class TestToolSignatures:
    """Guard against accidental parameter changes that would break ADK tool schemas."""

    def _params(self, func):
        sig = inspect.signature(func)
        return {
            name: {
                "kind": p.kind.name,
                "has_default": p.default is not inspect.Parameter.empty,
            }
            for name, p in sig.parameters.items()
        }

    def test_identify_specimen_signature(self):
        from ecoscout_agent.tools import identify_specimen
        params = self._params(identify_specimen)

        assert "description" in params
        assert "common_features" in params
        assert not params["description"]["has_default"]
        assert not params["common_features"]["has_default"]
        assert params["habitat_context"]["has_default"]
        assert params["season"]["has_default"]

    def test_record_observation_signature(self):
        from ecoscout_agent.tools import record_observation
        params = self._params(record_observation)

        required = ["species_name", "common_name", "description",
                     "ecological_notes", "confidence_level"]
        for name in required:
            assert name in params, f"Missing required param: {name}"
            assert not params[name]["has_default"], f"{name} should be required"

        optional = ["safety_warnings", "gps_lat", "gps_lon", "session_id"]
        for name in optional:
            assert name in params, f"Missing optional param: {name}"
            assert params[name]["has_default"], f"{name} should have a default"

    def test_generate_nature_video_signature(self):
        from ecoscout_agent.tools import generate_nature_video
        params = self._params(generate_nature_video)

        required = ["species_name", "process_description", "ecological_context"]
        for name in required:
            assert name in params
            assert not params[name]["has_default"]

        assert params["visual_style"]["has_default"]
        assert params["session_id"]["has_default"]

    def test_generate_field_entry_signature(self):
        from ecoscout_agent.tools import generate_field_entry
        params = self._params(generate_field_entry)

        required = ["species_name", "common_name", "description", "habitat"]
        for name in required:
            assert name in params
            assert not params[name]["has_default"]

    def test_extend_video_signature(self):
        from ecoscout_agent.tools import extend_video
        params = self._params(extend_video)

        assert "video_id" in params
        assert not params["video_id"]["has_default"]
        assert "extension_description" in params
        assert not params["extension_description"]["has_default"]

    def test_create_expedition_summary_signature(self):
        from ecoscout_agent.tools import create_expedition_summary
        params = self._params(create_expedition_summary)

        assert "session_id" in params
        assert params["session_id"]["has_default"]

    def test_query_nearby_species_signature(self):
        from ecoscout_agent.tools import query_nearby_species
        params = self._params(query_nearby_species)

        assert not params["lat"]["has_default"]
        assert not params["lon"]["has_default"]
        assert params["radius_km"]["has_default"]
        assert params["iconic_taxa"]["has_default"]

    def test_get_species_info_signature(self):
        from ecoscout_agent.tools import get_species_info
        params = self._params(get_species_info)

        assert "taxon_name" in params
        assert not params["taxon_name"]["has_default"]

    def test_get_area_species_checklist_signature(self):
        from ecoscout_agent.tools import get_area_species_checklist
        params = self._params(get_area_species_checklist)

        assert not params["lat"]["has_default"]
        assert not params["lon"]["has_default"]
        assert params["radius_km"]["has_default"]
        assert params["month"]["has_default"]

    def test_get_weather_context_signature(self):
        from ecoscout_agent.tools import get_weather_context
        params = self._params(get_weather_context)

        assert not params["lat"]["has_default"]
        assert not params["lon"]["has_default"]
        assert len(params) == 2

    def test_calculate_biodiversity_metrics_signature(self):
        from ecoscout_agent.tools import calculate_biodiversity_metrics
        params = self._params(calculate_biodiversity_metrics)

        assert "session_id" in params
        assert params["session_id"]["has_default"]

    def test_generate_survey_report_signature(self):
        from ecoscout_agent.tools import generate_survey_report
        params = self._params(generate_survey_report)

        assert "session_id" in params
        assert params["session_id"]["has_default"]


class TestToolsAreAsync:
    """All tool functions must be async for the ADK Live streaming pipeline."""

    def test_identify_specimen_is_async(self):
        from ecoscout_agent.tools import identify_specimen
        assert inspect.iscoroutinefunction(identify_specimen)

    def test_record_observation_is_async(self):
        from ecoscout_agent.tools import record_observation
        assert inspect.iscoroutinefunction(record_observation)

    def test_generate_field_entry_is_async(self):
        from ecoscout_agent.tools import generate_field_entry
        assert inspect.iscoroutinefunction(generate_field_entry)

    def test_generate_nature_video_is_async(self):
        from ecoscout_agent.tools import generate_nature_video
        assert inspect.iscoroutinefunction(generate_nature_video)

    def test_extend_video_is_async(self):
        from ecoscout_agent.tools import extend_video
        assert inspect.iscoroutinefunction(extend_video)

    def test_create_expedition_summary_is_async(self):
        from ecoscout_agent.tools import create_expedition_summary
        assert inspect.iscoroutinefunction(create_expedition_summary)

    def test_query_nearby_species_is_async(self):
        from ecoscout_agent.tools import query_nearby_species
        assert inspect.iscoroutinefunction(query_nearby_species)

    def test_get_species_info_is_async(self):
        from ecoscout_agent.tools import get_species_info
        assert inspect.iscoroutinefunction(get_species_info)

    def test_get_area_species_checklist_is_async(self):
        from ecoscout_agent.tools import get_area_species_checklist
        assert inspect.iscoroutinefunction(get_area_species_checklist)

    def test_get_weather_context_is_async(self):
        from ecoscout_agent.tools import get_weather_context
        assert inspect.iscoroutinefunction(get_weather_context)

    def test_calculate_biodiversity_metrics_is_async(self):
        from ecoscout_agent.tools import calculate_biodiversity_metrics
        assert inspect.iscoroutinefunction(calculate_biodiversity_metrics)

    def test_generate_survey_report_is_async(self):
        from ecoscout_agent.tools import generate_survey_report
        assert inspect.iscoroutinefunction(generate_survey_report)
