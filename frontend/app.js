/**
Liquid Avatar — Swarm Visualization Engine v1.2
Hybrid Rendering: D3.js Physics + Canvas 2D (Paperdoll) + AI-Generated (AnimeX) + SVG (Geometric)
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
let width, height;

// ─── RENDER MODE STATE ───────────────────────────────────────────────────────
// Modes: 'geometric' (SVG) | 'anime' (Canvas Paperdoll) | 'animex' (AI-Generated)
let renderMode = localStorage.getItem('liquid_render_mode') || 'geometric';
let canvas, ctx;

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
  
  if (renderMode === 'geometric') {
    const nodeSelection = svg.selectAll('.agent-node').filter(d => d.id === agentId);
    if (!nodeSelection.empty()) {
      const agentData = nodeSelection.data()[0];
      agentData.last_beacon = data.timestamp;
      renderAvatar(nodeSelection);
    }
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

      if (renderMode === 'geometric') {
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
      }

      updateStats();
    }
  } catch (err) {
    console.error('Failed to load swarm data:', err);
  }
}

// ─── AGENT FILTERING ─────────────────────────────────────────────────────────
/**
 * Filter agents for rendering based on mode and registration status.
 * - Canvas modes (anime/animex): Only registered agents (no 'discovered_via_*' clusters)
 * - SVG mode: All agents (including discovered placeholders)
 */
function getRenderableAgents() {
  if (renderMode === 'geometric') {
    return agentsData.nodes; // Show all in SVG mode
  }
  
  // Canvas modes: filter out discovered/unregistered agents
  return agentsData.nodes.filter(agent => {
    const cluster = agent.cluster || '';
    return !cluster.startsWith('discovered_via_');
  });
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
    const vibOffset = vibration * Math.sin(Date.now() * 0.005 + i);
    points.push([cx + (r + vibOffset) * Math.cos(angle), cy + (r + vibOffset) * Math.sin(angle)]);
  }
  return points.map(p => p.join(',')).join(' ');
}

// ─── DYNAMICS ─────────────────────────────────────────────────────────────────
const dynamicsState = new Map();

