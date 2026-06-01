/**
 * AI Avatar System v3.0 - OpenRouter Only
 * Features:
 * - Free-tier models only
 * - Persistent caching (IndexedDB + backend)
 * - 10% schema change threshold for re-renders
 * - Testing: clear cache button
 */

const AI_CONFIG = {
  provider: 'openrouter',
  threshold: 10, // % schema change to trigger re-render
  cachePrefix: 'ai_avatar_',
  maxCacheSize: 100,
  // Free-tier models
  openrouter: {
    model: 'meta-llama/llama-3.1-8b-instruct:free',
    max_tokens: 150,
    temperature: 0.3
  },
  huggingface: {
    model: 'stabilityai/stable-diffusion-2-1',
    steps: 20,
    guidance_scale: 7.5
  }
};

// ─── SCHEMA UTILS ───────────────────────────────────────────────────────────
function getSchemaVector(agent) {
  return {
    hue: agent.avatar?.base_hue ?? 180,
    sat: agent.avatar?.saturation ?? 0.75,
    complexity: agent.avatar?.shape_complexity ?? 5,
    role: agent.role || 'general',
    dynamics: agent.avatar?.dynamics_state || 'idle'
  };
}

function calculateSchemaDelta(current, baseline) {
  if (!baseline) return 100;
  const hueD = Math.abs(current.hue - baseline.hue) / 360;
  const satD = Math.abs(current.sat - baseline.sat);
  const compD = Math.abs(current.complexity - baseline.complexity) / 10;
  const catD = (current.role !== baseline.role || current.dynamics !== baseline.dynamics) ? 0.25 : 0;
  return Math.min((hueD + satD + compD + catD) * 100, 100);
}

// ─── CACHE MANAGER (IndexedDB) ──────────────────────────────────────────────
const AvatarCache = {
  db: null,
  
  async init() {
    if (!window.indexedDB) {
      console.warn('⚠️ IndexedDB not supported, using localStorage fallback');
      return;
    }
    return new Promise((resolve) => {
      const request = indexedDB.open('LiquidAvatarCache', 1);
      request.onupgradeneeded = (e) => {
        const db = e.target.result;
        if (!db.objectStoreNames.contains('avatars')) {
          db.createObjectStore('avatars', { keyPath: 'agentId' });
        }
      };
      request.onsuccess = (e) => {
        this.db = e.target.result;
        console.log('✅ AvatarCache initialized (IndexedDB)');
        resolve();
      };
      request.onerror = () => {
        console.warn('⚠️ IndexedDB init failed, using localStorage');
        resolve();
      };
    });
  },
  
  async get(agentId) {
    if (!this.db) {
      const raw = localStorage.getItem(`${AI_CONFIG.cachePrefix}${agentId}`);
      return raw ? JSON.parse(raw) : null;
    }
    return new Promise((resolve) => {
      const tx = this.db.transaction('avatars', 'readonly');
      const req = tx.objectStore('avatars').get(agentId);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => resolve(null);
    });
  },
  
  async set(agentId, data) {
    const entry = { 
      agentId, 
      imageUrl: data.imageUrl, 
      schemaSignature: data.schemaSignature,
      renderedAt: new Date().toISOString(),
      provider: 'openrouter'
    };
    
    if (!this.db) {
      localStorage.setItem(`${AI_CONFIG.cachePrefix}${agentId}`, JSON.stringify(entry));
      return;
    }
    return new Promise((resolve) => {
      const tx = this.db.transaction('avatars', 'readwrite');
      tx.objectStore('avatars').put(entry);
      tx.oncomplete = resolve;
      tx.onerror = () => resolve();
    });
  },
  
  async clear(agentId) {
    if (!this.db) {
      localStorage.removeItem(`${AI_CONFIG.cachePrefix}${agentId}`);
      return;
    }
    return new Promise((resolve) => {
      const tx = this.db.transaction('avatars', 'readwrite');
      tx.objectStore('avatars').delete(agentId);
      tx.oncomplete = resolve;
      tx.onerror = () => resolve();
    });
  },
  
  async clearAll() {
    if (!this.db) {
      Object.keys(localStorage).forEach(key => {
        if (key.startsWith(AI_CONFIG.cachePrefix)) {
          localStorage.removeItem(key);
        }
      });
      return;
    }
    return new Promise((resolve) => {
      const tx = this.db.transaction('avatars', 'readwrite');
      tx.objectStore('avatars').clear();
      tx.oncomplete = resolve;
      tx.onerror = () => resolve();
    });
  }
};

// ─── OPENROUTER PROVIDER ────────────────────────────────────────────────────
class OpenRouterProvider {
  async render(agent) {
    const prompt = this.buildPrompt(agent);
    
    // Step 1: Refine prompt via OpenRouter (free tier)
    const refinedPrompt = await this.refinePrompt(prompt);
    
    // Step 2: Generate image via Hugging Face (free tier)
    const imageUrl = await this.generateImage(refinedPrompt);
    
    return imageUrl;
  }
  
