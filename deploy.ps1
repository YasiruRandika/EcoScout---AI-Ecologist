# EcoScout — One-command deployment to Google Cloud (PowerShell)
# Requires: gcloud CLI
#
# Usage:
#   .\deploy.ps1 -ProjectId MY_PROJECT
#   .\deploy.ps1 -ProjectId MY_PROJECT -Token "your_secret_token"
#   .\deploy.ps1 -ProjectId MY_PROJECT -Bucket "my-bucket-name"
#   .\deploy.ps1 -ProjectId MY_PROJECT -NoAuth
#   .\deploy.ps1 -ProjectId MY_PROJECT -SkipCors   # Skip CORS (avoids Windows gcloud error)
#
# Examples:
#   .\deploy.ps1 -ProjectId my-gcp-project
#   .\deploy.ps1 -ProjectId my-gcp-project -Token ((1..24 | ForEach { [char](Get-Random -Min 97 -Max 123) }) -join "")

param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [string]$Token = "",
    [string]$Bucket = "",
    [string]$Region = "us-central1",
    [switch]$NoAuth,
    [switch]$SkipCors
)

$ErrorActionPreference = "Stop"
$ServiceName = "ecoscout"
$SecretName = "ecoscout-access-token"
$UseAuth = -not $NoAuth

if ([string]::IsNullOrEmpty($Bucket)) {
    $Bucket = "ecoscout-media-$ProjectId"
}

Write-Host "=============================================="
Write-Host "EcoScout Deployment"
Write-Host "=============================================="
Write-Host "Project:  $ProjectId"
Write-Host "Region:   $Region"
Write-Host "Bucket:   $Bucket"
Write-Host "Auth:     $UseAuth"
Write-Host "=============================================="

# Check gcloud
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    Write-Error "gcloud CLI not found. Install from https://cloud.google.com/sdk/docs/install"
}

# 1. Set project
Write-Host "[1/8] Setting project..."
gcloud config set project $ProjectId

# 2. Enable APIs
Write-Host "[2/8] Enabling required APIs..."
gcloud services enable `
    run.googleapis.com `
    cloudbuild.googleapis.com `
    containerregistry.googleapis.com `
    firestore.googleapis.com `
    storage.googleapis.com `
    aiplatform.googleapis.com `
    secretmanager.googleapis.com `
    --quiet

# 3. Firestore
Write-Host "[3/8] Ensuring Firestore database..."
$dbList = gcloud firestore databases list --format="value(name)" 2>$null
if ([string]::IsNullOrEmpty($dbList)) {
    Write-Host "  Creating Firestore database (Native mode)..."
    gcloud firestore databases create --location=nam5 --type=firestore-native 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Host "  Firestore may already exist. Continuing..." }
} else {
    Write-Host "  Firestore database exists."
}

# 4. GCS bucket (use gcloud storage - create idempotently)
Write-Host "[4/8] Ensuring GCS bucket..."
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$null = gcloud storage buckets describe "gs://$Bucket" --project=$ProjectId 2>$null
$describeSucceeded = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevErrorAction
if (-not $describeSucceeded) {
    Write-Host "  Creating bucket gs://$Bucket..."
    gcloud storage buckets create "gs://$Bucket" --location=$Region --project=$ProjectId
} else {
    Write-Host "  Bucket gs://$Bucket exists."
}
# Apply CORS for video playback from signed URLs (Cloud Run app origin)
# Use -SkipCors if you get NativeCommandError on Windows. Run manually: gsutil cors set gcs-cors.json gs://BUCKET
$corsFile = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "gcs-cors.json"
if (-not $SkipCors -and (Test-Path $corsFile)) {
    Write-Host "  Applying CORS config for video playback..."
    $corsOk = $false
    try {
        $proc = Start-Process -FilePath "gcloud" -ArgumentList "storage","buckets","update","gs://$Bucket","--cors-file=$corsFile","--project=$ProjectId" -NoNewWindow -Wait -PassThru -RedirectStandardError "$env:TEMP\ecoscout-cors-err.txt" -RedirectStandardOutput "$env:TEMP\ecoscout-cors-out.txt"
        $corsOk = ($proc.ExitCode -eq 0)
    } catch { }
    if (-not $corsOk) {
        Write-Host "  CORS update skipped (non-critical). If videos don't play, run: gsutil cors set $corsFile gs://$Bucket"
    }
}

