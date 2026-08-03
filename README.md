# MTGLogger

MTGLogger is a speed-first Magic: The Gathering collection catalog. Its scanner watches a webcam, waits for a card to become stable, identifies the exact printing, and records it without requiring a confirmation click when confidence is high.

## Quick start

1. Optionally copy `.env.example` to `.env` to change database credentials and set a descriptive Scryfall user agent.
2. Run `docker compose up --build`.
3. Open <http://localhost:5173>. API documentation is at <http://localhost:8000/docs>.

The browser owns webcam access and sends stable captures to the API. This works in Docker and avoids passing a host camera device into a container. Recognition uses OCR when installed, Scryfall exact metadata lookup, and perceptual artwork matching. Uncertain scans are retained in the review queue without stopping the scan loop.

## Local development

Backend:

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
uvicorn mtglogger.main:app --reload
pytest
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Without `DATABASE_URL`, the backend uses `backend/mtglogger.db`. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the recognition flow and extension points.
