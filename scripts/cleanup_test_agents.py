#!/usr/bin/env python3
"""Remove test/debug agents from live swarm."""
import requests
import sys

API_BASE = "https://liquid-avatar-poc.onrender.com"
API_KEY = "75ed35c005912c1149a170fe948168fc"

# Patterns that identify test/debug accounts
TEST_PATTERNS = [
    "test", "wave1-agent", "dev_", "fin_", "audit_", 
    "poc", "rate", "debug", "mock", "temp"
]

print("🧹 Fetching agent list...")
res = requests.get(f"{API_BASE}/agents", headers={"X-API-Key": API_KEY})
if res.status_code != 200:
    print(f"❌ Failed to fetch agents: {res.text}")
    sys.exit(1)

agents = res.json().get("agents", [])
deleted = 0

for agent in agents:
    aid = agent["agent_id"].lower()
    name = agent["name"].lower()
    is_test = any(p in aid or p in name for p in TEST_PATTERNS)
    
    if is_test:
        del_res = requests.delete(f"{API_BASE}/agents/{agent['agent_id']}", headers={"X-API-Key": API_KEY})
        if del_res.status_code in (200, 204):
            print(f"  ✅ Deleted: {agent['name']} ({agent['agent_id'][:8]}...)")
            deleted += 1
        else:
            print(f"  ⚠️  Failed to delete {agent['name']}: {del_res.status_code}")

print(f"\n✨ Cleanup complete. Removed {deleted} test agents.")
