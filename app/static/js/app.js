/**
 * EcoScout — Live Environmental Intelligence Companion
 * Mobile-first client with camera-hero layout, audio visualization,
 * reverse geocoding, and rich content rendering.
 */

import { startAudioPlayerWorklet } from "./audio-player.js";
import { startAudioRecorderWorklet } from "./audio-recorder.js";
import { handleEcologyUpdate, resetVisualization } from "./ecology-viz.js";

// ── Constants ──────────────────────────────────────────────────────────────

const userId = "ecoscout-user";
let sessionId = "session-" + Math.random().toString(36).substring(2, 9);
const CAMERA_FPS = 1;
const CAMERA_QUALITY = 0.7;
const CAMERA_WIDTH = 640;
const CAMERA_HEIGHT = 480;
const GPS_GEOCODE_THRESHOLD_M = 150;

// ── State ──────────────────────────────────────────────────────────────────

let websocket = null;
let isAudio = false;
let isCameraStreaming = false;
let cameraInterval = null;
let cameraStream = null;
let gpsWatchId = null;
let lastGeocodedLat = null;
let lastGeocodedLon = null;
let currentLocationName = "";

let audioPlayerNode = null;
let audioPlayerContext = null;
let audioRecorderNode = null;
let audioRecorderContext = null;
let micStream = null;

let currentMessageId = null;
let currentBubbleElement = null;
let currentInputTranscriptionId = null;
let currentInputTranscriptionElement = null;
let currentOutputTranscriptionId = null;
let currentOutputTranscriptionElement = null;
let turnUsedTranscription = false;

// Audio visualization state
let vizAnimFrame = null;
let micAnalyser = null;
let playerAnalyser = null;
let listeningPlaceholderElement = null;
let isAgentSpeaking = false;
let agentStoppedSpeakingAt = 0;
const ECHO_COOLDOWN_MS = 600;

// ── DOM Elements ───────────────────────────────────────────────────────────

const messageForm = document.getElementById("messageForm");
const messageInput = document.getElementById("message");
const messagesDiv = document.getElementById("messages");
const statusDot = document.getElementById("statusDot");
const startAudioButton = document.getElementById("startAudioButton");
const cameraToggle = document.getElementById("cameraToggle");
const cameraFeed = document.getElementById("cameraFeed");
const captureCanvas = document.getElementById("captureCanvas");
const cameraPlaceholder = document.getElementById("cameraPlaceholder");
const gpsBadge = document.getElementById("gpsBadge");
const gpsText = document.getElementById("gpsText");
const voiceFab = document.getElementById("voiceFab");
const audioVizCanvas = document.getElementById("audioViz");
const convoSheet = document.getElementById("convoSheet");

// ── WebSocket ──────────────────────────────────────────────────────────────

function getWebSocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  let url = `${protocol}//${window.location.host}/ws/${userId}/${sessionId}`;
  // Pass token in URL if present (helps when cookies aren't sent, e.g. some mobile browsers)
  const token = new URLSearchParams(window.location.search).get("token");
  if (token) {
    url += "?token=" + encodeURIComponent(token);
  }
  return url;
}

let reconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 3;

function connectWebsocket() {
  const wsUrl = getWebSocketUrl();
  websocket = new WebSocket(wsUrl);

  websocket.onopen = () => {
    reconnectAttempts = 0;
    updateConnectionStatus("connected");
    const sendBtn = document.getElementById("sendButton");
    sendBtn.disabled = false;
    sendBtn.title = "Send";
    removeConnectionBanner();
    // Replace "Connecting..." with connected state (remove last system message if it was ours)
    const sysMsgs = messagesDiv.querySelectorAll(".system-message");
    if (sysMsgs.length && sysMsgs[sysMsgs.length - 1].textContent.includes("Connecting")) {
      sysMsgs[sysMsgs.length - 1].textContent = "Connected. Ask about what you see!";
    }
    addSubmitHandler();
  };

  websocket.onmessage = (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch (err) {
      console.warn("WebSocket message not JSON:", event.data?.slice?.(0, 100));
      return;
    }
    if (data.type === "video_ready") {
      handleVideoReady(data);
    } else if (data.type === "video_failed") {
      handleVideoFailed(data);
    } else if (data.type === "video_started") {
      handleVideoStarted(data);
    } else if (data.type === "ecology_update") {
      handleEcologyUpdate(data);
    } else {
      handleIncomingEvent(data);
    }
  };

  websocket.onclose = (event) => {
    updateConnectionStatus("reconnecting");
    document.getElementById("sendButton").disabled = true;
    document.getElementById("sendButton").title = "Connecting...";
    const isAuthError = event.code === 4001 || event.code === 4003 || (event.code === 1006 && reconnectAttempts >= 2);
    if (isAuthError || reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      showConnectionBanner(
        "Could not connect. Open the app using the link with your access token (?token=...)."
      );
      reconnectAttempts = 0;
      setTimeout(connectWebsocket, 10000);
    } else {
      reconnectAttempts++;
      setTimeout(connectWebsocket, 3000);
    }
  };

  websocket.onerror = () => {
    updateConnectionStatus("disconnected");
  };
}

