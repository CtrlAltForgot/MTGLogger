# MTGLogger

> **Log your TCG collection.**

MTGLogger is a fast, camera-powered catalog for **Magic: The Gathering** cards. Hold a card under a webcam, let MTGLogger identify the exact printing, and move on to the next card. High-confidence matches are added automatically; uncertain cards stay attached to their camera capture so you can resolve them safely.

<p align="center">
  <img src="frontend/public/mtglogger-card-stack.png" alt="MTGLogger card-stack logo" width="160">
</p>

## What you get

- Automatic webcam capture when a card is present and steady
- Exact-printing recognition using card text, artwork, frame, set, and collector-number evidence
- Protection against counting the same physical card twice
- Collection browsing, search, sorting, editing, and deletion
- Current Scryfall prices and collection-value history
- Deck organization and physical-copy allocation
- CSV and JSON export
- An always-on web app that can run on a PC or Unraid server
- A local MTG recognition database covering current Scryfall paper printings

MTGLogger is currently a **usable beta**. Keep the confirmation/review workflow enabled while testing valuable, unusual, foreign-language, heavily foiled, damaged, or visually similar printings.

## Choose your setup

| I want to… | Recommended setup | Camera location |
| --- | --- | --- |
| Try MTGLogger on one computer | Docker Desktop or Docker Engine | Attached to that computer |
| Keep my collection available all the time | Unraid | Attached to any PC or phone opening the site |
| Develop or change the code | Local Python and Node.js tools | Attached to the development computer |

The webcam is used by your **web browser**, not by Docker. An Unraid server can therefore store and recognize your collection while the camera remains connected to your PC.

## Quick start — try it on one computer

### 1. Install the prerequisites

Install:

