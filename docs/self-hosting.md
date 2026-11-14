# Making igni genuinely self-hostable

The product promise is that a customer runs igni on their own hardware
and their code never leaves their network. This document is an honest
accounting of what the codebase currently requires from infrastructure
**we** operate, and what has to change before that promise is true.

It is a map, not a schedule. Each section says what exists, what a
self-hosted deployment needs instead, and how big the gap is.

## The decision this follows from

**Self-hosted only.** There is no hosted tier and no multi-tenant
deployment. Distribution is the GCP and AWS marketplaces; the marketplace
meters and invoices, so there is no in-app billing.

The argument for a hosted tier was a pricing floor: $2.50/developer
assumes 200 developers amortising one ~$504/month GH200, so at ten
developers it is $50 each and at five it is $100 — the seat price the
whole pitch attacks. That floor is an artifact of fixing the hardware.
igni 1.0 is a **27B** model; five developers do not need a GH200, they
need an L40S or an A10. Size the GPU to the team and cost-per-developer
stays flat all the way down. The floor is a hardware-sizing story, not a
hosting story.

Hosting would also cost the thing being sold. "Your code never leaves
your network" and "we hold your source" are not two tiers of one product
— they are different products with different threat models, compliance
surface and sales motion.

## Scale: one node or many

A customer with 600 developers runs three GH200s, not one. Everything
below assumes **a set of inference backends**, not a single endpoint.
This matters most in §1.

---

## 1. Inference — the AI key pool

**What exists.** `app/services/ai/` is ~1,000 lines shaped for a
multi-tenant proxy: a pool of upstream provider API keys in GCP Secret
Manager, rotated across tenants, with per-key rate limits, per-org
override pools, and sticky per-user assignment.

**What self-hosting needs.** The customer runs vLLM on their own GPUs.
There are no provider keys to ration and no tenants to ration between.
There is a *list of inference endpoints* to spread load across.

**The part worth keeping is bigger than it looks.** Sticky per-user
assignment already exists, and `key_pool.py`'s own docstring gives the
reason: "so prompt caches at the provider" stay warm. On a multi-node
self-hosted deployment that argument gets *stronger* — routing a
developer back to the same vLLM node reuses its KV cache, which is a
large latency win, not a rounding error. Capacity awareness and failover
also survive: a node has a concurrent-sequence limit, and a node can die.

So this is a rename and a narrowing, not a deletion:

| Today | Self-hosted |
|---|---|
| `AIKeyConfig` (api_key, base_url, model, tiers→pools) | `InferenceBackend` (base_url, model, optional token, max_concurrent) |
| Loaded from GCP Secret Manager | Loaded from config / env / mounted file |
| Sticky assignment for provider prompt cache | Sticky assignment for **node KV cache** — same code, better reason |
| Per-key rate limits (provider quota) | Per-node concurrency (GPU capacity) |
| Per-org override pools | Gone — one customer, one deployment |
| `chat` / `codeindex` pool split | **Keep.** Batch indexing must not starve interactive developers, so pointing them at different node sets is a real operational need. |

**Delete:** `secret_provider.py`, `org_pool_service.py` (388 lines of
Secret Manager CRUD), and the per-org override path in `pool_manager.py`.

**Estimated:** ~1,000 lines → ~350.

**Since written:** the list is no longer only static. Nodes can register
themselves at runtime so a customer can start and stop GPU clusters
without a config change or a rollout, which is how GPUs are actually
bought — burst capacity for a load test, an autoscaler replacing a spot
node, a cluster stopped overnight. Registrations carry a heartbeat TTL,
so a node that is reclaimed or panics leaves rotation on its own rather
than staying in the list and failing every request routed to it. See
[gpu-clusters.md](gpu-clusters.md).

