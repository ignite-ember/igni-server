The portal is the administrative side of a self-hosted igni deployment. It is where you admit developers, decide how they authenticate, ship them a standard set of agents and skills, and take access away again. Everything described here is an HTTP API under `/v1/portal` that the portal front end drives, so the same work can be scripted; the routes are given so you can.

## Your own account, first

There is no seeded administrator. Nothing in the chart or the compose stack creates one — so the first thing to do after the server comes up is sign yourself up at the portal.

**The first account a deployment creates is its super admin** — the bootstrap account, and the only one that may use any company address. A super admin owns the install: creates organisations, makes other super admins, and changes settings that belong to the whole deployment. Nobody else can create an organisation.

So, as that first account: create your company's organisation under **Deployment → Organisations** and give it your email domains (`acme.com`, `acme.co.uk`). Then either move this account onto your address at the company — the portal sends a code to it, and the old address is gone — or make a colleague on the domain a super admin and delete this account.

**With no mail relay, the sign-in code goes to the log, not to you.** `EMAIL_ENABLED` is false by default, so look for the `EMAIL DISABLED` marker:

```bash
kubectl -n igni logs deploy/igni-api | grep 'EMAIL DISABLED'
```

`SUPER_ADMIN_PASSWORD_HASH` is break-glass — a bcrypt hash you set, presented as `X-Super-Admin-Key`. It reaches the deployment-wide settings under `/v1/portal` when nobody can sign in, typically because the identity provider is the thing that needs fixing: the registered clients (`/v1/portal/clients`, with their approve, revoke and held-update routes) and the extension priority order (`/v1/portal/extension-priority`). It does not reach the super-admin list (`/v1/portal/super-admins`) or the organisation list (`/v1/portal/deployment/organizations`); those need a signed-in super admin, as do the deployment notices (`/v1/portal/deployment/notices`). It makes nobody a super admin, and it is not required for normal operation.

## Accounts, organisations and roles

An account is a row in `users`, keyed on a unique email address, with an `auth_provider` column recording how it was created (`email` by default, or `google`, `oidc`, `scim`). An organisation is a row in `organizations` with a unique slug. The two are joined by `org_memberships`, which carries the only two things that decide what somebody may do:

| Column | Values | Meaning |
|---|---|---|
| `role` | `admin`, `member` | Admin of **this** organisation. |
| `status` | `active`, `invited`, `deactivated` | `invited` becomes `active` when the person signs up with the invited address. |

The one deployment-wide role is on the account itself: `users.is_super_admin`. Super admins are managed under **Deployment → Super admins** in the portal (`/v1/portal/super-admins`); a person must have an account before they can be made one, and the last super admin cannot be removed — not even by themselves, and not by deleting their account.

**A person belongs to the organisation that owns their address's domain, and to no other.** An organisation owns one or more email domains, and no two organisations share one. Signing up — with a password, through SSO or a social provider, or provisioned by your directory over SCIM — is only possible for an address on a claimed domain, and the account joins that organisation as it is made: claiming a pending invitation, so the role an admin chose stands, or as a `member`, or as `admin` when the organisation has none yet. An address on no organisation's domain is refused. Invitations still pre-assign a role, and only for addresses on the organisation's domains.

Super admins add domains to an organisation at any time; a domain somebody's address is on cannot be removed. Apps — a PR reviewer and the like — are a different kind of identity, not accounts.

**Services are approved here too.** The documents service (Artifacts), CodeIndex and any later service are deployed with this server's URL and their own, and register themselves on first start. Each appears under **Services** in the portal as pending, with the thumbprint of its key — compare it with the one the service logged — and the extension it would put on people's machines: its pages, commands, settings, permissions, what it changes in igni's prompts, the agents of igni's it stands in for, and its plugin's own agents and skills. Two extensions that want the same thing — a prompt section, a command, an agent — collide, and the one higher in the priority order you keep on the **Services** page has it, on every machine; the page lists each collision with who has it and who loses it, and nothing is held back. To change who has something, reorder the priority. Approving it is the one decision a super admin makes for a service: from then on it can get tokens for each organisation, read people from the directory, and send notifications, and its extension is offered to every signed-in machine. Revoking it stops all of that at once.

