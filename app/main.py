"""EcoScout FastAPI application -- The AI Ecologist.

Bidirectional streaming via WebSocket with ADK Runner, supporting
simultaneous audio, camera frames, GPS with reverse geocoding, text input,
and ecological survey intelligence (iNaturalist, biodiversity metrics).
"""

import asyncio
import base64
import json
import logging
import os
import warnings
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote, quote

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from google.adk.runners import Runner
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.sessions import InMemorySessionService
from google.genai import types

from ecoscout_agent.agent import agent
from ecoscout_agent.tools import (
    _video_ready_events, _video_started_events, _ecology_events,
    _field_entry_events, clear_session_events,
)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

load_dotenv(Path(__file__).parent / ".env")

APP_NAME = "ecoscout"

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
ECOSCOUT_ACCESS_TOKEN = os.getenv("ECOSCOUT_ACCESS_TOKEN", "").strip()
TOKEN_COOKIE_NAME = "ecoscout_token"


def _get_token_from_request(request: Request) -> str | None:
    """Extract token from query param ?token=, Authorization: Bearer header, or cookie."""
    token = request.query_params.get("token")
    if token:
        return token
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(TOKEN_COOKIE_NAME)


def _is_token_valid(token: str | None) -> bool:
    """Check if provided token matches expected. If ECOSCOUT_ACCESS_TOKEN is unset, no auth required."""
    if not ECOSCOUT_ACCESS_TOKEN:
        return True
    return bool(token and token == ECOSCOUT_ACCESS_TOKEN)


async def require_token(request: Request) -> None:
    """Dependency: require valid access token when ECOSCOUT_ACCESS_TOKEN is set."""
    if not _is_token_valid(_get_token_from_request(request)):
        raise HTTPException(status_code=401, detail="Access token required")


def _get_token_from_websocket_scope(scope: dict) -> str | None:
    """Extract token from WebSocket query string or Cookie header."""
    query_bytes = scope.get("query_string", b"")
    query_str = query_bytes.decode() if isinstance(query_bytes, bytes) else query_bytes
    params = parse_qs(query_str)
    token = (params.get("token") or [None])[0]
    if token:
        return token
    for name, value in scope.get("headers", []):
        if name.lower() == b"cookie":
            cookies = value.decode().split(";")
            for c in cookies:
                parts = c.strip().split("=", 1)
                if len(parts) == 2 and parts[0].strip() == TOKEN_COOKIE_NAME:
                    return unquote(parts[1].strip())
            break
    return None

# ── Application setup ───────────────────────────────────────────────────────

app = FastAPI(title="EcoScout", description="Live Environmental Intelligence Companion")


@app.middleware("http")
async def add_permissions_policy(request: Request, call_next):
    """Set Permissions-Policy so browsers allow geolocation, camera, and microphone."""
    response = await call_next(request)
    response.headers["Permissions-Policy"] = (
        "geolocation=(self), camera=(self), microphone=(self)"
    )
    return response


app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)

session_service = InMemorySessionService()

runner = Runner(
    app_name=APP_NAME,
    agent=agent,
    session_service=session_service,
)


@app.get("/")
async def root(request: Request):
    token = _get_token_from_request(request)
    if ECOSCOUT_ACCESS_TOKEN and not _is_token_valid(token):
        cookie_token = request.cookies.get(TOKEN_COOKIE_NAME)
        if not _is_token_valid(cookie_token):
            return _access_form_response()
    # If token came from query param (e.g. shared link), set cookie and serve the page.
    # No redirect - that caused an infinite loop. Token stays in URL for WebSocket fallback on mobile.
    response = FileResponse(Path(__file__).parent / "static" / "index.html")
    if ECOSCOUT_ACCESS_TOKEN and token and request.query_params.get("token"):
        response.set_cookie(
            key=TOKEN_COOKIE_NAME,
            value=token,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            max_age=60 * 60 * 24 * 30,
        )
    return response