# 5. Secret Manager
$AccessToken = ""
if ($UseAuth) {
    Write-Host "[5/8] Configuring Secret Manager..."
    if ([string]::IsNullOrEmpty($Token)) {
        # Generate 48-char hex token (2 GUIDs without dashes)
        $AccessToken = [Guid]::NewGuid().ToString("n") + [Guid]::NewGuid().ToString("n")
        Write-Host "  Generated new access token: $AccessToken"
    } else {
        $AccessToken = $Token
    }

    $tmpFile = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($tmpFile, $AccessToken)
        $prevErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        $null = gcloud secrets describe $SecretName --project=$ProjectId 2>$null
        $secretExists = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $prevErrorAction
        if (-not $secretExists) {
            Write-Host "  Creating secret $SecretName..."
            gcloud secrets create $SecretName --data-file=$tmpFile --replication-policy=automatic --project=$ProjectId
        } else {
            Write-Host "  Adding new secret version..."
            gcloud secrets versions add $SecretName --data-file=$tmpFile --project=$ProjectId
        }
    } finally {
        Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
    }

    $projectNumber = gcloud projects describe $ProjectId --format="value(projectNumber)"
    $saEmail = "$projectNumber-compute@developer.gserviceaccount.com"
    Write-Host "  Granting secretAccessor to $saEmail..."
    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $null = gcloud secrets add-iam-policy-binding $SecretName `
        --member="serviceAccount:$saEmail" `
        --role="roles/secretmanager.secretAccessor" `
        --project=$ProjectId `
        --quiet 2>&1
    $ErrorActionPreference = $prevErrorAction
} else {
    Write-Host "[5/8] Skipping Secret Manager (-NoAuth)"
}

# 5a. Grant IAM roles to Cloud Run service account (Vertex AI, Firestore, Storage)
Write-Host "[5a] Granting API access to Cloud Run service account..."
$projectNumber = gcloud projects describe $ProjectId --format="value(projectNumber)"
$saEmail = "$projectNumber-compute@developer.gserviceaccount.com"
foreach ($role in @("roles/aiplatform.user", "roles/datastore.user")) {
    $prevEA = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    gcloud projects add-iam-policy-binding $ProjectId `
        --member="serviceAccount:$saEmail" `
        --role=$role `
        --quiet 2>&1 | Out-Null
    $ErrorActionPreference = $prevEA
}
$prevEA = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
gcloud storage buckets add-iam-policy-binding "gs://$Bucket" `
    --member="serviceAccount:$saEmail" `
    --role="roles/storage.objectAdmin" `
    --quiet 2>&1 | Out-Null
$ErrorActionPreference = $prevEA

# 5b. Cloud Build staging bucket (required for gcloud builds submit - uses US multi-region by default)
$cloudBuildBucket = "${ProjectId}_cloudbuild"
Write-Host "[5b] Ensuring Cloud Build staging bucket..."
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$null = gcloud storage buckets describe "gs://$cloudBuildBucket" --project=$ProjectId 2>$null
$cbBucketExists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevErrorAction
if (-not $cbBucketExists) {
    Write-Host "  Creating Cloud Build bucket gs://$cloudBuildBucket (location=US)..."
    gcloud storage buckets create "gs://$cloudBuildBucket" --location=US --project=$ProjectId
}

# 6. Build and deploy
Write-Host "[6/8] Building and deploying to Cloud Run..."
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $scriptDir
try {
    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    gcloud builds submit --config=cloudbuild.yaml . `
        --substitutions="_BUCKET_NAME=$Bucket" `
        --gcs-source-staging-dir="gs://$cloudBuildBucket/source" `
        --project=$ProjectId
    $buildSucceeded = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevErrorAction
} finally {
    Pop-Location
}
if (-not $buildSucceeded) {
    Write-Host ""
    Write-Host "ERROR: Cloud Build failed. Fix the errors above and try again." -ForegroundColor Red
    exit 1
}

# 7. Update Cloud Run
Write-Host "[7/8] Updating Cloud Run service..."
$updateArgs = @(
    "run", "services", "update", $ServiceName,
    "--region", $Region,
    "--project", $ProjectId,
    "--set-env-vars", "GOOGLE_CLOUD_PROJECT=$ProjectId,GOOGLE_GENAI_USE_VERTEXAI=True,GOOGLE_CLOUD_LOCATION=us-central1,GCS_BUCKET_NAME=$Bucket,ECOSCOUT_VOICE=Orus"
)
if ($UseAuth) {
    $updateArgs += "--set-secrets=ECOSCOUT_ACCESS_TOKEN=${SecretName}:latest"
}
gcloud @updateArgs

# 7b. Ensure public access (Cloud Run invoker for allUsers - app token auth controls who can use it)
Write-Host "[7b] Ensuring public access to Cloud Run..."
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$null = gcloud run services add-iam-policy-binding $ServiceName `
    --region=$Region --project=$ProjectId `
    --member="allUsers" `
    --role="roles/run.invoker" 2>&1
$ErrorActionPreference = $prevErrorAction

# 8. Output
Write-Host "[8/8] Getting service URL..."
$serviceUrl = gcloud run services describe $ServiceName --region $Region --project $ProjectId --format="value(status.url)"

Write-Host ""
Write-Host "=============================================="
Write-Host "Deployment complete!"
Write-Host "=============================================="
Write-Host "App URL:  $serviceUrl"
if ($UseAuth -and -not [string]::IsNullOrEmpty($AccessToken)) {
    Write-Host ""
    Write-Host "Share this link with judges (includes access token):"
    Write-Host "  ${serviceUrl}/?token=$AccessToken"
    Write-Host ""
    Write-Host "Save your token - you need it to access the app."
}
Write-Host "=============================================="
