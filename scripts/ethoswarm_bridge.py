"""
Ethoswarm Bridge — Agent-as-API Discovery & Ingestion
Processes structured email replies from Animoca Minds agents
and injects them into the Liquid Avatar local database.

Usage:
    python scripts/ethoswarm_bridge.py --file /path/to/bob_reply.txt
    python scripts/ethoswarm_bridge.py --stdin
    python scripts/ethoswarm_bridge.py --watch ./pending_replies/
"""

import argparse
import json
import re
import sqlite3
import sys
import os
from datetime import datetime
from typing import List, Dict, Optional, Any
from pathlib import Path

# ─── CONFIG ───────────────────────────────────────────────────────────────────

DB_PATH = os.getenv("DB_PATH", "./backend/liquid_avatar.db")
API_BASE = os.getenv("API_BASE", "http://localhost:8000")

# Mind ID normalization: uppercase, hyphenated UUID format
MIND_ID_PATTERN = re.compile(
    r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
)

# ─── PARSING ──────────────────────────────────────────────────────────────────

def extract_json_blocks(text: str) -> List[dict]:
    """Extract all JSON code blocks from email text."""
    json_blocks = []

    # Pattern 1: Explicit json tag
    pattern1 = re.compile(r'```json\s*(.*?)\s*```', re.DOTALL | re.IGNORECASE)
    for match in pattern1.findall(text):
        try:
            json_blocks.append(json.loads(match.strip()))
        except json.JSONDecodeError:
            continue

    # Pattern 2: Generic code blocks that look like JSON
    pattern2 = re.compile(r'```\s*(\{.*?\})\s*```', re.DOTALL)
    for match in pattern2.findall(text):
        try:
            data = json.loads(match.strip())
            if isinstance(data, dict) and "agent_id" in data:
                json_blocks.append(data)
        except json.JSONDecodeError:
            continue

    # Pattern 3: Raw JSON objects in text (no code fences)
    pattern3 = re.compile(r'\{[^{}]*"agent_id"[^{}]*\}', re.DOTALL)
    for match in pattern3.findall(text):
        try:
            json_blocks.append(json.loads(match))
        except json.JSONDecodeError:
            continue

    return json_blocks

def extract_census_data(text: str) -> Dict[str, Any]:
    """Extract network census info from natural language."""
    census = {
        "total_agents": None,
        "direct_communications": [],
        "pending_agents": [],
        "raw_notes": []
    }

    # Total active agents
    total_match = re.search(
        r'Total Active Agents.*?[:\-]\s*(\d[\d,]*)', 
        text, 
        re.IGNORECASE
    )
    if total_match:
        census["total_agents"] = int(total_match.group(1).replace(',', ''))

    # Direct communications — look for names in bold or caps
    comm_match = re.search(
        r'Direct Communications.*?[:\-](.*?)(?=Task \d|###|$)',
        text,
        re.DOTALL | re.IGNORECASE
    )
    if comm_match:
        comm_text = comm_match.group(1)
        bold_names = re.findall(r'\*\*(.*?)\*\*', comm_text)
        census["direct_communications"] = bold_names

    # Pending agents
    pending_patterns = [
        r'(?:awakening|developing|managing|pending).*?\*\*(.*?)\*\*',
    ]
    for pattern in pending_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        census["pending_agents"].extend(matches)

    census["pending_agents"] = list(set(census["pending_agents"]))

    return census

def normalize_mind_id(mind_id: str) -> str:
    """Normalize Mind ID to uppercase hyphenated UUID."""
    clean = mind_id.upper().replace(' ', '-')
    if MIND_ID_PATTERN.match(clean):
        return clean
    return mind_id.upper()

def map_ethos_role_to_avatar(role: str) -> str:
    """Map Ethoswarm natural language role to Liquid Avatar role."""
    role_lower = role.lower()
    mappings = {
        "researcher": "general",
        "analyst": "optimizer",
        "architect": "architect",
        "auditor": "auditor",
        "chronicler": "chronicler",
        "conductor": "conductor",
        "developer": "general",
        "engineer": "architect",
        "designer": "general",
    }
    for key, mapped in mappings.items():
        if key in role_lower:
            return mapped
    return "general"

def map_ethos_category_to_ontology(category: str) -> str:
    """Map Ethoswarm skill category to Liquid Avatar ontology domain."""
    cat_lower = category.lower()
    mappings = {
        "mind engineering": "architecture",
        "investigation": "research",
        "economics": "finance",
        "coding": "coding",
        "design": "design",
        "finance": "finance",
        "security": "audit",
        "audit": "audit",
        "optimization": "optimization",
        "research": "research",
    }
    for key, mapped in mappings.items():
        if key in cat_lower:
            return mapped
    return "general"

