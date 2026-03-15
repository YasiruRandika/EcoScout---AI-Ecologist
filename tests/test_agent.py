"""Tests for EcoScout v2 agent configuration.

Deterministic checks -- no LLM calls. Validates model selection,
tool registration, and instruction content for the AI Ecologist persona.
"""

from ecoscout_agent.agent import ECOSCOUT_INSTRUCTION, agent


EXPECTED_TOOL_NAMES = {
    "google_search",
    "identify_specimen",
    "record_observation",
    "generate_field_entry",
    "generate_nature_video",
    "extend_video",
    "create_expedition_summary",
    "query_nearby_species",
    "get_species_info",
    "get_area_species_checklist",
    "get_weather_context",
    "calculate_biodiversity_metrics",
    "generate_survey_report",
}


class TestAgentIdentity:

    def test_name_is_ecoscout(self):
        assert agent.name == "ecoscout"

    def test_model_is_latest_native_audio(self):
        assert "native-audio" in agent.model, (
            f"Model {agent.model!r} does not contain 'native-audio'"
        )
        assert "gemini" in agent.model.lower() and "live" in agent.model.lower()


class TestToolRegistration:

    def _get_tool_names(self):
        names = set()
        for tool in agent.tools:
            if hasattr(tool, "name"):
                names.add(tool.name)
            elif hasattr(tool, "__name__"):
                names.add(tool.__name__)
            elif hasattr(tool, "func") and hasattr(tool.func, "__name__"):
                names.add(tool.func.__name__)
            elif callable(tool):
                names.add(getattr(tool, "__name__", str(tool)))
        return names

    def test_has_correct_number_of_tools(self):
        assert len(agent.tools) == 13, (
            f"Expected 13 tools, got {len(agent.tools)}"
        )

    def test_all_expected_tools_registered(self):
        names = self._get_tool_names()
        for expected in EXPECTED_TOOL_NAMES:
            assert expected in names, (
                f"Tool {expected!r} not found in agent.tools. "
                f"Registered: {names}"
            )

    def test_no_unexpected_tools(self):
        names = self._get_tool_names()
        unexpected = names - EXPECTED_TOOL_NAMES
        assert not unexpected, (
            f"Unexpected tools registered: {unexpected}"
        )


class TestInstruction:

    def test_contains_tool_usage_section(self):
        assert "TOOL USAGE" in ECOSCOUT_INSTRUCTION

    def test_contains_safety_protocol(self):
        assert "SAFETY PROTOCOL" in ECOSCOUT_INSTRUCTION

    def test_emphasizes_must_call_tools(self):
        assert "MUST actually invoke tools" in ECOSCOUT_INSTRUCTION

    def test_emphasizes_video_tool_invocation(self):
        assert "generate_nature_video" in ECOSCOUT_INSTRUCTION

    def test_warns_against_simulating_tool_calls(self):
        assert "NEVER simulate or describe a tool call" in ECOSCOUT_INSTRUCTION

    def test_mentions_all_tools_in_instruction(self):
        all_tools = [
            "google_search",
            "identify_specimen",
            "record_observation",
            "generate_field_entry",
            "generate_nature_video",
            "extend_video",
            "create_expedition_summary",
            "query_nearby_species",
            "get_species_info",
            "get_area_species_checklist",
            "get_weather_context",
            "calculate_biodiversity_metrics",
            "generate_survey_report",
        ]
        for tool_name in all_tools:
            assert tool_name in ECOSCOUT_INSTRUCTION, (
                f"Instruction does not mention tool {tool_name!r}"
            )

    def test_has_ambient_soundscape_section(self):
        assert "AMBIENT SOUNDSCAPE ANALYSIS" in ECOSCOUT_INSTRUCTION

    def test_has_scientific_hypothesis_section(self):
        assert "SCIENTIFIC HYPOTHESIS GENERATION" in ECOSCOUT_INSTRUCTION

    def test_has_ecological_narrative_section(self):
        assert "ECOLOGICAL NARRATIVE STORYTELLING" in ECOSCOUT_INSTRUCTION

    def test_has_survey_initialization_section(self):
        assert "SURVEY INITIALIZATION PROTOCOL" in ECOSCOUT_INSTRUCTION

    def test_core_identity_is_ecologist(self):
        assert "AI Ecologist" in ECOSCOUT_INSTRUCTION
        assert "field ecologist" in ECOSCOUT_INSTRUCTION
