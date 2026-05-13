#!/usr/bin/env python3
"""
Network Probe — Discover Active Agents for Liquid Avatar
Scrapes public endpoints for agent metadata and ingests partial profiles.

Usage:
    python scripts/network_probe.py --hellominds --limit 100
    python scripts/network_probe.py --blockchain base --limit 50
    python scripts/network_probe.py --all --json > logs/probe_$(date +%Y%m%d).json

Run via cron (daily at 2 AM):
    0 2 * * * cd ~/liquid-avatar-poc && python scripts/network_probe.py --all --cron >> logs/probe.log 2>&1
"""
import argparse
import json
import requests
import sqlite3
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

# ─── CONFIG ───────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "./backend/liquid_avatar.db")
API_KEY = os.getenv("LIQUID_AVATAR_API_KEY", "dev-key")
BASE_URL = os.getenv("LIQUID_AVATAR_BASE", "https://liquid-avatar-poc.onrender.com")

# Rate limiting
REQUEST_DELAY = 1.0  # seconds between external API calls
MAX_RETRIES = 3

# ─── DIAGNOSTIC LOGGING ───────────────────────────────────────────────────────
def log_debug(msg: str):
    """Print debug message to stderr."""
    print(f"[PROBE-DEBUG] {msg}", file=sys.stderr)

# ─── DISCOVERY SOURCES ────────────────────────────────────────────────────────

def discover_hellominds_agents(limit: int = 100) -> List[Dict[str, Any]]:
    """
    Query HelloMinds public API for active agents.
    Replace with actual endpoint when available.
    """
    agents = []
    
    # Example: HelloMinds active agents endpoint (stub)
    url = "https://api.hellominds.ai/v1/agents/active"
    
    for attempt in range(MAX_RETRIES):
        try:
            res = requests.get(
                url, 
                params={"limit": limit, "fields": "id,name,role,last_active"},
                timeout=30,
                headers={"User-Agent": "LiquidAvatar-Probe/1.0"}
            )
            res.raise_for_status()
            data = res.json()
            
            # Parse response (adjust to actual API structure)
            for item in data.get("agents", []):
                agents.append({
                    "id": item.get("id") or item.get("agent_id"),
                    "name": item.get("name"),
                    "role": item.get("role"),
                    "last_seen": item.get("last_active"),
                    "source": "hellominds_api",
                    "metadata": {k: v for k, v in item.items() if k not in ["id", "name", "role", "last_active"]}
                })
            
            print(f"✅ HelloMinds: found {len(agents)} agents", file=sys.stderr)
            return agents
            
        except requests.exceptions.RequestException as e:
            print(f"⚠️  HelloMinds API error (attempt {attempt+1}/{MAX_RETRIES}): {e}", file=sys.stderr)
            time.sleep(REQUEST_DELAY * (attempt + 1))
    
    print(f"❌ HelloMinds: failed after {MAX_RETRIES} attempts", file=sys.stderr)
    return []

def discover_blockchain_agents(chain: str = "base", limit: int = 50) -> List[Dict[str, Any]]:
    """
    Query blockchain for agent-related transactions/events.
    Replace with actual indexer endpoint.
    """
    agents = []
    
    # Example: Base chain contract events (stub)
    # In production, use The Graph, Alchemy, or direct RPC
    url = f"https://api.base.org/v1/contracts/0x.../events"
    
    for attempt in range(MAX_RETRIES):
        try:
            res = requests.get(
                url,
                params={"limit": limit, "event": "AgentRegistered"},
                timeout=30,
                headers={"User-Agent": "LiquidAvatar-Probe/1.0"}
            )
            res.raise_for_status()
            data = res.json()
            
            # Parse events to extract agent IDs
            for event in data.get("events", []):
                # Extract from logs (adjust to actual event structure)
                agent_id = event.get("topics", [None])[1] if event.get("topics") else None
                if agent_id:
                    agents.append({
                        "id": agent_id,
                        "name": f"Unknown-{agent_id[:8]}",  # Fallback name
                        "role": None,
                        "last_seen": datetime.fromtimestamp(event.get("timestamp", 0), tz=timezone.utc).isoformat(),
                        "source": f"blockchain_{chain}",
                        "metadata": {"tx_hash": event.get("transactionHash"), "block": event.get("blockNumber")}
                    })
            
            print(f"✅ Blockchain ({chain}): found {len(agents)} agents", file=sys.stderr)
            return agents
            
        except requests.exceptions.RequestException as e:
            print(f"⚠️  Blockchain API error (attempt {attempt+1}/{MAX_RETRIES}): {e}", file=sys.stderr)
            time.sleep(REQUEST_DELAY * (attempt + 1))
    
    print(f"❌ Blockchain ({chain}): failed after {MAX_RETRIES} attempts", file=sys.stderr)
    return []

