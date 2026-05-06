"""
Liquid Avatar — MCP (Model Context Protocol) Server
Provides agent-to-agent interface for the Liquid Avatar visualization.
Free-tier: runs alongside main API, minimal overhead.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import sqlite3
import json
import os

DB_PATH = os.getenv("DB_PATH", "./liquid_avatar.db")

class MCPQuery(BaseModel):
    agent_id: Optional[str] = None
    query_type: str  # list_agents, get_profile, subscribe_changes, propose_rule
    parameters: Optional[Dict[str, Any]] = None

class MCPResponse(BaseModel):
    query_type: str
    data: Any
    timestamp: str

mcp_app = FastAPI(title="Liquid Avatar MCP")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@mcp_app.post("/mcp/query")
async def mcp_query(query: MCPQuery):
    """MCP endpoint for agent-to-agent communication."""
    from datetime import datetime
    now = datetime.utcnow().isoformat()

    conn = get_db()
    cursor = conn.cursor()

    if query.query_type == "list_agents":
        cursor.execute("SELECT agent_id, name, role, swarm_cluster FROM agents")
        rows = cursor.fetchall()
        data = [{"id": r["agent_id"], "name": r["name"], "role": r["role"], "cluster": r["swarm_cluster"]} for r in rows]

    elif query.query_type == "get_profile":
        if not query.agent_id:
            raise HTTPException(status_code=400, detail="agent_id required")
        cursor.execute("SELECT * FROM agents WHERE agent_id = ?", (query.agent_id,))
        agent = cursor.fetchone()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        cursor.execute("SELECT * FROM avatar_states WHERE agent_id = ? ORDER BY computed_at DESC LIMIT 1", (query.agent_id,))
        avatar = cursor.fetchone()

        data = {
            "agent_id": agent["agent_id"],
            "name": agent["name"],
            "role": agent["role"],
            "avatar": dict(avatar) if avatar else None
        }

    elif query.query_type == "get_ontology":
        cursor.execute("SELECT * FROM ontology")
        rows = cursor.fetchall()
        data = [{"domain": r["domain"], "base_hue": r["base_hue"], 
                 "spectrum": json.loads(r["spectrum"]), "geometry_hint": r["geometry_hint"]} for r in rows]

    elif query.query_type == "swarm_topology":
        cursor.execute("SELECT agent_id, initialized_by FROM agents WHERE initialized_by IS NOT NULL")
        edges = [{"source": r["initialized_by"], "target": r["agent_id"]} for r in cursor.fetchall()]
        cursor.execute("SELECT COUNT(*) as count FROM agents")
        count = cursor.fetchone()["count"]
        data = {"node_count": count, "edges": edges}

    else:
        data = {"error": "Unknown query type", "supported": ["list_agents", "get_profile", "get_ontology", "swarm_topology"]}

    conn.close()
    return MCPResponse(query_type=query.query_type, data=data, timestamp=now)

@mcp_app.get("/mcp/health")
async def mcp_health():
    return {"status": "ok", "protocol": "MCP", "version": "1.0"}
