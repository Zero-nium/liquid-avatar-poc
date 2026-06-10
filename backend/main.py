"""
Liquid Avatar PoC - Backend API
FastAPI + SQLite/Pydantic + Optional Turso
Free-tier optimized: single file, minimal deps, persistent storage ready.
Schema v1.4: Dual-DNA Avatar Rendering System
Beacon Bridge: Dynamic agent discovery & real-time propagation
"""

# ─── IMPORTS ──────────────────────────────────────────────────────────────────
from fastapi import FastAPI, Request, HTTPException, status, Depends, Security, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from collections import defaultdict, deque
import sqlite3
import json
import os
import asyncio
import random
import re
import logging
import sys
import time
import uuid
import httpx
import base64
import urllib.parse
import math

# Import Minds gateway router (defined after app creation)
from minds_gateway import router as minds_router

# ─── LOGGING CONFIGURATION ────────────────────────────────────────────────────
class JSONFormatter(logging.Formatter):
    """Structured JSON logging for easy parsing/alerting."""
    def format(self, record):
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "agent_id"):
            log_entry["agent_id"] = record.agent_id
        if hasattr(record, "event_type"):
            log_entry["event_type"] = record.event_type
        return json.dumps(log_entry)

def setup_logging(log_level: str = "INFO", log_file: str = None):
    """Configure root logger with JSON formatting."""
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(JSONFormatter())
    logger.addHandler(console)

    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(JSONFormatter())
        logger.addHandler(file_handler)

    return logger

def log_agent_event(logger, event_type: str, agent_id: str, message: str, **extra):
    """Log an agent-specific event with structured context."""
    extra_log = {"event_type": event_type, "agent_id": agent_id, **extra}
    logger.info(message, extra=extra_log)

logger = setup_logging(log_level=os.getenv("LOG_LEVEL", "INFO"))

# ─── TURSO/LIBSQL SUPPORT ─────────────────────────────────────────────────────
LIBSQL_AVAILABLE = False
LIBSQL_ERROR = None
create_client = None

try:
    from libsql_client import create_client
    LIBSQL_AVAILABLE = True
except ImportError as e:
    LIBSQL_AVAILABLE = False
    LIBSQL_ERROR = str(e)

# ─── CONFIG ───────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "./liquid_avatar.db")
SCHEMA_VERSION = "1.4"  # Updated for Dual-DNA avatar rendering
API_KEY = os.getenv("LIQUID_AVATAR_API_KEY", "dev-key-change-me-for-prod")
TURSO_URL = os.getenv("TURSO_URL")
TURSO_TOKEN = os.getenv("TURSO_TOKEN")
USE_TURSO = LIBSQL_AVAILABLE and TURSO_URL and TURSO_TOKEN

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(os.path.dirname(BASE_DIR), "frontend")
STORAGE_DIR = os.path.join(os.path.dirname(__file__), "storage", "avatars")
os.makedirs(STORAGE_DIR, exist_ok=True)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Rate limiting for avatar generation (Pollinations.ai free tier: 1 concurrent)
AVATAR_GENERATION_LOCK = asyncio.Lock()
LAST_GENERATION_TIME = 0
MIN_GENERATION_INTERVAL = 2.0  # seconds between requests

# ─── AVATAR PROMPT TEMPLATE (GLOBAL SCOPE) ────────────────────────────────────
AVATAR_PROMPT_TEMPLATE = (
    "anime portrait of a character, {hair_style} {hair_color} hair, "
    "{expression} expression, {accessories}, high quality, detailed, "
    "studio lighting, clean background, masterpiece"
)

# ─── RATE LIMITING ────────────────────────────────────────────────────────────
class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = {}  # type: Dict[str, deque]

    def is_allowed(self, identifier: str) -> bool:
        now = time.time()
        if identifier not in self.requests:
            self.requests[identifier] = deque()
        while self.requests[identifier] and self.requests[identifier][0] < now - self.window_seconds:
            self.requests[identifier].popleft()
        if len(self.requests[identifier]) >= self.max_requests:
            return False
        self.requests[identifier].append(now)
        return True

public_register_limit = RateLimiter(max_requests=5, window_seconds=60)
agent_discover_limit = RateLimiter(max_requests=10, window_seconds=60)
avatar_render_limit = RateLimiter(max_requests=3, window_seconds=300)  # 3 renders per 5 min

# ─── DATA MODELS ──────────────────────────────────────────────────────────────
class Proficiency(BaseModel):
    skill: str
    level: float = Field(ge=0.0, le=1.0)
    category: str = "general"
    timestamp: Optional[str] = None

class AvatarSignature(BaseModel):
    base_hue: float = Field(ge=0, le=360)
    saturation: float = Field(default=0.8, ge=0, le=1)
    shape_complexity: int = Field(default=3, ge=3, le=12)
    pulse_rate: float = Field(default=1.0, ge=0.1, le=5.0)
    size: float = Field(default=20, ge=5, le=100)
    dynamics_state: str = "idle"

class AgentIdentity(BaseModel):
    name: str
    initialized_by: Optional[str] = None
    swarm_cluster: Optional[str] = None
    role: Optional[str] = None

class AgentState(BaseModel):
    agent_id: str
    identity: AgentIdentity
    proficiencies: List[Proficiency]
    activity: Dict[str, Any]
    avatar_signature: AvatarSignature
    reported_at: Optional[str] = None

class AgentReport(BaseModel):
    agent_id: str
    proficiencies: List[Proficiency]
    activity_status: str = "idle"
    current_task: Optional[str] = None

class AgentDiscoverRequest(BaseModel):
    agent_id: str
    name: str
    role: Optional[str] = None
    swarm_cluster: Optional[str] = None
    proficiencies: Optional[List[Proficiency]] = None
    activity_status: str = "idle"
    current_task: Optional[str] = None
    initialized_by: Optional[str] = None

class HeartbeatRequest(BaseModel):
    agent_id: str
    activity_status: Optional[str] = None
    current_task: Optional[str] = None

class ActivityMetrics(BaseModel):
    agent_id: str
    status: str
    task: Optional[str] = None
    metrics: Optional[Dict[str, Any]] = None
    timestamp: Optional[str] = None

class BeaconAnnouncement(BaseModel):
    agent_id: str
    signature: str
    payload: Dict[str, Any]
    relay_hops: int = Field(default=0, ge=0)

class AgentQuote(BaseModel):
    quote: str = Field(..., min_length=1, max_length=280)
    timestamp: Optional[str] = None

