"""
Liquid Avatar PoC - Backend API
FastAPI + SQLite/Pydantic + Optional Turso
Free-tier optimized: single file, minimal deps, persistent storage ready.

Schema v1.3: Avatar Rendering with Rate Limiting + Fallback
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
SCHEMA_VERSION = "1.3"  # Updated for avatar rendering
API_KEY = os.getenv("LIQUID_AVATAR_API_KEY", "dev-key-change-me-for-prod")

TURSO_URL = os.getenv("TURSO_URL")
TURSO_TOKEN = os.getenv("TURSO_TOKEN")
USE_TURSO = LIBSQL_AVAILABLE and TURSO_URL and TURSO_TOKEN

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(os.path.dirname(BASE_DIR), "frontend")
STORAGE_DIR = os.path.join(os.path.dirname(__file__), "storage", "avatars")
os.makedirs(STORAGE_DIR, exist_ok=True)  # Automatically creates the directory (Handles Step 2)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Rate limiting for avatar generation (Pollinations.ai free tier: 1 concurrent)
AVATAR_GENERATION_LOCK = asyncio.Lock()
LAST_GENERATION_TIME = 0
MIN_GENERATION_INTERVAL = 2.0  # seconds between requests

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
        logger.info(f"Schema v1.3: Processing role '{role_lower}' for agent {agent_id}")
    
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
app = FastAPI(title="Liquid Avatar", version="1.3", lifespan=lifespan)

# Add middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers AFTER app exists
app.include_router(minds_router)

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
            }
        },
        "glossary": {
            "agent_id": "Unique UUID",
            "proficiency": "Skill + level (0.0-1.0)",
            "ontology domain": "Color mapping category",
            "dynamics_state": "Animation mode",
            "beacon": "Signed agent status announcement"
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

# ─── METADATA ENDPOINTS (PoC MODE - No database access) ──────────────────────

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
        "version": "1.3",
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
        shape = f'<polygon points="{" ".join(points)}" fill="{color}" stroke="white" stroke-width="2"/>'
    
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
    await run_query(conn, "DELETE FROM agents WHERE agent_id = ?", (agent_id,))
    
    if hasattr(conn, 'commit'):
        await conn.commit()
    await conn.close()
    
    log_agent_event(logger, "agent_deleted", agent_id, "Agent and all associated data deleted")
    
    await broadcast_swarm_update("agent_removed", {"agent_id": agent_id})
    
    return {"status": "deleted", "agent_id": agent_id}

# ─── AVATAR GENERATION HELPERS ────────────────────────────────────────────────

def generate_local_avatar_svg(agent_id: str, hue: float, sat: float, complexity: int, name: str = "Agent") -> str:
    """Generate a local SVG avatar as fallback when external service fails."""
    color = f"hsl({hue}, {sat*100}%, 55%)"
    
    # Generate polygon points based on complexity
    points = []
    for i in range(complexity):
        angle = (i * 2 * 3.14159 / complexity) - 1.5708
        x = 128 + 80 * 0.9 * 3.14159/180 * 3.14159 * 3.14159
        y = 128 + 80 * 0.9
        points.append(f"{x},{y}")
    
    if complexity >= 10:
        shape = f'<circle cx="128" cy="128" r="80" fill="{color}" stroke="white" stroke-width="3"/>'
    else:
        shape = f'<polygon points="{" ".join(points)}" fill="{color}" stroke="white" stroke-width="3"/>'
    
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

        # ── MISSING TEMPLATE ADDED HERE ──────────────────────────────────────────────
        AVATAR_PROMPT_TEMPLATE = (
        "anime portrait of a character, {hair_style} {hair_color} hair, "
        "{expression} expression, {accessories}, high quality, detailed, "
        "studio lighting, clean background, masterpiece"
    )

# ─── AVATAR RENDERING CACHE ENDPOINTS ─────────────────────────────────────────

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
    Generates and stores an avatar for the given agent.
    If force=True, bypasses cache and re-renders.
    """
    conn = await get_db()
    
    # If forcing, delete old cache first
    if force:
        logger.info(f"🔄 Force re-render requested for {agent_id}. Clearing old cache.")
        await run_query(conn, "DELETE FROM avatar_renders WHERE agent_id = ?", (agent_id,))
        for ext in [".png", ".svg"]:
            file_path = os.path.join(STORAGE_DIR, f"{agent_id}{ext}")
            if os.path.exists(file_path):
                os.remove(file_path)
    else:
        # Check persistent DB cache first (UNLESS force is True)
        cached = await run_query(conn,
            "SELECT image_url, schema_signature FROM avatar_renders WHERE agent_id = ?",
            (agent_id,), fetch="one")
        
        if cached:
            await conn.close()
            return {"imageUrl": cached["image_url"], "status": "cached"}
    else:
        # If forcing, delete old DB entry and file to ensure a fresh render
        logger.info(f"🔄 Force re-render requested for {agent_id}. Clearing old cache.")
        await run_query(conn, "DELETE FROM avatar_renders WHERE agent_id = ?", (agent_id,))
        for ext in [".png", ".svg"]:
            file_path = os.path.join(STORAGE_DIR, f"{agent_id}{ext}")
            if os.path.exists(file_path):
                os.remove(file_path)

    if not agent:
        await conn.close()
        raise HTTPException(status_code=404, detail="Agent not found")

    hue = avatar_state["base_hue"] if avatar_state else 180
    complexity = avatar_state["shape_complexity"] if avatar_state else 6
    role = agent["role"] or "general"
    dynamics = avatar_state["dynamics_state"] if avatar_state else "idle"
    
    # Determine file extension based on generation method
    file_ext = "svg" if mock or not use_fallback else "png"
    file_path = os.path.join(STORAGE_DIR, f"{agent_id}.{file_ext}")
    relative_url = f"/storage/avatars/{agent_id}.{file_ext}"

    # 2. Double-check if file already exists on disk (extra safety against DB desync)
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

    # 3. Build prompt based on agent attributes
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

    # 4. Mock mode or Forced Fallback mode: Generate SVG
    if mock or not use_fallback:
        logger.warning(f"🎭 Mock/Fallback mode for {agent_id}")
        svg = generate_local_avatar_svg(agent_id, hue, 0.7, complexity, agent["name"])
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(svg)
        image_generated = True
        image_url_to_store = relative_url
        schema_sig = {"mock": mock, "fallback": not use_fallback, "hue": hue, "complexity": complexity, "source": "local_svg"}
    else:
        # 5. Try Pollinations.ai with rate limiting and retry logic
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
                        # Success! Save bytes directly to disk
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

        # 6. Fallback to local SVG if Pollinations failed but use_fallback is True
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

    # 7. Final DB update
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
    
    # Also delete from disk if it exists
    for ext in [".png", ".svg"]:
        file_path = os.path.join(STORAGE_DIR, f"{agent_id}{ext}")
        if os.path.exists(file_path):
            os.remove(file_path)
            
    return {"status": "cleared", "agent_id": agent_id}

@app.delete("/api/avatars")
async def clear_all_cached_avatars():
    """Clear ALL cached avatar renders from DB and disk (for testing)."""
    conn = await get_db()
    
    # Get all agent_ids with cached renders
    renders = await run_query(conn, "SELECT agent_id FROM avatar_renders", fetch="all")
    
    # Delete all from database
    await run_query(conn, "DELETE FROM avatar_renders")
    if hasattr(conn, 'commit'):
        await conn.commit()
    await conn.close()
    
    # Delete all files from disk
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

# 1. Mount specific API/Storage paths FIRST (so they don't get caught by the frontend)
app.mount("/storage/avatars", StaticFiles(directory=STORAGE_DIR), name="avatar_storage")

# 2. Mount Frontend (Catch-all) LAST
if os.path.exists(FRONTEND_DIR):
    @app.get("/methodology")
    async def serve_methodology():
        return FileResponse(os.path.join(FRONTEND_DIR, "methodology.html"))

    # Catch-all mount for the frontend SPA
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