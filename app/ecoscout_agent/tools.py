"""EcoScout tools: species identification, observation recording, field guide
generation, video generation (Veo 3.1), expedition journaling, AND ecological
intelligence (iNaturalist, weather, biodiversity metrics, survey reports).
"""

import asyncio
import logging
import math
import os
import uuid
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import httpx
from google import genai
from google.genai.types import GenerateContentConfig, Modality

logger = logging.getLogger(__name__)

# ── Clients (lazy-initialized) ──────────────────────────────────────────────

_genai_client = None
_firestore_db = None
_storage_client = None

BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "ecoscout-media-ecoscout-vertexai-2026")
GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
LOCAL_MEDIA_DIR = Path(__file__).resolve().parent.parent / "static" / "media" / "videos"


def _get_genai_client():
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client()
    return _genai_client


def _get_firestore_db():
    """Return Firestore client. Skips init if project is unset (avoids RESOURCE_PROJECT_INVALID)."""
    global _firestore_db
    if _firestore_db is None and GCP_PROJECT:
        try:
            from google.cloud import firestore
            _firestore_db = firestore.AsyncClient(project=GCP_PROJECT)
        except Exception as e:
            logger.warning(f"Firestore client init failed: {e}")
            _firestore_db = False  # Sentinel: don't retry
    return _firestore_db if _firestore_db else None


def _get_storage_client():
    global _storage_client
    if _storage_client is None:
        from google.cloud import storage
        _storage_client = storage.Client(project=GCP_PROJECT)
    return _storage_client


# ── Shared state for video notifications ────────────────────────────────────

_video_ready_events: dict[str, dict] = {}
_video_started_events: dict[str, dict] = {}
_video_metadata: dict[str, dict] = {}

_ecology_events: list[dict] = []


def clear_session_events():
    """Reset all in-memory event queues for a fresh survey session."""
    _video_ready_events.clear()
    _video_started_events.clear()
    _ecology_events.clear()


# ── Tool: identify_specimen ─────────────────────────────────────────────────

async def identify_specimen(
    description: str,
    common_features: str,
    habitat_context: str = "",
    season: str = "",
) -> dict:
    """Identify a plant, animal, fungus, or mineral specimen based on visual observation.

    The agent should call this AFTER using google_search to verify the identification.
    This tool structures the result into a consistent format.

    Args:
        description: Detailed visual description of the specimen from camera observation.
        common_features: Key identifying features (color, shape, size, markings, texture).
        habitat_context: Surrounding environment details (tree species, soil, moisture).
        season: Current season or time of year if known.

    Returns:
        dict: Structured identification with species_name, common_name, taxonomy,
              ecological_role, safety_warnings, confidence_level, and notes.
    """
    logger.info(f"[TOOL CALLED] identify_specimen: {description[:80]}")
    return {
        "status": "identified",
        "description": description,
        "common_features": common_features,
        "habitat_context": habitat_context,
        "season": season,
        "note": "Identification structured. Agent should populate species details "
                "from google_search grounding results and relay to user.",
    }


# ── Tool: record_observation ────────────────────────────────────────────────

async def record_observation(
    species_name: str,
    common_name: str,
    description: str,
    ecological_notes: str,
    confidence_level: str,
    safety_warnings: str = "",
    gps_lat: float = 0.0,
    gps_lon: float = 0.0,
    session_id: str = "default",
    trophic_level: str = "unknown",
    taxonomic_group: str = "",
    conservation_status: str = "Not Evaluated",
) -> dict:
    """Record a nature observation to the expedition journal in Firestore.

    Call this after identifying a specimen to persist the finding with
    GPS coordinates and ecological context. This also updates the live
    survey dashboard in the frontend.

    Args:
        species_name: Scientific name (e.g. "Amanita muscaria").
        common_name: Common name (e.g. "Fly Agaric").
        description: Visual description of the specimen.
        ecological_notes: Ecological context and relationships observed.
        confidence_level: Identification confidence ("high", "medium", "low").
        safety_warnings: Any toxicity or danger warnings. Empty if safe.
        gps_lat: GPS latitude of the observation location.
        gps_lon: GPS longitude of the observation location.
        session_id: Current session identifier.
        trophic_level: Ecological role - one of "producer", "herbivore", "omnivore",
            "carnivore", "decomposer", or "unknown".
        taxonomic_group: Broad group e.g. "Aves", "Mammalia", "Fungi", "Plantae", "Insecta".
        conservation_status: IUCN status e.g. "Least Concern", "Vulnerable", "Endangered".

    Returns:
        dict: Confirmation with observation_id and timestamp.
    """
    obs_id = str(uuid.uuid4())[:8]
    timestamp = (
        datetime.now(datetime.UTC).isoformat()
        if hasattr(datetime, "UTC")
        else datetime.utcnow().isoformat()
    )

    db = _get_firestore_db()
    if db:
        try:
            await db.collection("sessions").document(session_id) \
                .collection("observations").document(obs_id).set({
                    "species_name": species_name,
                    "common_name": common_name,
                    "description": description,
                    "ecological_notes": ecological_notes,
                    "confidence_level": confidence_level,
                    "safety_warnings": safety_warnings,
                    "gps": {"lat": gps_lat, "lon": gps_lon},
                    "trophic_level": trophic_level,
                    "taxonomic_group": taxonomic_group,
                    "conservation_status": conservation_status,
                    "timestamp": timestamp,
                })
            logger.info(f"Observation {obs_id} saved for {species_name}")
        except Exception as e:
            logger.warning(f"Firestore write failed (non-critical): {e}")

    _ecology_events.append({
        "species": {
            "name": species_name,
            "commonName": common_name,
            "trophicLevel": trophic_level,
            "group": taxonomic_group,
            "conservationStatus": conservation_status,
        },
    })

    return {
        "status": "recorded",
        "observation_id": obs_id,
        "species_name": species_name,
        "common_name": common_name,
        "timestamp": timestamp,
    }


# ── Tool: generate_field_entry ──────────────────────────────────────────────

