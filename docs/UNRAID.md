# Unraid deployment

MTGLogger can run as an always-on canonical collection on Unraid. The webcam remains attached to the computer or phone running the browser; it is not passed into Docker. Captures travel to the API over the LAN, and the scanner's **Last** and **Recognition** counters show network overhead separately from OCR time.

## Before deployment

- Install Git and Docker Compose support on Unraid, or use the Compose Manager plugin.
- Reserve an unused web port (the examples use `5173`).
- Plan a trusted HTTPS route before scanning remotely. Browsers allow webcams only on `localhost` or a trusted secure origin.
- Do not expose MTGLogger directly to the public internet. The MVP has no application login. Use a LAN-only trusted certificate, Tailscale ACLs/Serve, or an authenticated reverse proxy.

## Install

Clone the repository into a persistent appdata source directory:

```bash
mkdir -p /mnt/user/appdata/mtglogger-src
git clone https://github.com/CtrlAltForgot/MTGLogger.git /mnt/user/appdata/mtglogger-src
cd /mnt/user/appdata/mtglogger-src
```

Create the persistent directories. PostgreSQL's Alpine image runs as UID/GID `70`; the database directory must belong to it.

```bash
mkdir -p /mnt/user/appdata/mtglogger/postgres /mnt/user/appdata/mtglogger/data /mnt/user/appdata/mtglogger/paddlex
chown -R 70:70 /mnt/user/appdata/mtglogger/postgres
chmod 700 /mnt/user/appdata/mtglogger/postgres
```

Copy `.env.example` to `.env`, generate a unique database password, and edit the values. Keep `VITE_API_URL` blank so browsers use the same-origin web proxy. For Unraid, these additional settings keep FastAPI private while exposing the web UI:

```dotenv
API_BIND=127.0.0.1
API_PORT=8000
WEB_BIND=0.0.0.0
WEB_PORT=5173
PRICE_REFRESH_HOURS=12
```

`POSTGRES_PASSWORD` and the password embedded in `DATABASE_URL` must match. Set `SCRYFALL_USER_AGENT` to identify your installation with a contact address.

Build and start the stack with the Unraid bind-mount override:

```bash
docker compose -f docker-compose.yml -f docker-compose.unraid.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.unraid.yml ps
```

Wait for `db`, `api`, and `web` to report healthy. The first API start may take longer while PaddleOCR downloads and initializes its models. Follow it with:

```bash
docker compose -f docker-compose.yml -f docker-compose.unraid.yml logs -f api
```

The HTTP UI is then available at `http://UNRAID-IP:5173` for browsing. Webcam scanning from another device requires the trusted HTTPS route described below.

## HTTPS and webcam access

Proxy HTTPS to `http://UNRAID-IP:5173`; do not proxy port `8000`, because nginx already sends `/api` traffic to FastAPI over Docker's private network.

Good private options are:

- Tailscale Serve with tailnet ACLs and a valid Tailscale certificate.
- A LAN reverse proxy using a real domain certificate obtained through DNS validation and split DNS.
- An authenticated reverse proxy or access gateway if remote internet access is required.

Afterward, open the HTTPS hostname on the PC, allow camera access, select the USB or phone camera, and keep the guide empty during calibration. Scan 5–10 cards and compare **Last** with **Recognition**. A small difference means LAN/proxy overhead is not a bottleneck; high **Recognition** means the Unraid CPU itself is slower.

## Updates

From `/mnt/user/appdata/mtglogger-src`:

```bash
git pull --ff-only
docker compose -f docker-compose.yml -f docker-compose.unraid.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.unraid.yml ps
```

The bind-mounted database, review images, and OCR cache survive image replacement.

## Backup and recovery

Back up `/mnt/user/appdata/mtglogger` with the normal Unraid appdata backup process. For a transaction-consistent PostgreSQL backup, run `pg_dump` into a protected backup directory before an update. Review images live under `data/scans`; learned artwork hashes and all inventory metadata live in PostgreSQL.

Never delete the `postgres`, `data`, or `paddlex` directories during an update. Before restoring a raw appdata snapshot, stop the stack cleanly. A database dump is preferred when moving between PostgreSQL versions.

## Removal

To stop and remove only MTGLogger containers and its private Docker network:

```bash
docker compose -f docker-compose.yml -f docker-compose.unraid.yml down
```

This does not delete the bind-mounted appdata. Remove `/mnt/user/appdata/mtglogger` only when you deliberately want to erase the collection and backups.
