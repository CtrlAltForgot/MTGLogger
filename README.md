# MTGLogger

MTGLogger is a speed-first Magic: The Gathering collection catalog. Its scanner watches a webcam, waits for a card to become stable, identifies the exact printing, and records it without requiring a confirmation click when confidence is high.

## Quick start

1. Optionally copy `.env.example` to `.env` to change database credentials and set a descriptive Scryfall user agent.
2. Run `docker compose up --build`.
3. Open <http://localhost:5173>. API documentation is at <http://localhost:8000/docs>.

The browser owns webcam access and sends stable captures to the API. This works in Docker and avoids passing a host camera device into a container. On startup, keep the card guide empty briefly while the scanner learns the background and camera noise. Recognition uses OCR, Scryfall exact metadata lookup, and perceptual artwork matching.

By default, a recognized card opens a keyboard-first confirmation: press **Enter** to add the exact printing or **Backspace** to decline it. The camera stays latched throughout the decision and cannot scan that card again until it leaves the calibrated guide. Disable **Confirm each identified card** in batch defaults to auto-add matches above 95% confidence. Low-confidence failures continue directly to the review queue without interrupting a batch.

## Preparing Box Mode

Enter a Scryfall set code in the scanner (for example `FDN`) and choose **Index set artwork**. MTGLogger downloads that set's card images as a throttled background job, stores compact perceptual hashes rather than duplicate image files, and reports progress in the scanner. Scanning remains available while indexing runs. Once cached, artwork matching works without a Scryfall request and Box Mode strongly limits the visual search space.

The production API image includes CPU PaddleOCR 3. Paddle's inference model is initialized when the API starts and cached in the persistent `ocr_models` volume. Check `GET /api/scanner/capabilities` and `GET /api/references/status` when diagnosing recognition.

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

Without `DATABASE_URL`, the backend uses `backend/mtglogger.db`. Install `.[ocr]` instead of `.` to enable PaddleOCR during local development. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the recognition flow and extension points.
