"""
Unified AI Avatar Gateway
Supports: Minds Protocol, OpenRouter+HuggingFace, Mock
"""
import os
import base64
import httpx
import logging
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai")

# Config
PROTOCOL_BASE = os.getenv("MINDS_PROTOCOL_BASE", "https://protocol.minds.com")
STEWARD_KEY = os.getenv("MINDS_STEWARD_KEY")
PLATFORM_KEY = os.getenv("PLATFORM_API_KEY")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")
HF_TOKEN = os.getenv("HF_API_TOKEN", "")

BOARD_ID = os.getenv("MINDS_BOARD_ID", "9058443E-F36B-1410-8464-00039CE7DF11")

class RenderRequest(BaseModel):
    agent_id: str
    schema: dict
    provider: str = "openrouter" # 'minds', 'openrouter', 'mock'
    reference_artifact_id: Optional[str] = None

class RenderResponse(BaseModel):
    imageUrl: str
    artifactId: Optional[str] = None
    provider: str

HEADERS_PROTOCOL = lambda: {"Authorization": f"Bearer {STEWARD_KEY}", "Content-Type": "application/json"}
HEADERS_OPENROUTER = lambda: {
    "Authorization": f"Bearer {OPENROUTER_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": "https://liquid-avatar-poc.onrender.com",
    "X-Title": "Liquid Avatar"
}
HEADERS_HF = lambda: {"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "application/json"}

# ─── PROVIDERS ────────────────────────────────────────────────────────────────

class MindsProvider:
    async def render(self, req: RenderRequest) -> RenderResponse:
        if not STEWARD_KEY:
            raise HTTPException(500, "Minds Steward Key not configured")
        
        payload = {
            "board_id": BOARD_ID,
            "tag": "render_request",
            "data": {
                "agent_id": req.agent_id,
                "schema": req.schema,
                "style": "anime_avatar",
                "output_format": "png",
                "resolution": "256x256",
                "reference_artifact_id": req.reference_artifact_id
            }
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            # Submit
            res = await client.post(f"{PROTOCOL_BASE}/v1/work/cards", headers=HEADERS_PROTOCOL(), json=payload)
            res.raise_for_status()
            card_id = res.json().get("id")
            if not card_id: raise HTTPException(500, "Failed to create Work Card")

            # Poll
            delay = 5.0
            for _ in range(12): # ~60s
                await httpx.AsyncClient().sleep(delay)
                status_res = await client.get(f"{PROTOCOL_BASE}/v1/work/cards/{card_id}", headers=HEADERS_PROTOCOL())
                status_res.raise_for_status()
                card = status_res.json()
                
                if card.get("status") == "done":
                    art_id = card.get("artifactId")
                    if not art_id: raise HTTPException(500, "Missing artifactId")
                    
                    art_res = await client.get(f"{PROTOCOL_BASE}/v1/artifacts/{art_id}", headers=HEADERS_PROTOCOL())
                    art_res.raise_for_status()
                    
                    return RenderResponse(
                        imageUrl=f"data:image/png;base64,{base64.b64encode(art_res.content).decode()}",
                        artifactId=art_id,
                        provider="minds"
                    )
                elif card.get("status") == "blocked":
                    raise HTTPException(400, f"Minds blocked: {card.get('data',{}).get('error')}")
                delay *= 1.5
            
            raise HTTPException(504, "Minds render timeout")

class OpenRouterProvider:
    async def render(self, req: RenderRequest) -> RenderResponse:
        if not OPENROUTER_KEY:
            raise HTTPException(500, "OpenRouter Key not configured")
        
        prompt = self._build_prompt(req.schema)
        
        # 1. Refine Prompt
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=HEADERS_OPENROUTER(),
                json={
                    "model": "meta-llama/llama-3.1-8b-instruct:free",
                    "messages": [
                        {"role": "system", "content": "Refine this avatar prompt for Stable Diffusion. Keep it under 75 words. Output ONLY the prompt."},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 100, "temperature": 0.3
                }
            )
            res.raise_for_status()
            refined = res.json()["choices"][0]["message"]["content"].strip()

        # 2. Generate Image (HuggingFace)
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(
                "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-2-1",
                headers=HEADERS_HF(),
                json={"inputs": refined, "parameters": {"width": 256, "height": 256, "num_inference_steps": 20}}
            )
            
            if res.status_code != 200:
                # Fallback placeholder
                return RenderResponse(imageUrl=self._placeholder(req.schema.get("hue", 180)), provider="openrouter")
            
            return RenderResponse(
                imageUrl=f"data:image/png;base64,{base64.b64encode(res.content).decode()}",
                provider="openrouter"
            )

    def _build_prompt(self, schema: dict) -> str:
        hue = schema.get("hue", 180)
        sat = schema.get("sat", 0.75)
        comp = schema.get("complexity", 5)
        dyn = schema.get("dynamics", "idle")
        role = schema.get("role", "general")
        
        hair = "short hair" if comp <= 4 else "twin tails" if comp <= 7 else "long flowing hair"
        expr = {"output": "bright smile", "analysis": "focused gaze", "idle": "soft neutral eyes"}.get(dyn, "gentle eyes")
        acc = {"auditor": "earpiece", "architect": "geometric hairpin"}.get(role, "")
        
        return f"Anime avatar, chibi style, {hair}, {expr}, hair color hsl({hue}, {sat*100}%, 35%), soft cel shading, white background, no text, game asset style, high quality, {acc}"

    def _placeholder(self, hue: int) -> str:
        return f"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ccircle cx='50' cy='50' r='40' fill='hsl({hue},60%25,70%25)'/%3E%3Ctext x='50' y='55' text-anchor='middle' fill='white' font-size='12'%3EAI%3C/text%3E%3C/svg%3E"

# ─── ROUTER ───────────────────────────────────────────────────────────────────

@router.post("/render", response_model=RenderResponse)
async def render_avatar(req: RenderRequest, x_platform_key: str = Header(...)):
    if x_platform_key != PLATFORM_KEY:
        raise HTTPException(401, "Invalid platform key")
    
    provider_map = {
        "minds": MindsProvider(),
        "openrouter": OpenRouterProvider()
    }
    
    provider = provider_map.get(req.provider)
    if not provider:
        raise HTTPException(400, f"Unsupported provider: {req.provider}")
    
    return await provider.render(req)