import { assetLoader } from './assets.js';

function drawPaperdollAvatar(ctx, agent, x, y, size) {
  if (!assetLoader.loaded) return;

  ctx.save();
  ctx.translate(x, y);

  const schema = agent.avatar || {};
  const hue = schema.base_hue ?? 180;
  const sat = schema.saturation ?? 0.75;
  const complexity = schema.shape_complexity ?? 5;
  const dynamics = schema.dynamics_state || 'idle';

  // 1. Draw aura/glow
  const aura = assetLoader.getAsset('auras', 'glow_circle');
  if (aura) {
    ctx.globalAlpha = 0.25;
    ctx.drawImage(aura, -size * 1.5, -size * 1.5, size * 3, size * 3);
    ctx.globalAlpha = 1.0;
  }

  // 2. Draw hair BACK layer (behind face)
  const hairBack = getHairAsset(complexity, 'back');
  if (hairBack) {
    assetLoader.drawColorizedHair(ctx, hairBack, -size * 1.2, -size * 1.0, size * 2.4, size * 2.0, hue, sat);
  }

  // 3. Draw face base
  const faceIndex = Math.min(Math.floor(complexity / 4) + 1, 3);
  const face = assetLoader.getAsset('faces', `face_${['round', 'oval', 'sharp'][faceIndex - 1]}`);
  if (face) {
    // Apply skin tone tint based on hue
    ctx.globalCompositeOperation = 'source-atop';
    ctx.fillStyle = `hsl(${(hue + 160) % 360}, 15%, 90%)`;
    ctx.fillRect(-size, -size * 0.8, size * 2, size * 1.8);
    ctx.globalCompositeOperation = 'source-over';
    
    ctx.drawImage(face, -size, -size * 0.8, size * 2, size * 1.8);
  }

  // 4. Draw eyes
  const eyeType = `eyes_${dynamics}`;
  const eyes = assetLoader.getAsset('eyes', eyeType) || assetLoader.getAsset('eyes', 'eyes_idle');
  if (eyes) {
    ctx.drawImage(eyes, -size * 0.45, -size * 0.25, size * 0.9, size * 0.5);
  }

  // 5. Draw mouth
  const mouthType = dynamics === 'output' ? 'mouth_smile' : 
                    dynamics === 'input' ? 'mouth_open' : 'mouth_neutral';
  const mouth = assetLoader.getAsset('mouths', mouthType);
  if (mouth) {
    ctx.drawImage(mouth, -size * 0.2, size * 0.3, size * 0.4, size * 0.2);
  }

  // 6. Draw hair FRONT layer (bangs/fringe)
  const hairFront = getHairAsset(complexity, 'front');
  if (hairFront) {
    assetLoader.drawColorizedHair(ctx, hairFront, -size * 1.1, -size * 1.1, size * 2.2, size * 1.2, hue, sat);
  }

  // 7. Draw accessories (ribbons for twin tails)
  if (complexity > 4 && complexity <= 7) {
    const ribbonColor = hue > 180 ? 'ribbon_blue' : 'ribbon_red';
    const ribbon = assetLoader.getAsset('accessories', ribbonColor);
    if (ribbon) {
      ctx.drawImage(ribbon, -size * 1.15, -size * 0.1, size * 0.4, size * 0.4);
      ctx.drawImage(ribbon, size * 0.75, -size * 0.1, size * 0.4, size * 0.4);
    }
  }

  ctx.restore();
}

function getHairAsset(complexity, layer) {
  if (layer === 'back') {
    if (complexity <= 3) return 'hair_short';
    if (complexity <= 5) return 'hair_bob';
    if (complexity <= 7) return 'hair_twintail';
    return 'hair_long_flow';
  }
  // Front layer (bangs)
  return `hair_${complexity <= 5 ? 'bob' : 'long'}_01`;
}

export { drawPaperdollAvatar };