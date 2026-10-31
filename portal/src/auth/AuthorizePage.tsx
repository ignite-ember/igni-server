/**
 * Signing a person in to a registered client — the `authorization_endpoint`
 * in igni's metadata (docs/service-identity.md §5).
 *
 * Flow:
 * 1. A service sends the browser here with
 *    `?response_type=code&client_id&redirect_uri&state&code_challenge&code_challenge_method=S256`.
 * 2. Not signed in: `AuthRoute` sends the person to /login with this
 *    URL, query and all, as `returnTo`, and sign-in brings them back.
 * 3. `GET /portal/oauth/request` says which application is asking and
 *    which organisations the person can sign in to it as. The server
 *    refuses an application that is not approved, or a redirect address
 *    it did not register.
 * 4. The person picks an organisation (chosen for them if they have
 *    one) and continues; `POST /portal/oauth/authorize` answers with
 *    where to send the browser, carrying a one-time code.
 *
 * **Nothing here redirects on an error.** A refused request is shown on
 * this page and goes no further: sending the browser to a redirect
 * address the server has just refused is exactly what an attacker
 * building the link would want.
 */

import { useState } from 'react'
import { Link as RouterLink, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Banner, Button, FormControl, Heading, Link, Radio, RadioGroup, Spinner, Stack, Text } from '@primer/react'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import { IgniLogo } from '../components/IgniLogo'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import type { AuthorizationAsk } from '../types'
import './AuthorizePage.css'

interface AuthorizationRequest {
  client_id: string
  redirect_uri: string
  state: string
  code_challenge: string
  code_challenge_method: 'S256'
}

/** The request in the query string, or why it cannot be used. */
function readRequest(params: URLSearchParams): { request: AuthorizationRequest } | { problem: string } {
  const clientId = params.get('client_id')
  const redirectUri = params.get('redirect_uri')
  if (!clientId || !redirectUri) {
    return { problem: 'This sign-in link does not say which application is asking, or where to return to.' }
  }
  if (params.get('response_type') !== 'code') {
    return { problem: 'This sign-in link asks for something igni does not offer. Only response_type=code is supported.' }
  }
  const challenge = params.get('code_challenge')
  if (params.get('code_challenge_method') !== 'S256' || !challenge) {
    return { problem: 'This sign-in link has no S256 code challenge, so igni will not use it.' }
  }
  return {
    request: {
      client_id: clientId,
      redirect_uri: redirectUri,
      state: params.get('state') ?? '',
      code_challenge: challenge,
      code_challenge_method: 'S256',
    },
  }
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="authorize-page">
      <div className="authorize-container">
        <Stack gap="spacious">
          <Stack align="center">
            <IgniLogo size={40} />
          </Stack>
          <div className="authorize-card">{children}</div>
        </Stack>
      </div>
    </div>
  )
}

function Refused({ reason }: { reason: string }) {
  return (
    <Frame>
      <Stack gap="normal">
        <Heading as="h1" variant="medium">
          This sign-in cannot continue
        </Heading>
        <Banner variant="critical" title="Refused" description={reason} hideTitle />
        <Text size="small" className="authorize-muted">
          Go back to the application and try again. If it keeps happening, tell whoever runs it.{' '}
          <Link as={RouterLink} to="/dashboard">
            Go to the dashboard
          </Link>
        </Text>
      </Stack>
    </Frame>
  )
}

export function AuthorizePage() {
  useDocumentTitle('Sign in')
  const [params] = useSearchParams()
  const read = readRequest(params)
  const request = 'request' in read ? read.request : null
  const [chosen, setChosen] = useState<string | null>(null)
  const [mismatch, setMismatch] = useState(false)

  const ask = useQuery({
    queryKey: ['oauth-request', request?.client_id, request?.redirect_uri],
    queryFn: () =>
      client
        .get<AuthorizationAsk>('/portal/oauth/request', {
          params: { client_id: request!.client_id, redirect_uri: request!.redirect_uri },
        })
        .then((r) => r.data),
    enabled: request !== null,
    // A refusal is an answer, not a blip: asking again would only show
    // the spinner for longer before saying the same thing.
    retry: false,
    staleTime: 0,
  })

  const authorize = useMutation({
    mutationFn: (organisation: string) =>
      client
        .post<{ redirect_to: string }>('/portal/oauth/authorize', { ...request!, organisation })
        .then((r) => r.data),
    onSuccess: ({ redirect_to }) => {
      // The server built this from a redirect address the application
      // registered. Checked anyway, because it is the one value on this
      // page that leaves igni.
      if (!request || !redirect_to.startsWith(request.redirect_uri)) {
        setMismatch(true)
        return
      }
      window.location.assign(redirect_to)
    },
  })

  if ('problem' in read) return <Refused reason={read.problem} />
  if (ask.isError) {
    return <Refused reason={refusalOf(ask.error, 'igni could not check this sign-in request. Try again.')} />
  }
  if (mismatch) {
    return <Refused reason="igni was about to send you somewhere the application did not register, and stopped." />
  }
  if (!ask.data) {
    return (
      <Frame>
        <Stack align="center" gap="normal">
          <Spinner size="large" />
          <Text className="authorize-muted">Checking the request…</Text>
        </Stack>
      </Frame>
    )
  }

  const { client_name: clientName, organisations } = ask.data
  const selected = chosen ?? (organisations.length === 1 ? organisations[0].org_id : null)
  const leaving = authorize.isPending || authorize.isSuccess

  return (
    <Frame>
      <Stack gap="normal">
        <Heading as="h1" variant="medium">
          Sign in to {clientName}
        </Heading>

        {organisations.length === 0 ? (
          <Text>
            You are not in any organisation on this igni install, so you cannot sign in to {clientName}. Ask an
            administrator to add you to one.
          </Text>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (selected && !leaving) authorize.mutate(selected)
            }}
          >
            <Stack gap="normal">
              <Text className="authorize-muted">
                {clientName} will receive your name, your email address and your role in the organisation you sign
                in as.
              </Text>

              {organisations.length === 1 ? (
                <Text>
                  Signing in as a member of <Text weight="semibold">{organisations[0].name}</Text>.
                </Text>
              ) : (
                <RadioGroup name="organisation" onChange={(value) => setChosen(value)}>
                  <RadioGroup.Label>Organisation</RadioGroup.Label>
                  {organisations.map((org) => (
                    <FormControl key={org.org_id}>
                      <Radio value={org.org_id} checked={selected === org.org_id} />
                      <FormControl.Label>{org.name}</FormControl.Label>
                    </FormControl>
                  ))}
                </RadioGroup>
              )}

              {authorize.isError && (
                <Banner variant="critical" title="Not signed in" description={refusalOf(authorize.error)} />
              )}

              <Button type="submit" variant="primary" block disabled={!selected || leaving}>
                {leaving ? `Returning you to ${clientName}…` : 'Continue'}
              </Button>
            </Stack>
          </form>
        )}
      </Stack>
    </Frame>
  )
}
