#!/usr/bin/env python3
"""
Database Health Check — Liquid Avatar
Validates schema integrity, detects orphaned records, and monitors agent activity.

Usage:
    python scripts/db_health.py                    # Run checks
    python scripts/db_health.py --fix-nulls        # Auto-fix null avatar states
    python scripts/db_health.py --json             # Output JSON for monitoring
    python scripts/db_health.py --cron             # Cron-friendly output

Run via cron (every 6 hours):
    0 */6 * * * cd ~/liquid-avatar-poc && python scripts/db_health.py --cron >> logs/db_health.log 2>&1
"""
import argparse
import json
import sqlite3
import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ─── CONFIG ───────────────────────────────────────────────────────────────────
DEFAULT_DB_PATH = os.getenv("DB_PATH", "./backend/liquid_avatar.db")
LOG_DIR = Path("./logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ─── HEALTH CHECKS ────────────────────────────────────────────────────────────

def check_tables_exist(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Verify all required tables exist."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    required = {"agents", "proficiencies", "avatar_states", "activity_log", "ontology"}
    missing = required - tables
    if missing:
        return False, f"Missing tables: {missing}"
    return True, "All required tables present"

def check_orphaned_avatars(conn: sqlite3.Connection) -> tuple[bool, str, list]:
    """Find avatar_states without corresponding agents."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT av.agent_id, av.computed_at 
        FROM avatar_states av
        WHERE NOT EXISTS (
            SELECT 1 FROM agents a WHERE a.agent_id = av.agent_id
        )
    """)
    orphans = cursor.fetchall()
    if orphans:
        details = [f"{row['agent_id']} (computed: {row['computed_at']})" for row in orphans]
        return False, f"{len(orphans)} orphaned avatar_states found", details
    return True, "No orphaned avatar states", []

def check_null_avatar_states(conn: sqlite3.Connection) -> tuple[bool, str, list]:
    """Find agents with null/missing avatar computation (Aura Quorum's observation)."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT a.agent_id, a.name, a.role, a.last_reported, a.created_at
        FROM agents a
        LEFT JOIN avatar_states av ON a.agent_id = av.agent_id
        WHERE av.agent_id IS NULL OR av.computed_at IS NULL
        ORDER BY a.last_reported DESC
        LIMIT 20
    """)
    nulls = cursor.fetchall()
    if nulls:
        details = [
            f"{row['name']} ({row['agent_id']}) — role: {row['role']}, "
            f"created: {row['created_at']}, last: {row['last_reported']}"
            for row in nulls
        ]
        return False, f"{len(nulls)} agents with null avatar state", details
    return True, "All agents have computed avatar states", []

def check_recent_activity(conn: sqlite3.Connection, hours: int = 24) -> tuple[bool, str, dict]:
    """Verify agents are reporting activity within expected window."""
    cursor = conn.cursor()
    
    # Count active agents
    cursor.execute("""
        SELECT COUNT(DISTINCT agent_id) FROM activity_log
        WHERE timestamp >= datetime('now', ?)
    """, (f"-{hours} hours",))
    active = cursor.fetchone()[0]
    
    # Count total agents
    cursor.execute("SELECT COUNT(*) FROM agents")
    total = cursor.fetchone()[0]
    
    pct = (active / total * 100) if total > 0 else 0
    
    stats = {
        "active_24h": active,
        "total_agents": total,
        "activity_rate_pct": round(pct, 1)
    }
    
    if pct < 10 and total > 5:
        return False, f"Low activity rate: {pct:.1f}% active in last {hours}h", stats
    return True, f"Activity healthy: {active}/{total} agents active ({pct:.1f}%)", stats

