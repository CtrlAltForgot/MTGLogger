# Architecture

The React client performs low-cost motion and stability checks against downscaled webcam frames. A stable card is captured once, and another capture is prohibited until the card leaves. This keeps camera latency out of the server and preserves the no-click workflow.

FastAPI accepts the crop and runs recognition off the request thread. The recognizer rectifies the largest card-shaped quadrilateral, extracts PaddleOCR hints, computes a perceptual hash of the artwork region, and fuses OCR and visual scores. Box Mode restricts both Scryfall and local artwork candidates to its selected set. Scores above 95 are atomically upserted into inventory, scores from 70 through 95 are returned as suggestions, and lower scores enter the review queue.

Reference indexing is explicitly set-scoped. `/api/references/sync/{set_code}` starts a throttled background download from Scryfall, computes 64-bit pHashes, and stores only metadata plus hashes in PostgreSQL. It is resumable because existing Scryfall IDs are skipped. This makes a booster-box workflow fast without imposing an enormous all-printings bootstrap.

PostgreSQL is the production database. SQLite is supported for tests and simple development. Inventory uniqueness is based on printing, finish, language, condition, collection, and storage location. Providers implement small metadata and pricing interfaces so Scryfall can later be joined by commercial sources. Camera thresholds are client-side and tunable live; brightness, contrast, and motion telemetry make calibration observable rather than guesswork.
