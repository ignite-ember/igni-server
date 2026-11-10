import { describe, expect, it } from 'vitest'
import { oauthStartUrl, ssoStartUrl } from '../providerStart'

const methods = { password: true, oauth_providers: [], sso: { enabled: true, label: 'SSO', start_url: '/v1/auth/oidc' } }

describe('provider start URLs', () => {
  it('carries returnTo through the provider round trip', () => {
    expect(oauthStartUrl('google', '/cli-auth?port=51234')).toBe('/v1/auth/google?returnTo=%2Fcli-auth%3Fport%3D51234')
    expect(ssoStartUrl(methods, '/dashboard')).toBe('/v1/auth/oidc?returnTo=%2Fdashboard')
  })

  it('leaves out the destination the server would choose anyway', () => {
    expect(oauthStartUrl('google', '/select-org')).toBe('/v1/auth/google')
    expect(ssoStartUrl(methods, '/select-org')).toBe('/v1/auth/oidc')
  })
})