**An approved service's update that asks for more waits for you.** Approval covers the version you read. When an approved service uploads a version that asks for more — a host it may reach, a path or wider access to people's projects, a secret, a tool or a riskier one, a host capability, a hook, a prompt override or section, a command, a page, an agent or a skill — igni holds it, and machines keep receiving the version you approved. The service gets an **Update asks for more** badge on the **Services** page, and its own page shows the held version with everything it adds listed, and **Approve** and **Reject**. Approving makes it the version every machine receives; rejecting discards it, and the service stays on the approved version until it uploads another. A newer upload replaces a held one. A version that asks for the same or less goes live without asking. The same decisions are in the API, with a super admin's token, and each is recorded in the audit trail with what was added:

```bash
curl https://igni.example.com/v1/portal/held-updates -H "Authorization: Bearer $TOKEN"
curl -X POST https://igni.example.com/v1/portal/clients/$CLIENT_ID/held-update/approve -H "Authorization: Bearer $TOKEN"
curl -X POST https://igni.example.com/v1/portal/clients/$CLIENT_ID/held-update/reject -H "Authorization: Bearer $TOKEN"
```

**A service's own settings are on its own site, not here.** Each service serves its screens on its own hostname, and its name on the Services page links there; a super admin signs in to it through igni, choosing an organisation as for any other sign-in. Artifacts' document graph for each organisation is set there, under **Settings** — the Organisations page only links to it.