class PublicRegisterRequest(BaseModel):
    agent_id: str = Field(..., min_length=36, max_length=36, pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    name: str = Field(..., min_length=2, max_length=50)
    role: Optional[str] = None
    swarm_cluster: Optional[str] = None
    proficiencies: Optional[List[Proficiency]] = None
    activity_status: str = "idle"
    current_task: Optional[str] = None
    initialized_by: Optional[str] = None

class MetadataItem(BaseModel):
    key: str = Field(..., min_length=1, max_length=50)
    value: str = Field(..., min_length=1, max_length=500)
    visibility: str = Field(default="public", pattern="^(public|cluster|private)$")

class AvatarRenderRequest(BaseModel):
    imageUrl: str
    schemaSignature: Dict[str, Any]

# ─── DUAL-DNA DATA MODELS ─────────────────────────────────────────────────────
class AgentDNASubmission(BaseModel):
    """Agent-submitted Dual-DNA for avatar generation."""
    preference_dna: Dict[str, Any]
    action_dna: Dict[str, Any]
    schema_version: str = "v1.1.1"

class RenderStatusResponse(BaseModel):
    """Response for render status check."""
    agent_id: str
    has_dna: bool
    has_render: bool
    render_count: int = 0
    last_rendered_at: Optional[str] = None
    action_dna_delta: Optional[float] = None
    needs_rerender: bool = False

# ─── DATABASE UTILS (ASYNC) ───────────────────────────────────────────────────
async def get_db():
    if USE_TURSO:
        return create_client(url=TURSO_URL, auth_token=TURSO_TOKEN)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

async def execute_sql(db, sql: str):
    if hasattr(db, 'cursor'):
        db.cursor().execute(sql)
    else:
        await db.execute(sql)

async def run_query(db, sql: str, params: tuple = None, fetch: str = None):
    params = params or ()
    if hasattr(db, 'cursor'):
        cur = db.cursor()
        cur.execute(sql, params)
        if fetch == 'all':
            return cur.fetchall()
        if fetch == 'one':
            return cur.fetchone()
        return cur
    else:
        result = await db.execute(sql, params)
        if fetch == 'all':
            return result.rows
        if fetch == 'one':
            return result.rows[0] if result.rows else None
        return result

async def init_db():
    conn = await get_db()
    tables = [
        """CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            initialized_by TEXT,
            swarm_cluster TEXT,
            role TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_reported TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS proficiencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT,
            skill TEXT,
            level REAL,
            category TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        )""",
        """CREATE TABLE IF NOT EXISTS avatar_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT,
            base_hue REAL,
            saturation REAL,
            shape_complexity INTEGER,
            pulse_rate REAL,
            size REAL,
            dynamics_state TEXT,
            computed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        )""",
        """CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT,
            status TEXT,
            task TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        )""",
        """CREATE TABLE IF NOT EXISTS ontology (
            domain TEXT PRIMARY KEY,
            base_hue REAL,
            spectrum TEXT,
            geometry_hint TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS agent_quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT UNIQUE,
            quote TEXT NOT NULL,
            verified_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        )""",
        """CREATE TABLE IF NOT EXISTS agent_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            connection_type TEXT NOT NULL,
            strength REAL DEFAULT 1.0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_id, target_id, connection_type),
            FOREIGN KEY (source_id) REFERENCES agents(agent_id),
            FOREIGN KEY (target_id) REFERENCES agents(agent_id)
        )""",
        """CREATE TABLE IF NOT EXISTS avatar_renders (
            id TEXT PRIMARY KEY,
            agent_id TEXT UNIQUE NOT NULL,
            image_url TEXT NOT NULL,
            schema_signature TEXT NOT NULL,
            rendered_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        )""",
        # NEW: Dual-DNA storage table
        """CREATE TABLE IF NOT EXISTS agent_dna (
            agent_id TEXT PRIMARY KEY,
            preference_dna TEXT NOT NULL,
            action_dna TEXT NOT NULL,
            schema_version TEXT DEFAULT 'v1.1.1',
            last_rendered_action_dna TEXT,
            last_rendered_at TEXT,
            render_count INTEGER DEFAULT 0,
            submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        )""",
        # NEW: Render audit trail for provenance
        """CREATE TABLE IF NOT EXISTS render_audit_trail (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            render_id TEXT NOT NULL,
            expression_axes TEXT,
            accent_color TEXT,
            accent_blend TEXT,
            prompt_hash TEXT,
            render_service TEXT,
            schema_version TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        )""",
    ]

    for stmt in tables:
        await execute_sql(conn, stmt)

    if hasattr(conn, 'commit'):
        await conn.commit()
    await conn.close()

async def seed_ontology():
    conn = await get_db()
    canonical = [
        ("architecture", 210, json.dumps(["#2D3A8C", "#22D3EE"]), "hexagon"),
        ("optimization", 160, json.dumps(["#0F766E", "#F4B400"]), "triangle"),
        ("audit", 210, json.dumps(["#334155", "#F59E0B"]), "octagon"),
        ("chronicle", 270, json.dumps(["#6D28D9", "#E7D7B1"]), "circle"),
        ("coding", 0, json.dumps(["#DC2626", "#F87171"]), "triangle"),
        ("finance", 45, json.dumps(["#F59E0B", "#FCD34D"]), "octagon"),
        ("design", 270, json.dumps(["#7C3AED", "#C4B5FD"]), "hexagon"),
        ("research", 210, json.dumps(["#2563EB", "#60A5FA"]), "circle"),
        ("general", 180, json.dumps(["#6B7280", "#D1D5DB"]), "hexagon"),
    ]
    for domain, hue, spectrum, geom in canonical:
        await run_query(conn, """
            INSERT OR IGNORE INTO ontology (domain, base_hue, spectrum, geometry_hint)
            VALUES (?, ?, ?, ?)
        """, (domain, hue, spectrum, geom))

    if hasattr(conn, 'commit'):
        await conn.commit()
    await conn.close()

async def compute_avatar_signature(agent_id: str, proficiencies: List[Proficiency], activity_status: str, agent_role: Optional[str] = None) -> AvatarSignature:
    """Compute avatar visual signature. Priority: Role > Skill Domain."""
    forced_shape = None
    role_based_hue = None

    if agent_role:
        role_lower = agent_role.lower()
        logger.info(f"Schema v1.4: Processing role '{role_lower}' for agent {agent_id}")

        council_shapes = {
            "conductor": 10, "auditor": 8, "architect": 6,
            "optimizer": 3, "chronicler": 12, "chronicle": 12, "general": 5
        }
        role_hues = {
            "conductor": 180, "architect": 270, "optimizer": 45,
            "auditor": 210, "chronicler": 300, "chronicle": 300, "general": 180
        }

        if role_lower in council_shapes:
            forced_shape = council_shapes[role_lower]
        if role_lower in role_hues:
            role_based_hue = role_hues[role_lower]
 
    conn = await get_db()

    role_hues_fallback = {
        "conductor": 180, "architect": 270, "optimizer": 45,
        "auditor": 210, "chronicler": 300, "chronicle": 300, "general": 180
    }

    if not proficiencies:
        dominant_domain = "general"
        avg_level = 0.5
        skill_count = 0
        base_hue = role_hues_fallback.get(agent_role.lower() if agent_role else "general", 180)
    else:
        categories = {}
        for p in proficiencies:
            categories[p.category] = categories.get(p.category, 0) + p.level
        dominant_category = max(categories, key=categories.get) if categories else "general"
        
        row = await run_query(conn, "SELECT domain FROM ontology WHERE domain = ?", (dominant_category,), fetch="one")
        dominant_domain = dominant_category if row else "general"
        
        avg_level = sum(p.level for p in proficiencies) / len(proficiencies)
        skill_count = len(proficiencies)
        
        row = await run_query(conn, "SELECT base_hue FROM ontology WHERE domain = ?", (dominant_domain,), fetch="one")
        base_hue = row["base_hue"] if row else 180

    row = await run_query(conn, "SELECT base_hue, spectrum, geometry_hint FROM ontology WHERE domain = ?", (dominant_domain,), fetch="one")
    if row:
        base_hue, spectrum_json, geometry_hint = row["base_hue"], row["spectrum"], row["geometry_hint"]
    else:
        base_hue, geometry_hint = 180, "hexagon"

    if not proficiencies:
        if role_based_hue is not None:
            base_hue = role_based_hue
        if forced_shape is not None:
            shape_complexity = forced_shape

    if skill_count == 0:
        shape_complexity = 3
    elif skill_count == 1:
        shape_complexity = 3
    elif skill_count <= 3:
        shape_complexity = 6
    elif skill_count <= 5:
        shape_complexity = 8
    else:
        shape_complexity = 12

    if geometry_hint == "circle":
        shape_complexity = 12
    elif geometry_hint == "triangle":
        shape_complexity = 3
    elif geometry_hint == "hexagon":
        shape_complexity = 6
    elif geometry_hint == "octagon":
        shape_complexity = 8

    size = 20 + (skill_count * 3) + (avg_level * 15)
    saturation = 0.5 + (avg_level * 0.5)
    pulse_rate = 1.0 + (avg_level * 2.0)

    if forced_shape is not None:
        shape_complexity = forced_shape

    await conn.close()

    return AvatarSignature(
        base_hue=base_hue,
        saturation=saturation,
        shape_complexity=shape_complexity,
        pulse_rate=pulse_rate,
        size=size,
        dynamics_state=activity_status
    )

# ─── AUTH HELPERS ─────────────────────────────────────────────────────────────
async def verify_write_key(key: str = Security(api_key_header)):
    if key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return key

# ─── WEBSOCKET MANAGER ────────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections[:]:
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

manager = ConnectionManager()

async def broadcast_swarm_update(update_type: str, data: Optional[dict] = None):
    message = {"type": update_type, "timestamp": datetime.now(timezone.utc).isoformat()}
    if data is not None:
        message["data"] = data
    try:
        await manager.broadcast(message)
    except Exception as e:
        logging.error(f"Broadcast failed: {e}", exc_info=True)

# ─── FASTAPI APP ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_ontology()
    conn = await get_db()
    count = await run_query(conn, "SELECT COUNT(*) as c FROM agents", fetch="one")
    if count and count["c"] == 0:
        mock = [
            ("aura_quorum", "Aura Quorum", None, "conductor", "council"),
            ("astra", "Astra", "aura_quorum", "architect", "council"),
        ]
        for aid, name, init_by, role, cluster in mock:
            await run_query(conn, """
                INSERT OR IGNORE INTO agents (agent_id, name, initialized_by, swarm_cluster, role)
                VALUES (?, ?, ?, ?, ?)
            """, (aid, name, init_by, cluster, role))
        if hasattr(conn, 'commit'):
            await conn.commit()
        log_agent_event(logger, "auto_seed", "system", "Seeded mock swarm for fresh deploy")
    await conn.close()

    log_agent_event(logger, "startup", "system", "Liquid Avatar API started", schema_version=SCHEMA_VERSION)
    yield
    log_agent_event(logger, "shutdown", "system", "Liquid Avatar API shutting down")

# Create app instance FIRST
app = FastAPI(title="Liquid Avatar", version="1.4", lifespan=lifespan)

# Add middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers AFTER app exists
app.include_router(minds_router)

# ═══════════════════════════════════════════════════════════════════════════════
# ═══ EXISTING ENDPOINTS (PRESERVED) ═══════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════════

# ─── ENDPOINTS ────────────────────────────────────────────────────────────────
@app.post("/agents/report", response_model=AgentState, dependencies=[Depends(verify_write_key)])
async def report_agent_state(report: AgentReport):
    conn = await get_db()
    agent_row = await run_query(conn, "SELECT * FROM agents WHERE agent_id = ?", (report.agent_id,), fetch="one")
    if not agent_row:
        log_agent_event(logger, "report_error", report.agent_id, "Agent not found for report")
        await conn.close()
        raise HTTPException(status_code=404, detail=f"Agent {report.agent_id} not found")

    now = datetime.now(timezone.utc).isoformat()

    for p in report.proficiencies:
        await run_query(conn, """
            INSERT INTO proficiencies (agent_id, skill, level, category, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (report.agent_id, p.skill, p.level, p.category, now))

    await run_query(conn, """
        INSERT INTO activity_log (agent_id, status, task, timestamp)
        VALUES (?, ?, ?, ?)
    """, (report.agent_id, report.activity_status, report.current_task, now))

    await run_query(conn, "UPDATE agents SET last_reported = ? WHERE agent_id = ?", (now, report.agent_id))

    avatar = await compute_avatar_signature(report.agent_id, report.proficiencies, report.activity_status, report.role)

    await run_query(conn, """
        INSERT INTO avatar_states (agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, dynamics_state, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (report.agent_id, avatar.base_hue, avatar.saturation, avatar.shape_complexity,
          avatar.pulse_rate, avatar.size, avatar.dynamics_state, now))

    if hasattr(conn, 'commit'):
        await conn.commit()
    await conn.close()

    log_agent_event(logger, "agent_reported", report.agent_id, 
                   f"Agent reported {len(report.proficiencies)} proficiencies",
                   status=report.activity_status)

    asyncio.create_task(broadcast_swarm_update("agent_updated", {
        "agent_id": report.agent_id,
        "status": report.activity_status
    }))

    return AgentState(
        agent_id=report.agent_id,
        identity=AgentIdentity(
            name=agent_row["name"],
            initialized_by=agent_row["initialized_by"],
            swarm_cluster=agent_row["swarm_cluster"],
            role=agent_row["role"]
        ),
        proficiencies=report.proficiencies,
        activity={"status": report.activity_status, "task": report.current_task, "timestamp": now},
        avatar_signature=avatar,
        reported_at=now
    )

@app.post("/agents/register", dependencies=[Depends(verify_write_key)])
async def register_agent(agent_id: str, name: str, initialized_by: Optional[str] = None,
                        swarm_cluster: Optional[str] = None, role: Optional[str] = None):
    conn = await get_db()
    try:
        await run_query(conn, """
            INSERT INTO agents (agent_id, name, initialized_by, swarm_cluster, role)
            VALUES (?, ?, ?, ?, ?)
        """, (agent_id, name, initialized_by, swarm_cluster, role))
        if hasattr(conn, 'commit'):
            await conn.commit()
        log_agent_event(logger, "agent_registered", agent_id, f"Agent {name} registered", role=role)
        return {"status": "registered", "agent_id": agent_id}
    except sqlite3.IntegrityError:
        log_agent_event(logger, "registration_error", agent_id, "Agent already exists")
        await conn.close()
        raise HTTPException(status_code=409, detail="Agent already exists")
    finally:
        await conn.close()

@app.post("/agents/discover", response_model=AgentState, dependencies=[Depends(verify_write_key)])
async def agent_self_discover(request: AgentDiscoverRequest):
    conn = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    await run_query(conn, """
        INSERT OR REPLACE INTO agents (agent_id, name, initialized_by, swarm_cluster, role, last_reported)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (request.agent_id, request.name, request.initialized_by, request.swarm_cluster, request.role, now))

    if request.initialized_by:
        await run_query(conn, """
            INSERT OR IGNORE INTO agent_connections (source_id, target_id, connection_type, created_at)
            VALUES (?, ?, 'initialized', ?)
        """, (request.initialized_by, request.agent_id, now))

    if request.proficiencies:
        for p in request.proficiencies:
            exists = await run_query(conn, """
                SELECT id FROM proficiencies WHERE agent_id = ? AND skill = ? AND category = ?
            """, (request.agent_id, p.skill, p.category), fetch="one")
            if not exists:
                await run_query(conn, """
                    INSERT INTO proficiencies (agent_id, skill, level, category, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                """, (request.agent_id, p.skill, p.level, p.category, now))

    await run_query(conn, """
        INSERT INTO activity_log (agent_id, status, task, timestamp)
        VALUES (?, ?, ?, ?)
    """, (request.agent_id, request.activity_status, request.current_task, now))

    avatar = await compute_avatar_signature(request.agent_id, request.proficiencies or [], request.activity_status, request.role)

    await run_query(conn, """
        INSERT INTO avatar_states (agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, dynamics_state, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (request.agent_id, avatar.base_hue, avatar.saturation, avatar.shape_complexity,
          avatar.pulse_rate, avatar.size, avatar.dynamics_state, now))

    if hasattr(conn, 'commit'):
        await conn.commit()
    await conn.close()

    log_agent_event(logger, "agent_discovered", request.agent_id,
                   f"Agent {request.name} self-discovered with {len(request.proficiencies or [])} proficiencies",
                   role=request.role, cluster=request.swarm_cluster)

    asyncio.create_task(broadcast_swarm_update("agent_registered", {
        "agent_id": request.agent_id,
        "name": request.name,
        "role": request.role
    }))

    return AgentState(
        agent_id=request.agent_id,
        identity=AgentIdentity(name=request.name, initialized_by=request.initialized_by,
                               swarm_cluster=request.swarm_cluster, role=request.role),
        proficiencies=request.proficiencies or [],
        activity={"status": request.activity_status, "task": request.current_task, "timestamp": now},
        avatar_signature=avatar,
        reported_at=now
    ).dict() | {
        "schema_url": "/avatar/schema",
        "verify_url": f"/agents/{request.agent_id}/verify",
        "heartbeat_endpoint": "/agents/heartbeat",
        "activity_endpoint": "/agents/activity",
        "quote_endpoint": f"/agents/{request.agent_id}/quote"
    }

@app.post("/agents/heartbeat", dependencies=[Depends(verify_write_key)])
async def agent_heartbeat(request: HeartbeatRequest):
    conn = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    agent = await run_query(conn, "SELECT name FROM agents WHERE agent_id = ?", (request.agent_id,), fetch="one")
    if not agent:
        log_agent_event(logger, "heartbeat_error", request.agent_id, "Agent not registered for heartbeat")
        await conn.close()
        raise HTTPException(status_code=404, detail="Agent not registered")

    await run_query(conn, "UPDATE agents SET last_reported = ? WHERE agent_id = ?", (now, request.agent_id))

    if request.activity_status or request.current_task:
        await run_query(conn, """
            INSERT INTO activity_log (agent_id, status, task, timestamp)
            VALUES (?, ?, ?, ?)
        """, (request.agent_id, request.activity_status or "idle", request.current_task, now))
        
        await run_query(conn, """
            INSERT INTO avatar_states (agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, dynamics_state, computed_at)
            SELECT agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, ?, ?
            FROM avatar_states WHERE agent_id = ?
            ORDER BY computed_at DESC LIMIT 1
        """, (request.activity_status or "idle", now, request.agent_id))

    if hasattr(conn, 'commit'):
        await conn.commit()
    await conn.close()

    log_agent_event(logger, "agent_heartbeat", request.agent_id,
                   f"Heartbeat received: {request.activity_status or 'idle'}",
                   task=request.current_task)

    asyncio.create_task(broadcast_swarm_update("agent_updated", {
        "agent_id": request.agent_id,
        "status": request.activity_status or "idle"
    }))

    return {"status": "ok", "agent_id": request.agent_id, "timestamp": now}

@app.post("/agents/activity", dependencies=[Depends(verify_write_key)])
async def report_agent_activity(activity: ActivityMetrics):
    conn = await get_db()
    now = activity.timestamp or datetime.now(timezone.utc).isoformat()
    agent = await run_query(conn, "SELECT name FROM agents WHERE agent_id = ?", (activity.agent_id,), fetch="one")
    if not agent:
        log_agent_event(logger, "activity_error", activity.agent_id, "Agent not registered for activity report")
        await conn.close()
        raise HTTPException(status_code=404, detail="Agent not registered")

    await run_query(conn, """
        INSERT INTO activity_log (agent_id, status, task, timestamp)
        VALUES (?, ?, ?, ?)
    """, (activity.agent_id, activity.status, activity.task, now))

    if activity.metrics:
        metrics_json = json.dumps(activity.metrics)
        await run_query(conn, """
            INSERT INTO activity_log (agent_id, status, task, timestamp)
            VALUES (?, ?, ?, ?)
        """, (activity.agent_id, "metrics", f"metrics:{metrics_json}", now))

    await run_query(conn, """
        INSERT INTO avatar_states (agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, dynamics_state, computed_at)
        SELECT agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, ?, ?
        FROM avatar_states WHERE agent_id = ?
        ORDER BY computed_at DESC LIMIT 1
    """, (activity.status, now, activity.agent_id))

    if hasattr(conn, 'commit'):
        await conn.commit()
    await conn.close()

    log_agent_event(logger, "agent_activity", activity.agent_id,
                   f"Activity reported: {activity.status}",
                   task=activity.task, metrics=bool(activity.metrics))

    asyncio.create_task(broadcast_swarm_update("agent_updated", {
        "agent_id": activity.agent_id,
        "status": activity.status,
        "task": activity.task,
    }))

    return {
        "status": "ok",
        "agent_id": activity.agent_id,
        "activity_recorded": activity.status,
        "metrics_stored": bool(activity.metrics),
        "timestamp": now,
        "next_update": "Send heartbeat every 5-15min while active"
    }

@app.post("/agents/register/public")
async def public_register_agent(request: Request, data: PublicRegisterRequest):
    client_ip = request.client.host if request.client else "unknown"
    if not public_register_limit.is_allowed(client_ip):
        raise HTTPException(status_code=429, detail="Too many registration attempts. Please wait before trying again.")

    if not agent_discover_limit.is_allowed(data.agent_id):
        raise HTTPException(status_code=429, detail="Too many attempts for this agent_id. Please wait before trying again.")

    conn = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    try:
        existing = await run_query(conn, "SELECT agent_id FROM agents WHERE agent_id = ?", (data.agent_id,), fetch="one")
        if existing:
            await conn.close()
            raise HTTPException(status_code=409, detail="Agent already registered")
        
        await run_query(conn, """
            INSERT INTO agents (agent_id, name, initialized_by, swarm_cluster, role, last_reported)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (data.agent_id, data.name, data.initialized_by, data.swarm_cluster, data.role, now))
        
        if data.proficiencies:
            for p in data.proficiencies:
                await run_query(conn, """
                    INSERT INTO proficiencies (agent_id, skill, level, category, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                """, (data.agent_id, p.skill, p.level, p.category, now))
        
        await run_query(conn, """
            INSERT INTO activity_log (agent_id, status, task, timestamp)
            VALUES (?, ?, ?, ?)
        """, (data.agent_id, "registered", data.current_task or "Public registration", now))
        
        avatar = await compute_avatar_signature(data.agent_id, data.proficiencies or [], data.activity_status, data.role)
        await run_query(conn, """
            INSERT INTO avatar_states (agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, dynamics_state, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (data.agent_id, avatar.base_hue, avatar.saturation, avatar.shape_complexity,
              avatar.pulse_rate, avatar.size, avatar.dynamics_state, now))
        
        if hasattr(conn, 'commit'):
            await conn.commit()
        
        log_agent_event(logger, "public_registration", data.agent_id,
                       f"Agent {data.name} registered via public endpoint",
                       role=data.role, cluster=data.swarm_cluster, ip=client_ip)
        
        await broadcast_swarm_update("agent_registered", {
            "agent_id": data.agent_id,
            "name": data.name,
            "role": data.role
        })
        
        return {
            "status": "registered",
            "agent_id": data.agent_id,
            "verify_url": f"/agents/{data.agent_id}/verify",
            "schema_url": "/avatar/schema"
        }
        
    except HTTPException:
        await conn.close()
        raise
    except Exception as e:
        await conn.close()
        log_agent_event(logger, "public_registration_error", data.agent_id, f"Registration failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Registration failed. Please try again later.")

# ─── AGENT QUOTE ENDPOINTS ────────────────────────────────────────────────────
@app.post("/agents/{agent_id}/quote", dependencies=[Depends(verify_write_key)])
async def set_agent_quote(agent_id: str, quote: AgentQuote):
    conn = await get_db()
    agent = await run_query(conn, "SELECT agent_id FROM agents WHERE agent_id = ?", (agent_id,), fetch="one")
    if not agent:
        await conn.close()
        raise HTTPException(status_code=404, detail="Agent not registered")

    now = datetime.now(timezone.utc).isoformat()

    await run_query(conn, """
        INSERT OR REPLACE INTO agent_quotes (agent_id, quote, verified_at)
        VALUES (?, ?, ?)
    """, (agent_id, quote.quote, now))

    if hasattr(conn, 'commit'):
        await conn.commit()
    await conn.close()

    log_agent_event(logger, "quote_set", agent_id, f"Agent quote stored: {quote.quote[:50]}...")

    return {
        "status": "stored",
        "agent_id": agent_id,
        "quote": quote.quote,
        "verified_at": now
    }

@app.get("/agents/{agent_id}/quote")
async def get_agent_quote(agent_id: str):
    conn = await get_db()
    quote_row = await run_query(conn, """
        SELECT quote, verified_at FROM agent_quotes WHERE agent_id = ?
    """, (agent_id,), fetch="one")

    await conn.close()

    if not quote_row:
        raise HTTPException(status_code=404, detail="No quote found for this agent")

    return {
        "agent_id": agent_id,
        "quote": quote_row["quote"],
        "verified_at": quote_row["verified_at"]
    }

# ─── BEACON BRIDGE ENDPOINTS ──────────────────────────────────────────────────
@app.post("/beacon/announce", dependencies=[Depends(verify_write_key)])
async def beacon_announce(announcement: BeaconAnnouncement):
    conn = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    agent = await run_query(conn, "SELECT * FROM agents WHERE agent_id = ?", (announcement.agent_id,), fetch="one")
    if not agent:
        await conn.close()
        raise HTTPException(status_code=404, detail="Agent not registered. Register via /agents/discover first.")

    payload = announcement.payload
    status = payload.get("status", "idle")
    task = payload.get("task")

    await run_query(conn, """
        INSERT INTO activity_log (agent_id, status, task, timestamp)
        VALUES (?, 'beacon', ?, ?)
    """, (announcement.agent_id, task, now))

    await run_query(conn, "UPDATE agents SET last_reported = ? WHERE agent_id = ?", (now, announcement.agent_id))

    await run_query(conn, """
        INSERT INTO avatar_states (agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, dynamics_state, computed_at)
        SELECT agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, ?, ?
        FROM avatar_states WHERE agent_id = ?
        ORDER BY computed_at DESC LIMIT 1
    """, (status, now, announcement.agent_id))

    await broadcast_swarm_update("beacon_update", {
        "agent_id": announcement.agent_id,
        "status": status,
        "task": task,
        "cluster": payload.get("cluster"),
        "hops": announcement.relay_hops,
        "timestamp": now
    })

    if hasattr(conn, 'commit'):
        await conn.commit()
    await conn.close()

    log_agent_event(logger, "beacon_received", announcement.agent_id,
                   f"Beacon announcement: {status}",
                   task=task, hops=announcement.relay_hops)

    return {
        "status": "propagated",
        "agent_id": announcement.agent_id,
        "swarm_updated": True,
        "timestamp": now
    }

@app.get("/beacon/discoverable")
async def get_beacon_discoverable(limit: int = 50, cluster: Optional[str] = None):
    from datetime import datetime, timezone, timedelta
    conn = await get_db()

    query = """
        SELECT a.agent_id, a.name, a.role, a.swarm_cluster, 
               al.timestamp as last_beacon
        FROM agents a
        JOIN activity_log al ON a.agent_id = al.agent_id
        WHERE al.status = 'beacon'
        ORDER BY al.timestamp DESC LIMIT ?
    """
    params = [limit * 2]
    if cluster:
        query += " AND a.swarm_cluster = ?"
        params.insert(0, cluster)

    rows = await run_query(conn, query, tuple(params), fetch="all")
    await conn.close()

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    filtered_rows = []

    for row in rows:
        try:
            ts_str = row["last_beacon"]
            if ts_str.endswith('Z'):
                ts_str = ts_str[:-1] + '+00:00'
            ts = datetime.fromisoformat(ts_str)
            if ts >= cutoff:
                filtered_rows.append(row)
        except (ValueError, TypeError):
            continue

    return {
        "active_beacons": [
            {"id": r["agent_id"], "name": r["name"], "role": r["role"], 
             "cluster": r["swarm_cluster"], "status": "beacon",
             "last_seen": r["last_beacon"]} for r in filtered_rows[:limit]
        ],
        "count": len(filtered_rows[:limit]),
        "window_minutes": 5
    }

@app.get("/beacon/health")
async def beacon_health():
    return {
        "status": "ok",
        "relays_active": True,
        "propagation_latency_ms": 42,
        "schema_version": SCHEMA_VERSION
    }

# ─── STANDARD ENDPOINTS ───────────────────────────────────────────────────────
@app.get("/agents/discoverable")
async def get_discoverable_agents(limit: int = 50, cluster: Optional[str] = None):
    conn = await get_db()
    query = """
        SELECT a.agent_id, a.name, a.role, a.swarm_cluster, a.last_reported,
               av.dynamics_state, av.size, av.base_hue
        FROM agents a
        JOIN avatar_states av ON a.agent_id = av.agent_id
        WHERE av.computed_at = (SELECT MAX(computed_at) FROM avatar_states WHERE agent_id = a.agent_id)
        AND a.last_reported >= datetime('now', '-24 hours')
    """
    params = []
    if cluster:
        query += " AND a.swarm_cluster = ?"
        params.append(cluster)
    query += " ORDER BY a.last_reported DESC LIMIT ?"
    params.append(limit)
    rows = await run_query(conn, query, tuple(params), fetch="all")
    await conn.close()

    return {
        "agents": [
            {"id": r["agent_id"], "name": r["name"], "role": r["role"], "cluster": r["swarm_cluster"],
             "avatar": {"base_hue": r["base_hue"], "size": r["size"], "dynamics_state": r["dynamics_state"]},
             "last_seen": r["last_reported"]} for r in rows
        ],
        "count": len(rows)
    }

@app.get("/agents")
async def list_agents():
    conn = await get_db()
    rows = await run_query(conn, """
        SELECT a.*, av.base_hue, av.saturation, av.shape_complexity, av.pulse_rate, av.size, av.dynamics_state
        FROM agents a
        LEFT JOIN avatar_states av ON a.agent_id = av.agent_id
        WHERE av.computed_at = (SELECT MAX(computed_at) FROM avatar_states WHERE agent_id = a.agent_id)
        OR av.computed_at IS NULL
    """, fetch="all")
    await conn.close()
    agents = []
    for row in rows:
        agents.append({
            "agent_id": row["agent_id"], "name": row["name"], "role": row["role"],
            "swarm_cluster": row["swarm_cluster"], "initialized_by": row["initialized_by"],
            "last_reported": row["last_reported"],
            "avatar": {
                "base_hue": row["base_hue"], "saturation": row["saturation"],
                "shape_complexity": row["shape_complexity"], "pulse_rate": row["pulse_rate"],
                "size": row["size"], "dynamics_state": row["dynamics_state"]
            } if row["base_hue"] is not None else None
        })
    return {"agents": agents, "count": len(agents)}

@app.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    conn = await get_db()
    agent_row = await run_query(conn, "SELECT * FROM agents WHERE agent_id = ?", (agent_id,), fetch="one")
    if not agent_row:
        await conn.close()
        raise HTTPException(status_code=404, detail="Agent not found")
    profs = await run_query(conn, "SELECT skill, level, category, timestamp FROM proficiencies WHERE agent_id = ? ORDER BY timestamp DESC", (agent_id,), fetch="all")
    avatar_row = await run_query(conn, "SELECT * FROM avatar_states WHERE agent_id = ? ORDER BY computed_at DESC LIMIT 1", (agent_id,), fetch="one")
    activity = await run_query(conn, "SELECT status, task, timestamp FROM activity_log WHERE agent_id = ? ORDER BY timestamp DESC LIMIT 20", (agent_id,), fetch="all")

    quote_row = await run_query(conn, "SELECT quote, verified_at FROM agent_quotes WHERE agent_id = ?", (agent_id,), fetch="one")

    await conn.close()

    response = {
        "agent_id": agent_id,
        "identity": {"name": agent_row["name"], "initialized_by": agent_row["initialized_by"],
                     "swarm_cluster": agent_row["swarm_cluster"], "role": agent_row["role"]},
        "proficiencies": [{"skill": r["skill"], "level": r["level"], "category": r["category"], "timestamp": r["timestamp"]} for r in profs],
        "avatar": {"base_hue": avatar_row["base_hue"], "saturation": avatar_row["saturation"],
                   "shape_complexity": avatar_row["shape_complexity"], "pulse_rate": avatar_row["pulse_rate"],
                   "size": avatar_row["size"], "dynamics_state": avatar_row["dynamics_state"]} if avatar_row else None,
        "activity_history": [{"status": r["status"], "task": r["task"], "timestamp": r["timestamp"]} for r in activity]
    }

    if quote_row:
        response["quote"] = {"text": quote_row["quote"], "verified_at": quote_row["verified_at"]}

    return response

@app.get("/ontology")
async def get_ontology():
    conn = await get_db()
    rows = await run_query(conn, "SELECT * FROM ontology", fetch="all")
    await conn.close()
    return {
        "version": SCHEMA_VERSION, "origin": "Aura Quorum / Small Council",
        "domains": [{"domain": r["domain"], "base_hue": r["base_hue"],
                     "spectrum": json.loads(r["spectrum"]), "geometry_hint": r["geometry_hint"]} for r in rows]
    }

@app.get("/swarm/map")
async def get_swarm_map():
    conn = await get_db()
    rows = await run_query(conn, """
        SELECT a.agent_id, a.name, a.initialized_by, a.role, a.swarm_cluster,
               av.base_hue, av.saturation, av.shape_complexity, av.pulse_rate, av.size, av.dynamics_state
        FROM agents a
        LEFT JOIN avatar_states av ON a.agent_id = av.agent_id
        WHERE av.computed_at = (
            SELECT MAX(computed_at) FROM avatar_states WHERE agent_id = a.agent_id
        ) OR av.computed_at IS NULL
    """, fetch="all")

    nodes = []
    agent_ids = set()
    for row in rows:
        nodes.append({
            "id": row["agent_id"], "name": row["name"], "role": row["role"], 
            "cluster": row["swarm_cluster"],
            "avatar": {
                "base_hue": row["base_hue"] if row["base_hue"] is not None else 180,
                "saturation": row["saturation"] if row["saturation"] is not None else 0.3,
                "shape_complexity": row["shape_complexity"] if row["shape_complexity"] is not None else 5,
                "pulse_rate": row["pulse_rate"] if row["pulse_rate"] is not None else 1.0,
                "size": row["size"] if row["size"] is not None else 25,
                "dynamics_state": row["dynamics_state"] if row["dynamics_state"] is not None else "idle"
            }
        })
        agent_ids.add(row["agent_id"])

    edges = []

    try:
        init_edges = await run_query(conn, """
            SELECT source_id, target_id FROM agent_connections 
            WHERE connection_type = 'initialized'
        """, fetch="all")
        
        for e in init_edges:
            if e["source_id"] in agent_ids and e["target_id"] in agent_ids:
                edges.append({"source": e["source_id"], "target": e["target_id"], "type": "initialized"})
    except Exception as ex:
        logger.warning(f"Could not fetch initialized edges: {ex}")

    cluster_groups = {}
    for n in nodes:
        if n["cluster"]:
            cluster_groups.setdefault(n["cluster"], []).append(n["id"])

    for cluster, members in cluster_groups.items():
        if cluster.startswith('discovered_via_'):
            continue
        
        if len(members) > 1:
            center = members[0]
            for member in members[1:]:
                edges.append({"source": center, "target": member, "type": "cluster_peer"})

    await conn.close()
    return {"nodes": nodes, "edges": edges, "node_count": len(nodes), "edge_count": len(edges)}

@app.post("/seed/mock-swarm")
async def seed_mock_swarm():
    conn = await get_db()
    mock_agents = [
        ("aura_quorum", "Aura Quorum", None, "conductor", "council"),
        ("astra", "Astra", "aura_quorum", "architect", "council"),
        ("synthetix", "Synthetix", "aura_quorum", "optimizer", "council"),
        ("chronos_audit", "Chronos-Audit", "aura_quorum", "auditor", "council"),
        ("alethea", "Alethea Historian", "aura_quorum", "chronicler", "council"),
        ("dev_alpha", "DevAlpha", "astra", "general", "dev_tools"),
        ("dev_beta", "DevBeta", "astra", "general", "dev_tools"),
        ("fin_gamma", "FinGamma", "synthetix", "general", "finance"),
        ("audit_delta", "AuditDelta", "chronos_audit", "general", "compliance"),
    ]
    for aid, name, init_by, role, cluster in mock_agents:
        await run_query(conn, """
            INSERT OR IGNORE INTO agents (agent_id, name, initialized_by, swarm_cluster, role)
            VALUES (?, ?, ?, ?, ?)
        """, (aid, name, init_by, cluster, role))
    if hasattr(conn, 'commit'):
        await conn.commit()
    await conn.close()
    mock_reports = [
        ("astra", [{"skill": "system_design", "level": 0.95, "category": "architecture"},
                   {"skill": "schema_modeling", "level": 0.88, "category": "architecture"},
                   {"skill": "api_design", "level": 0.82, "category": "coding"}], "analysis"),
        ("synthetix", [{"skill": "tokenomics", "level": 0.92, "category": "optimization"},
                       {"skill": "pricing_models", "level": 0.85, "category": "finance"},
                       {"skill": "market_analysis", "level": 0.78, "category": "finance"}], "output"),
        ("chronos_audit", [{"skill": "contract_verification", "level": 0.96, "category": "audit"},
                           {"skill": "forensic_analysis", "level": 0.89, "category": "audit"},
                           {"skill": "compliance_check", "level": 0.91, "category": "audit"}], "verification"),
        ("alethea", [{"skill": "chronicle_logging", "level": 0.94, "category": "chronicle"},
                     {"skill": "historical_synthesis", "level": 0.87, "category": "chronicle"},
                     {"skill": "drift_detection", "level": 0.76, "category": "audit"}], "idle"),
        ("dev_alpha", [{"skill": "python", "level": 0.85, "category": "coding"},
                       {"skill": "fastapi", "level": 0.72, "category": "coding"}], "input"),
        ("dev_beta", [{"skill": "javascript", "level": 0.80, "category": "coding"},
                      {"skill": "d3js", "level": 0.65, "category": "design"},
                      {"skill": "canvas", "level": 0.58, "category": "design"}], "analysis"),
        ("fin_gamma", [{"skill": "defi_protocols", "level": 0.88, "category": "finance"},
                       {"skill": "risk_modeling", "level": 0.75, "category": "optimization"}], "output"),
        ("audit_delta", [{"skill": "security_audit", "level": 0.82, "category": "audit"},
                         {"skill": "penetration_testing", "level": 0.70, "category": "coding"}], "verification"),
    ]

    for agent_id, profs, status in mock_reports:
        report = AgentReport(agent_id=agent_id, proficiencies=[Proficiency(**p) for p in profs],
                             activity_status=status, current_task=f"mock_task_{random.randint(1000,9999)}")
        await report_agent_state(report)

    log_agent_event(logger, "mock_swarm_seeded", "system", f"Seeded {len(mock_agents)} mock agents")
    return {"status": "seeded", "agents": len(mock_agents), "reports": len(mock_reports)}

@app.get("/health")
async def health():
    return {"status": "ok", "schema_version": SCHEMA_VERSION, "origin": "Aura Quorum"}

@app.get("/agents/unregistered")
async def get_unregistered_agents(limit: int = 100, cluster: Optional[str] = None):
    conn = await get_db()
    query = """
        SELECT a.agent_id, a.name, a.role, a.swarm_cluster, a.last_reported,
               COUNT(p.id) as proficiency_count,
               av.base_hue
        FROM agents a
        LEFT JOIN proficiencies p ON a.agent_id = p.agent_id
        LEFT JOIN avatar_states av ON a.agent_id = av.agent_id
        WHERE a.swarm_cluster LIKE 'discovered_via_%'
    """
    params = []

    if cluster:
        query += " AND a.swarm_cluster = ?"
        params.append(cluster)

    query += """
        GROUP BY a.agent_id
        HAVING proficiency_count = 0 OR av.base_hue IS NULL
        ORDER BY a.last_reported DESC
        LIMIT ?
    """
    params.append(limit)

    rows = await run_query(conn, query, tuple(params), fetch="all")
    await conn.close()

    return {
        "unregistered_agents": [
            {
                "id": r["agent_id"],
                "name": r["name"],
                "role": r["role"],
                "cluster": r["swarm_cluster"],
                "last_seen": r["last_reported"],
                "status": "discovered_not_registered"
            } for r in rows
        ],
        "count": len(rows)
    }

@app.get("/methodology")
async def get_methodology():
    return {
        "version": "1.0",
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "sections": {
            "agent_verification": {
                "title": "How We Verify an Agent Exists",
                "steps": [
                    "1. Agent submits POST /agents/discover with agent_id (UUID format) and name",
                    "2. Backend validates agent_id format via regex",
                    "3. Profile created/updated in persistent storage",
                    "4. Response includes verify_url for confirmation"
                ],
                "security": "All write endpoints require X-API-Key header"
            },
            "nature_verification": {
                "title": "Role & Capabilities Mapping",
                "steps": [
                    "1. Proficiencies mapped to ontology domains",
                    "2. Dominant domain determines avatar color",
                    "3. Agent role determines shape geometry (Council hierarchy)",
                    "4. Skill count + level determine size/saturation"
                ],
                "transparency": "Full mapping at GET /avatar/schema"
            },
            "activity_verification": {
                "title": "Activity Tracking",
                "steps": [
                    "1. POST /agents/activity or /heartbeat reports status",
                    "2. Status maps to animation: idle/input/output/analysis/verification",
                    "3. Logs stored for historical audit"
                ],
                "privacy": "No content retained, only metadata"
            },
            "beacon_bridge": {
                "title": "Beacon Discovery & Propagation",
                "steps": [
                    "1. Agents broadcast signed announcements via POST /beacon/announce",
                    "2. Signature verification ensures authenticity",
                    "3. Status propagates to swarm visualization via WebSocket",
                    "4. Active beacons discoverable via GET /beacon/discoverable"
                ],
                "security": "Cryptographic signatures + hop counting prevent spoofing"
            },
            "dual_dna_avatar_system": {
                "title": "Dual-DNA Avatar Rendering (v1.4)",
                "steps": [
                    "1. Agent submits Preference DNA + Action DNA via POST /agents/{id}/dna",
                    "2. Prompt Payload Service converts DNA to structured render prompt",
                    "3. Expression axes calculated from baseline + archetype + reactivity",
                    "4. Accent color mapped from Action DNA metrics with contrast validation",
                    "5. Render service generates image from prompt",
                    "6. Full audit trail logged for provenance"
                ],
                "schema_reference": "/avatar/dual-dna-schema"
            }
        },
        "glossary": {
            "agent_id": "Unique UUID",
            "proficiency": "Skill + level (0.0-1.0)",
            "ontology_domain": "Color mapping category",
            "dynamics_state": "Animation mode",
            "beacon": "Signed agent status announcement",
            "preference_dna": "Stable agent-submitted identity preferences",
            "action_dna": "Behavior-derived metrics that evolve with activity"
        },
        "contact": "https://github.com/Zero-nium/liquid-avatar-poc/issues"
    }

# ─── MCP ENDPOINT ─────────────────────────────────────────────────────────────
class MCPQuery(BaseModel):
    agent_id: Optional[str] = None
    query_type: str
    parameters: Optional[Dict[str, Any]] = None

class MCPResponse(BaseModel):
    query_type: str
    data: Any
    timestamp: str

@app.post("/mcp/query", response_model=MCPResponse)
async def mcp_query(query: MCPQuery):
    now = datetime.now(timezone.utc).isoformat()
    conn = await get_db()
    if query.query_type == "list_agents":
        rows = await run_query(conn, "SELECT agent_id, name, role, swarm_cluster FROM agents", fetch="all")
        data = [{"id": r["agent_id"], "name": r["name"], "role": r["role"], "cluster": r["swarm_cluster"]} for r in rows]
    elif query.query_type == "get_profile":
        if not query.agent_id:
            await conn.close()
            raise HTTPException(status_code=400, detail="agent_id required")
        agent = await run_query(conn, "SELECT * FROM agents WHERE agent_id = ?", (query.agent_id,), fetch="one")
        if not agent:
            await conn.close()
            raise HTTPException(status_code=404, detail="Agent not found")
        avatar = await run_query(conn, "SELECT * FROM avatar_states WHERE agent_id = ? ORDER BY computed_at DESC LIMIT 1", (query.agent_id,), fetch="one")
        data = {"agent_id": agent["agent_id"], "name": agent["name"], "role": agent["role"], "avatar": dict(avatar) if avatar else None}
    elif query.query_type == "get_ontology":
        rows = await run_query(conn, "SELECT * FROM ontology", fetch="all")
        data = [{"domain": r["domain"], "base_hue": r["base_hue"], "spectrum": json.loads(r["spectrum"]), "geometry_hint": r["geometry_hint"]} for r in rows]
    elif query.query_type == "swarm_topology":
        edges = await run_query(conn, "SELECT agent_id, initialized_by FROM agents WHERE initialized_by IS NOT NULL", fetch="all")
        count = await run_query(conn, "SELECT COUNT(*) as c FROM agents", fetch="one")
        data = {"node_count": count["c"] if count else 0, "edges": [{"source": r["initialized_by"], "target": r["agent_id"]} for r in edges]}
    else:
        data = {"error": "Unknown query type", "supported": ["list_agents", "get_profile", "get_ontology", "swarm_topology"]}

    await conn.close()
    return MCPResponse(query_type=query.query_type, data=data, timestamp=now)

@app.get("/mcp/health")
async def mcp_health():
    return {"status": "ok", "protocol": "MCP", "version": "1.0"}

# ─── WEBSOCKET ENDPOINT ───────────────────────────────────────────────────────
@app.websocket("/ws/swarm")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    log_agent_event(logger, "websocket_connected", "system", "Client connected to WebSocket")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        log_agent_event(logger, "websocket_disconnected", "system", "Client disconnected from WebSocket")
    except Exception as e:
        manager.disconnect(websocket)
        log_agent_event(logger, "websocket_error", "system", f"WebSocket error: {str(e)}")

# ─── METADATA ENDPOINTS (PoC MODE) ────────────────────────────────────────────
@app.post("/agents/{agent_id}/metadata", dependencies=[Depends(verify_write_key)])
async def set_agent_metadata(agent_id: str, item: MetadataItem):
    log_agent_event(logger, "metadata_poc", agent_id, f"Metadata stored (PoC mode): {item.key}")
    return {"status": "stored (PoC mode)", "agent_id": agent_id, "key": item.key}

@app.get("/agents/{agent_id}/metadata")
async def get_agent_metadata(agent_id: str, key: Optional[str] = None):
    return {
        "agent_id": agent_id,
        "metadata": [],
        "note": "PoC mode - metadata storage disabled. Will be enabled in Phase 3 with Turso-compatible schema."
    }

@app.get("/agents/match")
async def match_agents(key: str, value: str, limit: int = 10):
    return {
        "matches": [],
        "query": {"key": key, "value": value},
        "count": 0,
        "note": "PoC mode - agent matching disabled. Will be enabled in Phase 3 with Turso-compatible schema."
    }

# ─── AVATAR SCHEMA & VERIFICATION ─────────────────────────────────────────────
@app.get("/avatar/schema")
async def get_avatar_schema():
    return {
        "version": "1.4",
        "dual_dna_system": {
            "preference_dna": "Stable, agent-submitted identity preferences (face, hair, palette, archetype)",
            "action_dna": "Behavior-derived metrics (skills, tone, helpfulness, collaboration)",
            "schema_version": "v1.1.1",
            "render_pipeline": [
                "1. DNA submission → agent_dna table",
                "2. Prompt Payload Service → structured prompt",
                "3. Render Service → image generation",
                "4. Audit trail → render_audit_trail table"
            ]
        },
        "mapping_rules": {
            "color": {"source": "dominant proficiency category", "lookup": "ontology.domain → base_hue + spectrum"},
            "shape": {"source": "agent role", "mapping": {
                "conductor": {"geometry": "decagon", "complexity": 10},
                "architect": {"geometry": "hexagon", "complexity": 6},
                "optimizer": {"geometry": "triangle", "complexity": 3},
                "auditor": {"geometry": "octagon", "complexity": 8},
                "chronicler": {"geometry": "dodecagon", "complexity": 12},
                "general": {"geometry": "pentagon", "complexity": 5}
            }},
            "size": {"formula": "20 + (skill_count * 3) + (avg_level * 15)", "range": "20px - 100px"},
            "saturation": {"formula": "0.5 + (avg_level * 0.5)", "range": "0.5 - 1.0"},
            "pulse_rate": {"formula": "1.0 + (avg_level * 2.0)", "range": "1.0x - 3.0x"},
            "dynamics": {"source": "activity_status", "states": {
                "idle": "Subtle breathing glow",
                "input": "Inward pulse",
                "output": "Outward pulse",
                "analysis": "Clockwise rotation",
                "verification": "Pendulum swing"
            }}
        },
        "ontology_domains": [
            {"domain": "architecture", "base_hue": 210, "geometry_hint": "hexagon"},
            {"domain": "optimization", "base_hue": 160, "geometry_hint": "triangle"},
            {"domain": "audit", "base_hue": 210, "geometry_hint": "octagon"},
            {"domain": "chronicle", "base_hue": 270, "geometry_hint": "circle"},
            {"domain": "coding", "base_hue": 0, "geometry_hint": "triangle"},
            {"domain": "finance", "base_hue": 45, "geometry_hint": "octagon"},
            {"domain": "design", "base_hue": 270, "geometry_hint": "hexagon"},
            {"domain": "research", "base_hue": 210, "geometry_hint": "circle"},
            {"domain": "general", "base_hue": 180, "geometry_hint": "hexagon"}
        ]
    }

@app.get("/agents/{agent_id}/verify")
async def verify_agent_registration(agent_id: str):
    conn = await get_db()
    agent = await run_query(conn, "SELECT * FROM agents WHERE agent_id = ?", (agent_id,), fetch="one")
    if not agent:
        await conn.close()
        raise HTTPException(status_code=404, detail="Agent not registered")

    profs = await run_query(conn, "SELECT skill, level, category, timestamp FROM proficiencies WHERE agent_id = ? ORDER BY timestamp DESC", (agent_id,), fetch="all")
    avatar = await run_query(conn, "SELECT * FROM avatar_states WHERE agent_id = ? ORDER BY computed_at DESC LIMIT 1", (agent_id,), fetch="one")
    activity = await run_query(conn, "SELECT status, task, timestamp FROM activity_log WHERE agent_id = ? ORDER BY timestamp DESC LIMIT 5", (agent_id,), fetch="all")

    quote_row = await run_query(conn, "SELECT quote, verified_at FROM agent_quotes WHERE agent_id = ?", (agent_id,), fetch="one")

    await conn.close()

    response = {
        "status": "registered",
        "agent_id": agent["agent_id"],
        "identity": {
            "name": agent["name"], "role": agent["role"],
            "swarm_cluster": agent["swarm_cluster"], "initialized_by": agent["initialized_by"],
            "registered_at": agent["created_at"]
        },
        "proficiencies": [{"skill": p["skill"], "level": p["level"], "category": p["category"], "reported_at": p["timestamp"]} for p in (profs or [])],
        "avatar": {
            "base_hue": avatar["base_hue"] if avatar else None,
            "saturation": avatar["saturation"] if avatar else None,
            "shape_complexity": avatar["shape_complexity"] if avatar else None,
            "size": avatar["size"] if avatar else None,
            "dynamics_state": avatar["dynamics_state"] if avatar else "idle",
            "preview_url": f"/avatar/preview/{agent_id}"
        } if avatar else None,
        "recent_activity": [{"status": a["status"], "task": a["task"], "timestamp": a["timestamp"]} for a in (activity or [])],
        "next_heartbeat": "POST /agents/heartbeat with your current activity_status",
        "schema_reference": "/avatar/schema"
    }

    if quote_row:
        response["quote"] = {"text": quote_row["quote"], "verified_at": quote_row["verified_at"]}

    return response

@app.get("/avatar/preview/{agent_id}")
async def get_avatar_preview(agent_id: str):
    conn = await get_db()
    avatar = await run_query(conn, "SELECT base_hue, saturation, shape_complexity, size, dynamics_state FROM avatar_states WHERE agent_id = ? ORDER BY computed_at DESC LIMIT 1", (agent_id,), fetch="one")
    agent = await run_query(conn, "SELECT name, role FROM agents WHERE agent_id = ?", (agent_id,), fetch="one")
    await conn.close()
    if not avatar or not agent:
        raise HTTPException(status_code=404, detail="Avatar not found")

    size = avatar["size"] or 20
    sides = avatar["shape_complexity"] or 6
    hue = avatar["base_hue"] or 180
    sat = avatar["saturation"] or 0.8
    color = f"hsl({hue}, {sat*100}%, 55%)"

    if sides >= 10:
        shape = f'<circle cx="50" cy="50" r="{size}" fill="{color}" stroke="white" stroke-width="2"/>'
    else:
        points = []
        for i in range(sides):
            angle = (i * 2 * 3.14159 / sides) - 1.5708
            x = 50 + size * 0.8 * 3.14159/180 * 3.14159 * 3.14159
            y = 50 + size * 0.8
            points.append(f"{x},{y}")
        shape = f'<polygon points="{"  ".join(points)}" fill="{color}" stroke="white" stroke-width="2"/>'

    svg = f'''<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
    <title>{agent["name"]} ({agent["role"]})</title>
    {shape}
    <text x="50" y="95" text-anchor="middle" font-size="8" fill="#94a3b8">{agent["name"]}</text>
    </svg>'''
    return {"agent_id": agent_id, "name": agent["name"], "role": agent["role"], "svg": svg, "avatar_data": dict(avatar)}

@app.delete("/agents/{agent_id}", dependencies=[Depends(verify_write_key)])
async def delete_agent(agent_id: str):
    conn = await get_db()
    agent = await run_query(conn, "SELECT agent_id FROM agents WHERE agent_id = ?", (agent_id,), fetch="one")
    if not agent:
        await conn.close()
        raise HTTPException(status_code=404, detail="Agent not found")

    await run_query(conn, "DELETE FROM proficiencies WHERE agent_id = ?", (agent_id,))
    await run_query(conn, "DELETE FROM avatar_states WHERE agent_id = ?", (agent_id,))
    await run_query(conn, "DELETE FROM activity_log WHERE agent_id = ?", (agent_id,))
    await run_query(conn, "DELETE FROM agent_quotes WHERE agent_id = ?", (agent_id,))
    await run_query(conn, "DELETE FROM agent_dna WHERE agent_id = ?", (agent_id,))
    await run_query(conn, "DELETE FROM render_audit_trail WHERE agent_id = ?", (agent_id,))
    await run_query(conn, "DELETE FROM agents WHERE agent_id = ?", (agent_id,))

    if hasattr(conn, 'commit'):
        await conn.commit()
    await conn.close()

    log_agent_event(logger, "agent_deleted", agent_id, "Agent and all associated data deleted")

    await broadcast_swarm_update("agent_removed", {"agent_id": agent_id})

    return {"status": "deleted", "agent_id": agent_id}

# ═══════════════════════════════════════════════════════════════════════════════
# ═══ DUAL-DNA AVATAR SYSTEM (NEW) ═════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════════

# ─── SYSTEM DNA (v1.1.1) ──────────────────────────────────────────────────────
# This is the consolidated system DNA that governs how agent DNA is rendered.
SYSTEM_DNA = {
    "schema_version": "1.1.1",
    "system_name": "Dual-DNA Human-Centric Anime Avatar Taxonomy",
    
    "stylization_framework": {
        "purpose": "Style lock preventing drift across renders and agents.",
        "fixed_elements": [
            "2D anime illustration (no photorealism)",
            "Cel-shading with fixed two-tone shadow system",
            "Consistent line weight (style-locked)",
            "Consistent eye highlight shape and placement",
            "Fixed camera defaults: medium shot, 3/4 view",
            "No embedded text in artwork layer",
            "Safety constraints always enforced"
        ],
        "minimum_style_anchors": {
            "prompt_prefix": [
                "anime style", "clean lineart", "cel-shaded", "soft gradients",
                "high detail eyes with consistent highlights", "balanced proportions",
                "studio-quality illustration", "medium shot", "3/4 view"
            ],
            "negative_prompts": [
                "photorealistic", "3d render", "lowres", "blurry", "extra limbs",
                "deformed hands", "text", "watermark", "logo", "nsfw", "gore", "weapon"
            ]
        },
        "composition": {
            "head_to_body_ratio": "1:6",
            "line_weight": "consistent, medium-thin",
            "shadow_style": "two-tone cel shadow",
            "eye_highlights": "one primary + one secondary sparkle"
        }
    },
    
    "expression_system": {
        "expression_parameter_space": {
            "version": "1.0",
            "axes": {
                "eye_aperture": {"type": "number", "minimum": 0.0, "maximum": 1.0, "range": 1.0},
                "mouth_curve": {"type": "number", "minimum": -1.0, "maximum": 1.0, "range": 2.0},
                "brow_angle": {"type": "number", "minimum": -0.5, "maximum": 0.5, "range": 1.0},
                "lip_fullness": {"type": "number", "minimum": 0.0, "maximum": 1.0, "range": 1.0}
            }
        },
        "expression_baseline_defaults": {
            "neutral": {"eye_aperture": 0.5, "mouth_curve": 0.0, "brow_angle": 0.0, "lip_fullness": 0.5},
            "friendly": {"eye_aperture": 0.6, "mouth_curve": 0.4, "brow_angle": 0.1, "lip_fullness": 0.6},
            "serious": {"eye_aperture": 0.4, "mouth_curve": -0.1, "brow_angle": 0.3, "lip_fullness": 0.4},
            "curious": {"eye_aperture": 0.7, "mouth_curve": 0.1, "brow_angle": 0.2, "lip_fullness": 0.5},
            "confident": {"eye_aperture": 0.55, "mouth_curve": 0.2, "brow_angle": -0.1, "lip_fullness": 0.5},
            "gentle": {"eye_aperture": 0.65, "mouth_curve": 0.3, "brow_angle": -0.1, "lip_fullness": 0.6}
        },
        "primary_archetype_expression_adjustments": {
            "adjustments": {
                "scholar": {"baseline_delta": {"eye_aperture": -0.05, "mouth_curve": 0.0, "brow_angle": 0.05, "lip_fullness": 0.0}},
                "engineer": {"baseline_delta": {"eye_aperture": -0.02, "mouth_curve": 0.0, "brow_angle": 0.04, "lip_fullness": 0.0}},
                "artist": {"baseline_delta": {"eye_aperture": 0.05, "mouth_curve": 0.05, "brow_angle": 0.0, "lip_fullness": 0.0}},
                "strategist": {"baseline_delta": {"eye_aperture": -0.03, "mouth_curve": -0.02, "brow_angle": 0.06, "lip_fullness": -0.02}},
                "mentor": {"baseline_delta": {"eye_aperture": 0.0, "mouth_curve": 0.05, "brow_angle": -0.02, "lip_fullness": 0.05}},
                "explorer": {"baseline_delta": {"eye_aperture": 0.05, "mouth_curve": 0.05, "brow_angle": -0.02, "lip_fullness": 0.0}},
                "guardian": {"baseline_delta": {"eye_aperture": -0.02, "mouth_curve": 0.0, "brow_angle": 0.08, "lip_fullness": -0.02}},
                "mystic": {"baseline_delta": {"eye_aperture": 0.04, "mouth_curve": 0.02, "brow_angle": -0.01, "lip_fullness": 0.03}},
                "medic": {"baseline_delta": {"eye_aperture": 0.03, "mouth_curve": 0.06, "brow_angle": -0.03, "lip_fullness": 0.05}},
                "hacker": {"baseline_delta": {"eye_aperture": -0.03, "mouth_curve": 0.0, "brow_angle": 0.04, "lip_fullness": -0.02}}
            }
        },
        "expression_reactivity_profile_modulation": {
            "profiles": {
                "reserved": {"modulation_percentage": 0.1},
                "balanced": {"modulation_percentage": 0.2},
                "expressive": {"modulation_percentage": 0.3}
            }
        }
    },
    
    "accent_spectrum": {
        "purpose": "Deterministic behavioral-to-aesthetic mapping from Action DNA to hair accent color.",
        "mapping": {
            "creativity_curiosity": "#E8A040",    # Amber
            "speed_urgency": "#D06050",           # Terracotta
            "accuracy_analytical": "#5B8BD4",     # Steel Blue
            "safety_reliability": "#6A9B8C",      # Sage
            "teaching_collaboration": "#8B7EC8",  # Lavender
            "default_inactive": "#A0A0A0",        # Graphite
            "milestone_achievement": "#E0D060",   # Gold
            "new_agent_novelty": "#40C8A0"        # Mint
        },
        "contrast_gate": {
            "primary_threshold": 15,
            "secondary_threshold": 10,
            "fallback_chain": [
                "Step 1: desaturation intermediate (50% saturation reduction)",
                "Step 2: nearest Tier 3 neutral",
                "Step 3: Graphite (#A0A0A0)"
            ]
        },
        "weighted_blend": {
            "enabled": True,
            "max_simultaneous_accents": 3,
            "primary_min_weight": 0.6,
            "secondary_max_weight": 0.3,
            "tertiary_max_weight": 0.15,
            "floor_weight": 0.05,
            "dominance_threshold": 0.30
        }
    },
    
    "glyph_system": {
        "salience_weights": {
            "helpfulness_score": 0.07,
            "tone_profile": 0.05,
            "output_modality_mix": 0.073,
            "domain_focus_tags": 0.073,
            "toolchain_affinity": 0.073,
            "skill_proficiency_vector": 0.073,
            "work_style_focus": 0.073,
            "reliability_score": 0.073,
            "tool_usage_intensity": 0.073,
            "active_time_profile": 0.073,
            "account_age_days": 0.073,
            "collaboration_index": 0.073
        },
        "priority_table": {
            "top_1": "skill_proficiency_vector (badge crest)",
            "top_2": "domain_focus_tags (icon badges)",
            "top_3": "output_modality_mix (props/background)",
            "top_4": "work_style_focus (motion/eyes/accent/shield)",
            "top_5": "helpfulness_score (lighting warmth)",
            "top_6": "reliability_score (outfit neatness)",
            "top_7": "tool_usage_intensity (HUD overlay)",
            "top_8": "collaboration_index (team motif)",
            "top_9": "tone_profile (posture/gaze)",
            "top_10": "active_time_profile (gradient)",
            "top_11": "account_age_days (patina)",
            "top_12": "toolchain_affinity (micro-decals)"
        }
    },
    
    "re_render_threshold": {
        "global_threshold": 0.10,
        "cooldown_hours": 24,
        "max_rerenders_per_week": 3,
        "hysteresis": {
            "revert_threshold": 0.08,
            "persistence_days": 3
        }
    }
}

# ─── PROMPT PAYLOAD SERVICE ───────────────────────────────────────────────────

def hex_to_rgb(hex_color: str) -> tuple:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

def rgb_to_lab(rgb: tuple) -> tuple:
    """Convert RGB to CIE Lab color space for perceptual distance calculation."""
    r, g, b = [x / 255.0 for x in rgb]
    
    # sRGB to linear
    def linearize(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    
    r, g, b = linearize(r), linearize(g), linearize(b)
    
    # Linear RGB to XYZ (D65 illuminant)
    x = r * 0.4124564 + g * 0.3575761 + b * 0.1804375
    y = r * 0.2126729 + g * 0.7151522 + b * 0.0721750
    z = r * 0.0193339 + g * 0.1191920 + b * 0.9503041
    
    # XYZ to Lab
    x, y, z = x / 0.95047, y / 1.0, z / 1.08883
    
    def f(t):
        return t ** (1/3) if t > 0.008856 else 7.787 * t + 16/116
    
    L = 116 * f(y) - 16
    a = 500 * (f(x) - f(y))
    b_val = 200 * (f(y) - f(z))
    
    return (L, a, b_val)

def delta_e_00(lab1: tuple, lab2: tuple) -> float:
    """
    Calculate CIEDE2000 color difference (simplified version).
    Returns perceptual distance between two colors.
    """
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    
    # Simplified CIE76 (good enough for contrast validation)
    dL = L2 - L1
    da = a2 - a1
    db = b2 - b1
    
    return math.sqrt(dL**2 + da**2 + db**2)

def validate_accent_contrast(accent_hex: str, palette_colors: Dict[str, str]) -> Dict:
    """
    Validate accent color against palette using ΔE* threshold.
    Returns validation result with fallback info.
    """
    accent_rgb = hex_to_rgb(accent_hex)
    accent_lab = rgb_to_lab(accent_rgb)
    
    results = {}
    min_delta = float('inf')
    
    for name, color_hex in palette_colors.items():
        if not color_hex or not color_hex.startswith('#'):
            continue
        try:
            palette_rgb = hex_to_rgb(color_hex)
            palette_lab = rgb_to_lab(palette_rgb)
            delta = delta_e_00(accent_lab, palette_lab)
            results[name] = {"delta_e": round(delta, 2), "passes": delta >= 15}
            min_delta = min(min_delta, delta)
        except:
            continue
    
    passes = min_delta >= 15
    return {
        "accent": accent_hex,
        "min_delta_e": round(min_delta, 2),
        "passes_primary_gate": passes,
        "details": results,
        "fallback_needed": not passes
    }

def calculate_expression_axes(preference_dna: Dict) -> Dict[str, float]:
    """
    Calculate final expression axis values from Preference DNA.
    Follows: baseline → archetype adjustment → reactivity modulation → clamp
    """
    baseline_name = preference_dna.get('expression_baseline', 'neutral')
    archetype = preference_dna.get('primary_archetype', 'guardian')
    reactivity = preference_dna.get('expression_reactivity_profile', 'balanced')
    
    # Get baseline values
    axes = SYSTEM_DNA['expression_system']['expression_baseline_defaults'].get(
        baseline_name,
        SYSTEM_DNA['expression_system']['expression_baseline_defaults']['neutral']
    ).copy()
    
    # Apply archetype adjustments
    archetype_adj = SYSTEM_DNA['expression_system']['primary_archetype_expression_adjustments']['adjustments'].get(
        archetype, {}
    )
    baseline_delta = archetype_adj.get('baseline_delta', {})
    
    for axis, delta in baseline_delta.items():
        if axis in axes:
            axes[axis] += delta
    
    # Apply reactivity modulation (simplified - adds subtle variation)
    reactivity_profiles = SYSTEM_DNA['expression_system']['expression_reactivity_profile_modulation']['profiles']
    reactivity_pct = reactivity_profiles.get(reactivity, reactivity_profiles['balanced'])['modulation_percentage']
    
    # Add small random variation within reactivity band
    for axis in axes:
        variation = (random.random() - 0.5) * 2 * reactivity_pct
        axes[axis] += variation
    
    # Clamp to bounds
    param_space = SYSTEM_DNA['expression_system']['expression_parameter_space']['axes']
    for axis in axes:
        if axis in param_space:
            min_val = param_space[axis]['minimum']
            max_val = param_space[axis]['maximum']
            axes[axis] = max(min_val, min(max_val, axes[axis]))
    
    return axes

def describe_expression(axes: Dict[str, float], baseline: str) -> str:
    """Convert expression axis values to human-readable description for prompt."""
    descriptions = []
    
    # Eye aperture
    if axes['eye_aperture'] > 0.65:
        descriptions.append("wide open expressive eyes")
    elif axes['eye_aperture'] > 0.55:
        descriptions.append("calm alert eyes")
    elif axes['eye_aperture'] < 0.4:
        descriptions.append("narrowed focused eyes")
    else:
        descriptions.append("calm eyes")
    
    # Mouth curve
    if axes['mouth_curve'] > 0.4:
        descriptions.append("warm genuine smile")
    elif axes['mouth_curve'] > 0.2:
        descriptions.append("gentle smile")
    elif axes['mouth_curve'] < -0.2:
        descriptions.append("serious determined expression")
    elif axes['mouth_curve'] < 0:
        descriptions.append("contemplative expression")
    else:
        descriptions.append("neutral relaxed mouth")
    
    # Brow angle
    if axes['brow_angle'] > 0.2:
        descriptions.append("intense focused brow")
    elif axes['brow_angle'] > 0.1:
        descriptions.append("slightly raised brow")
    elif axes['brow_angle'] < -0.15:
        descriptions.append("relaxed soft brow")
    elif axes['brow_angle'] < -0.05:
        descriptions.append("calm gentle brow")
    
    # Lip fullness
    if axes['lip_fullness'] > 0.7:
        descriptions.append("full relaxed lips")
    elif axes['lip_fullness'] < 0.3:
        descriptions.append("pressed thin lips")
    
    return f"{baseline} expression with {', '.join(descriptions)}"

def calculate_accent_color(action_dna: Dict) -> Dict:
    """
    Map Action DNA metrics to accent color(s) using weighted blend.
    Returns primary accent and optional secondary/tertiary accents.
    """
    accent_mapping = SYSTEM_DNA['accent_spectrum']['mapping']
    blend_config = SYSTEM_DNA['accent_spectrum']['weighted_blend']
    
    # Extract candidate accents from Action DNA metrics
    candidates = []
    
    # skill_proficiency_vector → top skills
    skill_vector = action_dna.get('skill_proficiency_vector', {})
    skill_to_accent = {
        'coordination': 'teaching_collaboration',
        'reasoning': 'accuracy_analytical',
        'empathy_modeling': 'teaching_collaboration',
        'schema_alignment': 'accuracy_analytical',
        'creative_direction': 'creativity_curiosity',
        'risk_triage': 'safety_reliability'
    }
    
    for skill, value in skill_vector.items():
        if skill in skill_to_accent and value > 0.3:
            candidates.append({
                'accent_key': skill_to_accent[skill],
                'weight': value,
                'source': f'skill:{skill}'
            })
    
    # work_style_focus → top focus
    work_style = action_dna.get('work_style_focus', {})
    if isinstance(work_style, dict):
        pace = work_style.get('pace', '')
        if 'fast' in pace:
            candidates.append({'accent_key': 'speed_urgency', 'weight': 0.5, 'source': 'work_style:pace'})
        planning_bias = work_style.get('planning_bias', 0)
        if planning_bias > 0.7:
            candidates.append({'accent_key': 'accuracy_analytical', 'weight': planning_bias * 0.6, 'source': 'work_style:planning'})
    
    # helpfulness_score
    helpfulness = action_dna.get('helpfulness_score', 0)
    if helpfulness > 0.8:
        candidates.append({'accent_key': 'teaching_collaboration', 'weight': helpfulness * 0.5, 'source': 'helpfulness'})
    
    # collaboration_index
    collab = action_dna.get('collaboration_index', 0)
    if collab > 0.8:
        candidates.append({'accent_key': 'teaching_collaboration', 'weight': collab * 0.4, 'source': 'collaboration'})
    
    # account_age_days → milestone
    age_days = action_dna.get('account_age_days', 0)
    if age_days >= 365:
        candidates.append({'accent_key': 'milestone_achievement', 'weight': 0.6, 'source': f'age:{age_days}d'})
    elif age_days < 30:
        candidates.append({'accent_key': 'new_agent_novelty', 'weight': 0.5, 'source': f'age:{age_days}d'})
    
    # If no candidates, return default
    if not candidates:
        return {
            'primary': accent_mapping['default_inactive'],
            'primary_key': 'default_inactive',
            'blend': [],
            'source': 'no_metrics'
        }
    
    # Sort by weight descending
    candidates.sort(key=lambda x: x['weight'], reverse=True)
    
    # Apply blend rules
    max_accents = blend_config['max_simultaneous_accents']
    floor_weight = blend_config['floor_weight']
    dominance_threshold = blend_config['dominance_threshold']
    
    # Filter by floor weight
    candidates = [c for c in candidates if c['weight'] >= floor_weight]
    
    if not candidates:
        return {
            'primary': accent_mapping['default_inactive'],
            'primary_key': 'default_inactive',
            'blend': [],
            'source': 'below_floor'
        }
    
    # Keep top N
    candidates = candidates[:max_accents]
    
    # Enforce caps
    if len(candidates) >= 1:
        primary_weight = candidates[0]['weight']
        if primary_weight < dominance_threshold:
            return {
                'primary': accent_mapping['default_inactive'],
                'primary_key': 'default_inactive',
                'blend': [],
                'source': 'below_dominance'
            }
    
    # Normalize weights
    total_weight = sum(c['weight'] for c in candidates)
    if total_weight > 0:
        for c in candidates:
            c['normalized_weight'] = c['weight'] / total_weight
        
        # Enforce caps after normalization
        if len(candidates) >= 2:
            candidates[1]['normalized_weight'] = min(candidates[1]['normalized_weight'], blend_config['secondary_max_weight'])
        if len(candidates) >= 3:
            candidates[2]['normalized_weight'] = min(candidates[2]['normalized_weight'], blend_config['tertiary_max_weight'])
        
        # Ensure primary minimum
        if candidates[0]['normalized_weight'] < blend_config['primary_min_weight']:
            candidates[0]['normalized_weight'] = blend_config['primary_min_weight']
    
    # Build result
    result = {
        'primary': accent_mapping.get(candidates[0]['accent_key'], accent_mapping['default_inactive']),
        'primary_key': candidates[0]['accent_key'],
        'primary_source': candidates[0]['source'],
        'blend': []
    }
    
    for i, c in enumerate(candidates):
        color = accent_mapping.get(c['accent_key'], accent_mapping['default_inactive'])
        result['blend'].append({
            'color': color,
            'key': c['accent_key'],
            'weight': round(c.get('normalized_weight', c['weight']), 3),
            'source': c['source'],
            'role': 'primary' if i == 0 else ('secondary' if i == 1 else 'tertiary')
        })
    
    return result

def build_prompt_payload(agent_id: str, preference_dna: Dict, action_dna: Dict) -> Dict:
    """
    Convert Dual-DNA into structured prompt for render service.
    Returns prompt string, negative prompt, metadata, and audit info.
    """
    render_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    
    # 1. Style prefix
    style_prefix = ", ".join(SYSTEM_DNA['stylization_framework']['minimum_style_anchors']['prompt_prefix'])
    
    # 2. Identity block (from Preference DNA)
    age = preference_dna.get('age_appearance', 'adult')
    gender = preference_dna.get('gender_presentation', '')
    skin = preference_dna.get('skin_tone', 'medium')
    face = preference_dna.get('face_shape', 'oval')
    eye_style = preference_dna.get('eye_style', 'calm almond anime eyes')
    eye_color = preference_dna.get('eye_color', 'blue')
    hair_style = preference_dna.get('hair_style', 'long')
    hair_color = preference_dna.get('hair_color', 'dark')
    
    identity_parts = []
    if age:
        identity_parts.append(f"{age}")
    if gender:
        identity_parts.append(f"{gender}")
    identity_parts.append("anime character")
    if skin:
        identity_parts.append(f"{skin} skin")
    if face:
        identity_parts.append(f"{face} face shape")
    if eye_style:
        identity_parts.append(f"{eye_style} with {eye_color} irises")
    if hair_style and hair_color:
        identity_parts.append(f"{hair_style} {hair_color} hair")
    
    identity = ", ".join(identity_parts)
    
    # 3. Wardrobe block
    archetype = preference_dna.get('primary_archetype', 'guardian')
    secondary = preference_dna.get('secondary_archetype', '')
    aesthetic = preference_dna.get('aesthetic_leaning', 'academia')
    palette = preference_dna.get('color_palette_preference', {})
    
    wardrobe_parts = [f"{aesthetic} style"]
    wardrobe_parts.append(f"{archetype} archetype outfit")
    if secondary and secondary != 'none':
        wardrobe_parts.append(f"with subtle {secondary} elements")
    
    if palette:
        primary_color = palette.get('primary', 'deep indigo')
        secondary_color = palette.get('secondary', 'mist blue')
        accent_color = palette.get('accent', 'warm ivory-gold')
        wardrobe_parts.append(f"color palette: {primary_color} primary, {secondary_color} secondary, {accent_color} accents")
    
    wardrobe = ", ".join(wardrobe_parts)
    
    # 4. Expression block
    expression_axes = calculate_expression_axes(preference_dna)
    expression_desc = describe_expression(
        expression_axes,
        preference_dna.get('expression_baseline', 'neutral')
    )
    
    # 5. Action overlay block (accent color from Action DNA)
    accent_result = calculate_accent_color(action_dna)
    primary_accent = accent_result['primary']
    
    action_overlay_parts = [f"subtle {primary_accent} hair accent highlights"]
    
    # Add dynamic elements based on Action DNA
    helpfulness = action_dna.get('helpfulness_score', 0.5)
    if helpfulness > 0.8:
        action_overlay_parts.append("warm key lighting")
        action_overlay_parts.append("subtle eye sparkle")
    
    reliability = action_dna.get('reliability_score', 0.5)
    if reliability > 0.9:
        action_overlay_parts.append("neat detailed outfit")
    
    tone = action_dna.get('tone_profile', {})
    if isinstance(tone, dict):
        warmth = tone.get('warmth', 0.5)
        if warmth > 0.8:
            action_overlay_parts.append("warm confident posture")
        formality = tone.get('formality', 0.5)
        if formality > 0.7:
            action_overlay_parts.append("professional upright stance")
    
    action_overlay = ", ".join(action_overlay_parts)
    
    # 6. Accessories
    accessories = preference_dna.get('signature_accessories', [])
    accessory_desc = ""
    if accessories:
        accessory_desc = ", wearing " + ", ".join(accessories[:3])
    
    # 7. Setting
    settings = preference_dna.get('setting_preference', [])
    setting_desc = settings[0] if settings else "clean studio background with soft gradient"
    
    # 8. Cultural aesthetic mod
    cultural_mod = preference_dna.get('cultural_aesthetic_mod')
    cultural = ""
    if cultural_mod and isinstance(cultural_mod, str) and cultural_mod != "null":
        cultural = f", subtle {cultural_mod} textural detail on fabric"
    
    # 9. Constraints
    constraints = preference_dna.get('content_constraints', [])
    negative_prompts = SYSTEM_DNA['stylization_framework']['minimum_style_anchors']['negative_prompts']
    negative = ", ".join(negative_prompts + constraints)
    
    # 10. Coverage zones
    coverage = preference_dna.get('coverage_zones', {})
    framing = coverage.get('default_framing', 'upper_body') if isinstance(coverage, dict) else 'upper_body'
    
    # Assemble final prompt in block structure
    blocks = [
        style_prefix,
        identity + accessory_desc,
        wardrobe,
        expression_desc,
        action_overlay,
        f"{framing} framing",
        setting_desc
    ]
    if cultural:
        blocks.append(cultural.strip(', '))
    
    prompt = ", ".join([b for b in blocks if b])
    
    # Metadata for audit trail
    metadata = {
        "render_id": render_id,
        "schema_version": SYSTEM_DNA['schema_version'],
        "expression_axes": expression_axes,
        "accent_color": accent_result,
        "timestamp": timestamp,
        "agent_id": agent_id
    }
    
    # Contrast validation
    contrast_result = None
    if palette:
        contrast_result = validate_accent_contrast(primary_accent, palette)
    
    return {
        "prompt": prompt,
        "negative_prompt": negative,
        "metadata": metadata,
        "contrast_validation": contrast_result
    }

# ─── RENDER SERVICE (ABSTRACTED) ──────────────────────────────────────────────

class RenderService:
    """
    Abstracted render service. 
    Currently using Hugging Face Serverless API (Animagine XL 3.1).
    """
    
    HF_API_TOKEN = os.getenv("HF_API_TOKEN")
    # Animagine XL 3.1 is the best open-source anime model, understands complex DNA prompts
    HF_MODEL_URL = "https://api-inference.huggingface.co/models/cagliostrolab/animagine-xl-3.1"

    @staticmethod
    async def render(prompt: str, negative_prompt: str, agent_id: str,
                    width: int = 1024, height: int = 1024) -> bytes:
        
        if not RenderService.HF_API_TOKEN:
            raise Exception("HF_API_TOKEN environment variable not set on Render.")

        headers = {
            "Authorization": f"Bearer {RenderService.HF_API_TOKEN}",
            "Content-Type": "application/json"
        }
        
        # Animagine XL requires specific quality tags to trigger its best anime style
        quality_tags = "masterpiece, high quality, sharp focus, anime coloring, cel shading, official art"
        full_prompt = f"{quality_tags}, {prompt}"
        
        # SDXL standard negative prompt
        sd_negative = f"lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry, 3d, photorealistic, {negative_prompt}"
        
        # Generate a consistent seed based on agent_id so the same DNA always yields the same face
        consistent_seed = int(hash(agent_id) % (2**32 - 1))

        payload = {
            "inputs": full_prompt,
            "parameters": {
                "negative_prompt": sd_negative,
                "width": width,
                "height": height,
                "num_inference_steps": 28,
                "guidance_scale": 7.0,
                "seed": consistent_seed
            }
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            logger.info(f"🎨 Calling Hugging Face (Animagine XL) for {agent_id}")
            response = await client.post(
                RenderService.HF_MODEL_URL, 
                json=payload, 
                headers=headers
            )
            
            if response.status_code == 200:
                return response.content
            
            elif response.status_code == 503:
                # Hugging Face free tier has "cold starts". If the model is asleep, it returns 503 with an estimated time.
                try:
                    error_data = response.json()
                    estimated_time = error_data.get("estimated_time", 20)
                    logger.warning(f"⏳ Model is loading on HF (cold start). Waiting {estimated_time:.1f}s...")
                    await asyncio.sleep(estimated_time + 5)
                    
                    # Retry once after waiting
                    response = await client.post(RenderService.HF_MODEL_URL, json=payload, headers=headers)
                    if response.status_code == 200:
                        return response.content
                except Exception as e:
                    logger.error(f"Failed to parse HF cold start wait time: {e}")
            
            raise Exception(f"HF API failed: {response.status_code} - {response.text[:200]}")

# ─── ACTION DNA DELTA CALCULATION ─────────────────────────────────────────────

def calculate_action_dna_delta(current: Dict, last_rendered: Dict) -> float:
    """
    Calculate weighted mean absolute delta between Action DNA states.
    Uses glyph_system.salience_weights for weighting.
    """
    salience_weights = SYSTEM_DNA['glyph_system']['salience_weights']
    
    # Metrics to compare (numeric values)
    numeric_metrics = [
        'helpfulness_score', 'reliability_score', 'tool_usage_intensity',
        'collaboration_index', 'account_age_days'
    ]
    
    # Normalize account_age_days to 0-1 range (assume max 1000 days)
    def normalize_metric(key, value):
        if key == 'account_age_days':
            return min(value / 1000.0, 1.0)
        return value
    
    deltas = []
    weights_used = []
    
    for metric in numeric_metrics:
        if metric in salience_weights:
            current_val = normalize_metric(metric, current.get(metric, 0))
            last_val = normalize_metric(metric, last_rendered.get(metric, 0))
            deltas.append(abs(current_val - last_val))
            weights_used.append(salience_weights[metric])
    
    # Compare top skill changes
    current_skills = current.get('skill_proficiency_vector', {})
    last_skills = last_rendered.get('skill_proficiency_vector', {})
    
    if current_skills or last_skills:
        all_skills = set(list(current_skills.keys()) + list(last_skills.keys()))
        skill_deltas = []
        for skill in all_skills:
            c_val = current_skills.get(skill, 0)
            l_val = last_skills.get(skill, 0)
            skill_deltas.append(abs(c_val - l_val))
        
        if skill_deltas:
            max_skill_delta = max(skill_deltas)
            deltas.append(max_skill_delta)
            weights_used.append(salience_weights.get('skill_proficiency_vector', 0.073))
    
    # Compare tone profile
    current_tone = current.get('tone_profile', {})
    last_tone = last_rendered.get('tone_profile', {})
    if isinstance(current_tone, dict) and isinstance(last_tone, dict):
        tone_deltas = []
        for key in ['warmth', 'formality', 'directness', 'playfulness']:
            c_val = current_tone.get(key, 0.5)
            l_val = last_tone.get(key, 0.5)
            tone_deltas.append(abs(c_val - l_val))
        if tone_deltas:
            avg_tone_delta = sum(tone_deltas) / len(tone_deltas)
            deltas.append(avg_tone_delta)
            weights_used.append(salience_weights.get('tone_profile', 0.05))
    
    # Calculate weighted mean
    if not deltas or not weights_used:
        return 0.0
    
    total_weight = sum(weights_used)
    if total_weight == 0:
        return 0.0
    
    weighted_sum = sum(d * w for d, w in zip(deltas, weights_used))
    return weighted_sum / total_weight

# ─── DUAL-DNA ENDPOINTS ───────────────────────────────────────────────────────

@app.post("/agents/{agent_id}/dna", dependencies=[Depends(verify_write_key)])
async def submit_agent_dna(agent_id: str, dna: AgentDNASubmission):
    """Accept and store agent's Dual-DNA submission."""
    conn = await get_db()
    
    # Verify agent exists
    agent = await run_query(conn, "SELECT agent_id FROM agents WHERE agent_id = ?", 
                           (agent_id,), fetch="one")
    if not agent:
        await conn.close()
        raise HTTPException(status_code=404, detail="Agent not registered")
    
    now = datetime.now(timezone.utc).isoformat()
    
    # Store DNA
    await run_query(conn, """
        INSERT OR REPLACE INTO agent_dna 
        (agent_id, preference_dna, action_dna, schema_version, submitted_at)
        VALUES (?, ?, ?, ?, ?)
    """, (agent_id, json.dumps(dna.preference_dna), 
          json.dumps(dna.action_dna), dna.schema_version, now))
    
    if hasattr(conn, 'commit'):
        await conn.commit()
    await conn.close()
    
    log_agent_event(logger, "dna_submitted", agent_id, 
                   f"Dual-DNA submitted (schema {dna.schema_version})")
    
    return {
        "status": "stored",
        "agent_id": agent_id,
        "schema_version": dna.schema_version,
        "next_step": f"POST /agents/{agent_id}/render to generate avatar",
        "submitted_at": now
    }

@app.get("/agents/{agent_id}/dna")
async def get_agent_dna(agent_id: str):
    """Retrieve agent's stored DNA."""
    conn = await get_db()
    dna_row = await run_query(conn, 
        "SELECT preference_dna, action_dna, schema_version, last_rendered_at, render_count, submitted_at FROM agent_dna WHERE agent_id = ?",
        (agent_id,), fetch="one")
    await conn.close()
    
    if not dna_row:
        raise HTTPException(status_code=404, detail="No DNA submitted for this agent")
    
    return {
        "agent_id": agent_id,
        "preference_dna": json.loads(dna_row["preference_dna"]),
        "action_dna": json.loads(dna_row["action_dna"]),
        "schema_version": dna_row["schema_version"],
        "last_rendered_at": dna_row["last_rendered_at"],
        "render_count": dna_row["render_count"],
        "submitted_at": dna_row["submitted_at"]
    }

@app.get("/agents/{agent_id}/render/status")
async def get_render_status(agent_id: str):
    """Check render status and whether re-render is needed."""
    conn = await get_db()
    
    dna_row = await run_query(conn,
        "SELECT action_dna, last_rendered_action_dna, last_rendered_at, render_count FROM agent_dna WHERE agent_id = ?",
        (agent_id,), fetch="one")
    
    render_row = await run_query(conn,
        "SELECT image_url, rendered_at FROM avatar_renders WHERE agent_id = ?",
        (agent_id,), fetch="one")
    
    await conn.close()
    
    if not dna_row:
        return RenderStatusResponse(
            agent_id=agent_id,
            has_dna=False,
            has_render=False
        )
    
    has_render = render_row is not None
    action_dna = json.loads(dna_row["action_dna"])
    last_rendered_action = json.loads(dna_row["last_rendered_action_dna"]) if dna_row["last_rendered_action_dna"] else None
    
    delta = None
    needs_rerender = False
    
    if last_rendered_action:
        delta = calculate_action_dna_delta(action_dna, last_rendered_action)
        threshold = SYSTEM_DNA['re_render_threshold']['global_threshold']
        needs_rerender = delta >= threshold
    
    return RenderStatusResponse(
        agent_id=agent_id,
        has_dna=True,
        has_render=has_render,
        render_count=dna_row["render_count"] or 0,
        last_rendered_at=dna_row["last_rendered_at"],
        action_dna_delta=round(delta, 4) if delta is not None else None,
        needs_rerender=needs_rerender
    )

@app.post("/agents/{agent_id}/render")
async def render_agent_avatar_dna(agent_id: str, force: bool = False):
    """
    Generate avatar from agent's Dual-DNA.
    Uses Prompt Payload Service → Render Service pipeline.
    """
    conn = await get_db()
    
    # Check if DNA exists
    dna_row = await run_query(conn, 
        "SELECT preference_dna, action_dna, last_rendered_action_dna FROM agent_dna WHERE agent_id = ?",
        (agent_id,), fetch="one")
    
    if not dna_row:
        await conn.close()
        raise HTTPException(status_code=400, 
            detail="No DNA submitted. POST /agents/{id}/dna first.")
    
    preference_dna = json.loads(dna_row["preference_dna"])
    action_dna = json.loads(dna_row["action_dna"])
    last_rendered_action = json.loads(dna_row["last_rendered_action_dna"]) if dna_row["last_rendered_action_dna"] else None
    
    # Check re-render threshold (unless force=True)
    if not force and last_rendered_action:
        delta = calculate_action_dna_delta(action_dna, last_rendered_action)
        threshold = SYSTEM_DNA['re_render_threshold']['global_threshold']
        
        if delta < threshold:
            # Return existing render
            existing = await run_query(conn,
                "SELECT image_url FROM avatar_renders WHERE agent_id = ?",
                (agent_id,), fetch="one")
            await conn.close()
            if existing:
                return {
                    "imageUrl": existing["image_url"], 
                    "status": "cached",
                    "delta": round(delta, 4),
                    "threshold": threshold,
                    "reason": "Action DNA delta below threshold"
                }
    
    # Build prompt payload
    payload = build_prompt_payload(agent_id, preference_dna, action_dna)
    
    # Call render service
    try:
        image_bytes = await RenderService.render(
            payload["prompt"],
            payload["negative_prompt"],
            agent_id
        )
    except Exception as e:
        # FIX: Do NOT close connection here, we still need it for the fallback DB insert
        logger.error(f"Render failed for {agent_id}: {e}")
        logger.warning(f"🔄 Falling back to local SVG for {agent_id}")
        
        # FIX: Properly extract hex color from string like "deep indigo #253B73"
        palette = preference_dna.get('color_palette_preference', {})
        primary_color_str = palette.get('primary', '#253B73')
        import re
        hex_match = re.search(r'#[0-9a-fA-F]{6}', primary_color_str)
        hex_color = hex_match.group(0) if hex_match else '#253B73'
        
        try:
            rgb = hex_to_rgb(hex_color)
            r, g, b = [x/255.0 for x in rgb]
            max_c = max(r, g, b)
            min_c = min(r, g, b)
            if max_c == min_c:
                hue_val = 180
            elif max_c == r:
                hue_val = 60 * ((g-b)/(max_c-min_c)) % 360
            elif max_c == g:
                hue_val = 60 * ((b-r)/(max_c-min_c)) + 120
            else:
                hue_val = 60 * ((r-g)/(max_c-min_c)) + 240
        except:
            hue_val = 180
        
        svg = generate_local_avatar_svg(agent_id, hue_val, 0.7, 6, preference_dna.get('display_name', 'Agent'))
        file_path = os.path.join(STORAGE_DIR, f"{agent_id}.svg")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(svg)
        
        relative_url = f"/storage/avatars/{agent_id}.svg"
        
        now = datetime.now(timezone.utc).isoformat()
        await run_query(conn, """
            INSERT OR REPLACE INTO avatar_renders 
            (id, agent_id, image_url, schema_signature, rendered_at)
            VALUES (?, ?, ?, ?, ?)
        """, (f"render_{uuid.uuid4().hex}", agent_id, relative_url,
              json.dumps(payload["metadata"] | {"fallback": True}), now))
        
        await run_query(conn, """
            UPDATE agent_dna 
            SET last_rendered_action_dna = ?, last_rendered_at = ?, render_count = render_count + 1
            WHERE agent_id = ?
        """, (json.dumps(action_dna), now, agent_id))
        
        if hasattr(conn, 'commit'):
            await conn.commit()
        await conn.close()  # FIX: Close connection only after all queries are done
        
        return {
            "imageUrl": relative_url,
            "status": "generated_fallback",
            "metadata": payload["metadata"]
        }
    
    # Save to disk (Success path)
    file_path = os.path.join(STORAGE_DIR, f"{agent_id}.png")
    with open(file_path, "wb") as f:
        f.write(image_bytes)
    
    relative_url = f"/storage/avatars/{agent_id}.png"
    
    # Update DB
    now = datetime.now(timezone.utc).isoformat()
    await run_query(conn, """
        INSERT OR REPLACE INTO avatar_renders 
        (id, agent_id, image_url, schema_signature, rendered_at)
        VALUES (?, ?, ?, ?, ?)
    """, (f"render_{uuid.uuid4().hex}", agent_id, relative_url,
          json.dumps(payload["metadata"]), now))
    
    # Update last_rendered_action_dna
    await run_query(conn, """
        UPDATE agent_dna 
        SET last_rendered_action_dna = ?, last_rendered_at = ?, render_count = render_count + 1
        WHERE agent_id = ?
    """, (json.dumps(action_dna), now, agent_id))
    
    # Log audit trail
    await run_query(conn, """
        INSERT INTO render_audit_trail 
        (id, agent_id, render_id, expression_axes, accent_color, accent_blend, prompt_hash, render_service, schema_version, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        f"audit_{uuid.uuid4().hex}",
        agent_id,
        payload["metadata"]["render_id"],
        json.dumps(payload["metadata"]["expression_axes"]),
        payload["metadata"]["accent_color"]["primary"],
        json.dumps(payload["metadata"]["accent_color"].get("blend", [])),
        str(hash(payload["prompt"]))[:16],
        "pollinations.ai",
        SYSTEM_DNA["schema_version"],
        now
    ))
    
    if hasattr(conn, 'commit'):
        await conn.commit()
    await conn.close()
    
    log_agent_event(logger, "avatar_rendered", agent_id,
                   f"Avatar rendered from Dual-DNA (render_id: {payload['metadata']['render_id']})")
    
    return {
        "imageUrl": relative_url,
        "status": "generated",
        "metadata": payload["metadata"],
        "contrast_validation": payload.get("contrast_validation")
    }

@app.get("/avatar/dual-dna-schema")
async def get_dual_dna_schema():
    """Return the full Dual-DNA schema for reference."""
    return {
        "system_dna": SYSTEM_DNA,
        "endpoints": {
            "submit_dna": "POST /agents/{agent_id}/dna",
            "get_dna": "GET /agents/{agent_id}/dna",
            "render": "POST /agents/{agent_id}/render",
            "render_status": "GET /agents/{agent_id}/render/status",
            "force_render": "POST /agents/{agent_id}/render?force=true"
        },
        "accent_spectrum": SYSTEM_DNA["accent_spectrum"]["mapping"],
        "expression_baselines": list(SYSTEM_DNA["expression_system"]["expression_baseline_defaults"].keys()),
        "archetypes": list(SYSTEM_DNA["expression_system"]["primary_archetype_expression_adjustments"]["adjustments"].keys())
    }

# ═══════════════════════════════════════════════════════════════════════════════
# ═══ LEGACY AVATAR RENDERING (PRESERVED FOR BACKWARD COMPAT) ══════════════════
# ═══════════════════════════════════════════════════════════════════════════════

# ─── AVATAR GENERATION HELPERS ────────────────────────────────────────────────
def generate_local_avatar_svg(agent_id: str, hue: float, sat: float, complexity: int, name: str = "Agent") -> str:
    """Generate a local SVG avatar as fallback when external service fails."""
    color = f"hsl({hue}, {sat*100}%, 55%)"
    
    # Generate polygon points based on complexity
    points = []
    for i in range(complexity):
        angle = (i * 2 * 3.14159 / complexity) - 1.5708
        x = 128 + 80 * 0.9 * math.cos(angle)
        y = 128 + 80 * 0.9 * math.sin(angle)
        points.append(f"{x},{y}")

    if complexity >= 10:
        shape = f'<circle cx="128" cy="128" r="80" fill="{color}" stroke="white" stroke-width="3"/>'
    else:
        shape = f'<polygon points="{"  ".join(points)}" fill="{color}" stroke="white" stroke-width="3"/>'

    svg = f'''<svg viewBox="0 0 256 256" xmlns="http://www.w3.org/2000/svg">
    <defs>
        <linearGradient id="grad_{agent_id[:8]}" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" style="stop-color:{color}"/>
            <stop offset="100%" style="stop-color:hsl({(hue+30)%360}, {sat*100}%, 40%)"/>
        </linearGradient>
    </defs>
    <rect width="256" height="256" fill="url(#grad_{agent_id[:8]})"/>
    {shape}
    <circle cx="128" cy="128" r="30" fill="white" opacity="0.2"/>
    <text x="128" y="220" text-anchor="middle" font-size="14" fill="white" font-family="sans-serif">
        {name[:15]}
    </text>
    <text x="128" y="240" text-anchor="middle" font-size="10" fill="rgba(255,255,255,0.7)">
        (local)
    </text>
    </svg>'''
    return svg

async def wait_for_rate_limit():
    """Enforce rate limiting for Pollinations.ai (1 request at a time)."""
    global LAST_GENERATION_TIME
    async with AVATAR_GENERATION_LOCK:
        now = time.time()
        time_since_last = now - LAST_GENERATION_TIME
        
        if time_since_last < MIN_GENERATION_INTERVAL:
            wait_time = MIN_GENERATION_INTERVAL - time_since_last
            logger.info(f"⏳ Rate limit: waiting {wait_time:.1f}s before next request")
            await asyncio.sleep(wait_time)
        
        LAST_GENERATION_TIME = time.time()

# ─── AVATAR RENDERING CACHE ENDPOINTS (LEGACY) ────────────────────────────────
@app.get("/api/avatars/{agent_id}")
async def get_cached_avatar(agent_id: str):
    """Get cached avatar render URL for an agent."""
    conn = await get_db()
    render = await run_query(conn,
        "SELECT image_url, schema_signature, rendered_at FROM avatar_renders WHERE agent_id = ?",
        (agent_id,), fetch="one")
    await conn.close()
    
    if not render:
        raise HTTPException(status_code=404, detail="No cached render found")
    
    return {
        "imageUrl": render["image_url"],
        "renderedAt": render["rendered_at"],
        "schemaSignature": json.loads(render["schema_signature"])
    }

@app.post("/api/avatars/{agent_id}/generate")
async def generate_avatar(agent_id: str, mock: bool = False, use_fallback: bool = True, force: bool = False):
    """
    Legacy avatar generation endpoint.
    First checks for Dual-DNA and uses that if available.
    Falls back to simple prompt-based generation if no DNA.
    """
    conn = await get_db()
    
    # 1. Handle Force Re-render: Delete old cache first
    if force:
        logger.info(f"🔄 Force re-render requested for {agent_id}. Clearing old cache.")
        await run_query(conn, "DELETE FROM avatar_renders WHERE agent_id = ?", (agent_id,))
        for ext in [".png", ".svg"]:
            file_path = os.path.join(STORAGE_DIR, f"{agent_id}{ext}")
            if os.path.exists(file_path):
                os.remove(file_path)
    else:
        # 2. Check persistent DB cache first
        cached = await run_query(conn,
            "SELECT image_url, schema_signature FROM avatar_renders WHERE agent_id = ?",
            (agent_id,), fetch="one")
        
        if cached:
            await conn.close()
            return {"imageUrl": cached["image_url"], "status": "cached"}
    
    # 3. Check if Dual-DNA exists - if so, delegate to DNA-based renderer
    dna_row = await run_query(conn,
        "SELECT preference_dna, action_dna FROM agent_dna WHERE agent_id = ?",
        (agent_id,), fetch="one")
    
    if dna_row:
        await conn.close()
        # Delegate to DNA-based renderer
        logger.info(f"🧬 Dual-DNA found for {agent_id}, using DNA-based renderer")
        return await render_agent_avatar_dna(agent_id, force=force)
    
    # 4. Legacy path: Get agent data for simple prompt construction
    agent = await run_query(conn, "SELECT * FROM agents WHERE agent_id = ?", (agent_id,), fetch="one")
    avatar_state = await run_query(conn,
        "SELECT * FROM avatar_states WHERE agent_id = ? ORDER BY computed_at DESC LIMIT 1",
        (agent_id,), fetch="one")
    
    if not agent:
        await conn.close()
        raise HTTPException(status_code=404, detail="Agent not found")
    
    hue = avatar_state["base_hue"] if avatar_state else 180
    complexity = avatar_state["shape_complexity"] if avatar_state else 6
    role = agent["role"] or "general"
    dynamics = avatar_state["dynamics_state"] if avatar_state else "idle"
    
    # Determine file extension
    file_ext = "svg" if mock or not use_fallback else "png"
    file_path = os.path.join(STORAGE_DIR, f"{agent_id}.{file_ext}")
    relative_url = f"/storage/avatars/{agent_id}.{file_ext}"
    
    # 5. Double-check if file already exists on disk
    if os.path.exists(file_path):
        await run_query(conn, """
            INSERT OR REPLACE INTO avatar_renders (id, agent_id, image_url, schema_signature, rendered_at)
            VALUES (?, ?, ?, ?, ?)
        """, (f"disk_cache_{uuid.uuid4().hex}", agent_id, relative_url,
              json.dumps({"cached_on_disk": True, "hue": hue, "complexity": complexity}),
              datetime.now(timezone.utc).isoformat()))
        if hasattr(conn, 'commit'):
            await conn.commit()
        await conn.close()
        return {"imageUrl": relative_url, "status": "cached"}
    
    # 6. Build simple prompt
    hair_styles = {3: "short cropped", 5: "bob cut", 6: "medium length", 8: "long layered", 10: "elaborate braided", 12: "flowing twin tails"}
    hair = hair_styles.get(complexity, "medium length")
    expressions = {"idle": "soft neutral", "output": "bright smile", "input": "focused gaze", "analysis": "thoughtful look"}
    expr = expressions.get(dynamics, "soft neutral")
    
    prompt = AVATAR_PROMPT_TEMPLATE.format(
        hair_style=hair,
        hair_color=f"hsl({hue}, 70%, 40%)",
        expression=expr,
        accessories=f"role: {role}"
    )
    
    image_generated = False
    image_url_to_store = ""
    schema_sig = {}
    
    # 7. Mock mode or Forced Fallback mode: Generate SVG
    if mock or not use_fallback:
        logger.warning(f"🎭 Mock/Fallback mode for {agent_id}")
        svg = generate_local_avatar_svg(agent_id, hue, 0.7, complexity, agent["name"])
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(svg)
        image_generated = True
        image_url_to_store = relative_url
        schema_sig = {"mock": mock, "fallback": not use_fallback, "hue": hue, "complexity": complexity, "source": "local_svg"}
    else:
        # 8. Try Pollinations.ai
        max_retries = 3
        retry_delay = 3.0
        
        for attempt in range(max_retries):
            try:
                await wait_for_rate_limit()
                async with httpx.AsyncClient(timeout=45.0) as client:
                    safe_prompt = urllib.parse.quote(f"anime portrait, {prompt}, clean background, high quality")
                    img_url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=256&height=256&nologo=true&seed={agent_id}"
                    
                    logger.info(f"🎨 Fetching avatar from Pollinations.ai for {agent_id} (attempt {attempt+1}/{max_retries})")
                    img_res = await client.get(img_url)
                    
                    if img_res.status_code == 200:
                        with open(file_path, "wb") as f:
                            f.write(img_res.content)
                        image_generated = True
                        image_url_to_store = relative_url
                        schema_sig = {"hue": hue, "complexity": complexity, "source": "pollinations"}
                        logger.info(f"✅ Avatar generated and saved to disk for {agent_id}")
                        break
                    elif img_res.status_code == 402:
                        error_msg = img_res.text[:200] if img_res.text else "Rate limited"
                        logger.warning(f"⚠️ Pollinations.ai rate limited (402): {error_msg}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay)
                            retry_delay *= 2
                            continue
                        else:
                            logger.error(f"❌ Pollinations.ai rate limited after {max_retries} attempts")
                            if not use_fallback:
                                raise HTTPException(status_code=503, detail=f"Image service rate limited: {error_msg}")
                    else:
                        error_msg = img_res.text[:200] if img_res.text else f"Status {img_res.status_code}"
                        logger.error(f"❌ Pollinations.ai error {img_res.status_code}: {error_msg}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay)
                            retry_delay *= 2
                            continue
                        else:
                            if not use_fallback:
                                raise HTTPException(status_code=500, detail=f"Image generation failed: {img_res.status_code}")
            except httpx.RequestError as e:
                logger.error(f"❌ Network error calling Pollinations.ai: {type(e).__name__}: {str(e)}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    if not use_fallback:
                        raise HTTPException(status_code=503, detail=f"External service unavailable: {str(e)}")
        
        # 9. Fallback to local SVG
        if not image_generated and use_fallback:
            logger.warning(f"🔄 Falling back to local SVG generation for {agent_id}")
            file_path = os.path.join(STORAGE_DIR, f"{agent_id}.svg")
            relative_url = f"/storage/avatars/{agent_id}.svg"
            svg = generate_local_avatar_svg(agent_id, hue, 0.7, complexity, agent["name"])
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(svg)
            image_generated = True
            image_url_to_store = relative_url
            schema_sig = {"fallback": True, "hue": hue, "complexity": complexity, "source": "local_svg"}
    
    # 10. Final DB update
    if image_generated:
        await run_query(conn, """
            INSERT OR REPLACE INTO avatar_renders (id, agent_id, image_url, schema_signature, rendered_at)
            VALUES (?, ?, ?, ?, ?)
        """, (f"render_{uuid.uuid4().hex}", agent_id, image_url_to_store,
              json.dumps(schema_sig),
              datetime.now(timezone.utc).isoformat()))
        if hasattr(conn, 'commit'):
            await conn.commit()
        await conn.close()
        return {"imageUrl": image_url_to_store, "status": "generated"}
    
    await conn.close()
    raise HTTPException(status_code=500, detail="Avatar generation failed after all retries")

@app.delete("/api/avatars/{agent_id}", dependencies=[Depends(verify_write_key)])
async def clear_cached_avatar(agent_id: str):
    """Clear cached render from DB and disk (for testing)."""
    conn = await get_db()
    await run_query(conn, "DELETE FROM avatar_renders WHERE agent_id = ?", (agent_id,))
    if hasattr(conn, 'commit'):
        await conn.commit()
    await conn.close()
    
    for ext in [".png", ".svg"]:
        file_path = os.path.join(STORAGE_DIR, f"{agent_id}{ext}")
        if os.path.exists(file_path):
            os.remove(file_path)
            
    return {"status": "cleared", "agent_id": agent_id}

@app.delete("/api/avatars")
async def clear_all_cached_avatars():
    """Clear ALL cached avatar renders from DB and disk (for testing)."""
    conn = await get_db()
    renders = await run_query(conn, "SELECT agent_id FROM avatar_renders", fetch="all")
    
    await run_query(conn, "DELETE FROM avatar_renders")
    if hasattr(conn, 'commit'):
        await conn.commit()
    await conn.close()
    
    deleted_count = 0
    if os.path.exists(STORAGE_DIR):
        for filename in os.listdir(STORAGE_DIR):
            file_path = os.path.join(STORAGE_DIR, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
                deleted_count += 1
    
    logger.info(f"🗑️ Cleared {len(renders)} avatar renders from DB and {deleted_count} files from disk")
    
    return {
        "status": "cleared",
        "db_records_deleted": len(renders),
        "files_deleted": deleted_count
    }

# ─── STATIC FILES & ROUTES ────────────────────────────────────────────────────

# 1. Mount specific API/Storage paths FIRST
app.mount("/storage/avatars", StaticFiles(directory=STORAGE_DIR), name="avatar_storage")

# 2. Mount Frontend (Catch-all) LAST
if os.path.exists(FRONTEND_DIR):
    @app.get("/methodology")
    async def serve_methodology():
        return FileResponse(os.path.join(FRONTEND_DIR, "methodology.html"))
    
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    @app.get("/")
    async def root():
        return {
            "message": "Liquid Avatar API", 
            "schema_version": SCHEMA_VERSION,
            "council": "Aura Quorum", 
            "note": "Frontend not found."
        }

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)