import { it, expect, vi, afterEach } from 'vitest'
import { request, items } from '../src/services/api.js'
afterEach(() => vi.unstubAllGlobals())
it('rejects malformed list data', () => expect(() => items({ items: null })).toThrow('malformed_response'))
it('normalizes disconnected transport', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('secret internal details')))
  await expect(request('/health')).rejects.toMatchObject({ code: 'disconnected' })
})