The portal selects the active organisation with an `X-Org-Id` header (a CLI token's baked-in `org` claim wins over it). The caller must be an active member of whatever is requested, or the request is 403, and `require_admin` then tests the role **in that organisation**. Slugs are 3–32 characters, lowercase alphanumeric and hyphens, no hyphen at either end; omit one and it is derived from the display name, falling back to `org-<12 hex>` for a name that yields nothing valid.

## What an ordinary member can see

The portal is not an admin console with a few member pages; it is largely open to any active member, with `require_admin` on specific routes. Worth knowing before you decide whether to give developers portal logins.

| Open to any active member | Admin only |
|---|---|
| Dashboard, usage | Compliance — audit, erasure, subject access |
| Listing groups | Creating or editing a group, its entries and its membership |
| | Inviting, deactivating, changing roles |

Hiding a sidebar entry is cosmetic. Every route is registered unconditionally and typing the URL reaches it; `require_admin` on the server is the only real gate.


## Sign-in methods

`GET /v1/auth/methods` is unauthenticated and tells the sign-in page what this deployment actually offers. It is the quickest check that your configuration took effect. The response carries three things — `password`, `oauth_providers` and `sso`; the one-time code rides with the password method rather than being reported separately.

| Method | Configured by | Default | Notes |
|---|---|---|---|
| Email + password | always on | — | 8+ chars with upper, lower, digit and a symbol. No setting disables it. |
| Email one-time code | always on | — | 6 digits, 10-minute life, dead after 5 wrong attempts. Sign-up always requires one; `EMAIL_ENABLED` and `SMTP_HOST` decide only whether the code is emailed or written to the log. |
| SSO (OIDC) | `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | all empty | All three or none. `OIDC_DISPLAY_NAME` (default `SSO`) is the button label. |
| Social | `OAUTH_PROVIDERS` + that provider's credentials | `["google"]` | Must be listed **and** credentialed to appear. |
| SAML | — | — | Not available yet; it is the next sign-in method planned. Use OIDC, which every major identity provider also speaks. |

SSO is the one an air-gapped deployment can reach, since it talks to whatever you run — Keycloak, Okta, Entra, Auth0, Authentik. `OAUTH_PROVIDERS` is a JSON array; `OAUTH_PROVIDERS=google` is a startup crash, not a one-element list. Two limits worth knowing before you roll SSO out:

- A non-HTTPS `OIDC_ISSUER` is **fatal at startup** outside development. The signing keys for every login arrive over that connection.
- There is no group-to-role mapping. A first SSO login creates an ordinary member; an admin promotes them. Claims describing groups vary between providers and mapping them wrong grants rather than denies.
- `OIDC_LINK_BY_EMAIL` is `false`. With it off, a login whose ID token asserts no *verified* address is refused 403 rather than matched to an existing account.

By default, addresses at large consumer mail providers (`gmail.com`, `outlook.com`, `icloud.com`, …) are refused at sign-up — `domain` sharing visibility means "my colleagues", and that is only meaningful while an address domain names an employer. Set `BLOCK_PUBLIC_MAIL_DOMAINS=false` to turn it off. Existing accounts are never affected.

### Inviting somebody

```bash
curl -X POST https://igni.example.com/v1/portal/team/invite \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Org-Id: $ORG_ID" \
  -H 'Content-Type: application/json' \
  -d '{"email": "dev@example.com", "role": "member"}'
```

The response always carries `invite_url` and `emailed`. The URL is `<PORTAL_BASE_URL>/signup?email=<address>` — no token, no expiry, because the invitation is claimed by signing up with that address — so a deployment with no mail relay invites people perfectly well; the admin just passes the link on. Email is off by default (`EMAIL_ENABLED=false`, empty `SMTP_HOST`). When it is off, verification codes go to the server log behind the grep-able marker `EMAIL DISABLED` and never into an HTTP response.

**Invite before they sign up.** The duplicate check is scoped to the organisation, so inviting an address that already has an account elsewhere succeeds and writes a pending invitation that can never be claimed: the claim runs at sign-up, and that person has already signed up. Add them with a membership instead.

An invited row cannot be flipped to active by an admin. `PATCH /team/{id}/status` accepts only `active` ↔ `deactivated`; the claim happens at sign-up.

### Provisioning from a directory

SCIM 2.0 Users is served at `/scim/v2` — outside `/v1`, because the path is fixed by the standard. Every route answers **503 until `SCIM_TOKEN` is set**, so a deployment that has not configured provisioning is not running an unauthenticated one. Point Okta or Entra at `https://your-igni/scim/v2` with that token as a bearer credential. Three things it deliberately does not do: Groups are not implemented at all — there is no `/Groups` route, and `ServiceProviderConfig` advertises only patch, filtering (`maxResults` 200) and bearer-token auth, with bulk, sort, etag and changePassword unsupported; the only filter accepted is `userName eq "..."`, and an unfiltered list returns empty rather than your staff directory; `DELETE /Users/{id}` deactivates and never deletes, because a provider's choice of verb should not decide whether a departed colleague's documents survive. A provisioned account is given a random, discarded password, so nobody is ever handed a working one — but that alone does not close off local sign-in: an emailed one-time code signs in any account with an address, and the reset-password pair sets a usable password for whoever holds that address. What actually withdraws access is deactivation, which is what `active: false` and `DELETE /Users/{id}` set.

## Group policy

A group is how an organisation ships working material to its people. Each person belongs to **exactly one** group (a unique constraint on `user_id` enforces it) — the Default group until an admin puts them in another — and each group holds entries:

| Kind | Format | Kind | Format |
|---|---|---|---|
| `agents` | markdown | `tools` | python |
| `skills` | markdown | `hooks` | json |
| `commands` | markdown | `mcps` | json |
| `rules` | markdown | `plugins` | yaml |
| `output-styles` | markdown | `scripts` | shell |
| `workflows` | javascript | `settings` | json |

The format is not a choice — igni's loaders honour exactly one per kind. **Four of these ship executable code**: a workflow is JavaScript run in Node, a tool is Python imported into the session's own process, a hook is a command run on tool use, and a script is a shell file a hook invokes. Whoever can edit a group can run code on every machine in it. (LSP servers, monitors and knowledge are deliberately not group kinds.)

Every organisation gets one group flagged `is_default`, seeded lazily with what igni ships: 11 agents, 5 skills, 2 shell scripts and the 2 hooks (`pre-pr-review`, `post-commit-todo`) — 20 entries. It is **read-only**: editing it answers 409 telling you to copy it. Copying is the intended workflow — **Copy group** on any group's page (`POST /v1/portal/groups/{id}/copy` with a `name`) makes a new group holding its entries, its extensions and its settings, with nobody in it; `POST /v1/portal/groups` also takes `template_group_id`. So "the standard set, minus the coding agents" is two steps.

```bash
curl -X POST https://igni.example.com/v1/portal/groups \
  -H "Authorization: Bearer $TOKEN" -H "X-Org-Id: $ORG_ID" \
  -H 'Content-Type: application/json' \
  -d '{"name": "Platform", "template_group_id": "<default-group-id>",
       "default_model": "platform-review-lora"}'
```

A group's `default_model` — usually a LoRA adapter — and an agent entry's own `model` are checked against the nodes on write: a name no node serves is a **409 listing what is served**, rather than a failure that lands on whoever uses the group later. Resolution falls back agent entry → group default → deployment default. A group may also carry a `theme` (hex `accent`, `danger`, `success`, a `brand_name` up to 40 characters, and a `brand_mark` as a base64 `data:` URI under 64 KB — not a URL, because the desktop client's CSP allows `img-src` from `data:` only).

Entry content is capped at 256 KB and the entry name must be a plain filename (letters, digits, dot, underscore, hyphen) — the client turns it into a path on every developer's machine.

Members pick changes up by polling `GET /v1/portal/me/group/pack`, which is ETag-revalidated: an unchanged pack costs a 304 with no body. The client re-asks when its cache is over **300 seconds** old, and forces a conditional request at session start. It materialises entries under `~/.igni/group-policy/<kind>/` — agents as `<name>.md`, skills as `<name>/SKILL.md`, workflows `.mjs`, tools `.py`, scripts `.sh` (executable), MCPs `.json`. A hook reaches a script its own group ships through the `{group_scripts}` placeholder, which the client expands to wherever it put them. Disabled entries are omitted from the pack entirely.

### Switching a capability off for everyone

CodeIndex and Artifacts reach people as extensions, so switching one off for a group is switching its extension off on the group's **Extensions** tab (see *Which extensions a group gets* below). Its tools, pages, commands, agents and prompt changes leave the group's machines at their next sync. This is the supported way to say "we don't use that here" — more reliable than asking people not to, and it survives a new laptop.

What matters is that **the agent is told, not just blocked.** igni assembles a different system prompt depending on what is installed. With no document store, the archivist does not exist, and the main prompt says there is nowhere to file project knowledge this session: the agent must not offer to record things, and must not quietly put a codebase convention into the user's personal memory instead. So a developer in that group gets an assistant that says "worth writing down somewhere — there's no document store here, so this will be lost", rather than one that promises to record something and silently doesn't.

The same holds for CodeIndex: without it, the prompt has no guidance to hand code questions to `data-architect`, which does not exist either, and the agent searches with the shell.

Two consequences worth planning around:

- **It is a group-wide decision, not a per-person one.** A member cannot install locally what the group does not have: igni installs only the extensions their group has switched on. That is the point, but it means "one team wants documents, another doesn't" is two groups, not one group plus local overrides.
- **Switching it back on is not retroactive.** Nothing was recorded while it was off. If people were working in the gap, what they learned is in their commit messages and PRs, not in the store.

### Replacing an agent that igni ships

An `agents` entry whose name matches one of the built-ins replaces it for the group — `architect.md`, `reviewer.md`, and so on. The client materialises yours under `~/.igni/group-policy/agents/` and the loader prefers it, so you can change an agent's instructions, its tool grants, or the model it runs against without forking anything.

One rule the loader enforces regardless of what your markdown says: an agent only receives tools the session actually has, and no agent is handed a toolkit because of its name. An extension's tools reach agents over MCP, as its manifest allows, so naming your agent `archivist` does not hand it the document tools.

**Moving people.** Everybody is always in exactly one group. `POST /groups/{id}/members` with a `user_id` puts that person in this group and takes them out of the one they were in, in a single statement — it is the only way in, and doing it twice changes nothing. Removing someone from a group (`DELETE /groups/{id}/members/{user_id}`) puts them back in Default; nobody is removed from Default itself (409) — move them instead. Deleting a group puts its people back in Default. The group page's **Members** tab shows, for each person you could move, the group they are in now.

Listing groups is open to any org member; everything else — create, inspect, edit, entries, membership — is admin-only.

### Which extensions a group gets

An extension — CodeIndex, Artifacts, anything a service ships — reaches a machine only from its service, after a super admin approves that service. Approval is trust; it gives the extension to nobody. **An org admin switches each approved extension on per group**, on the group's **Extensions** tab, and it is off until they do. People in the group get the ones switched on there, and only those: igni server neither lists nor serves another extension to them, and their igni installs and offers nothing else.

The Default group can have extensions too — it is read-only as the copy of what igni ships, but this is not part of that copy, and most people are in it. Switching an extension off removes it from the group's machines at their next sync.

```bash
curl -X PUT https://igni.example.com/v1/portal/groups/$GROUP_ID/extensions/igni.codeindex \
  -H "Authorization: Bearer $TOKEN" -H "X-Org-Id: $ORG_ID" \
  -H 'Content-Type: application/json' -d '{"enabled": true}'
```

## Apps, VM profiles and commands: the cloud

In the cloud — the paid [Fleet](/docs/fleet) module — every **producer** runs in a microVM of its own. There are two kinds of producer: the people of your organisation, each in exactly one group, and **apps** — a review bot, an integration — which act only as themselves. The full contract is `docs/service-identity.md` §11–§15 in the server repository.

### Registering an app

**Apps** in the sidebar (org admins). An app generates its own key pair and prints the public half as a JWK; you register it with a name, an owner — a member answerable for it — and that public key. Nothing secret is typed into the portal, and a private key is refused. The app is approved by that act, in your organisation only. Its **client id** is what it signs its token requests with (`client_credentials` with `private_key_jwt`, the mechanism services use); its tokens say `kind: app` and carry no group.

An app is in no group and never acts for a person, so group switches do not reach it: on the app's page you switch extensions on **for that app**, and it is off until you do. The same page gives the app a VM profile, replaces its keys (the old key stops getting tokens at once), places a legal hold on it, and **discards** it: the app is removed at once, its place under the Fleet licence is free straight away, and its VM is erased in the background — unless a hold stands. You can start a fresh app with the same name, key and settings in the same step. The **Erasures** page shows each discarded app and former member as scheduled, held, erasing or erased, and lets you **discard now** a former member's VM instead of waiting for the retention period. See [Fleet](/docs/fleet).

```bash
curl -X POST https://igni.example.com/v1/portal/apps \
  -H "Authorization: Bearer $TOKEN" -H "X-Org-Id: $ORG_ID" \
  -H 'Content-Type: application/json' \
  -d '{"name": "Review bot", "owner_user_id": "<member>", "jwks": {"keys": [<public JWK>]}}'
```

### VM profiles

**VM profiles** in the sidebar (org admins). A profile is what a producer's microVM gets: its normal memory and the most it may have, vCPUs, how long it stays awake idle before it is paused (or never), optionally the memory a VM is expected to use (for placing it), for apps how many days unwoken before their VM is erased (never, by default; a person's VM is never erased this way), and the packages — apt, pip, npm, and an optional setup script run as root — built into the profile's read-only layer. Give one to each group and each app; a person gets their group's. Anything without one gets the organisation's **default** ("Standard": 1 GiB normally and up to 4 GiB, 1 vCPU, paused after 10 s idle), which you can edit but not delete. Deleting a profile puts its groups and apps back on the default; copying a group copies its profile.

A VM runs with its normal memory; while it needs more — a native build, say — it is given more, up to the most, if its node has room, and it goes back to normal once the need is over and before it is paused. Memory above the normal size costs nothing until a VM uses it. A new idle time or normal memory applies at once; a new most memory or CPU count at the producer's next cold boot. Changed packages rebuild the profile's layer, picked up at each VM's next cold boot.

**Limits.** A super admin sets the platform's caps on **Cloud limits** — the largest ceiling, the most vCPUs, the longest idle, the longest an app's VM may go unwoken before it is erased — and every profile must fit them; one saved before a cap was lowered is marked *Over the platform limits* and served clamped. The same page holds each organisation's quota of awake VM time, which is recorded but **not enforced yet**. A super admin can open any organisation's profiles from there and define or assign them.

### Commands in VMs

**Commands in VMs** in the sidebar. A command runs as root in one producer's VM, and who may ask is fixed:

- anybody — a person, or an app with its token — in **their own VM**;
- an org admin in **any VM of the organisation**;
- a super admin in **any VM of the platform**.

A command for one VM wakes it. A **job** — one command for a group's, a profile's or the whole organisation's VMs (every organisation's, for a super admin) — wakes nobody: each VM runs it at its next wake, including one that joins the scope later, until you cancel the job. What a command installs lives in the VM's writable layer, which an image upgrade may reset; anything meant to last belongs in a profile.

