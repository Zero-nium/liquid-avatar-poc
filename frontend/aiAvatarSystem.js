/**
 * AI Avatar System v2.0
 * Providers: Paperdoll (Local), Minds (Remote), OpenRouter (Remote)
 */

const AI_CONFIG = {
  provider: 'paperdoll', // 'paperdoll', 'minds', 'openrouter'
  threshold: 10,
  cachePrefix: 'ai_avatar_',
  maxCacheSize: 50
};

// ─── ASSET LOADER (PAPERDOLL) ───────────────────────────────────────────────
class AssetLoader {
  constructor() {
    this.cache = new Map();
    this.loaded = false;
  }

  async loadAll() {
    const assets = [
      'faces/face_base', 'hair/hair_short', 'hair/hair_twintail', 'hair/hair_long',
      'eyes/eyes_open', 'eyes/eyes_half', 'mouths/mouth_smile', 'mouths/mouth_neutral'
    ];
    await Promise.all(assets.map(p => this._load(p)));
    this.loaded = true;
    console.log(`✅ Paperdoll assets loaded`);
  }

  async _load(path) {
    return new Promise((resolve) => {
      const img = new Image();
      img.src = `/assets/anime/${path}.png`;
      img.onload = () => { this.cache.set(path, img); resolve(); };
      img.onerror = () => resolve();
    });
  }

  get(path) { return this.cache.get(path); }

  drawTinted(ctx, path, x, y, w, h, color) {
    const img = this.get(path);
    if (!img) return;
    
    const off = document.createElement('canvas');
    off.width = img.width; off.height = img.height;
    const oCtx = off.getContext('2d');
    oCtx.drawImage(img, 0, 0);
    oCtx.globalCompositeOperation = 'source-atop';
    oCtx.fillStyle = color;
    oCtx.fillRect(0, 0, img.width, img.height);
    
    ctx.drawImage(off, x, y, w, h);
  }
}

const assetLoader = new AssetLoader();

function drawPaperdollAvatar(ctx, agent, x, y, size) {
  if (!assetLoader.loaded) return;
  const s = agent.avatar || {};
  const hue = s.base_hue ?? 180;
  const sat = s.saturation ?? 0.75;
  const comp = s.shape_complexity ?? 5;
  const dyn = s.dynamics_state || 'idle';
  const r = size * 1.2;

  ctx.save();
  ctx.translate(x, y);

  // Aura
  const g = ctx.createRadialGradient(0, 0, r*0.5, 0, 0, r*2.2);
  g.addColorStop(0, `hsla(${hue}, ${sat*100}%, 60%, 0.3)`);
  g.addColorStop(1, 'transparent');
  ctx.fillStyle = g;
  ctx.beginPath(); ctx.arc(0, 0, r*2.2, 0, Math.PI*2); ctx.fill();

  // Hair
  const hair = comp > 7 ? 'hair_long' : (comp > 5 ? 'hair_twintail' : 'hair_short');
  assetLoader.drawTinted(ctx, `hair/${hair}`, -r*1.2, -r*1.2, r*2.4, r*2.4, `hsl(${hue}, ${sat*70}%, 35%)`);

  // Face
  assetLoader.drawTinted(ctx, 'faces/face_base', -r*0.8, -r*0.8, r*1.6, r*1.6, `hsl(${(hue+160)%360}, 15%, 92%)`);

  // Eyes
  const eyeType = (dyn === 'analysis') ? 'eyes_half' : 'eyes_open';
  assetLoader.drawTinted(ctx, `eyes/${eyeType}`, -r*0.5, -r*0.35, r, r*0.5, `hsl(${hue}, ${sat*100}%, 50%)`);

  // Mouth
  const mouth = (dyn === 'output') ? 'mouth_smile' : 'mouth_neutral';
  const mImg = assetLoader.get(`mouths/${mouth}`);
  if (mImg) ctx.drawImage(mImg, -r*0.2, r*0.2, r*0.4, r*0.2);

  ctx.restore();
}

// ─── AI SYSTEM ───────────────────────────────────────────────────────────────
function getSchemaVector(agent) {
  return {
    hue: agent.avatar?.base_hue ?? 180,
    sat: agent.avatar?.saturation ?? 0.75,
    complexity: agent.avatar?.shape_complexity ?? 5,
    role: agent.role || 'general',
    dynamics: agent.avatar?.dynamics_state || 'idle'
  };
}

