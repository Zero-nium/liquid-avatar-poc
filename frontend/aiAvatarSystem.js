/**
AI Avatar System v3.3 - In-Memory Cache + Click-to-Render
Prevents frame-by-frame network calls.
*/
const AI_CONFIG = {
  cachePrefix: 'ai_avatar_',
  maxCacheSize: 100
};

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
        if (key.startsWith(AI_CONFIG.cachePrefix)) localStorage.removeItem(key);
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

const AISystem = {
  initialized: false,
  queue: new Map(),
  memoryCache: new Map(), // 🚀 CRITICAL: Prevents network calls on every animation frame

  async init() {
    if (this.initialized) return;
    await AvatarCache.init();
    this.initialized = true;
    console.log('✅ AISystem initialized (v3.3 - In-Memory Cache)');
  }, // <--- Comma required here

  async getCachedAvatar(agentId) {
    if (!this.initialized) await this.init();
  
    // 1. Check IN-MEMORY cache first (instant, 0 network cost)
    if (this.memoryCache.has(agentId)) {
      const cached = this.memoryCache.get(agentId);
      // Return null if we've already determined this agent has no render
      return cached === 'NOT_FOUND' ? null : cached;
    }
  
    // 2. Check server cache (Source of Truth)
    try {
      const res = await fetch(`/api/avatars/${agentId}`);
      if (res.ok) {
        const data = await res.json();
        const url = data.imageUrl;
        
        // Save to memory AND local cache
        this.memoryCache.set(agentId, url);
        await AvatarCache.set(agentId, {
          imageUrl: url,
          schemaSignature: data.schemaSignature || {}
        });
        return url;
      } else if (res.status === 404) {
        // Cache the "not found" state to prevent repeated network calls
        this.memoryCache.set(agentId, 'NOT_FOUND');
        return null;
      }
    } catch (err) {
      // Silently fail and fall back
    }
  
    // 3. Fallback to local IndexedDB/localStorage cache
    const localCached = await AvatarCache.get(agentId);
    if (localCached && localCached.imageUrl) {
      this.memoryCache.set(agentId, localCached.imageUrl);
      return localCached.imageUrl;
    }
  
    // 4. Not rendered yet: cache this state and return null
    this.memoryCache.set(agentId, 'NOT_FOUND');
    return null;
  },

  async triggerRender(agentId) {
    if (this.queue.has(agentId)) {
      console.log(`⏳ Render already in progress for ${agentId}`);
      return this.queue.get(agentId);
    }
  
    const promise = (async () => {
      try {
        console.log(`🎨 Manually requesting render for ${agentId}...`);
        const res = await fetch(`/api/avatars/${agentId}/generate`, { 
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        });
        
        if (!res.ok) {
          const errText = await res.text().catch(() => 'Unknown error');
          throw new Error(`Backend error ${res.status}: ${errText}`);
        }
        
        const data = await res.json();
        if (!data.imageUrl) throw new Error('No imageUrl in response');
        
        // Cache result in memory (overwrites 'NOT_FOUND') and locally
        this.memoryCache.set(agentId, data.imageUrl);
        await AvatarCache.set(agentId, {
          imageUrl: data.imageUrl,
          schemaSignature: data.schemaSignature || {}
        });
        
        console.log(`✅ Render complete and cached for ${agentId}`);
        return data.imageUrl;
      } catch (err) {
        console.error(`❌ Render failed for ${agentId}:`, err.message);
        return null;
      } finally {
        this.queue.delete(agentId);
      }
    })();
  
    this.queue.set(agentId, promise);
    return promise;
  },

  async clearCache(agentId) {
    this.memoryCache.delete(agentId);
    await AvatarCache.clear(agentId);
    console.log(`🗑️ Cache cleared for ${agentId}`);
  }, // <--- Comma required here

  async clearAllCache() {
    this.memoryCache.clear();
    await AvatarCache.clearAll();
    console.log('🗑️ All avatar cache cleared');
  }
};

if (typeof window !== 'undefined') {
  window.AISystem = AISystem;
  console.log('✅ AISystem attached to window (v3.3)');
}