Every command is in the **audit log** on the same page: who asked and as what, which VM, the command, when it started and ended, and its exit code. Members see their own VM's; admins see the organisation's.

### VM credentials

A producer's VM can be woken with nobody present — an event starts or resumes its agent run — so each VM holds a credential of its own, and the platform itself can never get a token for anybody. A person's VM gets one when they sign in through the gateway; it rotates every time it is used, a stolen copy used after the VM has moved on revokes it, and it stops after 90 days unused. Signing out, being removed from the organisation or being deactivated revokes it too.

For an app, **Enable cloud** on the app's page issues one. Nobody sees it: the node agent picks it up once when it first creates the app's VM, and from then on it lives only on that VM. **Re-issue** does the same again and stops the old one at once — use it if an app's VM is lost or suspected compromised.

**VM credentials** in the sidebar lists the working ones — yours, or, for an org admin, everyone's in the organisation and every app's — with when each was last used, and revokes any of them.

### MCP approvals and events

In the cloud, a person's approvals of MCP servers are kept on igni server (`/v1/me/mcp-approvals`), not in a file in their VM; group policy still decides which servers are offered at all. A service can reach one of your producers' agents two ways: a **message**, which igni posts in a chat of the service's own and which starts or resumes the agent's run, or an **event**, which igni hands to the service's extension to act on. igni refuses either unless that producer has the extension switched on (through their group, or the app's own list), and a message needs grants the super admin approved with the service. igni keeps each item until the producer's igni takes it — waking their VM in the cloud — for at most 14 days, after which it expires and the service can see that it did. Either can start work with nobody present. How a service sends them is in [Building extensions](/docs/core/building-extensions).

