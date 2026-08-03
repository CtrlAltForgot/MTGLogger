# MTGLogger

MTGLogger is a speed-first Magic: The Gathering collection catalog. Its scanner watches a webcam, waits for a card to become stable, identifies the exact printing, and records it without requiring a confirmation click when confidence is high.

## Quick start

1. Optionally copy `.env.example` to `.env` to change database credentials and set a descriptive Scryfall user agent.
2. Run `docker compose up --build`.
3. Open <http://localhost:5173>. API documentation is at <http://localhost:8000/docs>.

Once served from the final HTTPS hostname, Chrome or Edge can install MTGLogger from the **Install app** action or the browser's install menu. The installed desktop window uses the same server-hosted client and local webcam, so there is no second database or scanner service to keep synchronized. Its service worker only provides an offline launch shell; collection and recognition operations still require the Unraid service.

With Chrome or Chromium installed, `./scripts/smoke-ui.sh` loads every lazy-rendered page through nginx and fails on missing content or browser runtime exceptions. Set `MTGLOGGER_URL=https://your-hostname` to check a remote deployment.

The included nginx layer accepts webcam payloads up to 16 MB (the API rejects anything above 15 MB), preserves the original HTTPS forwarding signal from an outer proxy, and allows camera access only from the page's own origin. The smoke script also proves FastAPI fully consumes a 2 MB request instead of either proxy rejecting or truncating it; the diagnostic endpoint never runs recognition or writes collection data.

The browser owns webcam access and sends stable captures to the API. This works in Docker and avoids passing a host camera device into a container. After camera permission is granted, a selector appears when multiple webcams are connected. On startup—or after changing cameras—keep the card guide empty briefly while the scanner learns the background and camera noise. Recognition uses OCR, Scryfall exact metadata lookup, and perceptual artwork matching.

Before a batch, choose condition, language, foil, storage location, and optionally a target deck or Box Mode set. Those defaults apply to every accepted card and are preserved with uncertain captures in Review. Recognition and manual Review searches constrain Scryfall by the selected language, preventing a foreign-language card from silently inheriting an English printing ID.

By default, matches at 98.5% confidence or higher are added automatically. That score requires near-exact printed evidence; artwork similarity alone is deliberately capped below the auto-add threshold because the same art can appear on multiple printings. On modern frames the printed name, collector number, and set code all contribute independent evidence. A brief card-image receipt confirms the exact printing, price, and quantity without pausing capture. Uncertain scans are saved with their camera image to Review, show a brief warning receipt, and never interrupt the batch. Turn off **Auto-add near-certain matches** when you explicitly want the keyboard-first candidate dialog (arrow/number keys, **Enter** to accept, **Backspace** to decline).

After every capture the camera stays latched in **Remove card** state. It must observe the calibrated empty guide for three consecutive checks before another capture is possible, so a stationary card cannot be logged twice. This still supports two copies of the same card back-to-back: briefly expose the empty guide between them.

The scanner displays live session counters for captures, additions, percentage routed to Review, the last browser round-trip, backend recognition time, and running average. It also reports cards per minute from the intervals between consecutive successful additions. The first addition begins measurement, and idle gaps longer than 30 seconds are discarded, so breaks do not distort batch throughput. Use these counters during a physical batch to verify one capture per presented card and measure the actual camera-to-result throughput. Scryfall metadata lookup shares a short time budget and runs alongside local artwork matching; if the network is unavailable, MTGLogger preserves the camera image in Review instead of losing the scan.

## Preparing Box Mode

Enter a Scryfall set code in the scanner (for example `FDN`) and choose **Index set artwork**. MTGLogger downloads that set's card images as a throttled background job, stores compact perceptual hashes rather than duplicate image files, and reports progress in the scanner. Scanning remains available while indexing runs. Once cached, artwork matching works without a Scryfall request and Box Mode strongly limits the visual search space.

The production API image includes CPU PaddleOCR 3 with the PP-OCRv4 mobile detection and recognition models. The mobile models keep CPU scans responsive while the ranking stage combines the printed name, collector number, set code, copyright year, Box Mode, and cached artwork similarity. Paddle's inference model is initialized when the API starts and cached in the persistent `ocr_models` volume. Check `GET /api/scanner/capabilities` and `GET /api/references/status` when diagnosing recognition; per-stage recognition timings are also written to the API log.

