# MTGLogger roadmap

This roadmap separates current product work from future ideas so the active scanner milestone stays focused and finishable.

## Current priority: dependable web scanner

- Improve exact-printing recognition against real webcam captures, especially reused artwork and basic lands. Land matching should use the exact artwork and printing evidence without making ordinary lands unnecessarily difficult to auto-add; only genuinely ambiguous land captures should require review.
- Measure top-1, top-k, false-auto-add, uncertainty, and latency results with saved and fresh physical scans.
- Keep uncertainty attached to the physical scan without interrupting fast batch handling.
- Preserve strict duplicate protection while allowing immediate card-to-card swaps.
- Finish the collection, deck, pricing, backup, and responsive UI workflows needed for daily use.

## After the web app is dependable

### Mobile companion scanner

Build a phone-first companion that connects to the same server-side collection and recognition database. It should make camera setup easier, use the phone's higher-quality camera, retain the same exact-printing safeguards, and avoid creating a separate collection that needs synchronization.

This is a long-term project item, not part of the active recognition milestone.

### Optional reference-data snapshot

Evaluate publishing a versioned, compressed recognition-profile snapshot as a release asset. It must be optional, verifiable, resumable, compliant with upstream data/image policies, and never replace the normal incremental updater. Large generated reference data should not be committed directly to the Git repository.

## Later ideas—not currently scheduled

- Pokémon, Yu-Gi-Oh!, Lorcana, and sports-card support
- Automated deck suggestions and format-aware deck building
- Marketplace and inventory synchronization
- AI-assisted condition grading
- Dedicated desktop packaging beyond the installable web app
