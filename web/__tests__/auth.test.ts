import { getToken, setToken, clearToken } from '../lib/auth'

// Tests run in Node (no `window`), so helpers must be SSR-safe no-ops.
describe('auth token helpers — SSR environment', () => {
  it('getToken returns null when window is undefined', () => {
    expect(getToken()).toBeNull()
  })

  it('setToken does not throw without window', () => {
    expect(() => setToken('tok_abc123')).not.toThrow()
  })

  it('clearToken does not throw without window', () => {
    expect(() => clearToken()).not.toThrow()
  })

  it('getToken still returns null after setToken in SSR context', () => {
    setToken('tok_abc123')
    expect(getToken()).toBeNull()
  })
})