- [Git](https://git-scm.com/downloads)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) on Windows or macOS, or Docker Engine with Compose on Linux
- Chrome, Edge, or another modern Chromium-based browser

Open Docker Desktop and wait until it reports that Docker is running.

### 2. Download MTGLogger

Open a terminal and run:

```bash
git clone https://github.com/CtrlAltForgot/MTGLogger.git
cd MTGLogger
```

If you downloaded a ZIP instead, extract it, open the extracted `MTGLogger` folder in a terminal, and continue below.

### 3. Start the app

```bash
docker compose up -d --build
```

The first launch can take several minutes while Docker builds the app and the OCR engine initializes. Later launches are faster.

### 4. Check that it started

```bash
docker compose ps
```

Wait until `db`, `api`, and `web` show **healthy**. Then open:

**[http://localhost:5173](http://localhost:5173)**

### 5. Scan your first card

1. Open **Scanner** and allow camera access.
2. Choose the correct camera if more than one is connected.
3. Leave the table empty briefly so MTGLogger can calibrate.
4. Place one card anywhere clearly visible in the camera view.
5. Hold it still while MTGLogger captures and identifies it.
6. Check the large last-scan image before moving to the next card.

For the best results, fill a useful portion of the frame, avoid glare, keep the full card visible, and place the camera parallel to the card. A fixed camera mount or card slinger can improve consistency.

## Everyday use

### Automatic and reviewed matches

- **Near-certain match:** added automatically when strict printing-specific evidence reaches the configured threshold.
- **Uncertain match:** shown for immediate resolution or saved with its camera capture for later review, depending on your scanner setting.
- **Failed match:** never silently added as a guess.

Artwork alone cannot always prove an exact printing because multiple sets can reuse the same art. MTGLogger also looks for collector numbers, set/footer details, card frames, language, and other visible evidence. This is especially important for basic lands.

### Avoiding duplicate scans

After identifying a card, MTGLogger compares the live view with the previous card. It will not log another copy until it sees that the physical card changed or left the view. Two identical cards can still be scanned back-to-back as separate physical copies.

### Batch defaults

Before a session, set the condition, language, foil status, storage location, and optional deck. These defaults are applied to accepted cards and preserved with uncertain captures.

### Collection and decks

The **Collection** page lets you search, sort, edit, delete, export, inspect, and assign physical copies to decks. Foil and nonfoil copies are tracked separately because their prices differ. Deck allocation prevents the same physical copy from being assigned to multiple decks.

### Prices

Scryfall prices refresh in the background, normally once per hour. Price requests never pause scanning. The **Value** page shows observed collection snapshots; it does not invent history from before MTGLogger began recording it.

### Installing it like a desktop app

When MTGLogger is served from `localhost` or a trusted HTTPS address, Chrome and Edge can install it from the browser's install menu. The installed window still uses the same server, collection, and local camera—there is no second database to synchronize.

## Run it on Unraid

Use Unraid when you want the collection and dashboard available even while your scanning PC is off.

Important points:

- Your webcam stays connected to the PC or phone running the browser.
- Only the web port (`5173` by default) should be exposed through your reverse proxy.
- Remote camera access requires a trusted **HTTPS** address; browsers normally block webcams on plain HTTP LAN addresses.
- MTGLogger does not yet include user accounts. Do not expose it openly to the internet without an authenticated access layer.
- Collection data and recognition profiles should live on persistent Unraid shares, not disposable container storage.

Follow the step-by-step **[Unraid installation guide](docs/UNRAID.md)** for folders, permissions, HTTPS, updates, backups, and recovery.

## Configuration

The included defaults work for a local trial. For a permanent installation, copy the example configuration:

macOS or Linux:

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Open `.env` in a text editor and change at least:

- `POSTGRES_PASSWORD`
- the same password inside `DATABASE_URL`
- `SCRYFALL_USER_AGENT` to include a contact email

Keep `VITE_API_URL` blank for the normal same-origin Docker setup.

After changing configuration, apply it with:

```bash
docker compose up -d --build
```

## Updating

From the MTGLogger folder:

```bash
git pull --ff-only
docker compose up -d --build
docker compose ps
```

Normal updates preserve the database and saved scans.

> [!WARNING]
> Do not run `docker compose down -v` unless you intentionally want to delete Docker-managed MTGLogger data. On Unraid, also keep the persistent appdata and reference-data directories intact.

## Troubleshooting

### The page does not open

Run:

```bash
docker compose ps
docker compose logs --tail=100 web api db
```

All three services should be healthy. If this is the first launch, give the API additional time to initialize OCR.

### The camera is black or unavailable

- Confirm another app is not exclusively using the camera.
- Allow camera permission in the browser's address-bar settings.
- Refresh the page after changing permission.
- Use `http://localhost:5173` on the same computer, or a trusted HTTPS address when connecting to another machine.
- Select the correct camera in Scanner when multiple cameras exist.

### Cards capture too early or never capture

- Recalibrate with an empty table.
- Improve lighting and reduce reflections.
- Keep the full card visible and reasonably large in frame.
- Adjust the scanner calibration controls gradually rather than changing all of them at once.

### Recognition is slow

The scanner shows browser round-trip and backend recognition timing separately. If recognition time is high, the server CPU is the bottleneck; if only round-trip time is high, inspect the network or reverse proxy.

### A card is identified as the wrong printing

Do not accept it. Choose the exact printing in the uncertainty screen or correct it from Review. Clear images of the collector/footer area are essential when printings share artwork.

### The MTG database is incomplete or interrupted

Open **Database** to see its live status. Syncing is resumable and keeps completed profiles; a restart should continue missing work instead of rebuilding the completed database.

## Backups

A collection is worth backing up. The Unraid guide contains verified PostgreSQL backup and restore commands and explains which scan/reference folders should be included. For other Docker hosts, preserve both the PostgreSQL volume and API data volume.

## For developers

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

Without `DATABASE_URL`, the backend uses `backend/mtglogger.db`. Install `.[ocr]` to enable PaddleOCR locally.

Useful references:

- [Architecture and recognition flow](docs/ARCHITECTURE.md)
- [Unraid deployment, updates, and backup](docs/UNRAID.md)
- [Project roadmap](docs/ROADMAP.md)
- API documentation while running: [http://localhost:8000/docs](http://localhost:8000/docs)

## Project direction

MTGLogger prioritizes scanning speed, exact-printing reliability, and a pleasant collection workflow over feature count. Pokémon, Yu-Gi-Oh!, Lorcana, sports cards, marketplace synchronization, automated condition grading, and automatic deck building are intentionally outside the current MVP.

See the **[project roadmap](docs/ROADMAP.md)** for the deliberately separated future mobile companion work.

## Scryfall

MTGLogger uses data and images from [Scryfall](https://scryfall.com/). Scryfall is not produced by or endorsed by Wizards of the Coast. Card images and Magic: The Gathering are property of Wizards of the Coast.
