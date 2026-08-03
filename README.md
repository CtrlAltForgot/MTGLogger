# MTGLogger

MTGLogger is a speed-first Magic: The Gathering collection catalog. Its scanner watches a webcam, waits for a card to become stable, identifies the exact printing, and records it without requiring a confirmation click when confidence is high.

## Quick start

1. Optionally copy `.env.example` to `.env` to change database credentials and set a descriptive Scryfall user agent.
2. Run `docker compose up --build`.
3. Open <http://localhost:5173>. API documentation is at <http://localhost:8000/docs>.

The browser owns webcam access and sends stable captures to the API. This works in Docker and avoids passing a host camera device into a container. On startup, keep the card guide empty briefly while the scanner learns the background and camera noise. Recognition uses OCR, Scryfall exact metadata lookup, and perceptual artwork matching.

By default, matches at 98.5% confidence or higher are added automatically. That score requires near-exact OCR agreement; on modern frames the printed name, collector number, and set code all contribute independent evidence. A brief card-image receipt confirms the exact printing, price, and quantity without pausing capture. Uncertain scans are saved with their camera image to Review, show a brief warning receipt, and never interrupt the batch. Turn off **Auto-add near-certain matches** when you explicitly want the keyboard-first candidate dialog (arrow/number keys, **Enter** to accept, **Backspace** to decline).

After every capture the camera stays latched in **Remove card** state. It must observe the calibrated empty guide for three consecutive checks before another capture is possible, so a stationary card cannot be logged twice. This still supports two copies of the same card back-to-back: briefly expose the empty guide between them.

## Preparing Box Mode

Enter a Scryfall set code in the scanner (for example `FDN`) and choose **Index set artwork**. MTGLogger downloads that set's card images as a throttled background job, stores compact perceptual hashes rather than duplicate image files, and reports progress in the scanner. Scanning remains available while indexing runs. Once cached, artwork matching works without a Scryfall request and Box Mode strongly limits the visual search space.

The production API image includes CPU PaddleOCR 3. Paddle's inference model is initialized when the API starts and cached in the persistent `ocr_models` volume. Check `GET /api/scanner/capabilities` and `GET /api/references/status` when diagnosing recognition.

Market prices are refreshed from Scryfall in a background task every 24 hours and never block scanning. A refresh can also be started with `POST /api/prices/refresh`; progress is available from `GET /api/prices/status`.

Collection entries can be searched, sorted by newest/name/value, edited, or deleted. Review supports the captured camera image, automatic candidates, manual Scryfall printing search, ignore, and delete. The dashboard includes set/color/rarity/type breakdowns, valuable/newest cards, and duplicate printings. Sealed products are stored separately from singles.

Decks allocate physical copy quantities from inventory. The Deck Builder lists only copies that remain unassigned across all decks, supports search and filtered select-all, and returns copies to availability when an entry or deck is removed. A scan session can target `Deck · None` or an existing deck, causing each accepted copy to be assigned immediately. Storage Location remains free text for physical labels such as boxes and rows; user-facing collection-name and inventory-status controls are intentionally omitted.

## Unraid and LAN deployment

The Docker web container proxies `/api` internally to FastAPI, so the default Compose deployment works from other computers on the LAN without pointing their browsers at their own `localhost`. Leave `VITE_API_URL` blank (the recommended default), run `docker compose up -d --build`, and open `http://UNRAID-IP:5173`. Set strong PostgreSQL credentials in `.env` before a permanent deployment. Named volumes preserve PostgreSQL data, review images, and OCR models across container replacement.

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