function showConnectionBanner(message) {
  removeConnectionBanner();
  const banner = document.createElement("div");
  banner.id = "connectionBanner";
  banner.className = "connection-banner";
  const span = document.createElement("span");
  span.textContent = message;
  const retryBtn = document.createElement("button");
  retryBtn.type = "button";
  retryBtn.textContent = "Retry";
  retryBtn.addEventListener("click", () => {
    removeConnectionBanner();
    connectWebsocket();
  });
  banner.appendChild(span);
  banner.appendChild(retryBtn);
  document.body.appendChild(banner);
}

function removeConnectionBanner() {
  const existing = document.getElementById("connectionBanner");
  if (existing) existing.remove();
}

// ── Handle incoming ADK events ─────────────────────────────────────────────

function handleIncomingEvent(adkEvent) {
  if (adkEvent.turnComplete === true) {
    finalizeBubble(currentBubbleElement);
    finalizeBubble(currentOutputTranscriptionElement);
    currentMessageId = null;
    currentBubbleElement = null;
    currentOutputTranscriptionId = null;
    currentOutputTranscriptionElement = null;
    turnUsedTranscription = false;
    if (listeningPlaceholderElement) {
      listeningPlaceholderElement.remove();
      listeningPlaceholderElement = null;
    }
    // Do NOT return — fall through to process inputTranscription etc. if in same event
  }

  if (adkEvent.interrupted === true) {
    if (audioPlayerNode) audioPlayerNode.port.postMessage({ command: "endOfAudio" });
    markInterrupted(currentBubbleElement);
    markInterrupted(currentOutputTranscriptionElement);
    currentMessageId = null;
    currentBubbleElement = null;
    currentOutputTranscriptionId = null;
    currentOutputTranscriptionElement = null;
    turnUsedTranscription = false;
    if (listeningPlaceholderElement) {
      listeningPlaceholderElement.remove();
      listeningPlaceholderElement = null;
    }
    return;
  }

  if (adkEvent.inputTranscription?.text) {
    handleTranscription(adkEvent.inputTranscription, true);
  }

  if (adkEvent.outputTranscription?.text) {
    turnUsedTranscription = true;
    handleTranscription(adkEvent.outputTranscription, false);
  }

  if (adkEvent.content?.parts) {
    finalizeInputTranscriptionIfNeeded();
    // Skip content.parts text when outputTranscription is active or was used in this turn.
    // Both can contain the same agent response — rendering both causes duplicate text.
    const useTranscriptionForText = adkEvent.outputTranscription != null || currentOutputTranscriptionId != null || turnUsedTranscription;
    for (const part of adkEvent.content.parts) {
      if (part.inlineData) {
        const { mimeType, data } = part.inlineData;
        if (mimeType?.startsWith("audio/pcm") && audioPlayerNode) {
          audioPlayerNode.port.postMessage(base64ToArray(data));
        }
      }
      if (part.text && !useTranscriptionForText) {
        const textContent = part.text;
        if (!currentMessageId) {
          currentMessageId = randomId();
          currentBubbleElement = createMessageBubble(textContent, false, true);
          currentBubbleElement.id = currentMessageId;
          messagesDiv.appendChild(currentBubbleElement);
        } else {
          const existing = currentBubbleElement.querySelector(".bubble-text").textContent;
          updateMessageBubble(currentBubbleElement, existing + textContent, true);
        }
        scrollToBottom();
      }
    }
  }
}

// ── Transcription handling ─────────────────────────────────────────────────

