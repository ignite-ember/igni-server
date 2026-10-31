# Google Cloud Marketplace listing

Three artefacts, and only two of them can be finished in this
repository.

| | What it is | State |
|---|---|---|
| `schema.yaml` | The form a customer fills in, and how its answers map onto Helm values | **Written.** Parameter set verified against the chart; structure not yet run through `mpdev` |
| `charts/igni/templates/application.yaml` | The Application object, so the deployment appears in the console as one thing | **Written.** Off unless `marketplace.enabled` |
| `Dockerfile` | The deployer image marketplace runs in the customer's cluster | **Layout verified, base image not.** `tools/marketplace/verify.sh` builds its COPY instructions and renders the chart from inside the result; the `FROM` line cannot be checked here — see below |

## What is actually verified

The join between the schema and the chart, which is the part that breaks
silently. `charts/igni` refuses to render without eight values, on
purpose; a form that cannot supply one of them produces a deploy that
fails with a message about a Helm value the customer never saw. Both
files look correct on their own, so
`tests/test_the_marketplace_listing_can_deploy.py` extracts the chart's
refusals from its own template source and matches them against what the
schema can answer — in both directions, because a form field that maps to
no chart value is worse: the customer sets it, the deploy succeeds, and
the answer is discarded.

Also checked: every secret parameter is masked or generated, the
encryption key is generated rather than typed into a browser (and shaped
as the Fernet key the chart will accept — there is no signing key to
supply, the server generates its own), both published images are declared, and the
Application object's selector actually matches the workloads.

## What building it found

The Dockerfile had never been built, and the first attempt failed:
`"/marketplace/schema.yaml": not found`, on a file that is plainly
there. The root `.dockerignore` excluded `marketplace`.

Neither change was a mistake. F1 added that ignore file to stop sending
a 1.3 GB context to the daemon on every build, and the API image does
not need this directory. This deployer builds from the repository root
because the chart is a sibling. A correct change made another artefact
false and nothing connected the two — and it survived because nothing
had ever run the thing it broke.

Fixed with a negation rather than a `marketplace/Dockerfile.dockerignore`,
which would have been more precise and would have reintroduced exactly
what `.dockerignore`'s own header warns about: the per-Dockerfile form
is BuildKit-only and is silently ignored without it.

`tests/test_dockerignore_keeps_what_is_copied.py` is the cheap half of
the guarantee and needs no daemon: every `COPY` source in every
Dockerfile must survive the ignore rules of the context it builds from.

## What the base image cannot tell us

`gcr.io/cloud-marketplace-tools/k8s/deployer_helm` is **not anonymously
pullable**. An unauthenticated pull is refused —
`artifactregistry.repositories.downloadArtifacts` — while an anonymous
pull of another `gcr.io` image in the same session succeeds, so this is
that repository specifically rather than a local network problem.

Which means item 4 below, checking the `0.13` tag, is itself blocked on
having a Google Cloud identity: you cannot list the tags to see what is
current. `verify.sh` therefore builds the same COPY layers onto a
stand-in base with helm in it, and checks everything the real base image
would care about — that the chart lands at `/data/chart/igni`, that the
schema lands at `/data/schema.yaml`, and that helm loads and renders
from those paths. It re-tests whether the real base has become reachable
on every run, and says so if it has.

What remains unverified is one line: `FROM`.

## What a person has to do

None of this is code, and none of it can be guessed:

1. **A Cloud Partner Advantage membership**, ISV track. Required before
   a listing can exist at all, and it has a review queue — start it
   before the artefacts are ready rather than after.
2. **A marketplace project** and a registered product, which is what
   produces the `partnerId` and `productId` the Application object
   carries.
3. **`mpdev` against a real cluster.** Google's tooling validates the
   schema, builds the deployer and runs an end-to-end install. The
   schema structure here is written from the v2 specification and has
   never been through it; expect corrections.
4. **The base image tag.** `deployer_helm:0.13` is what the
   documentation showed. Checking the current release needs an
   authenticated Google Cloud identity — the repository refuses
   anonymous reads, so the tag cannot be confirmed from here at all.
5. **The listing copy, the icon, the EULA and the support contact.** The
   EULA is drafted outside this repository, pending legal review, and has
   its own list of values only a person can supply.

## The decision worth revisiting before submission

**This listing requires an external PostgreSQL and Redis.** The chart
deploys neither, for reasons that are good engineering — a bundled
database is a second thing to patch, scan and back up, and a chart
dependency has to be fetched over a network an air-gapped install does
not have.

It is also a worse first-run experience than a marketplace customer
expects, because the form asks for a Cloud SQL address before anything
happens. Listings do this and are accepted; it costs conversions. The
alternatives are a bundled-Postgres option behind a parameter, or
Marketplace's own Cloud SQL integration if it fits. Recorded here rather
than decided, because it trades a support burden against a funnel and
that is not an engineering call.
