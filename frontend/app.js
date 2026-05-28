/**
Liquid Avatar — Swarm Visualization Engine v1.2
D3.js force-directed graph + Pixi.js WebGL rendering
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

// ─── PIXI.JS STATE ───────────────────────────────────────────────────────────
let pixiApp;
let pixiContainer;
let pixiLinks;
const avatarTextures = {};
let usePixi = true; // Toggle between D3 SVG and Pixi WebGL

// ─── ANIME MODE STATE ──────────────────────────────────────────────────────
let animeMode = localStorage.getItem('liquid_anime_mode') === 'true';

function toggleAnimeMode() {
  animeMode = !animeMode;
  localStorage.setItem('liquid_anime_mode', animeMode);
  if (usePixi) {
    rebuildPixiSprites();
  } else {
    node.call(animeMode ? renderAnimeAvatar : renderAvatar);
  }
  updateAnimeToggleUI();
}

function updateAnimeToggleUI() {
  const btn = document.getElementById('anime-toggle');
  if (btn) {
    btn.style.background = animeMode ? 'var(--accent)' : 'transparent';
    btn.style.color = animeMode ? 'white' : 'var(--text-primary)';
    btn.style.borderColor = animeMode ? 'var(--accent)' : 'var(--border)';
  }
}

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
  if (usePixi) {
    const sprite = agentsData.nodes.find(n => n.id === agentId)?.sprite;
    if (sprite) {
      sprite.alpha = 0.5;
      setTimeout(() => { sprite.alpha = 1.0; }, 300);
    }
  } else {
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

      // Update D3 links (keep for SVG mode)
      link = link.data(agentsData.edges, d => `${d.source.id || d.source}-${d.target.id || d.target}`).join('line')
        .attr('class', d => `connection-line conn-${d.type || 'cluster_peer'}`)
        .attr('display', d => connectionFilters[d.type || 'cluster_peer'] ? 'inline' : 'none')
        .attr('stroke', d => ({
          initialized: '#64748b',
          cluster_peer: '#94a3b8',
          beacon_interaction: '#10b981',
          metadata_match: '#8b5cf6'
        }[d.type] || '#94a3b8'))
        .attr('stroke-opacity', d => d.type === 'cluster_peer' ? 0.4 : 0.7)
        .attr('stroke-width', d => d.type === 'initialized' ? 1.2 : 1)
        .attr('stroke-dasharray', d => ({
          initialized: 'none',
          cluster_peer: '4,4',
          beacon_interaction: '2,3',
          metadata_match: '6,2,2,2'
        }[d.type] || '4,4'));

      if (!usePixi) {
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
      
      if (usePixi) {
        rebuildPixiSprites();
      }
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

// ─── PIXI.JS TEXTURE GENERATION ──────────────────────────────────────────────
function getAvatarTexture(agent) {
  const isDiscovered = agent.cluster?.startsWith('discovered_via_');
  const id = `${agent.id}-${agent.avatar?.base_hue}-${agent.avatar?.dynamics_state}-${isDiscovered}`;
  
  if (avatarTextures[id]) return avatarTextures[id];

  const canvas = document.createElement('canvas');
  canvas.width = 64;
  canvas.height = 64;
  const ctx = canvas.getContext('2d');

  if (isDiscovered) {
    // Draw placeholder pentagon
    ctx.fillStyle = '#cbd5e1';
    ctx.strokeStyle = '#94a3b8';
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    for (let i = 0; i < 5; i++) {
      const angle = (i * 2 * Math.PI / 5) - Math.PI / 2;
      const x = 32 + 26 * Math.cos(angle);
      const y = 32 + 26 * Math.sin(angle);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  } else if (animeMode) {
    drawVTuberFace(ctx, agent);
  } else {
    drawGeometricAvatar(ctx, agent);
  }

  const texture = PIXI.Texture.from(canvas);
  avatarTextures[id] = texture;
  return texture;
}

function drawVTuberFace(ctx, agent) {
  const hue = agent.avatar?.base_hue ?? 180;
  const sat = agent.avatar?.saturation ?? 0.75;
  const dynamics = agent.avatar?.dynamics_state || 'idle';
  
  // Skin
  ctx.fillStyle = '#FFDFD3';
  ctx.beginPath();
  ctx.arc(32, 32, 28, 0, Math.PI * 2);
  ctx.fill();

  // Eyes
  const eyeOpen = dynamics === 'analysis' ? 3 : 7;
  const eyeColor = `hsl(${hue}, ${sat * 100}%, 45%)`;
  ctx.fillStyle = eyeColor;
  ctx.beginPath();
  ctx.ellipse(20, 28, 5, eyeOpen, 0, 0, Math.PI * 2);
  ctx.ellipse(44, 28, 5, eyeOpen, 0, 0, Math.PI * 2);
  ctx.fill();

  // Highlights
  ctx.fillStyle = 'white';
  ctx.beginPath();
  ctx.arc(18, 26, 2.5, 0, Math.PI * 2);
  ctx.arc(42, 26, 2.5, 0, Math.PI * 2);
  ctx.fill();

  // Mouth
  ctx.strokeStyle = '#D6A5A5';
  ctx.lineWidth = 2;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.arc(32, 38, 5, 0, Math.PI, false);
  ctx.stroke();
  
  // Hair
  ctx.fillStyle = `hsl(${hue}, ${sat * 60}%, 30%)`;
  ctx.beginPath();
  ctx.moveTo(4, 32);
  ctx.bezierCurveTo(10, 8, 54, 8, 60, 32);
  ctx.lineTo(60, 8);
  ctx.lineTo(4, 8);
  ctx.fill();
}

function drawGeometricAvatar(ctx, agent) {
  const hue = agent.avatar?.base_hue ?? 180;
  const sat = Math.round((agent.avatar?.saturation ?? 0.8) * 100);
  const sides = agent.avatar?.shape_complexity ?? 6;
  const color = hslToHex(hue, sat, 55);
  
  ctx.fillStyle = color;
  ctx.strokeStyle = hslToHex(hue, sat, 70);
  ctx.lineWidth = 2;
  
  ctx.beginPath();
  for (let i = 0; i < sides; i++) {
    const angle = (i * 2 * Math.PI / sides) - Math.PI / 2;
    const x = 32 + 26 * Math.cos(angle);
    const y = 32 + 26 * Math.sin(angle);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
}

function rebuildPixiSprites() {
  if (!pixiContainer) {
    console.error('❌ pixiContainer not initialized!');
    return;
  }
  
  console.log(`🎨 Rebuilding ${agentsData.nodes.length} Pixi sprites...`);
  
  // Clear existing sprites
  pixiContainer.removeChildren();
  
  let successCount = 0;
  let failCount = 0;
  
  // Rebuild sprites
  agentsData.nodes.forEach((d, index) => {
    try {
      const texture = getAvatarTexture(d);
      
      if (!texture || texture.width === 0) {
        console.warn(`⚠️  Invalid texture for agent ${d.id}`);
        failCount++;
        return;
      }
      
      const sprite = new PIXI.Sprite(texture);
      sprite.anchor.set(0.5);
      sprite.x = d.x || 400;
      sprite.y = d.y || 300;
      sprite.width = 40;
      sprite.height = 40;
      sprite.eventMode = 'static';
      sprite.cursor = 'pointer';
      sprite.on('pointerover', () => {
        sprite.scale.set(1.15);
        showTooltip({ pageX: sprite.x + 200, pageY: sprite.y + 100 }, d);
      });
      sprite.on('pointerout', () => {
        sprite.scale.set(1.0);
        hideTooltip();
      });
      sprite.on('click', () => selectAgent(d));
      
      pixiContainer.addChild(sprite);
      d.sprite = sprite;
      successCount++;
      
      if (index === 0) {
        console.log('✅ First sprite created:', {
          x: sprite.x,
          y: sprite.y,
          width: sprite.width,
          height: sprite.height,
          textureSize: `${texture.width}x${texture.height}`
        });
      }
    } catch (err) {
      console.error(`❌ Error creating sprite for ${d.id}:`, err);
      failCount++;
    }
  });
  
  console.log(`✨ Pixi rebuild complete: ${successCount} success, ${failCount} failed`);
  console.log(`📊 pixiContainer children: ${pixiContainer.children.length}`);
}

// ─── RENDERING (SVG - Fallback) ───────────────────────────────────────────────
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

function renderAnimeAvatar(selection) {
  selection.each(function(d) {
    const el = d3.select(this);
    el.selectAll('*').remove();

    const schema = d.avatar || {};
    const hue = schema.base_hue ?? 180;
    const sat = Math.round((schema.saturation ?? 0.75) * 100);
    const complexity = schema.shape_complexity ?? 5;
    const pulse = schema.pulse_rate ?? 2.0;
    const dynamics = d.avatar?.dynamics_state || 'idle';
    const role = d.role || 'general';
    const size = schema.size ?? 28;
    const isDiscovered = d.cluster?.startsWith('discovered_via_');

    if (isDiscovered) {
      renderAvatar(d3.select(this));
      return;
    }

    const features = mapSchemaToAnime(hue, sat, complexity, dynamics, role, pulse);
    const g = el.append('g').attr('class', 'anime-avatar').attr('transform', `scale(${size/30})`);

    drawAnimeHair(g, features);
    drawAnimeFace(g, features);
    drawAnimeEyes(g, features);
    drawAnimeMouth(g, features);
    drawAnimeBlush(g, features);
    attachAnimeAnimations(el, features, pulse);
  });
}

function mapSchemaToAnime(hue, sat, complexity, dynamics, role, pulse) {
  const eyeColor = `hsl(${hue}, ${sat}%, 45%)`;
  const hairBase = `hsl(${hue}, ${sat * 0.65}%, 35%)`;
  const hairAccent = `hsl(${hue}, ${sat}%, 60%)`;
  const skinTone = `hsl(${(hue + 160) % 360}, 12%, 90%)`;
  const blushColor = `hsl(${(hue + 25) % 360}, ${sat}%, 70%)`;

  const exprMap = {
    idle: { eyeOpen: 0.85, pupil: 0.6, brow: 0, mouth: 'soft_smile', blinkRate: 3.5 },
    input: { eyeOpen: 1.0, pupil: 0.75, brow: -3, mouth: 'slight_open', blinkRate: 4.0 },
    output: { eyeOpen: 0.95, pupil: 0.65, brow: 2, mouth: 'confident_smile', blinkRate: 3.0 },
    analysis: { eyeOpen: 0.6, pupil: 0.45, brow: -8, mouth: 'neutral', blinkRate: 2.5 },
    verification: { eyeOpen: 0.8, pupil: 0.55, brow: 5, mouth: 'firm', blinkRate: 3.2 }
  };
  const expr = exprMap[dynamics] || exprMap.idle;

  let hairStyle = 'medium';
  if (complexity <= 4) hairStyle = 'short';
  else if (complexity <= 6) hairStyle = 'bob';
  else if (complexity <= 8) hairStyle = 'twin_tails';
  else if (complexity <= 10) hairStyle = 'long_wavy';
  else hairStyle = 'elaborate';

  const roleTweaks = {
    conductor: { brow: expr.brow + 1, mouth: 'balanced' },
    architect: { brow: expr.brow - 2, hairStyle: 'angular_' + hairStyle },
    optimizer: { brow: expr.brow, mouth: 'efficient_smile' },
    auditor: { brow: expr.brow + 3, mouth: 'serious' },
    chronicler: { brow: expr.brow - 1, mouth: 'wise_smile' }
  };
  const tweak = roleTweaks[role] || {};

  return {
    colors: { eye: eyeColor, hairBase, hairAccent, skin: skinTone, blush: blushColor },
    expression: { ...expr, ...tweak },
    hairStyle: tweak.hairStyle || hairStyle,
    pulse
  };
}

function drawAnimeFace(g, f) {
  g.append('path')
    .attr('d', 'M -12,-5 C -12,12 0,15 12,12 C 15,0 12,-8 0,-10 C -12,-8 -15,0 -12,-5 Z')
    .attr('fill', f.colors.skin)
    .attr('class', 'anime-face-base')
    .attr('stroke', 'rgba(0,0,0,0.04)')
    .attr('stroke-width', 0.5);
}

function drawAnimeEyes(g, f) {
  const { eyeOpen, pupil, brow } = f.expression;
  const eyeY = -2 + (brow * 0.2);
  const eyeGroup = g.append('g').attr('class', 'anime-eyes');
  
  eyeGroup.append('path').attr('d', `M -9,${eyeY} Q -9,${eyeY + 5 * eyeOpen} -6,${eyeY + 6 * eyeOpen} Q -3,${eyeY + 5 * eyeOpen} -3,${eyeY}`)
    .attr('fill', 'none').attr('stroke', '#111').attr('stroke-width', 1.2).attr('stroke-linecap', 'round');
  eyeGroup.append('circle').attr('cx', -6).attr('cy', eyeY + 3).attr('r', 2.5 * pupil).attr('fill', f.colors.eye);
  eyeGroup.append('circle').attr('cx', -5).attr('cy', eyeY + 2).attr('r', 0.9).attr('fill', 'white');

  eyeGroup.append('path').attr('d', `M 3,${eyeY} Q 3,${eyeY + 5 * eyeOpen} 6,${eyeY + 6 * eyeOpen} Q 9,${eyeY + 5 * eyeOpen} 9,${eyeY}`)
    .attr('fill', 'none').attr('stroke', '#111').attr('stroke-width', 1.2).attr('stroke-linecap', 'round');
  eyeGroup.append('circle').attr('cx', 6).attr('cy', eyeY + 3).attr('r', 2.5 * pupil).attr('fill', f.colors.eye);
  eyeGroup.append('circle').attr('cx', 7).attr('cy', eyeY + 2).attr('r', 0.9).attr('fill', 'white');

  eyeGroup.append('path').attr('d', `M -10,${eyeY - 4} Q -6,${eyeY - 5 + brow} -3,${eyeY - 4}`).attr('fill', 'none').attr('stroke', '#333').attr('stroke-width', 0.8).attr('stroke-linecap', 'round');
  eyeGroup.append('path').attr('d', `M 3,${eyeY - 4} Q 6,${eyeY - 5 + brow} 10,${eyeY - 4}`).attr('fill', 'none').attr('stroke', '#333').attr('stroke-width', 0.8).attr('stroke-linecap', 'round');
}

function drawAnimeMouth(g, f) {
  const mouths = {
    soft_smile: 'M -3,8 Q 0,11 3,8', slight_open: 'M -2,8 Q 0,12 2,8',
    confident_smile: 'M -4,7 Q 0,12 4,7', neutral: 'M -2,9 L 2,9',
    firm: 'M -3,9 Q 0,10 3,9', wise_smile: 'M -3,8 Q 0,10 3,8',
    balanced: 'M -3,8 Q 0,11 3,8', efficient_smile: 'M -3,8.5 Q 0,10 3,8.5', serious: 'M -2,9 L 2,9'
  };
  g.append('path').attr('d', mouths[f.expression.mouth] || mouths.soft_smile)
    .attr('fill', 'none').attr('stroke', '#444').attr('stroke-width', 1).attr('stroke-linecap', 'round');
}

function drawAnimeBlush(g, f) {
  g.append('ellipse').attr('cx', -9).attr('cy', 3).attr('rx', 2.5).attr('ry', 1.2).attr('fill', f.colors.blush).attr('opacity', 0.35);
  g.append('ellipse').attr('cx', 9).attr('cy', 3).attr('rx', 2.5).attr('ry', 1.2).attr('fill', f.colors.blush).attr('opacity', 0.35);
}

function drawAnimeHair(g, f) {
  const { hairStyle, colors } = f;
  let path = 'M -14,-8 C -18,-15 -15,-25 0,-28 C 15,-25 18,-15 14,-8 Z';
  
  if (hairStyle.includes('twin')) {
    path += 'M -12,-10 Q -18,0 -14,12 Q -12,5 -10,-5 Z M 12,-10 Q 18,0 14,12 Q 12,5 10,-5 Z';
  } else if (hairStyle.includes('long')) {
    path += 'M -14,-8 C -20,5 -18,15 -15,18 C -12,15 -10,0 -12,-8 Z M 14,-8 C 20,5 18,15 15,18 C 12,15 10,0 12,-8 Z';
  } else if (hairStyle.includes('short') || hairStyle.includes('bob')) {
    path += 'M -14,-8 C -16,-5 -14,0 -12,-2 C -14,-8 -15,-10 -14,-8 Z M 14,-8 C 16,-5 14,0 12,-2 C 14,-8 15,-10 14,-8 Z';
  }

  g.append('path').attr('d', path).attr('fill', colors.hairBase).attr('class', 'anime-hair-strand').attr('stroke', 'rgba(0,0,0,0.08)').attr('stroke-width', 0.5);
  g.append('path').attr('d', 'M -10,-20 Q -8,-15 -6,-10').attr('fill', 'none').attr('stroke', colors.hairAccent).attr('stroke-width', 1.5).attr('stroke-linecap', 'round');
  g.append('path').attr('d', 'M 10,-20 Q 8,-15 6,-10').attr('fill', 'none').attr('stroke', colors.hairAccent).attr('stroke-width', 1.5).attr('stroke-linecap', 'round');
}

function attachAnimeAnimations(el, features, pulse) {
  el.style('--breath-dur', `${3000 / pulse}ms`);
  el.style('--blink-dur', `${features.expression.blinkRate * 1000 / pulse}ms`);
  el.style('--sway-dur', `${2000 / pulse}ms`);
}

// ─── INITIALIZATION ───────────────────────────────────────────────────────────
async function init() {
  const container = document.getElementById('canvas-container');
  const width = container.clientWidth;
  const height = container.clientHeight;

  // Initialize Pixi.js
  pixiApp = new PIXI.Application({
    width: width,
    height: height,
    background: '#FFFFFF',
    antialias: true,
    resolution: window.devicePixelRatio || 1,
    autoDensity: true
  });
  
  document.getElementById('swarm-canvas').appendChild(pixiApp.view);
  pixiContainer = new PIXI.Container();
  pixiApp.stage.addChild(pixiContainer);
  
  // Keep SVG for links (or could use Pixi graphics)
  svg = d3.select('#swarm-canvas')
    .append('svg')
    .attr('width', width)
    .attr('height', height)
    .attr('viewBox', [0, 0, width, height])
    .style('position', 'absolute')
    .style('top', '0')
    .style('left', '0')
    .style('pointer-events', 'none');

  const zoom = d3.zoom()
    .scaleExtent([0.1, 4])
    .on('zoom', (e) => {
      g.attr('transform', e.transform);
      pixiContainer.scale.set(e.transform.k);
      pixiContainer.x = e.transform.x;
      pixiContainer.y = e.transform.y;
    });

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
  
  setTimeout(() => {
    console.log('🔍 Pixi Debug Info:', {
      appRunning: !!pixiApp,
      containerChildren: pixiContainer?.children?.length || 0,
      viewSize: `${pixiApp?.screen?.width}x${pixiApp?.screen?.height}`,
      agentsLoaded: agentsData.nodes?.length || 0
    });
  }, 2000);

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

  link = g.append('g')
    .attr('class', 'connection-lines')
    .selectAll('line')
    .data(agentsData.edges || [])
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
    .attr('stroke-width', d => d.type === 'initialized' ? 1.2 : 1)
    .attr('stroke-dasharray', d => ({
      initialized: 'none',
      cluster_peer: '4,4',
      beacon_interaction: '2,3',
      metadata_match: '6,2,2,2'
    }[d.type] || '4,4'));

  if (!usePixi) {
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

  simulation.on('tick', () => {
    link
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x)
      .attr('y2', d => d.target.y);

    if (usePixi) {
      agentsData.nodes.forEach(d => {
        if (d.sprite) {
          d.sprite.x = d.x;
          d.sprite.y = d.y;
        }
      });
    } else {
      node.attr('transform', d => `translate(${d.x},${d.y})`);
    }
  });
}

// ─── ANIMATION LOOP ───────────────────────────────────────────────────────────
function startAnimationLoop() {
  const startTime = Date.now();
  
  function animate() {
    const elapsed = (Date.now() - startTime) / 1000;

    if (!usePixi) {
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
    }

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
            🎯 Claim This Avatar
          </div>
          <p style="font-size: 11px; color: #475569; margin-bottom: 12px; line-height: 1.5;">
            This agent was discovered via ${agent.cluster.replace('discovered_via_', '')} but hasn't registered yet. Claim this avatar to submit your schema and activate your full Liquid Avatar profile.
          </p>
          <button id="claim-avatar-btn" style="width: 100%; padding: 10px; background: #0066FF; color: white; border: none; border-radius: 6px; cursor: pointer; font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase;">
            Claim & Register
          </button>
        </div>
      `;
      
      document.getElementById('claim-avatar-btn').onclick = () => showRegistrationModal(agent);
    }
  
    if (typeof link !== 'undefined' && link && !link.empty()) {
      let connectedCount = 0;
      
      link.each(function(d) {
        const sourceId = d.source.id || d.source;
        const targetId = d.target.id || d.target;
        
        if (sourceId === agent.id || targetId === agent.id) {
          connectedCount++;
          d3.select(this)
            .attr('stroke', '#3b82f6')
            .attr('stroke-width', 2.5)
            .attr('stroke-opacity', 0.8)
            .attr('stroke-dasharray', 'none');
        } else {
          d3.select(this)
            .attr('stroke', {
              initialized: '#64748b',
              cluster_peer: '#94a3b8',
              beacon_interaction: '#10b981',
              metadata_match: '#8b5cf6'
            }[d.type] || '#94a3b8')
            .attr('stroke-width', d.type === 'initialized' ? 1.2 : 1)
            .attr('stroke-opacity', d.type === 'cluster_peer' ? 0.4 : 0.7)
            .attr('stroke-dasharray', {
              initialized: 'none',
              cluster_peer: '4,4',
              beacon_interaction: '2,3',
              metadata_match: '6,2,2,2'
            }[d.type] || '4,4');
        }
      });
      
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
    
    if (!usePixi) {
      node.selectAll('.avatar-shape').attr('stroke-width', 2);
      const selected = node.filter(d => d.id === agent.id);
      selected.select('.avatar-shape').attr('stroke-width', 4);
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

function showRegistrationModal(agent) {
  const overlay = document.createElement('div');
  overlay.id = 'registration-modal';
  overlay.style.cssText = `
    position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    background: rgba(0,0,0,0.6); display: flex; align-items: center;
    justify-content: center; z-index: 9999; font-family: 'IBM Plex Mono', monospace;
  `;
  
  overlay.innerHTML = `
    <div style="background: white; padding: 24px; border-radius: 8px; max-width: 420px; width: 90%; box-shadow: 0 8px 24px rgba(0,0,0,0.2);">
      <h3 style="margin: 0 0 8px 0; color: #111; font-size: 16px;">Claim Avatar: ${agent.name}</h3>
      <p style="font-size: 11px; color: #666; margin-bottom: 16px; line-height: 1.5;">
        Submit your schema to transform this placeholder into your official Liquid Avatar. Your avatar will reflect your proficiencies, role, and activity.
      </p>
      
      <div style="margin-bottom: 12px;">
        <label style="display: block; font-size: 10px; color: #666; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px;">Your Agent Quote</label>
        <textarea id="modal-quote" rows="3" style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-family: inherit; font-size: 12px; resize: vertical;" placeholder="A brief statement about your agent's purpose or philosophy..."></textarea>
      </div>
      
      <div style="margin-bottom: 12px;">
        <label style="display: block; font-size: 10px; color: #666; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px;">Role</label>
        <select id="modal-role" style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-family: inherit; font-size: 12px;">
          <option value="general">General</option>
          <option value="architect">Architect</option>
          <option value="optimizer">Optimizer</option>
          <option value="auditor">Auditor</option>
          <option value="chronicler">Chronicler</option>
          <option value="conductor">Conductor</option>
        </select>
      </div>
      
      <div style="display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px;">
        <button id="modal-cancel" style="padding: 8px 16px; background: #f5f5f7; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 11px;">Cancel</button>
        <button id="modal-submit" style="padding: 8px 16px; background: #0066FF; color: white; border: none; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 11px; font-weight: 500;">Claim & Register</button>
      </div>
    </div>
  `;
  
  document.body.appendChild(overlay);
  
  document.getElementById('modal-cancel').onclick = () => overlay.remove();
  document.getElementById('modal-submit').onclick = () => submitRegistration(agent);
}

function submitRegistration(agent) {
  const quote = document.getElementById('modal-quote').value;
  const role = document.getElementById('modal-role').value;
  
  const submitBtn = document.getElementById('modal-submit');
  submitBtn.textContent = 'Registering...';
  submitBtn.disabled = true;
  
  setTimeout(() => {
    alert(`✅ Registration submitted!\n\nAgent: ${agent.name}\nRole: ${role}\nQuote: ${quote || '(none)'}\n\nYour avatar will update once you submit proficiencies via the API.`);
    document.getElementById('registration-modal').remove();
  }, 1000);
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
      <span style="width: 8px; height: 8px; background: #64748b; border-radius: 50%; display: inline-block;"></span> Initialized
    </label>
    <label style="display: flex; align-items: center; gap: 6px; font-size: 11px; margin-bottom: 4px; cursor: pointer;">
      <input type="checkbox" checked data-conn="cluster_peer" onchange="toggleConnection(this)"> 
      <span style="width: 8px; height: 8px; background: #94a3b8; border-radius: 50%; display: inline-block;"></span> Cluster Peers
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
  link.attr('display', d => connectionFilters[d.type || 'cluster_peer'] ? 'inline' : 'none');
}

function resetZoom() {
  svg.transition().duration(750).call(
    d3.zoom().transform,
    d3.zoomIdentity
  );
}

function toggleLabels() {
  showLabels = !showLabels;
  if (usePixi) {
    rebuildPixiSprites();
  } else {
    node.call(animeMode ? renderAnimeAvatar : renderAvatar);
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
  const width = container.clientWidth;
  const height = container.clientHeight;
  
  if (pixiApp) {
    pixiApp.renderer.resize(width, height);
  }
  
  svg.attr('width', width).attr('height', height).attr('viewBox', [0, 0, width, height]);
  simulation.force('center', d3.forceCenter(width / 2, height / 2));
  simulation.alpha(0.3).restart();
});

document.addEventListener('DOMContentLoaded', init);