function initDynamics(agentId) {
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
      if (agent.role === 'chronicler' || agent.role === 'chronicle') {
        opacity = 0.85;
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

// ─── CANVAS RENDERING (Direct Drawing - Paperdoll Anime) ─────────────────
function drawAnimeFaceOnCanvas(ctx, agent, x, y, size) {
  const hue = agent.avatar?.base_hue ?? 180;
  const sat = agent.avatar?.saturation ?? 0.75;
  const complexity = agent.avatar?.shape_complexity ?? 5;
  const dynamics = agent.avatar?.dynamics_state || 'idle';
  
  const r = size * 1.2;
  
  ctx.save();
  ctx.translate(x, y);

  // 1. Soft glow/aura
  const glowGrad = ctx.createRadialGradient(0, 0, r * 0.3, 0, 0, r * 2.0);
  glowGrad.addColorStop(0, `hsla(${hue}, ${sat*100}%, 60%, 0.2)`);
  glowGrad.addColorStop(1, 'transparent');
  ctx.fillStyle = glowGrad;
  ctx.beginPath();
  ctx.arc(0, 0, r * 2.0, 0, Math.PI * 2);
  ctx.fill();

  // 2. Hair (behind face)
  const hairColor = `hsl(${hue}, ${sat*70}%, 35%)`;
  const hairShadow = `hsl(${hue}, ${sat*60}%, 25%)`;
  const hairHighlight = `hsl(${hue}, ${sat*50}%, 50%)`;
  
  ctx.fillStyle = hairShadow;
  ctx.beginPath();
  ctx.arc(0, -r * 0.3, r * 0.95, Math.PI, 0, true);
  if (complexity > 7) {
    ctx.lineTo(r * 0.8, r * 1.0);
    ctx.quadraticCurveTo(0, r * 1.3, -r * 0.8, r * 1.0);
  } else {
    ctx.lineTo(r * 0.6, r * 0.45);
    ctx.quadraticCurveTo(0, r * 0.7, -r * 0.6, r * 0.45);
  }
  ctx.closePath();
  ctx.fill();
  
  ctx.fillStyle = hairColor;
  ctx.beginPath();
  ctx.arc(0, -r * 0.35, r * 0.9, Math.PI, 0, true);
  if (complexity > 7) {
    ctx.lineTo(r * 0.75, r * 0.95);
    ctx.quadraticCurveTo(0, r * 1.25, -r * 0.75, r * 0.95);
  } else {
    ctx.lineTo(r * 0.55, r * 0.4);
    ctx.quadraticCurveTo(0, r * 0.65, -r * 0.55, r * 0.4);
  }
  ctx.closePath();
  ctx.fill();
  
  // Bangs/fringe
  ctx.beginPath();
  ctx.moveTo(-r * 0.55, -r * 0.2);
  ctx.quadraticCurveTo(-r * 0.2, -r * 0.05, 0, -r * 0.12);
  ctx.quadraticCurveTo(r * 0.2, -r * 0.05, r * 0.55, -r * 0.2);
  ctx.quadraticCurveTo(r * 0.3, r * 0.2, r * 0.12, r * 0.08);
  ctx.quadraticCurveTo(0, r * 0.28, -r * 0.12, r * 0.08);
  ctx.quadraticCurveTo(-r * 0.3, r * 0.2, -r * 0.55, -r * 0.2);
  ctx.closePath();
  ctx.fill();
  
  // Hair shine
  ctx.fillStyle = hairHighlight;
  ctx.globalAlpha = 0.5;
  ctx.beginPath();
  ctx.ellipse(-r * 0.2, -r * 0.6, r * 0.25, r * 0.07, -0.25, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalAlpha = 1.0;
  
  // Twin tails
  if (complexity > 4 && complexity <= 7) {
    ctx.fillStyle = hairColor;
    ctx.beginPath();
    ctx.arc(-r * 0.75, r * 0.2, r * 0.28, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(r * 0.75, r * 0.2, r * 0.28, 0, Math.PI * 2);
    ctx.fill();
    
    ctx.fillStyle = `hsl(${(hue + 180) % 360}, ${sat*80}%, 50%)`;
    ctx.beginPath();
    ctx.moveTo(-r * 0.8, r * 0.08);
    ctx.lineTo(-r * 1.0, r * 0.2);
    ctx.lineTo(-r * 0.85, r * 0.38);
    ctx.lineTo(-r * 0.7, r * 0.2);
    ctx.closePath();
    ctx.fill();
    
    ctx.beginPath();
    ctx.moveTo(r * 0.8, r * 0.08);
    ctx.lineTo(r * 1.0, r * 0.2);
    ctx.lineTo(r * 0.85, r * 0.38);
    ctx.lineTo(r * 0.7, r * 0.2);
    ctx.closePath();
    ctx.fill();
  }

  // 3. Face base
  const skinGrad = ctx.createRadialGradient(-r*0.25, -r*0.25, 0, 0, 0, r * 0.85);
  skinGrad.addColorStop(0, '#FFE8D6');
  skinGrad.addColorStop(0.5, '#FFD4C0');
  skinGrad.addColorStop(1, '#F0C0B0');
  ctx.fillStyle = skinGrad;
  
  ctx.beginPath();
  ctx.moveTo(-r * 0.55, -r * 0.15);
  ctx.bezierCurveTo(-r * 0.65, r * 0.25, -r * 0.35, r * 0.8, 0, r * 0.85);
  ctx.bezierCurveTo(r * 0.35, r * 0.8, r * 0.65, r * 0.25, r * 0.55, -r * 0.15);
  ctx.arc(0, -r * 0.25, r * 0.6, Math.PI, 0, true);
  ctx.closePath();
  ctx.fill();

  // 4. Eyes
  const eyeColor = `hsl(${hue}, ${sat*100}%, 50%)`;
  const eyeDark = `hsl(${hue}, ${sat*100}%, 25%)`;
  const eyeY = -r * 0.1;
  const eyeOpen = dynamics === 'analysis' ? 0.5 : (dynamics === 'output' ? 1.05 : 1.0);
  
  [-1, 1].forEach(side => {
    const ex = side * r * 0.32;
    ctx.save();
    ctx.translate(ex, eyeY);
    ctx.scale(1, eyeOpen);
    
    ctx.fillStyle = '#FFF';
    ctx.beginPath();
    ctx.ellipse(0, 0, r * 0.23, r * 0.27, 0, 0, Math.PI * 2);
    ctx.fill();
    
    ctx.strokeStyle = '#2a2a2a';
    ctx.lineWidth = 2.2;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(-r * 0.25, 0);
    ctx.quadraticCurveTo(0, -r * 0.28, r * 0.25, 0);
    ctx.stroke();
    
    const irisGrad = ctx.createRadialGradient(0, 0, 0, 0, 0, r * 0.2);
    irisGrad.addColorStop(0, eyeDark);
    irisGrad.addColorStop(0.5, eyeColor);
    irisGrad.addColorStop(1, `hsl(${hue}, ${sat*80}%, 35%)`);
    
    ctx.fillStyle = irisGrad;
    ctx.beginPath();
    ctx.arc(0, 0, r * 0.2, 0, Math.PI * 2);
    ctx.fill();
    
    ctx.fillStyle = '#1a1a1a';
    ctx.beginPath();
    ctx.arc(0, 0, r * 0.09, 0, Math.PI * 2);
    ctx.fill();
    
    ctx.fillStyle = 'rgba(255,255,255,0.95)';
    ctx.beginPath();
    ctx.arc(-r * 0.07, -r * 0.07, r * 0.07, 0, Math.PI * 2);
    ctx.fill();
    
    ctx.fillStyle = 'rgba(255,255,255,0.7)';
    ctx.beginPath();
    ctx.arc(r * 0.06, r * 0.09, r * 0.04, 0, Math.PI * 2);
    ctx.fill();
    
    ctx.restore();
  });

  // 5. Blush
  ctx.fillStyle = `hsla(${(hue+15) % 360}, ${sat*90}%, 75%, 0.3)`;
  ctx.filter = 'blur(1px)';
  ctx.beginPath();
  ctx.ellipse(-r * 0.48, r * 0.2, r * 0.14, r * 0.08, -0.1, 0, Math.PI * 2);
  ctx.ellipse(r * 0.48, r * 0.2, r * 0.14, r * 0.08, 0.1, 0, Math.PI * 2);
  ctx.fill();
  ctx.filter = 'none';

  // 6. Mouth
  ctx.strokeStyle = '#C48B8B';
  ctx.lineWidth = 1.8;
  ctx.lineCap = 'round';
  ctx.beginPath();
  if (dynamics === 'output') {
    ctx.arc(0, r * 0.38, r * 0.1, 0.1, Math.PI - 0.1);
  } else if (dynamics === 'idle') {
    ctx.moveTo(-r * 0.06, r * 0.41);
    ctx.quadraticCurveTo(0, r * 0.45, r * 0.06, r * 0.41);
  } else {
    ctx.moveTo(-r * 0.04, r * 0.41);
    ctx.lineTo(r * 0.04, r * 0.41);
  }
  ctx.stroke();

  ctx.restore();
}

function renderCanvasFrame() {
  if (!ctx || renderMode !== 'anime') return;
  
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = '#FFFFFF';
  ctx.fillRect(0, 0, width, height);
  
  const renderableAgents = getRenderableAgents();
  
  renderableAgents.forEach(d => {
    if (!d.x || !d.y) return;
    
    const size = d.avatar?.size ?? 28;
    drawAnimeFaceOnCanvas(ctx, d, d.x, d.y, size);
    
    if (showLabels) {
      ctx.fillStyle = '#64748b';
      ctx.font = '11px "IBM Plex Mono"';
      ctx.textAlign = 'center';
      ctx.fillText(d.name, d.x, d.y + size * 2.8);
    }
  });

  animationFrame = requestAnimationFrame(renderCanvasFrame);
}

// ─── AI AVATAR RENDERING (AnimeX) ───────────────────────────────────────────
async function renderAnimexFrame() {
  if (!ctx || renderMode !== 'animex' || !window.AISystem?.initialized) return;
  
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = '#FFFFFF';
  ctx.fillRect(0, 0, width, height);
  
  const renderableAgents = getRenderableAgents();
  
  // Process nodes in batches to avoid blocking the main thread
  const batchSize = 10;
  for (let i = 0; i < renderableAgents.length; i += batchSize) {
    const batch = renderableAgents.slice(i, i + batchSize);
    
    for (const d of batch) {
      if (!d.x || !d.y) continue;
      
      const size = d.avatar?.size ?? 30;
      
      // For Paperdoll provider, draw directly
      if (window.AISystem?.provider === 'paperdoll') {
        window.AISystem.draw(ctx, d, d.x, d.y, size);
      } else {
        // For remote providers, get cached/async image
        const imgData = await window.AISystem.getAvatar(d);
        
        if (imgData) {
          const img = new Image();
          img.src = imgData;
          if (img.complete) {
            ctx.drawImage(img, d.x - size, d.y - size, size * 2, size * 2);
          } else {
            // Handle async load
            img.onload = () => {
              ctx.drawImage(img, d.x - size, d.y - size, size * 2, size * 2);
            };
          }
        } else {
          // Loading placeholder
          ctx.fillStyle = '#E2E8F0';
          ctx.beginPath();
          ctx.arc(d.x, d.y, size * 0.8, 0, Math.PI * 2);
          ctx.fill();
          ctx.fillStyle = '#64748B';
          ctx.font = '10px monospace';
          ctx.textAlign = 'center';
          ctx.fillText('AI...', d.x, d.y + 4);
        }
      }
      
      if (showLabels) {
        ctx.fillStyle = '#64748b';
        ctx.font = '11px "IBM Plex Mono"';
        ctx.textAlign = 'center';
        ctx.fillText(d.name, d.x, d.y + size * 2.2);
      }
    }
    
    // Yield to browser to maintain responsiveness
    await new Promise(resolve => setTimeout(resolve, 0));
  }
  
  animationFrame = requestAnimationFrame(renderAnimexFrame);
}

// ─── SVG RENDERING (Geometric - Fallback) ────────────────────────────────────
function renderAvatar(selection) {
  selection.each(function(d) {
    const el = d3.select(this);
    el.selectAll('*').remove();

    const isDiscovered = d.cluster && d.cluster.startsWith('discovered_via_');
    
    let color, glow, strokeDasharray, opacity;
    
    if (isDiscovered) {
      color = '#cbd5e1';
      glow = '#94a3b8';
      strokeDasharray = '4,4';
      opacity = 0.4;
    } else {
      color = getAgentColor(d);
      glow = getAgentGlow(d);
      strokeDasharray = 'none';
      opacity = 0.9;
    }

    const size = d.avatar?.size ?? 20;
    const sides = isDiscovered ? 5 : (d.avatar?.shape_complexity ?? 6);
    const isCircle = sides >= 20;

    const hoursSinceReport = d.last_reported 
      ? (Date.now() - new Date(d.last_reported).getTime()) / 3600000 
      : 0;
    const blurAmount = Math.min(hoursSinceReport / 24, 3);
    
    if (blurAmount > 0.5) {
      el.attr('filter', `blur(${blurAmount}px)`);
      el.append('circle').attr('r', size * 1.6).attr('fill', glow).attr('opacity', 0.04).attr('class', 'glow-outer');
      el.append('circle').attr('r', size * 1.2).attr('fill', glow).attr('opacity', 0.08).attr('class', 'glow-inner');
    } else {
      el.append('circle').attr('r', size * 1.6).attr('fill', glow).attr('opacity', 0.08).attr('class', 'glow-outer');
      el.append('circle').attr('r', size * 1.2).attr('fill', glow).attr('opacity', 0.15).attr('class', 'glow-inner');
    }

    if (isCircle) {
      el.append('circle')
        .attr('r', size)
        .attr('fill', color)
        .attr('stroke', glow)
        .attr('stroke-width', 2)
        .attr('opacity', opacity)
        .attr('class', 'avatar-shape');
      
      if (d.role === 'chronicler' && !isDiscovered) {
        const coil = d3.arc().innerRadius(size * 0.3).outerRadius(size * 0.5).startAngle(0).endAngle(Math.PI * 1.5);
        el.append('path').attr('d', coil).attr('fill', glow).attr('opacity', 0.6);
      }
    } else {
      const vibration = (d.role === 'architect' && sides === 6 && !isDiscovered) ? 1.5 : 0;
      const points = generatePolygon(0, 0, size, sides, 0, vibration);
      
      if (sides >= 10 && !isCircle && !isDiscovered) {
        const inner = generatePolygon(0, 0, size * 0.5, sides);
        el.append('polygon').attr('points', inner).attr('fill', 'none').attr('stroke', glow).attr('stroke-width', 1).attr('opacity', 0.4).attr('class', 'shape-detail');
        for (let i = 0; i < sides; i++) {
          const a = (i * 2 * Math.PI / sides) - Math.PI / 2;
          el.append('circle').attr('cx', size * 0.7 * Math.cos(a)).attr('cy', size * 0.7 * Math.sin(a)).attr('r', 2).attr('fill', glow).attr('opacity', 0.6).attr('class', 'vertex-marker');
        }
      }
      
      el.append('polygon')
        .attr('points', points)
        .attr('fill', color)
        .attr('stroke', glow)
        .attr('stroke-width', 2)
        .attr('opacity', opacity)
        .attr('stroke-dasharray', strokeDasharray)
        .attr('class', 'avatar-shape');
      
      if (!isDiscovered) {
        if (d.role === 'architect' && sides === 6) {
          for (let i = 1; i <= 2; i++) {
            const inner = generatePolygon(0, 0, size * (i / 3), sides, 0, vibration * 0.5);
            el.append('polygon').attr('points', inner).attr('fill', 'none').attr('stroke', glow).attr('stroke-width', 0.5).attr('opacity', 0.4);
          }
        } else if (d.role === 'optimizer' && sides === 3) {
          el.append('polygon').attr('points', `0,${-size*0.3} ${size*0.15},0 ${-size*0.15},0`).attr('fill', 'rgba(0,0,0,0.3)');
        } else if (d.role === 'auditor' && sides === 8) {
          el.append('circle').attr('r', size * 0.35).attr('fill', 'none').attr('stroke', glow).attr('stroke-width', 1.5).attr('opacity', 0.6);
        }
      }
    }

    if (showLabels) {
      el.append('text')
        .attr('dy', size + 16)
        .attr('text-anchor', 'middle')
        .attr('fill', '#64748b')
        .attr('font-family', "'IBM Plex Mono', monospace")
        .attr('font-size', '9px')
        .attr('font-weight', '500')
        .attr('letter-spacing', '0.3px')
        .text(d.name);
    }
    
    if (d.last_beacon && !isDiscovered) {
      const age = (Date.now() - new Date(d.last_beacon).getTime()) / 1000;
      if (age < 300) {
        const pulse = el.append('circle').attr('r', size * 2.2).attr('fill', 'none').attr('stroke', '#00FF9D').attr('stroke-width', 1.5).attr('stroke-dasharray', '4,4').attr('opacity', 0.6).attr('class', 'beacon-pulse');
        const anim = () => pulse.transition().duration(2000).attr('r', size * 2.8).attr('opacity', 0).on('end', () => { pulse.attr('r', size * 2.2).attr('opacity', 0.6); anim(); });
        anim();
      }
    }
    
    if (isDiscovered) {
      el.append('title').text(`${d.name}\nDiscovered via ${d.cluster.replace('discovered_via_', '')}\nClick to view details`);
    }

    if (d.cluster === 'discovered_via_ethoswarm') {
      el.append('circle')
        .attr('r', size * 1.3)
        .attr('fill', 'none')
        .attr('stroke', '#a78bfa')
        .attr('stroke-width', 1)
        .attr('opacity', 0.3)
        .attr('class', 'ethoswarm-pulse')
        .attr('stroke-dasharray', '2,4');
      el.append('title').text(`${d.name}\nEthoswarm Agent\nOn-chain: ${d.metadata?.on_chain_id?.slice(0,10)}...`);
    }
  });
}

// ─── INITIALIZATION ───────────────────────────────────────────────────────────
async function init() {
  const container = document.getElementById('canvas-container');
  width = container.clientWidth;
  height = container.clientHeight;

  canvas = document.getElementById('swarm-canvas');
  canvas.width = width;
  canvas.height = height;
  ctx = canvas.getContext('2d');
  
  svg = d3.select('#swarm-svg')
    .attr('width', width)
    .attr('height', height)
    .attr('viewBox', [0, 0, width, height]);

  const zoom = d3.zoom()
    .scaleExtent([0.1, 4])
    .on('zoom', (e) => {
      g.attr('transform', e.transform);
    });

  d3.select('#swarm-canvas').call(zoom);
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
    
    // Initialize AI Avatar System
    if (window.AISystem) {
      await window.AISystem.init();
    }
    
    setupSimulation(width, height);
    
    // Start appropriate render loop
    switchRenderEngine();
    
    connectSwarmWebSocket();
    initConnectionToggles();
    updateModeButton();

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

  console.log(`🔗 Creating simulation with ${agentsData.edges.length} edges`);

  simulation = d3.forceSimulation(agentsData.nodes)
    .force('link', d3.forceLink(agentsData.edges).id(d => d.id).distance(100).strength(0.1))
    .force('charge', d3.forceManyBody().strength(-300))
    .force('center', d3.forceCenter(w / 2, h / 2))
    .force('collision', d3.forceCollide().radius(d => (d.avatar?.size ?? 25) * 2.5))
    .force('clusterRadial', d3.forceRadial(d => {
      const pos = clusterRadial[d.role] || clusterRadial['general'];
      return Math.min(w, h) * 0.3 * pos.radius;
    }, w / 2, h / 2).strength(0.1));

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

  if (renderMode === 'geometric') {
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
  } else {
    // Canvas click handling for anime/animex modes
    canvas.addEventListener('click', (e) => {
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      
      let closest = null;
      let minDist = 40;

      // Only check renderable agents for click detection
      const renderableAgents = getRenderableAgents();
      
      renderableAgents.forEach(d => {
        if (!d.x) return;
        const dist = Math.hypot(d.x - x, d.y - y);
        if (dist < minDist) {
          minDist = dist;
          closest = d;
        }
      });

      if (closest) {
        selectAgent(closest);
      }
    });
  }

  simulation.on('tick', () => {
    link
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x)
      .attr('y2', d => d.target.y);

    if (renderMode === 'geometric') {
      node.attr('transform', d => `translate(${d.x},${d.y})`);
    }
  });
}

// ─── UI & TOGGLES ────────────────────────────────────────────────────────────
function updateModeButton() {
  const btn = document.getElementById('mode-toggle');
  if (!btn) return;
  
  const labels = { geometric: 'GEOMETRIC', anime: 'ANIME', animex: 'ANIMEX' };
  btn.textContent = labels[renderMode];
  
  if (renderMode === 'geometric') {
    btn.style.background = 'transparent';
    btn.style.color = 'var(--text-primary)';
  } else {
    btn.style.background = 'var(--accent)';
    btn.style.color = 'white';
  }
}

function cycleRenderMode() {
  const modes = ['geometric', 'anime', 'animex'];
  const currentIdx = modes.indexOf(renderMode);
  renderMode = modes[(currentIdx + 1) % modes.length];
  localStorage.setItem('liquid_render_mode', renderMode);
  
  updateModeButton();
  switchRenderEngine();
}

function switchRenderEngine() {
  // Clear existing nodes/canvas
  d3.selectAll('.agent-node').remove();
  ctx.clearRect(0, 0, width, height);
  
  if (renderMode === 'animex') {
    canvas.style.display = 'block';
    if (animationFrame) cancelAnimationFrame(animationFrame);
    renderAnimexFrame();
  } else if (renderMode === 'anime') {
    canvas.style.display = 'block';
    if (animationFrame) cancelAnimationFrame(animationFrame);
    renderCanvasFrame();
  } else {
    // geometric mode
    canvas.style.display = 'none';
    if (animationFrame) cancelAnimationFrame(animationFrame);
    
    // Rebuild SVG nodes
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
  }
}

function toggleLabels() {
  showLabels = !showLabels;
  if (renderMode === 'geometric') {
    node.call(renderAvatar);
  }
  // Canvas modes pick up showLabels in next frame automatically
}

function resetZoom() {}

function refreshData() {
  loadSwarmData();
}

async function selectAgent(agent) {
  selectedAgent = agent;
  const details = document.getElementById('agent-details');
  const color = getAgentColor(agent);
  
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
            This agent was discovered via ${agent.cluster.replace('discovered_via_', '')} but hasn't registered yet.
          </p>
        </div>
      `;
    }
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

function initConnectionToggles() {}

function toggleConnection(checkbox) {
  const type = checkbox.dataset.conn;
  connectionFilters[type] = checkbox.checked;
  if (link) {
    link.attr('display', d => connectionFilters[d.type || 'cluster_peer'] ? 'inline' : 'none');
  }
}

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

window.addEventListener('resize', () => {
  const container = document.getElementById('canvas-container');
  width = container.clientWidth;
  height = container.clientHeight;
  
  if (canvas) {
    canvas.width = width;
    canvas.height = height;
  }
  
  svg.attr('width', width).attr('height', height).attr('viewBox', [0, 0, width, height]);
  simulation.force('center', d3.forceCenter(width / 2, height / 2)).alpha(0.3).restart();
});

document.addEventListener('DOMContentLoaded', init);

// Debug: Verify functions are loaded
console.log('✅ app.js loaded');
console.log('✅ cycleRenderMode defined:', typeof cycleRenderMode === 'function');
console.log('✅ updateModeButton defined:', typeof updateModeButton === 'function');
console.log('✅ Current renderMode:', renderMode);