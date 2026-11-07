# Portal Manual QA Plan

Companion to the automated test suites (Vitest + Playwright). Covers the
flows the automation can't reach — visual polish, real email delivery,
real backend services.

## Where to run each section

| Section | Local (`make up`) | Stage |
|---|---|---|
| 1. Signup → Welcome → Dashboard | partial (no verification email) | ✅ required |
| 2. Org switching & management | ✅ | optional |
| 3. Team page (invites) | partial (DB only, no email) | ✅ required |
| 4. Inference | ✅ | optional |
| 5. Cross-cutting | ✅ | optional |
| 6. Regression sweep | local for fast iteration; stage before release | both |

The marketing pages (landing, pricing, documentation) are not in the
portal any more: they moved to the public site's own repository
(igni-site) on 2026-10-31, and their checks went with them.

### Why some sections need stage

- **Email delivery.** Locally `SMTP_USERNAME` / `SMTP_PASSWORD` are empty
  and the SMTP service swallows auth errors. Outgoing mail is dropped on
  the floor — invite + verification emails never arrive. The DB row is
  still created (you can watch backend logs for the warning), so the
  *functional* flow is testable locally, but the *delivery* is not.
  See `app/services/email/transports/smtp.py` and `app/services/email/service.py`.

### Prereqs

- Backend: `make up`
- Frontend: `make portal-dev` → http://localhost:5173
- Stage URL: (whatever your stage portal URL is)
- Two real inboxes for invite flows: admin + invitee

---

## 1. Signup → Welcome → Dashboard — **stage required for verification email**

**Signup (`/signup`)**
- [ ] Empty form shows validation errors
- [ ] Weak password rejected
- [ ] Mismatched password confirm rejected
- [ ] Valid signup succeeds → redirects to `/welcome`
- [ ] **(stage)** Verification email arrives in inbox; link works

**Welcome (`/welcome`)**
- [ ] Two install commands visible (igni + CodeIndex)
- [ ] Copy-to-clipboard icons work on both commands
- [ ] "Continue to dashboard" → `/dashboard`

---

## 2. Org switching & management (Sidebar) — local

**As admin of 1 org**
- [ ] Org switcher opens; shows current org
- [ ] **Rename** action visible; renaming updates sidebar + persists on refresh
- [ ] **Delete** action **hidden** (only one org)
- [ ] **Create organization** dialog opens; creating new org auto-switches to it

**As admin of 2+ orgs**
- [ ] Switching between orgs updates active org + invalidates queries
- [ ] Delete action visible; confirmation requires typing org name exactly
- [ ] Deleting org auto-switches to remaining org

**As member (not admin)**
- [ ] Rename + Delete actions **hidden**
- [ ] Create organization **still available**

**User menu**
- [ ] Delete account dialog requires typing email; logs out on success
- [ ] Sign out clears state

---

## 3. Team page — **stage required for invite email**

**As admin**
- [ ] Invite form: empty email → "Email is required"
- [ ] Malformed email (`not-an-email`) → "Enter a valid email address"
- [ ] Valid invite → success toast
  - **Local:** verify DB row created (status=`invited`); backend logs show SMTP warning
  - **Stage:** invitee receives email; invite link works end-to-end
- [ ] Re-inviting existing member → 409 error surfaced
- [ ] Member list shows: role, tier, status, last active

**As member**
- [ ] Invite form is hidden

---

## 4. Inference — local

Needs at least one vLLM node, pinned in the portal or registered.

- [ ] "Capacity in use" shows in-flight of capacity, and "no nodes" with none
- [ ] Each row shows the node, its load, and its model (or "N models")
- [ ] Expanding a row shows address, models, source and health
- [ ] A pinned node offers Edit; a configured or registered one says where to change it
- [ ] Stop serving takes a node out of rotation; Resume puts it back
- [ ] Member: the page is not in the sidebar

---

## 5. Cross-cutting — local

- [ ] No "private beta" copy anywhere in the UI (search the app for it)
- [ ] Hard refresh on any deep route (`/team`, `/settings`, etc.) doesn't kick to login
- [ ] Logout from any page returns to `/`
- [ ] Browser back/forward navigation doesn't break the SPA
- [ ] Dark/light mode toggle (if exposed) re-themes the whole app
- [ ] Responsive layout: 390px (iPhone), 768px (tablet), 1280px (desktop)

---

## 6. Regression sweep — run locally for speed, then on stage before release

5-min click-through, in order:

1. `/signup` → submit → `/welcome` → `/dashboard`
2. Sidebar: switch org, rename, create new, delete
3. Team: invite a fresh email
   - Local: confirm DB + backend log
   - Stage: confirm email lands
4. Inference: stop serving a node, resume it
5. Sign out → log back in → state restored

---

## Time budget

- Local-only pass (sections 2, 4, 5): ~15 min
- Full pass including stage email sections: ~45 min
- Regression sweep: ~5 min

## Notes on automated coverage

Anything covered by `npm test` or `npm run test:e2e` doesn't need to be
re-clicked manually unless visual polish matters. The automated suites
already cover:

- Invite form validation + 409 handling
- Inference page states (no nodes, drained, unhealthy, pinned vs registered)
- Sidebar admin-gating (Rename/Delete visible only to admins)
- Welcome page commands + navigation buttons
- Visual regression (desktop + mobile snapshots)

Focus manual time on what *can't* be automated locally: email delivery
and overall visual polish.
