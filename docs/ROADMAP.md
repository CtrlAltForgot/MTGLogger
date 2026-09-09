# MTGLogger roadmap

This roadmap separates current product work from future ideas so the active scanner milestone stays focused and finishable.

## Current priority: dependable web scanner

- Improve exact-printing recognition against real webcam captures, especially reused artwork and basic lands. Land matching should use the exact artwork and printing evidence without making ordinary lands unnecessarily difficult to auto-add; only genuinely ambiguous land captures should require review.
- Measure top-1, top-k, false-auto-add, uncertainty, and latency results with saved and fresh physical scans.
- Keep uncertainty attached to the physical scan without interrupting fast batch handling.
- Preserve strict duplicate protection while allowing immediate card-to-card swaps.
- Finish the collection, deck, pricing, backup, and responsive UI workflows needed for daily use.

## iPhone companion

The native iOS companion now has rear-camera still capture, steady-card detection,
photo import, batch defaults, a durable upload outbox, and the full website in
embedded collection/review/library tabs. Its SideStore distribution workflow
builds an ARM64 IPA and source feed. See [iPhone details](IPHONE.md).

Physical-phone installation, camera quality, backgrounding, and interrupted Wi-Fi
acceptance remain to be verified on the user's device. Fresh phone scans should
be kept separate from the historical camera cohort when measuring recognition.

## Optional future distribution

### Optional reference-data snapshot

Export/import tooling now creates a versioned, checksummed recognition-profile
snapshot split for GitHub Release assets. Publishing the first snapshot remains
pending an upstream-data policy review; generated reference data must never be
committed directly to Git history. The snapshot stays optional and never replaces
the normal resumable updater.

## Later ideas—not currently scheduled

- Pokémon, Yu-Gi-Oh!, Lorcana, and sports-card support
- Automated deck suggestions and format-aware deck building
- Marketplace and inventory synchronization
- AI-assisted condition grading
- Dedicated desktop packaging beyond the installable web app
