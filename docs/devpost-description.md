# EcoScout - AI Ecologist

**Gemini can identify a bird. EcoScout does ecology.**

## Inspiration

Identification apps tell you what something is. Ecologists ask what it means. We built EcoScout to bridge that gap: a live agent that does the work of a field ecologist - listening to soundscapes, cross-referencing global biodiversity databases, computing diversity metrics, and producing scientific reasoning that no standalone chatbot can deliver. The goal was not another nature Q&A tool, but an AI that performs real ecological analysis in real time.

## What it does

EcoScout is an AI Ecologist powered by the Gemini Live API. It does what the Gemini app cannot:

- **Listens to ambient soundscapes** - Continuously analyzes the audio stream for bird calls, frog vocalizations, and other bioacoustic signals. Identifies species from background sounds without being asked, turning passive listening into active ecological monitoring.
- **Generates scientific hypotheses and gap analysis** - Compares observed species against expected species from iNaturalist data for the region. Performs gap analysis: what should be here but isn't? What's unexpected? Produces testable ecological hypotheses in real time.
- **Cross-references every identification with iNaturalist** - Validates against 100M+ observations for conservation status, regional distribution, phenology, and taxonomic authority. No hallucinated species; every claim is grounded in the world's largest citizen science database.
- **Computes real-time biodiversity metrics** - Shannon-Wiener diversity index, Simpson's diversity index, species richness. Updates live as new species are detected, turning a walk in the woods into a quantitative ecological survey.
- **Builds a living ecological visualization** - D3.js food web showing trophic relationships between observed species; species accumulation curve showing how diversity builds over the session. A dynamic, data-driven view of the ecosystem unfolding in real time.
- **Produces professional ecological survey reports** - Structured reports suitable for land managers, researchers, or citizen science submissions. Habitat description, species list with conservation status, diversity metrics, and methodological notes.

## How we built it

**Backend**: Python 3.11 / FastAPI / Google ADK with `LlmAgent` and `run_live()` bidirectional streaming. Custom `FunctionTool` implementations for ambient sound analysis, iNaturalist lookups, biodiversity calculations, visualization generation, and report production.

**Live Streaming**: Single WebSocket connection carrying binary PCM audio (16kHz) and JSON frames. The agent processes the continuous audio stream for bioacoustic detection, triggering species identification and database lookups without user prompts. Browser echo cancellation prevents the agent's own speech from being re-processed as input.

**Live Survey Dashboard**: A real-time ecology dashboard that updates as the agent works. When the agent calls `record_observation()`, species data flows through an `_ecology_events` in-memory queue, drained by an `ecology_notification_task` that pushes `ecology_update` WebSocket messages to the frontend. The dashboard shows live species count, observation count, Shannon H', and conservation alerts. A D3.js food web builds trophic relationships between observed species, and a species accumulation curve tracks survey completeness against the expected species asymptote from iNaturalist.

**Biodiversity Engine**: Real-time computation of Shannon-Wiener index, Simpson's diversity, and species richness. Species accumulation curve tracking. All metrics update incrementally as new observations enter the session.

**iNaturalist Integration**: pyinaturalist for taxon search, observation lookup, and conservation status. Every identification is validated against regional observation density and taxonomic authority before being added to the survey.

**Weather Context**: Open-Meteo API (free, no key required) for temperature, precipitation, and conditions - ecological context that affects species presence and behavior.

**Visualization**: D3.js for interactive food web construction with trophic-level color coding, species accumulation curves with expected species asymptote, and a real-time survey statistics dashboard. All visualizations update live via WebSocket events.

**Audio Pipeline**: AudioWorklet-based PCM recording at 16kHz with browser echo cancellation (`echoCancellation: true`) to prevent feedback loops. Noise suppression is deliberately disabled (`noiseSuppression: false`) to preserve environmental sounds (bird calls, frog vocalizations) for the agent to analyze. A mic mute/unmute toggle lets users control when audio is sent without re-requesting microphone permissions.

**Video Generation**: Veo 3.1 for nature process videos with context-rich prompts derived from session observations. Videos are stored in Google Cloud Storage (`ecoscout-media-ecoscout-vertexai-2026` bucket) when GCS output is configured, or returned as inline video bytes from Vertex AI when `VEO_OUTPUT_GCS` is not set.

**Infrastructure**: Google Cloud Run with WebSocket session affinity, Cloud Firestore for survey state (with graceful in-memory fallback), Google Cloud Storage for media assets, and automated deployment via `cloudbuild.yaml`, `deploy.sh`, and `deploy.ps1`.

## Challenges we ran into