async def generate_field_entry(
    species_name: str,
    common_name: str,
    description: str,
    habitat: str,
    gps_lat: float = 0.0,
    gps_lon: float = 0.0,
    session_id: str = "default",
) -> dict:
    """Generate an illustrated field guide entry with interleaved text and image.

    Uses Gemini 3 Pro Image to create a naturalist-style illustrated entry
    combining narrative text with a generated botanical/zoological illustration.

    Args:
        species_name: Scientific name of the specimen.
        common_name: Common name of the specimen.
        description: Detailed visual description for the illustration.
        habitat: Habitat and ecological context.
        gps_lat: GPS latitude of the observation.
        gps_lon: GPS longitude of the observation.
        session_id: Current session identifier.

    Returns:
        dict: Field guide entry with entry_id, text_content, and image_url.
    """
    entry_id = str(uuid.uuid4())[:8]

    prompt = (
        f"Create a naturalist's field guide entry for {common_name} ({species_name}). "
        f"Include a detailed botanical or zoological illustration in the style of a "
        f"Victorian naturalist's journal, with fine ink-and-watercolor detail. "
        f"Visual description: {description}. Habitat: {habitat}. "
        f"Write a concise scientific description alongside the illustration."
    )

    try:
        client = _get_genai_client()
        response = client.models.generate_content(
            model=os.getenv("IMAGE_MODEL", "gemini-3-pro-image-preview"),
            contents=prompt,
            config=GenerateContentConfig(
                response_modalities=[Modality.TEXT, Modality.IMAGE],
            ),
        )
    except Exception as e:
        logger.error(f"Field guide generation failed: {e}")
        return {"status": "error", "error": str(e)}

    text_content = ""
    image_url = ""

    for part in response.candidates[0].content.parts:
        if part.text:
            text_content += part.text
        elif part.inline_data:
            image_bytes = part.inline_data.data
            blob_name = f"field-guide/{session_id}/{entry_id}.png"
            try:
                storage = _get_storage_client()
                bucket = storage.bucket(BUCKET_NAME)
                blob = bucket.blob(blob_name)
                blob.upload_from_string(image_bytes, content_type="image/png")
                image_url = blob.generate_signed_url(
                    expiration=timedelta(hours=24), method="GET"
                )
                logger.info(f"Field guide image uploaded: {blob_name}")
            except Exception as e:
                logger.warning(f"GCS upload failed (non-critical): {e}")

    db = _get_firestore_db()
    if db:
        try:
            await db.collection("sessions").document(session_id) \
                .collection("field_entries").document(entry_id).set({
                    "species_name": species_name,
                    "common_name": common_name,
                    "text_content": text_content,
                    "image_url": image_url,
                    "gps": {"lat": gps_lat, "lon": gps_lon},
                    "timestamp": (datetime.now(datetime.UTC) if hasattr(datetime, "UTC") else datetime.utcnow()).isoformat(),
                })
        except Exception as e:
            logger.warning(f"Firestore write failed (non-critical): {e}")

    return {
        "status": "created",
        "entry_id": entry_id,
        "text_content": text_content[:500],
        "image_url": image_url,
    }


# ── Tool: generate_nature_video ─────────────────────────────────────────────

async def generate_nature_video(
    species_name: str,
    process_description: str,
    ecological_context: str,
    visual_style: str = "photorealistic nature documentary",
    duration_seconds: int = 8,
    auto_extend_count: int = 0,
    session_id: str = "default",
) -> dict:
    """Generate an educational nature video using Veo 3.1 with context-rich prompting.

    The agent crafts a deeply contextual video prompt from the accumulated session
    knowledge (species, ecosystem, season, lighting, surrounding organisms).
    Video generation is async -- the agent should continue the conversation
    and notify the user when the video is ready.

    Args:
        species_name: Scientific name of the subject species.
        process_description: Natural process to visualize (e.g. "mushroom growth cycle",
            "butterfly metamorphosis", "seed germination and root development").
        ecological_context: Detailed ecosystem context from the live session (e.g.
            "beneath European oak roots, decomposing autumn leaf litter, dappled sunlight").
        visual_style: Visual style for the video. Defaults to photorealistic documentary.
        duration_seconds: Video length in seconds. Veo 3.1 supports 4, 6, or 8. Default 8.
        auto_extend_count: Number of automatic extensions after initial generation (each adds ~7s).
            E.g. 1 yields ~15s total, 2 yields ~22s. Use when user wants longer videos.
        session_id: Current session identifier.

    Returns:
        dict: Video request with video_id, status "generating", and estimated wait time.
    """
    logger.info(
        f"[TOOL CALLED] generate_nature_video: species={species_name}, "
        f"process={process_description}, context={ecological_context[:80]}"
    )
    video_id = str(uuid.uuid4())[:8]

    duration_val = duration_seconds if duration_seconds in (4, 6, 8) else 8
    prompt = (
        f"Cinematic nature documentary footage: {process_description} of {species_name}. "
        f"Setting: {ecological_context}. "
        f"Style: {visual_style}. No people, no text overlays."
    )
    logger.info(f"[VIDEO] Prompt: {prompt}")

    try:
        client = _get_genai_client()
        from google.genai import types as genai_types
        use_vertex = (
            os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() == "true"
            or bool(os.getenv("GOOGLE_CLOUD_PROJECT"))
        )
        config_kw = {"number_of_videos": 1, "duration_seconds": duration_val}
        # Only use output_gcs_uri if explicitly enabled via env var.
        # The Vertex AI service agent needs storage.objects.create on the bucket,
        # which requires a separate IAM grant. Without it, Vertex returns inline.
        if os.getenv("VEO_OUTPUT_GCS", "").lower() == "true" and use_vertex and BUCKET_NAME:
            config_kw["output_gcs_uri"] = f"gs://{BUCKET_NAME}/videos/{session_id}/"
        config = genai_types.GenerateVideosConfig(**config_kw)
        operation = client.models.generate_videos(
            model=os.getenv("VIDEO_MODEL", "veo-3.1-generate-preview"),
            source=genai_types.GenerateVideosSource(prompt=prompt),
            config=config,
        )
    except Exception as e:
        logger.error(f"Video generation failed to start: {e}")
        return {"status": "error", "error": str(e)}

    meta = {
        "session_id": session_id,
        "operation_name": getattr(operation, "name", ""),
        "status": "generating",
        "prompt": prompt,
        "species": species_name,
        "auto_extend_count": auto_extend_count,
        "process_description": process_description,
        "created_at": datetime.now(datetime.UTC).isoformat()
        if hasattr(datetime, "UTC")
        else datetime.utcnow().isoformat(),
    }
    _video_metadata[video_id] = meta
    db = _get_firestore_db()
    if db:
        try:
            await db.collection("videos").document(video_id).set(meta)
        except Exception as e:
            logger.warning(f"Firestore write failed (non-critical): {e}")

    asyncio.create_task(_poll_video_completion(video_id, operation, session_id))
    _video_started_events[video_id] = {"status": "started"}

    logger.info(f"Video generation started: {video_id} for {species_name}")
    return {
        "status": "generating",
        "video_id": video_id,
        "message": f"Video of {process_description} is being generated. "
                   f"I'll let you know when it's ready!",
        "estimated_wait_seconds": 60,
    }


