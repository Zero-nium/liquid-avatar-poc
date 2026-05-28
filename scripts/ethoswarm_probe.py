#!/usr/bin/env python3
"""
Ethoswarm MVP Probe — Index 100 most recently active agents from Base chain.
Uses Covalent API (free tier) for reliable event indexing.
"""
import os
import sys
import requests
from datetime import datetime, timezone

# ─── CONFIG (Real addresses from Deon) ────────────────────────────────────────
BASE_CHAIN_ID = 8453  # Base mainnet
MENTE_CONTRACT = "0x4CD9a847f39106E19A4E41Aea8a232E915C82aF5"
CENTRAL_BANK = "0xd85096fAeC1aC03075667B4C1a1661F5623Bf111"
COVALENT_API_KEY = os.getenv("COVALENT_API_KEY", "")
API_KEY = os.getenv("LIQUID_AVATAR_API_KEY", "dev-key")
BASE_URL = os.getenv("LIQUID_AVATAR_BASE", "https://liquid-avatar-poc.onrender.com")

# ─── DISCOVERY ────────────────────────────────────────────────────────────────

def fetch_recent_transfers(limit: int = 150) -> list[dict]:
    """Fetch $MENTE Transfer events to centralBank via Covalent API."""
    
    # Mock mode for UI testing (no API key needed)
    if os.getenv("MOCK_MODE") == "true" or not COVALENT_API_KEY:
        print("🎭 Mock mode: generating 100 test agents", file=sys.stderr)
        return [
            {
                "from": f"0xabc123{i:03d}def456789012345678901234567890abcd",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tx_hash": f"0xmock{i:04d}",
                "value": 131.0
            }
            for i in range(min(limit, 100))
        ]
    
    # Real Covalent API call
    url = f"https://api.covalenthq.com/v1/{BASE_CHAIN_ID}/address/{CENTRAL_BANK}/transactions_v2/"
    
    params = {
        "key": COVALENT_API_KEY,
        "quote-currency": "USD",
        "page-size": 100,
        "page-number": 0,
        "block-signed-at-asc": False,
        "no-logs": False
    }
    
    try:
        res = requests.get(url, params=params, timeout=30)
        res.raise_for_status()
        data = res.json()
        
        transfers = []
        for tx in data.get("data", {}).get("items", []):
            for log in tx.get("log_events", []):
                # Filter for MENTE Transfer to centralBank
                if (log.get("address", "").lower() == MENTE_CONTRACT.lower() and
                    log.get("topics", [None])[0] == "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef" and
                    log.get("topics", [None, None, None])[2].lower() == f"0x000000000000000000000000{CENTRAL_BANK[2:].lower()}"):
                    
                    from_addr = "0x" + log["topics"][1][-40:]
                    value_hex = log.get("data", "0x0")
                    value = int(value_hex, 16) / 1e18 if value_hex != "0x0" else 0
                    
                    # Only include exact 131 MENTE transfers (cognition credits)
                    if abs(value - 131) < 0.01:
                        transfers.append({
                            "from": from_addr.lower(),
                            "timestamp": tx.get("block_signed_at"),
                            "tx_hash": tx.get("tx_hash"),
                            "value": value
                        })
        
        print(f"🔍 Covalent: found {len(transfers)} matching transfers", file=sys.stderr)
        return transfers
        
    except requests.exceptions.RequestException as e:
        print(f"⚠️  Covalent API error: {e}", file=sys.stderr)
        # Fallback to mock on error
        return [{"from": f"0xmock{i:040}", "timestamp": datetime.now(timezone.utc).isoformat(), "tx_hash": f"0xerror{i}", "value": 131} for i in range(10)]

def ingest_ethoswarm_agents(transfers: list[dict]):
    """Deduplicate, sort by recency, take top 100, ingest via API."""
    seen = {}
    for t in transfers:
        wallet = t["from"].lower()
        if wallet not in seen or t["timestamp"] > seen[wallet]["timestamp"]:
            seen[wallet] = t
    
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
            print(f"✅ {agent_id[:25]}... {'new' if res.status_code==200 else 'exists'}")
        else:
            print(f"❌ {agent_id[:25]}... {res.status_code} - {res.text[:100]}")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🔍 Fetching Ethoswarm transfers...")
    transfers = fetch_recent_transfers()
    print(f"📥 Found {len(transfers)} events, ingesting top {min(100, len(transfers))}...")
    ingest_ethoswarm_agents(transfers)
    print("✨ MVP probe complete.")