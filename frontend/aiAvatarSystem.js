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
/**
 * Checks if an anime avatar is already rendered and cached on the server.
 * @param {string} agentId - The ID of the agent.
 * @returns {Promise<string|null>} - The image URL if cached, null otherwise.
 */
async function loadCachedAnimeAvatar(agentId) {
  try {
      const response = await fetch(`/api/avatars/${agentId}`);
      if (response.ok) {
          const data = await response.json();
          return data.imageUrl; // e.g., "/storage/avatars/agent-123.png"
      }
  } catch (err) {
      console.warn(`[AvatarSystem] No cached avatar found for ${agentId}`);
  }
  return null;
}

/**
* Manually triggers the server to generate and cache the anime avatar.
* @param {string} agentId - The ID of the agent.
*/
async function triggerAnimeRender(agentId) {
  const btn = document.getElementById(`render-btn-${agentId}`);
  const imgContainer = document.getElementById(`avatar-container-${agentId}`);
  
  if (!btn || !imgContainer) {
      console.error(`[AvatarSystem] UI elements not found for ${agentId}`);
      return;
  }

  // 1. Show loading state
  btn.innerHTML = '<span class="spinner"></span> Generating...';
  btn.disabled = true;
  btn.classList.add('rendering');

  try {
      // 2. Call the backend generation endpoint
      const response = await fetch(`/api/avatars/${agentId}/generate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
      });
      
      if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(errorData.detail || `Generation failed: ${response.status}`);
      }
      
      const data = await response.json();
      
      // 3. On success, replace the button with the rendered image
      if (data.imageUrl) {
          imgContainer.innerHTML = `
              <img src="${data.imageUrl}" alt="Anime Avatar" class="anime-avatar-rendered" loading="lazy" />
          `;
          console.log(`[AvatarSystem] Successfully rendered and cached avatar for ${agentId}`);
      } else {
          throw new Error("No image URL returned from server");
      }
      
  } catch (error) {
      console.error(`[AvatarSystem] Error generating avatar for ${agentId}:`, error);
      // Revert button to allow retry
      btn.innerHTML = '❌ Failed. Click to retry';
      btn.disabled = false;
      btn.classList.remove('rendering');
  }
}

// ── MAIN SYSTEM ────────────────────────────────────────────────────────────
const AISystem = {
  initialized: false,
  queue: new Map(),
  
  async init() {
    if (this.initialized) return;
    await AvatarCache.init();
    this.initialized = true;
    console.log('✅ AISystem initialized (v3.2 - Click-to-Render)');
  },

  /**
   * Checks if an avatar is already rendered. 
   * Checks SERVER first (for cross-user consistency), then falls back to local cache.
   * DOES NOT auto-generate.
   */
  async getCachedAvatar(agentId) {
    if (!this.initialized) await this.init();

    // 1. Check server cache first (Source of Truth)
    try {
      const res = await fetch(`/api/avatars/${agentId}`);
      if (res.ok) {
        const data = await res.json();
        // Sync to local cache for faster subsequent loads
        await AvatarCache.set(agentId, {
          imageUrl: data.imageUrl,
          schemaSignature: data.schemaSignature || {}
        });
        return data.imageUrl;
      }
    } catch (err) {
      // Silently fail and fall back to local cache
    }

    // 2. Fallback to local IndexedDB/localStorage cache
    const localCached = await AvatarCache.get(agentId);
    if (localCached && localCached.imageUrl) {
      return localCached.imageUrl;
    }

    return null; // Not rendered yet
  },

  /**
   * Manually triggers the backend to generate and cache the avatar.
   * Called only when the user clicks an uncached avatar in Anime mode.
   */
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
        
        if (!data.imageUrl) {
          throw new Error('No imageUrl in response');
        }
        
        // Cache result locally for future loads
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
    await AvatarCache.clear(agentId);
    console.log(`🗑️ Cache cleared for ${agentId}`);
  },

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