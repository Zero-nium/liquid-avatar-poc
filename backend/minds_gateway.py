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

class MindsEmailProvider:
    """Email-based protocol bridge for Minds avatar rendering."""
    
    RENDER_EMAIL = "CF79493E-F36B-1410-8462-00039CE7DF11@hellominds.ai"
    STATUS_PAGE = "https://mindspage.com/s/HJp74cU_A6yc"
    
    async def render(self, req: RenderRequest) -> RenderResponse:
        """Submit render request via email, poll for response."""
        
        # 1. Format email payload
        email_payload = {
            "type": "render_request",
            "version": "1.0",
            "board_id": "9058443E-F36B-1410-8464-00039CE7DF11",  # Renderer Board
            "data": {
                "agent_id": req.agent_id,
                "schema": req.schema,
                "style": "anime_avatar",
                "output_format": "png",
                "resolution": "256x256",
                "reference_artifact_id": req.reference_artifact_id
            },
            "metadata": {
                "requested_by": "liquid_avatar_platform",
                "callback_email": os.getenv("PLATFORM_CALLBACK_EMAIL", ""),  # Optional reply-to
                "priority": "normal"
            }
        }
        
        # 2. Send via Resend (or any email API)
        email_sent = await self._send_render_email(email_payload)
        if not email_sent:
            raise HTTPException(500, "Failed to send render request email")
        
        logger.info(f"📧 Render request sent to {self.RENDER_EMAIL} for agent {req.agent_id}")
        
        # 3. Poll status page for completion (simplified)
        # In production, you'd implement webhook/callback handling
        artifact_id = await self._poll_status_page(req.agent_id, max_attempts=20)
        
        if not artifact_id:
            # Fallback: return placeholder after timeout
            return RenderResponse(
                imageUrl=self._placeholder(req.schema.get("hue", 180)),
                provider="minds_email"
            )
        
        # 4. Resolve artifact to image URI
        # For now, assume agent replies with direct image URL or we fetch from status page
        image_url = await self._resolve_artifact(artifact_id)
        
        return RenderResponse(
            imageUrl=image_url,
            artifactId=artifact_id,
            provider="minds_email"
        )
    
    async def _send_render_email(self, payload: dict) -> bool:
        """Send JSON payload via Resend API."""
        resend_key = os.getenv("RESEND_API_KEY")
        if not resend_key:
            logger.warning("⚠️ RESEND_API_KEY not set, simulating email send")
            return True  # Simulate success for testing
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {resend_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "from": "Liquid Avatar <noreply@yourdomain.com>",
                    "to": [self.RENDER_EMAIL],
                    "subject": f"Render Request: {payload['data']['agent_id']}",
                    "text": json.dumps(payload, indent=2),
                    "headers": {
                        "X-Render-Request": "true",
                        "X-Agent-ID": payload['data']['agent_id']
                    }
                }
            )
            return response.status_code == 200
    
    async def _poll_status_page(self, agent_id: str, max_attempts: int = 20) -> Optional[str]:
        """Poll Minds status page for render completion."""
        # Simplified polling - in production, implement proper webhook handling
        for attempt in range(max_attempts):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    # Check if status page has entry for this agent
                    # This is a placeholder - actual implementation depends on status page API
                    response = await client.get(f"{self.STATUS_PAGE}/api/status/{agent_id}")
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("status") == "completed" and data.get("artifactId"):
                            return data["artifactId"]
                        elif data.get("status") == "failed":
                            logger.warning(f"❌ Render failed for {agent_id}: {data.get('error')}")
                            return None
                await asyncio.sleep(10)  # Wait 10s between polls
            except Exception as e:
                logger.warning(f"⚠️ Status poll error for {agent_id}: {e}")
                await asyncio.sleep(10)
        return None
    
    async def _resolve_artifact(self, artifact_id: str) -> str:
        """Convert artifactId to accessible image URL."""
        # Placeholder: assume agent replies with direct URL or we fetch from known endpoint
        # In production, this would call the protocol artifact resolution endpoint
        return f"https://artifacts.minds.com/{artifact_id}.png"  # Example
    
    def _placeholder(self, hue: int) -> str:
        """Generate placeholder SVG for fallback."""
        return f"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ccircle cx='50' cy='50' r='40' fill='hsl({hue},60%25,70%25)'/%3E%3Ctext x='50' y='55' text-anchor='middle' fill='white' font-size='12'%3EMinds%3C/text%3E%3C/svg%3E"

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
        "minds": MindsEmailProvider(), # Now uses email bridge
        "minds_email": MindsEmailProvider(), # Alias for clarity
        "openrouter": OpenRouterProvider()
    }
    
    provider = provider_map.get(req.provider)
    if not provider:
        raise HTTPException(400, f"Unsupported provider: {req.provider}")
    
    return await provider.render(req)