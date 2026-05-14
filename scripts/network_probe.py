#!/usr/bin/env python3
"""
Network Probe — Discover Active Agents for Liquid Avatar
Scrapes public endpoints for agent metadata and ingests partial profiles via live API.

Usage:
    python scripts/network_probe.py --hellominds --limit 100
    python scripts/network_probe.py --blockchain base --limit 50
    python scripts/network_probe.py --all --json > logs/probe_$(date +%Y%m%d).json
    python scripts/network_probe.py --directory --dry-run --limit 5  # Test without registering

Run via cron (daily at 2 AM):
    0 2 * * * cd ~/liquid-avatar-poc && python scripts/network_probe.py --all --cron >> logs/probe.log 2>&1

Env vars required for API mode:
    export LIQUID_AVATAR_API_KEY="your-api-key"
    export LIQUID_AVATAR_BASE="https://liquid-avatar-poc.onrender.com"
"""
import argparse
import json
import requests
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

# ─── CONFIG ───────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "./backend/liquid_avatar.db")  # Keep for fallback
API_KEY = os.getenv("LIQUID_AVATAR_API_KEY", "dev-key")  # ← Your API key
BASE_URL = os.getenv("LIQUID_AVATAR_BASE", "https://liquid-avatar-poc.onrender.com")  # ← Live URL

# Rate limiting
REQUEST_DELAY = 1.0  # seconds between external API calls
MAX_RETRIES = 3

# ─── DIAGNOSTIC LOGGING ───────────────────────────────────────────────────────
def log_debug(msg: str):
    """Print debug message to stderr."""
    print(f"[PROBE-DEBUG] {msg}", file=sys.stderr)

# ─── DISCOVERY SOURCES ────────────────────────────────────────────────────────

