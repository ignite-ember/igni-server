/**
 * api/client — axios instance with the two interceptors that the entire
 * portal depends on. Every page silently breaks if either of these regress.
 *
 * Request interceptor:
 *  - injects `Authorization: Bearer <token>` from localStorage
 *  - falls back to "bypass" when no token is stored (used for public endpoints
 *    that the server still accepts during local/demo runs)
 *  - injects `X-Org-Id` from localStorage when present
 *
 * Response interceptor:
 *  - on HTTP 401 *from a /portal route*, clears the token AND navigates
 *    to /login — those are the routes carrying this session, so a 401
 *    there means the session is over
 *  - on HTTP 401 from anywhere else, leaves the session alone and warns:
 *    that route wanted different credentials, which is a bug in the
 *    caller rather than an expired login
 *  - on other errors, propagates the rejection unchanged
 */
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import client from '../client'
import { server } from '../../test/server'

// Capture writes to window.location.href without actually navigating.
// jsdom's real location.href is non-configurable; replace `window.location`
// wholesale with a stub that keeps the origin (so MSW can resolve relative
// URLs against it) but treats `href` as a plain mutable string.
const hrefHistory: string[] = []

const stubLocation = {
  href: 'http://localhost/',
  origin: 'http://localhost',
  protocol: 'http:',
  host: 'localhost',
  hostname: 'localhost',
  port: '',
  pathname: '/',
  search: '',
  hash: '',
  assign: () => {},
  replace: () => {},
  reload: () => {},
} as unknown as Location

Object.defineProperty(window, 'location', {
  configurable: true,
  writable: true,
  value: new Proxy(stubLocation, {
    set(target, prop: string, value) {
      if (prop === 'href') hrefHistory.push(value)
      ;(target as unknown as Record<string, unknown>)[prop] = value
      return true
    },
  }),
})

beforeEach(() => {
  localStorage.clear()
  hrefHistory.length = 0
  // Reset href + pathname so MSW can resolve relative URLs even if a prior
  // test wrote into them.
  ;(stubLocation as { href: string }).href = 'http://localhost/'
  ;(stubLocation as { pathname: string }).pathname = '/'
})

// No `afterEach` restoring `location`: the stub stays in place across files
// in the same worker, because restoring re-triggers the non-configurable
// property problem this stub exists to avoid. It is transparent to other
// tests, which do not touch `location.href`.
//
// There was an `afterEach` here containing `if (false) { originalLocation }`
// — dead code whose only job was to stop a variable reading as unused,
// with a lint suppression on top of it. Removing it made the variable
// genuinely unused, which it had been all along: nothing ever restored
// `window.location`, and the dead statement was what hid that.

describe('api/client request interceptor', () => {
  test('attaches Bearer token from localStorage', async () => {
    localStorage.setItem('igni_token', 'tok-xyz')

    let receivedAuth: string | null = null
    server.use(
      http.get('/v1/_test/auth', ({ request }) => {
        receivedAuth = request.headers.get('Authorization')
        return HttpResponse.json({ ok: true })
      }),
    )

    await client.get('/_test/auth')
    expect(receivedAuth).toBe('Bearer tok-xyz')
  })

  test('falls back to "bypass" when no token is stored', async () => {
    let receivedAuth: string | null = null
    server.use(
      http.get('/v1/_test/no-auth', ({ request }) => {
        receivedAuth = request.headers.get('Authorization')
        return HttpResponse.json({ ok: true })
      }),
    )

    await client.get('/_test/no-auth')
    expect(receivedAuth).toBe('Bearer bypass')
  })

  test('attaches X-Org-Id when active_org is set', async () => {
    localStorage.setItem('igni_token', 't')
    localStorage.setItem('igni_active_org', 'org-42')

    let receivedOrg: string | null = null
    server.use(
      http.get('/v1/_test/org', ({ request }) => {
        receivedOrg = request.headers.get('X-Org-Id')
        return HttpResponse.json({ ok: true })
      }),
    )

    await client.get('/_test/org')
    expect(receivedOrg).toBe('org-42')
  })

  test('omits X-Org-Id when no active_org is stored', async () => {
    localStorage.setItem('igni_token', 't')

    let receivedOrg: string | null = null
    server.use(
      http.get('/v1/_test/no-org', ({ request }) => {
        receivedOrg = request.headers.get('X-Org-Id')
        return HttpResponse.json({ ok: true })
      }),
    )

    await client.get('/_test/no-org')
    expect(receivedOrg).toBeNull()
  })
})

