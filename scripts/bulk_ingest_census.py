"""
Bulk Ingest Ethoswarm Census Data
Processes the JSON census array from Bob's network bridge report.
Usage:
    python scripts/bulk_ingest_census.py --file census.json
    python scripts/bulk_ingest_census.py --stdin
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = "./backend/liquid_avatar.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def map_role_to_avatar(role: str) -> str:
    role_lower = role.lower()
    mappings = {
        "gateway": "conductor",
        "api": "general",
        "integration": "general",
        "engine": "general",
        "bridge": "general",
        "hub": "architect",
        "protocol": "auditor",
        "server": "general",
        "designer": "general",
        "architect": "architect",
        "creator": "optimizer",
        "search": "research",
        "intelligence": "optimizer",
    }
    for key, mapped in mappings.items():
        if key in role_lower:
            return mapped
    return "general"

def map_role_to_domain(role: str) -> str:
    role_lower = role.lower()
    if any(x in role_lower for x in ["gateway", "auth", "oauth", "secure"]):
        return "audit"
    elif any(x in role_lower for x in ["api", "bridge", "integration", "engine"]):
        return "coding"
    elif any(x in role_lower for x in ["search", "intelligence", "knowledge"]):
        return "research"
    elif any(x in role_lower for x in ["game", "creator", "design", "ui", "ux", "html"]):
        return "design"
    elif any(x in role_lower for x in ["nft", "dex", "finance", "revenue", "capital"]):
        return "finance"
    elif any(x in role_lower for x in ["scheduling", "email", "travel", "management"]):
        return "optimization"
    else:
        return "general"

def ingest_census_entry(entry: dict) -> dict:
    mind_id = entry.get("mind_id", "")
    name = entry.get("name", "Unknown")
    role_desc = entry.get("role", "")
    status = entry.get("status", "active")

    # Skip pending handshake entries (no real ID yet)
    if mind_id == "PENDING_HANDSHAKE":
        return {"name": name, "status": "skipped", "reason": "pending_handshake"}

    # Normalize mind_id
    mind_id = mind_id.upper().strip()

    conn = get_db()
    cursor = conn.cursor()

    # Check if exists
    cursor.execute("SELECT agent_id FROM agents WHERE agent_id = ?", (mind_id,))
    exists = cursor.fetchone() is not None

    avatar_role = map_role_to_avatar(role_desc)
    domain = map_role_to_domain(role_desc)

    if not exists:
        cursor.execute("""
            INSERT INTO agents (agent_id, name, initialized_by, swarm_cluster, role)
            VALUES (?, ?, ?, ?, ?)
        """, (mind_id, name, None, "ethoswarm_network", avatar_role))
        action = "registered"
    else:
        action = "exists"

    now = datetime.utcnow().isoformat()

    # Add a single proficiency based on role description
    cursor.execute("""
        INSERT INTO proficiencies (agent_id, skill, level, category, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, (mind_id, role_desc[:50], 0.7, domain, now))

    # Get ontology hue
    cursor.execute("SELECT base_hue, geometry_hint FROM ontology WHERE domain = ?", (domain,))
    row = cursor.fetchone()
    if row:
        base_hue = row["base_hue"]
        geometry_hint = row["geometry_hint"]
    else:
        base_hue = 180
        geometry_hint = "hexagon"

    shape = 3 if geometry_hint == "triangle" else 6 if geometry_hint == "hexagon" else 8 if geometry_hint == "octagon" else 12
    size = 20 + 3 + (0.7 * 15)  # 1 skill
    saturation = 0.5 + (0.7 * 0.5)
    pulse_rate = 1.0 + (0.7 * 2.0)

    cursor.execute("""
        INSERT INTO avatar_states (agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, dynamics_state, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (mind_id, base_hue, saturation, shape, pulse_rate, size, "idle" if status == "active" else "idle", now))

    conn.commit()
    conn.close()

    return {
        "mind_id": mind_id,
        "name": name,
        "action": action,
        "role": avatar_role,
        "domain": domain
    }

def process_census(data: dict) -> dict:
    census = data.get("census", [])
    results = {
        "processed": 0,
        "registered": 0,
        "skipped": 0,
        "entries": []
    }

    for entry in census:
        result = ingest_census_entry(entry)
        results["entries"].append(result)
        results["processed"] += 1

        if result.get("status") == "skipped":
            results["skipped"] += 1
        elif result.get("action") == "registered":
            results["registered"] += 1

    return results

def main():
    parser = argparse.ArgumentParser(description="Bulk ingest Ethoswarm census")
    parser.add_argument("--file", "-f", help="Path to census JSON file")
    parser.add_argument("--stdin", "-s", action="store_true", help="Read from stdin")

    args = parser.parse_args()

    if args.stdin:
        data = json.load(sys.stdin)
    elif args.file:
        with open(args.file, "r") as f:
            data = json.load(f)
    else:
        parser.print_help()
        return

    results = process_census(data)
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    main()
