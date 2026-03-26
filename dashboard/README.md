# UnderCurrent Dashboard

Professional dark-theme React + Flask dashboard for the UnderCurrent adaptive control plane.

## Quick start

### 1. Backend (Flask API — port 5050)

```bash
cd /path/to/UnderCurrent
pip install flask flask-cors
python dashboard/backend.py
```

The backend reads:
- `stateless/traces.jsonl`  → track "S"
- `stateful/traces.jsonl`   → track "F"

API endpoints:
- `GET /api/traces`     — all trace entries (S + F combined)
- `GET /api/stats`      — KPI metrics
- `GET /api/containers` — latest state per container
- `GET /api/timeline`   — score over time per container

### 2. Frontend (Vite dev server — port 5173)

```bash
cd dashboard/frontend
npm install
npm run dev
```

Open http://localhost:5173

### Production build

```bash
cd dashboard/frontend
npm run build
# Serve dist/ with any static server
```

## Stack

- **Backend**: Python 3, Flask, flask-cors
- **Frontend**: React 18, Vite 5, Tailwind CSS 3, Recharts, TanStack Query, Axios, date-fns
- **Icons**: Material Symbols Outlined (Google Fonts)
- **Fonts**: Space Grotesk (headlines), Inter (body)
