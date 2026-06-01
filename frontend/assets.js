// Asset configuration
const ASSET_CONFIG = {
  faces: ['face_round', 'face_oval', 'face_sharp'],
  hair: ['hair_short', 'hair_bob', 'hair_twintail', 'hair_long', 'hair_long_flow'],
  eyes: ['eyes_idle', 'eyes_input', 'eyes_output', 'eyes_analysis', 'eyes_verification'],
  mouths: ['mouth_neutral', 'mouth_smile', 'mouth_open'],
  accessories: ['ribbon_red', 'ribbon_blue', 'ribbon_purple'],
  auras: ['glow_circle']
};

class AssetLoader {
  constructor() {
    this.cache = new Map();
    this.loaded = false;
  }

  async loadAll() {
    const promises = [];
    
    for (const [category, assets] of Object.entries(ASSET_CONFIG)) {
      for (const asset of assets) {
        promises.push(this.loadAsset(category, asset));
      }
    }
    
    await Promise.all(promises);
    this.loaded = true;
    console.log(`✅ Loaded ${this.cache.size} anime assets`);
  }

  async loadAsset(category, name) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        this.cache.set(`${category}/${name}`, img);
        resolve();
      };
      img.onerror = () => reject(new Error(`Failed to load ${category}/${name}`));
      img.src = `/assets/anime/${category}/${name}.png`;
    });
  }

  getAsset(category, name) {
    return this.cache.get(`${category}/${name}`);
  }

  // Color-shift hair using canvas composite
  drawColorizedHair(ctx, hairName, x, y, w, h, hue, sat) {
    const img = this.getAsset('hair', hairName);
    if (!img) return;

    // Create offscreen canvas for colorizing
    const offCanvas = document.createElement('canvas');
    offCanvas.width = img.width;
    offCanvas.height = img.height;
    const offCtx = offCanvas.getContext('2d');

    // Draw hair
    offCtx.drawImage(img, 0, 0);
    
    // Colorize using composite
    offCtx.globalCompositeOperation = 'source-atop';
    offCtx.fillStyle = `hsl(${hue}, ${sat * 70}%, 35%)`;
    offCtx.fillRect(0, 0, img.width, img.height);
    
    // Add shading layer
    offCtx.globalCompositeOperation = 'multiply';
    const grad = offCtx.createLinearGradient(0, 0, 0, img.height);
    grad.addColorStop(0, 'rgba(255,255,255,0.3)');
    grad.addColorStop(1, 'rgba(0,0,0,0.4)');
    offCtx.fillStyle = grad;
    offCtx.fillRect(0, 0, img.width, img.height);

    // Draw to main canvas
    ctx.drawImage(offCanvas, x, y, w, h);
  }
}

export const assetLoader = new AssetLoader();