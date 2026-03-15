# EcoScout Access Token

When `ECOSCOUT_ACCESS_TOKEN` is set, only users with the correct token can use the app. This limits access and controls costs.

## Setup

1. Generate a token:
   ```bash
   openssl rand -hex 24
   ```

2. Store in Secret Manager (recommended for Cloud Run):
   ```bash
   echo -n "YOUR_TOKEN_HERE" | gcloud secrets create ecoscout-access-token --data-file=-
   # Or add a new version to existing secret:
   echo -n "NEW_TOKEN_HERE" | gcloud secrets versions add ecoscout-access-token --data-file=-
   ```

3. Grant Cloud Run access:
   ```bash
   gcloud secrets add-iam-policy-binding ecoscout-access-token \
     --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
     --role="roles/secretmanager.secretAccessor"
   ```

4. Deploy with the secret:
   ```bash
   gcloud run services update ecoscout --region us-central1 \
     --set-secrets="ECOSCOUT_ACCESS_TOKEN=ecoscout-access-token:latest"
   ```

## Rotating the Token

To update the token (e.g. after sharing with too many people):

1. Add a new secret version:
   ```bash
   echo -n "NEW_TOKEN_HERE" | gcloud secrets versions add ecoscout-access-token --data-file=-
   ```

2. Force Cloud Run to pick it up (new revision):
   ```bash
   gcloud run services update ecoscout --region us-central1 \
     --update-secrets="ECOSCOUT_ACCESS_TOKEN=ecoscout-access-token:latest"
   ```

3. Share the new token with judges. Existing users with the old cookie will get the access form on their next request; they enter the new code.

## User Flow

- **First visit**: User sees "Enter access code" form. They enter the token, which is stored in a secure cookie.
- **Shared link**: Share `https://your-app.run.app/?token=YOUR_TOKEN` - the token is set as a cookie and they're redirected to the app.
- **Token rotated**: User visits `/auth/clear` or gets redirected there on 401, then enters the new code.

## Local Development

Leave `ECOSCOUT_ACCESS_TOKEN` unset in `.env` for no authentication during local dev.
