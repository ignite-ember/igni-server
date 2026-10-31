# Visual baselines

The portal has a pixel baseline for every page, in light and dark, at
1440×900, full page. A layout that moves fails a test.

```bash
cd portal
./scripts/baselines-linux.sh              # compare — what CI does
./scripts/baselines-linux.sh --update     # regenerate, then review the diff
```

## Why it runs in a container

Playwright names baselines per-platform. Before E4 only
`-chromium-darwin.png` files were committed, so on Linux — the only
platform CI runs — there was nothing to compare against, and the tests
were excluded with `--grep-invert 'Visual regression'`, a filter that
produces no skipped test and a green job. That was the open half of
F67.

**Only the Linux baselines are committed.** Two sets means every
intentional design change has to regenerate both, and whoever
regenerates one leaves CI red for reasons unrelated to their change.
One set, owned by the environment that gates it.

The image is pinned to the Playwright version in `package.json`
(`mcr.microsoft.com/playwright:v<version>-noble`) and CI's portal job
runs in the same image, so the renderer and the fonts match. A local
Linux VM, or a different image tag, produces baselines that disagree
with CI and then fail forever on antialiasing.

On macOS the visual tests skip themselves, tagged `@needs-baseline`,
and the run prints each one with the file it is missing. Use the script
above to see a diff.

## What makes the comparison meaningful

**The data cannot move.** Every `/v1` response is replayed from
`portal/tests/e2e/fixtures/api/`, recorded off a real deployment and
then scrubbed. A request with no recording fails the test rather than
reaching a server: a page quietly rendering an error state would
otherwise become the baseline of an error state.

**The clock cannot move.** Sixteen places render Primer's
`RelativeTime`, so "4 days ago" means something different tomorrow.
`page.clock` pins now to `2026-09-03T12:00:00Z`, just after the newest
recorded timestamp.

**A missing baseline never writes itself.** `updateSnapshots: 'none'`
in the config. Playwright's default would let a runner *create* the
baseline it has none of and pass — a visual suite that approves
whatever it first sees. Generating is deliberate.

**The threshold is measured, not guessed.** `maxDiffPixels: 100`. Two
full runs at zero tolerance came out pixel-identical, so there is no
run-to-run noise to absorb; a font-size change on four short numbers
moved 4,985 pixels. The first value here was `maxDiffPixelRatio: 0.002`
— about 2,600 pixels — picked by feel, and loose enough to hide small
type changes.

## Re-recording the fixtures

Needed when a page starts calling a new endpoint, or a response shape
changes.

```bash
cd portal
PORTAL_E2E_RECORD=1 npx playwright test record-fixtures   # against a running stack
node scripts/scrub-fixtures.mjs                           # required
```

The scrub step is not optional and it is not cosmetic. The first
recording carried an email address at a domain we no longer use, an
ngrok tunnel URL pointing at a laptop, two internal container IPs, and
a group description reading "the damn contractors". The script replaces
them, then refuses to finish if anything identifying survives or if a
fixture stopped parsing.

Public pages are recorded as a *visitor*: the recorder forces
`/portal/me` to 401 for them, because the stack these fixtures come
from runs `PORTAL_AUTH_BYPASS` and answers 200 to anyone. Recorded
as-is, the sign-in page would have been captured in its signed-in
state — which is F130 and F133, one in a test and one in the product.

## When a baseline change is right

A failing visual test means the page changed. Two cases:

* **The change was intended.** Look at the diff in
  `portal/test-results/`, then `--update` and commit the new baselines
  *with* the change that caused them. A baseline commit on its own says
  nothing about whether the new rendering is correct.
* **It was not.** The diff names the page and the pixel count. Fix the
  page.

Never update a baseline to make a red build green without looking at
the diff. That is how the pre-E4 baselines came to show "Ember Code
v0.1.0" against a page that says "igni".