def check_foreign_keys(conn: sqlite3.Connection) -> tuple[bool, str]:
    """Verify foreign key constraints are enforced."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys")
    if cursor.fetchone()[0] != 1:
        return False, "Foreign keys not enforced (PRAGMA foreign_keys=OFF)"
    
    # Check for referential integrity violations
    cursor.execute("PRAGMA foreign_key_check")
    violations = cursor.fetchall()
    if violations:
        return False, f"Foreign key violations: {violations}"
    return True, "Foreign keys enforced and valid"

def check_ontology_domains(conn: sqlite3.Connection) -> tuple[bool, str, list]:
    """Verify ontology has all canonical domains."""
    cursor = conn.cursor()
    cursor.execute("SELECT domain FROM ontology ORDER BY domain")
    domains = {row[0] for row in cursor.fetchall()}
    
    canonical = {
        "architecture", "optimization", "audit", "chronicle",
        "coding", "finance", "design", "research", "general"
    }
    
    missing = canonical - domains
    extra = domains - canonical
    
    issues = []
    if missing:
        issues.append(f"Missing canonical domains: {missing}")
    if extra:
        issues.append(f"Extra domains (may be intentional): {extra}")
    
    if issues:
        return False, "; ".join(issues), {"missing": list(missing), "extra": list(extra)}
    return True, "Ontology domains valid", {}

# ─── AUTO-FIX FUNCTIONS ───────────────────────────────────────────────────────

def fix_null_avatar_states(conn: sqlite3.Connection) -> dict:
    """Auto-compute avatar states for agents missing them."""
    cursor = conn.cursor()
    fixed = []
    
    # Get agents without avatar states
    cursor.execute("""
        SELECT a.agent_id, a.name, a.role
        FROM agents a
        LEFT JOIN avatar_states av ON a.agent_id = av.agent_id
        WHERE av.agent_id IS NULL
    """)
    agents = cursor.fetchall()
    
    for agent in agents:
        agent_id = agent["agent_id"]
        
        # Get proficiencies for this agent
        cursor.execute("""
            SELECT skill, level, category FROM proficiencies WHERE agent_id = ?
        """, (agent_id,))
        profs = cursor.fetchall()
        
        # Compute avatar signature (simplified version of compute_avatar_signature)
        if not profs:
            dominant_domain = "general"
            avg_level = 0.5
            skill_count = 0
        else:
            categories = {}
            for p in profs:
                cat = p["category"].lower()
                # Map to canonical domain
                if any(x in cat for x in ["architecture", "design", "schema"]):
                    domain = "architecture"
                elif any(x in cat for x in ["optimization", "efficiency"]):
                    domain = "optimization"
                elif any(x in cat for x in ["audit", "security", "verification"]):
                    domain = "audit"
                elif any(x in cat for x in ["chronicle", "history", "log"]):
                    domain = "chronicle"
                elif any(x in cat for x in ["coding", "api", "integration"]):
                    domain = "coding"
                elif any(x in cat for x in ["finance", "economics", "token"]):
                    domain = "finance"
                elif any(x in cat for x in ["research", "analysis", "intelligence"]):
                    domain = "research"
                else:
                    domain = "general"
                categories[domain] = categories.get(domain, 0) + p["level"]
            
            dominant_domain = max(categories, key=categories.get) if categories else "general"
            avg_level = sum(p["level"] for p in profs) / len(profs)
            skill_count = len(profs)
        
        # Get ontology values
        cursor.execute("SELECT base_hue, geometry_hint FROM ontology WHERE domain = ?", (dominant_domain,))
        row = cursor.fetchone()
        base_hue = row["base_hue"] if row else 180
        geom = row["geometry_hint"] if row else "hexagon"
        
        # Compute shape
        shape_map = {"triangle": 3, "hexagon": 6, "octagon": 8, "circle": 12}
        shape_complexity = shape_map.get(geom, 6)
        
        # Compute other values
        size = 20 + (skill_count * 3) + (avg_level * 15)
        saturation = 0.5 + (avg_level * 0.5)
        pulse_rate = 1.0 + (avg_level * 2.0)
        now = datetime.now(timezone.utc).isoformat()
        
        # Insert avatar state
        cursor.execute("""
            INSERT INTO avatar_states 
            (agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, dynamics_state, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (agent_id, base_hue, saturation, shape_complexity, pulse_rate, size, "idle", now))
        
        fixed.append(agent_id)
    
    if fixed:
        conn.commit()
    
    return {
        "fixed_count": len(fixed),
        "fixed_agents": fixed
    }

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def run_checks(conn: sqlite3.Connection, db_path: str, fix_nulls: bool = False) -> dict:
    """Run all health checks and optionally auto-fix issues."""
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "db_path": db_path,
        "checks": {},
        "summary": {"passed": 0, "failed": 0, "warnings": 0},
        "auto_fixes": {}
    }
    
    # Run checks
    checks = [
        ("tables_exist", check_tables_exist),
        ("orphaned_avatars", check_orphaned_avatars),
        ("null_avatar_states", check_null_avatar_states),
        ("recent_activity", lambda c: check_recent_activity(c, 24)),
        ("foreign_keys", check_foreign_keys),
        ("ontology_domains", check_ontology_domains),
    ]
    
    for name, check_func in checks:
        try:
            passed, message, *extra = check_func(conn)
            result = {"passed": passed, "message": message}
            if extra:
                result["details"] = extra[0] if len(extra) == 1 else extra
            results["checks"][name] = result
            
            if passed:
                results["summary"]["passed"] += 1
            else:
                results["summary"]["failed"] += 1
                # Auto-fix null avatar states if requested
                if name == "null_avatar_states" and fix_nulls:
                    fix_result = fix_null_avatar_states(conn)
                    results["auto_fixes"]["null_avatar_states"] = fix_result
                    if fix_result["fixed_count"] > 0:
                        results["summary"]["warnings"] += 1
                        results["checks"][name]["auto_fixed"] = fix_result["fixed_count"]
                        
        except Exception as e:
            results["checks"][name] = {"passed": False, "message": f"Check error: {str(e)}"}
            results["summary"]["failed"] += 1
    
    return results

