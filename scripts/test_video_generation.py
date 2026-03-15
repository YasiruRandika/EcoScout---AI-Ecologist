#!/usr/bin/env python3
"""Diagnostic script to test Veo 3.1 video generation.

Run from project root:
  cd ecoscout/app
  python -c "exec(open('../scripts/test_video_generation.py').read())"

Or with API key:
  GOOGLE_API_KEY=your-key python -c "exec(open('../scripts/test_video_generation.py').read())"

This will attempt to start a video generation and print the result or error.
"""

import asyncio
import os
import sys

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "app", ".env"))


async def main():
    from ecoscout_agent.tools import generate_nature_video

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY not set. Add it to app/.env or set the env var.")
        print("  Example: GOOGLE_API_KEY=your-key python scripts/test_video_generation.py")
        return 1

    print("Starting video generation test...")
    print("Model:", os.getenv("VIDEO_MODEL", "veo-3.1-generate-preview"))

    result = await generate_nature_video(
        species_name="Amanita muscaria",
        process_description="mushroom emergence from forest floor",
        ecological_context="European oak forest, autumn leaf litter",
        session_id="test-video-script",
    )

    if result.get("status") == "generating":
        print("OK: Video generation started successfully")
        print("  video_id:", result.get("video_id"))
        print("  message:", result.get("message"))
        print("  (Video will generate in background; full flow requires running server)")
        return 0
    elif result.get("status") == "error":
        print("ERROR: Video generation failed")
        print("  error:", result.get("error"))
        return 1
    else:
        print("Unexpected result:", result)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
