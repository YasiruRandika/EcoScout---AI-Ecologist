#!/usr/bin/env python3
"""Pre-deploy test: validates Gemini Live connection before deploying to Cloud Run.

Use this to reproduce the production env locally (Vertex AI + us-central1) before deployment.
The 1008 policy violation often occurs when GOOGLE_CLOUD_LOCATION is missing or wrong.

Requirements:
  - gcloud auth application-default login (for local Vertex AI auth)
  - app/.env with GOOGLE_GENAI_USE_VERTEXAI=True, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION=us-central1

Run from project root:
  cd ecoscout
  python scripts/test_gemini_live_connection.py

Or with explicit env:
  set GOOGLE_GENAI_USE_VERTEXAI=True
  set GOOGLE_CLOUD_PROJECT=ecoscout-vertexai-2026
  set GOOGLE_CLOUD_LOCATION=us-central1
  python scripts/test_gemini_live_connection.py
"""

import asyncio
import os
import sys
import time
from pathlib import Path

# Add app to path
app_dir = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(app_dir))

from dotenv import load_dotenv
load_dotenv(app_dir / ".env")


async def test_gemini_live_connection():
    """Establish a brief Gemini Live session and send one text message."""
    use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() == "true"
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "")

    print("=" * 60)
    print("EcoScout - Gemini Live Connection Test (Pre-Deploy)")
    print("=" * 60)
    print(f"  GOOGLE_GENAI_USE_VERTEXAI: {use_vertex}")
    print(f"  GOOGLE_CLOUD_PROJECT:      {project or '(not set)'}")
    print(f"  GOOGLE_CLOUD_LOCATION:     {location or '(not set)'}")
    print()

    if not use_vertex:
        print("WARNING: GOOGLE_GENAI_USE_VERTEXAI is not True.")
        print("  This test uses the same config as Cloud Run. Set it in app/.env:")
        print("    GOOGLE_GENAI_USE_VERTEXAI=True")
        print("    GOOGLE_CLOUD_PROJECT=your-project-id")
        print("    GOOGLE_CLOUD_LOCATION=us-central1")
        print()
        print("  Or run with API key (different from production):")
        print("    GOOGLE_API_KEY=your-key python scripts/test_gemini_live_connection.py")
        print("=" * 60)
        return 1

    if not project:
        print("ERROR: GOOGLE_CLOUD_PROJECT required for Vertex AI.")
        return 1

    if not location:
        print("ERROR: GOOGLE_CLOUD_LOCATION required for Gemini Live on Vertex AI.")
        print("  Set GOOGLE_CLOUD_LOCATION=us-central1 in app/.env")
        print("  (Using 'global' can cause 1008 policy violation)")
        return 1

    print("Importing agent and runner...")
    from google.adk.runners import Runner
    from google.adk.agents.run_config import RunConfig, StreamingMode
    from google.adk.agents.live_request_queue import LiveRequestQueue
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    from ecoscout_agent.agent import agent

    app_name = "ecoscout"
    session_service = InMemorySessionService()
    runner = Runner(
        app_name=app_name,
        agent=agent,
        session_service=session_service,
    )

    model_name = agent.model
    is_native_audio = "native-audio" in model_name.lower()
    print(f"Model: {model_name} (native_audio={is_native_audio})")
    print()

    if is_native_audio:
        run_config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=os.getenv("ECOSCOUT_VOICE", "Orus")
                    )
                )
            ),
        )
    else:
        run_config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["TEXT"],
            session_resumption=types.SessionResumptionConfig(),
        )

    user_id = "test-user"
    session_id = "test-session"
    await session_service.create_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )
    live_request_queue = LiveRequestQueue()

    events_received = []
    first_event_elapsed = None
    _connect_start = time.monotonic()

    async def consume_events():
        nonlocal first_event_elapsed
        try:
            async for event in runner.run_live(
                user_id=user_id,
                session_id=session_id,
                live_request_queue=live_request_queue,
                run_config=run_config,
            ):
                if first_event_elapsed is None:
                    first_event_elapsed = time.monotonic() - _connect_start
                events_received.append(event)
        finally:
            live_request_queue.close()

    async def send_test_message():
        await asyncio.sleep(2.0)  # Let connection establish
        content = types.Content(
            parts=[types.Part(text="Hi EcoScout, say 'Connection test OK' in one short sentence.")]
        )
        live_request_queue.send_content(content)
        await asyncio.sleep(10.0)  # Wait for response
        live_request_queue.close()

    print("Connecting to Gemini Live...")
    try:
        await asyncio.wait_for(
            asyncio.gather(consume_events(), send_test_message()),
            timeout=20.0,
        )
    except asyncio.TimeoutError:
        pass  # We may have received events already
    except Exception as e:
        print()
        print("FAILED: Connection error")
        print(f"  {type(e).__name__}: {e}")
        if "1008" in str(e) or "policy violation" in str(e).lower():
            print()
            print("  This is the 1008 policy violation. Ensure:")
            print("    - GOOGLE_CLOUD_LOCATION=us-central1 (not 'global')")
            print("    - Project has Vertex AI API enabled")
            print("    - Service account has roles/aiplatform.user")
        print("=" * 60)
        return 1
    finally:
        live_request_queue.close()

    if events_received:
        print()
        print("PASSED: Received", len(events_received), "event(s) from Gemini Live")
        if first_event_elapsed is not None:
            print(f"  First event in ~{first_event_elapsed:.1f}s")
        print()
        print("Connection test OK. Safe to deploy.")
    else:
        print()
        print("WARNING: No events received. Connection may have dropped.")
        print("  Check logs for 1008 or streaming errors.")

    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(test_gemini_live_connection()))