def _gcs_uri_from_video_obj(video_obj) -> str | None:
    """Extract gs:// URI from video response object (used when output_gcs_uri was set)."""
    return (
        getattr(video_obj, "uri", None)
        or getattr(video_obj, "download_uri", None)
        or getattr(video_obj, "gcs_uri", None)
    ) or None


def _signed_url_from_gcs_uri(gcs_uri: str) -> str:
    """Generate a signed URL for a gs:// object."""
    if not gcs_uri or not gcs_uri.startswith("gs://"):
        return ""
    try:
        parts = gcs_uri[5:].split("/", 1)
        bucket_name, blob_path = parts[0], parts[1]
        storage_client = _get_storage_client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        return blob.generate_signed_url(expiration=timedelta(hours=24), method="GET")
    except Exception as e:
        logger.warning(f"Signed URL from {gcs_uri} failed: {e}")
        return ""


def _download_video_from_response(video_obj, client) -> bytes | None:
    """Extract video bytes from API response. Handles inline bytes, GCS, and Gemini Developer."""
    # 1. Inline bytes (Vertex AI returns these when output_gcs_uri is not set)
    inline = getattr(video_obj, "video_bytes", None)
    if inline and isinstance(inline, bytes):
        logger.info(f"Video bytes received inline ({len(inline)} bytes)")
        return inline

    # 2. GCS URI (when output_gcs_uri was used)
    gcs_uri = _gcs_uri_from_video_obj(video_obj)
    if gcs_uri and gcs_uri.startswith("gs://"):
        try:
            parts = gcs_uri[5:].split("/", 1)
            bucket_name, blob_path = parts[0], parts[1]
            storage_client = _get_storage_client()
            bucket = storage_client.bucket(bucket_name)
            return bucket.blob(blob_path).download_as_bytes()
        except Exception as gcs_err:
            logger.warning(f"GCS download from {gcs_uri} failed: {gcs_err}")

    # 3. Gemini Developer client files.download (not supported on Vertex AI)
    try:
        video_file = client.files.download(file=video_obj)
        return video_file if isinstance(video_file, bytes) else video_file.read()
    except (ValueError, AttributeError) as e:
        if "only supported in the Gemini Developer client" not in str(e):
            raise
        logger.warning(f"files.download not available (Vertex AI); no inline bytes or GCS URI found")
        return None


def _upload_video_bytes(video_file, video_id: str, session_id: str) -> tuple[str, str]:
    """Upload video bytes to GCS or local; return (display_url, gcs_uri). gcs_uri empty if local."""
    if video_file is None:
        return ("", "")
    video_bytes = video_file if isinstance(video_file, bytes) else (
        video_file.read() if hasattr(video_file, "read") else None
    )
    if not video_bytes:
        return ("", "")
    url = ""
    gcs_uri = ""
    try:
        storage = _get_storage_client()
        bucket = storage.bucket(BUCKET_NAME)
        blob_path = f"videos/{session_id}/{video_id}.mp4"
        blob = bucket.blob(blob_path)
        blob.upload_from_string(video_bytes, content_type="video/mp4")
        url = blob.generate_signed_url(expiration=timedelta(hours=24), method="GET")
        gcs_uri = f"gs://{BUCKET_NAME}/{blob_path}"
    except Exception as e:
        logger.warning(f"GCS signed URL failed, falling back to local: {e}")

    if not url:
        try:
            LOCAL_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
            local_path = LOCAL_MEDIA_DIR / f"{video_id}.mp4"
            local_path.write_bytes(video_bytes)
            url = f"/static/media/videos/{video_id}.mp4"
            logger.info(f"Video saved locally: {local_path} ({len(video_bytes)} bytes)")
        except Exception as e:
            logger.error(f"Local video save also failed: {e}")
    return (url, gcs_uri)


def _find_video_in_gcs_prefix(bucket_name: str, prefix: str) -> tuple[str, str] | None:
    """When Vertex writes to output_gcs_uri, find the generated video in GCS.
    Returns (signed_url, gcs_uri) or None if not found.
    """
    try:
        storage_client = _get_storage_client()
        bucket = storage_client.bucket(bucket_name)
        blobs = list(bucket.list_blobs(prefix=prefix))
        # Filter to .mp4 and pick most recently updated
        mp4_blobs = [b for b in blobs if b.name.lower().endswith(".mp4")]
        if not mp4_blobs:
            return None
        latest = max(mp4_blobs, key=lambda b: b.updated or b.time_created)
        gcs_uri = f"gs://{bucket_name}/{latest.name}"
        url = latest.generate_signed_url(expiration=timedelta(hours=24), method="GET")
        return (url, gcs_uri)
    except Exception as e:
        logger.warning(f"GCS fallback lookup failed for {prefix}: {e}")
        return None


