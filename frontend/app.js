/**
Liquid Avatar — Swarm Visualization Engine v1.3
Hybrid Rendering: D3.js Physics + SVG (Geometric) + Canvas (Cached Anime)
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
let width, height;
let renderMode = localStorage.getItem('liquid_render_mode') || 'geometric';
let canvas, ctx;

// ─── CONNECTION FILTERS ──────────────────────────────────────────────────────
const connectionFilters = {
  initialized: true,
  cluster_peer: true,
  beacon_interaction: false,
  metadata_match: false
};

// ─── WEBSOCKET ───────────────────────────────────────────────────────────────
let swarmSocket = null;

function connectSwarmWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/swarm`;
  
  swarmSocket = new WebSocket(wsUrl);
  swarmSocket.onopen = () => console.log('🔌 WebSocket connected');
  swarmSocket.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'agent_updated' || msg.type === 'agent_registered') {
        loadSwarmData();
      }
    } catch (err) {
      console.error('WebSocket parse error:', err);
    }
  };
  swarmSocket.onclose = () => setTimeout(connectSwarmWebSocket, 5000);
}

// ─── DATA LOADING ────────────────────────────────────────────────────────────
async function loadSwarmData() {
  try {
    const res = await fetch(`${API_BASE}/swarm/map`);
    agentsData = await res.json();
    
    if (simulation) {
      simulation.nodes(agentsData.nodes);
      simulation.force('link').links(agentsData.edges);
      simulation.alpha(1).restart();
      
      if (renderMode === 'geometric') {
        node = node.data(agentsData.nodes, d => d.id).join('g')
          .attr('class', 'agent-node')
          .call(renderAvatar)
          .call(d3.drag().on('start', dragstarted).on('drag', dragged).on('end', dragended));
        node.on('click', (e, d) => selectAgent(d))
            .on('mouseover', (e, d) => showTooltip(e, d))
            .on('mouseout', hideTooltip);
      }
      updateStats();
    }
  } catch (err) {
    console.error('Failed to load swarm:', err);
  }
}

// ─── COLOR UTILS ─────────────────────────────────────────────────────────────
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

// ─── SVG RENDERING (Geometric - Full Featured) ───────────────────────────────
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

// ─── CANVAS RENDERING (Anime via In-Memory Cache) ────────────────────────────
async function renderAnimeFrame() {
  if (!ctx || renderMode !== 'anime') return;
  
  // Pastel pink gradient background
  const gradient = ctx.createLinearGradient(0, 0, width, height);
  gradient.addColorStop(0, '#FFF0F5');  // Lavender Blush
  gradient.addColorStop(0.5, '#FFE4E1'); // Misty Rose
  gradient.addColorStop(1, '#FFB6C1');   // Light Pink
  
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, width, height);

  const renderable = agentsData.nodes.filter(a => !a.cluster?.startsWith('discovered_via_'));
  
  for (const d of renderable) {
    if (!d.x || !d.y) continue;
    const size = d.avatar?.size ?? 30;

    const cachedUrl = await window.AISystem?.getCachedAvatar?.(d.id);

    if (cachedUrl && cachedUrl !== 'NOT_FOUND') {
      const img = new Image();
      img.src = cachedUrl;
      
      // Check if image is already loaded and valid (naturalWidth > 0 means it's not a broken 404)
      if (img.complete && img.naturalWidth > 0) {
        ctx.drawImage(img, d.x - size, d.y - size, size * 2, size * 2);
      } else {
        img.onload = () => {
          if (img.naturalWidth > 0) {
            ctx.drawImage(img, d.x - size, d.y - size, size * 2, size * 2);
          }
        };
        img.onerror = () => {
          // Image failed to load (404). Clear it from cache so we stop trying every frame!
          console.warn(`❌ Image failed to load for ${d.id}. Clearing cache.`);
          if (window.AISystem?.memoryCache) {
            window.AISystem.memoryCache.delete(d.id);
          }
          if (window.AvatarCache?.clear) {
            window.AvatarCache.clear(d.id);
          }
        };
      }
    } else {
      // Pastel pink placeholder
      ctx.fillStyle = '#FFB6C1';  // Light Pink
      ctx.beginPath();
      ctx.arc(d.x, d.y, size * 0.8, 0, Math.PI * 2);
      ctx.fill();
      
      // Softer text
      ctx.fillStyle = '#DB7093';  // Pale Violet Red
      ctx.font = '10px "IBM Plex Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText('Click', d.x, d.y + 4);
    }

    if (showLabels) {
      ctx.fillStyle = '#C71585';  // Medium Violet Red
      ctx.font = '11px "IBM Plex Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText(d.name, d.x, d.y + size * 2.2);
    }
  }
  
  animationFrame = requestAnimationFrame(renderAnimeFrame);
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
  
  svg = d3.select('#swarm-svg').attr('width', width).attr('height', height).attr('viewBox', [0, 0, width, height]);
  
  const zoom = d3.zoom().scaleExtent([0.1, 4]).on('zoom', (e) => g.attr('transform', e.transform));
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
    
    if (window.AISystem?.init) {
      await AISystem.init();
    }
    
    setupSimulation(width, height);
    switchRenderEngine();
    connectSwarmWebSocket();
    updateModeButton();
  } catch (err) {
    console.error('Init failed:', err);
    document.getElementById('loading').innerHTML = `<div style="color:#ef4444">Error: ${err.message}</div>`;
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
  
  link = g.append('g').selectAll('line').data(agentsData.edges).join('line')
    .attr('class', d => `connection-line conn-${d.type || 'cluster_peer'}`)
    .attr('display', d => connectionFilters[d.type || 'cluster_peer'] ? 'inline' : 'none')
    .attr('stroke', d => ({ initialized: '#64748b', cluster_peer: '#94a3b8', beacon_interaction: '#10b981' }[d.type] || '#94a3b8'))
    .attr('stroke-opacity', d => d.type === 'cluster_peer' ? 0.4 : 0.7)
    .attr('stroke-width', d => d.type === 'initialized' ? 1.5 : 1)
    .attr('stroke-dasharray', d => ({ initialized: 'none', cluster_peer: '4,4', beacon_interaction: '2,3' }[d.type] || '4,4'));
  
  if (renderMode === 'geometric') {
    node = g.append('g').selectAll('g').data(agentsData.nodes).join('g')
      .attr('class', 'agent-node')
      .call(renderAvatar)
      .call(d3.drag()
        .on('start', dragstarted)
        .on('drag', dragged)
        .on('end', dragended));
    
    node.on('click', function(event, d) {
      console.log('🖱️ SVG node clicked:', d.id);
      selectAgent(d);
    })
    .on('mouseover', (e, d) => showTooltip(e, d))
    .on('mouseout', hideTooltip);

    svg.style('pointer-events', 'auto');
    canvas.style.pointerEvents = 'none';
    canvas.style.display = 'none';
  } else {
    // Anime mode: canvas receives events
    svg.style('pointer-events', 'none');
    svg.style('opacity', '0');
    canvas.style.pointerEvents = 'auto';
    canvas.style.display = 'block';
  }

  canvas.onclick = async (e) => {
    console.log('🖱️ Canvas clicked, renderMode:', renderMode);  // DEBUG LOG

    if (renderMode !== 'anime') {
      console.log('⚠️ Canvas click blocked in geometric mode');
      return;
    }
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    console.log(' Click position:', x, y);  // DEBUG LOG

    let closest = null, minDist = 40;
    agentsData.nodes.filter(a => !a.cluster?.startsWith('discovered_via_')).forEach(d => {
      if (!d.x) return;
      const dist = Math.hypot(d.x - x, d.y - y);
      if (dist < minDist) { minDist = dist; closest = d; }
    });

    if (closest) {
      console.log('🖱️ Canvas click:', closest.id);
      
      const isCached = await window.AISystem?.getCachedAvatar?.(closest.id);
      if (!isCached) {
        console.log(`🎨 Triggering render for ${closest.id}...`);
        await window.AISystem?.triggerRender?.(closest.id);
      } else {
        selectAgent(closest);
      }
    } else {
      console.log('❌ No agent found near click');  // DEBUG LOG
    }
  };
  
  simulation.on('tick', () => {
    link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    if (renderMode === 'geometric') {
      node.attr('transform', d => `translate(${d.x},${d.y})`);
    }
  });
}

function switchRenderEngine() {
  d3.selectAll('.agent-node').remove();
  ctx.clearRect(0, 0, width, height);
  
  if (renderMode === 'anime') {
    canvas.style.display = 'block';
    canvas.style.pointerEvents = 'auto';
    canvas.style.cursor = 'pointer';
    svg.style('pointer-events', 'none');
    svg.style('opacity', '0');
    
    if (animationFrame) cancelAnimationFrame(animationFrame);
    renderAnimeFrame();
  } else {
    canvas.style.display = 'none';
    canvas.style.pointerEvents = 'none';
    canvas.style.cursor = 'default';
    svg.style('pointer-events', 'auto');
    svg.style('opacity', '1'); 
    
    if (animationFrame) cancelAnimationFrame(animationFrame);
    
    node = g.append('g').selectAll('g').data(agentsData.nodes).join('g')
      .attr('class', 'agent-node')
      .call(renderAvatar)
      .call(d3.drag()
        .on('start', dragstarted)
        .on('drag', dragged)
        .on('end', dragended));
    
    node.on('click', function(event, d) {
      console.log('️ SVG node clicked:', d.id);
      selectAgent(d);
    })
    .on('mouseover', (e, d) => showTooltip(e, d))
    .on('mouseout', hideTooltip);
  }
}

// ─── UI TOGGLES ──────────────────────────────────────────────────────────────
function updateModeButton() {
  const btn = document.getElementById('mode-toggle');
  if (!btn) return;
  btn.textContent = renderMode === 'geometric' ? 'GEOMETRIC' : 'ANIME';
  btn.style.background = renderMode === 'geometric' ? 'transparent' : 'var(--accent)';
  btn.style.color = renderMode === 'geometric' ? 'var(--text-primary)' : 'white';
}

function cycleRenderMode() {
  renderMode = renderMode === 'geometric' ? 'anime' : 'geometric';
  localStorage.setItem('liquid_render_mode', renderMode);
  updateModeButton();
  switchRenderEngine();
}

function toggleLabels() {
  showLabels = !showLabels;
  if (renderMode === 'geometric') node.call(renderAvatar);
}

async function clearAvatarCache() {
  try {
    console.log('🗑️ Clearing server-side avatar cache...');
    
    // 1. Clear server-side cache first
    const res = await fetch('/api/avatars', { method: 'DELETE' });
    if (!res.ok) {
      throw new Error(`Server cache clear failed: ${res.status}`);
    }
    
    const data = await res.json();
    console.log(`✅ Server cache cleared: ${data.db_records_deleted} DB records, ${data.files_deleted} files`);
    
    // 2. Clear local cache
    if (window.AISystem?.clearAllCache) {
      AISystem.clearAllCache();
    }
    
    // 3. Reload to see fresh state
    alert(`✅ Cache cleared!\n\nServer: ${data.db_records_deleted} records, ${data.files_deleted} files\nLocal: IndexedDB + Memory cleared\n\nReloading...`);
    location.reload();
    
  } catch (err) {
    console.error('❌ Failed to clear cache:', err);
    alert(`❌ Failed to clear cache: ${err.message}`);
  }
}

// ─── INTERACTIONS ────────────────────────────────────────────────────────────
async function selectAgent(agent) {
  selectedAgent = agent;
  const details = document.getElementById('agent-details');
  const color = getAgentColor(agent);
  
  try {
    const res = await fetch(`${API_BASE}/agents/${agent.id}`);
    const fullData = await res.json();
    const isDiscovered = agent.cluster?.startsWith('discovered_via_');
    
    details.innerHTML = `
      <div style="margin-bottom:12px">
        <div style="font-size:16px;font-weight:600;color:${color};margin-bottom:4px">${fullData.identity?.name || agent.name}</div>
        <div style="font-size:11px;color:#64748b;text-transform:uppercase">${fullData.identity?.role || agent.role || 'general'} · ${agent.cluster || 'no cluster'}</div>
      </div>
      <div style="margin-bottom:12px">
        <span style="display:inline-flex;align-items:center;padding:2px 6px;border:1px solid var(--border);border-radius:0;font-size:9px;text-transform:uppercase">${agent.avatar?.dynamics_state || 'idle'}</span>
      </div>
      <div style="font-size:11px;color:#94a3b8;margin-bottom:8px">Avatar Signature</div>
      <div style="display:flex;justify-content:space-between;padding:2px 0;font-size:10px;border-bottom:1px dashed var(--border)">
        <span style="color:var(--text-secondary)">Hue</span><span>${Math.round(agent.avatar?.base_hue ?? 180)}°</span>
      </div>
      <div style="display:flex;justify-content:space-between;padding:2px 0;font-size:10px;border-bottom:1px dashed var(--border)">
        <span style="color:var(--text-secondary)">Saturation</span><span>${Math.round((agent.avatar?.saturation ?? 0.8) * 100)}%</span>
      </div>
      <div style="display:flex;justify-content:space-between;padding:2px 0;font-size:10px;border-bottom:1px dashed var(--border)">
        <span style="color:var(--text-secondary)">Shape</span><span>${agent.avatar?.shape_complexity ?? 6}-gon</span>
      </div>
      <div style="display:flex;justify-content:space-between;padding:2px 0;font-size:10px;border-bottom:1px dashed var(--border)">
        <span style="color:var(--text-secondary)">Size</span><span>${Math.round(agent.avatar?.size ?? 20)}px</span>
      </div>
      <div style="display:flex;justify-content:space-between;padding:2px 0;font-size:10px;border-bottom:1px dashed var(--border)">
        <span style="color:var(--text-secondary)">Pulse</span><span>${(agent.avatar?.pulse_rate ?? 1.0).toFixed(2)}x</span>
      </div>
      ${isDiscovered ? `
        <div style="margin-top:20px;padding:16px;background:linear-gradient(135deg,#f0f9ff,#e0f2fe);border:1px solid #bae6fd;border-radius:8px">
          <div style="font-size:12px;font-weight:600;color:#0369a1;margin-bottom:8px;text-transform:uppercase">Claim This Avatar</div>
          <p style="font-size:11px;color:#475569;margin-bottom:12px">
            This agent was discovered via ${agent.cluster.replace('discovered_via_', '')} but hasn't registered yet.
          </p>
        </div>
      ` : ''}
    `;

    // Force Re-Render button for testing
    if (!isDiscovered && window.AISystem?.triggerRender) {
      details.innerHTML += `
        <div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--border)">
          <button onclick="window.AISystem.triggerRender('${agent.id}', true)" 
                  style="width:100%;padding:8px;background:#f1f5f9;border:1px solid var(--border);border-radius:4px;cursor:pointer;font-family:var(--font-mono);font-size:10px">
            🔄 Force Re-Render (Test)
          </button>
          <div style="font-size:9px;color:var(--text-muted);margin-top:4px">
            Clears server cache and generates a new image.
          </div>
        </div>
      `;
    }
    
  } catch (err) {
    console.error('Failed to fetch agent:', err);
  }
}

function showTooltip(event, agent) {
  const tooltip = document.getElementById('tooltip');
  const color = getAgentColor(agent);
  
  tooltip.innerHTML = `<div style="font-weight:600;color:${color}">${agent.name}</div><div style="color:#94a3b8;font-size:11px">${agent.role} · ${agent.avatar?.dynamics_state || 'idle'}</div>`;
  tooltip.style.left = (event.pageX + 16) + 'px';
  tooltip.style.top = (event.pageY + 16) + 'px';
  tooltip.classList.add('visible');
}

function hideTooltip() {
  document.getElementById('tooltip').classList.remove('visible');
}

function updateStats() {
  const nodes = agentsData.nodes;
  document.getElementById('stat-count').textContent = nodes.length;
  document.getElementById('stat-active').textContent = nodes.filter(n => n.avatar?.dynamics_state !== 'idle').length;
  document.getElementById('stat-clusters').textContent = [...new Set(nodes.map(n => n.cluster).filter(Boolean))].length;
}

function renderOntology() {
  if (!ontologyData) return;
  document.getElementById('ontology-list').innerHTML = ontologyData.domains.map(d => 
    `<div style="display:flex;align-items:center;gap:8px;padding:2px 0"><div style="width:16px;height:16px;background:${d.spectrum[0]};border:1px solid var(--border)"></div><span>${d.domain}</span></div>`
  ).join('');
}

function renderDynamicsLegend() {
  const states = ['idle', 'input', 'output', 'analysis', 'verification'];
  const desc = { idle: 'Subtle glow', input: 'Inward pulse', output: 'Outward pulse', analysis: 'Rotation', verification: 'Pendulum' };
  document.getElementById('dynamics-legend').innerHTML = states.map(s => 
    `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:11px"><span style="display:inline-flex;align-items:center;padding:2px 6px;border:1px solid var(--border);border-radius:0;font-size:9px">${s}</span><span style="color:#64748b">${desc[s]}</span></div>`
  ).join('');
}

function dragstarted(event, d) {
  if (!event.active) simulation.alphaTarget(0.3).restart();
  d.fx = d.x; d.fy = d.y;
}

function dragged(event, d) {
  d.fx = event.x; d.fy = event.y;
}

function dragended(event, d) {
  if (!event.active) simulation.alphaTarget(0);
  d.fx = null; d.fy = null;
}

window.addEventListener('resize', () => {
  const container = document.getElementById('canvas-container');
  width = container.clientWidth;
  height = container.clientHeight;
  if (canvas) { canvas.width = width; canvas.height = height; }
  svg.attr('width', width).attr('height', height).attr('viewBox', [0, 0, width, height]);
  simulation.force('center', d3.forceCenter(width / 2, height / 2)).alpha(0.3).restart();
});

document.addEventListener('DOMContentLoaded', init);