# EcoScout - The AI Ecologist

**Gemini Live Agent Challenge 2026 Submission**

EcoScout thinks like an ecologist: it listens to ambient sounds, cross-references iNaturalist's 100M+ observations, computes biodiversity metrics, and builds living ecological visualizations. Through bidirectional audio and video streaming, it identifies species from background audio, generates scientific hypotheses, performs gap analysis against expected species lists, and produces professional ecological survey reports - all through natural voice conversation.

## Key Features

- **Ambient Soundscape Analysis** - Identifies species from background audio (bird calls, frog sounds)
- **Scientific Hypothesis Generation** - Predicts species, identifies gaps, builds ecological narratives
- **iNaturalist Integration** - Cross-references 100M+ observations, conservation status, gap analysis
- **Real-time Biodiversity Metrics** - Shannon-Wiener, Simpson's diversity, species accumulation
- **Living Ecological Visualization** - D3.js food web, accumulation curve, survey dashboard
- **Live Survey Dashboard** - Real-time species count, Shannon H', conservation alerts, updating live as species are identified
- **Echo Cancellation + Mic Toggle** - Browser AEC prevents feedback loops; noise suppression disabled to preserve environmental sounds; mic mute/unmute without re-requesting permissions
- **Illustrated Field Guide** - Gemini 3 Pro interleaved text+image
- **Nature Video Generation** - Veo 3.1 with contextual prompts
- **Professional Survey Reports** - Automated ecological assessment

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Agent Runtime | Google ADK |
| Live Model | gemini-live-2.5-flash-native-audio |
| Image Generation | gemini-3-pro-image-preview |
| Video Generation | Veo 3.1 |
| Grounding | Google Search |
| External Intelligence | iNaturalist API (pyinaturalist), Open-Meteo API |
| Backend | Python 3.11+ / FastAPI / Uvicorn |
| Database | Google Cloud Firestore |
| Storage | Google Cloud Storage |
| Deployment | Google Cloud Run |
| Frontend | Vanilla JS PWA with AudioWorklets + Camera API + GPS |

## Agent Tools

| Tool | Purpose |
|------|---------|
| `google_search` | Species verification, ecological facts grounding |
| `identify_specimen()` | Structure visual identification results |
| `record_observation()` | Save findings with GPS + trophic_level + conservation_status to Firestore and _ecology_events |
| `generate_field_entry()` | Interleaved text + illustration via Gemini 3 Pro Image |
| `generate_nature_video()` | Veo 3.1 async video from contextual prompt |
| `extend_video()` | Continue a video with next phase (+7s) |
| `create_expedition_summary()` | Compile full expedition journal |
| `query_nearby_species()` | iNaturalist species near GPS |
| `get_species_info()` | Detailed taxon lookup + conservation status |
| `get_area_species_checklist()` | Expected species for gap analysis |
| `get_weather_context()` | Ecological weather interpretation |
| `calculate_biodiversity_metrics()` | Shannon, Simpson, richness, evenness |
| `generate_survey_report()` | Professional ecological survey report |

## Quick Start (Local Development)

### Prerequisites

- Python 3.11+
- Google API key with Gemini API access

### Setup

```bash
cd ecoscout

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -e .

# Configure environment
cp app/.env.example app/.env
# Edit app/.env with your GOOGLE_API_KEY

# Run the server
cd app
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

Open http://localhost:8080 in your browser.

### Test Before Deploy

Validate the Gemini Live connection (same config as Cloud Run) before deploying:

```bash
cd ecoscout

# 1. Authenticate for Vertex AI (required for Vertex mode)
gcloud auth application-default login

# 2. Ensure app/.env has Vertex AI config for pre-deploy test
#    GOOGLE_GENAI_USE_VERTEXAI=True
#    GOOGLE_CLOUD_PROJECT=your-project-id
#    GOOGLE_CLOUD_LOCATION=us-central1

# 3. Run the connection test
python scripts/test_gemini_live_connection.py
```

If the test passes, deploy. If you see `1008 policy violation`, ensure `GOOGLE_CLOUD_LOCATION=us-central1` is set (the deploy script sets this automatically).

**Video generation**: Video (Veo) works with Vertex AI. For local video testing, set `GOOGLE_GENAI_USE_VERTEXAI=True` in `app/.env` and run `gcloud auth application-default login`. The app uses `output_gcs_uri` when Vertex is enabled so videos are written to your GCS bucket.

**Firestore optional**: If you see `RESOURCE_PROJECT_INVALID` or Firestore is not configured, the app still works: video metadata uses an in-memory fallback, and observations/field entries are skipped. For full persistence, enable Firestore in your project and run `gcloud firestore databases create --location=nam5 --type=firestore-native`.

## Cloud Deployment

**One-command deploy** (recommended):

```bash
./deploy.sh YOUR_PROJECT_ID
```

Or with a custom token:

```bash
./deploy.sh YOUR_PROJECT_ID --token $(openssl rand -hex 24)
```

Windows PowerShell:

```powershell
.\deploy.ps1 -ProjectId YOUR_PROJECT_ID
```

The script enables APIs, creates Firestore + GCS bucket, configures Secret Manager with the access token, and deploys to Cloud Run. See [docs/DEPLOYMENT_PLAN.md](docs/DEPLOYMENT_PLAN.md) for the full resource list.

**Manual deploy**:

```bash
gcloud run deploy ecoscout --source . --region us-central1 --allow-unauthenticated \
  --session-affinity --timeout 3600 \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=<your-project>,GOOGLE_GENAI_USE_VERTEXAI=True,GOOGLE_CLOUD_LOCATION=us-central1,GCS_BUCKET_NAME=ecoscout-media"
```

**Access control**: The deploy script uses Secret Manager for `ECOSCOUT_ACCESS_TOKEN`. See [docs/AUTH_GUIDE.md](docs/AUTH_GUIDE.md) for token rotation.

## Audio Configuration

EcoScout uses a carefully tuned audio pipeline:

- **Echo Cancellation**: Enabled (`echoCancellation: true`) to prevent the agent's speech from being re-processed as input
- **Noise Suppression**: Disabled (`noiseSuppression: false`) to preserve environmental sounds (bird calls, frog vocalizations) for bioacoustic analysis
- **Auto Gain Control**: Enabled for consistent microphone levels
- **Mic Toggle**: Mute/unmute without re-requesting browser permissions; visual state indicator on the FAB button

## Live Survey Dashboard

The ecology dashboard updates in real time as the agent identifies species:

1. Agent calls `record_observation()` with trophic_level, taxonomic_group, and conservation_status
2. Species data is pushed to `_ecology_events` (in-memory queue)
3. `ecology_notification_task` drains the queue and sends `ecology_update` WebSocket messages
4. Frontend updates: species count, observation count, Shannon H', D3.js food web, species accumulation curve

**Test endpoint**: `GET /api/test-dashboard` injects sample species, metrics, and relationships to verify the dashboard without running a real survey.

**Session cleanup**: `clear_session_events()` is called on each new WebSocket connection to prevent stale data from previous surveys.

## Architecture

See `docs/architecture-system.md` for the full system architecture diagram, `docs/architecture-components.md` for the detailed component view, and `docs/architecture-interaction.md` for the demo sequence flow.

A polished HTML version is available at `docs/architecture-diagram.html`.

## License

Apache-2.0