Saved pending captures can be replayed through the current recognizer without changing Review or inventory: `docker compose exec api python -m mtglogger.tools.replay_reviews --limit 20`. This is useful after recognition upgrades because it compares old and current confidence against the same physical images.

Market prices are refreshed from Scryfall in a throttled background task every 12 hours and never block scanning. Metadata and price requests reuse a bounded HTTP connection pool, avoiding a new TLS handshake for every card. Set `PRICE_REFRESH_HOURS` to a different interval when deploying; 12–24 hours is recommended. A refresh can also be started with `POST /api/prices/refresh`; progress is available from `GET /api/prices/status`. Collection value and the displayed physical-card count are quantity-aware. Changed card prices retain their prior observation for up/down indicators, while the Value tab charts real collection snapshots without fabricating historical data.

Collection entries can be searched, sorted by newest/name/value, paged at user-selectable sizes, edited, or deleted, so collections containing thousands of printings remain browsable without rendering every card image at once. Review preserves the captured camera image and the scan-time condition, foil, storage, and deck defaults, then supports automatic candidates, manual Scryfall printing search, ignore, and delete. Resolving an item applies those original defaults, so cards do not need to be physically resorted after a batch. The dashboard includes set/color/rarity/type breakdowns, valuable/newest cards, and duplicate printings. Sealed products are stored separately from singles and can be edited for type, set, quantity, purchase/market price, storage, and notes.

Decks allocate physical copy quantities from inventory. The Deck Builder lists only copies that remain unassigned across all decks, supports search, paginated browsing, and select-all on the current filtered page, and returns copies to availability when an entry or deck is removed. A scan session can target `Deck · None` or an existing deck, causing each accepted copy to be assigned immediately. Storage Location remains free text for physical labels such as boxes and rows; user-facing collection-name and inventory-status controls are intentionally omitted.

## Unraid and LAN deployment

For an always-available collection, run MTGLogger on Unraid as the canonical service. No separate scanner program must remain open on the PC: a browser on the PC captures its attached webcam and sends the image to the Unraid API, while a phone browser can use the phone camera against the same collection. `restart: unless-stopped` brings every service back after a server restart.

LAN transfer is normally small compared with OCR, but server CPU speed varies. Compare the scanner's **Last** round-trip with **Recognition** time during a short batch; the difference exposes browser/network overhead. If Unraid recognition itself is materially slower, running the same Compose stack locally remains the lowest-latency option. An optional local recognition worker can be added later without moving the canonical database, but only if measurements justify that extra architecture.

The Docker web container proxies `/api` internally to FastAPI, so the default Compose deployment works from other computers on the LAN without pointing their browsers at their own `localhost`. Leave `VITE_API_URL` blank (the recommended default), run `docker compose up -d --build`, and open `http://UNRAID-IP:5173`. Set strong PostgreSQL credentials in `.env` before a permanent deployment. Named volumes preserve PostgreSQL data, review images, and OCR models across container replacement.

Run `docker compose ps` to check readiness: `db`, `api`, and `web` should all report healthy. API health verifies PostgreSQL rather than only the HTTP process, and web health verifies the nginx-to-API proxy, so a green stack represents a usable collection path. The API health check allows extra startup time for the first PaddleOCR model initialization, and the web service waits for that check before starting. Use `docker compose logs -f api` to watch OCR initialization, recognition timings, artwork indexing, and background pricing.

Browsers only permit webcam access in a secure context. `http://localhost:5173` works on the machine hosting the browser; access from another device using an Unraid IP or hostname must be placed behind a trusted HTTPS reverse proxy (for example, an Unraid-managed proxy with a valid local or public certificate). MTGLogger reports this requirement directly instead of failing with an unavailable-camera error.

See [the Unraid deployment guide](docs/UNRAID.md) for persistent appdata bind mounts, private API binding, HTTPS choices, updates, verified PostgreSQL backup/restore commands, and latency measurement.

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
npm test
npm run dev
```

Without `DATABASE_URL`, the backend uses `backend/mtglogger.db`. Install `.[ocr]` instead of `.` to enable PaddleOCR during local development. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the recognition flow and extension points.