function calculateDelta(current, baseline) {
  if (!baseline) return 100;
  const hueD = Math.abs(current.hue - baseline.hue) / 360;
  const satD = Math.abs(current.sat - baseline.sat);
  const compD = Math.abs(current.complexity - baseline.complexity) / 10;
  const catD = (current.role !== baseline.role || current.dynamics !== baseline.dynamics) ? 0.25 : 0;
  return Math.min((hueD + satD + compD + catD) * 100, 100);
}

const AvatarCache = {
  db: null,
  async init() {
    if (!window.indexedDB) return;
    return new Promise((resolve) => {
      const req = indexedDB.open('LiquidAvatarCache', 1);
      req.onupgradeneeded = (e) => {
        const db = e.target.result;
        if (!db.objectStoreNames.contains('avatars')) db.createObjectStore('avatars', { keyPath: 'agentId' });
      };
      req.onsuccess = (e) => { this.db = e.target.result; resolve(); };
      req.onerror = () => resolve();
    });
  },
  async get(id) {
    if (!this.db) return JSON.parse(localStorage.getItem(`${AI_CONFIG.cachePrefix}${id}`) || 'null');
    return new Promise((resolve) => {
      const tx = this.db.transaction('avatars', 'readonly');
      const req = tx.objectStore('avatars').get(id);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => resolve(null);
    });
  },
  async set(id, data) {
    const entry = { agentId: id, ...data, timestamp: Date.now() };
    if (!this.db) {
      localStorage.setItem(`${AI_CONFIG.cachePrefix}${id}`, JSON.stringify(entry));
      return;
    }
    return new Promise((resolve) => {
      const tx = this.db.transaction('avatars', 'readwrite');
      tx.objectStore('avatars').put(entry);
      tx.oncomplete = resolve;
    });
  }
};

const AISystem = {
  provider: 'paperdoll',
  queue: new Map(),

  async init() {
    await AvatarCache.init();
    if (this.provider === 'paperdoll') await assetLoader.loadAll();
    console.log(`✅ AI System initialized: ${this.provider}`);
  },

  switchProvider(newProvider) {
    this.provider = newProvider;
    localStorage.setItem('liquid_ai_provider', newProvider);
    if (newProvider === 'paperdoll' && !assetLoader.loaded) assetLoader.loadAll();
  },

  async getAvatar(agent) {
    if (this.provider === 'paperdoll') return null; // Handled by drawPaperdollAvatar

    const cached = await AvatarCache.get(agent.id);
    const current = getSchemaVector(agent);

    if (cached) {
      if (calculateDelta(current, cached.schemaSignature) < AI_CONFIG.threshold) {
        return cached.imageUrl;
      }
    }

    if (this.queue.has(agent.id)) return this.queue.get(agent.id);

    const promise = (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/ai/render`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Platform-Key': process.env.PLATFORM_API_KEY || 'dev-key' },
          body: JSON.stringify({
            agent_id: agent.id,
            schema: current,
            provider: this.provider,
            reference_artifact_id: cached?.artifactId
          })
        });
        if (!res.ok) throw new Error(`Render failed: ${res.status}`);
        
        const data = await res.json();
        await AvatarCache.set(agent.id, { imageUrl: data.imageUrl, artifactId: data.artifactId, schemaSignature: current });
        return data.imageUrl;
      } catch (err) {
        console.error(`AI render error: ${err.message}`);
        return cached?.imageUrl || '';
      } finally {
        this.queue.delete(agent.id);
      }
    })();

    this.queue.set(agent.id, promise);
    return cached?.imageUrl || '';
  },

  draw(ctx, agent, x, y, size) {
    if (this.provider === 'paperdoll') {
      drawPaperdollAvatar(ctx, agent, x, y, size);
    } else {
      // AI providers handled by app.js render loop via getAvatar()
    }
  }
};

// Init from storage
const savedProvider = localStorage.getItem('liquid_ai_provider');
if (savedProvider) AISystem.provider = savedProvider;

export default AISystem;

// Attach to window for global access (bypass ES module scope)
window.AISystem = AISystem;
console.log('✅ AISystem attached to window');