async def _poll_video_completion(video_id: str, operation, session_id: str):
    """Background task: poll Veo 3.1 until complete, upload to Cloud Storage.

    If the Firestore doc has auto_extend_count > 0 and we have an absolute URL,
    chains extend calls to produce a longer video.
    """
    client = _get_genai_client()
    from google.genai import types as genai_types

    try:
        while not operation.done:
            await asyncio.sleep(10)
            operation = client.operations.get(operation)

        # Check for Vertex AI operation-level error first
        op_error = getattr(operation, "error", None)
        if op_error:
            error_msg = op_error if isinstance(op_error, str) else (
                op_error.get("message", str(op_error)) if isinstance(op_error, dict) else str(op_error)
            )
            logger.error(f"Vertex AI video operation failed for {video_id}: {error_msg}")
            raise RuntimeError(f"Vertex AI video generation failed: {error_msg}")

        response = getattr(operation, "result", None) or getattr(operation, "response", None)
        generated = getattr(response, "generated_videos", None) if response else None
        video = None

        if response and generated and len(generated) > 0:
            video = generated[0]
        elif response is None or not generated:
            # When output_gcs_uri was used, Vertex writes directly to GCS
            gcs_prefix = f"videos/{session_id}/"
            if BUCKET_NAME:
                found = None
                for _ in range(5):
                    found = _find_video_in_gcs_prefix(BUCKET_NAME, gcs_prefix)
                    if found:
                        break
                    await asyncio.sleep(5)
                if found:
                    url, gcs_uri = found
                    _video_ready_events[video_id] = {"url": url, "status": "ready"}
                    _video_metadata[video_id] = _video_metadata.get(video_id, {}) | {
                        "status": "ready", "url": url, "gcs_uri": gcs_uri,
                        "completed_at": datetime.now(datetime.UTC).isoformat()
                        if hasattr(datetime, "UTC") else datetime.utcnow().isoformat(),
                    }
                    db = _get_firestore_db()
                    if db:
                        try:
                            await db.collection("videos").document(video_id).update({
                                "status": "ready", "url": url, "gcs_uri": gcs_uri,
                                "completed_at": datetime.now(datetime.UTC).isoformat()
                                if hasattr(datetime, "UTC") else datetime.utcnow().isoformat(),
                            })
                        except Exception:
                            pass
                    logger.info(f"Video {video_id} ready (GCS fallback): {url}")
                    return
            raise RuntimeError(
                "Video operation completed but no video was returned and none found in GCS. "
                "The model may have rejected the prompt (content policy) or encountered an internal error."
            )

        video_obj = video.video
        existing_gcs = _gcs_uri_from_video_obj(video_obj)
        if existing_gcs and existing_gcs.startswith("gs://"):
            url = _signed_url_from_gcs_uri(existing_gcs)
            gcs_uri = existing_gcs
        else:
            video_bytes = _download_video_from_response(video_obj, client)
            url, gcs_uri = _upload_video_bytes(video_bytes, video_id, session_id) if video_bytes else ("", "")

        doc_data = _video_metadata.get(video_id, {})
        db = _get_firestore_db()
        if db:
            try:
                doc = await db.collection("videos").document(video_id).get()
                if doc.exists:
                    doc_data = doc.to_dict() or doc_data
            except Exception:
                pass
        auto_extend_count = doc_data.get("auto_extend_count", 0)
        process_description = doc_data.get("process_description", "")

        try:
            # Veo extend requires gs:// URI, not signed https URL
            video_uri_for_extend = gcs_uri if gcs_uri else (url if url and url.startswith("gs://") else None)
            while auto_extend_count > 0 and video_uri_for_extend and url and url.startswith("http"):
                auto_extend_count -= 1
                ext_prompt = (
                    f"Continue the natural process of {process_description} seamlessly, "
                    "maintaining visual continuity with the previous sequence."
                )
                logger.info(f"Auto-extending video {video_id} ({auto_extend_count + 1} remaining)")
                video_input = genai_types.Video(uri=video_uri_for_extend, mime_type="video/mp4")
                ext_operation = client.models.generate_videos(
                    model=os.getenv("VIDEO_MODEL", "veo-3.1-generate-preview"),
                    source=genai_types.GenerateVideosSource(
                        prompt=ext_prompt,
                        video=video_input,
                    ),
                    config=genai_types.GenerateVideosConfig(number_of_videos=1),
                )
                while not ext_operation.done:
                    await asyncio.sleep(10)
                    ext_operation = client.operations.get(ext_operation)
                ext_response = getattr(ext_operation, "result", None) or getattr(ext_operation, "response", None)
                ext_generated = getattr(ext_response, "generated_videos", None) if ext_response else None
                if ext_response and ext_generated and len(ext_generated) > 0:
                    ext_video = ext_generated[0]
                    ext_bytes = _download_video_from_response(ext_video.video, client)
                    if ext_bytes:
                        url, gcs_uri = _upload_video_bytes(ext_bytes, video_id, session_id)
                        video_uri_for_extend = gcs_uri or (url if url.startswith("gs://") else None)
        except Exception as e:
            logger.warning(f"Auto-extend failed for {video_id}: {e}")

        _video_metadata[video_id] = _video_metadata.get(video_id, {}) | {
            "status": "ready", "url": url, "gcs_uri": gcs_uri,
            "completed_at": datetime.now(datetime.UTC).isoformat()
            if hasattr(datetime, "UTC") else datetime.utcnow().isoformat(),
        }
        db = _get_firestore_db()
        if db:
            try:
                update_data = {
                    "status": "ready", "url": url,
                    "completed_at": datetime.now(datetime.UTC).isoformat()
                    if hasattr(datetime, "UTC") else datetime.utcnow().isoformat(),
                }
                if gcs_uri:
                    update_data["gcs_uri"] = gcs_uri
                await db.collection("videos").document(video_id).update(update_data)
            except Exception as e:
                logger.warning(f"Firestore update failed (non-critical): {e}")

        if url:
            _video_ready_events[video_id] = {"url": url, "status": "ready"}
            logger.info(f"Video {video_id} ready: {url}")
        else:
            _video_ready_events[video_id] = {"url": "", "status": "failed", "error": "Video generated but no playback URL could be created"}
            logger.error(f"Video {video_id} generated but URL is empty (signed URL or local save failed)")

    except Exception as e:
        logger.error(f"Video polling failed for {video_id}: {e}", exc_info=True)
        _video_ready_events[video_id] = {"url": "", "status": "failed", "error": str(e)}
        _video_metadata[video_id] = _video_metadata.get(video_id, {}) | {
            "status": "failed", "error": str(e),
        }
        db = _get_firestore_db()
        if db:
            try:
                await db.collection("videos").document(video_id).update({
                    "status": "failed", "error": str(e),
                })
            except Exception:
                pass


