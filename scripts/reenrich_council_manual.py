#!/usr/bin/env python3
"""Force re-enrich specific Council members with correct Schema v1.2 values."""
import requests

API_BASE = "https://liquid-avatar-poc.onrender.com"
API_KEY = "75ed35c005912c1149a170fe948168fc"

# Council members with their CORRECT roles and expected values
council_agents = [
    {
        "id": "A2D2F4D4-532A-F111-AD1D-0EA9A5017E89",
        "name": "Aura Quorum",
        "role": "conductor",
        "expected_shape": 10,  # Decagon
        "expected_hue": 180    # Teal (correct for conductor)
    },
    {
        "id": "6354CCB3-AC2A-F111-AD1D-0EA9A5017E89",
        "name": "Echo-Alpha",
        "role": "auditor",
        "expected_shape": 8,   # Octagon
        "expected_hue": 210    # Blue (correct for auditor)
    }
]

print("🔄 Force re-enriching Council members with Schema v1.2...\n")

for agent in council_agents:
    # Trigger avatar recomputation with empty proficiencies (forces role-based fallback)
    res = requests.post(
        f"{API_BASE}/agents/discover",
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
        json={
            "agent_id": agent["id"],
            "name": agent["name"],
            "role": agent["role"],
            "swarm_cluster": "council",
            "proficiencies": [],  # Empty to trigger role-based hue/shape
            "activity_status": "idle",
            "current_task": "Schema v1.2 cache reset"
        }
    )
    
    if res.status_code == 200:
        print(f"✅ {agent['name']} ({agent['role']})")
        print(f"   Expected: Shape={agent['expected_shape']}-gon, Hue={agent['expected_hue']}°")
        print(f"   Status: {res.status_code}\n")
    else:
        print(f"❌ {agent['name']} - Failed: {res.status_code} - {res.text}\n")

print("✨ Re-enrichment complete. Hard refresh browser to see changes.")
