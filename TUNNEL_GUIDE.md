# EcoScout - Mobile Testing via HTTPS Tunnel

Camera and microphone require HTTPS (secure context) on non-localhost devices.
Use a tunnel to get a public HTTPS URL for testing on your phone.

## 1. Start the server

```bash
cd ecoscout/app
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

- PC access: **http://localhost:8080** (camera/mic work on localhost)

## 2. Start the tunnel (in a separate terminal)

### Recommended: Cloudflare Tunnel (no signup, no password, stable)

```bash
# Install (one-time)
winget install Cloudflare.cloudflared --source winget

# Start tunnel
cloudflared tunnel --url http://localhost:8080
```

Output will show something like:

```
Your quick Tunnel has been created! Visit it at:
https://some-random-words.trycloudflare.com
```

Copy that HTTPS URL - open it directly on your phone. No password needed.

### Alternative: localtunnel (no install, less stable)

```bash
npx localtunnel --port 8080
```

Localtunnel asks for a password - enter your public IP:

```bash
# PowerShell
(Invoke-WebRequest -Uri "https://api.ipify.org" -UseBasicParsing).Content

# Or open https://api.ipify.org in any browser
```

### Alternative: ngrok (most stable, requires free signup)

```bash
# Install (one-time)
winget install Ngrok.Ngrok --source winget

# Sign up at https://dashboard.ngrok.com/signup
# Copy your authtoken from https://dashboard.ngrok.com/get-started/your-authtoken
ngrok config add-authtoken YOUR_TOKEN_HERE

# Start tunnel
ngrok http 8080
```

## 3. Open on your phone

1. Open the `https://...trycloudflare.com` URL in your phone browser
2. Grant camera and microphone permissions when prompted
3. Tap **Voice** and **Camera** to start

## Quick reference

| What             | URL                                          |
|------------------|----------------------------------------------|
| PC (local)       | http://localhost:8080                         |
| Mobile (tunnel)  | https://\<random\>.trycloudflare.com (from step 2) |
