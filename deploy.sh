#!/usr/bin/env bash
# EcoScout — One-command deployment to Google Cloud
# Requires: gcloud CLI, optional openssl for token generation
#
# Usage:
#   ./deploy.sh PROJECT_ID
#   ./deploy.sh PROJECT_ID --token YOUR_SECRET_TOKEN
#   ./deploy.sh PROJECT_ID --bucket my-bucket-name
#   ./deploy.sh PROJECT_ID --no-auth     # Deploy without access control (not recommended for production)
#
# Examples:
#   ./deploy.sh my-gcp-project
#   ./deploy.sh my-gcp-project --token $(openssl rand -hex 24)

set -e

REGION="${REGION:-us-central1}"
SERVICE_NAME="ecoscout"
SECRET_NAME="ecoscout-access-token"
USE_AUTH=true
BUCKET_NAME=""

# Parse arguments
PROJECT_ID=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --token)
      ACCESS_TOKEN="$2"
      shift 2
      ;;
    --bucket)
      BUCKET_NAME="$2"
      shift 2
      ;;
    --region)
      REGION="$2"
      shift 2
      ;;
    --no-auth)
      USE_AUTH=false
      shift
      ;;
    -*)
      echo "Unknown option: $1"
      echo "Usage: $0 PROJECT_ID [--token TOKEN] [--bucket BUCKET] [--region REGION] [--no-auth]"
      exit 1
      ;;
    *)
      PROJECT_ID="$1"
      shift
      ;;
  esac
done

if [[ -z "$PROJECT_ID" ]]; then
  echo "Error: PROJECT_ID is required"
  echo "Usage: $0 PROJECT_ID [--token TOKEN] [--bucket BUCKET] [--region REGION] [--no-auth]"
  exit 1
fi

# Default bucket name (globally unique)
if [[ -z "$BUCKET_NAME" ]]; then
  BUCKET_NAME="ecoscout-media-${PROJECT_ID}"
fi

echo "=============================================="
echo "EcoScout Deployment"
echo "=============================================="
echo "Project:  $PROJECT_ID"
echo "Region:   $REGION"
echo "Bucket:   $BUCKET_NAME"
echo "Auth:     $USE_AUTH"
echo "=============================================="

# Check gcloud
if ! command -v gcloud &> /dev/null; then
  echo "Error: gcloud CLI not found. Install from https://cloud.google.com/sdk/docs/install"
  exit 1
fi

# Set project
echo "[1/8] Setting project..."
gcloud config set project "$PROJECT_ID"

# Enable APIs
echo "[2/8] Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  containerregistry.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  aiplatform.googleapis.com \
  secretmanager.googleapis.com \
  --quiet

# Firestore (create if not exists)
echo "[3/8] Ensuring Firestore database..."
DB_LIST=$(gcloud firestore databases list --format="value(name)" 2>/dev/null || true)
if [[ -z "$DB_LIST" ]]; then
  echo "  Creating Firestore database (Native mode)..."
  gcloud firestore databases create --location=nam5 --type=firestore-native 2>/dev/null || {
    echo "  Firestore may already exist or creation failed. Continuing..."
  }
else
  echo "  Firestore database exists."
fi

# Cloud Build staging bucket (required for gcloud builds submit)
echo "[4a/9] Ensuring Cloud Build staging bucket..."
CLOUD_BUILD_BUCKET="${PROJECT_ID}_cloudbuild"
if ! gcloud storage buckets describe "gs://${CLOUD_BUILD_BUCKET}" --project="${PROJECT_ID}" &>/dev/null; then
  echo "  Creating Cloud Build bucket gs://${CLOUD_BUILD_BUCKET} (location=US)..."
  gcloud storage buckets create "gs://${CLOUD_BUILD_BUCKET}" --location=US --project="${PROJECT_ID}"
else
  echo "  Cloud Build bucket exists."
fi

# GCS bucket (use gcloud storage instead of gsutil for better compatibility)
echo "[4/9] Ensuring GCS bucket..."
if ! gcloud storage buckets describe "gs://${BUCKET_NAME}" --project="${PROJECT_ID}" &>/dev/null; then
  echo "  Creating bucket gs://${BUCKET_NAME}..."
  gcloud storage buckets create "gs://${BUCKET_NAME}" --location="${REGION}" --project="${PROJECT_ID}"
