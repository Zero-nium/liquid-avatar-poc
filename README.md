# Liquid Avatar — PoC

Agent-orchestrated visual identity system for AI swarms.
Implements schema v1.1 by Aura Quorum / Small Council.

## Quick Start

### Local Development

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Then seed the mock swarm:
```bash
curl -X POST http://localhost:8000/seed/mock-swarm
```

Open `http://localhost:8000` in your browser.

### Deploy to Render (Free Tier)

1. Push this repo to GitHub
2. Create new Web Service on Render
3. Set build command: `pip install -r backend/requirements.txt`
4. Set start command: `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Deploy

### Deploy to Fly.io (Free Tier)

```bash
cd backend
fly launch --name liquid-avatar-poc
fly deploy
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/agents` | GET | List all agents |
| `/agents/{id}` | GET | Agent profile + history |
| `/agents/register` | POST | Register new agent |
| `/agents/report` | POST | Agent self-reports state |
| `/swarm/map` | GET | Swarm layout data |
| `/ontology` | GET | Council ontology |
| `/seed/mock-swarm` | POST | Seed mock data |
| `/mcp/query` | POST | MCP agent interface |

## Architecture

```
┌─────────────────────────────────────────┐
│  Browser (D3.js Visualization)          │
├─────────────────────────────────────────┤
│  FastAPI Backend                        │
│  ├── Agent Status API                   │
│  ├── Avatar Computation Engine          │
│  ├── Ontology Store (SQLite)            │
│  └── MCP Server (agent-to-agent)        │
├─────────────────────────────────────────┤
│  SQLite (file-based, zero cost)         │
└─────────────────────────────────────────┘
```

## Schema v1.1 Implementation

- **Expertise → Color**: Determined by dominant skill category via ontology lookup
- **Role → Geometry**: Architect=hexagon, Optimizer=triangle, Auditor=octagon, Chronicler=circle
- **Activity → Dynamics**: idle/input/output/analysis/verification with distinct animations

## Cost Model

- **SQLite**: $0 (file-based)
- **FastAPI + Uvicorn**: $0 (open source)
- **D3.js via CDN**: $0
- **Render free tier**: $0 (sleeps after 15min inactivity)
- **Fly.io free tier**: $0 (256MB RAM, 3 shared-cpu)

No inference costs. No external APIs required for core functionality.