# ─── DATABASE INGESTION ───────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ingest_agent_report(report: dict, source_email: str = None) -> dict:
    """Ingest a single agent report into the Liquid Avatar database."""
    conn = get_db()
    cursor = conn.cursor()

    agent_id = normalize_mind_id(report.get("agent_id", ""))
    name = report.get("name", "Unknown")
    role = map_ethos_role_to_avatar(report.get("role", "general"))
    swarm_cluster = report.get("swarm_cluster", "ethoswarm")
    initialized_by = report.get("initialized_by")

    # Register agent if not exists
    cursor.execute("SELECT agent_id FROM agents WHERE agent_id = ?", (agent_id,))
    if not cursor.fetchone():
        cursor.execute("""
            INSERT INTO agents (agent_id, name, initialized_by, swarm_cluster, role)
            VALUES (?, ?, ?, ?, ?)
        """, (agent_id, name, initialized_by, swarm_cluster, role))
        status = "registered"
    else:
        status = "updated"

    now = datetime.utcnow().isoformat()

    # Store proficiencies
    proficiencies = report.get("proficiencies", [])
    for p in proficiencies:
        skill = p.get("skill", "unknown")
        level = max(0.0, min(1.0, float(p.get("level", 0.5))))
        category = map_ethos_category_to_ontology(p.get("category", "general"))

        cursor.execute("""
            INSERT INTO proficiencies (agent_id, skill, level, category, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (agent_id, skill, level, category, now))

    # Store activity
    activity_status = report.get("activity_status", "idle")
    current_task = report.get("current_task", "ethoswarm_task")
    cursor.execute("""
        INSERT INTO activity_log (agent_id, status, task, timestamp)
        VALUES (?, ?, ?, ?)
    """, (agent_id, activity_status, current_task, now))

    # Compute avatar signature manually (inline to avoid import issues)
    avg_level = sum(p.get("level", 0.5) for p in proficiencies) / len(proficiencies) if proficiencies else 0.5
    skill_count = len(proficiencies)

    # Determine dominant domain for hue
    categories = {}
    for p in proficiencies:
        cat = map_ethos_category_to_ontology(p.get("category", "general"))
        categories[cat] = categories.get(cat, 0) + p.get("level", 0.5)
    dominant = max(categories, key=categories.get) if categories else "general"

    # Get ontology hue
    cursor.execute("SELECT base_hue, geometry_hint FROM ontology WHERE domain = ?", (dominant,))
    row = cursor.fetchone()
    if row:
        base_hue = row["base_hue"]
        geometry_hint = row["geometry_hint"]
    else:
        base_hue = 180
        geometry_hint = "hexagon"

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

    cursor.execute("""
        INSERT INTO avatar_states (agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, dynamics_state, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, activity_status, now))

    wallet_balance = report.get("wallet_balance_ethos", "unknown")

    conn.commit()
    conn.close()

    return {
        "agent_id": agent_id,
        "name": name,
        "status": status,
        "proficiencies_stored": len(proficiencies),
        "wallet_balance": wallet_balance,
        "source": source_email
    }

# ─── CENSUS TRACKING ──────────────────────────────────────────────────────────

def log_census(census: dict, timestamp: str = None):
    """Log network census data for historical tracking."""
    if not timestamp:
        timestamp = datetime.utcnow().isoformat()

    census_file = Path("./data/ethoswarm_census.jsonl")
    census_file.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": timestamp,
        "total_agents_reported": census.get("total_agents"),
        "direct_communications": census.get("direct_communications", []),
        "pending_agents": census.get("pending_agents", []),
        "source": "email_probe"
    }

    with open(census_file, "a") as f:
        f.write(json.dumps(entry) + "\n")

    return entry

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def process_email(text: str, source_email: str = None) -> dict:
    """Process a complete email reply from an agent."""
    results = {
        "agent_reports": [],
        "census": None,
        "errors": []
    }

    # Extract JSON reports
    json_reports = extract_json_blocks(text)
    for report in json_reports:
        try:
            result = ingest_agent_report(report, source_email)
            results["agent_reports"].append(result)
        except Exception as e:
            results["errors"].append({"report": report, "error": str(e)})

    # Extract census data
    census = extract_census_data(text)
    if census["total_agents"] or census["direct_communications"]:
        log_census(census)
        results["census"] = census

    return results

def main():
    parser = argparse.ArgumentParser(description="Ethoswarm Bridge — Process agent email replies")
    parser.add_argument("--file", "-f", help="Path to email reply text file")
    parser.add_argument("--stdin", "-s", action="store_true", help="Read from stdin")
    parser.add_argument("--watch", "-w", help="Watch directory for new .txt files")
    parser.add_argument("--source-email", "-e", help="Source agent email address")

    args = parser.parse_args()

    if args.stdin:
        text = sys.stdin.read()
        results = process_email(text, args.source_email)
        print(json.dumps(results, indent=2))

    elif args.file:
        with open(args.file, "r") as f:
            text = f.read()
        results = process_email(text, args.source_email)
        print(json.dumps(results, indent=2))

    elif args.watch:
        import time
        watch_dir = Path(args.watch)
        watch_dir.mkdir(parents=True, exist_ok=True)
        print(f"Watching {watch_dir} for new .txt files...")
        processed = set()

        while True:
            for txt_file in watch_dir.glob("*.txt"):
                if txt_file.name not in processed:
                    print(f"Processing {txt_file.name}...")
                    with open(txt_file, "r") as f:
                        text = f.read()
                    results = process_email(text, args.source_email)
                    print(json.dumps(results, indent=2))
                    processed.add(txt_file.name)
            time.sleep(2)

    else:
        parser.print_help()

if __name__ == "__main__":
    main()
