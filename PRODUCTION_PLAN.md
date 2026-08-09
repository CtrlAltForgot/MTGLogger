# MTGLogger Production Handoff

Last updated: 2026-08-09

## Stop point

Work was deliberately stopped so the Unraid server can be restarted. There are no active migrations, builds, deployments, or partially edited application files. The Play goal is still active and is **not complete**: the current product is a substantial playable foundation, but it does not yet implement every Magic rule or card-specific effect.

The last deployed application revision is `304b0de` (`feat: enforce plot rules`). The handoff document is the only change after that revision.

## Current production state

- Unraid host: `root@192.168.1.254:22`
- SSH key: `/home/user/.ssh/id_devman` (use SSH with `-F /dev/null`)
- server source: `/mnt/user/appdata/mtglogger-src`
- Compose containers: `mtglogger-api-1`, `mtglogger-web-1`, and `mtglogger-db-1`
- API host endpoint: `http://127.0.0.1:18000`
- web host port: `5173`
- latest API image before shutdown: `sha256:d150a22d821798226148fbba948183d5d2eeacf81411916fc4923c736ce86a0b`
- latest web image: `sha256:52b515f2703c99e0e37e361c5f3c828df5efcda145362de40d3c02c737cc24a1`
- persistent frontend bundle at the stop point: `Play-D5wvBspO.js`
- latest backend engine verification: 262 tests passed
- latest frontend verification: 49 tests passed and the production build succeeded
- production health was good after the Plot deployment.

The Compose services use `restart: unless-stopped`, so they should return after an ordinary server restart. After boot, verify with the commands in the runbook below.

## Completed product scope

The project already includes the collection scanner, review flow, global database search, decks, auto-deck building, and the Play tab. The current Play implementation includes:

- an expanded, animated browser game table using database card images and owned decks;
- server-authoritative game sessions, persisted game state and action history, undo, save, and resume;
- zones, phases, stack interaction, combat, life, counters, tokens, and Commander handling;
- private/public player state and secure invite-link multiplayer;
- bot difficulty levels and randomized, synergistic auto-built bot decks;
- automatic safe phase advancement when no meaningful action is available;
- progressively expanding automatic rules enforcement.

Recent rules milestones, newest first:

- Plot (`304b0de`)
- Unearth (`6395aec`)
- Madness (`d2db722`)
- Affinity (`92e16b4`)
- Ninjutsu (`5d5bfda`)
- Day/night (`348e884`)
- Connive (`060f201`)
- Foretell (`fc45f9d`)
- Explore (`6805d90`)
- Suspend (`f35ba18`)
- Energy (`3fbb7db`)
- Morph/disguise, monarch/initiative, and manifest/cloak in the immediately preceding commits

## Known incomplete scope

Do not describe the rules engine as complete or Arena-equivalent yet. Full Magic coverage is extremely large and should continue as catalog-driven, tested milestones.

Plot currently supports the keyword identities and its static reducer, but bespoke effects that cause another card to become plotted still need support. Examples include Aven Interrupter, Fblthp, Jace, Kellan Joins Up, Lilah, and Make Your Own Luck.

High-value remaining keyword families from the latest catalog audit include Escape, Buyback, Channel, Rebound, Mutate, Evoke, Delve, Backup, Encore, Disturb, Dash, Adventure, Craft, Miracle, Blitz, Prototype, Reconfigure, and Transmute.

## Exact next milestone: Escape

Escape was audited but **no Escape code edits were started**. Continue from a clean tree.

The catalog contains both ordinary and special Escape forms:

- fixed alternate costs such as `Escape—{mana}, Exile N other cards from your graveyard`;
- Nethergoyf's variable exile choice with a four-or-more-card-types constraint;
- Lunar Hatchling's controlled-land exile plus five graveyard cards;
- creatures that escape with counters, including special counts such as Polukranos;
- Tizerus choosing its counter result;
- Kroxa, Uro, and Phlage sacrifice behavior when cast without escaping.

Recommended implementation sequence:

1. Parse Escape alternate mana costs and graveyard-exile requirements into structured rules metadata.
2. Add `source: "escape"` to the casting path and validate that the card is being cast from its owner's graveyard.
3. Let players select required additional-cost cards from the graveyard; validate and exile them atomically when casting.
4. Extend the cost-selection UI because its current generic selection searches only hand and battlefield.
5. Add the special variants above without weakening ordinary cost validation.
6. Teach bots to choose low-value graveyard cards while protecting useful recursion targets.
7. Add parser, reducer, API, visibility, bot, and UI regression tests.
8. Commit, deploy, smoke-test production, then continue to the next catalog mechanic.

Be careful in `frontend/src/pages/Play.tsx`: several JSX sections are long single lines. Keep the patch targeted and avoid an unrelated whole-file formatting rewrite.

## Verification gates

Run these before every rules milestone is committed:

```bash
/tmp/mtglogger-test-venv/bin/pytest -q backend/tests/test_game_engine.py
cd frontend
npm test -- --run
npm run build
git diff --check
```

Use broader backend tests when a change crosses API, persistence, deck construction, or database behavior.

## Unraid deployment runbook

Sync a committed revision into the server-side source tree:

```bash
git archive --format=tar HEAD | ssh -F /dev/null -i /home/user/.ssh/id_devman -p 22 root@192.168.1.254 "tar -xf - -C /mnt/user/appdata/mtglogger-src"
```

Inspect services after the server restarts:

```bash
ssh -F /dev/null -i /home/user/.ssh/id_devman -p 22 root@192.168.1.254 "cd /mnt/user/appdata/mtglogger-src && docker compose ps"
```

For backend changes, rebuild and restart the API service from the Compose directory, then check `http://127.0.0.1:18000/health`. For frontend changes, rebuild the web service and also copy the newly built `dist` output into the persistent frontend mount; rebuilding the image alone does not replace that mounted bundle. Confirm the served asset name and smoke-test the Play route after deployment.

## Repository and deployment discipline

- Preserve unrelated user changes in a dirty worktree.
- Use `apply_patch` for source and documentation edits.
- Keep mechanics in small, independently tested commits.
- Commit only after the relevant verification gates pass.
- Deploy only committed revisions.
- Never add an SSH password or private key contents to this repository.
- The SSH key does not normally expire; if access fails, first verify file permissions, server authorization, and host reachability.