function handleTranscription(transcription, isInput) {
  const text = transcription.text;
  const isFinished = transcription.finished;

  if (isInput) {
    if (!currentInputTranscriptionId) {
      if (listeningPlaceholderElement) {
        listeningPlaceholderElement.remove();
        listeningPlaceholderElement = null;
      }
      currentInputTranscriptionId = randomId();
      currentInputTranscriptionElement = createMessageBubble(cleanCJKSpaces(text), true, !isFinished);
      currentInputTranscriptionElement.id = currentInputTranscriptionId;
      currentInputTranscriptionElement.classList.add("transcription");
      messagesDiv.appendChild(currentInputTranscriptionElement);
    } else if (currentInputTranscriptionElement) {
      const existing = currentInputTranscriptionElement.querySelector(".bubble-text").textContent;
      const merged = mergeTranscriptionText(existing, cleanCJKSpaces(text));
      updateMessageBubble(currentInputTranscriptionElement, merged, !isFinished);
    }
    if (isFinished) {
      currentInputTranscriptionId = null;
      currentInputTranscriptionElement = null;
    }
  } else {
    finalizeInputTranscriptionIfNeeded();
    if (!currentOutputTranscriptionId) {
      currentOutputTranscriptionId = randomId();
      currentOutputTranscriptionElement = createMessageBubble(text, false, !isFinished);
      currentOutputTranscriptionElement.id = currentOutputTranscriptionId;
      currentOutputTranscriptionElement.classList.add("transcription");
      messagesDiv.appendChild(currentOutputTranscriptionElement);
    } else {
      const existing = currentOutputTranscriptionElement.querySelector(".bubble-text").textContent;
      const merged = mergeTranscriptionText(existing, text);
      updateMessageBubble(currentOutputTranscriptionElement, merged, !isFinished);
    }
    if (isFinished) {
      currentOutputTranscriptionId = null;
      currentOutputTranscriptionElement = null;
    }
  }
  scrollToBottom();
}

function finalizeInputTranscriptionIfNeeded() {
  if (currentInputTranscriptionId && !currentOutputTranscriptionId && !currentMessageId) {
    finalizeBubble(currentInputTranscriptionElement);
    currentInputTranscriptionId = null;
    currentInputTranscriptionElement = null;
  }
}

// ── Camera streaming ───────────────────────────────────────────────────────

async function startCameraStream() {
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({
      video: {
        width: { ideal: CAMERA_WIDTH },
        height: { ideal: CAMERA_HEIGHT },
        facingMode: "environment",
      },
    });
    cameraFeed.srcObject = cameraStream;
    captureCanvas.width = CAMERA_WIDTH;
    captureCanvas.height = CAMERA_HEIGHT;

    isCameraStreaming = true;
    cameraPlaceholder.classList.add("hidden");
    cameraToggle.classList.add("active");

    cameraInterval = setInterval(captureAndSendFrame, 1000 / CAMERA_FPS);
  } catch (err) {
    addSystemMessage(`Camera unavailable: ${err.message}`);
  }
}

function stopCameraStream() {
  if (cameraInterval) {
    clearInterval(cameraInterval);
    cameraInterval = null;
  }
  if (cameraStream) {
    cameraStream.getTracks().forEach((t) => t.stop());
    cameraStream = null;
  }
  cameraFeed.srcObject = null;
  isCameraStreaming = false;
  cameraPlaceholder.classList.remove("hidden");
  cameraToggle.classList.remove("active");
}

function captureAndSendFrame() {
  if (!cameraStream || !websocket || websocket.readyState !== WebSocket.OPEN) return;
  const ctx = captureCanvas.getContext("2d");
  ctx.drawImage(cameraFeed, 0, 0, CAMERA_WIDTH, CAMERA_HEIGHT);
  captureCanvas.toBlob(
    (blob) => {
      if (!blob) return;
      const reader = new FileReader();
      reader.onloadend = () => {
        const base64 = reader.result.split(",")[1];
        websocket.send(JSON.stringify({ type: "image", data: base64, mimeType: "image/jpeg" }));
      };
      reader.readAsDataURL(blob);
    },
    "image/jpeg",
    CAMERA_QUALITY
  );
}

cameraToggle.addEventListener("click", () => {
  if (isCameraStreaming) stopCameraStream();
  else startCameraStream();
});

cameraPlaceholder.addEventListener("click", () => {
  if (!isCameraStreaming) startCameraStream();
});

// ── GPS tracking + Reverse Geocoding ────────────────────────────────────────