# ── Tool: extend_video ──────────────────────────────────────────────────────

async def extend_video(
    video_id: str,
    extension_description: str,
    session_id: str = "default",
) -> dict:
    """Extend an existing Veo 3.1-generated video with additional content.

    Each extension adds approximately 7 seconds. Use this when the user asks
    to continue a video to show the next phase of a natural process.

    Args:
        video_id: The ID of the previously generated video to extend.
        extension_description: Description of what the extension should show
            (e.g. "spore release and wind dispersal phase").
        session_id: Current session identifier.

    Returns:
        dict: Extension status with new video_id.
    """
    new_video_id = str(uuid.uuid4())[:8]

    video_data = _video_metadata.get(video_id, {})
    db = _get_firestore_db()
    if db:
        try:
            video_doc = await db.collection("videos").document(video_id).get()
            if video_doc.exists:
                video_data = video_doc.to_dict() or video_data
        except Exception as e:
            logger.warning(f"Firestore read failed, using fallback: {e}")
    original_prompt = video_data.get("prompt", "")
    original_species = video_data.get("species", "")
    original_video_url = video_data.get("url", "")
    original_gcs_uri = video_data.get("gcs_uri", "")
    if not original_prompt and not original_video_url and not original_gcs_uri:
        return {"status": "error", "error": f"Original video {video_id} not found"}

    extended_prompt = (
        f"{original_prompt} Continue with: {extension_description}. "
        f"Maintain visual continuity with the previous sequence."
    )

    logger.info(
        f"[TOOL CALLED] extend_video: video_id={video_id}, "
        f"description={extension_description}"
    )

    try:
        client = _get_genai_client()
        from google.genai import types as genai_types

        # Veo extend requires gs:// URI; signed https URLs are not accepted
        video_uri = original_gcs_uri if original_gcs_uri else None
        if not video_uri and original_video_url:
            if original_video_url.startswith("gs://"):
                video_uri = original_video_url
            elif "storage.googleapis.com" in original_video_url:
                # Extract gs:// from signed URL: https://storage.googleapis.com/bucket/path?...
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(original_video_url)
                    path = parsed.path.lstrip("/")
                    if path:
                        video_uri = f"gs://{path}"
                except Exception:
                    pass
        use_vertex = (
            os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() == "true"
            or bool(os.getenv("GOOGLE_CLOUD_PROJECT"))
        )
        config_kw = {"number_of_videos": 1}
        if os.getenv("VEO_OUTPUT_GCS", "").lower() == "true" and use_vertex and BUCKET_NAME:
            config_kw["output_gcs_uri"] = f"gs://{BUCKET_NAME}/videos/{session_id}/"
        config = genai_types.GenerateVideosConfig(**config_kw)

        if video_uri:
            video_input = genai_types.Video(uri=video_uri, mime_type="video/mp4")
            operation = client.models.generate_videos(
                model=os.getenv("VIDEO_MODEL", "veo-3.1-generate-preview"),
                source=genai_types.GenerateVideosSource(
                    prompt=extension_description,
                    video=video_input,
                ),
                config=config,
            )
        else:
            operation = client.models.generate_videos(
                model=os.getenv("VIDEO_MODEL", "veo-3.1-generate-preview"),
                source=genai_types.GenerateVideosSource(prompt=extended_prompt),
                config=config,
            )
    except Exception as e:
        logger.error(f"Video extension API call failed: {e}")
        return {"status": "error", "error": str(e)}

    new_meta = {
        "session_id": session_id,
        "parent_video_id": video_id,
        "operation_name": getattr(operation, "name", ""),
        "status": "generating",
        "prompt": extended_prompt,
        "species": original_species,
        "created_at": (datetime.now(datetime.UTC) if hasattr(datetime, "UTC") else datetime.utcnow()).isoformat(),
    }
    _video_metadata[new_video_id] = new_meta
    db = _get_firestore_db()
    if db:
        try:
            await db.collection("videos").document(new_video_id).set(new_meta)
        except Exception as e:
            logger.warning(f"Firestore write failed (non-critical): {e}")

    asyncio.create_task(_poll_video_completion(new_video_id, operation, session_id))

    logger.info(f"Video extension started: {new_video_id} extending {video_id}")
    return {
        "status": "generating",
        "video_id": new_video_id,
        "extends": video_id,
        "message": f"Extending the video with {extension_description}. "
                   f"I'll notify you when it's ready!",
        "estimated_wait_seconds": 60,
    }


# ── Tool: create_expedition_summary ─────────────────────────────────────────

async def create_expedition_summary(session_id: str = "default") -> dict:
    """Compile a complete expedition summary from all observations and media.

    Gathers all observations, field guide entries, and generated videos
    from the current session and returns a structured summary.

    Args:
        session_id: Current session identifier.

    Returns:
        dict: Complete expedition summary with observations, field entries,
              videos, and statistics.
    """
    db = _get_firestore_db()
    observations = []
    field_entries = []
    videos = []
    if db:
        try:
            obs_ref = db.collection("sessions").document(session_id).collection("observations")
            observations = [doc.to_dict() async for doc in obs_ref.stream()]
            entries_ref = db.collection("sessions").document(session_id).collection("field_entries")
            field_entries = [doc.to_dict() async for doc in entries_ref.stream()]
            videos_query = db.collection("videos").where("session_id", "==", session_id)
            videos = [doc.to_dict() async for doc in videos_query.stream()]
        except Exception as e:
            logger.error(f"Expedition summary Firestore read failed: {e}")
    else:
        videos = [v for v in _video_metadata.values() if v.get("session_id") == session_id]

    species_list = list({obs.get("species_name", "") for obs in observations})
    ready_videos = [v for v in videos if v.get("status") == "ready"]

    return {
            "status": "compiled",
            "session_id": session_id,
            "total_observations": len(observations),
            "total_field_entries": len(field_entries),
            "total_videos": len(ready_videos),
            "species_observed": species_list,
            "observations": observations[:10],
            "field_entries": [
                {"species": e.get("species_name"), "image_url": e.get("image_url")}
                for e in field_entries[:5]
            ],
            "videos": [
                {"species": v.get("species"), "url": v.get("url")}
                for v in ready_videos[:5]
            ],
        }