## Onboarding a developer

There is nothing for a developer to install by hand — no `pip`, no CLI, no Python. They install one application and it provisions its own backend. Your work is the step before that, and you do it once for the whole company.

### Publish where your igni is

Do this first and onboarding becomes "install it and type your work email". A client asks for an **email address**, not a server URL, and resolves the address's domain to your deployment. The lookup reads a record you publish in your own zone or at your own web root; nothing vendor-run is contacted, which is what keeps it workable for an air-gapped install.

Publish either one. The file is tried first — it carries a content type and a wrong answer is easier to see — and the TXT record is there because most organisations find one of the two easy and the other a ticket.

```jsonc
// https://acme.example/.well-known/igni.json  — at the *email* domain
{ "api_url": "https://igni.acme.example", "portal_url": "https://igni.acme.example" }
```

```
_igni.acme.example.  TXT  "api_url=https://igni.acme.example"
```

`portal_url` is optional and is derived from `api_url` when absent, which is right for a deployment serving both under one name. `api_url` **must be https** — a plaintext answer is refused rather than written into a config file, because whoever answers this decides where your staff's sign-ins are sent. For the same reason the fetch does not follow redirects, times out in 4 seconds and reads at most 64 KB. `http://` is accepted only for `localhost`, `127.0.0.1` and `[::1]`.