function handleGPSPosition(pos) {
  const { latitude, longitude } = pos.coords;

  if (gpsText.textContent === "Locating..." || gpsText.textContent === "GPS timeout") {
    gpsText.textContent = `${latitude.toFixed(4)}, ${longitude.toFixed(4)}`;
    gpsBadge.title = `${latitude.toFixed(6)}, ${longitude.toFixed(6)}`;
  }

  if (websocket?.readyState === WebSocket.OPEN) {
    const msg = { type: "gps", lat: latitude, lon: longitude };
    if (currentLocationName) msg.locationName = currentLocationName;
    websocket.send(JSON.stringify(msg));
  }

  if (shouldReverseGeocode(latitude, longitude)) {
    reverseGeocode(latitude, longitude).then((name) => {
      if (name) {
        currentLocationName = name;
        gpsText.textContent = name;
        gpsBadge.title = `${latitude.toFixed(6)}, ${longitude.toFixed(6)}`;
        lastGeocodedLat = latitude;
        lastGeocodedLon = longitude;
        if (websocket?.readyState === WebSocket.OPEN) {
          websocket.send(JSON.stringify({
            type: "gps",
            lat: latitude,
            lon: longitude,
            locationName: name,
          }));
        }
      }
    });
  }
}

function startGPS() {
  if (!navigator.geolocation) {
    gpsText.textContent = "GPS N/A";
    return;
  }
  gpsWatchId = navigator.geolocation.watchPosition(
    handleGPSPosition,
    (err) => {
      console.warn("GPS watchPosition error:", err.code, err.message);
      if (err.code === 1) {
        gpsText.textContent = "GPS denied";
      } else if (err.code === 3) {
        gpsText.textContent = "GPS timeout";
      } else {
        gpsText.textContent = "GPS unavailable";
      }
    },
    { enableHighAccuracy: true, maximumAge: 10000, timeout: 15000 }
  );

  setTimeout(() => {
    if (gpsText.textContent === "Locating..." || gpsText.textContent === "GPS timeout") {
      navigator.geolocation.getCurrentPosition(
        handleGPSPosition,
        (err) => {
          console.warn("GPS fallback error:", err.code, err.message);
          if (gpsText.textContent === "Locating...") {
            gpsText.textContent = err.code === 1 ? "GPS denied" : "GPS unavailable";
          }
        },
        { enableHighAccuracy: false, maximumAge: 60000, timeout: 10000 }
      );
    }
  }, 5000);
}

function shouldReverseGeocode(lat, lon) {
  if (lastGeocodedLat === null) return true;
  const d = haversineMeters(lastGeocodedLat, lastGeocodedLon, lat, lon);
  return d > GPS_GEOCODE_THRESHOLD_M;
}

function haversineMeters(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
    Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

async function reverseGeocode(lat, lon) {
  try {
    const res = await fetch(`/api/geocode?lat=${lat}&lon=${lon}`, { credentials: "include" });
    if (res.status === 401) {
      window.location.href = "/auth/clear";
      return null;
    }
    if (!res.ok) throw new Error("geocode endpoint error");
    const data = await res.json();
    return data.locationName || `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
  } catch {
    try {
      const res = await fetch(
        `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json&zoom=14`
      );
      const data = await res.json();
      const addr = data.address || {};
      const parts = [
        addr.park || addr.nature_reserve || addr.leisure || addr.tourism || "",
        addr.city || addr.town || addr.village || addr.suburb || "",
        addr.state || addr.county || "",
      ].filter(Boolean);
      return parts.length > 0 ? parts.join(", ") : data.display_name?.split(",").slice(0, 3).join(",").trim();
    } catch {
      return `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
    }
  }
}

// ── Audio ──────────────────────────────────────────────────────────────────

async function startAudio() {
  try {
    const [playerNode, playerCtx] = await startAudioPlayerWorklet();
    audioPlayerNode = playerNode;
    audioPlayerContext = playerCtx;
    if (playerCtx.state === "suspended") await playerCtx.resume();

    playerAnalyser = playerCtx.createAnalyser();
    playerAnalyser.fftSize = 256;
    playerNode.connect(playerAnalyser);
  } catch (err) {
    console.error("Audio player setup failed:", err);
    showMicError(`Audio playback error: ${err.message}`);
    return;
  }

  try {
    const [recorderNode, recorderCtx, stream] = await startAudioRecorderWorklet(audioRecorderHandler);
    audioRecorderNode = recorderNode;
    audioRecorderContext = recorderCtx;
    micStream = stream;
    if (recorderCtx.state === "suspended") await recorderCtx.resume();

    micAnalyser = recorderCtx.createAnalyser();
    micAnalyser.fftSize = 256;
    const source = recorderCtx.createMediaStreamSource(stream);
    source.connect(micAnalyser);
  } catch (err) {
    console.error("Microphone setup failed:", err);
    const isDenied = err.name === "NotAllowedError" || err.name === "PermissionDeniedError";
    if (isDenied) {
      showMicError("Microphone access denied. Check browser permissions for this site.");
    } else {
      showMicError(`Microphone error: ${err.message}`);
    }
    return;
  }

  voiceFab.classList.remove("disabled");
  voiceFab.classList.add("active");
  addSystemMessage("Voice mode active. Speak naturally!");
  startAudioVisualization();
}

