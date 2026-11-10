# Air-gap rehearsal

`tools/airgap-rehearsal.sh` installs the free base on a Kubernetes cluster
that cannot reach the internet, from the offline bundle alone. It then
reports every attempt anything in that cluster made to reach outside.
[OFFLINE_UPDATES.md](OFFLINE_UPDATES.md) says "Nothing in this process
contacts Ignite Ember or the internet". This script is how we check that
claim instead of just asserting it.

```sh
tools/airgap-rehearsal.sh          # the whole thing; deletes the cluster at the end
tools/airgap-rehearsal.sh --keep   # leave the cluster and the gap's registry up
```

Run it from a checkout of igni-server, with the igni CLI checked out beside
it (`../igni`, `uv sync` run) if you want the CLI steps. Needs docker,
kind, kubectl, helm, skopeo, cosign, jq and python3. syft is optional:
with it, the images are signed and their SBOMs attested, and
`igni-import --cosign-key` checks both. It takes about 15 minutes on
a laptop. Peak memory is about 3 GB in Docker: one kind node, Postgres,
Redis, the API, the portal and a stub model.

It runs weekly in CI and can be started by hand:
`.github/workflows/airgap-rehearsal.yml`.

## What it does

**1. Ignite Ember's side (connected).** It builds `igni-api` and
`igni-portal` from the checkout and pulls the third-party images the
install needs (`postgres:17.6-alpine`, `redis:7.4-alpine`). All four go into a
throwaway staging registry, where each is signed with a **TEST** cosign
key and given a CycloneDX SBOM attestation (syft). Then
`tools/release/igni-bundle export-image` exports each one, `helm package`
adds the chart, and `igni-bundle seal` signs the bundle with the same
**TEST** cosign key. These are the same steps the release's `offline-bundle` job
runs, with two differences: the keys, and the two third-party images that
the release does not carry (see "What it found").