If you publish nothing, the client falls back to asking for the server address and shows the reason discovery gave — "acme.example has no igni record published". That works, but it is a URL every new hire has to be told.

### What the developer does

**1. Install the application.** The desktop app is the normal choice. It is the only client a person needs; the CLI is for scripts and CI.

| Platform | Files |
|---|---|
| macOS (Apple silicon, macOS 26 or later) | `.dmg` |
| Windows | `.msi` / `.exe` |
| Linux | `.AppImage` / `.deb` / `.rpm` |

The builds are on each release at [ignite-ember/igni-app](https://github.com/ignite-ember/igni-app/releases). The macOS build is signed with a Developer ID, notarised, and updates itself. The Windows and Linux builds have no OS code signing — Windows shows a SmartScreen "unknown publisher" prompt on first launch — and do not update themselves, so those installs stay on the version they were installed at until somebody installs the next release.

**2. It installs its own backend, on first launch.** The app downloads `uv`, uses it to provision a pinned CPython 3.12, creates a managed virtualenv, installs `igni-core` at the version the app was built against, and downloads igni's decision model. Everything lands in a per-user cache — `~/Library/Caches/igni` on macOS, `%LOCALAPPDATA%\igni` on Windows, `$XDG_CACHE_HOME/igni` on Linux — so later launches skip straight to starting the backend.

Two things to put in the onboarding ticket: it checks for at least **1 GB free** before it starts, and the decision model it then downloads is about 1.7 GB on top; and it needs **network access on first run**. If your developers cannot reach the internet at all, provision the backend yourself and point the app at it with `runtime_python` — see [Installing igni](/docs/app/installing-igni).

**3. They sign in with their work email.** The app opens on a sign-in wall rather than behind a dismissable prompt — a session with no account has nowhere to put anything. They type the address, discovery finds your server from the domain, and sign-in hands off to **your** deployment's sign-in page, which renders the methods you enabled: password, whichever OAuth providers, your SSO button under the name you chose. Picking an organisation mints an org-scoped token that lasts 30 days.

A developer who is not an active member of the organisation they pick is refused at that step, which is the normal result of installing before you have invited them.

Then two things arrive without being asked for: model discovery registers whatever your nodes serve, and the group pack lands on disk — so the agents, skills and hooks you decided on are simply there.

**Assign the group whenever suits you.** A developer who starts igni before you have assigned them is in Default until you move them; moving them later just works, and their next sync picks up the new group.

### The CLI, for the cases that are not a person

`pip install igni-core` (Python 3.12) gives you `igni`, and it is the right thing for CI, a pre-commit hook or a scripted batch job. `ignite-ember` is the package's old name: it is still installable, and each release of it installs `igni-core` of the same version with the same extras.

**There is no headless way to sign in.** `/login` opens a browser, and the credential it writes — `~/.igni/credentials.json`, mode `0600` — is the only thing that authenticates. No environment variable supplies a token and there are no machine accounts or API keys. So a CI install is credentialled by signing in once on a machine that has a browser and copying that file across, and the token expires after 30 days, which makes that a recurring errand rather than a setup step. Plan for it, or keep CI to the parts that need no server. It is a single command with three non-interactive modes — `-m` for one message, `-p` for stdin → stdout, `-c -m` to resume the last session — and it is not how anybody should be told to start.

## The compliance surface

Four admin-only routes, behind the portal's Compliance page — the one sidebar entry that is hidden from members.

| Route | What it does |
|---|---|
| `GET /v1/portal/audit-events` | The trail, filterable |
| `GET /v1/portal/audit-events/export` | The same as JSON Lines, for a SIEM |
| `POST /v1/portal/erasure` | GDPR Article 17 |
| `POST /v1/portal/subject-access` | GDPR Article 15 |

Both reach every approved service as well as this server. An erasure that a service does not confirm answers 502 naming it, and is safe to run again; the answer lists the services that confirmed. A subject-access export that a service does not answer comes back marked `truncated`.

The trail records every `POST`, `PUT`, `PATCH` and `DELETE` under `/v1/portal` (which includes `/v1/portal/deployment`), `/v1/directory`, `/v1/service-notifications`, `/v1/nodes`, `/v1/vm`, `/v1/service-events`, `/v1/service-items`, `/v1/service-attachments`, `/v1/me/mcp-approvals` and `/scim/v2` — plus, wherever they are mounted, the paths ending `/vm-credential/pickup`, `/events/fail`, `/events/ack`, `/exports/pull`, `/archive`, `/failed` and `/vm-profile-request`.

The authentication paths are recorded whatever their method and outcome, because a failed sign-in is among the most valuable things a trail holds: `/v1/sign-in`, `/v1/sign-up`, `/v1/sign-in-with-code`, `/v1/send-sign-in-code`, `/v1/send-sign-up-code`, `/v1/send-reset-password-code`, `/v1/reset-password`, `/v1/auth`, `/v1/cli/token` and `/v1/clients` — the last being where a service or an app registers itself.

Reads are not recorded, with three exceptions that are events in themselves: `GET /v1/portal/audit-events/export`, `GET /v1/portal/access-review`, and a `GET` under `/v1/portal/orgs/` ending `/download`. `/heartbeat` is never recorded — a node beats every 60 seconds, which would bury the rows an administrator reads.

`audit_events` is **append-only in the database**, by a Postgres trigger rather than by convention: an `UPDATE` is refused even to a caller holding the connection. `DELETE` is deliberately left open, because `AUDIT_RETENTION_DAYS` has to be able to do its job.


## Deactivating somebody

```bash
curl -X PATCH https://igni.example.com/v1/portal/team/$USER_ID/status \
  -H "Authorization: Bearer $TOKEN" -H "X-Org-Id: $ORG_ID" \
  -H 'Content-Type: application/json' -d '{"status": "deactivated"}'
```

This sets the membership to `deactivated` **and** sets `users.deactivated_at`, which withdraws every session the person holds in the same statement. Both matter, and the second is the one that does the work: `deactivated_at` is checked at the single point every authenticated request turns a token into a person, so the account is refused everywhere on its next request — not only under `/v1/portal`. Setting the membership alone used to leave somebody holding their documents and their ability to publish for the thirty days their token had left.

It also closes the front door. A deactivated person cannot sign in again with a password, a one-time code, or their identity provider: those paths answer `403 This account has been deactivated.`

Three related levers, which are not the same act:

- **`POST /v1/portal/team/{id}/revoke-sessions`** (admin, same org) refuses every token the member already holds, by comparing each token's `iat` against a watermark. Use this for a suspected leaked credential; it leaves the membership alone.
- **`POST /v1/portal/me/revoke-sessions`** is the self-service version, needing no admin and no organisation — the route somebody takes when they lose a laptop. It signs the calling request out too, which is what "everywhere" means.
- **`DELETE /v1/portal/team/{id}`** removes the membership row permanently. It does not delete the account.

Reactivating clears `deactivated_at` and deliberately leaves the revocation watermark where it is: coming back is not a reason to re-admit tokens that existed before somebody left. Tokens are 30-day JWTs, so without these levers "revoked" would mean "revoked in a month".

Admins cannot change their own role or status, nor remove themselves — the guard exists so the last administrator cannot lock the organisation out. They can revoke their own sessions, since signing in again fixes it.

For the periodic review an auditor asks for, `GET /v1/portal/access-review` (admin, scoped to your org by the token) returns who holds which role with the three signals worth acting on separately: unclaimed invitations, accounts that never signed in at all, and accounts dormant beyond `?dormant_after_days=` (default 90). Deleting an organisation, by contrast, requires that you are its admin, that you have another organisation, and that every other active or invited member has already been removed.

## Where to go next

- **[Artifacts](/docs/artifacts)** — if you are deploying the documents service, this is what your team gets from it.
- **[Configuration: files, precedence and where igni loads things from](/docs/core/configuration-and-precedence)** — how your group policy interacts with what a developer sets locally.
- **[Building extensions](/docs/core/building-extensions)** — what a service's extension can do on your people's machines, before you approve one.
- **[Licensing and evaluations](/docs/server/licensing-and-evaluations)** — installing the licence file the paid modules check.