function stopAudio() {
  isAudio = false;
  if (micStream) {
    micStream.getAudioTracks().forEach(t => t.enabled = false);
  }
  voiceFab.classList.remove("active");
  voiceFab.classList.add("muted");
  stopAudioVisualization();
  addSystemMessage("Microphone muted.");
}

function resumeAudio() {
  isAudio = true;
  if (micStream) {
    micStream.getAudioTracks().forEach(t => t.enabled = true);
  }
  voiceFab.classList.remove("muted");
  voiceFab.classList.add("active");
  startAudioVisualization();
  addSystemMessage("Microphone unmuted.");
}

function showMicError(message) {
  addSystemMessage(message);
  voiceFab.classList.remove("active", "disabled", "muted");
  voiceFab.classList.add("error");
  isAudio = false;
  setTimeout(() => voiceFab.classList.remove("error"), 4000);
}

function audioRecorderHandler(pcmData) {
  if (websocket?.readyState === WebSocket.OPEN && isAudio && !isMicDucked()) {
    websocket.send(pcmData);
  }
}

function isMicDucked() {
  if (isAgentSpeaking) return true;
  if (agentStoppedSpeakingAt > 0 && (Date.now() - agentStoppedSpeakingAt) < ECHO_COOLDOWN_MS) return true;
  return false;
}

let audioInitialized = false;

startAudioButton.addEventListener("click", () => {
  if (!audioInitialized) {
    audioInitialized = true;
    isAudio = true;
    voiceFab.classList.remove("error");
    voiceFab.classList.add("disabled");
    startAudio();
  } else if (isAudio) {
    stopAudio();
  } else {
    resumeAudio();
  }
});

// ── Audio Visualization ────────────────────────────────────────────────────