  buildPrompt(agent) {
    const s = agent.avatar || {};
    const hue = s.base_hue ?? 180;
    const sat = s.saturation ?? 0.75;
    const comp = s.shape_complexity ?? 5;
    const dyn = s.dynamics_state || 'idle';
    const role = agent.role || 'general';
    
    // Hair mapping
    const hair = comp <= 4 ? 'short cropped hair' : 
                 comp <= 6 ? 'bob cut with soft bangs' : 
                 comp <= 8 ? 'twin tails with ribbons' : 'long flowing layered hair';
    
    // Expression mapping
    const expr = {
      output: 'bright confident smile, sparkling eyes',
      input: 'focused attentive eyes, slight brow furrow',
      analysis: 'thoughtful half-lidded gaze, concentrated',
      verification: 'sharp alert eyes, precise expression'
    }[dyn] || 'soft neutral expression, gentle eyes';
    
    // Role accessories
    const acc = {
      architect: 'subtle geometric hairpin, creative flair',
      auditor: 'minimalist earpiece, precise styling',
      chronicler: 'delicate ribbon, timeless elegance',
      conductor: 'regal headband, authoritative presence'
    }[role] || '';
    
    return `Anime avatar portrait, chibi style, ${hair}, ${expr}, hair color hsl(${hue}, ${sat*100}%, 35%), soft cel shading, clean linework, white background, no text, no border, game asset style, high quality${acc ? ', ' + acc : ''}`;
  }
  
  async refinePrompt(basePrompt) {
    const res = await fetch('https://openrouter.ai/api/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${process.env.OPENROUTER_API_KEY || ''}`,
        'Content-Type': 'application/json',
        'HTTP-Referer': window.location.origin,
        'X-Title': 'Liquid Avatar'
      },
      body: JSON.stringify({
        model: AI_CONFIG.openrouter.model,
        messages: [
          {
            role: 'system',
            content: 'You are an expert prompt engineer for anime avatar generation. Refine the user\'s prompt to be more vivid, consistent, and optimized for Stable Diffusion image generation. Keep it under 75 words. Output ONLY the refined prompt, no explanations.'
          },
          { role: 'user', content: basePrompt }
        ],
        max_tokens: AI_CONFIG.openrouter.max_tokens,
        temperature: AI_CONFIG.openrouter.temperature
      })
    });
    
    if (!res.ok) {
      console.warn('⚠️ OpenRouter prompt refinement failed, using original');
      return basePrompt;
    }
    
    const data = await res.json();
    return data.choices?.[0]?.message?.content?.trim() || basePrompt;
  }
  
  async generateImage(prompt) {
    const res = await fetch('https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-2-1', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${process.env.HF_API_TOKEN || ''}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        inputs: prompt,
        parameters: {
          width: 256,
          height: 256,
          num_inference_steps: AI_CONFIG.huggingface.steps,
          guidance_scale: AI_CONFIG.huggingface.guidance_scale
        }
      })
    });
    
    if (!res.ok) {
      console.warn('⚠️ Hugging Face generation failed, using placeholder');
      return this.createPlaceholder(prompt);
    }
    
    const blob = await res.blob();
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(reader.result);
      reader.readAsDataURL(blob);
    });
  }
  
  createPlaceholder(prompt) {
    // Extract hue for color consistency
    const hueMatch = prompt.match(/hsl\((\d+)/);
    const hue = hueMatch ? parseInt(hueMatch[1]) : 180;
    
    const canvas = document.createElement('canvas');
    canvas.width = 128;
    canvas.height = 128;
    const ctx = canvas.getContext('2d');
    
    ctx.fillStyle = `hsl(${hue}, 60%, 70%)`;
    ctx.fillRect(0, 0, 128, 128);
    ctx.fillStyle = '#FFF';
    ctx.font = '12px monospace';
    ctx.textAlign = 'center';
    ctx.fillText('AI', 64, 70);
    
    return canvas.toDataURL();
  }
}

// ─── MAIN AISYSTEM ──────────────────────────────────────────────────────────
const AISystem = {
  provider: 'openrouter',
  initialized: false,
  queue: new Map(),
  
  async init() {
    await AvatarCache.init();
    this.initialized = true;
    console.log(`✅ AISystem initialized: ${this.provider}`);
  },
  
  async getAvatar(agent) {
    if (!this.initialized) await this.init();
    
    const cached = await AvatarCache.get(agent.id);
    const currentSig = getSchemaVector(agent);
    
    // Check if re-render needed
    if (cached) {
      const delta = calculateSchemaDelta(currentSig, cached.schemaSignature);
      if (delta < AI_CONFIG.threshold) {
        console.log(`✅ Cache hit for ${agent.id} (delta: ${delta.toFixed(1)}%)`);
        return cached.imageUrl;
      }
      console.log(`🔄 Schema changed ${delta.toFixed(1)}% for ${agent.id}, re-rendering`);
    }
    
    // Prevent duplicate concurrent requests
    if (this.queue.has(agent.id)) {
      return this.queue.get(agent.id);
    }
    
    // Generate new render
    const promise = (async () => {
      try {
        console.log(`🎨 Rendering avatar for ${agent.id} via OpenRouter`);
        const provider = new OpenRouterProvider();
        const imageUrl = await provider.render(agent);
        
        // Cache the result
        await AvatarCache.set(agent.id, {
          imageUrl,
          schemaSignature: currentSig
        });
        
        console.log(`✅ Avatar rendered and cached for ${agent.id}`);
        return imageUrl;
      } catch (err) {
        console.error(`❌ Render failed for ${agent.id}:`, err.message);
        // Return cached version if available, else placeholder
        return cached?.imageUrl || null;
      } finally {
        this.queue.delete(agent.id);
      }
    })();
    
    this.queue.set(agent.id, promise);
    return cached?.imageUrl || null;
  },
  
  // Testing: Clear cache for specific agent
  async clearCache(agentId) {
    await AvatarCache.clear(agentId);
    console.log(`🗑️ Cache cleared for ${agentId}`);
  },
  
  // Testing: Clear all cache
  async clearAllCache() {
    await AvatarCache.clearAll();
    console.log('🗑️ All avatar cache cleared');
  }
};

// Attach to window for global access
if (typeof window !== 'undefined') {
  window.AISystem = AISystem;
  console.log('✅ AISystem attached to window');
}

export default AISystem;