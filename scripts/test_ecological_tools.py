"""Live smoke tests for EcoScout v2 ecological intelligence tools.

Hits the REAL iNaturalist and Open-Meteo APIs (no mocks).
No API keys required - both services are free and public.

Usage:
    python scripts/test_ecological_tools.py
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from ecoscout_agent.tools import (
    get_area_species_checklist,
    get_species_info,
    get_weather_context,
    query_nearby_species,
)

# Sydney Royal Botanic Garden - rich biodiversity, well-documented on iNaturalist
TEST_LAT = -33.8642
TEST_LON = 151.2166
TEST_RADIUS = 5.0


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def _header(title: str) -> None:
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}{Colors.RESET}")


def _pass(msg: str) -> None:
    print(f"  {Colors.GREEN}[PASS]{Colors.RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"  {Colors.RED}[FAIL]{Colors.RESET} {msg}")


def _info(msg: str) -> None:
    print(f"  {Colors.YELLOW}[info]{Colors.RESET} {msg}")


passed = 0
failed = 0


def assert_check(condition: bool, description: str) -> None:
    global passed, failed
    if condition:
        _pass(description)
        passed += 1
    else:
        _fail(description)
        failed += 1


async def test_query_nearby_species() -> None:
    _header("1. query_nearby_species (iNaturalist)")
    _info(f"Location: Sydney Botanic Garden ({TEST_LAT}, {TEST_LON})")

    start = time.monotonic()
    result = await query_nearby_species(TEST_LAT, TEST_LON, TEST_RADIUS)
    elapsed = time.monotonic() - start

    _info(f"Response time: {elapsed:.2f}s")
    assert_check(result.get("status") == "ok", f"Status is 'ok' (got: {result.get('status')})")

    if result.get("status") != "ok":
        _info(f"Error: {result.get('error', 'unknown')}")
        return

    assert_check(result.get("total_species", 0) > 0, f"Found {result.get('total_species', 0)} species")
    assert_check(len(result.get("top_species", [])) > 0, "top_species list is not empty")

    if result.get("top_species"):
        sp = result["top_species"][0]
        assert_check("name" in sp, f"First species has 'name': {sp.get('name', '?')}")
        assert_check("common_name" in sp, f"First species has 'common_name': {sp.get('common_name', '?')}")
        assert_check("observation_count" in sp, f"observation_count: {sp.get('observation_count', '?')}")
        assert_check("iconic_taxon" in sp, f"iconic_taxon: {sp.get('iconic_taxon', '?')}")
        assert_check("conservation_status" in sp, f"conservation_status: {sp.get('conservation_status', '?')}")

    _info("Testing with iconic_taxa filter (Aves)...")
    bird_result = await query_nearby_species(TEST_LAT, TEST_LON, TEST_RADIUS, iconic_taxa="Aves")
    assert_check(bird_result.get("status") == "ok", "Aves-filtered query succeeded")
    if bird_result.get("top_species"):
        first_bird = bird_result["top_species"][0]
        assert_check(
            first_bird.get("iconic_taxon") == "Aves",
            f"First result is Aves: {first_bird.get('common_name', first_bird.get('name', '?'))}",
        )


async def test_get_species_info() -> None:
    _header("2. get_species_info (iNaturalist)")

    test_species = [
        ("Dacelo novaeguineae", "Laughing Kookaburra"),
        ("Trichosurus vulpecula", "Common Brushtail Possum"),
        ("Eucalyptus", "Eucalyptus genus"),
    ]

    for scientific, label in test_species:
        _info(f"Looking up: {scientific} ({label})")
        start = time.monotonic()
        result = await get_species_info(scientific)
        elapsed = time.monotonic() - start
        _info(f"  Response time: {elapsed:.2f}s")

        if result.get("status") == "error":
            _fail(f"'{scientific}' API error: {result.get('error')}")
            continue
        assert_check(result.get("status") == "found", f"'{scientific}' found")
        assert_check(result.get("taxon_id") is not None, f"taxon_id: {result.get('taxon_id')}")
        assert_check(result.get("observations_count", 0) > 0, f"observations_count: {result.get('observations_count', 0)}")
        if result.get("wikipedia_url"):
            _info(f"  Wikipedia: {result['wikipedia_url']}")

    _info("Testing unknown species...")
    not_found = await get_species_info("Notarealspecies xyzabc")
    assert_check(not_found.get("status") == "not_found", "Nonexistent species returns 'not_found'")


async def test_get_area_species_checklist() -> None:
    _header("3. get_area_species_checklist (iNaturalist)")

    _info(f"All-time checklist for ({TEST_LAT}, {TEST_LON}), radius {TEST_RADIUS}km")
    start = time.monotonic()
    result = await get_area_species_checklist(TEST_LAT, TEST_LON, TEST_RADIUS)
    elapsed = time.monotonic() - start
    _info(f"Response time: {elapsed:.2f}s")

    assert_check(result.get("status") == "ok", f"Status is 'ok' (got: {result.get('status')})")

    if result.get("status") != "ok":
        _info(f"Error: {result.get('error', 'unknown')}")
        return

    assert_check(result.get("total_documented_species", 0) > 0, f"Documented species: {result.get('total_documented_species', 0)}")
    assert_check(len(result.get("groups", {})) > 0, f"Taxonomic groups: {list(result.get('groups', {}).keys())}")

    groups = result.get("group_counts", {})
    for group, count in sorted(groups.items(), key=lambda x: -x[1]):
        _info(f"  {group}: {count} species")

    _info("Testing with month filter (month=3, March)...")
    march_result = await get_area_species_checklist(TEST_LAT, TEST_LON, TEST_RADIUS, month=3)
    assert_check(march_result.get("status") == "ok", "March-filtered query succeeded")
    assert_check(
        march_result.get("total_documented_species", 0) > 0,
        f"March species: {march_result.get('total_documented_species', 0)}",
    )


async def test_get_weather_context() -> None:
    _header("4. get_weather_context (Open-Meteo)")
    _info(f"Current weather at ({TEST_LAT}, {TEST_LON})")

    start = time.monotonic()
    result = await get_weather_context(TEST_LAT, TEST_LON)
    elapsed = time.monotonic() - start
    _info(f"Response time: {elapsed:.2f}s")

    assert_check(result.get("status") == "ok", f"Status is 'ok' (got: {result.get('status')})")

    if result.get("status") != "ok":
        _info(f"Error: {result.get('error', 'unknown')}")
        return

    required_fields = [
        "temperature_c",
        "apparent_temperature_c",
        "relative_humidity_pct",
        "precipitation_mm",
        "rain_mm",
        "cloud_cover_pct",
        "wind_speed_kmh",
        "wind_direction_deg",
        "uv_index",
        "is_day",
        "timezone",
        "ecological_hints",
    ]
    for field in required_fields:
        assert_check(field in result, f"Has '{field}': {result.get(field, 'MISSING')}")

    temp = result.get("temperature_c", -999)
    assert_check(-50 < temp < 60, f"Temperature {temp}°C is within plausible range")

    humidity = result.get("relative_humidity_pct", -1)
    assert_check(0 <= humidity <= 100, f"Humidity {humidity}% is valid")

    hints = result.get("ecological_hints", [])
    assert_check(isinstance(hints, list), f"ecological_hints is a list ({len(hints)} hints)")
    for h in hints:
        _info(f"  Ecological hint: {h}")


async def main() -> None:
    print(f"\n{Colors.BOLD}EcoScout v2 - Live Ecological Tool Smoke Tests{Colors.RESET}")
    print(f"Target: Sydney Royal Botanic Garden ({TEST_LAT}, {TEST_LON})")
    print("APIs: iNaturalist (no key), Open-Meteo (no key)")
    print(f"{'-' * 60}")

    total_start = time.monotonic()

    await test_query_nearby_species()
    await test_get_species_info()
    await test_get_area_species_checklist()
    await test_get_weather_context()

    total_elapsed = time.monotonic() - total_start

    print(f"\n{Colors.BOLD}{'=' * 60}")
    print(f"  RESULTS: {Colors.GREEN}{passed} passed{Colors.RESET}{Colors.BOLD}, ", end="")
    if failed:
        print(f"{Colors.RED}{failed} failed{Colors.RESET}{Colors.BOLD}")
    else:
        print(f"0 failed")
    print(f"  Total time: {total_elapsed:.2f}s")
    print(f"{'=' * 60}{Colors.RESET}\n")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