- **Ambient sound vs. direct questions**: Training the agent to treat the audio stream as continuous ecological input rather than waiting for explicit queries. Required careful system prompt design so it proactively identifies species from background sounds.
- **Audio feedback loop**: The agent's spoken output was re-captured by the microphone and re-processed as input. We solved this with browser echo cancellation (`echoCancellation: true`) while keeping noise suppression disabled to preserve environmental sounds - a careful balance between preventing feedback and preserving ecological audio data.
- **iNaturalist rate limits and regional filtering**: Balancing API calls with session flow. Implemented caching and batched lookups; regional filters (lat/lon) ensure relevance to the survey location.
- **Biodiversity metrics on streaming data**: Shannon-Wiener and Simpson's indices assume a closed community. We use incremental updates with session-scoped species lists, clearly documenting the methodology in reports.
- **Food web construction from partial observations**: D3.js food web requires trophic relationships. We derive relationships from taxonomic knowledge and iNaturalist observation co-occurrence when direct diet data is unavailable.
- **Vertex AI video bytes handling**: Veo 3.1 on Vertex AI returns inline video bytes rather than file references. We built a multi-path download handler that checks for inline bytes first, then GCS URIs, then falls back to the Gemini Developer client API - making the same code work across both API surfaces.
- **Real-time dashboard synchronization**: Bridging the gap between agent tool execution (backend) and frontend dashboard updates required an in-memory event queue (`_ecology_events`) with an async polling task that drains events and pushes them as WebSocket messages. Session boundaries required explicit `clear_session_events()` to prevent stale data.

## Accomplishments that we're proud of

- **External database integration**: The Gemini app cannot call iNaturalist. EcoScout validates every identification against 100M+ observations. This is impossible in a closed chatbot.
- **Quantitative ecological analysis**: Shannon-Wiener, Simpson's diversity, species richness - real biodiversity metrics computed in real time. No other live agent does this.
- **Ambient sound species detection**: The agent identifies birds and frogs from background audio without being asked. Proactive bioacoustic monitoring, not reactive Q&A.
- **Live survey dashboard**: Real-time species count, biodiversity metrics, D3.js food web, and species accumulation curve - all updating live as the agent identifies species. Judges can watch the ecosystem model assemble itself.
- **Echo cancellation without losing environmental audio**: Most voice apps suppress background noise. We enabled echo cancellation while disabling noise suppression - the agent hears bird calls clearly without hearing its own voice.
- **Scientific reasoning**: Gap analysis, hypothesis generation, and professional reports. EcoScout thinks like an ecologist, not just a field guide.

## What we learned

- The Live API's continuous audio stream is underused. Most demos treat it as voice input; we treat it as ecological sensor data. The same stream that carries speech carries bird calls.
- pyinaturalist + Open-Meteo provide rich ecological context with minimal setup. Free, well-documented APIs that transform an agent from "smart" to "scientifically grounded."
- Biodiversity metrics are computable in real time. The math is simple; the value is in connecting it to a live observation stream.
- D3.js food webs and species accumulation curves make abstract ecology tangible. Users see the ecosystem structure emerge as they walk.

## What's next for EcoScout

- **Bioacoustic model fine-tuning**: Train on regional bird/frog call libraries for higher accuracy in ambient sound identification.
- **Export to iNaturalist**: One-click submission of survey data as iNaturalist observations.
- **Multi-session temporal analysis**: Track biodiversity changes across repeated visits to the same site.
- **Integration with eBird and other regional databases**: Expand beyond iNaturalist for ornithology-specific workflows.

## Built With

- Google ADK (Agent Development Kit)
- Gemini Live API (`gemini-live-2.5-flash-native-audio`, bidirectional streaming)
- Gemini 3 Pro Image (`gemini-3-pro-image-preview`, interleaved text + illustration)
- Veo 3.1 (`veo-3.1-generate-preview`, nature video generation + extension)
- Google Search Grounding (species verification)
- pyinaturalist (iNaturalist API - 100M+ observations)
- Open-Meteo API (ecological weather context, no key required)
- Biodiversity calculation engine (Shannon-Wiener, Simpson, species richness, evenness)
- D3.js (interactive food web, species accumulation curve, live dashboard)
- Google Cloud Run (WebSocket session affinity)
- Google Cloud Firestore (survey state, graceful in-memory fallback)
- Google Cloud Storage (field guide images, generated videos)
- Python 3.11+ / FastAPI / Uvicorn
- Vanilla JavaScript / Web Audio API / AudioWorklets
- Browser Echo Cancellation (WebRTC AEC)

---

**Project**: Gemini Live Agent Challenge 2026 | **Category**: Live Agents