@app.get("/auth/set")
async def auth_set(
    request: Request,
    token: str = Query(..., alias="token"),
):
    """Validate token and set cookie, then redirect. Keep token in URL for WebSocket fallback on mobile."""
    if not _is_token_valid(token):
        raise HTTPException(status_code=401, detail="Invalid access token")
    redirect_url = f"/?token={quote(token, safe='')}"
    response = HTMLResponse(content=f"<script>window.location.href={json.dumps(redirect_url)};</script>Redirecting...")
    response.set_cookie(
        key=TOKEN_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@app.get("/auth/clear")
async def auth_clear(request: Request):
    """Clear the access token cookie and redirect to /. Use when rotating tokens."""
    response = HTMLResponse(content="<script>window.location.href='/';</script>Redirecting...")
    response.delete_cookie(TOKEN_COOKIE_NAME)
    return response


def _access_form_response() -> HTMLResponse:
    """Return minimal HTML for access code entry (no static deps)."""
    html = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EcoScout - Access Required</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: system-ui, sans-serif;
      background: #0a0f0d;
      color: #e2e8f0;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }
    h1 { font-size: 1.5rem; margin-bottom: 8px; color: #4ade80; }
    p { color: #94a3b8; margin-bottom: 24px; font-size: 0.95rem; }
    form { display: flex; flex-direction: column; gap: 12px; width: 100%; max-width: 320px; }
    input {
      padding: 12px 16px;
      border: 1px solid #2a3d33;
      border-radius: 8px;
      background: #121a16;
      color: #e2e8f0;
      font-size: 1rem;
    }
    input:focus { outline: none; border-color: #4ade80; }
    button {
      padding: 12px 24px;
      background: #4ade80;
      color: #0a0f0d;
      border: none;
      border-radius: 8px;
      font-weight: 600;
      font-size: 1rem;
      cursor: pointer;
    }
    button:hover { background: #22c55e; }
    .error { color: #f87171; font-size: 0.9rem; margin-top: 8px; }
  </style>
</head>
<body>
  <h1>EcoScout</h1>
  <p>Enter your access code to continue.</p>
  <p style="margin-bottom:16px;"><a href="/auth/clear" style="color:#94a3b8;font-size:0.85rem;">Use different code</a></p>
  <form id="accessForm">
    <input type="password" id="token" placeholder="Access code" autocomplete="off" required />
    <button type="submit">Continue</button>
  </form>
  <div id="error" class="error" style="display:none;"></div>
  <script>
    document.getElementById("accessForm").onsubmit = function(e) {
      e.preventDefault();
      const token = document.getElementById("token").value.trim();
      if (!token) return;
      const base = window.location.origin + window.location.pathname.replace(/\\/$/, "") || "/";
      window.location.href = base + "/auth/set?token=" + encodeURIComponent(token);
    };
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html)


@app.get("/api/geocode")
async def geocode_endpoint(
    request: Request,
    lat: float = Query(...),
    lon: float = Query(...),
    _: None = Depends(require_token),
):
    """Server-side reverse geocoding to keep API keys off the client."""
    if GOOGLE_MAPS_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    "https://maps.googleapis.com/maps/api/geocode/json",
                    params={"latlng": f"{lat},{lon}", "key": GOOGLE_MAPS_API_KEY},
                )
                data = resp.json()
                if data.get("results"):
                    address = data["results"][0].get("formatted_address", "")
                    parts = address.split(",")
                    short = ", ".join(p.strip() for p in parts[:3])
                    return JSONResponse({"locationName": short})
        except Exception as e:
            logger.warning(f"Google Maps geocode failed: {e}")

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={"lat": lat, "lon": lon, "format": "json", "zoom": 18},
                headers={"User-Agent": "EcoScout/1.0"},
            )
            data = resp.json()
            addr = data.get("address", {})
            parts = [
                addr.get("tourism") or addr.get("garden") or addr.get("attraction")
                or addr.get("park") or addr.get("nature_reserve") or addr.get("leisure") or "",
                addr.get("city") or addr.get("town") or addr.get("village") or addr.get("suburb") or "",
                addr.get("state") or addr.get("county") or "",
            ]
            parts = [p for p in parts if p]
            name = ", ".join(parts) if parts else data.get("display_name", "").split(",")[0]
            return JSONResponse({"locationName": name})
    except Exception as e:
        logger.warning(f"Nominatim geocode failed: {e}")
        return JSONResponse({"locationName": f"{lat:.4f}, {lon:.4f}"})


# ── Survey API endpoints ────────────────────────────────────────────────────

@app.get("/api/test-dashboard")
async def test_dashboard(request: Request):
    """Inject sample ecology data to test the live dashboard visualizations."""
    import math

    sample_species = [
        {"name": "Dacelo novaeguineae", "commonName": "Laughing Kookaburra", "trophicLevel": "carnivore", "group": "Aves", "conservationStatus": "Least Concern"},
        {"name": "Eucalyptus regnans", "commonName": "Mountain Ash", "trophicLevel": "producer", "group": "Plantae", "conservationStatus": "Least Concern"},
        {"name": "Trichosurus vulpecula", "commonName": "Common Brushtail Possum", "trophicLevel": "herbivore", "group": "Mammalia", "conservationStatus": "Least Concern"},
        {"name": "Trametes versicolor", "commonName": "Turkey Tail Fungus", "trophicLevel": "decomposer", "group": "Fungi", "conservationStatus": "Not Evaluated"},
        {"name": "Malurus cyaneus", "commonName": "Superb Fairywren", "trophicLevel": "omnivore", "group": "Aves", "conservationStatus": "Least Concern"},
        {"name": "Tiliqua scincoides", "commonName": "Blue-tongued Lizard", "trophicLevel": "omnivore", "group": "Reptilia", "conservationStatus": "Least Concern"},
        {"name": "Petaurus breviceps", "commonName": "Sugar Glider", "trophicLevel": "omnivore", "group": "Mammalia", "conservationStatus": "Least Concern"},
        {"name": "Ninox strenua", "commonName": "Powerful Owl", "trophicLevel": "carnivore", "group": "Aves", "conservationStatus": "Vulnerable"},
    ]

    for sp in sample_species:
        _ecology_events.append({"species": sp})

    n = len(sample_species)
    counts = {}
    for sp in sample_species:
        counts[sp["name"]] = counts.get(sp["name"], 0) + 1
    total = sum(counts.values())
    h = -sum((c/total) * math.log(c/total) for c in counts.values())
    simp = 1 - sum(c*(c-1) for c in counts.values()) / (total*(total-1)) if total > 1 else 0
    even = h / math.log(n) if n > 1 else 1.0

    _ecology_events.append({
        "metrics": {"shannon": round(h, 3), "simpson": round(simp, 3), "richness": n, "evenness": round(even, 3)},
    })
    _ecology_events.append({"expectedSpecies": 45})

    _ecology_events.append({"relationship": {"source": "Dacelo novaeguineae", "target": "Tiliqua scincoides", "type": "predator-prey"}})
    _ecology_events.append({"relationship": {"source": "Trichosurus vulpecula", "target": "Eucalyptus regnans", "type": "herbivory"}})
    _ecology_events.append({"relationship": {"source": "Malurus cyaneus", "target": "Trametes versicolor", "type": "ecological"}})
    _ecology_events.append({"relationship": {"source": "Ninox strenua", "target": "Petaurus breviceps", "type": "predator-prey"}})
    _ecology_events.append({"relationship": {"source": "Ninox strenua", "target": "Trichosurus vulpecula", "type": "predator-prey"}})

    return JSONResponse({
        "status": "ok",
        "message": f"Injected {n} sample species, metrics, relationships, and expected species count. Watch the dashboard!",
    })


@app.get("/api/survey/{session_id}")
async def get_survey_stats(
    request: Request,
    session_id: str,
    _: None = Depends(require_token),
):
    """Return current survey state: observations, metrics, species list."""
    from ecoscout_agent.tools import calculate_biodiversity_metrics
    metrics = await calculate_biodiversity_metrics(session_id)
    return JSONResponse(metrics)


@app.get("/api/nearby-species")
async def get_nearby_species(
    request: Request,
    lat: float = Query(...),
    lon: float = Query(...),
    radius: float = Query(5.0),
    _: None = Depends(require_token),
):
    """Query iNaturalist for species near a GPS location."""
    from ecoscout_agent.tools import query_nearby_species
    result = await query_nearby_species(lat, lon, radius)
    return JSONResponse(result)


# ── WebSocket endpoint ──────────────────────────────────────────────────────

@app.websocket("/ws/{user_id}/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket, user_id: str, session_id: str
) -> None:
    """Bidirectional streaming endpoint for EcoScout.

    Accepts:
      - Binary frames: raw PCM audio (16kHz, 16-bit, mono)
      - JSON text frames:
          {"type": "image", "data": "<base64 JPEG>", "mimeType": "image/jpeg"}
          {"type": "text", "text": "user message"}
          {"type": "gps", "lat": 51.5, "lon": -0.12, "locationName": "..."}

    When ECOSCOUT_ACCESS_TOKEN is set, client must pass ?token=... in the WebSocket URL.
    """
    token = _get_token_from_websocket_scope(websocket.scope)
    if not _is_token_valid(token):
        raise HTTPException(status_code=401, detail="Access token required")

    logger.debug(f"WebSocket connection: user={user_id}, session={session_id}")
    await websocket.accept()

    clear_session_events()

    model_name = agent.model
    is_native_audio = "native-audio" in model_name.lower()

    if is_native_audio:
        run_config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            session_resumption=types.SessionResumptionConfig(),
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=100000,
                sliding_window=types.SlidingWindow(target_tokens=80000),
            ),
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
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=100000,
                sliding_window=types.SlidingWindow(target_tokens=80000),
            ),
        )

    session = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if not session:
        await session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )

    live_request_queue = LiveRequestQueue()

    _gps_state = {"lat": 0.0, "lon": 0.0, "last_location": ""}
    _location_sent = False

    async def upstream_task() -> None:
        """Receive messages from WebSocket client and route to LiveRequestQueue."""
        nonlocal _location_sent

        while True:
            message = await websocket.receive()

            if "bytes" in message:
                audio_data = message["bytes"]
                audio_blob = types.Blob(
                    mime_type="audio/pcm;rate=16000", data=audio_data
                )
                live_request_queue.send_realtime(audio_blob)

            elif "text" in message:
                text_data = message["text"]
                try:
                    json_msg = json.loads(text_data)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON: {text_data[:100]}")
                    continue

                msg_type = json_msg.get("type")

                if msg_type == "text":
                    content = types.Content(
                        parts=[types.Part(text=json_msg["text"])]
                    )
                    live_request_queue.send_content(content)

                elif msg_type == "image":
                    image_data = base64.b64decode(json_msg["data"])
                    mime_type = json_msg.get("mimeType", "image/jpeg")
                    image_blob = types.Blob(
                        mime_type=mime_type, data=image_data
                    )
                    live_request_queue.send_realtime(image_blob)

                elif msg_type == "gps":
                    _gps_state["lat"] = json_msg.get("lat", 0.0)
                    _gps_state["lon"] = json_msg.get("lon", 0.0)
                    location_name = json_msg.get("locationName", "")
                    _gps_state["last_location"] = location_name

                    if location_name and not _location_sent:
                        _location_sent = True
                        utc_offset_hours = round(_gps_state["lon"] / 15)
                        local_tz = timezone(timedelta(hours=utc_offset_hours))
                        local_time = datetime.now(local_tz)
                        time_str = local_time.strftime("%I:%M %p, %A %B %d, %Y")

                        context = types.Content(
                            parts=[types.Part(text=(
                                f"System update — The user is currently at: {location_name} "
                                f"(GPS: {_gps_state['lat']:.6f}, {_gps_state['lon']:.6f}). "
                                f"Local time is {time_str}. "
                                f"Use this location and time to contextualize species "
                                f"identification, habitat descriptions, and ecological "
                                f"observations for this specific region."
                            ))]
                        )
                        live_request_queue.send_content(context)
                        logger.info(f"Location context sent to agent (once): {location_name}")

    async def downstream_task() -> None:
        """Forward ADK events to the WebSocket client."""
        async for event in runner.run_live(
            user_id=user_id,
            session_id=session_id,
            live_request_queue=live_request_queue,
            run_config=run_config,
        ):
            event_json = event.model_dump_json(
                exclude_none=True, by_alias=True
            )
            await websocket.send_text(event_json)

    async def video_notification_task() -> None:
        """Poll for completed videos and send notifications to client."""
        notified = set()
        notified_started = set()
        while True:
            await asyncio.sleep(2)
            try:
                for vid in list(_video_started_events.keys()):
                    if vid not in notified_started:
                        notified_started.add(vid)
                        await websocket.send_text(json.dumps({
                            "type": "video_started",
                            "video_id": vid,
                        }))
                        logger.info(f"Video started notification sent: {vid}")
                for vid, info in list(_video_ready_events.items()):
                    if vid in notified:
                        continue
                    status = info.get("status")
                    if status == "ready" and info.get("url"):
                        notified.add(vid)
                        await websocket.send_text(json.dumps({
                            "type": "video_ready",
                            "video_id": vid,
                            "url": info["url"],
                        }))
                        logger.info(f"Video notification sent: {vid}")
                    elif status == "failed":
                        notified.add(vid)
                        await websocket.send_text(json.dumps({
                            "type": "video_failed",
                            "video_id": vid,
                            "error": info.get("error", "Unknown error"),
                        }))
                        logger.warning(f"Video failure notification sent: {vid}")
            except Exception as e:
                logger.warning(f"Video notification send failed: {e}")
                return

    async def ecology_notification_task() -> None:
        """Poll for ecology events from tools and push to frontend dashboard."""
        logger.info("ecology_notification_task STARTED")
        while True:
            await asyncio.sleep(1)
            while _ecology_events:
                event = _ecology_events[0]
                payload = {**event, "type": "ecology_update"}
                try:
                    await websocket.send_text(json.dumps(payload))
                    _ecology_events.pop(0)
                    logger.info(f"Ecology update SENT: {payload.get('species', {}).get('name', '?')}")
                except Exception as e:
                    logger.warning(f"Ecology send failed, events preserved: {e}")
                    return

    async def field_entry_notification_task() -> None:
        """Poll for generated field guide entries and push to frontend."""
        notified: set[str] = set()
        while True:
            await asyncio.sleep(2)
            try:
                for eid, info in list(_field_entry_events.items()):
                    if eid not in notified and info.get("image_url"):
                        notified.add(eid)
                        await websocket.send_text(json.dumps({
                            "type": "field_entry_ready",
                            **info,
                        }))
                        logger.info(f"Field entry notification sent: {eid}")
            except Exception as e:
                logger.warning(f"Field entry notification send failed: {e}")
                return

    try:
        await asyncio.gather(
            upstream_task(),
            downstream_task(),
            video_notification_task(),
            ecology_notification_task(),
            field_entry_notification_task(),
        )
    except WebSocketDisconnect:
        logger.debug("Client disconnected")
    except Exception as e:
        logger.error(
            "Streaming error (often Vertex AI permission or region): %s",
            e,
            exc_info=True,
        )
    finally:
        live_request_queue.close()
