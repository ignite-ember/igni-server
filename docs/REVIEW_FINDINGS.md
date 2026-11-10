# Review findings

Before its first release, igni server went through a pre-production review.
Every check in it was **run**, with the output recorded, rather than read off
the code. Each defect it found got an id (`F5`, `F107`, …) and each part of the
review a section number (`§58`). Code comments, tests and the compliance
documents in this repository cite those ids.

The review log itself, with its command output, is kept by Ignite Ember and
is not published. This index is what the ids refer to: what each finding was,
where it stands, and the tests in this repository that cite it — usually
the ones written to catch it. Where none are listed, the evidence is the
code and the row that cites it in [COMPLIANCE_TRACKER.md](COMPLIANCE_TRACKER.md),
which describes it inline. A status of — means the review recorded the
finding without a one-line status.

`tests/test_compliance_tracker_is_current.py` checks that every id cited in
the compliance documents is listed here.

## Findings

| Id | Finding | Status | Guarded by |
|---|---|---|---|
| F1 | `black --check` failing — CI red | Fixed | `tests/test_compose_deployment.py`, `tests/test_decisions.py`, `tests/test_one_version_number.py`, `tests/test_the_helm_chart_deploys_igni.py`, `tests/test_the_policies_are_enforced.py` |
| F2 | `mypy` failing, 9 errors — CI red | Fixed | — |
| F3 | `downgrade base` could not be rolled forward (orphaned enum types) | Fixed | `tests/test_migration_round_trip.py` |
| F4 | `alembic/` was linted by nothing | Fixed | — |
| F5 | An unset `JWT_SECRET_KEY` signed *and accepted* forged tokens — full auth bypass | Fixed; the setting is gone — the server now generates its own signing key | `tests/test_compose_deployment.py`, `tests/test_the_policies_are_enforced.py` |
| F6 | `PORTAL_AUTH_BYPASS` had nothing stopping it reaching production | Fixed | — |
| F7 | CI ran no tests at all, for either repo | Fixed | `tests/test_the_policies_are_enforced.py` |
| F8 | Three public URLs defaulted to localhost, absent from the template | Fixed | — |
| F9 | `dist.env` did not describe a bootable server | Fixed | — |
| F10 | A dead endpoint returning a fake success | Removed | — |
| F11 | `python -m build` failed outright — no release artifact | Fixed | — |
| F12 | A group could ship three agents and deliver one | Fixed | — |
| F13 | A real-session check still asserted deleted behaviour | Fixed | — |
| F14 | Two portal tests failing — one a real jsdom gap | Fixed | — |
| F15 | The worker's `include` list was tested by nothing | Fixed | — |
| F16 | The read-only Cypher guard could be talked into a write via `apoc.cypher.run` | Fixed | — |
| F17 | An unset webhook secret skipped signature verification entirely | Fixed | — |
| F18 | Two group routes leaked another org's membership, emails included | Fixed | — |
| F19 | Repository reads were unscoped — including a signed URL to another org's **source** | Fixed | `tests/test_no_cross_org_access.py` |
| F20 | `/repositories/{id}/members` was a 500 for everybody | Fixed | — |
| F21 | `/health` said ok while the API could serve nothing; no healthchecks anywhere | Fixed | — |
| F22 | Flower unauthenticated on a public port, serving the broker URL | Fixed | `tests/test_compose_deployment.py` |
| F23 | A password pasted into the hash field gave a blind 500 | Fixed | `tests/test_sign_in_does_not_leak_account_existence.py` |
| F24 | An entry's content had no size limit — 32 MB accepted | Fixed | `tests/test_request_size_and_cost_bounds.py` |
| F25 | A group entry name became a path and escaped the cache, executable for `scripts` | Fixed | — |
| F26 | The file preview refused the config directory the session was using | Fixed | — |
| F27 | OIDC id_token algorithms unpinned (**not exploitable** — jose blocked both attacks) | Fixed (pinned; was not exploitable) | `tests/test_oidc_id_token_algorithms.py` |
| F28 | A six-digit sign-in code with no attempt limit and a non-cryptographic generator | Fixed | `tests/test_sign_in_does_not_leak_account_existence.py` |
| F29 | …which could also claim somebody else's pending admin invitation | Fixed | `tests/test_no_cross_org_access.py`, `tests/test_sign_in_does_not_leak_account_existence.py` |
| F30 | Two machine tokens with no length floor | Fixed | — |
| F31 | A new hire who starts igni too early keeps the wrong policy, permanently | Fixed | — |
| F32 | The pack's `Cache-Control` was dead code a middleware overwrote | Fixed | — |
| F33 | A membership cache that claimed ten minutes and cached nothing | Fixed | — |
| F34 | You could not delete your account if you had ever done admin work | Fixed | — |
| F35 | Sign-in codes written to the log by default, with nobody told | Fixed: off by default (`LOG_SIGN_IN_CODES`); while on, startup and every logged code say so, and super admins see the warning as a portal banner (§109) | `portal/src/components/layout/__tests__/DeploymentBanner.test.tsx`, `tests/test_deployment_notices.py`, `tests/test_email_delivery.py`, `tests/test_startup_checks.py` |
| F36 | A dead `access_token` field on the "send me a code" response | Removed | — |
| F37 | An expired GitLab token read as active on every gate | Fixed | — |
| F38 | A group could silently turn a developer's `--strict` into `allow` | Fixed | — |
| F39 | `--strict` claimed to "deny all dangerous operations"; group hooks run `bash` regardless | Fixed (in the igni client, 4471ce43): `hooks.group` (`run`, `ask` or `refuse`; default `run`). Only the developer's own config and command line set it, and the group pack, managed file and projects are ignored. `--strict` means at least `ask`, and headless `ask` refuses. Enforced where every hook is dispatched | `portal/src/lib/__tests__/entryKinds.test.ts`, `tests/test_entry_kinds.py` |
| F40 | Nothing had ever audited a dependency: 76 + 101 + 9 advisories, 66 of them in the shipped image | Fixed, CI gated | `tests/test_every_release_has_an_sbom.py` |
| F41 | Three dev-group pins were capping the production resolution, holding a `click` fix back | Fixed | — |
| F42 | `bandit` was a declared dev dep nothing ran; MD5 with no `usedforsecurity` 500s on FIPS | Fixed, CI gated | `portal/tests/e2e/accessibility.spec.ts`, `tests/test_bypass_identity_does_not_reach_production.py`, `tests/test_the_portal_lint_gate_exists.py` |
| F43 | numpy 2.5 made ember-code's mypy abort — zero files checked, exit non-zero | Fixed | — |
| F44 | Password sign-in 500d for every passwordless account, and the status code revealed who has one | Fixed | `tests/test_a_token_cannot_become_a_password.py`, `tests/test_anonymous_email_cannot_be_abused.py`, `tests/test_no_cross_org_access.py` |
| F45 | `POST /token`: a root-mounted login endpoint that 500d on every request, called by nothing | Removed | — |
| F46 | Webhook parse failures logged contributor emails and commit messages, then answered 500 | Fixed | — |
| F47 | Duplicate creates answered 500 — including a taken org slug, with no concurrency involved | Fixed | — |
| F48 | Inviting somebody twice created duplicate pending rows, and then their signup could not complete | Fixed | — |
| F49 | The shipped VS Code extension said "Ember" in both right-click menus | Fixed | — |
| F50 | Every `EMBER_*` environment variable, across five languages and a cross-process contract | Fixed | — |
| F51 | The prod compose pointed at an image nobody publishes, and nothing waited for migrations | Fixed | — |
| F52 | `make up` published an unauthenticated admin API and Flower on every interface | Fixed | `tests/test_bypass_identity_does_not_reach_production.py` |
| F53 | Every log line ended in a literal `.%f` instead of a fraction | Fixed | — |
| F54 | The code-index worker died on import from a stale `include` entry the extraction left behind | Fixed | — |
| F55 | A worker and Flower ran for a task nothing dispatched | Removed (DEC-13); recovering missed webhooks is CodeIndex's, where webhooks arrive — built there: a periodic repository reconcile (`igni__code-index` f47cdb5) | `tests/test_the_worker_is_gone.py` |
| F56 | No request body limit anywhere: 193 MB read and parsed, then rejected over the model name | Fixed | — |
| F57 | `n=100000` and `max_tokens=10000000` accepted and forwarded to the provider | Fixed | — |
| F58 | Cancelling a generation held a node concurrency slot for 15 minutes; 64 of them take the node out | Fixed | — |
| F59 | An upstream 200 with an unreadable body surfaced as a 500 rather than a 502 | Fixed | — |
| F60 | A migration written in this review could not be rolled back on a database in the state it exists for | Fixed | — |
| F61 | Running a migration disabled every `app.*` logger — the cause of a mystery worked around twice | Fixed | — |
| F62 | Seven FE specs gated off, including the only Python↔TypeScript wire-format check | Fixed, CI gated | — |
| F63 | A user-facing message printed a literal `{DEFAULT_DATA_DIR}` | Fixed | — |
| F64 | The CodeIndex cutover dropped its tables from a database it created by accident, and logged success | Fixed | — |
| F65 | A closed FE client resurrected itself and could take the backend single-client slot | Fixed | — |
| F66 | `theme.css` ended mid-rule; the next appended rule would silently never apply | Fixed | — |
| F67 | The portal e2e suite never ran in CI and had rotted 8 specs deep | Fixed, CI gated | `portal/src/test/primitives.test.ts`, `portal/tests/e2e/visual.spec.ts` |
| F68 | Playwright output committed to git (29 files); axe declared but never invoked | Fixed (E6: axe gates every portal page in CI; F137 fixed what it found) | `portal/tests/e2e/accessibility.spec.ts`, `tests/test_bypass_identity_does_not_reach_production.py` |
| F69 | A wrong `POSTGRES_DB` created an empty database and the whole stack booted green | Fixed | — |
| F70 | Flower promised persistence and kept none — no volume, no save interval | Fixed | — |
| F71 | The shipped image ran Python 3.12; pyproject and every CI job say 3.13 | Fixed | — |
| F72 | The README: 4 dead links, 3 phantom endpoints, a missing `make` target, "Ember Server" | Fixed | `tests/test_compliance_tracker_is_current.py` |
| F73 | `dist.env` copied as documented would not boot the local stack | Fixed | — |
| F74 | Nothing tested the commit CD built and pushed; and CD has no deploy job | Fixed / corrected | — |
| F75 | A plugin README could draw a full-screen overlay in the app and phone home | Fixed | `tests/test_nothing_phones_home.py` |
| F76 | The composer's contenteditable input had 2.7% coverage and no test file | Fixed (no defect found) | — |
| F77 | The igni client's `Composer.tsx` at 1.5% of 397 statements (coverage itself was unmeasurable — fixed) | Fixed (in the igni client, 086b3dd6): 46 tests mount the whole Composer against a stubbed client. Statement coverage 5.9% → 94.9%, branch coverage 3.8% → 85.8% | — |
| F78 | `igni --help` printed an implementation note and promised a `[COMMAND]` with none | Fixed | — |
| F79 | Five documents told users to run `ignite-ember /login`, which errors | Fixed | — |
| F80 | 25 strings still said "Ember"; F49 fixed instances, not the cause | Fixed | `tests/test_access_review.py`, `tests/test_anonymous_email_cannot_be_abused.py`, `tests/test_audit_trail.py` |
| F81 | Three JetBrains notification lookups used an id `plugin.xml` never registered | Fixed | — |
| F82 | The Cypher guard rejected read-only queries based on a quote's position | Fixed | — |
| F83 | Every accepted GitHub webhook logged its full payload — commit messages and author emails | Fixed | `tests/test_nothing_phones_home.py`, `tests/test_the_prompt_stays_out_of_the_logs.py` |
| F84 | Secret-encryption key rotation reached the shared plane only, missing `vm_credentials.pending_pickup` in every organisation's database | Fixed: the tool walks the shared database and every organisation's, detached included, and its column list is checked against both live schemas (§109) | `tests/test_encryption_key_rotation.py` |
| F85 | Deleting one organisation deleted GPU nodes the whole deployment was routing to | Fixed | — |
| F86 | Concurrent use of the test database produced fake regressions three times | Fixed | — |
| F87 | The dev auth bypass left a live admin account that survived into production | Fixed | — |
| F88 | The service contract described 60% of the boundary and one endpoint that never existed | Fixed | `tests/test_access_review.py`, `tests/test_audit_trail.py`, `tests/test_compliance_tracker_is_current.py`, `tests/test_statement_of_applicability.py` |
| F89 | Both published images ran as root, and nothing ever scanned them | Fixed | `tests/test_the_helm_chart_deploys_igni.py` |
| F90 | No audit trail existed at all — the largest compliance gap | Built | `tests/test_a_node_can_be_taken_out_of_rotation.py`, `tests/test_a_node_joining_the_fleet_is_recorded.py`, `tests/test_access_review.py`, `tests/test_anonymous_email_cannot_be_abused.py`, `tests/test_concurrent_creates_do_not_500.py`, `tests/test_retention_and_erasure.py`, `tests/test_subject_access.py`, `tests/test_the_audit_trail_stays_readable.py`, `tests/test_you_can_see_everything_yourself.py` |
| F91 | Compliance state lived only in narrative; there was no register to track it in | Built | — |
| F92 | No access review was possible — nothing recorded a sign-in (AC-8) | Built | — |
| F93 | Webhook payloads held contributor PII forever, unreachable by erasure (DP-6, DP-7) | Built | — |
| F94 | Nothing defended the air-gap claim; the CLI called PyPI with no off switch (DP-5) | Built | — |
| F95 | No SBOM shipped, and an image scan of the portal sees none of its 242 npm packages (CM-8) | Built | — |
| F96 | No way to answer a subject access request across 20 tables (DP-8) | Built | — |
| F97 | The server would not start outside Google Cloud, and the shipped template would not start anywhere | Fixed | `tests/test_a_deployment_outside_google_cloud_starts.py`, `tests/test_sso_is_usable.py` |
| F98 | No deployment artefact for a customer’s cluster; the chart that fixes it had three bugs that render perfectly | Built | `tests/test_the_helm_chart_deploys_igni.py` |
| F99 | The production compose stack had no portal, no datastores and no TLS | Built | — |
| F100 | Nothing backed the database up, in either deployment artefact | Built | `tests/test_compose_deployment.py`, `tests/test_the_helm_chart_deploys_igni.py` |
| F101 | Nothing to alert on beyond “the process is up”, and LG-6’s fail-open write had no rule | Built | `tests/test_lease_is_always_released.py`, `tests/test_monitoring_and_alerts.py`, `tests/test_you_can_see_everything_yourself.py` |
| F102 | Logs no shipper could parse, no dashboard, and no way to get the audit trail into a SIEM | Built | `tests/test_anonymous_email_cannot_be_abused.py`, `tests/test_compose_deployment.py`, `tests/test_monitoring_and_alerts.py`, `tests/test_the_prompt_stays_out_of_the_logs.py`, `tests/test_the_token_is_not_in_the_url.py`, `tests/test_you_can_see_everything_yourself.py` |
| F103 | SSO existed and was unreachable: no PKCE, no key rotation, no button (AC-9) | Fixed | `portal/src/components/layout/__tests__/Sidebar.compliance.test.tsx` |
| F104 | eslint ran nowhere, so 11 errors accumulated — three of them a reachable React crash | Fixed | `portal/src/auth/__tests__/LoginPage.exchange.test.tsx`, `tests/test_the_portal_lint_gate_exists.py` |
| F105 | Four admin tools had no interface, and the portal container would not start without the API | Built | `portal/src/pages/__tests__/CompliancePage.test.tsx`, `tests/test_compose_deployment.py`, `tests/test_the_token_is_not_in_the_url.py` |
| F106 | The encryption key could not be rotated, and the tool that rotates it was written in a gitignored directory | Built | — |
| F107 | Every sign-in handed a bearer token to the browser in a query string | Fixed | `portal/src/auth/__tests__/LoginPage.exchange.test.tsx`, `tests/test_a_token_cannot_become_a_password.py`, `tests/test_auth_oauth.py`, `tests/test_the_token_is_not_in_the_url.py` |
| F108 | No licence file, and both images declared themselves unlicensed | Built | — |
| F109 | No GCP listing artefacts, and nothing would have connected the form to the chart’s refusals | Built | `tests/test_the_marketplace_listing_can_deploy.py` |
| F110 | A session token could replace the account password and lock the owner out | Removed | `tests/test_a_token_cannot_become_a_password.py` |
| F111 | The live chat path logged the prompt, and F83’s guard against exactly that covered two directories | Fixed | `tests/test_nothing_blocks_the_event_loop.py`, `tests/test_the_prompt_stays_out_of_the_logs.py`, `tests/test_the_token_is_not_in_the_url.py` |
| F112 | Anonymous callers could enumerate accounts and make the deployment send unlimited mail | Fixed | `tests/test_a_node_joining_the_fleet_is_recorded.py`, `tests/test_a_personal_address_is_not_a_company.py`, `tests/test_anonymous_email_cannot_be_abused.py`, `tests/test_auth_signup.py`, `tests/test_email_delivery.py`, `tests/test_otp_hardening.py` |
| F113 | A public PII-collecting waitlist in every customer’s deployment, exported incompletely and impossible to erase | Fixed | `tests/test_a_node_joining_the_fleet_is_recorded.py`, `tests/test_subject_access.py` |
| F114 | Nothing recorded a node joining the fleet, and a node receives prompts | Fixed | `tests/test_a_deleted_user_cannot_come_back.py`, `tests/test_a_node_can_be_taken_out_of_rotation.py`, `tests/test_a_node_joining_the_fleet_is_recorded.py`, `tests/test_compliance_tracker_is_current.py`, `tests/test_lease_is_always_released.py`, `tests/test_the_token_is_not_in_the_url.py`, `tests/test_withdrawn_sessions_are_refused.py` |
| F115 | 152 of 184 columns in personal-data tables were unclassified; eight `included` columns were never exported, and one that names third parties was | Fixed | `tests/test_a_deleted_user_cannot_come_back.py`, `tests/test_nothing_blocks_the_event_loop.py`, `tests/test_records_of_processing.py`, `tests/test_subject_access.py`, `tests/test_the_audit_trail_stays_readable.py`, `tests/test_the_token_is_not_in_the_url.py`, `tests/test_withdrawn_sessions_are_refused.py` |
| F116 | Fourteen connection pools of five, so any one repository could run five concurrent queries against a worker built for 200 — and `/ready` held a private pool, so it never noticed | Fixed | `tests/test_nothing_blocks_the_event_loop.py`, `tests/test_the_audit_trail_stays_readable.py`, `tests/test_the_helm_chart_deploys_igni.py`, `tests/test_the_pool_is_one_pool.py` |
| F117 | No way to stop a GPU serving short of deleting it, and for a self-registered node no way at all | Fixed | — |
| F118 | Every OAuth sign-in blocked the whole API process for as long as the provider took — up to 35 seconds of serving nothing | Fixed | `tests/test_nothing_blocks_the_event_loop.py` |
| F119 | The API waits 300s for a model and Kubernetes gave it 30s to shut down, so every rolling upgrade killed long chat streams | Fixed | `portal/tests/e2e/page-shots.spec.ts`, `tests/test_the_helm_chart_deploys_igni.py` |
| F120 | An anonymous sign-up received the failing SQL, every column of `users` and the bound parameters, on a 400 | Fixed | `tests/test_an_error_says_nothing_it_should_not.py` |
| F121 | Answering a subject access request scanned the whole audit table — 344 ms at 2M rows, in the one table with no retention ceiling | Fixed | `portal/src/utils/__tests__/plural.test.ts`, `portal/src/utils/__tests__/selfHostedCopy.test.ts`, `tests/test_the_audit_trail_stays_readable.py` |
| F122 | Deleting an account did not end the session: the token kept working for 30 days, on chat and on the portal, where the first request provisioned a new org with the deleted user as admin | Fixed | `tests/test_a_deleted_user_cannot_come_back.py`, `tests/test_withdrawn_sessions_are_refused.py` |
| F123 | A config naming a model the registry lacks killed the desktop backend at boot, and the app discarded the reason | Fixed | — |
| F124 | The desktop sign-in put a working 30-day token in the browser address bar — the path F107 did not reach | Fixed | `tests/test_the_token_is_not_in_the_url.py` |
| F125 | A registered-but-unreachable GPU made non-streaming chat answer 500, where the streaming path retries on another node | Fixed | `tests/test_lease_is_always_released.py` |
| F126 | The desktop app contacted GitHub on every launch, ignoring the `update_check_ttl: 0` switch that exists to stop exactly that | Fixed | `portal/src/visualBaselineEnvironment.test.ts`, `tests/test_policies.py` |
| F127 | The desktop webview rendered model output with no Content-Security-Policy, and my first draft of one let a transcript be POSTed to any host | Fixed | — |
| F128 | The e2e suite reported "41 passed" while skipping every test that drives a real backend — four had never run anywhere, and the whole directory was never type-checked | Fixed | — |
| F129 | Running all 48 tests at once exposed two long-standing races: a slash command that lost its first keystrokes and sent a different command, and a scroll test asserting which rows were mounted rather than where the reader was | Fixed | — |
| F130 | The portal's browser tests read whatever auth mode the machine was in, so two passed in CI and failed on a developer's stack; CI also excluded the visual tests with a filter that produced no skipped test | Fixed | `portal/tests/e2e/anonymous.spec.ts`, `portal/tests/e2e/protected.spec.ts` |
| F131 | No page in the portal had an `h1` — thirty page titles rendered as `h2`, so every document outline started at the sidebar's `h3` with no title above it | Fixed | `portal/src/pages/__tests__/GroupDetailPage.test.tsx`, `portal/tests/e2e/headings.spec.ts` |
| F132 | Six defects on pages no test had ever looked at: a duplicated nav label, "1 members"/"1 people", three search boxes narrower than their placeholders, a footer breaking mid-phrase, an offer to contact support on a self-hosted product, and unsorted capability chips. Writing the fixes as rules found four more | Fixed | `portal/src/utils/__tests__/plural.test.ts`, `portal/src/utils/__tests__/selfHostedCopy.test.ts`, `portal/tests/e2e/polish.spec.ts` |
| F133 | **HIGH.** On any deployment without `PORTAL_AUTH_BYPASS`, an anonymous visitor was redirected off the landing page, `/signup` and `/docs` to `/login` — the public site and account creation were unreachable | Fixed | `portal/tests/e2e/anonymous.spec.ts` |
| F134 | The portal's browser suite started no web server when `CI` was set, so all 65 tests failed on a connection refused — the one environment variable nobody sets locally | Fixed | — |
| F135 | The desktop app hedged its counts — "1 message(s) above were interrupted", the sentence shown after a backend crash — and four more like it. My first fix turned one into "1 runs" | Fixed | — |
| F136 | The approval dialog rendered the requesting agent's path bare, so a single-agent run showed the word "main" under the heading attached to nothing | Fixed | — |
| F137 | The portal had 200 accessibility violations — an accessibility-testing dependency that nothing invoked, 135 nodes from one missing `<main>`, and a brand colour used as text at 2.4:1 | Fixed | `portal/tests/e2e/accessibility.spec.ts` |
| F138 | The desktop app had 154 in its dark theme and 52 more in light, including a running tool's output faded to 2.0:1 and one text token wrong in both themes | Fixed | — |
| F139 | The compliance argument the README links claimed five controls did not exist — retention, subject access, erasure, an SBOM and the air-gap test — all of which had been built. The register beside it was correct throughout, because the register is asserted by a test | Fixed | `tests/test_decisions.py`, `tests/test_records_of_processing.py`, `tests/test_statement_of_applicability.py`, `tests/test_the_docs_name_real_things.py`, `tests/test_the_policies_are_enforced.py`, `tests/test_the_worker_is_gone.py` |
| F140 | The bundled Caddyfile put `email` in the site block, so `caddy validate` rejected the whole file and the only TLS path the product documents exited on start — since the day it was written, because nothing ever parsed a deployment config | Fixed | — |
| F141 | `make lint` and CI linted different code | Fixed | — |
| F142 | Three rules were named after a service, not after a property | Fixed | `tests/test_compose_deployment.py` |
| F143 | The README understated what a customer must back up | Fixed | `tests/test_the_front_door_is_true.py` |
| F144 | `docs/ARCHITECTURE.md` described a system that does not exist | Fixed | `tests/test_the_portal_calls_real_routes.py` |
| F145 | Removing Flower deleted the portal's Service | Fixed | `tests/test_the_helm_chart_deploys_igni.py` |
| F146 | A removed service keeps running | Fixed where it can be: the upgrade command carries `--remove-orphans`, and a test keeps it there. Compose cannot stop a service its file no longer declares, so the host is the operator's | `tests/test_the_front_door_is_true.py` |
| F147 | The Progress table said 18 of 39; the goals said 35 of 41 | Fixed | — |
| F148 | `.dockerignore` made the deployer unbuildable | Fixed | `tests/test_dockerignore_keeps_what_is_copied.py` |
| F149 | Two policy clauses had gone stale | Fixed (`IR-P7` completed in §108) | `tests/test_policies.py` |
| F150 | GPU nodes' API keys stored in plaintext, beside the encrypted signing key | Fixed: encrypted under `SECRET_ENCRYPTION_KEY`, existing rows migrated, rotated with the rest (§109) | `tests/test_encryption_key_rotation.py`, `tests/test_migration_round_trip.py`, `tests/test_pinned_nodes.py` |
| F151 | Redis held sign-in codes, handoff tokens, OAuth codes and state, and registered nodes' API keys in plaintext | Fixed: high-entropy codes stored as their SHA-256, everything handed back sealed under `SECRET_ENCRYPTION_KEY`; every key and value in Redis scanned by a test (§109) | `tests/test_anonymous_email_cannot_be_abused.py`, `tests/test_encryption_key_rotation.py`, `tests/test_no_secret_is_in_redis_in_the_clear.py`, `tests/test_redis_keys_expire.py` |

