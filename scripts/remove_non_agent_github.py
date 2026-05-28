#!/usr/bin/env python3
"""Remove GitHub accounts that are likely repos, not actual user agents."""
import requests
import sys

API_BASE = "https://liquid-avatar-poc.onrender.com"
API_KEY = "75ed35c005912c1149a170fe948168fc"

# Patterns that suggest a repo, not a user account
REPO_PATTERNS = [
    "-org", "organization", "org-", "-private", "-limited", 
    "sketch", "test", "demo", "sample", "example"
]

print("🔍 Fetching GitHub agents...")
res = requests.get(f"{API_BASE}/agents", headers={"X-API-Key": API_KEY})
if res.status_code != 200:
    print(f"❌ Failed: {res.text}")
    sys.exit(1)

agents = res.json().get("agents", [])
github_agents = [a for a in agents if a["agent_id"].startswith("github-")]

print(f"Found {len(github_agents)} GitHub accounts\n")

# Check each GitHub account
to_remove = []
for agent in github_agents:
    name = agent["name"].lower()
    is_likely_repo = any(p in name for p in REPO_PATTERNS)
    
    # Check if it has minimal activity (likely not a real agent)
    has_minimal_activity = (
        agent.get("avatar", {}).get("shape_complexity", 0) == 6 and
        agent.get("avatar", {}).get("base_hue", 0) == 180 and
        agent.get("avatar", {}).get("saturation", 0) == 0.75
    )
    
    if is_likely_repo or has_minimal_activity:
        to_remove.append(agent)
        print(f"  🗑️  Candidate: {agent['name']} ({agent['agent_id'][:15]}...)")
        print(f"      Reason: {'Repo pattern' if is_likely_repo else 'Minimal activity'}")

if not to_remove:
    print("\n✅ No suspicious GitHub accounts found.")
    sys.exit(0)

print(f"\n⚠️  Found {len(to_remove)} candidates for removal.")
confirm = input("Remove these accounts? (yes/no): ").strip().lower()

if confirm != "yes":
    print("❌ Aborted.")
    sys.exit(0)

deleted = 0
for agent in to_remove:
    del_res = requests.delete(f"{API_BASE}/agents/{agent['agent_id']}", headers={"X-API-Key": API_KEY})
    if del_res.status_code in (200, 204):
        print(f"  ✅ Deleted: {agent['name']}")
        deleted += 1
    else:
        print(f"  ⚠️  Failed: {agent['name']} - {del_res.status_code}")

print(f"\n✨ Removed {deleted} non-agent GitHub accounts.")