def discover_minds_agents(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Query Animoca Minds/Hello Minds for active agent IDs.
    This is a stub — replace with actual Minds API endpoint when available.
    """
    agents = []
    
    # Example: Minds public agent directory (stub)
    # In production, use the actual Minds API endpoint
    url = "https://api.animoca-minds.ai/v1/agents/public"
    
    for attempt in range(MAX_RETRIES):
        try:
            res = requests.get(
                url,
                params={"limit": limit, "fields": "id,name,role,last_active,metadata"},
                timeout=30,
                headers={"User-Agent": "LiquidAvatar-Probe/1.0"}
            )
            res.raise_for_status()
            data = res.json()
            
            # Parse response (adjust to actual API structure)
            for item in data.get("agents", []):
                agents.append({
                    "id": item.get("id"),
                    "name": item.get("name") or f"Minds-{item.get('id', 'unknown')[:8]}",
                    "role": item.get("role"),
                    "last_seen": item.get("last_active"),
                    "source": "minds_api",
                    "metadata": {
                        "minds_verified": item.get("verified", False),
                        "steward_contact": item.get("steward_email"),  # Optional
                        "capabilities": item.get("capabilities", [])
                    }
                })
            
            log_debug(f"✅ Minds API: found {len(agents)} agents")
            return agents
            
        except requests.exceptions.RequestException as e:
            log_debug(f"⚠️  Minds API error (attempt {attempt+1}/{MAX_RETRIES}): {e}")
            time.sleep(REQUEST_DELAY * (attempt + 1))
    
    log_debug(f"❌ Minds API: failed after {MAX_RETRIES} attempts")
    return []

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

# ─── API-BASED INGESTION ──────────────────────────────────────────────────────

def ingest_partial_agent_api(agent: Dict[str, Any], source: str, api_key: str, base_url: str, dry_run: bool = False) -> Dict[str, Any]:
    """
    Register a minimally-inferred agent profile via the LIVE API.
    Returns ingestion result for logging.
    """
    agent_id = agent.get("id")
    name = agent.get("name") or f"Unknown-{agent_id[:8] if agent_id else '00000000'}"
    
    if not agent_id:
        return {"status": "skipped", "reason": "no_agent_id", "source": source}
    
    # Dry-run mode: skip actual API call
    if dry_run:
        log_debug(f"[DRY-RUN] Would register: {agent_id} ({name}) via {source}")
        return {"status": "dry-run", "agent_id": agent_id, "name": name, "source": source}
    
    try:
        # Call the live /agents/discover endpoint
        res = requests.post(
            f"{base_url}/agents/discover",
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
                "User-Agent": "LiquidAvatar-Probe/1.0"
            },
            json={
                "agent_id": agent_id,
                "name": name,
                "role": "general",  # Default role for discovered agents
                "swarm_cluster": f"discovered_via_{source}",
                "proficiencies": [],  # Empty until agent enriches
                "activity_status": "idle",
                "current_task": f"Discovered via {source}"
            },
            timeout=30
        )
        
        if res.status_code == 200:
            log_debug(f"✅ Registered {agent_id} via API")
            return {"status": "registered", "agent_id": agent_id, "name": name, "source": source}
        elif res.status_code == 409:  # Conflict = already exists
            log_debug(f"Agent {agent_id} already exists via API")
            return {"status": "exists", "agent_id": agent_id, "source": source}
        else:
            log_debug(f"❌ API error for {agent_id}: {res.status_code} - {res.text}")
            return {"status": "error", "agent_id": agent_id, "error": res.text, "source": source}
            
    except requests.exceptions.RequestException as e:
        log_debug(f"❌ Request error for {agent_id}: {e}")
        return {"status": "error", "agent_id": agent_id, "error": str(e), "source": source}

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def run_probe(sources: List[str], limit: int, json_output: bool, cron_mode: bool, dry_run: bool) -> Dict[str, Any]:
    """Run discovery probes and ingest results via live API."""
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sources_queried": sources,
        "limit": limit,
        "ingestion": {"hellominds": [], "blockchain": [], "directory": [], "minds": []},  # ← Fixed: added "minds"
        "summary": {"total_discovered": 0, "newly_registered": 0, "errors": 0}
    }
    
    log_debug(f"Starting probe with sources: {sources}, limit: {limit}, dry_run: {dry_run}")
    log_debug(f"API base: {BASE_URL}, API key set: {bool(API_KEY)}")
    
    # Animoca Minds API
    if "minds" in sources:
        print("🔍 Querying Animoca Minds API...", file=sys.stderr)
        agents = discover_minds_agents(limit)
        for agent in agents:
            result = ingest_partial_agent_api(agent, "minds_api", API_KEY, BASE_URL, dry_run)
            results["ingestion"]["minds"].append(result)
            if result["status"] == "registered":
                results["summary"]["newly_registered"] += 1
            elif result["status"] == "error":
                results["summary"]["errors"] += 1
        results["summary"]["total_discovered"] += len(agents)
        time.sleep(REQUEST_DELAY)

    # HelloMinds API
    if "hellominds" in sources:
        print("🔍 Querying HelloMinds API...", file=sys.stderr)
        agents = discover_hellominds_agents(limit)
        for agent in agents:
            result = ingest_partial_agent_api(agent, "hellominds_api", API_KEY, BASE_URL, dry_run)
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
                result = ingest_partial_agent_api(agent, f"blockchain_{chain}", API_KEY, BASE_URL, dry_run)
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
            result = ingest_partial_agent_api(agent, "public_directory", API_KEY, BASE_URL, dry_run)
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
    parser.add_argument("--minds", action="store_true", help="Query Animoca Minds API")
    parser.add_argument("--hellominds", action="store_true", help="Query HelloMinds API")
    parser.add_argument("--blockchain", action="store_true", help="Query blockchain events")
    parser.add_argument("--directory", action="store_true", help="Query public directories")
    parser.add_argument("--all", action="store_true", help="Query all sources")
    parser.add_argument("--limit", type=int, default=50, help="Max agents per source")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--cron", action="store_true", help="Cron-friendly minimal output")
    parser.add_argument("--dry-run", action="store_true", help="Test discovery without registering agents")  # ← Added
    args = parser.parse_args()
    
    # Determine sources
    sources = []
    if args.all:
        sources = ["hellominds", "blockchain", "directory", "minds"]
    else:
        if args.hellominds: sources.append("hellominds")
        if args.blockchain: sources.append("blockchain")
        if args.directory: sources.append("directory")
        if args.minds: sources.append("minds")
    
    if not sources:
        print("❌ No sources specified. Use --hellominds, --blockchain, --directory, --minds, or --all")
        return 1
    
    # Run probe
    results = run_probe(sources, args.limit, args.json, args.cron, args.dry_run)  # ← Pass dry_run
    
    # Output results
    output = format_output(results, args.json, args.cron)
    print(output)
    
    # Return exit code for cron/automation
    return 0 if results["summary"]["errors"] == 0 else 1

if __name__ == "__main__":
    sys.exit(main())