**2. The gap.** It creates a Docker network for the gap, a registry on it
(the customer's registry), and a kind cluster with one control-plane
node on that network. Netfilter rules inside the node's network namespace
then drop and log (NFLOG) every packet that is not addressed to the
cluster (pods, services), the gap's network or loopback. Every pod and
every node process sends from that namespace: kubelet, containerd, and
CoreDNS when it forwards upstream. Docker's embedded DNS and the network
gateway (the host) count as outside. Before the rules go in, a `curl`
from the node to 1.1.1.1 has to succeed. That is the control, and it
shows the test can fail. After the rules, curl from the node, DNS from
the node, a `crictl pull` from Docker Hub, and sockets from a pod to
1.1.1.1, example.com and ghcr.io must all fail.

A `docker network create --internal` network would be the obvious gap.
kind's node entrypoint cannot start on one: it rewrites Docker's DNS
through the network gateway, and an internal network has no gateway.

**3. The import.** The customer's steps: `igni-import` is taken from
inside the bundle and run with the TEST public keys, `--expect-version`,
`--keep` and `--cosign-key`. It verifies the bundle and pushes the images
into the gap's registry. Then `helm install` runs on the chart from the
verified bundle, with `image.repository` and `portalImage.repository`
pointed at that registry; the backup CronJob's Postgres image follows
`image.repository` there. There is no chart repository, and no image is
side-loaded.

**4. Smoke and journey.** The migration Job completes, the API and portal
roll out, `/health`, `/ready` (Postgres and Redis ok) and `/version`
answer, and the portal serves the app. Then the journey:

- the first account signs up (the one-time code is read from the API
  log, `email.logSignInCodes`);
- it creates an organisation with a claimed domain;
- it invites a member, and the member signs up and is a `member` of the
  organisation;
- the igni CLI runs its own `igni login`: callback server,
  `/v1/auth/exchange`, credentials file. The portal's `/cli-auth` step is
  done over HTTP (`/v1/cli/token`, then the CLI's callback), not in a
  browser;
- `igni -m 'Say hello'` goes through the chat proxy to a stub model
  (`tools/e2e_journey/stub_openai.py`) running as a pod inside the gap,
  and the stub logs the completion request.

Without the CLI checkout, the last two steps are replaced by an
org-scoped token and a direct `/v1/chat/completions` call. The output
says which ran.

**5. The report.** The evidence goes in `$IGNI_AIRGAP_WORK/report` (by
default `${TMPDIR:-/tmp}/igni-airgap-rehearsal/report`), and
`tools/airgap_egress.py` summarises it in `egress.md`:

| Evidence | From |
|---|---|
| Every packet the gap dropped | tcpdump on `nflog:5` in the node's namespace (`dropped.pcap`) |
| Every connection attempt and DNS packet, on every interface | tcpdump `-i any` in the node's namespace (`attempts.pcap`) |
| Every name any pod looked up, and which pod | CoreDNS with the `log` plugin (`coredns.log`) |
| Every image pull | `kubectl get events -A`, and `crictl images` on the node |
| Who had which address | `kubectl get pods -A` |

Each attempt is labelled with its phase. **Before the gap** means kind
booting. **Probe** means the deliberate failures in step 2. Anything in
**install/journey** fails the run. So does an image pull from anywhere
other than the gap's registry.

## TEST keys

The release key is in Cloud KMS and only igni__release's CD can sign with
it (OFFLINE_UPDATES.md, "The release key"). The rehearsal generates `TEST-cosign.{key,pub}` (cosign,
password `TEST-only-not-a-release-key`) under `$IGNI_AIRGAP_WORK/keys` on
every run, and deletes it with the cluster. It signs the images and the
bundle, as the release key does, and nothing it signs leaves the
machine. It cannot be given the release key, and should not be: only CD
signs releases.

## Settings

| Variable | Default | |
|---|---|---|
| `IGNI_AIRGAP_NAME` | `igni-airgap` | cluster, network and container prefix |
| `IGNI_AIRGAP_WORK` | `${TMPDIR:-/tmp}/igni-airgap-rehearsal` | bundle, evidence, report |
| `IGNI_AIRGAP_SUBNET` | `172.30.250.0/24` | the gap's Docker network |
| `IGNI_AIRGAP_SKIP_BUILD` | unset | `1` reuses `igni-api:airgap` and `igni-portal:airgap` |
| `IGNI_CLI` | `../igni` | the CLI checkout |

Ports on 127.0.0.1: 55100 (staging registry, only during step 1), 55101
(the gap's registry, as the import host sees it), 56443 (the cluster's
API server), 18777 and 18300 (port-forwards to the API and the portal).

## What it found (first run, 2026-11-03)

No workload tried to leave the gap. During the install and the journey,
the API, the migration Job, the portal, Postgres, Redis and the stub
model looked up no name outside the cluster and sent no packet outside
it. Every image pull named the gap's registry. Outside traffic appeared
in two places only. The first was the rehearsal's own probes. The second
was CoreDNS checking its upstream (`NS .` from `forward`, `HINFO` from
`loop`), which is kind's Corefile and not igni. A customer's CoreDNS
forwards to their own resolver.

What the run did find:

- **The backup CronJob defaulted to Docker Hub.** The chart treats
  Postgres and Redis as external (`charts/igni/values.yaml`, "The data
  stores, both external"), but its backup CronJob ran
  `postgres:17.6-alpine`, an unqualified name, so an air-gapped install
  that turned on `backup.enabled` pulled from the internet, or failed to.
  Fixed (B38): the image now comes from beside `image.repository`
  (`<registry>/postgres:17.6-alpine`), every bundle carries that image,
  and `tests/test_the_helm_chart_deploys_igni.py` fails if any rendered
  image, with every optional feature on, points outside the customer's
  registry. The rehearsal no longer overrides `backup.image`.
- **`igni-bundle export-image` failed on a Mac.** `skopeo inspect
  --format {{.Digest}}` picks the image-index entry for the host's
  platform, and darwin has none. It now takes the sha256 of the raw
  manifest (`tools/release/igni-bundle`), which is also exactly what the
  tag names.
- `socialSignIn.providers` defaulted to `[google]`. With no client ID it
  caused no traffic here, and the sign-in page offered no button, but the
  sign-up page hardcoded one. Fixed (B39): the chart lists Google only
  with `socialSignIn.googleClientId`, and both pages show only what
  `/v1/auth/methods` reports.

## What it does not cover

- **The browser.** The portal is served from inside the gap, but nothing
  renders it. The rehearsal flags any absolute `http(s)://` address in the
  portal's `index.html`. What the running app fetches after that (for
  example Monaco, which 0fcce906 made local) needs the browser suites.
- **The CLI's host.** The igni CLI runs on the operator's machine, outside
  the gap, so its own network use is not captured here.
- **SSO.** Dex is not in the bundle. Sign-in uses email and one-time code.
  An SSO rehearsal would need the IdP inside the gap too.
- **The paid modules and Fleet.** Free base only.