def format_output(results: dict, json_output: bool = False, cron_mode: bool = False) -> str:
    """Format results for console, JSON, or cron output."""
    if cron_mode:
        # Minimal output for cron/logging
        status = "OK" if results["summary"]["failed"] == 0 else "FAIL"
        msg = f"{status} | {results['summary']['passed']} passed, {results['summary']['failed']} failed"
        if results["auto_fixes"]:
            fixes = ", ".join(f"{k}:{v['fixed_count']}" for k, v in results["auto_fixes"].items())
            msg += f" | auto-fixed: {fixes}"
        return f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    
    if json_output:
        return json.dumps(results, indent=2, default=str)
    
    # Human-readable console output
    lines = [
        f"🔍 Database Health Check — {results['timestamp']}",
        f"📁 DB: {results['db_path']}",
        "",
    ]
    
    for name, result in results["checks"].items():
        icon = "✅" if result["passed"] else "❌"
        lines.append(f"{icon} {name}: {result['message']}")
        if "details" in result and result["details"]:
            if isinstance(result["details"], list):
                for detail in result["details"][:5]:  # Limit output
                    lines.append(f"   • {detail}")
            else:
                lines.append(f"   • {result['details']}")
    
    if results["auto_fixes"]:
        lines.append("")
        lines.append("🔧 Auto-fixes applied:")
        for fix_name, fix_data in results["auto_fixes"].items():
            lines.append(f"   • {fix_name}: fixed {fix_data['fixed_count']} agents")
    
    lines.append("")
    lines.append(f"📊 Summary: {results['summary']['passed']} passed, "
                f"{results['summary']['failed']} failed, "
                f"{results['summary']['warnings']} warnings")
    
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="Liquid Avatar Database Health Check")
    parser.add_argument("--fix-nulls", action="store_true", 
                       help="Auto-compute avatar states for agents missing them")
    parser.add_argument("--json", action="store_true", 
                       help="Output results as JSON")
    parser.add_argument("--cron", action="store_true", 
                       help="Cron-friendly minimal output")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_PATH,
                       help=f"Database path (default: {DEFAULT_DB_PATH})")
    args = parser.parse_args()
    
    # Use provided DB path or default
    db_path = args.db
    
    if not os.path.exists(db_path):
        error = {"error": f"Database file not found: {db_path}"}
        if args.json:
            print(json.dumps(error))
        elif args.cron:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR: {error['error']}")
        else:
            print(f"❌ {error['error']}")
        return 1
    
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        
        # Enable foreign keys
        conn.execute("PRAGMA foreign_keys = ON")
        
        # Run checks
        results = run_checks(conn, db_path, fix_nulls=args.fix_nulls)
        
        # Output results
        output = format_output(results, json_output=args.json, cron_mode=args.cron)
        print(output)
        
        # Return exit code for cron/automation
        exit_code = 0 if results["summary"]["failed"] == 0 else 1
        conn.close()
        return exit_code
        
    except Exception as e:
        error = {"error": f"Health check failed: {str(e)}"}
        if args.json:
            print(json.dumps(error))
        elif args.cron:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR: {error['error']}")
        else:
            print(f"❌ {error['error']}")
        return 2

if __name__ == "__main__":
    sys.exit(main())