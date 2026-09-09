# Recognition and iPhone release validation — 2026-09-09

The update fixes destructive recropping of close portrait card captures,
split OCR titles, footer separators and joined language/artist text, and a
basic-land safety flag that could survive a later rejection. Automatic adds
still require both confidence of at least 98.5 and independent printing proof.
This is an improvement to the server's existing local recognition pipeline;
scans are not sent to a new paid cloud model.

## Recent difficult captures: development comparison

The last 80 of 319 preserved, user-confirmed captures were used to reproduce
failures and refine the implementation. They are a development cohort, not a
blind test or a sample of all live scans. All source images were available.

| Measure | Before | After |
| --- | ---: | ---: |
| Exact printing ranked first | 69 / 80 (86.25%) | 78 / 80 (97.5%) |
| Correct card name ranked first | 72 / 80 (90%) | 80 / 80 (100%) |
| Exact printing in first five | 71 / 80 (88.75%) | 80 / 80 (100%) |
| Automatic adds | 41 / 80 | 57 / 80 |
| Manual review required | 39 / 80 (48.75%) | 23 / 80 (28.75%) |
| Incorrect automatic adds | 1 | 0 |
| Median recognition latency | 6,655 ms | 4,601.5 ms |
| 95th-percentile latency | 11,005 ms | 9,080 ms |

That is 41.0% fewer reviews and 30.9% lower median latency on this cohort.
One root cause was visible in the image pixels: the old portrait fallback
removed 48% of the image width, turning `Hog-Monkey` into `-Monkey`. Internal
rules boxes could also be enlarged in place of the physical card, losing the
actual title and footer. The fix preserves these already-cropped images.

## Separate older capture comparison

After freezing the recognition changes, a separate sample of 60 was selected
from the preceding 239 records with `random.Random(20260909).sample(older, 60)`.
No captures from the 80-image development cohort were included. The sample was
not used to tune the release after seeing its results.

| Measure | Before | After |
| --- | ---: | ---: |
| Exact printing ranked first | 50 / 60 (83.33%) | 49 / 60 (81.67%) |
| Correct card name ranked first | 59 / 60 | 58 / 60 |
| Exact printing in first five | 58 / 60 | 57 / 60 |
| Automatic adds | 41 / 60 | 42 / 60 |
| Manual review required | 19 / 60 (31.67%) | 18 / 60 (30%) |
| Incorrect automatic adds | 0 | 0 |
| Median recognition latency | 3,649 ms | 3,305.5 ms |
| 95th-percentile latency | 9,039 ms | 8,213 ms |

This is a small intervention improvement with mixed ranking results. It does
not establish a broad accuracy improvement for older sets. Reused-art lands
and printings with unreadable distinguishing marks remain the main unresolved
cases. Their uncertain suggestions were not automatically added.

## Method and limits

- Baseline recognition source matched the running server and local revision
  `9fe5fe5` by SHA-256 before changes. Candidate code ran from an isolated
  directory in the same server container with the same database and references.
- Each before/after run was sequential, with two OpenMP/OpenBLAS threads.
  Model/gallery warmup preceded scoring. Latency excludes phone upload time.
- The existing evaluator excludes each capture's own example ID and current
  image hashes from matching. Other confirmed examples remain available.
  This is not a fully independent dataset of unseen card identities.
- Evaluations call the recognizer directly. They do not add inventory, resolve
  reviews, or treat unconfirmed automatic predictions as correct labels.
- Both cohorts consist of previously reviewed cards. Their manual-review
  percentages must not be presented as the overall live manual-review rate.
- Zero observed incorrect automatic adds is not proof that the error rate is
  zero. Fresh scans, especially native iPhone photos, still need validation.
- The later native `full_photo` option leaves default historical preprocessing
  unchanged. Its full-frame versus cropped-frame distinction has synthetic and
  API coverage; it has not been validated with physical iPhone captures.

Private replay files are retained on the server as
`/tmp/mtglogger-astra-baseline.json`, `/tmp/mtglogger-astra-final.json`, and
`/tmp/mtglogger-independent-{before,after}.json`. The fixed independent manifest
SHA-256 is `017e8d22092a696800830b2f1eb941889fd7841605501a62833017cf8d489aa2`.
Aggregate results are published here; camera images and individual collection
records are not committed to the repository.

## Application verification

The final source passes backend regression tests, 55 frontend tests, and a
production web build. The iOS workflow runs five simulator tests before building
the ARM64 IPA. Backend retry tests cover the actual multipart HTTP contract,
repeated submissions, changed payload rejection, concurrent completion,
transaction rollback, and preserving separate physical copies.

Browser checks cover desktop and 320/393-pixel phone layouts, scanner controls,
collection bounds, mobile navigation, embedded app mode, and the download page.
See [iPhone acceptance](IPHONE.md#acceptance-on-a-physical-phone) for the remaining
installation, camera, local-network, backgrounding, and interrupted-upload checks.