**Cloud model providers (DEC-16).** A backend can instead be a cloud's
model service, and igni server signs for it — clients keep their igni
token and never hold cloud credentials. Each kind speaks one API:
`/v1/chat/completions` for `openai`, `azure_openai` and `vertex_openai`;
`/v1/messages` (Anthropic's API) for `anthropic`, `bedrock`,
`vertex_anthropic` and `azure_anthropic`. Add one in the portal under
Inference, or in the static backend list:

```json
{"id": "bedrock-eu", "kind": "bedrock", "region": "eu-central-1",
 "models": ["claude-opus-5-5"],
 "model_ids": {"claude-opus-5-5": "eu.anthropic.claude-opus-5-5-v1:0"},
 "auth": "assume_role",
 "auth_settings": {"role_arn": "arn:aws:iam::123456789012:role/igni-bedrock"}}
```

| Kind | Auth methods | The identity needs |
|---|---|---|
| `bedrock` | `ambient` (the server's IAM role), `assume_role` (`role_arn`, optional `external_id`), `access_keys` | `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`; for `assume_role`, `sts:AssumeRole` on the role and a trust policy naming the server |
| `vertex_openai`, `vertex_anthropic` | `ambient` (Workload Identity), `impersonate` (`service_account`), `service_account_key` | `roles/aiplatform.user`; for `impersonate`, Service Account Token Creator on the account |
| `azure_openai`, `azure_anthropic` | `ambient`, `managed_identity` (`client_id`), `client_secret` (`tenant_id`, `client_id`), `api_key` | Cognitive Services OpenAI User (or the Foundry equivalent) on the resource |
| `anthropic` | `api_key` | — |

A cloud backend makes that cloud a processor of prompts: see R-A in
[SUPPLIERS.md](SUPPLIERS.md). `tools/smoke_provider.py` sends one real
request through a configured backend, to prove its identity and model
before users rely on it.

## 2. Object storage — changesets

**Not this repo's.** igni server touches no object storage: changesets
belong to CodeIndex (igni__code-index), and so does the work of making
their storage self-hostable. No cloud SDK here is for storage: boto3
sends SES email, and boto3, google-auth and azure-identity sign for a
cloud model backend (§1) and load only when one is configured. A test
asserts that a deployment with no Google Cloud in its environment starts.

## 3. Secrets

**What exists.** GCP Secret Manager in three places — the AI key pool
(going away with §1), the org pool service (deleting), and the
super-admin password hash.

**What self-hosting needs.** Nothing. Inference backends and the admin
hash become environment variables or a mounted config file the customer
owns. Delete the dependency.

### Rotating the encryption key

`SECRET_ENCRYPTION_KEY` encrypts what the deployment keeps in its own
databases: the token-signing private key (`signing_keys`) and the API
keys of GPU nodes added in the portal (`inference_nodes`), both in the
shared plane, and every app's first VM credential awaiting pickup
(`vm_credentials`, in each organisation's database). It can be rotated
without downtime:

1. Mint a new key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
2. Put the new key in `SECRET_ENCRYPTION_KEY` and the old one in
   `SECRET_ENCRYPTION_KEYS_PREVIOUS` (comma-separated if there are
   several). Restart. Both keys read; new writes use the new one.
3. `python tools/rotate_secret_key.py` inside the API container reports
   what is still on an old key, in the shared database and every
   organisation's own one, detached organisations included.
4. `python tools/rotate_secret_key.py --apply` re-encrypts it, one row at
   a time. Interrupted, it is safe to run again: what it finished is
   counted as current, the rest is still readable under the old key. Run
   it until it reports `to rotate 0` and `unreadable 0`.
5. Clear `SECRET_ENCRYPTION_KEYS_PREVIOUS` and restart. Until you do, the
   old key still opens every stored secret, and the server warns on
   every boot.

Exit status 1 means a row decrypts under none of the configured keys: do
not remove the old key until that is understood. Helm commands for the
same steps are in `charts/igni/README.md`.

Redis is not walked, and need not be. The server also seals a few
short-lived values there under the same key — pending sign-in codes, the
token behind a sign-in handoff, OIDC state, and a self-registered GPU
node's API key — and every one expires within ten minutes (a node's
registration within three, refreshed by its heartbeat under the current
key). Leave at least ten minutes between step 2 and step 5; anything
still sealed under a removed key is treated as absent, so the worst case
is a user asking for a new code or a node registering again.

## 4. Identity — the largest new build

**What exists.** Email/password with OTP, plus OAuth against
**google.com**.

**What self-hosting needs.** An air-gapped deployment cannot reach it,
so:

* **Local accounts must be a complete path**, not a fallback. They mostly
  are already — email/password and OTP exist — but OTP delivery goes
  through SMTP, which needs to become the customer's relay or be
  disableable.
* **OIDC against the customer's IdP** (Keycloak, Okta, Entra, Authentik).
  This is new work and it is what enterprises will actually ask for.
  SAML is the follow-on; OIDC covers most of it.
* **Google sign-in stays optional.** The list of social sign-ins can be
  empty; email and SSO work without it.

This is the biggest genuinely new component in the list.

### Signing in is not the same as joining

OIDC answers "who is this?". It does not answer "which organization are
they in?", and on a first login the two come apart in a way that is
easy to miss.

`OAuthService.find_or_create_user_and_get_token` — the path OIDC uses —
creates the user, and `UserDB.create` claims a pending invitation as it does. If
an admin has already invited that address, the pending membership is
claimed and the person lands in the right org with the role the admin
chose. If nobody has, they still land in the right org: a person
belongs to the organisation that owns their address's domain, and joins
it as their account is made — directory (SCIM) accounts included. An
address on no organisation's domain is refused.

**Invites remain necessary self-hosted**, therefore, and they are about
membership rather than authentication. Invite first, then have the person
sign in; the order matters because the claim happens at first login.

Deliberately not "solved" by auto-joining everyone from the IdP to a
single org: claims describing group membership vary wildly between
providers, and mapping them wrong grants access rather than denying it.
An admin inviting a known address is a smaller and more auditable
surface. See `app/services/auth/oidc.py` for the same reasoning applied
to role mapping.

### Invites work without a mail server

`POST /portal/team/invite` always returns `invite_url` alongside
`emailed`. The link is not a secret — the invite is claimed by signing up
with the invited address, so it is derivable from the address alone —
which is why it is safe to hand back to the admin who created it.

When `emailed` is false the portal says so and shows the link to copy,
rather than claiming a message was sent. That is the difference between
"invites work without email" and "invites are silently broken without
email".


## 5. Git providers

Not this repo's. Repository integration (GitHub, GitLab, webhooks,
repositories) belongs to CodeIndex (igni__code-index).

## 6. Email

Nine files touch SMTP. Self-hosted needs the customer's relay, and needs
every path to work with `email_enabled=False` — an air-gapped install may
have no mail at all. Invite flows and OTP currently assume delivery
works; they need a no-mail path (admin-visible invite links, printed
codes) or those features degrade honestly.

**Done.** `app/services/email/delivery.py` gives every flow a defined
answer for "and if it cannot be sent?": one-time codes are *never*
returned to the caller, informational mail is dropped with a note, and
invite links come back to the admin who created them. See "Invites work
without a mail server" above.

### Signing in before email works

One-time codes are written to the server log only when you say so:
`LOG_SIGN_IN_CODES=true` (Helm: `email.logSignInCodes`). It is off by
default, because anyone who can read those logs — wherever they are
shipped, for as long as they are kept — can then sign in as any user and
reset any password. With it off and no relay, a code is issued, reaches
nobody, and the log says so without the code.

So the first-setup path on a deployment with no email is:

1. set `LOG_SIGN_IN_CODES=true` and restart — the startup log warns, and
   keeps warning on every boot while it is on, and every super admin
   sees the same warning as a banner in the portal;
2. request a sign-in code in the portal, and find it in the API log
   under `EMAIL DISABLED`;
3. configure a relay (below) or SSO (§4);
4. set `LOG_SIGN_IN_CODES` back to `false` and restart.

Codes are never logged while a relay is configured, whatever the setting
says — and the server warns if it is left on then, because removing the
relay later would send codes back to the log without anybody choosing it.

The defaults were flipped to match. `email_enabled` was `True` and
`smtp_host` was `smtp.gmail.com`, which was correct for one hosted
deployment and wrong for every self-hosted one: a customer who
configured nothing got a service convinced it could send mail, trying to
reach Gmail from a network with no route to it, failing silently inside a
background task once per message. Now both default to off and empty —
opting *in* to email is the deliberate act.

`portal_base_url` moved from `https://ignite-ember.sh` to
`http://localhost:3000` for the same reason. Every deployment must set
it, and a default that is obviously wrong locally beats one that points
at somebody else's domain.

### Relays we support

Three shapes, and `smtp_tls_mode` picks between them. They are different
servers rather than preferences — two of the three could not be reached
at all while STARTTLS was hardcoded on.

| Relay | host | port | `smtp_tls_mode` | auth |
|---|---|---|---|---|
| **Amazon SES** | `email-smtp.<region>.amazonaws.com` | 587 | `starttls` | SES SMTP credentials |
| **Amazon SES** (implicit TLS) | same | 465 | `tls` | same |
| Hosted provider | e.g. `smtp.gmail.com` | 587 | `starttls` | username + password |
| Internal relay | e.g. `mail.corp.internal` | 25 | `none` | usually none |

**Amazon SES**, specifically, because three details catch people out:

* **SMTP credentials are not your IAM access key.** SES derives a
  separate username/password pair from an IAM user (console: *SES → SMTP
  settings → Create SMTP credentials*). The username looks like an access
  key id, which is exactly why the mistake is easy — pasting the real
  access key gets you an authentication failure that reads like a wrong
  password.
* **`email_from_address` must be a verified identity** in that SES
  account, domain or address. SES rejects anything else outright, so the
  default `noreply@igni.local` will not send.
* **A new SES account is sandboxed**: it can only send *to* verified
  addresses until AWS grants production access. Worth requesting before
  you need it, not during a rollout.

Set `email_from_name` too — a self-hosted deployment is the customer's,
and their people should not receive sign-in codes from a vendor's brand.

### SES without a stored password

`email_backend=ses` uses the SES API instead of its SMTP interface, and
the reason to choose it is credentials rather than email. boto3 resolves
the standard AWS chain — environment, shared config, container
credentials endpoint, IMDS — so a deployment on EC2, ECS or EKS
authenticates with its instance profile, task role or pod identity and
**stores no mail secret at all**. That is the difference between a role
AWS rotates and a password in a Helm values file, and it is what a
Marketplace listing is expected to support.

```
EMAIL_BACKEND=ses
SES_REGION=eu-west-1
EMAIL_FROM_ADDRESS=noreply@acme.example      # a verified SES identity
EMAIL_FROM_NAME=Acme Engineering
```

No credentials appear in that block, and that is the point. Set
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` only where no role is
available; leaving them empty is what engages the chain. Cross-account
assume-role needs no code from us either — an `AWS_PROFILE` with a
`role_arn` in the shared config file is resolved by the SDK.

`SES_CONFIGURATION_SET` is optional and only needed if you use one for
bounce and complaint handling.

The IAM policy is one action, and can be narrowed to the identity you
send from:

```json
{
  "Effect": "Allow",
  "Action": "ses:SendEmail",
  "Resource": "arn:aws:ses:eu-west-1:<account>:identity/acme.example"
}
```

Both backends render the same `EmailMessage` and send it raw, so
switching between them does not change what recipients see. The
verified-identity and sandbox rules above apply either way — they are
SES account state, not a property of how you connect.

An internal relay on port 25 with no authentication is the common
air-gapped case, and it needs `smtp_tls_mode=none` with `smtp_username`
left empty. Empty credentials are omitted entirely rather than sent as
blanks, because aiosmtplib authenticates whenever a username is not
`None` and a relay offering no AUTH refuses the whole message.

## 7. Billing — delete

Six files plus the Stripe webhook, the portal billing page, and the
subscription tables. The marketplace meters and invoices. Nothing about
entitlement lives in the application: a customer has CodeIndex because
code-index is deployed for them, and that is the whole check.

## 8. Telemetry

Clean. Nothing in `app/` phones home. The analytics snippet that was in
the portal HTML, gated on hostname, left with the public site when it
moved to its own repository; the portal has none. Worth an explicit test so it
stays that way, because a single well-meaning error-reporting SDK would
break the air-gap claim silently.

---

## Ordering

The dependencies run roughly:

1. **Billing deletion** and **§3 secrets** — pure removal, unblock nothing but shrink everything.
2. **§1 inference backends** — self-contained, largest line reduction, and the pool is currently load-bearing for both chat and indexing.
3. **§6 email degradation** — small, and makes §4 testable without a relay.
4. **§4 OIDC** — largest new build, and the one customers will ask about by name.
5. ~~**Packaging**~~ — **done.** Two artefacts, both aimed at a customer rather than at us: the Helm chart `charts/igni` (F98), and the single-instance compose stack — portal, bundled Postgres and Redis behind a profile, Caddy terminating TLS in an overlay (F99), and a verified nightly `pg_dump` in both (F100). Booted end to end before being written up. Ignite Ember's own infrastructure code is separate and is neither of those, which is now fine: it is not the customer-facing artefact any more.

## What this does not change

The split between igni server and the services beside it — igni__artifacts
and CodeIndex, each deployed on its own — stands. It was designed around
each service owning its own data and asking the other over HTTP, which is
correct regardless of who runs the servers. Only the operator changes, and
what the operator has to do is small: deploy each service with the
server's URL and its own, then approve it once on the portal's *Services*
page. No secret is shared between them
([service-identity.md](service-identity.md)).