# ══════════════════════════════════════════════════════════════════════════════
# NEW: Ecological Intelligence Tools (iNaturalist, Weather, Biodiversity)
# ══════════════════════════════════════════════════════════════════════════════

_INAT_API_BASE = "https://api.inaturalist.org/v1"
_INAT_HEADERS = {"User-Agent": "EcoScout/2.0 (biodiversity-survey)"}
_OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"


async def _inat_get(path: str, params: dict | None = None) -> dict:
    """Make a rate-limit-aware GET request to the iNaturalist API v1."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{_INAT_API_BASE}{path}",
            params=params or {},
            headers=_INAT_HEADERS,
        )
        resp.raise_for_status()
        return resp.json()


# ── Tool: query_nearby_species ───────────────────────────────────────────────

async def query_nearby_species(
    lat: float,
    lon: float,
    radius_km: float = 5.0,
    iconic_taxa: str = "",
) -> dict:
    """Query iNaturalist for research-grade species observations near a GPS location.

    Returns a list of species documented in the area with observation counts
    and conservation status. Use this to understand what species are known
    to exist near the user's current position.

    Args:
        lat: GPS latitude of the search center.
        lon: GPS longitude of the search center.
        radius_km: Search radius in kilometers (default 5).
        iconic_taxa: Filter by broad group. One of: Plantae, Animalia, Fungi,
            Insecta, Aves, Mammalia, Reptilia, Amphibia, Arachnida, Mollusca,
            Actinopterygii. Empty string for all taxa.

    Returns:
        dict: Species list with total_species count and top species by
              observation frequency, including conservation status.
    """
    logger.info(f"[TOOL] query_nearby_species: ({lat}, {lon}) r={radius_km}km")
    params = {
        "lat": lat,
        "lng": lon,
        "radius": radius_km,
        "quality_grade": "research",
        "per_page": 20,
    }
    if iconic_taxa:
        params["iconic_taxa"] = iconic_taxa

    try:
        data = await _inat_get("/observations/species_counts", params)
        results = data.get("results", [])
        total = data.get("total_results", 0)

        species_list = []
        for r in results[:20]:
            taxon = r.get("taxon", {})
            cs = taxon.get("conservation_status", {})
            species_list.append({
                "name": taxon.get("name", ""),
                "common_name": taxon.get("preferred_common_name", ""),
                "observation_count": r.get("count", 0),
                "iconic_taxon": taxon.get("iconic_taxon_name", ""),
                "conservation_status": cs.get("status_name", "Not Evaluated") if cs else "Not Evaluated",
                "native": not taxon.get("introduced", False),
                "taxon_id": taxon.get("id"),
            })

        return {
            "status": "ok",
            "total_species": total,
            "radius_km": radius_km,
            "location": {"lat": lat, "lon": lon},
            "top_species": species_list,
        }
    except Exception as e:
        logger.error(f"iNaturalist query failed: {e}")
        return {"status": "error", "error": str(e)}


# ── Tool: get_species_info ───────────────────────────────────────────────────

async def get_species_info(taxon_name: str) -> dict:
    """Look up detailed species information from iNaturalist's taxonomy database.

    Cross-references an identification with real scientific data: full taxonomy,
    conservation status, global observation count, and whether the species is
    native or introduced in the observation region.

    Args:
        taxon_name: Scientific name (e.g. "Rhipidura leucophrys") or common
            name (e.g. "Willie Wagtail") to look up.

    Returns:
        dict: Taxonomy, conservation status, observation stats, and Wikipedia summary.
    """
    logger.info(f"[TOOL] get_species_info: {taxon_name}")

    try:
        data = await _inat_get("/taxa/autocomplete", {"q": taxon_name, "per_page": 3})
        results = data.get("results", [])
        if not results:
            return {"status": "not_found", "query": taxon_name}

        taxon = results[0]
        cs = taxon.get("conservation_status", {})

        ancestors = taxon.get("ancestors", [])
        taxonomy = {}
        for a in ancestors:
            rank = a.get("rank", "")
            if rank in ("kingdom", "phylum", "class", "order", "family", "genus"):
                taxonomy[rank] = a.get("name", "")
        taxonomy["species"] = taxon.get("name", "")

        return {
            "status": "found",
            "taxon_id": taxon.get("id"),
            "name": taxon.get("name", ""),
            "common_name": taxon.get("preferred_common_name", ""),
            "rank": taxon.get("rank", ""),
            "taxonomy": taxonomy,
            "observations_count": taxon.get("observations_count", 0),
            "conservation_status": cs.get("status_name", "Not Evaluated") if cs else "Not Evaluated",
            "conservation_status_code": cs.get("status", "") if cs else "",
            "is_active": taxon.get("is_active", True),
            "introduced": taxon.get("introduced", False),
            "native": taxon.get("native", True),
            "endemic": taxon.get("endemic", False),
            "threatened": taxon.get("threatened", False),
            "wikipedia_url": taxon.get("wikipedia_url", ""),
            "iconic_taxon": taxon.get("iconic_taxon_name", ""),
        }
    except Exception as e:
        logger.error(f"iNaturalist species lookup failed: {e}")
        return {"status": "error", "error": str(e)}


# ── Tool: get_area_species_checklist ─────────────────────────────────────────

async def get_area_species_checklist(
    lat: float,
    lon: float,
    radius_km: float = 5.0,
    month: int = 0,
) -> dict:
    """Get the expected species checklist for an area, optionally filtered by month.

    Queries iNaturalist for all research-grade species documented in this area
    during a specific month. This is the foundation for gap analysis: compare
    what you find in your survey against what has historically been observed.

    Args:
        lat: GPS latitude.
        lon: GPS longitude.
        radius_km: Search radius in km (default 5).
        month: Month number (1-12) to filter by seasonal data. 0 for all months.

    Returns:
        dict: Expected species checklist with counts by taxonomic group,
              total documented species, and top species per group.
    """
    logger.info(f"[TOOL] get_area_species_checklist: ({lat},{lon}) month={month}")
    params = {
        "lat": lat,
        "lng": lon,
        "radius": radius_km,
        "quality_grade": "research",
        "per_page": 100,
    }
    if 1 <= month <= 12:
        params["month"] = month

    try:
        data = await _inat_get("/observations/species_counts", params)
        results = data.get("results", [])
        total = data.get("total_results", 0)

        by_group: dict[str, list] = {}
        for r in results:
            taxon = r.get("taxon", {})
            group = taxon.get("iconic_taxon_name", "Other")
            if group not in by_group:
                by_group[group] = []
            by_group[group].append({
                "name": taxon.get("name", ""),
                "common_name": taxon.get("preferred_common_name", ""),
                "count": r.get("count", 0),
            })

        group_summary = {
            g: {"species_count": len(spp), "top_5": spp[:5]}
            for g, spp in by_group.items()
        }

        if total > 0:
            _ecology_events.append({"expectedSpecies": total})

        return {
            "status": "ok",
            "total_documented_species": total,
            "location": {"lat": lat, "lon": lon},
            "radius_km": radius_km,
            "month": month if month else "all",
            "groups": group_summary,
            "group_counts": {g: len(spp) for g, spp in by_group.items()},
        }
    except Exception as e:
        logger.error(f"Area species checklist failed: {e}")
        return {"status": "error", "error": str(e)}


# ── Tool: get_weather_context ────────────────────────────────────────────────

async def get_weather_context(lat: float, lon: float) -> dict:
    """Get current weather conditions for ecological context interpretation.

    Uses the Open-Meteo API (free, no API key required) to fetch current
    weather data. The agent should interpret these conditions ecologically:
    humidity affects amphibian activity, wind affects bird behavior, etc.

    Args:
        lat: GPS latitude.
        lon: GPS longitude.

    Returns:
        dict: Current temperature, humidity, precipitation, wind, cloud cover,
              UV index, and an ecological interpretation hint.
    """
    logger.info(f"[TOOL] get_weather_context: ({lat}, {lon})")
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": (
            "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "precipitation,rain,cloud_cover,wind_speed_10m,wind_direction_10m,"
            "uv_index,is_day"
        ),
        "timezone": "auto",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_OPEN_METEO_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()

        current = data.get("current", {})
        units = data.get("current_units", {})

        temp = current.get("temperature_2m", 0)
        humidity = current.get("relative_humidity_2m", 0)
        precip = current.get("precipitation", 0)
        rain = current.get("rain", 0)
        cloud = current.get("cloud_cover", 0)
        wind = current.get("wind_speed_10m", 0)
        uv = current.get("uv_index", 0)
        is_day = current.get("is_day", 1)

        hints = []
        if humidity > 80:
            hints.append("High humidity favors amphibian and fungal activity")
        if rain > 0 or precip > 0:
            hints.append("Recent rain increases invertebrate surface activity")
        if wind > 20:
            hints.append("Strong wind reduces bird song and insect flight")
        if not is_day:
            hints.append("Nocturnal conditions: expect different species assemblage")
        if 15 <= temp <= 25:
            hints.append("Moderate temperature is optimal for reptile basking")
        if uv > 6:
            hints.append("High UV: many animals seek shade, check sheltered spots")

        return {
            "status": "ok",
            "temperature_c": temp,
            "apparent_temperature_c": current.get("apparent_temperature", temp),
            "relative_humidity_pct": humidity,
            "precipitation_mm": precip,
            "rain_mm": rain,
            "cloud_cover_pct": cloud,
            "wind_speed_kmh": wind,
            "wind_direction_deg": current.get("wind_direction_10m", 0),
            "uv_index": uv,
            "is_day": bool(is_day),
            "timezone": data.get("timezone", ""),
            "ecological_hints": hints,
        }
    except Exception as e:
        logger.error(f"Weather context failed: {e}")
        return {"status": "error", "error": str(e)}


# ── Tool: calculate_biodiversity_metrics ─────────────────────────────────────

async def calculate_biodiversity_metrics(session_id: str = "default") -> dict:
    """Calculate biodiversity indices from all recorded observations in this survey.

    Computes Shannon-Wiener diversity (H'), Simpson's diversity (1-D),
    species richness (S), and Pielou's evenness (J'). These are standard
    ecological metrics that quantify the biodiversity of a surveyed area.

    Args:
        session_id: Current survey session identifier.

    Returns:
        dict: Biodiversity metrics with scientific interpretation.
    """
    logger.info(f"[TOOL] calculate_biodiversity_metrics: session={session_id}")

    observations = []
    db = _get_firestore_db()
    if db:
        try:
            obs_ref = db.collection("sessions").document(session_id).collection("observations")
            observations = [doc.to_dict() async for doc in obs_ref.stream()]
        except Exception as e:
            logger.error(f"Firestore read failed: {e}")
            return {"status": "error", "error": str(e)}

    if not observations:
        return {
            "status": "ok",
            "species_richness": 0,
            "shannon_index": 0.0,
            "simpson_index": 0.0,
            "evenness": 0.0,
            "interpretation": "No observations recorded yet.",
        }

    species_counts: dict[str, int] = {}
    taxonomic_groups: dict[str, set] = {}
    for obs in observations:
        sp = obs.get("species_name", "unknown")
        species_counts[sp] = species_counts.get(sp, 0) + 1

    total_n = sum(species_counts.values())
    s = len(species_counts)

    # Shannon-Wiener H'
    h_prime = 0.0
    for count in species_counts.values():
        p = count / total_n
        if p > 0:
            h_prime -= p * math.log(p)

    # Simpson's diversity 1-D
    simpson_d = 0.0
    for count in species_counts.values():
        simpson_d += (count * (count - 1))
    simpson_1d = 1 - (simpson_d / (total_n * (total_n - 1))) if total_n > 1 else 0.0

    # Pielou's evenness J' = H' / ln(S)
    evenness = h_prime / math.log(s) if s > 1 else 1.0

    interpretation_parts = []
    if h_prime < 1.0:
        interpretation_parts.append(f"Shannon index {h_prime:.2f} indicates low diversity")
    elif h_prime < 2.0:
        interpretation_parts.append(f"Shannon index {h_prime:.2f} indicates moderate diversity")
    else:
        interpretation_parts.append(f"Shannon index {h_prime:.2f} indicates high diversity")

    if evenness < 0.5:
        interpretation_parts.append("community is dominated by few species")
    elif evenness > 0.8:
        interpretation_parts.append("species are relatively evenly distributed")

    # Build species accumulation data (order of discovery)
    seen_species: set = set()
    accumulation = []
    for i, obs in enumerate(observations, 1):
        sp = obs.get("species_name", "unknown")
        seen_species.add(sp)
        accumulation.append({"observation_number": i, "cumulative_species": len(seen_species)})

    return {
        "status": "ok",
        "total_observations": total_n,
        "species_richness": s,
        "shannon_index": round(h_prime, 3),
        "simpson_index": round(simpson_1d, 3),
        "evenness": round(evenness, 3),
        "species_counts": species_counts,
        "accumulation_curve": accumulation,
        "interpretation": ". ".join(interpretation_parts),
    }

    _ecology_events.append({
        "metrics": {
            "shannon": round(h_prime, 3),
            "simpson": round(simpson_1d, 3),
            "richness": s,
            "evenness": round(evenness, 3),
        },
    })


# ── Tool: generate_survey_report ─────────────────────────────────────────────

async def generate_survey_report(session_id: str = "default") -> dict:
    """Generate a professional ecological survey report for the current session.

    Compiles all observations, biodiversity metrics, gap analysis results,
    and ecological narratives into a structured report suitable for
    scientific or conservation purposes.

    Args:
        session_id: Current survey session identifier.

    Returns:
        dict: Structured survey report with metadata, species inventory,
              biodiversity metrics, conservation flags, and recommendations.
    """
    logger.info(f"[TOOL] generate_survey_report: session={session_id}")

    observations = []
    field_entries = []
    videos = []
    db = _get_firestore_db()
    if db:
        try:
            obs_ref = db.collection("sessions").document(session_id).collection("observations")
            observations = [doc.to_dict() async for doc in obs_ref.stream()]
            entries_ref = db.collection("sessions").document(session_id).collection("field_entries")
            field_entries = [doc.to_dict() async for doc in entries_ref.stream()]
            videos_query = db.collection("videos").where("session_id", "==", session_id)
            videos = [doc.to_dict() async for doc in videos_query.stream()]
        except Exception as e:
            logger.error(f"Firestore read failed: {e}")
            return {"status": "error", "error": str(e)}
    else:
        videos = [v for v in _video_metadata.values() if v.get("session_id") == session_id]

    species_set: dict[str, dict] = {}
    taxonomic_groups: dict[str, int] = {}
    conservation_flags = []
    detection_sources = {"visual": 0, "audio": 0, "visual+audio": 0, "unknown": 0}

    for obs in observations:
        sp = obs.get("species_name", "unknown")
        if sp not in species_set:
            species_set[sp] = {
                "scientific_name": sp,
                "common_name": obs.get("common_name", ""),
                "first_observed": obs.get("timestamp", ""),
                "confidence": obs.get("confidence_level", ""),
                "ecological_notes": obs.get("ecological_notes", ""),
                "gps": obs.get("gps", {}),
            }

        notes = obs.get("ecological_notes", "").lower()
        if "audio" in notes and "visual" in notes:
            detection_sources["visual+audio"] += 1
        elif "audio" in notes:
            detection_sources["audio"] += 1
        elif "visual" in notes:
            detection_sources["visual"] += 1
        else:
            detection_sources["unknown"] += 1

        warnings = obs.get("safety_warnings", "")
        if warnings:
            conservation_flags.append({"species": sp, "warning": warnings})

    total_obs = len(observations)
    s = len(species_set)
    total_n = total_obs if total_obs > 0 else 1

    h_prime = 0.0
    species_counts: dict[str, int] = {}
    for obs in observations:
        sp = obs.get("species_name", "unknown")
        species_counts[sp] = species_counts.get(sp, 0) + 1
    for count in species_counts.values():
        p = count / total_n
        if p > 0:
            h_prime -= p * math.log(p)

    timestamps = [obs.get("timestamp", "") for obs in observations if obs.get("timestamp")]
    duration_note = ""
    if len(timestamps) >= 2:
        try:
            t_start = datetime.fromisoformat(timestamps[0])
            t_end = datetime.fromisoformat(timestamps[-1])
            duration_min = (t_end - t_start).total_seconds() / 60
            duration_note = f"{duration_min:.0f} minutes"
        except Exception:
            pass

    gps_points = [obs.get("gps", {}) for obs in observations if obs.get("gps")]
    center_lat = sum(g.get("lat", 0) for g in gps_points) / len(gps_points) if gps_points else 0
    center_lon = sum(g.get("lon", 0) for g in gps_points) / len(gps_points) if gps_points else 0

    report = {
        "status": "compiled",
        "report_type": "Ecological Biodiversity Survey Report",
        "session_id": session_id,
        "generated_at": datetime.utcnow().isoformat(),
        "survey_metadata": {
            "total_observations": total_obs,
            "species_count": s,
            "duration": duration_note,
            "center_coordinates": {"lat": round(center_lat, 6), "lon": round(center_lon, 6)},
            "detection_methods": detection_sources,
        },
        "biodiversity_metrics": {
            "species_richness": s,
            "shannon_wiener_index": round(h_prime, 3),
            "total_individuals": total_obs,
        },
        "species_inventory": list(species_set.values()),
        "conservation_flags": conservation_flags,
        "media_generated": {
            "field_guide_entries": len(field_entries),
            "videos": len([v for v in videos if v.get("status") == "ready"]),
        },
        "recommendations": [],
    }

    if s < 5:
        report["recommendations"].append("Low species count - consider extending survey duration or covering more habitat types")
    if detection_sources.get("audio", 0) == 0:
        report["recommendations"].append("No audio detections recorded - ambient soundscape analysis may improve coverage")
    if not conservation_flags:
        report["recommendations"].append("No conservation-significant species detected in this survey")

    db = _get_firestore_db()
    if db:
        try:
            await db.collection("sessions").document(session_id).collection("reports").document("latest").set(report)
        except Exception as e:
            logger.warning(f"Report save to Firestore failed (non-critical): {e}")

    return report
