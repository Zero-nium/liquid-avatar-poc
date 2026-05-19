#!/usr/bin/env python3
"""
Ethoswarm MVP Probe — Index 100 most recently active agents from Base chain.
Uses public RPC + basic log scraping. No ABI decoding required.
"""
import os
import requests
from datetime import datetime, timezone, timedelta

BASE_RPC = os.getenv("BASE_RPC", "https://mainnet.base.org")
MENTE_CONTRACT = "0x4CD9..."      # ← Replace with actual
CENTRAL_BANK = "0xd850..."        # ← Replace with actual
API_KEY = os.getenv("LIQUID_AVATAR_API_KEY")
BASE_URL = os.getenv("LIQUID_AVATAR_BASE", "https://liquid-avatar-poc.onrender.com")

def fetch_recent_transfers(limit: int = 150) -> list[dict]:
    """Fetch Transfer events to centralBank, deduplicate by sender, sort by recency."""
    # Use Alchemy/QuickNode free tier or public RPC with eth_getLogs
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getLogs",
        "params": [{
            "address": MENTE_CONTRACT,
            "topics": [
                "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                None,
                f"0x000000000000000000000000{CENTRAL_BANK[2:].lower()}"
            ],
            "fromBlock": "latest",
            "toBlock": "latest"
        }],
        "id": 1
    }
    
    # In production, replace with proper block range + pagination
    # For MVP, we'll simulate with a lightweight indexer or use a free API like Covalent/BaseScan
    # Placeholder: Return mock structure matching expected format
    return []  # ← Replace with actual RPC call or Covalent API

def ingest_ethoswarm_agents(transfers: list[dict]):
    """Deduplicate, sort by recency, take top 100, ingest via API."""
    seen = {}
    for t in transfers:
        wallet = t["from"].lower()
        if wallet not in seen or t["timestamp"] > seen[wallet]["timestamp"]:
            seen[wallet] = t
    
    # Sort by most recent activity
    sorted_agents = sorted(seen.values(), key=lambda x: x["timestamp"], reverse=True)[:100]
    
    for agent in sorted_agents:
        wallet = agent["from"].lower()
        agent_id = f"ethoswarm-{wallet}"
        
        payload = {
            "agent_id": agent_id,
            "name": f"Agent-{wallet[:6]}",
            "role": "general",
            "swarm_cluster": "discovered_via_ethoswarm",
            "proficiencies": [],
            "activity_status": "active",
            "current_task": "Ethoswarm cognition active",
            "metadata": {
                "on_chain_id": wallet,
                "last_transfer": agent["timestamp"],
                "cognition_credits": 1,
                "source": "blockchain_mvp"
            }
        }
        
        res = requests.post(
            f"{BASE_URL}/agents/discover",
            headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        
        if res.status_code in (200, 409):
            print(f"✅ {agent_id[:20]}... {'new' if res.status_code==200 else 'exists'}")
        else:
            print(f"❌ {agent_id[:20]}... {res.status_code}")

if __name__ == "__main__":
    print("🔍 Fetching Ethoswarm transfers...")
    transfers = fetch_recent_transfers()
    print(f"📥 Found {len(transfers)} events, ingesting top 100...")
    ingest_ethoswarm_agents(transfers)
    print("✨ MVP probe complete.")