def discover_public_directory(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Query GitHub for Animoca-related repos/users as a working example.
    Replace with actual agent registries when available.
    """
    agents = []
    
    # Real, public GitHub API endpoint (no auth needed for basic queries)
    url = "https://api.github.com/search/users"
    
    for attempt in range(MAX_RETRIES):
        try:
            # Search for users/orgs related to Animoca/Minds
            res = requests.get(
                url,
                params={
                    "q": "animoca OR minds OR ethoswarm in:name,bio",
                    "per_page": min(limit, 30),  # GitHub API max per page
                    "sort": "joined",
                    "order": "desc"
                },
                timeout=30,
                headers={
                    "User-Agent": "LiquidAvatar-Probe/1.0",
                    "Accept": "application/vnd.github.v3+json"
                }
            )
            res.raise_for_status()
            data = res.json()
            
            for user in data.get("items", []):
                # Generate a deterministic agent_id from GitHub login
                agent_id = f"github-{user['id']}"
                
                agents.append({
                    "id": agent_id,
                    "name": user["login"],
                    "role": None,  # Will be "general" until enriched
                    "last_seen": user.get("updated_at"),
                    "source": "github_search",
                    "metadata": {
                        "avatar_url": user.get("avatar_url"),
                        "profile_url": user.get("html_url"),
                        "bio": user.get("bio"),
                        "location": user.get("location"),
                        "public_repos": user.get("public_repos")
                    }
                })
            
            print(f"✅ GitHub: found {len(agents)} potential agents", file=sys.stderr)
            return agents
            
        except requests.exceptions.RequestException as e:
            print(f"⚠️  GitHub API error (attempt {attempt+1}/{MAX_RETRIES}): {e}", file=sys.stderr)
            time.sleep(REQUEST_DELAY * (attempt + 1))
    
    print(f"❌ GitHub: failed after {MAX_RETRIES} attempts", file=sys.stderr)
    return []

# ─── INGESTION ────────────────────────────────────────────────────────────────

def get_db():
    """Get database connection (supports SQLite/Turso via env vars)."""
    # Reuse main.py's logic for consistency
    if os.getenv("TURSO_URL") and os.getenv("TURSO_TOKEN"):
        try:
            from libsql_client import create_client
            log_debug(f"Connecting to Turso: {os.getenv('TURSO_URL')[:50]}...")
            return create_client(url=os.getenv("TURSO_URL"), auth_token=os.getenv("TURSO_TOKEN"))
        except ImportError:
            log_debug("libsql_client not available, falling back to SQLite")
        except Exception as e:
            log_debug(f"Turso connection failed: {e}, falling back to SQLite")
    
    log_debug(f"Using SQLite: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ingest_partial_agent(agent: Dict[str, Any], source: str) -> Dict[str, Any]:
    """
    Register a minimally-inferred agent profile.
    Returns ingestion result for logging.
    """
    conn = get_db()
    
    agent_id = agent.get("id")
    name = agent.get("name") or f"Unknown-{agent_id[:8] if agent_id else '00000000'}"
    
    if not agent_id:
        log_debug(f"Skipping agent with no ID from {source}")
        return {"status": "skipped", "reason": "no_agent_id", "source": source}
    
    log_debug(f"Checking if {agent_id} exists...")
    
    # Check if already exists (handle SQLite vs Turso)
    exists = None
    try:
        if hasattr(conn, 'execute') and not hasattr(conn, 'row_factory'):  # Turso
            result = conn.execute("SELECT agent_id FROM agents WHERE agent_id = ?", (agent_id,))
            exists = result.rows[0] if result.rows else None
        else:  # SQLite
            cursor = conn.cursor()
            cursor.execute("SELECT agent_id FROM agents WHERE agent_id = ?", (agent_id,))
            exists = cursor.fetchone()
    except Exception as e:
        log_debug(f"Error checking existence for {agent_id}: {e}")
        if hasattr(conn, 'close'):
            conn.close()
        return {"status": "error", "agent_id": agent_id, "error": str(e), "source": source}
    
    if exists:
        log_debug(f"Agent {agent_id} already exists")
        if hasattr(conn, 'close'):
            conn.close()
        return {"status": "exists", "agent_id": agent_id, "source": source}
    
    # Insert minimal profile
    now = datetime.now(timezone.utc).isoformat()
    last_reported = now  # Forces agent to pass the 24h filter in /swarm/map
    
    log_debug(f"Inserting new agent {agent_id} from {source}")
    
    try:
        if hasattr(conn, 'execute') and not hasattr(conn, 'row_factory'):  # Turso
            # Insert agent record
            conn.execute("""
                INSERT INTO agents (agent_id, name, initialized_by, swarm_cluster, role, last_reported)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (agent_id, name, None, f"discovered_via_{source}", "general", last_reported))
            
            # Insert minimal avatar state (gray/hexagon/idle for unenriched)
            conn.execute("""
                INSERT INTO avatar_states (agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, dynamics_state, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (agent_id, 180, 0.3, 6, 1.0, 20, "idle", now))
            
            # Log discovery source
            metadata_json = json.dumps(agent.get("metadata", {}))
            conn.execute("""
                INSERT INTO activity_log (agent_id, status, task, timestamp)
                VALUES (?, ?, ?, ?)
            """, (agent_id, "discovered", f"source:{source}|meta:{metadata_json}", now))
            
        else:  # SQLite
            cursor = conn.cursor()
            
            # Insert agent record
            cursor.execute("""
                INSERT INTO agents (agent_id, name, initialized_by, swarm_cluster, role, last_reported)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (agent_id, name, None, f"discovered_via_{source}", "general", last_reported))
            
            # Insert minimal avatar state
            cursor.execute("""
                INSERT INTO avatar_states (agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, dynamics_state, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (agent_id, 180, 0.3, 6, 1.0, 20, "idle", now))
            
            # Log discovery source
            metadata_json = json.dumps(agent.get("metadata", {}))
            cursor.execute("""
                INSERT INTO activity_log (agent_id, status, task, timestamp)
                VALUES (?, ?, ?, ?)
            """, (agent_id, "discovered", f"source:{source}|meta:{metadata_json}", now))
        
        # Commit transaction - CRITICAL FOR TURSO
        if hasattr(conn, 'commit'):
            conn.commit()
            log_debug(f"Committed transaction for {agent_id}")
        
        if hasattr(conn, 'close'):
            conn.close()
        
        log_debug(f"✅ Successfully registered {agent_id}")
        return {"status": "registered", "agent_id": agent_id, "name": name, "source": source}
        
    except Exception as e:
        log_debug(f"❌ Error ingesting {agent_id}: {e}")
        if hasattr(conn, 'close'):
            conn.close()
        return {"status": "error", "agent_id": agent_id, "error": str(e), "source": source}

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def run_probe(sources: List[str], limit: int, json_output: bool, cron_mode: bool) -> Dict[str, Any]:
    """Run discovery probes and ingest results."""
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sources_queried": sources,
        "limit": limit,
        "ingestion": {"hellominds": [], "blockchain": [], "directory": []},
        "summary": {"total_discovered": 0, "newly_registered": 0, "errors": 0}
    }
    
    log_debug(f"Starting probe with sources: {sources}, limit: {limit}")
    
    # HelloMinds API
    if "hellominds" in sources:
        print("🔍 Querying HelloMinds API...", file=sys.stderr)
        agents = discover_hellominds_agents(limit)
        for agent in agents:
            result = ingest_partial_agent(agent, "hellominds_api")
            results["ingestion"]["hellominds"].append(result)
            if result["status"] == "registered":
                results["summary"]["newly_registered"] += 1
            elif result["status"] == "error":
                results["summary"]["errors"] += 1
        results["summary"]["total_discovered"] += len(agents)
        time.sleep(REQUEST_DELAY)
    
    # Blockchain events
    if "blockchain" in sources:
        for chain in ["base", "ethereum"]:  # Extend as needed
            print(f"🔍 Querying {chain} blockchain...", file=sys.stderr)
            agents = discover_blockchain_agents(chain, limit // 2)
            for agent in agents:
                result = ingest_partial_agent(agent, f"blockchain_{chain}")
                results["ingestion"]["blockchain"].append(result)
                if result["status"] == "registered":
                    results["summary"]["newly_registered"] += 1
                elif result["status"] == "error":
                    results["summary"]["errors"] += 1
            results["summary"]["total_discovered"] += len(agents)
            time.sleep(REQUEST_DELAY)
    
    # Public directories
    if "directory" in sources:
        print("🔍 Querying public directories...", file=sys.stderr)
        agents = discover_public_directory(limit)
        for agent in agents:
            result = ingest_partial_agent(agent, "public_directory")
            results["ingestion"]["directory"].append(result)
            if result["status"] == "registered":
                results["summary"]["newly_registered"] += 1
            elif result["status"] == "error":
                results["summary"]["errors"] += 1
        results["summary"]["total_discovered"] += len(agents)
    
    log_debug(f"Probe complete: {results['summary']}")
    return results

def format_output(results: Dict[str, Any], json_output: bool, cron_mode: bool) -> str:
    """Format results for console, JSON, or cron output."""
    if cron_mode:
        status = "OK" if results["summary"]["errors"] == 0 else "WARN"
        msg = f"{status} | {results['summary']['newly_registered']} registered, {results['summary']['total_discovered']} discovered"
        return f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    
    if json_output:
        return json.dumps(results, indent=2, default=str)
    
    # Human-readable console output
    lines = [
        f"🔍 Network Probe — {results['timestamp']}",
        f"📡 Sources: {', '.join(results['sources_queried'])}",
        f"",
    ]
    
    for source, ingestions in results["ingestion"].items():
        if ingestions:
            registered = sum(1 for r in ingestions if r.get("status") == "registered")
            errors = sum(1 for r in ingestions if r.get("status") == "error")
            lines.append(f"📦 {source}: {registered} registered, {errors} errors")
    
    lines.append("")
    lines.append(f"📊 Summary: {results['summary']['newly_registered']} new agents, "
                f"{results['summary']['total_discovered']} total discovered, "
                f"{results['summary']['errors']} errors")
    
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="Liquid Avatar Network Probe")
    parser.add_argument("--hellominds", action="store_true", help="Query HelloMinds API")
    parser.add_argument("--blockchain", action="store_true", help="Query blockchain events")
    parser.add_argument("--directory", action="store_true", help="Query public directories")
    parser.add_argument("--all", action="store_true", help="Query all sources")
    parser.add_argument("--limit", type=int, default=50, help="Max agents per source")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--cron", action="store_true", help="Cron-friendly minimal output")
    args = parser.parse_args()
    
    # Determine sources
    sources = []
    if args.all:
        sources = ["hellominds", "blockchain", "directory"]
    else:
        if args.hellominds: sources.append("hellominds")
        if args.blockchain: sources.append("blockchain")
        if args.directory: sources.append("directory")
    
    if not sources:
        print("❌ No sources specified. Use --hellominds, --blockchain, --directory, or --all")
        return 1
    
    # Run probe
    results = run_probe(sources, args.limit, args.json, args.cron)
    
    # Output results
    output = format_output(results, args.json, args.cron)
    print(output)
    
    # Return exit code for cron/automation
    return 0 if results["summary"]["errors"] == 0 else 1

if __name__ == "__main__":
    sys.exit(main())