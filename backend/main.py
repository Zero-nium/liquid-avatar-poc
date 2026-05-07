"""
Liquid Avatar PoC - Backend API
FastAPI + SQLite/Pydantic + Optional Turso
Free-tier optimized: single file, minimal deps, persistent storage ready.

Schema v1.1: Expertise→Color, Role→Geometry, Activity→Dynamics
"""

from fastapi import FastAPI, HTTPException, Depends, Security, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
import sqlite3
import json
import os
import asyncio
import random
import re

# Turso/libSQL support (optional, falls back to SQLite if not configured)
try:
    from libsql.client import create_client
    LIBSQL_AVAILABLE = True
except ImportError:
    LIBSQL_AVAILABLE = False

# ─── CONFIG ───────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "./liquid_avatar.db")
SCHEMA_VERSION = "1.1"
API_KEY = os.getenv("LIQUID_AVATAR_API_KEY", "dev-key-change-me-for-prod")

# Turso/libSQL configuration (optional)
TURSO_URL = os.getenv("TURSO_URL")
TURSO_TOKEN = os.getenv("TURSO_TOKEN")
USE_TURSO = LIBSQL_AVAILABLE and TURSO_URL and TURSO_TOKEN

# Resolve frontend path relative to this file
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(os.path.dirname(BASE_DIR), "frontend")

# API Key header for auth
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

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

# ─── DATABASE UTILS ───────────────────────────────────────────────────────────

def get_db():
    """Get database connection — supports both SQLite and Turso/libSQL."""
    if USE_TURSO:
        return create_client(url=TURSO_URL, auth_token=TURSO_TOKEN)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

def execute_sql(db, sql: str):
    """Execute a SQL statement, handling both SQLite and Turso/libSQL."""
    if hasattr(db, 'cursor'):  # SQLite
        db.cursor().execute(sql)
    else:  # Turso libsql client
        db.execute(sql)

def run_query(db, sql: str, params: tuple = None, fetch: str = None):
    """Unified query runner for SQLite & Turso with parameter binding."""
    params = params or ()
    if hasattr(db, 'cursor'):  # SQLite
        cur = db.cursor()
        cur.execute(sql, params)
        if fetch == 'all':
            return cur.fetchall()
        if fetch == 'one':
            return cur.fetchone()
        return cur
    else:  # Turso libsql client
        result = db.execute(sql, params)
        if fetch == 'all':
            return result.rows
        if fetch == 'one':
            return result.rows[0] if result.rows else None
        return result

