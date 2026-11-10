/**
 * CLI authentication page — styled to match the landing page.
 *
 * Flow:
 * 1. CLI starts a local HTTP server and opens the browser to:
 *    /cli-auth?port=XXXX
 * 2. If not logged in, AuthRoute redirects to /login, which redirects
 *    back here after auth (via returnTo query param).
 * 3. This page fetches the user's orgs from /portal/me.
 * 4. Single org → auto-select.  Multiple → user picks.
 * 5. Calls POST /v1/cli/token with the selected org_id to get an org-scoped JWT.
 * 6. Redirects to http://127.0.0.1:{port}/callback?code=... so the CLI
 *    can exchange it. A *code*, not the token: this line is a browser
 *    navigation, and a token in a navigation is in the address bar and
 *    the history for as long as the profile lives. F107 removed exactly
 *    that from the browser sign-in redirect and this path kept it — a
 *    working org-scoped 30-day credential, in history, on every desktop
 *    login. F124.
 */

import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  Banner,
  Spinner,
  Text,
} from '@primer/react'
import { ChevronRightIcon, CheckCircleIcon, ShieldLockIcon, TerminalIcon } from '@primer/octicons-react'
import client from '../api/client'
import { IgniLogo } from '../components/IgniLogo'
import type { CLITokenResponse, UserMe, OrgSummary } from '../types'
import { Fireworks } from './Fireworks'
import './CLIAuthPage.css'

export function CLIAuthPage() {
  const [searchParams] = useSearchParams()
  const port = searchParams.get('port')
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
    staleTime: 0,
  })

  const orgs = meQuery.data?.organizations ?? []

  const tokenMutation = useMutation({
    mutationFn: (orgId: string) =>
      client
        .post<CLITokenResponse>('/cli/token', { org_id: orgId })
        .then((r) => r.data),
    onSuccess: (data) => {
      if (!port) {
        setError('Missing callback port — please restart the CLI login flow.')
        return
      }
      setDone(true)
      // 127.0.0.1 rather than localhost. The CLI's callback server binds
      // 127.0.0.1, and `localhost` resolves to ::1 first on a dual-stack
      // machine — which is a connection refused, on a step the user
      // cannot retry without starting the whole login again.
      window.location.href = `http://127.0.0.1:${port}/callback?code=${encodeURIComponent(data.handoff_code)}`
    },
    onError: () => {
      setError('Failed to generate CLI token. Please try again.')
    },
  })

  const handleSelect = (orgId: string) => {
    if (tokenMutation.isPending) return
    tokenMutation.mutate(orgId)
  }

  // Auto-select if single org. Only from idle: once the mutation has run,
  // succeeded or failed, a refetch of /portal/me must not fire it again.
  const onlyOrgId = orgs.length === 1 ? orgs[0].id : null
  const { mutate, isIdle } = tokenMutation
  useEffect(() => {
    if (onlyOrgId && isIdle) mutate(onlyOrgId)
  }, [onlyOrgId, isIdle, mutate])

  // ── Error: missing port ──
  //
  // Below every hook, not above them. This return used to sit before
  // the page's later hooks, so the number of hooks React saw
  // depended on whether `?port=` was present — and that is not a
  // hypothetical: `port` comes from the search params, so navigating
  // from `/cli-auth?port=51234` to `/cli-auth` re-renders this same
  // component instance with one fewer hook and React throws "Rendered
  // fewer hooks than expected". Every conditional return has to come
  // after the last hook call, which is why this one is here and not
  // where it reads most naturally.
  if (!port) {
    return (
      <div className="cli-auth-page">
        <div className="cli-auth-container">
          <div className="cli-auth-logo"><IgniLogo size={48} /></div>
          <h1 className="cli-auth-title">Invalid Request</h1>
          <p className="cli-auth-subtitle">Missing port parameter. Please restart the CLI login flow.</p>
        </div>
      </div>
    )
  }

  // ── Success ──
  if (done) {
    return (
      <div className="cli-auth-page">
        <Fireworks className="cli-auth-fireworks-canvas" />
        <div className="cli-auth-container">
          <div className="cli-auth-success-icon">
            <CheckCircleIcon size={32} fill="#F97316" />
          </div>
          <h2 className="cli-auth-success-title">CLI Authenticated</h2>
          <p className="cli-auth-success-hint">
            You're all set. Return to your terminal to start using igni.
          </p>
          <div className="cli-auth-terminal-hint">
            <TerminalIcon size={16} />
            You can close this window
          </div>
        </div>
      </div>
    )
  }

  // ── Loading ──
  if (meQuery.isLoading || (orgs.length <= 1 && !error)) {
    return (
      <div className="cli-auth-page">
        <div className="cli-auth-container">
          <div className="cli-auth-logo"><IgniLogo size={48} /></div>
          <Spinner size="large" />
          <p className="cli-auth-subtitle" style={{ marginTop: 16 }}>Authorizing...</p>
        </div>
      </div>
    )
  }

  // ── Org selection ──
  return (
    <div className="cli-auth-page">
      <div className="cli-auth-container">
        <div className="cli-auth-logo"><IgniLogo size={48} /></div>

        <h1 className="cli-auth-title">
          Authorize <span className="cli-auth-gradient">igni</span>
        </h1>

        <p className="cli-auth-subtitle">
          Select the organization to connect to the CLI.
        </p>

        {error && (
          <div className="cli-auth-error">
            <Banner variant="critical" title={error} onDismiss={() => setError(null)} />
          </div>
        )}

        <div className="cli-auth-card">
          {orgs.map((org: OrgSummary, i: number) => (
            <button
              key={org.id}
              className={`cli-auth-row${i < orgs.length - 1 ? ' cli-auth-row-border' : ''}`}
              onClick={() => handleSelect(org.id)}
              disabled={tokenMutation.isPending}
            >
              <span className="cli-auth-row-icon">
                {org.display_name.charAt(0).toUpperCase()}
              </span>
              <span className="cli-auth-row-info">
                <span className="cli-auth-row-name">{org.display_name}</span>
                <span className="cli-auth-row-meta">{org.slug} &middot; {org.role}</span>
              </span>
              {tokenMutation.isPending ? (
                <Spinner size="small" />
              ) : (
                <ChevronRightIcon size={16} className="cli-auth-chevron" />
              )}
            </button>
          ))}
        </div>

        <Text as="p" className="cli-auth-security">
          <ShieldLockIcon size={12} /> <strong>Secure connection</strong> — a one-time code is handed to the CLI on localhost.
        </Text>
      </div>
    </div>
  )
}
