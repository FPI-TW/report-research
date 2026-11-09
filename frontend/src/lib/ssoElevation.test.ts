import { describe, expect, it } from 'vitest'
import { isSsoSession, ssoElevateUrl } from './ssoElevation'

describe('ssoElevation', () => {
  it('builds the IdP re-authentication URL that returns to the current page', () => {
    expect(ssoElevateUrl({ pathname: '/app/admin/users', search: '?q=a&b=1' }))
      .toBe('/login/oidc?intent=elevate&next=%2Fapp%2Fadmin%2Fusers%3Fq%3Da%26b%3D1')
  })

  it('only treats sso as an SSO session', () => {
    expect(isSsoSession('sso')).toBe(true)
    expect(isSsoSession('password')).toBe(false)
    expect(isSsoSession(undefined)).toBe(false)
  })
})
