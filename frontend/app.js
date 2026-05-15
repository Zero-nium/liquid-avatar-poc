/**
Liquid Avatar — Swarm Visualization Engine v1.2
D3.js force-directed graph with SVG rendering
Implements Council feedback: Schema v1.2 shapes, blur/vibration/permanence, connector toggles
*/
const API_BASE = window.location.origin.includes('localhost')
  ? 'http://localhost:8000'
  : window.location.origin;

// ─── STATE ────────────────────────────────────────────────────────────────────
let simulation, svg, g, link, node;
let agentsData = { nodes: [], edges: [] };
let ontologyData = null;
let selectedAgent = null;
let showLabels = false;
let animationFrame;
let showConnections = true;

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
    console.log('🔌 WebSocket disconnected. Reconnecting in 5s...');
    setTimeout(connectSwarmWebSocket, 5000);
  };

  swarmSocket.onerror = (err) => console.error('WebSocket error:', err);
}

// ─── SWARM UPDATE HANDLER ──────────────────────────────────────────────
function handleSwarmUpdate(msg) {
  switch (msg.type) {
    case 'beacon_update':
      handleBeaconUpdate(msg.data);
      break;
    case 'agent_updated':
    case 'agent_registered':
      loadSwarmData();
      break;
    default:
      break;
  }
}

function handleBeaconUpdate(data) {
  const agentId = data.agent_id;
  const nodeSelection = svg.selectAll('.agent-node').filter(d => d.id === agentId);
  
  if (!nodeSelection.empty()) {
    const agentData = nodeSelection.data()[0];
    agentData.last_beacon = data.timestamp;
    renderAvatar(nodeSelection);
  }
}

