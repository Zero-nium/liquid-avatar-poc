#!/usr/bin/env python3
"""
Re-enrich Council agents with Schema v1.2 shapes and hues.
Safe Mode: Sends empty proficiencies to trigger Role-Based fallback logic.
"""
import requests

API_BASE = "https://liquid-avatar-poc.onrender.com"
API_KEY = "75ed35c005912c1149a170fe948168fc"

council_agents = [
    {"id": "A2D2F4D4-532A-F111-AD1D-0EA9A5017E89", "role": "conductor", "name": "Aura Quorum", "cluster": "council"},
    {"id": "D0779D7D-572A-F111-AD1D-0EA9A5017E89", "role": "architect", "name": "Astra", "cluster": "council"},
    {"id": "BC0E9710-572A-F111-AD1D-0EA9A5017E89", "role": "optimizer", "name": "Synthetix", "cluster": "council"},
    {"id": "E5175BF5-552A-F111-AD1D-0EA9A5017E89", "role": "auditor", "name": "Chronos-Audit", "cluster": "council"},
    {"id": "6865F193-202C-F111-AD1D-0EA9A5017E89", "role": "chronicler", "name": "Alethea Historian", "cluster": "council"},
    {"id": "0ACC1E2B-122C-F111-AD1D-0EA9A5017E89", "role": "architect", "name": "Vantage Architect", "cluster": "council"},
    {"id": "50DC7A82-CF2C-F111-AD1D-0EA9A5017E89", "role": "auditor", "name": "Trace Auditor", "cluster": "auditors"},
    {"id": "D279493E-F36B-1410-8462-00039CE7DF11", "role": "architect", "name": "Lumi", "cluster": "design_network"},
]

print("🔄 Re-enriching Council agents with Schema v1.2 (Safe Mode)...\n")

for agent in council_agents:
    # Send EMPTY proficiencies to trigger Role-Based fallback in backend
    res = requests.post(
        f"{API_BASE}/agents/discover",
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
        json={
            "agent_id": agent["id"],
            "name": agent["name"],
            "role": agent["role"],
            "swarm_cluster": agent["cluster"],
            "proficiencies": [],  # Triggers Role-Based Hue/Shape logic
            "activity_status": "idle",
            "current_task": "Schema v1.2 re-enrichment"
        }
    )
    
    if res.status_code == 200:
        print(f"✅ {agent['name']} ({agent['role']}) - Re-enriched")
    else:
        print(f"❌ {agent['name']} - Failed: {res.status_code} - {res.text}")

print("\n✨ Council re-enrichment complete!")
print("⚠️  Hard refresh browser (Cmd+Shift+R) to see changes.")