describe('api/client response interceptor', () => {
  test('on 401: clears token + redirects to /login', async () => {
    localStorage.setItem('igni_token', 'expired')
    server.use(
      http.get('/v1/portal/_test/401', () =>
        HttpResponse.json({ detail: 'expired' }, { status: 401 }),
      ),
    )

    await expect(client.get('/portal/_test/401')).rejects.toThrow()

    expect(localStorage.getItem('igni_token')).toBeNull()
    expect(hrefHistory).toContain('/login')
  })

  test('on 401: the way back to where the person was goes with them', async () => {
    // A service's sign-in request lives only in the /authorize URL. Bare
    // `/login` dropped it, and the person landed on the dashboard after
    // signing in again with the service still waiting.
    ;(stubLocation as { pathname: string }).pathname = '/authorize'
    ;(stubLocation as { search: string }).search = '?client_id=svc&state=xyz'
    localStorage.setItem('igni_token', 'expired')
    server.use(
      http.get('/v1/portal/oauth/request', () => HttpResponse.json({ detail: 'expired' }, { status: 401 })),
    )

    try {
      await expect(client.get('/portal/oauth/request')).rejects.toThrow()
    } finally {
      ;(stubLocation as { search: string }).search = ''
    }

    expect(hrefHistory.at(-1)).toBe(`/login?returnTo=${encodeURIComponent('/authorize?client_id=svc&state=xyz')}`)
  })

  test('on 401 while already on /login: clears token but does NOT redirect', async () => {
    ;(stubLocation as { pathname: string }).pathname = '/login'
    localStorage.setItem('igni_token', 'expired')
    server.use(
      http.get('/v1/portal/_test/401-on-login', () =>
        HttpResponse.json({ detail: 'bad creds' }, { status: 401 }),
      ),
    )

    await expect(client.get('/portal/_test/401-on-login')).rejects.toThrow()

    expect(localStorage.getItem('igni_token')).toBeNull()
    // No redirect — the login form's own error banner handles UX.
    expect(hrefHistory).toEqual([])
  })

  test('on 500: propagates error WITHOUT clearing the token', async () => {
    localStorage.setItem('igni_token', 'still-valid')
    server.use(
      http.get('/v1/_test/500', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    )

    await expect(client.get('/_test/500')).rejects.toThrow()

    expect(localStorage.getItem('igni_token')).toBe('still-valid')
    expect(hrefHistory).toEqual([])
  })

  test('on 200: passes the response through', async () => {
    server.use(
      http.get('/v1/_test/ok', () => HttpResponse.json({ value: 7 })),
    )

    const res = await client.get<{ value: number }>('/_test/ok')
    expect(res.data).toEqual({ value: 7 })
  })
})

describe('401 handling is scoped to the session', () => {
  /**
   * The portal's token is a portal token. A 401 from `/portal/*` means
   * the server rejected *us* and the session is over. A 401 from
   * anywhere else means that route wanted different credentials — the
   * `/chat/*` endpoints authenticate a CLI user's JWT, and asking them
   * with a portal session is a bug in the caller.
   *
   * Treating the two alike is not theoretical. A component that called
   * `/chat/models` to fill a dropdown logged every admin out of the
   * group page, on a request they never made and could not see.
   */
  beforeEach(() => {
    localStorage.setItem('igni_token', 'a-real-session')
    hrefHistory.length = 0
  })

  test('a portal 401 ends the session', async () => {
    server.use(http.get('/v1/portal/me', () => new HttpResponse(null, { status: 401 })))

    await expect(client.get('/portal/me')).rejects.toBeDefined()

    expect(localStorage.getItem('igni_token')).toBeNull()
    expect(hrefHistory.at(-1)).toBe('/login')
  })

  test('a 401 from a differently-authenticated route does not', async () => {
    server.use(http.get('/v1/chat/models', () => new HttpResponse(null, { status: 401 })))

    await expect(client.get('/chat/models')).rejects.toBeDefined()

    expect(localStorage.getItem('igni_token')).toBe('a-real-session')
    expect(hrefHistory).toHaveLength(0)
  })

  test('the mistake is reported rather than swallowed', async () => {
    // Otherwise the request fails, the component renders an empty state,
    // and nothing anywhere says why.
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    server.use(http.get('/v1/chat/models', () => new HttpResponse(null, { status: 401 })))

    await expect(client.get('/chat/models')).rejects.toBeDefined()

    expect(warn).toHaveBeenCalledWith(expect.stringContaining('does not use the portal session'))
    warn.mockRestore()
  })

  test('a non-401 failure on a portal route leaves the session alone', async () => {
    server.use(http.get('/v1/portal/team', () => new HttpResponse(null, { status: 500 })))

    await expect(client.get('/portal/team')).rejects.toBeDefined()

    expect(localStorage.getItem('igni_token')).toBe('a-real-session')
  })
})