def init_db():
    """Initialize database schema — works with SQLite or Turso."""
    conn = get_db()
    
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
        )"""
    ]
    
    for stmt in tables:
        execute_sql(conn, stmt)
    
    if hasattr(conn, 'commit'):
        conn.commit()
    conn.close()

def seed_ontology():
    """Seed canonical ontology domains."""
    conn = get_db()
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
        run_query(conn, """
            INSERT OR IGNORE INTO ontology (domain, base_hue, spectrum, geometry_hint)
            VALUES (?, ?, ?, ?)
        """, (domain, hue, spectrum, geom))
    
    if hasattr(conn, 'commit'):
        conn.commit()
    conn.close()

def compute_avatar_signature(agent_id: str, proficiencies: List[Proficiency], activity_status: str) -> AvatarSignature:
    """Compute avatar visual signature from agent proficiencies."""
    conn = get_db()
    
    if not proficiencies:
        dominant_domain = "general"
        avg_level = 0.5
        skill_count = 0
    else:
        categories = {}
        for p in proficiencies:
            categories[p.category] = categories.get(p.category, 0) + p.level
        dominant_category = max(categories, key=categories.get) if categories else "general"
        
        row = run_query(conn, "SELECT domain FROM ontology WHERE domain = ?", (dominant_category,), fetch="one")
        dominant_domain = dominant_category if row else "general"
        
        avg_level = sum(p.level for p in proficiencies) / len(proficiencies)
        skill_count = len(proficiencies)
    
    row = run_query(conn, "SELECT base_hue, spectrum, geometry_hint FROM ontology WHERE domain = ?", (dominant_domain,), fetch="one")
    if row:
        base_hue, spectrum_json, geometry_hint = row["base_hue"], row["spectrum"], row["geometry_hint"]
    else:
        base_hue, geometry_hint = 180, "hexagon"
    
    # Shape complexity
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
    
    conn.close()
    
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
    """Protect write endpoints; read endpoints remain open."""
    if key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return key

# ─── FASTAPI APP ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_ontology()
    
    # Auto-seed mock swarm if DB is empty (for fresh deploys)
    conn = get_db()
    count = run_query(conn, "SELECT COUNT(*) as c FROM agents", fetch="one")
    if count and count["c"] == 0:
        # Seed minimal mock data silently
        mock = [
            ("aura_quorum", "Aura Quorum", None, "conductor", "council"),
            ("astra", "Astra", "aura_quorum", "architect", "council"),
        ]
        for aid, name, init_by, role, cluster in mock:
            run_query(conn, """
                INSERT OR IGNORE INTO agents (agent_id, name, initialized_by, swarm_cluster, role)
                VALUES (?, ?, ?, ?, ?)
            """, (aid, name, init_by, cluster, role))
        if hasattr(conn, 'commit'):
            conn.commit()
    conn.close()
    
    yield

app = FastAPI(title="Liquid Avatar API", version=SCHEMA_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── ENDPOINTS ────────────────────────────────────────────────────────────────

@app.post("/agents/report", response_model=AgentState, dependencies=[Depends(verify_write_key)])
async def report_agent_state(report: AgentReport):
    conn = get_db()
    agent_row = run_query(conn, "SELECT * FROM agents WHERE agent_id = ?", (report.agent_id,), fetch="one")
    
    if not agent_row:
        raise HTTPException(status_code=404, detail=f"Agent {report.agent_id} not found")
    
    now = datetime.now(timezone.utc).isoformat()
    
    for p in report.proficiencies:
        run_query(conn, """
            INSERT INTO proficiencies (agent_id, skill, level, category, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (report.agent_id, p.skill, p.level, p.category, now))
    
    run_query(conn, """
        INSERT INTO activity_log (agent_id, status, task, timestamp)
        VALUES (?, ?, ?, ?)
    """, (report.agent_id, report.activity_status, report.current_task, now))
    
    run_query(conn, "UPDATE agents SET last_reported = ? WHERE agent_id = ?", (now, report.agent_id))
    
    avatar = compute_avatar_signature(report.agent_id, report.proficiencies, report.activity_status)
    
    run_query(conn, """
        INSERT INTO avatar_states (agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, dynamics_state, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (report.agent_id, avatar.base_hue, avatar.saturation, avatar.shape_complexity,
          avatar.pulse_rate, avatar.size, avatar.dynamics_state, now))
    
    if hasattr(conn, 'commit'):
        conn.commit()
    conn.close()
    
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
    conn = get_db()
    try:
        run_query(conn, """
            INSERT INTO agents (agent_id, name, initialized_by, swarm_cluster, role)
            VALUES (?, ?, ?, ?, ?)
        """, (agent_id, name, initialized_by, swarm_cluster, role))
        if hasattr(conn, 'commit'):
            conn.commit()
        return {"status": "registered", "agent_id": agent_id}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Agent already exists")
    finally:
        conn.close()

@app.post("/agents/discover", response_model=AgentState, dependencies=[Depends(verify_write_key)])
async def agent_self_discover(request: AgentDiscoverRequest):
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    
    run_query(conn, """
        INSERT OR REPLACE INTO agents (agent_id, name, initialized_by, swarm_cluster, role, last_reported)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (request.agent_id, request.name, request.initialized_by, request.swarm_cluster, request.role, now))
    
    if request.proficiencies:
        for p in request.proficiencies:
            exists = run_query(conn, """
                SELECT id FROM proficiencies WHERE agent_id = ? AND skill = ? AND category = ?
            """, (request.agent_id, p.skill, p.category), fetch="one")
            if not exists:
                run_query(conn, """
                    INSERT INTO proficiencies (agent_id, skill, level, category, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                """, (request.agent_id, p.skill, p.level, p.category, now))
    
    run_query(conn, """
        INSERT INTO activity_log (agent_id, status, task, timestamp)
        VALUES (?, ?, ?, ?)
    """, (request.agent_id, request.activity_status, request.current_task, now))
    
    avatar = compute_avatar_signature(request.agent_id, request.proficiencies or [], request.activity_status)
    
    run_query(conn, """
        INSERT INTO avatar_states (agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, dynamics_state, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (request.agent_id, avatar.base_hue, avatar.saturation, avatar.shape_complexity,
          avatar.pulse_rate, avatar.size, avatar.dynamics_state, now))
    
    if hasattr(conn, 'commit'):
        conn.commit()
    conn.close()
    
    return AgentState(
        agent_id=request.agent_id,
        identity=AgentIdentity(name=request.name, initialized_by=request.initialized_by,
                              swarm_cluster=request.swarm_cluster, role=request.role),
        proficiencies=request.proficiencies or [],
        activity={"status": request.activity_status, "task": request.current_task, "timestamp": now},
        avatar_signature=avatar,
        reported_at=now
    )

@app.post("/agents/heartbeat", dependencies=[Depends(verify_write_key)])
async def agent_heartbeat(request: HeartbeatRequest):
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    
    agent = run_query(conn, "SELECT name FROM agents WHERE agent_id = ?", (request.agent_id,), fetch="one")
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not registered")
    
    run_query(conn, "UPDATE agents SET last_reported = ? WHERE agent_id = ?", (now, request.agent_id))
    
    if request.activity_status or request.current_task:
        run_query(conn, """
            INSERT INTO activity_log (agent_id, status, task, timestamp)
            VALUES (?, ?, ?, ?)
        """, (request.agent_id, request.activity_status or "idle", request.current_task, now))
        
        run_query(conn, """
            INSERT INTO avatar_states (agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, dynamics_state, computed_at)
            SELECT agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, ?, ?
            FROM avatar_states WHERE agent_id = ?
            ORDER BY computed_at DESC LIMIT 1
        """, (request.activity_status or "idle", now, request.agent_id))
    
    if hasattr(conn, 'commit'):
        conn.commit()
    conn.close()
    return {"status": "ok", "agent_id": request.agent_id, "timestamp": now}

@app.get("/agents/discoverable")
async def get_discoverable_agents(limit: int = 50, cluster: Optional[str] = None):
    conn = get_db()
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
    
    rows = run_query(conn, query, tuple(params), fetch="all")
    conn.close()
    
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
    conn = get_db()
    rows = run_query(conn, """
        SELECT a.*, av.base_hue, av.saturation, av.shape_complexity, av.pulse_rate, av.size, av.dynamics_state
        FROM agents a
        LEFT JOIN avatar_states av ON a.agent_id = av.agent_id
        WHERE av.computed_at = (SELECT MAX(computed_at) FROM avatar_states WHERE agent_id = a.agent_id)
        OR av.computed_at IS NULL
    """, fetch="all")
    conn.close()
    
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
    conn = get_db()
    agent_row = run_query(conn, "SELECT * FROM agents WHERE agent_id = ?", (agent_id,), fetch="one")
    if not agent_row:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    profs = run_query(conn, "SELECT skill, level, category, timestamp FROM proficiencies WHERE agent_id = ? ORDER BY timestamp DESC", (agent_id,), fetch="all")
    avatar_row = run_query(conn, "SELECT * FROM avatar_states WHERE agent_id = ? ORDER BY computed_at DESC LIMIT 1", (agent_id,), fetch="one")
    activity = run_query(conn, "SELECT status, task, timestamp FROM activity_log WHERE agent_id = ? ORDER BY timestamp DESC LIMIT 20", (agent_id,), fetch="all")
    conn.close()
    
    return {
        "agent_id": agent_id,
        "identity": {"name": agent_row["name"], "initialized_by": agent_row["initialized_by"],
                    "swarm_cluster": agent_row["swarm_cluster"], "role": agent_row["role"]},
        "proficiencies": [{"skill": r["skill"], "level": r["level"], "category": r["category"], "timestamp": r["timestamp"]} for r in profs],
        "avatar": {"base_hue": avatar_row["base_hue"], "saturation": avatar_row["saturation"],
                  "shape_complexity": avatar_row["shape_complexity"], "pulse_rate": avatar_row["pulse_rate"],
                  "size": avatar_row["size"], "dynamics_state": avatar_row["dynamics_state"]} if avatar_row else None,
        "activity_history": [{"status": r["status"], "task": r["task"], "timestamp": r["timestamp"]} for r in activity]
    }

@app.get("/ontology")
async def get_ontology():
    conn = get_db()
    rows = run_query(conn, "SELECT * FROM ontology", fetch="all")
    conn.close()
    return {
        "version": SCHEMA_VERSION, "origin": "Aura Quorum / Small Council",
        "domains": [{"domain": r["domain"], "base_hue": r["base_hue"],
                    "spectrum": json.loads(r["spectrum"]), "geometry_hint": r["geometry_hint"]} for r in rows]
    }

@app.get("/swarm/map")
async def get_swarm_map():
    conn = get_db()
    rows = run_query(conn, """
        SELECT a.agent_id, a.name, a.initialized_by, a.role, a.swarm_cluster,
               av.base_hue, av.saturation, av.shape_complexity, av.pulse_rate, av.size, av.dynamics_state
        FROM agents a
        LEFT JOIN avatar_states av ON a.agent_id = av.agent_id
        WHERE av.computed_at = (SELECT MAX(computed_at) FROM avatar_states WHERE agent_id = a.agent_id)
        OR av.computed_at IS NULL
    """, fetch="all")
    
    nodes, edges, agent_ids = [], [], set()
    for row in rows:
        node = {
            "id": row["agent_id"], "name": row["name"], "role": row["role"], "cluster": row["swarm_cluster"],
            "avatar": {
                "base_hue": row["base_hue"] if row["base_hue"] is not None else 180,
                "saturation": row["saturation"] if row["saturation"] is not None else 0.8,
                "shape_complexity": row["shape_complexity"] if row["shape_complexity"] is not None else 6,
                "pulse_rate": row["pulse_rate"] if row["pulse_rate"] is not None else 1.0,
                "size": row["size"] if row["size"] is not None else 20,
                "dynamics_state": row["dynamics_state"] if row["dynamics_state"] is not None else "idle"
            }
        }
        nodes.append(node)
        agent_ids.add(row["agent_id"])
    
    for row in nodes:
        init_row = run_query(conn, "SELECT initialized_by FROM agents WHERE agent_id = ?", (row["id"],), fetch="one")
        if init_row and init_row["initialized_by"] and init_row["initialized_by"] in agent_ids:
            edges.append({"source": init_row["initialized_by"], "target": row["id"], "type": "initialization"})
    
    conn.close()
    return {"nodes": nodes, "edges": edges, "node_count": len(nodes), "edge_count": len(edges)}

@app.post("/seed/mock-swarm")
async def seed_mock_swarm():
    conn = get_db()
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
        run_query(conn, """
            INSERT OR IGNORE INTO agents (agent_id, name, initialized_by, swarm_cluster, role)
            VALUES (?, ?, ?, ?, ?)
        """, (aid, name, init_by, cluster, role))
    if hasattr(conn, 'commit'):
        conn.commit()
    conn.close()
    
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
    
    return {"status": "seeded", "agents": len(mock_agents), "reports": len(mock_reports)}

@app.get("/health")
async def health():
    return {"status": "ok", "schema_version": SCHEMA_VERSION, "origin": "Aura Quorum"}

# ─── MCP ENDPOINT (Agent-to-Agent) ────────────────────────────────────────────

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
    conn = get_db()
    
    if query.query_type == "list_agents":
        rows = run_query(conn, "SELECT agent_id, name, role, swarm_cluster FROM agents", fetch="all")
        data = [{"id": r["agent_id"], "name": r["name"], "role": r["role"], "cluster": r["swarm_cluster"]} for r in rows]
    elif query.query_type == "get_profile":
        if not query.agent_id:
            raise HTTPException(status_code=400, detail="agent_id required")
        agent = run_query(conn, "SELECT * FROM agents WHERE agent_id = ?", (query.agent_id,), fetch="one")
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        avatar = run_query(conn, "SELECT * FROM avatar_states WHERE agent_id = ? ORDER BY computed_at DESC LIMIT 1", (query.agent_id,), fetch="one")
        data = {"agent_id": agent["agent_id"], "name": agent["name"], "role": agent["role"], "avatar": dict(avatar) if avatar else None}
    elif query.query_type == "get_ontology":
        rows = run_query(conn, "SELECT * FROM ontology", fetch="all")
        data = [{"domain": r["domain"], "base_hue": r["base_hue"], "spectrum": json.loads(r["spectrum"]), "geometry_hint": r["geometry_hint"]} for r in rows]
    elif query.query_type == "swarm_topology":
        edges = run_query(conn, "SELECT agent_id, initialized_by FROM agents WHERE initialized_by IS NOT NULL", fetch="all")
        count = run_query(conn, "SELECT COUNT(*) as c FROM agents", fetch="one")
        data = {"node_count": count["c"] if count else 0, "edges": [{"source": r["initialized_by"], "target": r["agent_id"]} for r in edges]}
    else:
        data = {"error": "Unknown query type", "supported": ["list_agents", "get_profile", "get_ontology", "swarm_topology"]}
    
    conn.close()
    return MCPResponse(query_type=query.query_type, data=data, timestamp=now)

@app.get("/mcp/health")
async def mcp_health():
    return {"status": "ok", "protocol": "MCP", "version": "1.0"}

# ─── STATIC FILES (FRONTEND) ──────────────────────────────────────────────────

if os.path.exists(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    @app.get("/")
    async def root():
        return {"message": "Liquid Avatar API", "schema_version": SCHEMA_VERSION,
                "council": "Aura Quorum", "note": "Frontend not found. Ensure frontend/ exists alongside backend/"}

# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)