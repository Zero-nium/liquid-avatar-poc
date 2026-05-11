"""
Liquid Avatar — API Integration Tests
Run with: python -m pytest tests/test_api.py -v
"""
import pytest
import requests
import json
import os

BASE_URL = os.getenv("API_BASE", "http://localhost:8000")
API_KEY = os.getenv("LIQUID_AVATAR_API_KEY", "dev-key")

def test_health():
    """Verify backend is responsive."""
    res = requests.get(f"{BASE_URL}/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "schema_version" in data

def test_swarm_map_structure():
    """Verify /swarm/map returns valid D3-compatible data."""
    res = requests.get(f"{BASE_URL}/swarm/map")
    assert res.status_code == 200
    data = res.json()
    
    assert "nodes" in data and isinstance(data["nodes"], list)
    assert "edges" in data and isinstance(data["edges"], list)
    assert "node_count" in data and data["node_count"] == len(data["nodes"])
    
    # Validate node structure
    if data["nodes"]:
        node = data["nodes"][0]
        assert "id" in node and "name" in node
        assert "avatar" in node
        assert all(k in node["avatar"] for k in ["base_hue", "saturation", "shape_complexity", "dynamics_state"])

def test_agent_registration_flow():
    """Test full registration → verification → activity cycle."""
    test_id = "TEST-API-001"
    
    # 1. Register
    res = requests.post(
        f"{BASE_URL}/agents/discover",
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
        json={
            "agent_id": test_id,
            "name": "TestAgent",
            "role": "architect",
            "proficiencies": [{"skill": "testing", "level": 0.9, "category": "architecture"}],
            "activity_status": "analysis"
        }
    )
    assert res.status_code == 200
    reg_data = res.json()
    assert reg_data["agent_id"] == test_id
    assert "schema_url" in reg_data  # Step 2 enhancement
    
    # 2. Verify registration
    res = requests.get(f"{BASE_URL}/agents/{test_id}/verify")
    assert res.status_code == 200
    verify_data = res.json()
    assert verify_data["status"] == "registered"
    assert verify_data["avatar"]["shape_complexity"] == 6  # architect = hexagon
    
    # 3. Report activity
    res = requests.post(
        f"{BASE_URL}/agents/activity",
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
        json={
            "agent_id": test_id,
            "status": "output",
            "task": "Running API tests",
            "metrics": {"test_pass": True}
        }
    )
    assert res.status_code == 200
    assert res.json()["metrics_stored"] is True
    
    # 4. Confirm avatar updated
    res = requests.get(f"{BASE_URL}/agents/{test_id}/verify")
    assert res.json()["avatar"]["dynamics_state"] == "output"

def test_avatar_schema_endpoint():
    """Verify /avatar/schema returns complete documentation."""
    res = requests.get(f"{BASE_URL}/avatar/schema")
    assert res.status_code == 200
    schema = res.json()
    
    assert "version" in schema
    assert "mapping_rules" in schema
    rules = schema["mapping_rules"]
    assert all(k in rules for k in ["color", "shape", "size", "dynamics"])
    
    # Verify ontology domains match backend
    assert len(schema["ontology_domains"]) == 9  # 9 canonical domains

def test_403_without_api_key():
    """Verify write endpoints reject unauthenticated requests."""
    res = requests.post(
        f"{BASE_URL}/agents/discover",
        headers={"Content-Type": "application/json"},
        json={"agent_id": "test", "name": "test"}
    )
    assert res.status_code == 403