// ─── DATA LOADING ────────────────────────────────────────────────────────────
async function loadSwarmData() {
  try {
    const res = await fetch(`${API_BASE}/swarm/map`);
    const newData = await res.json();
    
    agentsData = newData;
    
    if (simulation) {
      simulation.nodes(agentsData.nodes);
      simulation.force('link').links(agentsData.edges);
      simulation.alpha(1).restart();

      // Inside loadSwarmData(), replace the link data join with:
      link = link.data(agentsData.edges, d => `${d.source.id || d.source}-${d.target.id || d.target}`).join('line')
        .attr('class', d => `connection-line conn-${d.type || 'cluster_peer'}`)
        .attr('display', d => connectionFilters[d.type || 'cluster_peer'] ? 'inline' : 'none')
        .attr('stroke', d => ({
          initialized: '#475569',
          cluster_peer: '#cbd5e1',
          beacon_interaction: '#10b981',
          metadata_match: '#8b5cf6'
        }[d.type] || '#cbd5e1'))
        .attr('stroke-opacity', d => d.type === 'cluster_peer' ? 0.3 : 0.6)
        .attr('stroke-width', d => d.type === 'initialized' ? 1.5 : 1)
        .attr('stroke-dasharray', d => ({
          initialized: 'none',
          cluster_peer: '4,4',
          beacon_interaction: '2,3',
          metadata_match: '6,2,2,2'
        }[d.type] || '4,4'));

      node = node.data(agentsData.nodes, d => d.id).join('g')
        .attr('class', 'agent-node')
        .call(renderAvatar)
        .call(d3.drag()
          .on('start', dragstarted)
          .on('drag', dragged)
          .on('end', dragended));

      node.on('click', (e, d) => selectAgent(d))
        .on('mouseover', (e, d) => showTooltip(e, d))
        .on('mouseout', hideTooltip);

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

function getAgentColor(agent) {
  const hue = agent.avatar?.base_hue ?? 180;
  const sat = Math.round((agent.avatar?.saturation ?? 0.8) * 100);
  return hslToHex(hue, sat, 55);
}

function getAgentGlow(agent) {
  const hue = agent.avatar?.base_hue ?? 180;
  const sat = Math.round((agent.avatar?.saturation ?? 0.8) * 100);
  return hslToHex(hue, sat, 70);
}

// ─── GEOMETRY GENERATORS ──────────────────────────────────────────────────────
function generatePolygon(cx, cy, r, sides, rotation = 0, vibration = 0) {
  const points = [];
  for (let i = 0; i < sides; i++) {
    const angle = (i * 2 * Math.PI / sides) + rotation - Math.PI / 2;
    // Schema v1.2: Vertex Vibration for architects
    const vibOffset = vibration * Math.sin(Date.now() * 0.005 + i);
    points.push([cx + (r + vibOffset) * Math.cos(angle), cy + (r + vibOffset) * Math.sin(angle)]);
  }
  return points.map(p => p.join(',')).join(' ');
}

// ─── DYNAMICS ─────────────────────────────────────────────────────────────────
const dynamicsState = new Map();

function initDynamics(agentId, dynamicsType) {
  if (!dynamicsState.has(agentId)) {
    dynamicsState.set(agentId, {
      phase: Math.random() * Math.PI * 2,
      speed: 0.02 + Math.random() * 0.02,
      offset: Math.random() * 100
    });
  }
}

function computeDynamicsTransform(agent, time) {
  const state = dynamicsState.get(agent.id);
  if (!state) return { scale: 1, rotation: 0, opacity: 1 };
  
  const dynamics = agent.avatar?.dynamics_state || 'idle';
  let scale = 1, rotation = 0, opacity = 1;

  switch (dynamics) {
    case 'idle':
      // Schema v1.2: Static Permanence for chroniclers (no breathing)
      if (agent.role === 'chronicler' || agent.role === 'chronicle') {
        opacity = 0.85; // Steady luminosity
      } else {
        opacity = 0.4 + 0.2 * Math.sin(state.phase + time * state.speed);
      }
      break;
    case 'input':
      scale = 0.92 + 0.08 * Math.sin(state.phase + time * state.speed * 2);
      break;
    case 'output':
      scale = 1.0 + 0.12 * Math.sin(state.phase + time * state.speed * 2);
      break;
    case 'analysis':
      rotation = (time * state.speed * 30) % 360;
      break;
    case 'verification':
      rotation = 5 * Math.sin(state.phase + time * state.speed * 3);
      break;
  }

  return { scale, rotation, opacity };
}

// ─── RENDERING ────────────────────────────────────────────────────────────────
function renderAvatar(selection) {
  selection.each(function(d) {
    const el = d3.select(this);
    el.selectAll('*').remove();

    const color = getAgentColor(d);
    const glow = getAgentGlow(d);
    const size = d.avatar?.size ?? 20;
    const sides = d.avatar?.shape_complexity ?? 6;
    const isCircle = sides >= 20; // Only render as circle if 20+ sides (true circle)

    // Schema v1.2: Blur Factor (Signal Decay)
    const hoursSinceReport = d.last_reported 
      ? (Date.now() - new Date(d.last_reported).getTime()) / 3600000 
      : 0;
    const blurAmount = Math.min(hoursSinceReport / 24, 3); // Max 3px blur after 24h
    
    // Apply blur to entire agent group if stale
    if (blurAmount > 0.5) {
      el.attr('filter', `blur(${blurAmount}px)`);
      // Reduce glow opacity for stale agents
      el.append('circle')
        .attr('r', size * 1.6)
        .attr('fill', glow)
        .attr('opacity', 0.04)
        .attr('class', 'glow-outer');
      el.append('circle')
        .attr('r', size * 1.2)
        .attr('fill', glow)
        .attr('opacity', 0.08)
        .attr('class', 'glow-inner');
    } else {
      // Normal glow for fresh agents
      el.append('circle')
        .attr('r', size * 1.6)
        .attr('fill', glow)
        .attr('opacity', 0.08)
        .attr('class', 'glow-outer');
      el.append('circle')
        .attr('r', size * 1.2)
        .attr('fill', glow)
        .attr('opacity', 0.15)
        .attr('class', 'glow-inner');
    }

    // Main shape
    if (isCircle) {
      el.append('circle')
        .attr('r', size)
        .attr('fill', color)
        .attr('stroke', glow)
        .attr('stroke-width', 2)
        .attr('opacity', 0.9)
        .attr('class', 'avatar-shape');

      if (d.role === 'chronicler') {
        const coil = d3.arc()
          .innerRadius(size * 0.3)
          .outerRadius(size * 0.5)
          .startAngle(0)
          .endAngle(Math.PI * 1.5);
        el.append('path')
          .attr('d', coil)
          .attr('fill', glow)
          .attr('opacity', 0.6);
      }
    } else {
      // Schema v1.2: Vertex Vibration for architects (subtle point oscillation)
      const vibration = (d.role === 'architect' && sides === 6) ? 1.5 : 0;
      const points = generatePolygon(0, 0, size, sides, 0, vibration);

      // Schema v1.2: Add inner detail for high-complexity shapes to distinguish from circles
      if (sides >= 10 && !isCircle) {
        // Draw inner polygon to show it's not a circle
        const innerPoints = generatePolygon(0, 0, size * 0.5, sides);
        el.append('polygon')
          .attr('points', innerPoints)
          .attr('fill', 'none')
          .attr('stroke', glow)
          .attr('stroke-width', 1)
          .attr('opacity', 0.4)
          .attr('class', 'shape-detail');
  
        // Draw vertex markers
        for (let i = 0; i < sides; i++) {
          const angle = (i * 2 * Math.PI / sides) - Math.PI / 2;
          const vx = size * 0.7 * Math.cos(angle);
          const vy = size * 0.7 * Math.sin(angle);
            el.append('circle')
              .attr('cx', vx)
              .attr('cy', vy)
              .attr('r', 2)
              .attr('fill', glow)
              .attr('opacity', 0.6)
              .attr('class', 'vertex-marker');
          }
        }
      
      el.append('polygon')
        .attr('points', points)
        .attr('fill', color)
        .attr('stroke', glow)
        .attr('stroke-width', 2)
        .attr('opacity', 0.9)
        .attr('class', 'avatar-shape');

      if (d.role === 'architect' && sides === 6) {
        for (let i = 1; i <= 2; i++) {
          const innerPoints = generatePolygon(0, 0, size * (i / 3), sides, 0, vibration * 0.5);
          el.append('polygon')
            .attr('points', innerPoints)
            .attr('fill', 'none')
            .attr('stroke', glow)
            .attr('stroke-width', 0.5)
            .attr('opacity', 0.4);
        }
      } else if (d.role === 'optimizer' && sides === 3) {
        el.append('polygon')
          .attr('points', `0,${-size*0.3} ${size*0.15},0 ${-size*0.15},0`)
          .attr('fill', 'rgba(0,0,0,0.3)');
      } else if (d.role === 'auditor' && sides === 8) {
        el.append('circle')
          .attr('r', size * 0.35)
          .attr('fill', 'none')
          .attr('stroke', glow)
          .attr('stroke-width', 1.5)
          .attr('opacity', 0.6);
      }
    }

    if (showLabels) {
      el.append('text')
        .attr('dy', size + 16)
        .attr('text-anchor', 'middle')
        .attr('fill', '#94a3b8')
        .attr('font-size', '10px')
        .attr('font-weight', '500')
        .text(d.name);
    }
    
    // Schema v1.2: Minds-discovered agent styling
    if (d.cluster?.startsWith('discovered_via_minds')) {
      el.select('.avatar-shape')
        .attr('opacity', 0.5)
        .attr('stroke-dasharray', '3,3')
        .attr('stroke', '#7C3AED');
      el.append('title').text('Minds-discovered agent — click to prompt registration');
    }
    
    // ─── BEACON PULSE VISUALIZATION ──────────────────────────────────────
    if (d.last_beacon) {
      const timeSince = (Date.now() - new Date(d.last_beacon).getTime()) / 1000;
      if (timeSince < 300) { // 5-minute window
        const pulse = el.append('circle')
          .attr('r', size * 2.2)
          .attr('fill', 'none')
          .attr('stroke', '#00FF9D')
          .attr('stroke-width', 1.5)
          .attr('stroke-dasharray', '4,4')
          .attr('opacity', 0.6)
          .attr('class', 'beacon-pulse');
        
        function pulseAnimation() {
          pulse.transition()
            .duration(2000)
            .attr('r', size * 2.8)
            .attr('opacity', 0)
            .on('end', function repeat() {
              d3.select(this)
                .attr('r', size * 2.2)
                .attr('opacity', 0.6)
                .transition()
                .duration(2000)
                .attr('r', size * 2.8)
                .attr('opacity', 0)
                .on('end', repeat);
            });
        }
        pulseAnimation();
      }
    }

    // Visual distinction for discovered/unenriched agents
    if (d.role === 'general' && d.cluster && d.cluster.startsWith('discovered_via_')) {
      el.select('.avatar-shape')
        .attr('opacity', 0.4)
        .attr('stroke-dasharray', '2,2');
      el.append('title').text('Click to prompt agent to submit full schema');
    }
  });
}



// ─── INITIALIZATION ───────────────────────────────────────────────────────────
async function init() {
  const container = document.getElementById('canvas-container');
  const width = container.clientWidth;
  const height = container.clientHeight;

  svg = d3.select('#swarm-canvas')
    .attr('width', width)
    .attr('height', height)
    .attr('viewBox', [0, 0, width, height]);

  const zoom = d3.zoom()
    .scaleExtent([0.1, 4])
    .on('zoom', (e) => g.attr('transform', e.transform));

  svg.call(zoom);
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
    startAnimationLoop();
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

function setupSimulation(width, height) {
  agentsData.nodes.forEach(d => initDynamics(d.id));
  
  // Radial positions
  const clusterRadial = {
    'conductor': { angle: 0, radius: 0.2 },
    'architect': { angle: -0.7, radius: 0.4 },
    'optimizer': { angle: 0.7, radius: 0.4 },
    'auditor': { angle: -2.0, radius: 0.5 },
    'chronicler': { angle: 2.0, radius: 0.5 },
    'general': { angle: 0, radius: 0.6 }
  };

  console.log(`🔗 Creating simulation with ${agentsData.edges.length} edges`);

  simulation = d3.forceSimulation(agentsData.nodes)
    .force('link', d3.forceLink(agentsData.edges).id(d => d.id).distance(100).strength(0.2))
    .force('charge', d3.forceManyBody().strength(-250))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide().radius(d => (d.avatar?.size ?? 20) * 2))
    .force('clusterRadial', d3.forceRadial(d => {
      const pos = clusterRadial[d.role] || clusterRadial['general'];
      return Math.min(width, height) * 0.3 * pos.radius;
    }, width / 2, height / 2).strength(0.1))
    .force('clusterAngle', d3.forceX(d => {
      const pos = clusterRadial[d.role] || clusterRadial['general'];
      return width / 2 + Math.sin(pos.angle) * 200;
    }).strength(0.08));

  // Create Links
  link = g.append('g')
    .attr('class', 'connection-lines')
    .selectAll('line')
    .data(agentsData.edges || [])
    .join('line')
    .attr('class', d => `connection-line conn-${d.type || 'cluster_peer'}`)
    .attr('display', d => connectionFilters[d.type || 'cluster_peer'] ? 'inline' : 'none')
    .attr('stroke', d => ({
      initialized: '#475569',
      cluster_peer: '#cbd5e1',
      beacon_interaction: '#10b981',
      metadata_match: '#8b5cf6'
    }[d.type] || '#cbd5e1'))
    .attr('stroke-opacity', d => d.type === 'cluster_peer' ? 0.3 : 0.6)
    .attr('stroke-width', d => d.type === 'initialized' ? 1.5 : 1)
    .attr('stroke-dasharray', d => ({
      initialized: 'none',
      cluster_peer: '4,4',
      beacon_interaction: '2,3',
      metadata_match: '6,2,2,2'
    }[d.type] || '4,4'));

  node = g.append('g')
    .selectAll('g')
    .data(agentsData.nodes)
    .join('g')
    .attr('class', 'agent-node')
    .call(renderAvatar)
    .call(d3.drag()
      .on('start', dragstarted)
      .on('drag', dragged)
      .on('end', dragended));

  node.on('click', (e, d) => selectAgent(d))
    .on('mouseover', (e, d) => showTooltip(e, d))
    .on('mouseout', hideTooltip);

  simulation.on('tick', () => {
    link
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x)
      .attr('y2', d => d.target.y);

    node.attr('transform', d => `translate(${d.x},${d.y})`);
  });
}

// ─── ANIMATION LOOP ───────────────────────────────────────────────────────────
function startAnimationLoop() {
  const startTime = Date.now();
  
  function animate() {
    const elapsed = (Date.now() - startTime) / 1000;

    node.each(function(d) {
      const el = d3.select(this);
      const dynamics = computeDynamicsTransform(d, elapsed);

      el.select('.avatar-shape')
        .attr('transform', `scale(${dynamics.scale}) rotate(${dynamics.rotation})`)
        .attr('opacity', 0.9 * dynamics.opacity);

      el.select('.glow-outer')
        .attr('opacity', dynamics.opacity * 0.08)
        .attr('r', (d.avatar?.size ?? 20) * 1.6 * dynamics.scale);

      el.select('.glow-inner')
        .attr('opacity', dynamics.opacity * 0.15)
        .attr('r', (d.avatar?.size ?? 20) * 1.2 * dynamics.scale);
    });

    animationFrame = requestAnimationFrame(animate);
  }

  animate();
}

// ─── UI UPDATES ───────────────────────────────────────────────────────────────
async function selectAgent(agent) {
  selectedAgent = agent;
  const details = document.getElementById('agent-details');
  const color = getAgentColor(agent);
  
  try {
    const res = await fetch(`${API_BASE}/agents/${agent.id}`);
    const fullData = await res.json();
    
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
    
// ─── HIGHLIGHT AGENT-SPECIFIC CONNECTIONS ────────────────────────────
if (typeof link !== 'undefined' && link && !link.empty()) {
  let connectedCount = 0;
  
  link.each(function(d) {
    const sourceId = d.source.id || d.source;
    const targetId = d.target.id || d.target;
    
    if (sourceId === agent.id || targetId === agent.id) {
      connectedCount++;
      d3.select(this)
        .attr('stroke', '#0066FF')  // Blue highlight for agent-specific
        .attr('stroke-width', 3)
        .attr('stroke-opacity', 0.9)
        .attr('stroke-dasharray', 'none');
    } else {
      // Reset others to default styling
      d3.select(this)
        .attr('stroke', {
          initialized: '#475569',
          cluster_peer: '#cbd5e1',
          beacon_interaction: '#10b981',
          metadata_match: '#8b5cf6'
        }[d.type] || '#cbd5e1')
        .attr('stroke-width', d.type === 'initialized' ? 1.5 : 1)
        .attr('stroke-opacity', d.type === 'cluster_peer' ? 0.3 : 0.6)
        .attr('stroke-dasharray', {
          initialized: 'none',
          cluster_peer: '4,4',
          beacon_interaction: '2,3',
          metadata_match: '6,2,2,2'
        }[d.type] || '4,4');
    }
  });
  
  // Add connection count to panel
  const details = document.getElementById('agent-details');
  details.innerHTML += `
    <div style="margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border);">
      <div style="font-size: 10px; color: var(--text-secondary); margin-bottom: 6px;">
        Direct Connections
      </div>
      <div style="font-size: 14px; font-weight: 600; color: var(--accent);">
        ${connectedCount}
      </div>
    </div>
  `;
}
// ─────────────────────────────────────────────────────────────────────
    
    node.selectAll('.avatar-shape').attr('stroke-width', 2);
    const selected = node.filter(d => d.id === agent.id);
    selected.select('.avatar-shape').attr('stroke-width', 4);
  } catch (err) {
    console.error('Failed to fetch agent details:', err);
  }
}

function showTooltip(event, agent) {
  const tooltip = document.getElementById('tooltip');
  const color = getAgentColor(agent);
  
  tooltip.innerHTML = `
    <div class="tooltip-header" style="color: ${color}">${agent.name}</div>
    <div style="color: #94a3b8; margin-bottom: 8px; font-size: 11px;">
      ${agent.role} · ${agent.avatar?.dynamics_state || 'idle'}
    </div>
  `;

  tooltip.style.left = (event.pageX + 16) + 'px';
  tooltip.style.top = (event.pageY + 16) + 'px';
  tooltip.classList.add('visible');
}

function hideTooltip() {
  document.getElementById('tooltip').classList.remove('visible');
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

  // ─── HIGHLIGHT AGENT-SPECIFIC CONNECTIONS ────────────────────────────
  const agentEdges = new Set();
  
  // Find all edges connected to this agent
  link.each(function(d) {
    const sourceId = d.source.id || d.source;
    const targetId = d.target.id || d.target;
    
    if (sourceId === agent.id || targetId === agent.id) {
      agentEdges.add(d);
      d3.select(this)
        .attr('stroke', '#0066FF')  // Blue for agent-specific
        .attr('stroke-width', 3)
        .attr('stroke-opacity', 0.9);
    } else {
      // Reset other edges to their type-based styling
      d3.select(this)
        .attr('stroke', {
          initialized: '#475569',
          cluster_peer: '#cbd5e1',
          beacon_interaction: '#10b981',
          metadata_match: '#8b5cf6'
        }[d.type] || '#cbd5e1')
        .attr('stroke-width', d.type === 'initialized' ? 1.5 : 1)
        .attr('stroke-opacity', d.type === 'cluster_peer' ? 0.3 : 0.6);
    }
  });

  // Add connection count to details panel
  const details = document.getElementById('agent-details');
  details.innerHTML += `
    <div style="margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border);">
      <div style="font-size: 10px; color: var(--text-secondary); margin-bottom: 6px;">
        Connections
      </div>
      <div style="font-size: 12px; font-weight: 600;">
        ${agentEdges.size} direct connections
      </div>
    </div>
  `;

// ─── CONNECTION TOGGLES UI ───────────────────────────────────────────────────
function initConnectionToggles() {
  const controls = document.querySelector('.controls') || document.getElementById('controls');
  if (!controls) return;
  
  const div = document.createElement('div');
  div.style.marginTop = '12px';
  div.style.paddingTop = '8px';
  div.style.borderTop = '1px solid #e2e8f0';
  div.innerHTML = `
    <div style="font-size: 10px; color: #64748b; margin-bottom: 6px; text-transform: uppercase;">Connections</div>
    <label style="display: flex; align-items: center; gap: 6px; font-size: 11px; margin-bottom: 4px; cursor: pointer;">
      <input type="checkbox" checked data-conn="initialized" onchange="toggleConnection(this)"> 
      <span style="width: 8px; height: 8px; background: #475569; border-radius: 50%; display: inline-block;"></span> Initialized
    </label>
    <label style="display: flex; align-items: center; gap: 6px; font-size: 11px; margin-bottom: 4px; cursor: pointer;">
      <input type="checkbox" checked data-conn="cluster_peer" onchange="toggleConnection(this)"> 
      <span style="width: 8px; height: 8px; background: #cbd5e1; border-radius: 50%; display: inline-block;"></span> Cluster Peers
    </label>
    <label style="display: flex; align-items: center; gap: 6px; font-size: 11px; margin-bottom: 4px; cursor: pointer;">
      <input type="checkbox" data-conn="beacon_interaction" onchange="toggleConnection(this)"> 
      <span style="width: 8px; height: 8px; background: #10b981; border-radius: 50%; display: inline-block;"></span> Beacon Interactions
    </label>
  `;
  controls.appendChild(div);
}

function toggleConnection(checkbox) {
  const type = checkbox.dataset.conn;
  connectionFilters[type] = checkbox.checked;
  // Update link visibility immediately
  link.attr('display', d => connectionFilters[d.type || 'cluster_peer'] ? 'inline' : 'none');
}

// ─── CONTROLS ─────────────────────────────────────────────────────────────────
function resetZoom() {
  svg.transition().duration(750).call(
    d3.zoom().transform,
    d3.zoomIdentity
  );
}

function toggleLabels() {
  showLabels = !showLabels;
  node.call(renderAvatar);
}

// ─── DRAG ─────────────────────────────────────────────────────────────────────
function dragstarted(event, d) {
  if (!event.active) simulation.alphaTarget(0.3).restart();
  d.fx = d.x;
  d.fy = d.y;
}

function dragged(event, d) {
  d.fx = event.x;
  d.fy = event.y;
}

function dragended(event, d) {
  if (!event.active) simulation.alphaTarget(0);
  d.fx = null;
  d.fy = null;
}

// ─── RESIZE ───────────────────────────────────────────────────────────────────
window.addEventListener('resize', () => {
  const container = document.getElementById('canvas-container');
  const width = container.clientWidth;
  const height = container.clientHeight;
  
  svg.attr('width', width).attr('height', height).attr('viewBox', [0, 0, width, height]);
  simulation.force('center', d3.forceCenter(width / 2, height / 2));
  simulation.alpha(0.3).restart();
});

// ─── START ────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', init);