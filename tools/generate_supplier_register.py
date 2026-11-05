"""Generate `docs/SUPPLIERS.md` — suppliers, and the sub-processors there are not.

C7, OR-4. SOC 2 CC3 and GDPR Article 28 want to know who else touches
the data.

**The answer is nobody, and saying that with evidence is the whole
deliverable.** The software runs in the customer's infrastructure, no
data reaches the vendor, so the vendor is not a processor and has no
sub-processors for customer data. A register that listed some would be
inventing them.

But "no sub-processors" is three different claims wearing one sentence,
and separating them is what makes this document worth reading:

* **suppliers**, in the supply-chain sense — base images, package
  registries, the CI that builds the artefacts. They never see customer
  data; they affect what the customer installs. This is A 5.21 and
  CM-8's territory, not Article 28's;
* **recipients a deployment may acquire** — the model endpoint, an SMTP
  relay, a monitoring system. None receives anything
  by default and each is configured by the customer, which makes them
  the *customer's* processors to assess. C5 established the one that
  matters;
* **the vendor**, which receives nothing.

The supplier list is **derived from the Dockerfiles and compose files**,
and the generator refuses to render if an image is pinned somewhere and
missing here. A supplier register that goes stale on the next base-image
bump is a supplier register that lies about the supply chain, which is
the one thing it exists to describe.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_TRACKER = _ROOT / 'docs/COMPLIANCE_TRACKER.md'
_AI_SCHEMAS = _ROOT / 'app/services/ai/schemas.py'
_OUTPUT = _ROOT / 'docs/SUPPLIERS.md'

PLACEHOLDER = '`__ORGANISATION_TO_COMPLETE__`'

#: Files that pin a container image. Anything pinned in one of these and
#: absent from `SUPPLIERS` fails the render.
IMAGE_SOURCES = (
    'containers/be/Dockerfile',
    'portal/Dockerfile',
    'marketplace/Dockerfile',
    'containers/docker-compose.prod.yml',
    'containers/docker-compose.local.yml',
    'charts/igni/values.yaml',
    'tools/kind/datastores.yaml',
)


@dataclass(frozen=True)
class Supplier:
    #: The image reference or registry, exactly as pinned.
    ref: str
    what: str
    #: Why this is in the artefact at all.
    role: str
    #: What it would cost if this supplier shipped something malicious or
    #: simply broke. Stated because "we have a supplier list" is not a
    #: risk assessment.
    exposure: str


SUPPLIERS: tuple[Supplier, ...] = (
    Supplier(
        'python:3.13-slim',
        'Base image for the API and worker',
        'Both stages of `containers/be/Dockerfile`. Slim rather than alpine because the wheels '
        'this project installs are built against glibc.',
        'The largest single supply-chain exposure in the product: it is the OS under the process '
        'that holds the database credentials and the encryption key. Scanned on every pull '
        'request and daily against the default branch (V-4, V-7), and the scheduled scan gates.',
    ),
    Supplier(
        'node:26-alpine',
        'Build stage for the portal bundle',
        'Compiles the React application in `portal/Dockerfile`. Discarded afterwards — a '
        'multi-stage build, so node is not in the shipped image.',
        'Build-time only, which bounds it: a compromise here could alter the bundle, which is '
        'why the bundle is built in CI from a pinned lockfile rather than uploaded by anybody. '
        'It cannot reach a running deployment directly.',
    ),
    Supplier(
        'nginxinc/nginx-unprivileged:alpine',
        'Runtime image for the portal',
        'Serves the compiled bundle. The unprivileged variant because CM-4 requires a non-root '
        'container and this is the image that satisfies it rather than a `USER` line bolted on.',
        'Serves static files and proxies `/v1` to the API. It holds no secret and terminates no '
        'TLS in the shipped configuration, so the exposure is defacement or a redirect rather '
        'than data.',
    ),
    Supplier(
        'nginx:1.27-alpine',
        "The local edge for igni's own host in the compose stack",
        'Fronts the API on :7777 in `containers/docker-compose.local.yml`, as the first rule of '
        "the chart's ingress does. It routes to no other service: igni__artifacts has its own "
        'name, as every service has its own host in production.',
        "Local development only: the chart routes through the cluster's ingress and never "
        "ships this image. It holds no secret; a compromise would misroute a developer's own "
        'traffic.',
    ),
    Supplier(
        'gcr.io/cloud-marketplace-tools/k8s/deployer_helm:0.13',
        'Google Cloud Marketplace deployer',
        'Required by the marketplace listing format. Used only when a customer installs through '
        'GCP Marketplace, and never at runtime.',
        "Install-time only, and it is Google's own image — a customer installing from their "
        'marketplace has already accepted that trust relationship. It is here because a register '
        'that omitted it would be describing a supply chain the marketplace path does not have.',
    ),
    Supplier(
        'postgres:17.6-alpine',
        'The database, in the shipped compose stack and the backup job',
        'The chart deploys **no** database — `values.yaml` gives three reasons — so this pin is '
        "the compose stack's bundled profile and the CronJob's `pg_dump`, which uses the "
        "server's own image so the versions match by construction.",
        "Holds everything. But under Kubernetes this is the customer's own instance, managed or "
        'otherwise, and the pin only matters to the compose path and the backup job.',
    ),
    Supplier(
        'redis:7.4-alpine',
        'Concurrency leases and one-time sign-in codes in the shipped compose stack',
        'Same position as Postgres: external by design under the chart, pinned here for the compose profile.',
        'Holds one-time codes and OAuth state — short-lived, and the auth handoff was moved off '
        'a URL and into it precisely so a credential is not in a browser history (F107).',
    ),
    Supplier(
        'PyPI',
        'Every Python dependency',
        'Resolved and pinned by `uv.lock`, installed from a generated `requirements.txt` in the '
        'builder stage. The lock is what `pip-audit` reads, which is why the audit and the '
        'artefact cannot disagree.',
        'A malicious release cannot enter a build without the lockfile changing, which is a '
        'reviewable diff. `pip-audit` gates on pull requests and daily (V-1, V-6).',
    ),
    Supplier(
        'npm',
        'Every portal dependency',
        'Pinned by `portal/package-lock.json`. Compiled into the bundle, which is why the image '
        'scan cannot see them and the SBOM is produced separately from the filesystem (V-8).',
        '`npm audit --omit=dev` gates, and a dev-only package is not in the artefact a customer '
        "runs. The Playwright baselines are pinned to the lockfile's version rather than "
        "package.json's range, after a mismatch failed 84 tests at once (F135).",
    ),
    Supplier(
        'GitHub Actions',
        'Builds and publishes the artefacts',
        'CI and CD. Actions are pinned to a major version or a release tag rather than a moving '
        'reference. Release images are published to its container registry, '
        '`ghcr.io/ignite-ember`, from which the offline bundle is built.',
        'A compromise of the build system is a compromise of every artefact, which no amount of '
        'scanning inside the artefact detects. The mitigations that exist are the SBOM per '
        'release (CM-8), image tags that name a commit, and CD that cannot build a commit CI has '
        'not passed. This is the supplier a customer can least verify and it belongs at the top '
        'of any honest assessment.',
    ),
)


@dataclass(frozen=True)
class Recipient:
    ref: str
    name: str
    #: What reaches it, and whether that is new disclosure.
    reaches: str
    #: Configured where — the setting, so a reader can go and look.
    configured_by: str
    #: Whether anything flows by default.
    default: str
    #: What the customer has to do about it.
    obligation: str


RECIPIENTS: tuple[Recipient, ...] = (
    Recipient(
        'R-A',
        'The model endpoint',
        "**Prompts — the developer's source code and their words.** This is the one that would change a DPIA.",
        '`InferenceBackend.base_url`, which validates only that the URL is absolute. The product '
        "is built for the customer's own vLLM nodes and the whole inference stack is about GPUs "
        'they run.',
        'Nothing. There is no default endpoint; an unconfigured deployment serves a '
        '`NoModelConfigured` placeholder rather than calling anywhere.',
        '**A customer who points this at a third-party API acquires a processor** and needs an '
        'Article 28 agreement, a recipient entry in their own Article 30 record, and probably a '
        'transfer assessment. A customer running their own nodes acquires nothing. Same '
        'software, two different compliance positions, decided by one configuration value.',
    ),
    Recipient(
        'R-B',
        'An SMTP relay or SES',
        'Notification emails: an address, a repository name, a branch and a commit message. The '
        'commit message is free text, so this is the one flow where the content is whatever '
        'somebody typed.',
        '`smtp_host` and friends, or `email_backend=ses`. Unset means undeliverable notices are '
        'logged and nothing is sent — a deployment with no relay still works, which was not true '
        'until the invite flow was fixed to hand back the claim link.',
        'Nothing without a relay configured.',
        'A processor for the customer if it is a hosted relay. The volume is small and the '
        'content is bounded, but a commit message is personal data belonging to whoever wrote '
        'it.',
    ),
    Recipient(
        'R-C',
        'A Prometheus scrape and a log shipper',
        'Metrics with **allowlisted labels**, so a label cannot carry personal data by '
        'construction; and logs, which carry no personal data by rule — an AST check over the '
        'whole application, added after commit messages and whole prompts were found in them.',
        '`/metrics` behind a bearer token, and `LOG_FORMAT=json` for shippable logs.',
        'Nothing. `/metrics` answers 404 without a token configured.',
        'Usually inside the customer\'s own boundary. Worth an entry because "we ship logs to a '
        'SaaS" is a common answer and it changes who holds them.',
    ),
    Recipient(
        'R-D',
        'The vendor',
        '**Nothing.** No telemetry, no online licence check (the licence is an offline signed '
        'file), no error reporting. The client\'s update checks go to pypi.org and github.com, '
        'not to a vendor host, and `air_gapped: true` turns them off.',
        'Not configurable, because there is nothing to configure. DP-5, defended by a source '
        'scan and a runtime test in each repository after two outbound calls were found — the '
        'CLI checking PyPI and the desktop updater calling github.com on every launch, both now '
        'behind a switch.',
        'Nothing, and there is no setting that would change it.',
        'None. This is why there is no Article 28 agreement to sign with the vendor, and why '
        'this register has no sub-processors to declare.',
    ),
    Recipient(
        'R-E',
        'An OpenTelemetry collector',
        'Traces: request routes and statuses, parameterised SQL, outgoing hosts; from the client, '
        'agent and tool names, model ids, token counts, outcomes, session and producer ids. No '
        'request body, query string, caller address, prompt, response, file content or tool '
        "argument — unless the client's organisation sets `capture_content: true`, when prompts "
        'and code are in its spans.',
        "`OTEL_EXPORTER_OTLP_ENDPOINT` on the server; `telemetry.otlp` in the client's managed "
        "settings or group policy, or the same variables. Never a user's or a repository's "
        'config. Under `air_gapped: true` the client refuses a collector outside the network.',
        'Nothing. There is no default endpoint; without one no provider is built.',
        "Usually inside the customer's own boundary, like R-C. A hosted tracing service is a "
        "processor; with `capture_content: true` it holds source code, and R-A's reasoning applies.",
    ),
)


def pinned_images() -> dict[str, list[str]]:
    """Every container image pinned anywhere, with where it was found.

    Parsed rather than listed, so a base-image bump or a new service
    shows up here instead of quietly leaving the register describing an
    older supply chain.
    """
    found: dict[str, list[str]] = {}
    for relative in IMAGE_SOURCES:
        path = _ROOT / relative
        if not path.exists():
            continue
        text = path.read_text()
        references = re.findall(r'^FROM\s+(\S+)', text, re.M)
        references += re.findall(r'^\s*image:\s*["\']?([a-z0-9][\w./-]*:[\w.-]+)["\']?\s*$', text, re.M)
        for reference in references:
            if reference.upper() == 'SCRATCH':
                continue
            sources = found.setdefault(reference, [])
            # Deduplicated: a multi-stage Dockerfile names its base twice
            # and the prod compose file names Postgres once per service,
            # which rendered as "Dockerfile, Dockerfile".
            if relative not in sources:
                sources.append(relative)
    return found


def undeclared_images() -> list[str]:
    """Images the build pins and the register does not name.

    The completeness direction, and the one that matters: a supplier
    added to a Dockerfile without a register entry is exactly how a
    supply chain drifts away from its description.
    """
    declared = {s.ref for s in SUPPLIERS}
    return sorted(
        f'{reference} (in {", ".join(sources)})'
        for reference, sources in pinned_images().items()
        if reference not in declared
    )


def stale_declarations() -> list[str]:
    """Register entries naming an image nothing pins any more.

    A supplier that has been removed but is still declared makes the
    register look more carefully maintained than it is.
    """
    pinned = set(pinned_images())
    registries = {'PyPI', 'npm', 'GitHub Actions'}
    return sorted(s.ref for s in SUPPLIERS if s.ref not in pinned and s.ref not in registries)


def register_rows() -> dict[str, str]:
    rows: dict[str, str] = {}
    for match in re.finditer(
        r'^\|\s*([A-Z]{2}-\d+)\s*\|([^|]*)\|([^|]*)\|\s*`?([A-Za-z]+)`?\s*\|', _TRACKER.read_text(), re.M
    ):
        rows[match.group(1)] = match.group(4)
    return rows


def model_endpoint_is_unrestricted() -> bool:
    """R-A says a customer can point inference anywhere. Keep it true."""
    source = _AI_SCHEMAS.read_text()
    if 'base_url must start with http:// or https://' not in source:
        return False
    return not any(marker in source for marker in ('is_private', 'ip_address(', 'loopback'))


def render() -> str:
    statuses = register_rows()
    pinned = pinned_images()

    out: list[str] = []
    out.append('# Suppliers, and the sub-processors there are not')
    out.append('')
    out.append('**GENERATED by tools/generate_supplier_register.py — do not edit.**')
    out.append('Regenerate with `make docs`; `tests/test_supplier_register.py` fails if the')
    out.append('committed file and the generator disagree.')
    out.append('')
    out.append('C7, OR-4. SOC 2 CC3 and GDPR Article 28 ask who else touches the data.')
    out.append('')
    out.append('## The short answer, and why it needs three sections')
    out.append('')
    out.append('**The vendor has no sub-processors for customer data, because the vendor is not a')
    out.append("processor.** The software runs in the customer's infrastructure and nothing")
    out.append('reaches us — DP-5, defended by rules in each repository after two outbound calls')
    out.append('were found and removed. There is no Article 28 agreement to sign with us because')
    out.append('there is no processing to contract for.')
    out.append('')
    out.append('That sentence is true and it is also three different claims, so this document')
    out.append('separates them:')
    out.append('')
    out.append('| | | Sees customer data? |')
    out.append('|---|---|---|')
    for category, examples, sees in (
        (
            '**Suppliers**',
            'Base images, package registries, the build system',
            '**No.** They decide what the customer installs, not what happens to their data',
        ),
        (
            '**Recipients a deployment may acquire**',
            'The model endpoint, an SMTP relay, monitoring',
            "**Only if the customer configures them** — and then they are the customer's processors",
        ),
        ('**The vendor**', '—', '**No.** Nothing, by any path'),
    ):
        out.append(f'| {category} | {examples} | {sees} |')
    out.append('')
    out.append('Conflating the three is how a supplier register becomes either alarming or')
    out.append('useless: a list that treats a base image as a data processor is noise, and one')
    out.append("that omits the model endpoint has missed the only entry that changes a customer's")
    out.append('obligations.')
    out.append('')
    out.append('| Field | Value |')
    out.append('|---|---|')
    for name in (
        'Organisation maintaining this register',
        'Owner',
        'Review interval',
        'Approved by',
        'Date of approval',
    ):
        out.append(f'| {name} | {PLACEHOLDER} |')
    out.append('')

    # ── Suppliers ────────────────────────────────────────────────────
    out.append('## Suppliers — what the customer installs')
    out.append('')
    out.append('Derived from the Dockerfiles and compose files rather than typed, so a base-image')
    out.append('bump or a new service appears here instead of leaving this page describing an')
    out.append('older supply chain. An image pinned anywhere and missing from this table fails')
    out.append('the build.')
    out.append('')
    out.append('None of them sees customer data. Each is listed with what it would cost if it')
    out.append('shipped something malicious, because a list of names is not an assessment.')
    out.append('')
    out.append('| Supplier | What it is | Pinned in |')
    out.append('|---|---|---|')
    for supplier in SUPPLIERS:
        where = ', '.join(f'`{p}`' for p in pinned.get(supplier.ref, [])) or 'a registry, not an image'
        out.append(f'| `{supplier.ref}` | {supplier.what} | {where} |')
    out.append('')
    for supplier in SUPPLIERS:
        out.append(f'### `{supplier.ref}`')
        out.append('')
        out.append(supplier.role)
        out.append('')
        out.append(f'**Exposure.** {supplier.exposure}')
        out.append('')
    out.append('The scanning that watches them is `docs/VULNERABILITY_MANAGEMENT.md`; the')
    out.append(f'inventory a customer can read is the SBOM per release (`CM-8`, {statuses.get("CM-8", "?")}).')
    out.append('')

    # ── Recipients ───────────────────────────────────────────────────
    out.append('## Recipients a deployment may acquire')
    out.append('')
    out.append('**Nothing here receives anything by default.** Each is a destination the customer')
    out.append('configures, and configuring it is what creates the relationship — which means')
    out.append("these are the customer's processors to assess, not sub-processors of ours.")
    out.append('')
    out.append('| | Recipient | By default |')
    out.append('|---|---|---|')
    for recipient in RECIPIENTS:
        out.append(f'| {recipient.ref} | {recipient.name} | {recipient.default} |')
    out.append('')
    for recipient in RECIPIENTS:
        out.append(f'### {recipient.ref} — {recipient.name}')
        out.append('')
        out.append(f'**What reaches it.** {recipient.reaches}')
        out.append('')
        out.append(f'**Configured by.** {recipient.configured_by}')
        out.append('')
        out.append(f'**What the customer has to do.** {recipient.obligation}')
        out.append('')

    out.append('## A fact a customer should check for themselves')
    out.append('')
    out.append('**The model endpoint accepts any absolute URL.** That is the single value that')
    out.append('decides whether this deployment has a processor to paper. A test keeps this')
    out.append('sentence true against the code, so if somebody ever restricts the URL to private')
    out.append('ranges the claim fails rather than quietly becoming wrong.')
    out.append('')
    out.append('## What this register does not do')
    out.append('')
    out.append("**Assess the suppliers on the customer's behalf.** Whether `python:3.13-slim` is")
    out.append('an acceptable dependency is a judgement about a threat model this document does')
    out.append('not know. What it provides is the list, pinned versions, and where each one sits')
    out.append('in the artefact.')
    out.append('')
    out.append("**Cover the vendor's own suppliers.** The company that builds this software has")
    out.append('its own — an email provider, a cloud account, whatever it runs its business on —')
    out.append("and none of them touches a customer's deployment. They belong in the")
    out.append("organisation's own vendor register, which is `OR-4` and stays `Process`.")
    out.append('')
    out.append('**Speak for the sibling repositories.** `igni`, `igni__code-index`,')
    out.append('`igni__artifacts` and `igni__cloudvm` ship their own artefacts with their own base')
    out.append('images; the SBOMs of the CodeIndex and Artifacts images list theirs. Porting this')
    out.append('register is a smaller job than describing them from here would be.')
    out.append('')
    out.append("**Cover the models.** igni's models are tuned from open-weight Qwen base models;")
    out.append('where they come from and under what licence is in `docs/MODEL_PROVENANCE.md`.')
    out.append('')
    return '\n'.join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='exit non-zero if the file is stale')
    args = parser.parse_args()

    for label, offenders in (
        ('are pinned in the build and missing from the register', undeclared_images()),
        ('are declared and pinned nowhere any more', stale_declarations()),
    ):
        if offenders:
            print(f'these images {label}:', file=sys.stderr)
            for line in offenders:
                print(f'  {line}', file=sys.stderr)
            return 2

    if not model_endpoint_is_unrestricted():
        print(
            'R-A says a customer can point inference at any host, and `InferenceBackend.base_url` '
            'appears to be restricted now — so the register is wrong about the one entry that '
            "changes a customer's obligations.",
            file=sys.stderr,
        )
        return 2

    rendered = render()
    if args.check:
        if not _OUTPUT.exists() or _OUTPUT.read_text() != rendered:
            print(f'{_OUTPUT} is stale; regenerate it', file=sys.stderr)
            return 1
        print(f'{_OUTPUT} is current')
        return 0

    _OUTPUT.write_text(rendered)
    print(
        f'wrote {_OUTPUT} ({len(rendered.splitlines())} lines, '
        f'{len(SUPPLIERS)} suppliers, {len(RECIPIENTS)} recipients)'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