function startAudioVisualization() {
  const ctx = audioVizCanvas.getContext("2d");
  const W = audioVizCanvas.width;
  const H = audioVizCanvas.height;
  const cx = W / 2;
  const cy = H / 2;
  const baseRadius = 30;

  const micData = micAnalyser ? new Uint8Array(micAnalyser.frequencyBinCount) : null;
  const playerData = playerAnalyser ? new Uint8Array(playerAnalyser.frequencyBinCount) : null;

  function draw() {
    vizAnimFrame = requestAnimationFrame(draw);
    ctx.clearRect(0, 0, W, H);

    // Opaque background to prevent content behind canvas from showing through
    ctx.fillStyle = "rgba(10, 15, 13, 0.95)";
    ctx.beginPath();
    ctx.arc(cx, cy, Math.max(W, H) / 2 + 2, 0, Math.PI * 2);
    ctx.fill();

    let micLevel = 0;
    let playerLevel = 0;

    if (micData && micAnalyser) {
      micAnalyser.getByteFrequencyData(micData);
      micLevel = micData.reduce((s, v) => s + v, 0) / micData.length / 255;
    }
    if (playerData && playerAnalyser) {
      playerAnalyser.getByteFrequencyData(playerData);
      playerLevel = playerData.reduce((s, v) => s + v, 0) / playerData.length / 255;
    }

    const isListening = micLevel > 0.05;
    const isSpeaking = playerLevel > 0.05;
    if (isAgentSpeaking && !isSpeaking) {
      agentStoppedSpeakingAt = Date.now();
    }
    isAgentSpeaking = isSpeaking;
    // Optimistic "Listening..." placeholder when user speaks but transcription not yet received
    if (isListening && !currentInputTranscriptionId && !listeningPlaceholderElement) {
      listeningPlaceholderElement = createMessageBubble("Listening...", true, true);
      listeningPlaceholderElement.classList.add("transcription");
      messagesDiv.appendChild(listeningPlaceholderElement);
      scrollToBottom();
    }
    const level = Math.max(micLevel, playerLevel);
    const r = baseRadius + level * 14;

    // Outer glow ring
    const glowColor = isSpeaking
      ? `rgba(34, 211, 238, ${0.15 + level * 0.3})`
      : `rgba(74, 222, 128, ${0.1 + level * 0.35})`;
    ctx.beginPath();
    ctx.arc(cx, cy, r + 4, 0, Math.PI * 2);
    ctx.strokeStyle = glowColor;
    ctx.lineWidth = 3 + level * 5;
    ctx.stroke();

    // Inner ring
    if (isListening || isSpeaking) {
      const innerColor = isSpeaking
        ? `rgba(34, 211, 238, ${0.2 + level * 0.4})`
        : `rgba(74, 222, 128, ${0.15 + level * 0.45})`;
      ctx.beginPath();
      ctx.arc(cx, cy, r - 2, 0, Math.PI * 2);
      ctx.strokeStyle = innerColor;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }

    // Subtle idle pulse
    if (!isListening && !isSpeaking) {
      const t = Date.now() / 2000;
      const pulse = Math.sin(t * Math.PI) * 0.5 + 0.5;
      ctx.beginPath();
      ctx.arc(cx, cy, baseRadius + pulse * 2, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(74, 222, 128, ${0.08 + pulse * 0.08})`;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  }

  draw();
}

function stopAudioVisualization() {
  if (vizAnimFrame) {
    cancelAnimationFrame(vizAnimFrame);
    vizAnimFrame = null;
  }
  const ctx = audioVizCanvas.getContext("2d");
  ctx.clearRect(0, 0, audioVizCanvas.width, audioVizCanvas.height);
}

// ── Text input ─────────────────────────────────────────────────────────────

function sendTextMessage() {
  const msg = messageInput.value.trim();
  if (!msg) return;
  const bubble = createMessageBubble(msg, true, false);
  messagesDiv.appendChild(bubble);
  scrollToBottom();
  messageInput.value = "";
  if (websocket?.readyState === WebSocket.OPEN) {
    websocket.send(JSON.stringify({ type: "text", text: msg }));
  } else {
    addSystemMessage("Not connected. Retrying...");
  }
}

function addSubmitHandler() {
  const sendBtn = document.getElementById("sendButton");
  messageForm.onsubmit = (e) => {
    e.preventDefault();
    if (websocket?.readyState === WebSocket.OPEN) {
      sendTextMessage();
    } else {
      addSystemMessage("Not connected. Wait for connection or retry.");
    }
    return false;
  };
  // Direct click handler for mobile (form submit can be unreliable on touch devices)
  sendBtn.onclick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!sendBtn.disabled) sendTextMessage();
  };
}

// ── UI helpers ─────────────────────────────────────────────────────────────

function updateConnectionStatus(state) {
  statusDot.className = "status-dot";
  if (state === "connected") {
    statusDot.classList.remove("disconnected", "reconnecting");
  } else if (state === "reconnecting") {
    statusDot.classList.add("reconnecting");
  } else {
    statusDot.classList.add("disconnected");
  }
}

function createMessageBubble(text, isUser, isPartial = false) {
  const messageDiv = document.createElement("div");
  messageDiv.className = `message ${isUser ? "user" : "agent"}`;
  const bubbleDiv = document.createElement("div");
  bubbleDiv.className = "bubble";
  const textP = document.createElement("p");
  textP.className = "bubble-text";

  const rendered = renderRichText(text);
  if (rendered.html) {
    textP.innerHTML = rendered.html;
  } else {
    textP.textContent = text;
  }

  if (isPartial && !isUser) {
    const typing = createTypingIndicator();
    textP.appendChild(typing);
  }
  bubbleDiv.appendChild(textP);
  messageDiv.appendChild(bubbleDiv);
  return messageDiv;
}

function updateMessageBubble(element, text, isPartial = false) {
  const textEl = element.querySelector(".bubble-text");
  const existing = textEl.querySelector(".typing-indicator");
  if (existing) existing.remove();

  const rendered = renderRichText(text);
  if (rendered.html) {
    textEl.innerHTML = rendered.html;
  } else {
    textEl.textContent = text;
  }

  if (isPartial) {
    textEl.appendChild(createTypingIndicator());
  }
}

function createTypingIndicator() {
  const span = document.createElement("span");
  span.className = "typing-indicator";
  for (let i = 0; i < 3; i++) {
    const dot = document.createElement("span");
    dot.className = "typing-dot";
    span.appendChild(dot);
  }
  return span;
}

function renderRichText(text) {
  const imgMatch = text.match(/(https?:\/\/[^\s]+\.(?:png|jpg|jpeg|gif|webp)[^\s]*)/i);
  if (imgMatch) {
    const url = imgMatch[1];
    const cleanText = text.replace(url, "").trim();
    const escaped = escapeHtml(cleanText);
    return {
      html: `${escaped}${escaped ? "" : ""}<img class="bubble-image" src="${escapeHtml(url)}" alt="Field guide illustration" loading="lazy" onerror="this.style.display='none'">`
    };
  }

  const gcsMatch = text.match(/(https:\/\/storage\.googleapis\.com\/[^\s]+)/i);
  if (gcsMatch) {
    const url = gcsMatch[1];
    if (/\.(png|jpg|jpeg|gif|webp)/i.test(url)) {
      const cleanText = text.replace(url, "").trim();
      const escaped = escapeHtml(cleanText);
      return {
        html: `${escaped}<img class="bubble-image" src="${escapeHtml(url)}" alt="Generated image" loading="lazy" onerror="this.style.display='none'">`
      };
    }
  }

  return { html: null };
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function finalizeBubble(element) {
  if (!element) return;
  const indicator = element.querySelector(".typing-indicator");
  if (indicator) indicator.remove();
}

function markInterrupted(element) {
  if (!element) return;
  finalizeBubble(element);
  element.classList.add("interrupted");
}

function addSystemMessage(text) {
  const div = document.createElement("div");
  div.className = "system-message";
  div.textContent = text;
  messagesDiv.appendChild(div);
  scrollToBottom();
}

function scrollToBottom() {
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function randomId() {
  return Math.random().toString(36).substring(2, 9);
}

/** Merge transcription text: handles both delta (append) and full-text (replace) updates. */
function mergeTranscriptionText(existing, incoming) {
  if (!incoming) return existing;
  if (!existing) return incoming;
  if (incoming === existing) return existing;  // Exact duplicate
  if (incoming.startsWith(existing)) return incoming;  // Full-text update
  if (existing.endsWith(incoming)) return existing;  // Redundant suffix
  return existing + incoming;  // Delta append
}

function cleanCJKSpaces(text) {
  const cjk = /[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf\uff00-\uffef]/;
  return text.replace(/(\S)\s+(?=\S)/g, (match, c1) => {
    const next = text.match(new RegExp(c1 + "\\s+(.)", "g"));
    if (next?.length > 0) {
      const c2 = next[0].slice(-1);
      if (cjk.test(c1) && cjk.test(c2)) return c1;
    }
    return match;
  });
}

function base64ToArray(base64) {
  let std = base64.replace(/-/g, "+").replace(/_/g, "/");
  while (std.length % 4) std += "=";
  const bin = window.atob(std);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

// ── Video notifications ────────────────────────────────────────────────────

function getVideoPlaceholder(videoId) {
  return messagesDiv.querySelector(`.video-placeholder[data-video-id="${videoId}"]`);
}

function handleVideoStarted(data) {
  const { video_id } = data;
  if (!video_id) return;
  const wrapper = document.createElement("div");
  wrapper.className = "message agent video-placeholder";
  wrapper.setAttribute("data-video-id", video_id);
  const card = document.createElement("div");
  card.className = "video-card video-card--generating";
  const spinner = document.createElement("div");
  spinner.className = "video-spinner";
  spinner.setAttribute("aria-hidden", "true");
  const label = document.createElement("div");
  label.className = "card-label";
  label.textContent = "Generating nature video…";
  card.appendChild(spinner);
  card.appendChild(label);
  wrapper.appendChild(card);
  messagesDiv.appendChild(wrapper);
  scrollToBottom();
}

function handleVideoFailed(data) {
  const { video_id, error } = data;
  const placeholder = video_id ? getVideoPlaceholder(video_id) : null;
  const msg = `Video generation failed: ${error || "Unknown error"}. Try again.`;
  if (placeholder) {
    const wrapper = placeholder.closest(".message") || placeholder;
    const outer = document.createElement("div");
    outer.className = "message agent";
    const card = document.createElement("div");
    card.className = "video-card video-card--error";
    card.textContent = msg;
    outer.appendChild(card);
    wrapper.replaceWith(outer);
  } else {
    addSystemMessage(msg);
  }
  scrollToBottom();
}

function handleVideoReady(data) {
  const { video_id, url } = data;
  if (!url || typeof url !== "string") {
    console.warn("Video ready event missing valid URL:", data);
    if (video_id) {
      const placeholder = getVideoPlaceholder(video_id);
      if (placeholder) {
        const wrapper = placeholder.closest(".message") || placeholder;
        const outer = document.createElement("div");
        outer.className = "message agent";
        const card = document.createElement("div");
        card.className = "video-card video-card--error";
        card.textContent = "Video ready but playback URL unavailable.";
        outer.appendChild(card);
        wrapper.replaceWith(outer);
      }
    } else {
      addSystemMessage("Video generated but playback URL unavailable. Check server logs.");
    }
    scrollToBottom();
    return;
  }

  const wrapper = document.createElement("div");
  wrapper.className = "message agent";
  const card = document.createElement("div");
  card.className = "video-card";

  const label = document.createElement("div");
  label.className = "card-label";
  label.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> Nature video ready`;
  card.appendChild(label);

  const vid = document.createElement("video");
  vid.src = url;
  vid.controls = true;
  vid.playsInline = true;
  vid.preload = "metadata";
  vid.addEventListener("error", (e) => {
    console.warn("Video load error:", e, url);
    card.appendChild(document.createElement("br"));
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "Open video in new tab";
    link.className = "video-fallback-link";
    card.appendChild(link);
  });
  card.appendChild(vid);

  wrapper.appendChild(card);

  const placeholder = video_id ? getVideoPlaceholder(video_id) : null;
  if (placeholder) {
    const placeholderWrapper = placeholder.closest(".message") || placeholder;
    placeholderWrapper.replaceWith(wrapper);
  } else {
    messagesDiv.appendChild(wrapper);
  }
  scrollToBottom();
}

// ── Sheet drag / expand logic ──────────────────────────────────────────────

let sheetStartY = 0;
let sheetStartHeight = 0;

const sheetHandle = document.querySelector(".sheet-handle");

sheetHandle.addEventListener("touchstart", (e) => {
  sheetStartY = e.touches[0].clientY;
  sheetStartHeight = convoSheet.getBoundingClientRect().height;
  convoSheet.style.transition = "none";
}, { passive: true });

sheetHandle.addEventListener("touchmove", (e) => {
  const dy = sheetStartY - e.touches[0].clientY;
  const newH = Math.min(Math.max(sheetStartHeight + dy, 120), window.innerHeight * 0.85);
  convoSheet.style.height = newH + "px";
  voiceFab.style.bottom = (newH + 16) + "px";
}, { passive: true });

sheetHandle.addEventListener("touchend", () => {
  convoSheet.style.transition = "";
  const h = convoSheet.getBoundingClientRect().height;
  const vh = window.innerHeight;
  if (h > vh * 0.55) {
    convoSheet.classList.add("expanded");
    convoSheet.classList.remove("collapsed");
    convoSheet.style.height = "";
    voiceFab.style.bottom = "";
  } else if (h < 160) {
    convoSheet.classList.add("collapsed");
    convoSheet.classList.remove("expanded");
    convoSheet.style.height = "";
    voiceFab.style.bottom = "";
  } else {
    convoSheet.classList.remove("expanded", "collapsed");
    convoSheet.style.height = "";
    voiceFab.style.bottom = "";
  }
});

// ── New Survey / Session Management ──────────────────────────────────────

function startNewSurvey() {
  if (!confirm("Start a new biodiversity survey?\nCurrent observations are saved in the cloud.")) {
    return;
  }

  if (websocket && websocket.readyState === WebSocket.OPEN) {
    websocket.close(1000, "new_survey");
  }

  sessionId = "session-" + Math.random().toString(36).substring(2, 9);

  messagesDiv.innerHTML = "";

  currentMessageId = null;
  currentBubbleElement = null;
  currentInputTranscriptionId = null;
  currentInputTranscriptionElement = null;
  currentOutputTranscriptionId = null;
  currentOutputTranscriptionElement = null;
  if (listeningPlaceholderElement) {
    listeningPlaceholderElement = null;
  }

  reconnectAttempts = 0;

  resetVisualization();

  addSystemMessage("New biodiversity survey started. Connecting...");
  connectWebsocket();
}

document.getElementById("newSurveyBtn").addEventListener("click", startNewSurvey);

// ── Ecology Panel Toggle ────────────────────────────────────────────────────

document.getElementById("ecoPanelToggle").addEventListener("click", () => {
  document.getElementById("ecologyPanel").classList.toggle("open");
});

// ── Boot ───────────────────────────────────────────────────────────────────

addSubmitHandler();

if (!window.isSecureContext) {
  addSystemMessage("HTTPS required for camera & microphone. Use localhost or a secure tunnel.");
}

// Show initial connection state
addSystemMessage("Connecting to EcoScout...");
connectWebsocket();
startGPS();
