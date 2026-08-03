# Architecture

The React client performs low-cost motion and stability checks against downscaled webcam frames. A stable card is captured once, and another capture is prohibited until the card leaves. This keeps camera latency out of the server and preserves the no-click workflow.

FastAPI accepts the crop and runs recognition off the request thread. The recognizer rectifies the largest card-shaped quadrilateral, extracts OCR hints, searches Scryfall, and ranks candidates. Scores above 95 are atomically upserted into inventory, scores from 70 through 95 are returned as suggestions, and lower scores enter the review queue. Network price refreshes are separate jobs and never block the scanner loop.

PostgreSQL is the production database. SQLite is supported for tests and simple development. Inventory uniqueness is based on printing, finish, language, condition, collection, and storage location. Providers implement small metadata and pricing interfaces so Scryfall can later be joined by commercial sources.

