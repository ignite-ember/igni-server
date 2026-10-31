import { describe, expect, it } from 'vitest'
import { DEFAULT_RETURN_TO, safeReturnTo } from '../returnTo'

describe('safeReturnTo', () => {
  it('keeps the destinations the application actually uses', () => {
    for (const path of ['/dashboard', '/cli-auth?port=51234', '/groups/abc#members', '/select-org']) {
      expect(safeReturnTo(path)).toBe(path)
    }
  })

  it('falls back when there is nothing to go back to', () => {
    for (const empty of [null, undefined, '']) {
      expect(safeReturnTo(empty)).toBe(DEFAULT_RETURN_TO)
    }
  })

  it('refuses every form a browser resolves to another origin', () => {
    // Measured against react-router 7.18.3, which throws
    // "External navigation is not allowed" for all of these. The point
    // of this rule is that it does not depend on the router continuing
    // to do that — and a fallback beats an exception either way.
    for (const hostile of [
      '//evil.example.com',
      '///evil.example.com',
      '/\\evil.example.com',
      '/\\\\evil.example.com',
      'https://evil.example.com',
      'http://evil.example.com',
      '//evil.example.com/dashboard',
    ]) {
      expect(safeReturnTo(hostile)).toBe(DEFAULT_RETURN_TO)
    }
  })

  it('refuses a scheme that executes rather than navigates', () => {
    for (const hostile of ['javascript:alert(1)', 'data:text/html,<script>alert(1)</script>', 'vbscript:msgbox']) {
      expect(safeReturnTo(hostile)).toBe(DEFAULT_RETURN_TO)
    }
  })

  it('refuses a leading control character, which a browser would strip', () => {
    // `\t` and `\n` are removed by URL parsing, so `/\t/evil.example.com`
    // becomes `//evil.example.com` *after* a naive check has passed it.
    for (const hostile of ['/\t/evil.example.com', '/\n/evil.example.com', '/ /evil.example.com']) {
      expect(safeReturnTo(hostile)).toBe(DEFAULT_RETURN_TO)
    }
  })

  it('refuses a bare relative path, which resolves against the current directory', () => {
    expect(safeReturnTo('dashboard')).toBe(DEFAULT_RETURN_TO)
    expect(safeReturnTo('../admin')).toBe(DEFAULT_RETURN_TO)
  })
})
