# EcoScout - Environment Variables & IAM

## Environment Variables (Cloud Run)

| Variable | Required | Description | Set by |
|----------|----------|-------------|--------|
| `GOOGLE_CLOUD_PROJECT` | Yes | GCP project ID | deploy script |
| `GOOGLE_GENAI_USE_VERTEXAI` | Yes | Must be `True` for production (uses Vertex AI, not API key) | deploy script |
| `GCS_BUCKET_NAME` | Yes | Bucket for field guide images, videos | deploy script |
| `ECOSCOUT_ACCESS_TOKEN` | Optional | Secret for app access control (from Secret Manager) | deploy script |
| `ECOSCOUT_VOICE` | Optional | Voice for audio output (default: Orus) | deploy script |
| `GOOGLE_MAPS_API_KEY` | Optional | Reverse geocoding (falls back to Nominatim if unset) | manual |

**Note**: With `GOOGLE_GENAI_USE_VERTEXAI=True`, the app uses the Cloud Run service account - no API key needed.

## IAM Roles (Cloud Run Service Account)

The default Compute Engine service account (`PROJECT_NUMBER-compute@developer.gserviceaccount.com`) needs:

| Role | Purpose |
|------|---------|
| `roles/aiplatform.user` | Vertex AI - Gemini Live, Gemini 3 Pro Image, Veo |
| `roles/datastore.user` | Firestore - sessions, observations, video metadata |
| `roles/storage.objectAdmin` | GCS bucket - upload images, videos |
| `roles/secretmanager.secretAccessor` | Secret Manager - read ECOSCOUT_ACCESS_TOKEN |

The deploy script grants these automatically. If APIs still fail, run manually:

```powershell
$PROJECT_ID = "ecoscout-vertexai-2026"
$BUCKET = "ecoscout-media-$PROJECT_ID"
$PROJECT_NUM = gcloud projects describe $PROJECT_ID --format="value(projectNumber)"
$SA = "$PROJECT_NUM-compute@developer.gserviceaccount.com"

# Vertex AI
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA" --role="roles/aiplatform.user"

# Firestore
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA" --role="roles/datastore.user"

# GCS bucket
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$SA" --role="roles/storage.objectAdmin"
```

## Optional: Google Maps for Geocoding

For better location names (instead of Nominatim), add your API key:

```powershell
gcloud run services update ecoscout --region us-central1 --project ecoscout-vertexai-2026 `
  --update-env-vars="GOOGLE_MAPS_API_KEY=YOUR_MAPS_API_KEY"
```

## Verify Setup

1. **Cloud Run logs**: Check for Vertex AI or permission errors.
2. **IAM**: In GCP Console → IAM, find the compute service account and confirm the roles above.
3. **APIs**: Ensure these are enabled: `run`, `aiplatform`, `firestore`, `storage`, `secretmanager`.

## Orange Dot (Reconnecting) Loop

If the status dot is **green briefly then turns orange and stays orange**, the WebSocket connects but the agent fails when calling Vertex AI. Common causes:

1. **Missing `roles/aiplatform.user`** on the Cloud Run service account
2. **Vertex AI API** not enabled or wrong region
3. **Model not available** in your region (gemini-2.5-flash-native-audio uses specific regions)

**Fix – grant IAM roles:**

```powershell
$PROJECT_ID = "ecoscout-vertexai-2026"
$BUCKET = "ecoscout-media-ecoscout-vertexai-2026"
$PROJECT_NUM = gcloud projects describe $PROJECT_ID --format="value(projectNumber)"
$SA = "$PROJECT_NUM-compute@developer.gserviceaccount.com"

gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA" --role="roles/aiplatform.user"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$SA" --role="roles/datastore.user"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$SA" --role="roles/storage.objectAdmin"
```

**Check Cloud Run logs** for the actual error:

```powershell
gcloud run services logs read ecoscout --region us-central1 --project ecoscout-vertexai-2026 --limit 50
```
