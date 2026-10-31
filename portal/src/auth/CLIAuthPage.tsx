/**
 * CLI authentication page — styled to match the landing page.
 *
 * Flow:
 * 1. CLI starts a local HTTP server and opens the browser to:
 *    /cli-auth?port=XXXX
 * 2. If not logged in, ProtectedRoute redirects to /login, which redirects
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

import { useCallback, useEffect, useRef, useState } from 'react'
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
import type { UserMe, OrgSummary } from '../types'
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
        .post<{ access_token: string; handoff_code: string }>('/cli/token', { org_id: orgId })
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

  // Auto-select if single org
  useEffect(() => {
    if (meQuery.data && orgs.length === 1 && !tokenMutation.isPending && !done) {
      tokenMutation.mutate(orgs[0].id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meQuery.data])

  // ── Fireworks canvas ──
  const canvasRef = useRef<HTMLCanvasElement>(null)

  const startFireworks = useCallback((canvas: HTMLCanvasElement) => {
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    canvas.width = window.innerWidth
    canvas.height = window.innerHeight
    const W = canvas.width
    const H = canvas.height
    const colors = ['#F97316', '#DC2626', '#FBBF24', '#f85149', '#f0883e', '#FF6B35']

    const bg = getComputedStyle(document.body).backgroundColor || 'rgb(255,255,255)'
    const m = bg.match(/\d+/g)
    const trailColor = m ? `rgba(${m[0]},${m[1]},${m[2]},0.25)` : 'rgba(255,255,255,0.25)'

    type Spark = { x: number; y: number; vx: number; vy: number; r: number; color: string; life: number; maxLife: number }
    const sparks: Spark[] = []

    function burst(x: number, y: number) {
      for (let i = 0; i < 80; i++) {
        const a = Math.random() * Math.PI * 2
        const s = 2 + Math.random() * 5
        sparks.push({ x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s, life: 80 + Math.random() * 40, maxLife: 80 + Math.random() * 40, age: 0, r: 1.5 + Math.random() * 2, color: colors[Math.floor(Math.random() * 6)] } as Spark & { age: number })
      }
    }

    type Rocket = { x: number; y: number; targetY: number; vy: number; delay: number; done: boolean }
    const launches: Rocket[] = Array.from({ length: 6 }, (_, i) => ({
      x: W * 0.15 + Math.random() * W * 0.7,
      y: H,
      targetY: H * 0.1 + Math.random() * H * 0.3,
      vy: -12 - Math.random() * 4,
      delay: i * 25,
      done: false,
    }))

    let frame = 0
    const animate = () => {
      ctx.fillStyle = trailColor
      ctx.fillRect(0, 0, W, H)

      for (const l of launches) {
        if (frame < l.delay || l.done) continue
        l.y += l.vy
        l.vy += 0.15
        ctx.fillStyle = '#FBBF24'
        ctx.globalAlpha = 1
        ctx.beginPath(); ctx.arc(l.x, l.y, 3, 0, Math.PI * 2); ctx.fill()
        if (l.y <= l.targetY || l.vy >= 0) {
          burst(l.x, l.y)
          l.done = true
        }
      }

      for (const s of sparks) {
        if (s.life <= 0) continue
        s.life--
        s.x += s.vx; s.y += s.vy
        s.vy += 0.1; s.vx *= 0.98
        const t = 1 - s.life / s.maxLife
        ctx.globalAlpha = 1 - t
        ctx.fillStyle = t < 0.15 ? '#FBBF24' : s.color
        ctx.beginPath(); ctx.arc(s.x, s.y, s.r * (1 - t * 0.5), 0, Math.PI * 2); ctx.fill()
      }

      ctx.globalAlpha = 1
      frame++
      requestAnimationFrame(animate)
    }
    requestAnimationFrame(animate)
  }, [])

  useEffect(() => {
    if (done && canvasRef.current) {
      startFireworks(canvasRef.current)
    }
  }, [done, startFireworks])

  // ── Error: missing port ──
  //
  // Below every hook, not above them. This return used to sit before
  // `useRef` and `useCallback`, so the number of hooks React saw
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
        <canvas ref={canvasRef} className="cli-auth-fireworks-canvas" />
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
          <ShieldLockIcon size={12} /> <strong>Secure connection</strong> — your token is sent directly to the CLI on localhost.
        </Text>
      </div>
    </div>
  )
}
