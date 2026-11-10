/**
 * Default MSW request handlers — match the real backend just well enough
 * that components mount happily. Individual tests entry these with
 * `server.use(...)` when they need specific responses.
 */
import { http, HttpResponse } from 'msw'

const ME_DEFAULT = {
  user_id: 'demo-user-001',
  email: 'alex@ember.sh',
  full_name: 'Alex Chen',
  created_at: '2026-01-01T00:00:00Z',
  role: 'admin',
  pending_email: null,
  auth_provider: 'email',
  org_id: 'demo-org-001',
  org_slug: 'demo',
  org_display_name: 'Demo Org',
  organizations: [
    { id: 'demo-org-001', slug: 'demo', display_name: 'Demo Org', role: 'admin' },
  ],
}

const UNLICENSED_MODULES = (
  [
    ['codeindex', 'CodeIndex'],
    ['artifacts', 'Artifacts'],
    ['fleet', 'Fleet'],
    ['factory', 'The Factory'],
    ['autopilot', 'Autopilot'],
  ] as const
).map(([module, name]) => ({
  module,
  state: 'unlicensed',
  reason: 'no_licence',
  licence_id: null,
  customer: null,
  kind: null,
  users: null,
  expires: null,
  read_only_from: null,
  days_left: null,
  message: `${name} is not licensed on this deployment. No licence is installed.`,
}))

/** `GET /portal/licence` on a deployment with no licence file. */
export const NO_LICENCE = {
  path: '/etc/igni/licence/licence.json',
  installed: false,
  problem: 'no_licence',
  licence_id: null,
  customer: null,
  kind: null,
  issued: null,
  modules: UNLICENSED_MODULES,
}

export const defaultHandlers = [
  http.get('/v1/portal/me', () => HttpResponse.json(ME_DEFAULT)),

  // A deployment with local accounts only: what the chart installs by
  // default, and every air-gapped one (no Google, no SSO).
  http.get('/v1/auth/methods', () =>
    HttpResponse.json({
      password: true,
      oauth_providers: [],
      sso: { enabled: false, label: 'SSO', start_url: '/v1/auth/oidc' },
    }),
  ),

  // The free base with no paid modules: nothing to say about a licence,
  // and nobody to name as its licensee.
  http.get('/v1/portal/licence/notice', () =>
    HttpResponse.json({ warnings: [], customer: null, licence_id: null }),
  ),
  // What a super admin sees there: no file, and every module unlicensed.
  http.get('/v1/portal/licence', () => HttpResponse.json(NO_LICENCE)),

  // A deployment its startup checks have nothing to say about.
  http.get('/v1/portal/deployment/notices', () => HttpResponse.json({ notices: [] })),

  http.get('/v1/portal/team', () =>
    HttpResponse.json({
      members: [
        {
          id: 'm-1',
          user_id: 'demo-user-001',
          email: 'alex@ember.sh',
          full_name: 'Alex Chen',
          role: 'admin',
          status: 'active',
          total_requests: 0,
          total_tokens: 0,
          last_active: null,
          joined_at: '2026-01-01T00:00:00Z',
          assigned_by: null,
        },
      ],
      total: 1,
    }),
  ),

  http.get('/v1/portal/groups', () =>
    HttpResponse.json({ groups: [], total: 0 }),
  ),
  http.get('/v1/portal/groups/:groupId', () =>
    HttpResponse.json({
      id: 'g-1',
      org_id: 'demo-org-001',
      name: 'Engineering',
      description: 'Engineering team',
      created_by_user_id: 'demo-user-001',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      member_count: 0,
      entry_count: 0,
    }),
  ),
  http.get('/v1/portal/groups/:groupId/members', () =>
    HttpResponse.json({ members: [], total: 0 }),
  ),
  http.get('/v1/portal/group-memberships', () => HttpResponse.json({ memberships: [] })),
  http.get('/v1/portal/groups/:groupId/entries', () =>
    HttpResponse.json({ entries: [], total: 0 }),
  ),
  http.get('/v1/portal/me/group', () => HttpResponse.json(null)),
  // No app or extension has asked for a VM profile.
  http.get('/v1/portal/orgs/:orgId/vm-profile-requests', () => HttpResponse.json({ requests: [] })),
  http.get('/v1/portal/me/group/pack', () => HttpResponse.json(null)),

  // The inference page polls this; a deployment with no GPUs configured
  // is a legitimate state, so the default is an empty fleet rather than
  // an error.
  http.get('/v1/portal/inference', () =>
    HttpResponse.json({
      nodes: [],
      total_in_flight: 0,
      total_capacity: 0,
      utilization: 0,
      registration_enabled: false,
      heartbeat_interval_seconds: 60,
    }),
  ),
]
