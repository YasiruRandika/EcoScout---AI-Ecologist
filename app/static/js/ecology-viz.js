/**
 * EcoScout — Living Ecological Visualization
 * D3.js food web, species accumulation curve, and survey dashboard.
 */

// ── State ──────────────────────────────────────────────────────────────────

const surveyState = {
  species: [],          // { name, commonName, trophicLevel, group, count, conservationStatus, detectedAt }
  relationships: [],    // { source, target, type }
  accumulationCurve: [],// { t, species }
  metrics: { shannon: 0, simpson: 0, richness: 0, evenness: 0 },
  totalObservations: 0,
  expectedSpecies: 0,
};

// ── Trophic color map ──────────────────────────────────────────────────────

const TROPHIC_COLORS = {
  producer:    "#4ade80",
  herbivore:   "#22d3ee",
  omnivore:    "#a78bfa",
  carnivore:   "#f87171",
  decomposer:  "#fbbf24",
  unknown:     "#94a3b8",
};

const CONSERVATION_COLORS = {
  "Least Concern": "#4ade80",
  "Near Threatened": "#a3e635",
  "Vulnerable": "#fbbf24",
  "Endangered": "#fb923c",
  "Critically Endangered": "#ef4444",
  "Not Evaluated": "#94a3b8",
};

// ── Public API ─────────────────────────────────────────────────────────────

export function addSpecies(name, commonName, trophicLevel = "unknown", group = "", conservationStatus = "Not Evaluated") {
  const existing = surveyState.species.find(s => s.name === name);
  if (existing) {
    existing.count++;
  } else {
    surveyState.species.push({
      name, commonName,
      trophicLevel: trophicLevel.toLowerCase(),
      group,
      count: 1,
      conservationStatus,
      detectedAt: Date.now(),
    });
  }
  surveyState.totalObservations++;
  surveyState.accumulationCurve.push({
    t: surveyState.totalObservations,
    species: surveyState.species.length,
  });
  renderDashboard();
  renderFoodWeb();
  renderAccumulationCurve();
}

export function addRelationship(sourceName, targetName, type = "predator-prey") {
  const exists = surveyState.relationships.find(
    r => r.source === sourceName && r.target === targetName
  );
  if (!exists) {
    surveyState.relationships.push({ source: sourceName, target: targetName, type });
    renderFoodWeb();
  }
}

export function updateMetrics(metrics) {
  Object.assign(surveyState.metrics, metrics);
  renderDashboard();
}

export function setExpectedSpecies(count) {
  surveyState.expectedSpecies = count;
  renderDashboard();
  renderAccumulationCurve();
}

export function resetVisualization() {
  surveyState.species = [];
  surveyState.relationships = [];
  surveyState.accumulationCurve = [];
  surveyState.metrics = { shannon: 0, simpson: 0, richness: 0, evenness: 0 };
  surveyState.totalObservations = 0;
  surveyState.expectedSpecies = 0;
  renderDashboard();
  renderFoodWeb();
  renderAccumulationCurve();
}

// ── Dashboard Stats Rendering ──────────────────────────────────────────────

function renderDashboard() {
  const el = document.getElementById("surveyDashboard");
  if (!el) return;

  const { species, metrics, expectedSpecies, totalObservations } = surveyState;
  const coverage = expectedSpecies > 0
    ? Math.round((species.length / expectedSpecies) * 100)
    : 0;

  const shannonClass = metrics.shannon < 1 ? "metric-low" :
                        metrics.shannon < 2 ? "metric-mid" : "metric-high";

  const groupCounts = {};
  species.forEach(s => {
    const g = s.group || "Other";
    groupCounts[g] = (groupCounts[g] || 0) + 1;
  });
  const groupTags = Object.entries(groupCounts)
    .map(([g, c]) => `<span class="group-tag">${g}: ${c}</span>`)
    .join("");

  const conservationAlerts = species
    .filter(s => ["Vulnerable", "Endangered", "Critically Endangered"].includes(s.conservationStatus))
    .map(s => `<div class="conservation-alert">${s.conservationStatus}: ${s.commonName || s.name}</div>`)
    .join("");

  el.innerHTML = `
    <div class="dash-row">
      <div class="dash-stat">
        <span class="dash-value">${species.length}</span>
        <span class="dash-label">Species</span>
      </div>
      <div class="dash-stat">
        <span class="dash-value">${totalObservations}</span>
        <span class="dash-label">Observations</span>
      </div>
      <div class="dash-stat">
        <span class="dash-value ${shannonClass}">${metrics.shannon.toFixed(2)}</span>
        <span class="dash-label">Shannon H'</span>
      </div>
      ${expectedSpecies > 0 ? `
      <div class="dash-stat">
        <span class="dash-value">${coverage}%</span>
        <span class="dash-label">Coverage</span>
      </div>` : ""}
    </div>
    ${groupTags ? `<div class="group-tags">${groupTags}</div>` : ""}
    ${conservationAlerts ? `<div class="conservation-alerts">${conservationAlerts}</div>` : ""}
  `;
}

// ── Food Web (SVG-based force graph) ───────────────────────────────────────