## Review sections

| Section | What it covered |
|---|---|
| §1 | Server — build and static checks |
| §2 | Server — database and migrations |
| §3 | Server — configuration and secrets |
| §4 | Server — runtime |
| §5 | igni app — build and static checks |
| §6 | igni app — runtime |
| §7 | Clients |
| §8 | Server — authorization (tenant isolation) |
| §9 | Server — failure behaviour and operations |
| §10 | Server — privileged paths and input validation |
| §11 | Client file access and the OIDC login |
| §12 | Server — account authentication |
| §13 | Server — invitations and machine tokens |
| §14 | Server — group membership and the pack |
| §15 | Server — the live chat path |
| §16 | Server — account deletion |
| §17 | Server — where one-time codes go |
| §18 | Server — do any responses hand back a credential? |
| §19 | Server — the GitLab integration token lifecycle |
| §20 | App — can org policy weaken a developer's safety flags? |
| §21 | Both — dependency CVEs and static security analysis |
| §22 | Server — what ends up in the logs |
| §23 | Server — concurrency on the paths that create things |
| §24 | igni app — the shippable client artifacts |
| §25 | Server — does the production stack actually deploy? |
| §26 | Server — the Celery worker and what it actually runs |
| §27 | Server — what a single request may cost |
| §28 | Server — does a cancelled request give the capacity back? |
| §29 | Server — can a bad deploy be rolled back? |
| §30 | igni app — the frontend, and the tests that never ran |
| §31 | igni app — the frontend's connection lifecycle |
| §32 | Portal — the admin frontend's own test suite |
| §33 | Durability, and the front door |
| §34 | The igni FE app, finished |
| §35 | Installing the CLI, and the rename that did not hold |
| §36 | The Cypher guard — the last never-examined area |
| §37 | Secrets at rest, and a leak two guards were watching for |
| §38 | Cross-organisation authorisation, swept rather than sampled |
| §39 | The restore drill — and what it found in the restored database |
| §40 | The service contract, against both routers |
| §41 | Marketplace shape — GCP and AWS |
| §42 | The audit trail — built, not flagged |
| §43 | The access review — closing AC-8 |
| §44 | Retention and erasure — closing DP-6 and DP-7 |
| §45 | The air-gap claim, defended by a rule — closing DP-5 |
| §46 | The SBOM — closing CM-8, and what an image scan cannot see |
| §47 | Subject access — closing DP-8 |
| §48 | A deployment outside Google Cloud |
| §49 | A chart a customer can install |
| §50 | One instance, one command |
| §51 | Backups |
| §52 | Something to watch, and rules to watch it with |
| §53 | The means to look, on their side |
| §54 | SSO — auditing a build the tracker called a gap |
| §55 | The linter that ran nowhere |
| §56 | Four tools nobody could reach, and a portal that stopped booting |
| §57 | Rotating the key that protects everything else |
| §58 | The token stopped travelling in a URL |
| §59 | A licence, and the difference between a gap and a guess |
| §60 | The listing artefacts, and the join that breaks silently |
| §61 | What the review had never looked at |
| §62 | The live path — chat completions |
| §63 | The endpoints an anonymous caller can make work |
| §64 | The waitlist — somebody else's marketing list, in every deployment |
| §65 | Where the prompts go |
| §66 | Every column, and who said so |
| §67 | Fourteen pools nobody chose |
| §68 | Stopping a GPU without removing it |
| §69 | What happens when the far end is slow |
| §70 | What a failure tells the caller |
| §71 | Does it stay fast when there is data in it |
| §72 | Offboarding |
| §73 | Signing in from the app |
| §74 | A prompt from the app to a GPU |
| §75 | What the desktop app tells GitHub |
| §76 | What the desktop webview is allowed to do |
| §77 | What the e2e suite was not telling anyone |
| §78 | One command, and what it found |
| §79 | Putting a browser on the portal |
| §80 | Sixty-four pictures of the portal |
| §81 | Fixing what the pictures showed, and what they could not |
| §82 | Baselines that mean something |
| §83 | The desktop app's screens |
| §84 | What a baseline is actually pinned to |
| §85 | The accessibility pass |
| §86 | Auditing the register's own claims |
| §87 | Auditing the rows nobody owns |
| §88 | A record of processing, generated |
| §89 | Revocation, at last |
| §90 | The internal API nothing could reach |
| §91 | The surface nobody had looked at |
| §92 | The chart had never been installed |
| §93 | The backup script had never been run |
| §94 | A Statement of Applicability that cannot overclaim |
| §95 | A risk register that cannot invent anything |
| §96 | Policies where every sentence names what enforces it |
| §97 | An incident response plan that names real buttons |
| §98 | A DPIA that reaches an uncomfortable conclusion |
| §99 | An SLA needs a clock, and there was not one |
| §100 | The sub-processors there are not |
| §101 | The decision list had decided one of them already |
| §102 | The removal was the easy half |
| §103 | Re-verifying what "done" meant |
| §104 | The half of a EULA that is not a legal question |
| §105 | The deployer image had never been built |
| §106 | A syllabus derived from what the software does not enforce |
| §107 | The audit was already happening; the cycle was not |
| §108 | The index published every status, including the stale ones |
| §109 | Closing what §108 left fixable |
