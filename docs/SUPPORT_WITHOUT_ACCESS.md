# Support without access

For the customer's security and operations teams. How support works when
Ignite Ember cannot see the deployment: what the customer sends, what it
should remove first, and what Ignite Ember never asks for. Part of the
security pack ([SECURITY_PACK.md](SECURITY_PACK.md)).

Ignite Ember is the company. igni is the software.

## 1. Why there is no access to give

igni has no path in for its vendor, by design:

* **No support tunnel or remote session.** Nothing in igni opens a
  connection to Ignite Ember, and there is no endpoint for one
  ([NETWORK_DIAGRAM.md](NETWORK_DIAGRAM.md), section 3).
* **No vendor account.** igni creates no default, shared or standing
  account, and refuses to start on a database that carries the
  development bypass account (AC-4 in
  [COMPLIANCE_TRACKER.md](COMPLIANCE_TRACKER.md); AC-P7 in
  [POLICIES.md](POLICIES.md)).
* **No telemetry or error reporting.** The egress tests fail the build on a
  telemetry package or an unexplained outbound host (DP-5).
* **An offline licence.** Renewing is replacing a file. Nothing reports
  usage to Ignite Ember ([licence-file.md](licence-file.md)).

So support works on what the customer chooses to send, and nothing else.
The EULA says the same in its support clause (§5, still
awaiting legal review).

## 2. Who answers

| Level | Who | What |
|---|---|---|
| L1 and L2 | The customer's own team, or its integrator | Running the deployment, first diagnosis, collecting what section 3 lists |
| L3 | Ignite Ember | Faults in igni itself. Response targets, not resolution targets: 4 business hours when the deployment is not serving requests, 2 business days otherwise (the EULA §5) |

| | |
|---|---|
| Support address | `__ORGANISATION_TO_COMPLETE__` |
| Business hours and time zone | `__ORGANISATION_TO_COMPLETE__` |

A suspected security vulnerability goes to the channel in
[SECURITY.md](../SECURITY.md), not to support.

## 3. What to send

Send what answers the question, and only that. Each item below carries no
personal data unless noted.

| Item | How to get it | Notes |
|---|---|---|
| The version | `GET /version` on igni server, and on each paid module | The first thing asked for. A report against the wrong version is answered for the wrong code |
| Readiness | `GET /ready` | Says which dependency is down: PostgreSQL, Redis, or the pool |
| Licence status | `GET /v1/portal/licence` (super admin), or `GET /v1/licence` on a module | Names the customer and the licence id. Needed for anything about read-only or a refused request |
| Logs around the fault | `LOG_FORMAT=json` makes them filterable ([observability.md](observability.md)) | igni's logs carry no request body or prompt, by a rule over all of `app/` (`tests/test_the_prompt_stays_out_of_the_logs.py`). **Remove any one-time sign-in code first**: with no mail relay configured, igni writes them to the log |
| Metrics | `/metrics`, behind the metrics token | Labels are from an allow-list, so a label cannot carry personal data |
| Configuration | The chart values or env file, **with every secret removed** | Lets Ignite Ember reproduce the setup |
| A failing request | The method, path, status and response body, with the bearer token removed | A reproducible request ends most arguments about whether a fault exists |
| The SBOM version | `sboms/` in the release bundle | Only for a question about a dependency or a CVE |

Ignite Ember reproduces a fault on its own copy of the same version. It does
not need the customer's data to do that.

## 4. What Ignite Ember never asks for

Ignite Ember does not ask for any of the following. A request for one, from
anybody claiming to be Ignite Ember, is a reason to stop and check who is
asking.

* Access of any kind: a VPN account, a remote session, a jump host, an
  account in the deployment or the identity provider.
* A secret: the encryption key (`SECRET_ENCRYPTION_KEY`), a password, a
  bearer token, the super-admin key, the node registration token, the SCIM
  or metrics token, a service's private key, a database password.
* Customer data: a database dump or backup, an audit-trail export,
  subject-access results, conversations or prompts, source code, documents,
  the CodeIndex index.
* Model weights or training data the customer owns.

If a fault cannot be diagnosed without something on that list, the customer
decides, and the default answer is no. The EULA limits Ignite Ember's
support obligation to what can be determined from what it is given
(the EULA §5).

This section is a commitment the organisation makes, not something the
software enforces. Approved by: `__ORGANISATION_TO_COMPLETE__`. Date:
`__ORGANISATION_TO_COMPLETE__`.

## 5. Updates and fixes

A fix reaches the customer as a release: a signed offline bundle the
customer verifies and imports itself, on its own schedule
([OFFLINE_UPDATES.md](OFFLINE_UPDATES.md)). Ignite Ember cannot push a fix
into a deployment. A security fix is announced as an advisory
([VULNERABILITY_MANAGEMENT.md](VULNERABILITY_MANAGEMENT.md)). No release
has been signed yet, so no signed bundle has been produced
([SECURITY_PACK.md](SECURITY_PACK.md), Gaps).
