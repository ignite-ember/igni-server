# Reporting a security problem

Ignite Ember maintains igni server. This file is where to send a security
report, what to put in it, and what happens next. It is at the
repository root because that is where GitHub looks for it and where
somebody who has found something looks first.

## Where to send it

| | |
|---|---|
| Address | `dmytro.zezyk@ignite-ember.sh`, or GitHub's private vulnerability reporting on this repository ("Report a vulnerability" under the Security tab) |
| PGP key | A PGP key is not yet published. If a report must not travel in plain email, say so in a first message without the details, and we will agree a channel. |
| Acknowledgement | We acknowledge every report within **3 business days** (Europe/Amsterdam). |
| Assessment | We aim for a first assessment — whether we can reproduce it, how severe we think it is, and what we plan to do — within **10 business days**. Fix times then follow the targets in `docs/VULNERABILITY_MANAGEMENT.md`. |
| Disclosure | **Coordinated.** Details are disclosed **90 days after your report or when the fix ships, whichever is first** — later only if you agree to it. We ask you to keep them private until then, credit you in the advisory if you want to be credited, and if we need longer we ask you, and say why. |
| Safe harbour | If you act in good faith — you test only against your own installation or one you are authorised to test, avoid privacy violations, data destruction and service disruption, do not use more access than you need to show the problem, and give us a reasonable time to fix it before disclosure — we will not pursue or support legal action against you for your research, and we will say so publicly if anybody else does. |

We are a small vendor. The acknowledgement time is a commitment and the
assessment time is a target we hold ourselves to; if either slips, we
tell you rather than going quiet.

What follows is a property of the software rather than of a company.

## Advisories

A fixed vulnerability is published as a **GitHub Security Advisory on
`ignite-ember/igni-server`** (the Security tab, then Advisories), with a CVE
identifier assigned through GitHub, which is a CVE Numbering Authority.
The advisory is published once the fixed release is available, and names
that version; the release notes and `CHANGELOG.md` name the advisory.
Watching the repository for security alerts is how to hear of one;
customers under contract are also told directly. How a report becomes an
advisory, and how that lines up with the Cyber Resilience Act's reporting
duties, is in `docs/VULNERABILITY_MANAGEMENT.md` ("Advisories and CVE
identifiers").

## Please do not open a public issue first

Not because disclosure is unwelcome — because this product is
**self-hosted**. Every customer runs their own deployment and upgrades on
their own schedule, so a public report is exploitable against every
installation that has not upgraded yet, and there is no vendor-operated
service anybody can patch centrally to protect them.

That asymmetry is the whole reason for a private channel here. It also
means a fix is only half of a remediation: the other half is customers
knowing they need to deploy it, which is what the advisory in
`docs/VULNERABILITY_MANAGEMENT.md` is for.

## What helps

* **the version.** `GET /version` returns it, and it is the same number
  in `pyproject.toml`, `app/config.py` and the chart's `appVersion` —
  three files a test holds together, because they once disagreed;
* **whether you are looking at a deployment or the source.** A finding in
  a customer's configuration and a finding in the product need different
  answers, and `Operator` rows in `docs/COMPLIANCE_TRACKER.md` are the
  ones that are a deployment's own;
* **a request, not a description**, if you have one. Most of what has
  been found in this codebase was found by running something rather than
  reading it, and a curl that demonstrates it removes an argument about
  whether it reproduces;
* **what you did not check.** A report saying "this endpoint leaks X, I
  did not look at the others" is more useful than one that implies
  completeness.

## What you can expect us to check

Every report is checked against the register and the review record before
anything is claimed about it:

* whether it is already known — our pre-production review recorded each
  finding and its fix, and the tests that guard them are named in
  `docs/COMPLIANCE_TRACKER.md`. A report matching one of them may be a
  regression, which is more serious than a new finding;
* whether a rule was supposed to prevent it. The recurring pattern in
  this codebase is that a guard existed and measured the wrong thing, so
  "why did the test not catch this" is part of the answer rather than an
  afterthought;
* whether the fix belongs in the code or in the deployment's
  configuration. Some controls are genuinely the operator's — TLS trust,
  disk encryption, off-host backups — and saying so is not a deflection
  when the register says the same thing.

## Scope

**In scope:** this repository, the portal it serves, the desktop client
and CLI in `igni`, the paid modules — CodeIndex in
`igni__code-index`, Artifacts in `igni__artifacts`, Fleet in
`igni__cloudvm` — the extensions they ship, the release bundle and
`igni-import`, the Helm chart, and the compose files:
everything a customer installs. The models themselves are weights, not
software; a report about how igni serves or routes to them is in scope.

**Out of scope**, and stated so a report is not wasted:

* **a vendor-operated service, because there is not one.** Nothing here
  phones home; there is no dashboard, no telemetry endpoint and no
  central instance to test against. `DP-5` in the register is that claim,
  and a source scan and a runtime test in each repository defend it;
* **a customer's deployment you do not operate.** Testing somebody
  else's installation without their permission is their incident, not a
  report;
* **findings that require the node token, the super-admin key, or the
  private key of a service a super admin has approved — and with it
  that service's service token.** Those are
  deployment secrets; holding one already means the deployment is
  compromised, and the boundaries they protect are documented in
  `docs/service-identity.md` and `docs/gpu-clusters.md` rather than
  assumed. (A service registering itself is not one of them: that is
  unauthenticated by design and grants nothing until it is approved, so
  a way past the approval *is* in scope);
* **an unfixed upstream CVE in a base image.** Reported by our own
  scheduled scan already, listed in the SBOM, and not remediable here
  until upstream ships — see the SLA's note on unfixed findings.
