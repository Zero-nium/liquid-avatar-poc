/**
Liquid Avatar — Swarm Visualization Engine v1.2
Hybrid Rendering: D3.js Physics + Canvas 2D (High Quality) + SVG Links
*/
const API_BASE = window.location.origin.includes('localhost')
  ? 'http://localhost:8000'
  : window.location.origin;

// ─── STATE ────────────────────────────────────────────────────────────────────
let simulation, svg, g, link;
let agentsData = { nodes: [], edges: [] };
let ontologyData = null;
let selectedAgent = null;
let showLabels = false;
let showConnections = true;

// ─── CANVAS ENGINE STATE ────────────────────────────────────────────────────
let canvas, ctx, width, height;
let useAnimeMode = localStorage.getItem('liquid_anime_mode') === 'true';
const avatarCache = new Map(); // Texture cache for performance
const offscreenCanvas = document.createElement('canvas');
const offCtx = offscreenCanvas.getContext('2d', { willReadFrequently: false });

// ─── CONNECTION FILTERS ──────────────────────────────────────────────────────
const connectionFilters = {
  initialized: true,
  cluster_peer: true,
  beacon_interaction: false,
  metadata_match: false
};

// ─── WEBSOCKET CONNECTION ──────────────────────────────────────────────
let swarmSocket = null;

function connectSwarmWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/swarm`;
  
  swarmSocket = new WebSocket(wsUrl);
  swarmSocket.onopen = () => console.log('🔌 WebSocket connected to swarm');
  
  swarmSocket.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleSwarmUpdate(msg);
    } catch (e) {
      console.error('WebSocket message parse error:', e);
    }
  };

  swarmSocket.onclose = () => {
    console.log(' WebSocket disconnected. Reconnecting in 5s...');
    setTimeout(connectSwarmWebSocket, 5000);
  };

  swarmSocket.onerror = (err) => console.error('WebSocket error:', err);
}

// ─── SWARM UPDATE HANDLER ─────────────────────────────────────────────
function handleSwarmUpdate(msg) {
  switch (msg.type) {
    case 'beacon_update':
      // Clear cache for updated agent to force redraw
      avatarCache.delete(msg.data.agent_id);
      break;
    case 'agent_updated':
    case 'agent_registered':
      loadSwarmData();
      break;
    default:
      break;
  }
}

// ─── DATA LOADING ────────────────────────────────────────────────────────────
async function loadSwarmData() {
  try {
    const res = await fetch(`${API_BASE}/swarm/map`);
    agentsData = await res.json();
    avatarCache.clear(); // Clear cache on data load
    
    if (simulation) {
      simulation.nodes(agentsData.nodes);
      simulation.force('link').links(agentsData.edges);
      simulation.alpha(1).restart();
      updateStats();
    }
  } catch (err) {
    console.error('Failed to load swarm data:', err);
  }
}

// ─── COLOR UTILS ──────────────────────────────────────────────────────────────
function hslToHex(h, s, l) {
  l /= 100;
  const a = s * Math.min(l, 1 - l) / 100;
  const f = n => {
    const k = (n + h / 30) % 12;
    const color = l - a * Math.max(Math.min(k - 3, 9 - k, 1), -1);
    return Math.round(255 * color).toString(16).padStart(2, '0');
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

// ─── CANVAS TEXTURE GENERATION (The "vTuber" Quality Engine) ────────────────
function generateAvatarTexture(agent, size) {
  const cacheKey = `${agent.id}-${agent.avatar?.base_hue}-${agent.avatar?.dynamics_state}-${agent.avatar?.shape_complexity}`;
  
  if (avatarCache.has(cacheKey)) {
    return avatarCache.get(cacheKey);
  }

  // Offscreen rendering
  const w = size * 3; // High res for crispness
  const h = size * 3;
  offscreenCanvas.width = w;
  offscreenCanvas.height = h;
  offCtx.clearRect(0, 0, w, h);

  const isDiscovered = agent.cluster?.startsWith('discovered_via_');
  const hue = agent.avatar?.base_hue ?? 180;
  const sat = agent.avatar?.saturation ?? 0.75;
  const complexity = agent.avatar?.shape_complexity ?? 5;
  const dynamics = agent.avatar?.dynamics_state || 'idle';
  const role = agent.role || 'general';

  const cx = w / 2;
  const cy = h / 2;
  const r = size * 1.2;

  if (isDiscovered) {
    // Discovered: Dimmed Pentagon
    offCtx.save();
    offCtx.translate(cx, cy);
    offCtx.fillStyle = '#cbd5e1';
    offCtx.strokeStyle = '#94a3b8';
    offCtx.lineWidth = 3;
    offCtx.setLineDash([6, 6]);
    offCtx.globalAlpha = 0.6;
    
    offCtx.beginPath();
    for (let i = 0; i < 5; i++) {
      const angle = (i * 2 * Math.PI / 5) - Math.PI / 2;
      offCtx.lineTo(r * Math.cos(angle), r * Math.sin(angle));
    }
    offCtx.closePath();
    offCtx.fill();
    offCtx.stroke();
    offCtx.restore();
  } else {
    // Registered: High-Quality Anime Face
    
    // 1. Glow / Aura
    const glowGrad = offCtx.createRadialGradient(cx, cy, r * 0.5, cx, cy, r * 2.5);
    glowGrad.addColorStop(0, `hsla(${hue}, ${sat*100}%, 60%, 0.4)`);
    glowGrad.addColorStop(1, 'transparent');
    offCtx.fillStyle = glowGrad;
    offCtx.beginPath();
    offCtx.arc(cx, cy, r * 2.5, 0, Math.PI * 2);
    offCtx.fill();

    // 2. Face Base (Skin Gradient)
    offCtx.save();
    offCtx.translate(cx, cy);
    const skinGrad = offCtx.createRadialGradient(-r*0.3, -r*0.3, 0, 0, 0, r);
    skinGrad.addColorStop(0, '#FFE8D6');
    skinGrad.addColorStop(0.6, '#FFD4C0');
    skinGrad.addColorStop(1, '#EAC0B0');
    offCtx.fillStyle = skinGrad;
    
    // Face Shape (Chin sharpness based on complexity)
    offCtx.beginPath();
    if (complexity > 8) {
      offCtx.ellipse(0, 0, r * 0.8, r * 0.9, 0, 0, Math.PI * 2);
    } else {
      offCtx.moveTo(-r * 0.7, -r * 0.2);
      offCtx.bezierCurveTo(-r * 0.7, r * 0.6, -r * 0.3, r * 0.9, 0, r * 0.95);
      offCtx.bezierCurveTo(r * 0.3, r * 0.9, r * 0.7, r * 0.6, r * 0.7, -r * 0.2);
      offCtx.arc(0, -r * 0.2, r * 0.7, Math.PI, 0, true);
    }
    offCtx.fill();
    offCtx.restore();

    // 3. Hair (Complexity-Based)
    const hairColor = `hsl(${hue}, ${sat*60}%, 25%)`;
    const hairHighlight = `hsl(${hue}, ${sat*40}%, 40%)`;
    offCtx.fillStyle = hairColor;
    offCtx.save();
    offCtx.translate(cx, cy);
    
    // Bangs / Top
    offCtx.beginPath();
    offCtx.arc(0, -r * 0.2, r * 1.1, Math.PI, 0, true);
    offCtx.lineTo(r * 0.8, r * 0.2);
    offCtx.quadraticCurveTo(0, -r * 0.5, -r * 0.8, r * 0.2);
    offCtx.fill();

    // Side Hair / Tails
    if (complexity <= 4) {
      // Short
    } else if (complexity <= 7) {
      // Twin Tails
      offCtx.beginPath();
      offCtx.arc(-r * 0.9, r * 0.2, r * 0.5, 0, Math.PI * 2);
      offCtx.arc(r * 0.9, r * 0.2, r * 0.5, 0, Math.PI * 2);
      offCtx.fill();
    } else {
      // Long Flowing
      offCtx.beginPath();
      offCtx.moveTo(-r * 0.8, -r * 0.2);
      offCtx.quadraticCurveTo(-r * 1.2, r * 1.2, -r * 0.5, r * 1.5);
      offCtx.quadraticCurveTo(0, r * 1.2, r * 0.5, r * 1.5);
      offCtx.quadraticCurveTo(r * 1.2, r * 1.2, r * 0.8, -r * 0.2);
      offCtx.fill();
    }
    
    // Hair Shine
    offCtx.fillStyle = hairHighlight;
    offCtx.beginPath();
    offCtx.ellipse(-r * 0.3, -r * 0.8, r * 0.4, r * 0.1, -0.2, 0, Math.PI * 2);
    offCtx.fill();
    offCtx.restore();

    // 4. Eyes (Gradient & Shine)
    const eyeColor = `hsl(${hue}, ${sat*100}%, 50%)`;
    const eyeGrad = offCtx.createRadialGradient(0, 0, 0, 0, 0, r * 0.35);
    eyeGrad.addColorStop(0, '#FFF');
    eyeGrad.addColorStop(0.2, eyeColor);
    eyeGrad.addColorStop(1, `hsl(${hue}, ${sat*100}%, 20%)`);

    const eyeY = -r * 0.1;
    const eyeOpen = dynamics === 'analysis' ? 0.4 : 1.0;
    
    [-1, 1].forEach(side => {
      const ex = side * r * 0.4;
      offCtx.save();
      offCtx.translate(ex, eyeY);
      offCtx.scale(1, eyeOpen);
      
      // Eye White
      offCtx.fillStyle = '#FFF';
      offCtx.beginPath();
      offCtx.ellipse(0, 0, r * 0.3, r * 0.35, 0, 0, Math.PI * 2);
      offCtx.fill();
      
      // Iris
      offCtx.fillStyle = eyeGrad;
      offCtx.beginPath();
      offCtx.arc(0, 0, r * 0.25, 0, Math.PI * 2);
      offCtx.fill();
      
      // Pupil
      offCtx.fillStyle = '#111';
      offCtx.beginPath();
      offCtx.arc(0, 0, r * 0.1, 0, Math.PI * 2);
      offCtx.fill();
      
      // Shine
      offCtx.fillStyle = 'rgba(255,255,255,0.9)';
      offCtx.beginPath();
      offCtx.arc(-r * 0.1, -r * 0.1, r * 0.08, 0, Math.PI * 2);
      offCtx.arc(r * 0.05, r * 0.1, r * 0.04, 0, Math.PI * 2);
      offCtx.fill();
      
      // Lashes
      offCtx.strokeStyle = '#222';
      offCtx.lineWidth = 3;
      offCtx.lineCap = 'round';
      offCtx.beginPath();
      offCtx.moveTo(-r * 0.35, 0);
      offCtx.quadraticCurveTo(0, -r * 0.4, r * 0.35, 0);
      offCtx.stroke();
      
      offCtx.restore();
    });

    // 5. Blush
    offCtx.fillStyle = `hsla(${(hue+20)%360}, ${sat*100}%, 70%, 0.4)`;
    offCtx.beginPath();
    offCtx.ellipse(-r * 0.55, r * 0.3, r * 0.2, r * 0.1, 0, 0, Math.PI * 2);
    offCtx.ellipse(r * 0.55, r * 0.3, r * 0.2, r * 0.1, 0, 0, Math.PI * 2);
    offCtx.fill();

    // 6. Mouth
    offCtx.strokeStyle = '#C48B8B';
    offCtx.lineWidth = 3;
    offCtx.lineCap = 'round';
    offCtx.beginPath();
    if (dynamics === 'output' || dynamics === 'idle') {
      offCtx.arc(0, r * 0.5, r * 0.15, 0.2, Math.PI - 0.2);
    } else {
      offCtx.moveTo(-r * 0.1, r * 0.5);
      offCtx.lineTo(r * 0.1, r * 0.5);
    }
    offCtx.stroke();
  }

  const texture = offCtx.getImageData(0, 0, w, h);
  avatarCache.set(cacheKey, texture);
  return texture;
}

// ─── RENDER LOOP ────────────────────────────────────────────────────────────
function renderFrame() {
  if (!ctx) return;
  
  ctx.clearRect(0, 0, width, height);
  
  // Draw Avatars
  agentsData.nodes.forEach(d => {
    if (!d.x || !d.y) return;
    
    const size = d.avatar?.size ?? 25;
    const texture = generateAvatarTexture(d, size);
    
    ctx.putImageData(texture, d.x - texture.width/2, d.y - texture.height/2);
    
    // Labels
    if (showLabels) {
      ctx.fillStyle = '#64748b';
      ctx.font = '11px "IBM Plex Mono"';
      ctx.textAlign = 'center';
      ctx.fillText(d.name, d.x, d.y + size * 1.8);
    }
  });

  requestAnimationFrame(renderFrame);
}

// ─── INITIALIZATION ───────────────────────────────────────────────────────────
async function init() {
  const container = document.getElementById('canvas-container');
  width = container.clientWidth;
  height = container.clientHeight;

  // Setup Canvas
  canvas = document.getElementById('swarm-canvas');
  canvas.width = width;
  canvas.height = height;
  ctx = canvas.getContext('2d', { alpha: true });
  
  // Setup SVG (for Links)
  svg = d3.select('#swarm-svg')
    .attr('width', width)
    .attr('height', height)
    .attr('viewBox', [0, 0, width, height]);

  // Zoom/Pan Logic (applies to both Canvas and SVG)
  const zoom = d3.zoom()
    .scaleExtent([0.1, 4])
    .on('zoom', (e) => {
      // Transform SVG links
      g.attr('transform', e.transform);
      
      // Transform Canvas avatars
      ctx.save();
      ctx.setTransform(e.transform.k, 0, 0, e.transform.k, e.transform.x, e.transform.y);
      // Note: In a full implementation, we'd redraw canvas here. 
      // For simplicity, we'll rely on requestAnimationFrame loop to redraw with transform logic if needed,
      // but for this MVP, we'll let D3 drive the node positions and canvas redraws them at 0,0 relative to zoom?
      // Actually, easiest way: Update node.x/y in D3, and Canvas draws at x,y. 
      // Zoom is handled by CSS transform on the container or re-drawing.
      // Let's stick to: D3 updates node positions. Canvas draws nodes.
      ctx.restore();
    });

  // Apply initial zoom to container for simplicity
  // For a robust solution, we usually zoom the canvas context.
  // Let's attach zoom to the canvas
  d3.select(canvas).call(zoom);

  g = svg.append('g');

  try {
    const [swarmRes, ontologyRes] = await Promise.all([
      fetch(`${API_BASE}/swarm/map`),
      fetch(`${API_BASE}/ontology`)
    ]);

    agentsData = await swarmRes.json();
    ontologyData = await ontologyRes.json();

    document.getElementById('loading').style.display = 'none';

    renderOntology();
    renderDynamicsLegend();
    updateStats();
    setupSimulation(width, height);
    
    // Start Render Loop
    renderFrame();
    
    connectSwarmWebSocket();
    initConnectionToggles();

  } catch (err) {
    console.error('Failed to load swarm data:', err);
    document.getElementById('loading').innerHTML = `
      <div style="color: #ef4444;">Failed to connect to API</div>
      <div style="font-size: 11px; margin-top: 8px;">${err.message}</div>
    `;
  }
}

function setupSimulation(w, h) {
  const clusterRadial = {
    'conductor': { angle: 0, radius: 0.2 },
    'architect': { angle: -0.7, radius: 0.4 },
    'optimizer': { angle: 0.7, radius: 0.4 },
    'auditor': { angle: -2.0, radius: 0.5 },
    'chronicler': { angle: 2.0, radius: 0.5 },
    'general': { angle: 0, radius: 0.6 }
  };

  simulation = d3.forceSimulation(agentsData.nodes)
    .force('link', d3.forceLink(agentsData.edges).id(d => d.id).distance(100).strength(0.1))
    .force('charge', d3.forceManyBody().strength(-300))
    .force('center', d3.forceCenter(w / 2, h / 2))
    .force('collision', d3.forceCollide().radius(d => (d.avatar?.size ?? 25) * 2.5))
    .force('clusterRadial', d3.forceRadial(d => {
      const pos = clusterRadial[d.role] || clusterRadial['general'];
      return Math.min(w, h) * 0.3 * pos.radius;
    }, w / 2, h / 2).strength(0.1));

  // Render SVG Links
  link = g.append('g')
    .attr('class', 'connection-lines')
    .selectAll('line')
    .data(agentsData.edges)
    .join('line')
    .attr('class', d => `connection-line conn-${d.type || 'cluster_peer'}`)
    .attr('display', d => connectionFilters[d.type || 'cluster_peer'] ? 'inline' : 'none')
    .attr('stroke', d => ({
      initialized: '#64748b',
      cluster_peer: '#94a3b8',
      beacon_interaction: '#10b981',
      metadata_match: '#8b5cf6'
    }[d.type] || '#94a3b8'))
    .attr('stroke-opacity', d => d.type === 'cluster_peer' ? 0.4 : 0.7)
    .attr('stroke-width', d => d.type === 'initialized' ? 1.5 : 1)
    .attr('stroke-dasharray', d => ({
      initialized: 'none',
      cluster_peer: '4,4',
      beacon_interaction: '2,3',
      metadata_match: '6,2,2,2'
    }[d.type] || '4,4'));

  // Interaction: Click on Canvas -> Find Node -> Select
  canvas.addEventListener('click', (e) => {
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    
    // Find closest node
    let closest = null;
    let minDist = 40; // Hit radius

    agentsData.nodes.forEach(d => {
      if (!d.x) return;
      const dist = Math.hypot(d.x - x, d.y - y);
      if (dist < minDist) {
        minDist = dist;
        closest = d;
      }
    });

    if (closest) {
      selectAgent(closest);
      // Highlight effect
      canvas.style.cursor = 'pointer';
      setTimeout(() => canvas.style.cursor = 'grab', 200);
    } else {
      selectedAgent = null;
      document.getElementById('agent-details').innerHTML = '<div style="color: var(--text-muted); font-style: italic;">Click an avatar to inspect</div>';
    }
  });

  simulation.on('tick', () => {
    // Update SVG Links
    link
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x)
      .attr('y2', d => d.target.y);
      
    // Canvas avatars are redrawn in renderFrame() using d.x / d.y
  });
}

// ─── UI & INTERACTIONS ──────────────────────────────────────────────────────
function toggleAnimeMode() {
  useAnimeMode = !useAnimeMode;
  localStorage.setItem('liquid_anime_mode', useAnimeMode);
  avatarCache.clear(); // Force re-render with new mode
  
  const btn = document.getElementById('anime-toggle');
  if (btn) {
    btn.style.background = useAnimeMode ? 'var(--accent)' : 'transparent';
    btn.style.color = useAnimeMode ? 'white' : 'var(--text-primary)';
    btn.style.borderColor = useAnimeMode ? 'var(--accent)' : 'var(--border)';
  }
}

function toggleLabels() {
  showLabels = !showLabels;
}

function resetZoom() {
  // Reset zoom logic here if implemented fully
}

function refreshData() {
  loadSwarmData();
}

async function selectAgent(agent) {
  selectedAgent = agent;
  const details = document.getElementById('agent-details');
  const hue = agent.avatar?.base_hue ?? 180;
  const color = hslToHex(hue, 70, 55);
  
  try {
    const res = await fetch(`${API_BASE}/agents/${agent.id}`);
    const fullData = await res.json();
    
    const isDiscovered = agent.cluster && agent.cluster.startsWith('discovered_via_');
    
    details.innerHTML = `
      <div style="margin-bottom: 12px;">
        <div style="font-size: 16px; font-weight: 600; color: ${color}; margin-bottom: 4px;">
          ${fullData.identity?.name || agent.name}
        </div>
        <div style="font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px;">
          ${fullData.identity?.role || agent.role || 'general'} · ${fullData.identity?.swarm_cluster || agent.cluster || 'no cluster'}
        </div>
      </div>

      <div style="margin-bottom: 12px;">
        <span class="dynamics-badge dynamics-${agent.avatar?.dynamics_state || 'idle'}">
          ${agent.avatar?.dynamics_state || 'idle'}
        </span>
      </div>

      <div style="font-size: 11px; color: #94a3b8; margin-bottom: 8px;">Avatar Signature</div>
      <div class="stat-row">
        <span class="stat-label">Hue</span>
        <span class="stat-value">${Math.round(agent.avatar?.base_hue ?? 180)}°</span>
      </div>
      <div class="stat-row">
        <span class="stat-label">Saturation</span>
        <span class="stat-value">${Math.round((agent.avatar?.saturation ?? 0.8) * 100)}%</span>
      </div>
      <div class="stat-row">
        <span class="stat-label">Shape</span>
        <span class="stat-value">${agent.avatar?.shape_complexity ?? 6}-gon</span>
      </div>
      <div class="stat-row">
        <span class="stat-label">Size</span>
        <span class="stat-value">${Math.round(agent.avatar?.size ?? 20)}px</span>
      </div>
      <div class="stat-row">
        <span class="stat-label">Pulse</span>
        <span class="stat-value">${(agent.avatar?.pulse_rate ?? 1.0).toFixed(2)}x</span>
      </div>
    `;

    if (fullData.quote?.text) {
      details.innerHTML += `
        <div style="margin-top: 12px; padding: 8px; background: var(--bg-panel); border-left: 2px solid var(--accent); font-style: italic; font-size: 12px; color: var(--text-secondary);">
          "${fullData.quote.text}"
          <div style="margin-top: 4px; font-size: 10px; color: var(--text-muted);">
            — Verified ${new Date(fullData.quote.verified_at).toLocaleDateString()}
          </div>
        </div>
      `;
    }
    
    if (isDiscovered) {
      details.innerHTML += `
        <div style="margin-top: 20px; padding: 16px; background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%); border: 1px solid #bae6fd; border-radius: 8px;">
          <div style="font-size: 12px; font-weight: 600; color: #0369a1; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px;">
             Claim This Avatar
          </div>
          <p style="font-size: 11px; color: #475569; margin-bottom: 12px; line-height: 1.5;">
            This agent was discovered via ${agent.cluster.replace('discovered_via_', '')} but hasn't registered yet. Claim this avatar to submit your schema and activate your full Liquid Avatar profile.
          </p>
          <button id="claim-avatar-btn" style="width: 100%; padding: 10px; background: #0066FF; color: white; border: none; border-radius: 6px; cursor: pointer; font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase;">
            Claim & Register
          </button>
        </div>
      `;
      
      document.getElementById('claim-avatar-btn').onclick = () => {
        alert('Registration flow would open here.');
      };
    }
  } catch (err) {
    console.error('Failed to fetch agent details:', err);
  }
}

function updateStats() {
  const nodes = agentsData.nodes;
  const active = nodes.filter(n => n.avatar?.dynamics_state !== 'idle').length;
  const clusters = [...new Set(nodes.map(n => n.cluster).filter(Boolean))];
  
  document.getElementById('stat-count').textContent = nodes.length;
  document.getElementById('stat-active').textContent = active;
  document.getElementById('stat-clusters').textContent = clusters.length;
}

function renderOntology() {
  if (!ontologyData) return;
  const container = document.getElementById('ontology-list');
  container.innerHTML = ontologyData.domains.map(d => `
    <div class="legend-item">
      <div class="legend-color" style="background: ${d.spectrum[0]}"></div>
      <span>${d.domain}</span>
    </div>
  `).join('');
}

function renderDynamicsLegend() {
  const states = ['idle', 'input', 'output', 'analysis', 'verification'];
  const container = document.getElementById('dynamics-legend');
  const desc = {
    idle: 'Subtle breathing glow',
    input: 'Inward pulse — receiving',
    output: 'Outward pulse — emitting',
    analysis: 'Clockwise rotation',
    verification: 'Pendulum swing'
  };

  container.innerHTML = states.map(s => `
    <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 11px;">
      <span class="dynamics-badge dynamics-${s}">${s}</span>
      <span style="color: #64748b;">${desc[s]}</span>
    </div>
  `).join('');
}

function initConnectionToggles() {
  // Implementation of toggles...
}

function toggleConnection(checkbox) {
  const type = checkbox.dataset.conn;
  connectionFilters[type] = checkbox.checked;
  if (link) {
    link.attr('display', d => connectionFilters[d.type || 'cluster_peer'] ? 'inline' : 'none');
  }
}

window.addEventListener('resize', () => {
  const container = document.getElementById('canvas-container');
  width = container.clientWidth;
  height = container.clientHeight;
  canvas.width = width;
  canvas.height = height;
  svg.attr('width', width).attr('height', height).attr('viewBox', [0, 0, width, height]);
  simulation.force('center', d3.forceCenter(width / 2, height / 2)).alpha(0.3).restart();
});

document.addEventListener('DOMContentLoaded', init);