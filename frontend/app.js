/**
Liquid Avatar — Swarm Visualization Engine
D3.js force-directed graph with SVG rendering
Implements schema v1.1: Expertise→Color, Role→Geometry, Activity→Dynamics
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
  const hue = agent.avatar.base_hue;
  const sat = Math.round(agent.avatar.saturation * 100);
  return hslToHex(hue, sat, 55);
}

function getAgentGlow(agent) {
  const hue = agent.avatar.base_hue;
  const sat = Math.round(agent.avatar.saturation * 100);
  return hslToHex(hue, sat, 70);
}

// ─── GEOMETRY GENERATORS ──────────────────────────────────────────────────────
function generatePolygon(cx, cy, r, sides, rotation = 0) {
  const points = [];
  for (let i = 0; i < sides; i++) {
    const angle = (i * 2 * Math.PI / sides) + rotation - Math.PI / 2;
    points.push([cx + r * Math.cos(angle), cy + r * Math.sin(angle)]);
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
  
  const dynamics = agent.avatar.dynamics_state;
  let scale = 1, rotation = 0, opacity = 1;

  switch (dynamics) {
    case 'idle':
      opacity = 0.4 + 0.2 * Math.sin(state.phase + time * state.speed);
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
    const size = d.avatar.size;
    const sides = d.avatar.shape_complexity;
    const isCircle = sides >= 10;

    // Glow effect (outer halo)
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
      const points = generatePolygon(0, 0, size, sides);
      el.append('polygon')
        .attr('points', points)
        .attr('fill', color)
        .attr('stroke', glow)
        .attr('stroke-width', 2)
        .attr('opacity', 0.9)
        .attr('class', 'avatar-shape');

      if (d.role === 'architect' && sides === 6) {
        for (let i = 1; i <= 2; i++) {
          const innerPoints = generatePolygon(0, 0, size * (i / 3), sides);
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
    // Visual distinction for discovered/unenriched agents
    if (d.role === 'general' && d.cluster && d.cluster.startsWith('discovered_via_')) {
      // Dimmed appearance for unenriched agents
      el.select('.avatar-shape')
        .attr('opacity', 0.4)
        .attr('stroke-dasharray', '2,2'); // Dashed border
      
      // Add tooltip hint
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

  } catch (err) {
    console.error('Failed to load swarm data:', err);
    document.getElementById('loading').innerHTML = `
      <div style="color: #ef4444;">Failed to connect to API</div>
      <div style="font-size: 11px; margin-top: 8px;">${err.message}</div>
    `;
  }
}

function setupSimulation(width, height) {
  agentsData.nodes.forEach(d => initDynamics(d.id, d.avatar.dynamics_state));

  simulation = d3.forceSimulation(agentsData.nodes)
    .force('link', d3.forceLink(agentsData.edges)
      .id(d => d.id)
      .distance(120)
      .strength(0.5))
    .force('charge', d3.forceManyBody().strength(-400))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide().radius(d => d.avatar.size * 2.5))
    .force('cluster', d3.forceY(d => {
      const roleY = {
        'conductor': height * 0.3,
        'architect': height * 0.4,
        'optimizer': height * 0.5,
        'auditor': height * 0.6,
        'chronicler': height * 0.7,
        'general': height * 0.55
      };
      return roleY[d.role] || height * 0.5;
    }).strength(0.08));

  link = g.append('g')
    .attr('stroke', '#334155')
    .attr('stroke-opacity', 0.4)
    .selectAll('line')
    .data(agentsData.edges)
    .join('line')
    .attr('stroke-width', 1.5);

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
        .attr('opacity', 0.08 * dynamics.opacity)
        .attr('r', d.avatar.size * 1.6 * dynamics.scale);

      el.select('.glow-inner')
        .attr('opacity', 0.15 * dynamics.opacity)
        .attr('r', d.avatar.size * 1.2 * dynamics.scale);
    });

    animationFrame = requestAnimationFrame(animate);
  }

  animate();
}

// ─── UI UPDATES ───────────────────────────────────────────────────────────────
function selectAgent(agent) {
  selectedAgent = agent;
  const details = document.getElementById('agent-details');
  const color = getAgentColor(agent);
  
  details.innerHTML = `
    <div style="margin-bottom: 12px;">
      <div style="font-size: 16px; font-weight: 600; color: ${color}; margin-bottom: 4px;">
        ${agent.name}
      </div>
      <div style="font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px;">
        ${agent.role} · ${agent.cluster || 'no cluster'}
      </div>
    </div>

    <div style="margin-bottom: 12px;">
      <span class="dynamics-badge dynamics-${agent.avatar.dynamics_state}">
        ${agent.avatar.dynamics_state}
      </span>
    </div>

    <div style="font-size: 11px; color: #94a3b8; margin-bottom: 8px;">Avatar Signature</div>
    <div class="stat-row">
      <span class="stat-label">Hue</span>
      <span class="stat-value">${Math.round(agent.avatar.base_hue)}°</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">Saturation</span>
      <span class="stat-value">${Math.round(agent.avatar.saturation * 100)}%</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">Shape</span>
      <span class="stat-value">${agent.avatar.shape_complexity}-gon</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">Size</span>
      <span class="stat-value">${Math.round(agent.avatar.size)}px</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">Pulse</span>
      <span class="stat-value">${agent.avatar.pulse_rate.toFixed(2)}x</span>
    </div>
  `;

  node.selectAll('.avatar-shape').attr('stroke-width', 2);
  const selected = node.filter(d => d.id === agent.id);
  selected.select('.avatar-shape').attr('stroke-width', 4);
}

function showTooltip(event, agent) {
  const tooltip = document.getElementById('tooltip');
  const color = getAgentColor(agent);
  
  tooltip.innerHTML = `
    <div class="tooltip-header" style="color: ${color}">${agent.name}</div>
    <div style="color: #94a3b8; margin-bottom: 8px; font-size: 11px;">
      ${agent.role} · ${agent.avatar.dynamics_state}
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
  const active = nodes.filter(n => n.avatar.dynamics_state !== 'idle').length;
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

async function refreshData() {
  document.getElementById('loading').style.display = 'block';
  
  try {
    const res = await fetch(`${API_BASE}/swarm/map`);
    const newData = await res.json();
    
    agentsData = newData;
    simulation.nodes(agentsData.nodes);
    simulation.force('link').links(agentsData.edges);
    simulation.alpha(1).restart();

    link = link.data(agentsData.edges).join('line')
      .attr('stroke-width', 1.5);

    node = node.data(agentsData.nodes).join('g')
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

  } catch (err) {
    console.error('Refresh failed:', err);
  } finally {
    document.getElementById('loading').style.display = 'none';
  }
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