/**
 * AI Avatar System v3.1 - Backend Integrated
 * Calls backend to generate/render avatars securely.
 * Loaded as classic script (no ES6 modules).
 */

const AI_CONFIG = {
  cachePrefix: 'ai_avatar_',
  maxCacheSize: 100
};

// ─── CACHE MANAGER ──────────────────────────────────────────────────────────
const AvatarCache = {
  db: null,
  
  async init() {
    if (!window.indexedDB) {
      console.warn('⚠️ IndexedDB not available, using localStorage fallback');
      return;
    }
    return new Promise((resolve) => {
      try {
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
      } catch (err) {
        console.warn('⚠️ IndexedDB error:', err);
        resolve();
      }
    });
  },
  
  async get(agentId) {
    // Fallback to localStorage if IndexedDB not available
    if (!this.db) {
      const raw = localStorage.getItem(`${AI_CONFIG.cachePrefix}${agentId}`);
      return raw ? JSON.parse(raw) : null;
    }
    
    return new Promise((resolve) => {
      try {
        const tx = this.db.transaction('avatars', 'readonly');
        const req = tx.objectStore('avatars').get(agentId);
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => resolve(null);
      } catch (err) {
        console.warn('⚠️ Cache get error:', err);
        resolve(null);
      }
    });
  },
  
  async set(agentId, data) {
    const entry = { 
      agentId, 
      imageUrl: data.imageUrl, 
      schemaSignature: data.schemaSignature,
      timestamp: Date.now()
    };
    
    // Fallback to localStorage if IndexedDB not available
    if (!this.db) {
      localStorage.setItem(`${AI_CONFIG.cachePrefix}${agentId}`, JSON.stringify(entry));
      return;
    }
    
    return new Promise((resolve) => {
      try {
        const tx = this.db.transaction('avatars', 'readwrite');
        tx.objectStore('avatars').put(entry);
        tx.oncomplete = resolve;
        tx.onerror = () => resolve();
      } catch (err) {
        console.warn('⚠️ Cache set error:', err);
        resolve();
      }
    });
  },
  
  async clear(agentId) {
    if (!this.db) {
      localStorage.removeItem(`${AI_CONFIG.cachePrefix}${agentId}`);
      return;
    }
    return new Promise((resolve) => {
      try {
        const tx = this.db.transaction('avatars', 'readwrite');
        tx.objectStore('avatars').delete(agentId);
        tx.oncomplete = resolve;
        tx.onerror = () => resolve();
      } catch (err) {
        console.warn('⚠️ Cache clear error:', err);
        resolve();
      }
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
      try {
        const tx = this.db.transaction('avatars', 'readwrite');
        tx.objectStore('avatars').clear();
        tx.oncomplete = resolve;
        tx.onerror = () => resolve();
      } catch (err) {
        console.warn('⚠️ Cache clearAll error:', err);
        resolve();
      }
    });
  }
};

// ── MAIN SYSTEM ────────────────────────────────────────────────────────────
const AISystem = {
  initialized: false,
  queue: new Map(),
  
  async init() {
    if (this.initialized) return;
    await AvatarCache.init();
    this.initialized = true;
    console.log('✅ AISystem initialized (v3.1)');
  },
  
  async getAvatar(agent) {
    if (!this.initialized) {
      await this.init();
    }
    
    // 1. Check local cache first
    const cached = await AvatarCache.get(agent.id);
    if (cached && cached.imageUrl) {
      console.log(`✅ Cache hit for ${agent.id}`);
      return cached.imageUrl;
    }
    
    // 2. Prevent duplicate concurrent requests
    if (this.queue.has(agent.id)) {
      console.log(`⏳ Request already queued for ${agent.id}`);
      return this.queue.get(agent.id);
    }
    
    // 3. Generate via backend
    const promise = (async () => {
      try {
        console.log(`🎨 Requesting render for ${agent.id}...`);
        
        const res = await fetch(`/api/avatars/${agent.id}/generate`, { 
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        });
        
        if (!res.ok) {
          const errText = await res.text().catch(() => 'Unknown error');
          throw new Error(`Backend error ${res.status}: ${errText}`);
        }
        
        const data = await res.json();
        
        if (!data.imageUrl) {
          throw new Error('No imageUrl in response');
        }
        
        // Cache result locally for future loads
        await AvatarCache.set(agent.id, {
          imageUrl: data.imageUrl,
          schemaSignature: data.schemaSignature || {}
        });
        
        console.log(`✅ Render complete for ${agent.id}`);
        return data.imageUrl;
        
      } catch (err) {
        console.error(`❌ Render failed for ${agent.id}:`, err.message);
        // Return null to trigger placeholder in UI
        return null;
      } finally {
        // Always clean up the queue
        this.queue.delete(agent.id);
      }
    })();
    
    // Store promise in queue to prevent duplicate requests
    this.queue.set(agent.id, promise);
    
    // Return null immediately; UI will show placeholder until promise resolves
    return null;
  },
  
  // Testing utility: clear cache for a specific agent
  async clearCache(agentId) {
    await AvatarCache.clear(agentId);
    console.log(`🗑️ Cache cleared for ${agentId}`);
  },
  
  // Testing utility: clear all cached avatars
  async clearAllCache() {
    await AvatarCache.clearAll();
    console.log('🗑️ All avatar cache cleared');
  }
};

// ─── GLOBAL ATTACHMENT (Classic Script Loading) ─────────────────────────────
// This makes AISystem available globally without ES6 modules
if (typeof window !== 'undefined') {
  window.AISystem = AISystem;
  console.log('✅ AISystem attached to window (classic script mode)');
}

// No export statement - this file is loaded as a classic script, not a module