else
  echo "  Bucket gs://${BUCKET_NAME} exists."
fi
# Apply CORS for video playback from signed URLs
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORS_FILE="${SCRIPT_DIR}/gcs-cors.json"
if [[ -f "$CORS_FILE" ]]; then
  echo "  Applying CORS config for video playback..."
  gcloud storage buckets update "gs://${BUCKET_NAME}" --cors-file="$CORS_FILE" --project="${PROJECT_ID}" 2>/dev/null || true
fi

# Secret Manager + token
if [[ "$USE_AUTH" == "true" ]]; then
  echo "[5/8] Configuring Secret Manager..."
  if [[ -z "$ACCESS_TOKEN" ]]; then
    if command -v openssl &> /dev/null; then
      ACCESS_TOKEN=$(openssl rand -hex 24)
      echo "  Generated new access token: $ACCESS_TOKEN"
    else
      echo "Error: No token provided and openssl not found. Use --token YOUR_TOKEN or install openssl."
      exit 1
    fi
  fi

  # Create secret if not exists
  if ! gcloud secrets describe "$SECRET_NAME" --project="$PROJECT_ID" &>/dev/null; then
    echo "  Creating secret $SECRET_NAME..."
    echo -n "$ACCESS_TOKEN" | gcloud secrets create "$SECRET_NAME" --data-file=- --replication-policy=automatic
  else
    echo "  Adding new secret version..."
    echo -n "$ACCESS_TOKEN" | gcloud secrets versions add "$SECRET_NAME" --data-file=-
  fi

  # Grant Cloud Run service account access to secret
  PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
  SA_EMAIL="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
  echo "  Granting secretAccessor to $SA_EMAIL..."
  gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$PROJECT_ID" \
    --quiet 2>/dev/null || true
else
  echo "[5/8] Skipping Secret Manager (--no-auth)"
fi

# 5a. Grant IAM roles to Cloud Run service account (Vertex AI, Firestore, Storage)
echo "[5a] Granting API access to Cloud Run service account..."
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
SA_EMAIL="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
for role in roles/aiplatform.user roles/datastore.user; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$role" \
    --quiet 2>/dev/null || true
done
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/storage.objectAdmin" \
  --quiet 2>/dev/null || true

# Build and deploy
echo "[6/8] Building and deploying to Cloud Run..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

gcloud builds submit --config=cloudbuild.yaml . \
  --substitutions="_BUCKET_NAME=${BUCKET_NAME}" \
  --gcs-source-staging-dir="gs://${CLOUD_BUILD_BUCKET}/source" \
  --project="$PROJECT_ID" || { echo "ERROR: Cloud Build failed."; exit 1; }

# Update Cloud Run with secret (and bucket if different from default)
echo "[7/8] Updating Cloud Run service..."
UPDATE_ARGS=(
  run services update "$SERVICE_NAME"
  --region "$REGION"
  --project "$PROJECT_ID"
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_GENAI_USE_VERTEXAI=True,GOOGLE_CLOUD_LOCATION=us-central1,GCS_BUCKET_NAME=${BUCKET_NAME},ECOSCOUT_VOICE=Orus"
)

if [[ "$USE_AUTH" == "true" ]]; then
  UPDATE_ARGS+=(--set-secrets="ECOSCOUT_ACCESS_TOKEN=${SECRET_NAME}:latest")
fi

gcloud "${UPDATE_ARGS[@]}"

# Output
echo "[8/8] Getting service URL..."
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --project "$PROJECT_ID" --format="value(status.url)")

echo ""
echo "=============================================="
echo "Deployment complete!"
echo "=============================================="
echo "App URL:  $SERVICE_URL"
if [[ "$USE_AUTH" == "true" && -n "$ACCESS_TOKEN" ]]; then
  echo ""
  echo "Share this link with judges (includes access token):"
  echo "  ${SERVICE_URL}/?token=${ACCESS_TOKEN}"
  echo ""
  echo "Save your token - you need it to access the app."
fi
echo "=============================================="
