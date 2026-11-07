# igni security pack

What a customer's security team receives before the first workshop, so it can
check igni's claims for itself rather than take them on trust. Every item is
a test that runs in CI, a document generated from the code and checked
against it, or a document that names the file behind each claim.

igni is self-hosted. The customer runs the server, the database and the
inference. Ignite Ember hosts nothing and has no route into the customer's
network.

## No DPA is needed

Ignite Ember never processes the customer's data. Under GDPR the customer is
the controller, and Ignite Ember is neither controller nor processor for that
data, so there is no data processing agreement to negotiate, no
international-transfer mechanism and no Article 28 processor obligations. The
reasoning is in [COMPLIANCE.md](COMPLIANCE.md), "The architectural advantage".

That position depends on igni never connecting out, which is why the egress
tests come first.

## Contents

| # | Item | What it shows | Where |
|---|---|---|---|
| 1 | Egress tests | Two tests. A source scan of the server and the client fails the build on any outbound host that is not on the allow-list, and on any vendor telemetry package. The OpenTelemetry OTLP exporters are allowed, by name: they send only to a collector you configure, there is no default endpoint, and the client refuses one outside the network when air-gapped ([observability.md](observability.md) §5). A runtime test boots the server, drives it through a working day against stand-ins on 127.0.0.1, and fails on any connection or name lookup outside the network. What neither can see is under Gaps. | [`tests/test_nothing_phones_home.py`](../tests/test_nothing_phones_home.py), [`tests/test_igni_reaches_nothing_outside_the_network.py`](../tests/test_igni_reaches_nothing_outside_the_network.py) |
| 2 | SBOM | Every component in each igni image (API, portal, Artifacts, embedder, CodeIndex) and the portal npm packages, in CycloneDX format, generated on each release. Each image SBOM is also a cosign attestation signed with the release key, and every SBOM is in the offline bundle. | `.github/workflows/cd.yml`; `sboms/` in the bundle; `cosign verify-attestation --type cyclonedx` |
| 2a | Signed releases and offline updates | Each release is one tarball: images, Helm chart, SBOMs and a MANIFEST of sha256 hashes signed with cosign. `igni-import` verifies it offline with your copy of the public key and refuses any mismatch before pushing to your registry. Images are published to `ghcr.io/ignite-ember/<name>` (release tags only in a bundle), signed with cosign (no transparency log), and keep their digests, so signatures verify in your registry. One release key signs the images and the bundle, so you hold one public key and need one tool. The key is in Cloud KMS (ECDSA P-256, in an HSM, not exportable) in Ignite Ember's Google Cloud project; CD signs through GitHub OIDC (Workload Identity Federation) from the private release repository's `prod` environment, and there is no signing secret in GitHub. The keys do not exist yet (Gaps). | [OFFLINE_UPDATES.md](OFFLINE_UPDATES.md), `tools/release/`, [`tests/test_an_offline_bundle_is_verified_before_import.py`](../tests/test_an_offline_bundle_is_verified_before_import.py) |
| 3 | Network diagram | Every component inside your network, every flow between them, and what crosses the boundary. | [NETWORK_DIAGRAM.md](NETWORK_DIAGRAM.md) |
| 4 | Exit-test runbook | How to rehearse a lapsed licence, an ended evaluation, Ignite Ember stopping, and leaving altogether, with the evidence to keep for a DORA exit plan. | [EXIT_TEST_RUNBOOK.md](EXIT_TEST_RUNBOOK.md) |
| 4a | Licence | How the paid modules are licensed with no call home: an offline signed file each module checks itself, what each module still does after a lapse or an ended evaluation, and what read-only allows. | [licence-file.md](licence-file.md) |
| 4b | Backup, restore and continuity | What the shipped backup covers, how a restore is proven, what it leaves out, and what the customer must keep outside the database. | [BACKUP_AND_CONTINUITY.md](BACKUP_AND_CONTINUITY.md) |
| 4c | Retention and deletion | What igni keeps, for how long, which setting changes it, and how a person, an organisation and a whole deployment are erased. | [DATA_RETENTION.md](DATA_RETENTION.md) |
| 4d | Support without access | What the customer sends when something is wrong, and what Ignite Ember never asks for. | [SUPPORT_WITHOUT_ACCESS.md](SUPPORT_WITHOUT_ACCESS.md) |
| 4e | The models | Where igni's models come from, their licence, and what runs where. | [MODEL_PROVENANCE.md](MODEL_PROVENANCE.md) |
| 5 | Compliance self-assessment | igni's technical controls mapped to ISO 27001, SOC 2 and GDPR, with the evidence for each. Not an audit. | [COMPLIANCE.md](COMPLIANCE.md), with the register in [COMPLIANCE_TRACKER.md](COMPLIANCE_TRACKER.md) |
| 6 | Statement of Applicability | ISO 27001 Annex A controls and how each is met. | [STATEMENT_OF_APPLICABILITY.md](STATEMENT_OF_APPLICABILITY.md) |
| 7 | Risk assessment | Risks and the treatment plan. | [RISK_ASSESSMENT.md](RISK_ASSESSMENT.md) |
| 8 | Vulnerability management | How vulnerabilities are found, rated and fixed. | [VULNERABILITY_MANAGEMENT.md](VULNERABILITY_MANAGEMENT.md) |
| 9 | Incident response plan | What happens when something goes wrong. | [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md) |
| 10 | DPIA, the vendor's half | Input for the customer's own data protection impact assessment. | [DPIA.md](DPIA.md) |
| 11 | Records of processing | The personal data igni stores, by column. | [RECORDS_OF_PROCESSING.md](RECORDS_OF_PROCESSING.md) |
| 12 | Suppliers | Who else touches the data. For a self-hosted install, no sub-processors. | [SUPPLIERS.md](SUPPLIERS.md) |
| 13 | Policies | Access control, cryptography, secure development and incident response. The secure development policy (SD-P1 to SD-P9) is igni's secure development lifecycle: each clause names the CI gate or test that enforces it, or says it is not enforced. | [POLICIES.md](POLICIES.md) |
| 14 | Reporting a security problem | Where to send a report (`dmytro.zezyk@ignite-ember.sh`, or GitHub's private vulnerability reporting), what is in scope, the response times and the disclosure terms, and where fixed vulnerabilities are published. | [SECURITY.md](../SECURITY.md) |

Items 6 to 13 are generated from the code by `tools/generate_*.py`, and a test
fails if a committed file and its generator disagree.

## Where igni stands on certification

igni is starting on the path to ISO 27001, SOC 2 Type I and Type II, and GDPR
compliance. It holds no certificate or attestation report yet. GDPR is law,
not a certificate. [COMPLIANCE.md](COMPLIANCE.md) explains what each one
requires.

## Penetration testing

No independent penetration test of igni has been done, and there is no
report to share. What exists is the internal adversarial review, indexed
in [REVIEW_FINDINGS.md](REVIEW_FINDINGS.md): each finding in it was found by running a request
or an attack against the running system, and each fix came with a test that
failed without it. That is not a substitute for an independent test. A
customer may test its own deployment; [SECURITY.md](../SECURITY.md) says what
is in scope.

## Gaps

Known gaps, stated so the customer does not find them first.

- **Default-on outbound calls in the client.** On an unconfigured install the
  client checks `pypi.org` for updates, the desktop app asks `github.com` for
  updates, a plugin marketplace is cloned from `github.com`, and the agent's
  web tools are allowed. None of these goes to an Ignite Ember host. Each has
  its own switch, and `air_gapped: true` turns all of them off at once. It
  also refuses `igni ext install`, MCP servers reached by URL and HTTP hooks
  to any host outside the network, and a project's config cannot switch it
  off. What the agent runs as a program (shell commands, local MCP
  processes, command hooks) is limited by the customer's egress rules, not
  by igni. The
  marketplace clone's own switch is `plugins.default_marketplace: false`. An
  organisation can set `air_gapped: true` in a managed-settings file that a
  user cannot override. Fleet VMs have internet egress unless the node agent
  sets `air_gapped = true`. [NETWORK_DIAGRAM.md](NETWORK_DIAGRAM.md),
  section 3, lists each flow and its switch.
- **What the egress tests cannot see.** The source scan reads source and
  dependency files only. The runtime test runs the server on the standard
  asyncio loop, because its guard cannot observe uvloop, which production
  uses; it does not see native-code networking or what a child process does
  after it starts. The client, CodeIndex, Artifacts and Fleet each have their
  own source scan and runtime test, and each runs them in CI; Fleet's
  gateway tests run there too, but its tests on a real KVM host still run
  only when somebody runs them
  ([NETWORK_DIAGRAM.md](NETWORK_DIAGRAM.md), Gaps, item 2). The inference
  server is the customer's own, and switching off its outbound calls is the
  customer's job ([MODEL_PROVENANCE.md](MODEL_PROVENANCE.md)).
- **No release has been signed yet.** Signing, the offline bundle and the
  import tool are in place, and the release key exists (2026-11-07): a Cloud
  KMS key that cannot be exported, which only the private release
  repository's `prod` environment can sign with
  ([OFFLINE_UPDATES.md](OFFLINE_UPDATES.md), "The release key"). The first
  release has not been run, so no bundle has been produced. The public key
  and its fingerprint are sent out of band.
- **The bundle holds igni's own images only.** Third-party images the
  deployment uses (`postgres` for the chart's backup job; `postgres`,
  `redis` and `caddy` in the compose files) are not in it. Fleet ships
  binaries, not images; the bundle can carry its signed release
  ([OFFLINE_UPDATES.md](OFFLINE_UPDATES.md)), but Fleet's CD does not yet
  publish it where the bundle job picks it up, so it is not in a bundle
  yet.
- **Releases are kept on one provider.** Each release bundle is kept on
  GitHub Container Registry beside its images, written once and never
  deleted, so every security update stays available for ten years
  ([OFFLINE_UPDATES.md](OFFLINE_UPDATES.md), "How long releases are
  kept"). There is no second copy outside GitHub yet, and no bundle has
  been kept until the first release.
- **No commercial licence has been issued yet.** Each paid module checks
  an offline, Ed25519-signed licence file itself. A lapsed paid licence warns
  for 90 days and then goes read-only, never off; an ended evaluation is
  read-only for 30 days and then stops ([licence-file.md](licence-file.md)).
  Ignite Ember's licence key (`ie-2026`, in Cloud KMS, created 2026-11-07)
  is in the code, so a release built from it onwards accepts licences it
  signs; earlier releases accept none. The check uses the deployment's
  own clock, and a Fleet node's own timed wakes are not yet refused in
  read-only.
- **The EULA does not yet match the commercial terms** on what is proprietary
  and what happens at expiry. The modules' `LICENSE` text is proposed. Both
  await legal review ([runbook, G4](EXIT_TEST_RUNBOOK.md#6-gaps)).
- **No escrow agreement.** No escrow agent is chosen and no agreement is
  signed, so nothing has been deposited with anyone. The deposit and
  verification tooling exists and has been dry-run
  ([runbook, G7](EXIT_TEST_RUNBOOK.md#6-gaps)).
- **No factory delivery code.** How a domain model is handed over is a
  contract term the code does not yet support
  ([runbook, G9](EXIT_TEST_RUNBOOK.md#6-gaps)).
- **Backups do not cover the paid modules, Fleet or Neo4j.** The shipped
  backup dumps igni server's databases only
  ([BACKUP_AND_CONTINUITY.md](BACKUP_AND_CONTINUITY.md)).
- **No independent penetration test** (above).
- **SAML sign-in is not built.** It is coming next. OIDC, SCIM, roles and the
  audit trail exist. igni does not implement multi-factor authentication
  itself; it relies on the customer's identity provider
  ([POLICIES.md](POLICIES.md), AC-P10).
- **No PGP key for reports.** [SECURITY.md](../SECURITY.md) names the
  address (`dmytro.zezyk@ignite-ember.sh`) and GitHub's private
  vulnerability reporting, acknowledges within 3 business days, aims for a
  first assessment within 10, and discloses after 90 days or the fix,
  whichever is first. No PGP key is published yet; a reporter who needs one
  asks for a channel first.