let foodWebSvg = null;

function renderFoodWeb() {
  const container = document.getElementById("foodWebContainer");
  if (!container) return;
  if (surveyState.species.length === 0) {
    container.innerHTML = '<div class="viz-empty">Species will appear here as you explore</div>';
    return;
  }

  const width = container.clientWidth || 280;
  const height = 200;

  container.innerHTML = "";
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  container.appendChild(svg);

  const nodes = surveyState.species.map((s, i) => ({
    id: s.name,
    label: s.commonName || s.name.split(" ").pop(),
    color: TROPHIC_COLORS[s.trophicLevel] || TROPHIC_COLORS.unknown,
    radius: Math.min(8 + s.count * 2, 18),
    x: width / 2 + (Math.random() - 0.5) * width * 0.6,
    y: height / 2 + (Math.random() - 0.5) * height * 0.6,
  }));

  const links = surveyState.relationships
    .map(r => {
      const si = nodes.findIndex(n => n.id === r.source);
      const ti = nodes.findIndex(n => n.id === r.target);
      return si >= 0 && ti >= 0 ? { source: si, target: ti, type: r.type } : null;
    })
    .filter(Boolean);

  // Draw links
  links.forEach(l => {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", nodes[l.source].x);
    line.setAttribute("y1", nodes[l.source].y);
    line.setAttribute("x2", nodes[l.target].x);
    line.setAttribute("y2", nodes[l.target].y);
    line.setAttribute("stroke", "rgba(255,255,255,0.15)");
    line.setAttribute("stroke-width", "1");
    svg.appendChild(line);
  });

  // Draw nodes
  nodes.forEach(n => {
    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("cx", n.x);
    circle.setAttribute("cy", n.y);
    circle.setAttribute("r", n.radius);
    circle.setAttribute("fill", n.color);
    circle.setAttribute("opacity", "0.85");
    svg.appendChild(circle);

    if (n.radius >= 8) {
      const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
      text.setAttribute("x", n.x);
      text.setAttribute("y", n.y + n.radius + 12);
      text.setAttribute("text-anchor", "middle");
      text.setAttribute("fill", "rgba(255,255,255,0.6)");
      text.setAttribute("font-size", "9");
      text.textContent = n.label.length > 12 ? n.label.slice(0, 11) + "…" : n.label;
      svg.appendChild(text);
    }
  });
}

// ── Species Accumulation Curve (Canvas-based) ──────────────────────────────

function renderAccumulationCurve() {
  const canvas = document.getElementById("accumulationCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const W = canvas.width;
  const H = canvas.height;

  ctx.clearRect(0, 0, W, H);

  const data = surveyState.accumulationCurve;
  if (data.length < 2) return;

  const maxT = data[data.length - 1].t;
  const maxS = Math.max(
    data[data.length - 1].species,
    surveyState.expectedSpecies || data[data.length - 1].species
  );
  const padX = 30, padY = 15;
  const plotW = W - padX - 10;
  const plotH = H - padY - 10;

  // Asymptote line (expected species)
  if (surveyState.expectedSpecies > 0) {
    const ey = padY + plotH * (1 - surveyState.expectedSpecies / maxS);
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = "rgba(255,255,255,0.15)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padX, ey);
    ctx.lineTo(W - 10, ey);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = "rgba(255,255,255,0.3)";
    ctx.font = "9px sans-serif";
    ctx.fillText(`Expected: ${surveyState.expectedSpecies}`, padX + 2, ey - 3);
  }

  // Accumulation curve
  ctx.strokeStyle = "#4ade80";
  ctx.lineWidth = 2;
  ctx.beginPath();
  data.forEach((d, i) => {
    const x = padX + (d.t / maxT) * plotW;
    const y = padY + plotH * (1 - d.species / maxS);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Dots
  data.forEach(d => {
    const x = padX + (d.t / maxT) * plotW;
    const y = padY + plotH * (1 - d.species / maxS);
    ctx.fillStyle = "#4ade80";
    ctx.beginPath();
    ctx.arc(x, y, 2.5, 0, Math.PI * 2);
    ctx.fill();
  });

  // Axes labels
  ctx.fillStyle = "rgba(255,255,255,0.4)";
  ctx.font = "9px sans-serif";
  ctx.fillText("Observations →", padX, H - 2);
  ctx.save();
  ctx.translate(8, padY + plotH / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText("Species ↑", 0, 0);
  ctx.restore();
}

// ── Handle ecology_update messages from WebSocket ──────────────────────────

export function handleEcologyUpdate(data) {
  if (data.species) {
    addSpecies(
      data.species.name,
      data.species.commonName || "",
      data.species.trophicLevel || "unknown",
      data.species.group || "",
      data.species.conservationStatus || "Not Evaluated"
    );
  }
  if (data.relationship) {
    addRelationship(
      data.relationship.source,
      data.relationship.target,
      data.relationship.type || "ecological"
    );
  }
  if (data.metrics) {
    updateMetrics(data.metrics);
  }
  if (data.expectedSpecies) {
    setExpectedSpecies(data.expectedSpecies);
  }
}
