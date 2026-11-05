import { validResponse } from './contract.js'
let csrf
export class ApiError extends Error {
  constructor(code, status = 0) {
    super(code)
    this.code = code
    this.status = status
  }
}
export async function request(path, options = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15000)
  try {
    if (!csrf) {
      const session = await fetch('/session', { signal: controller.signal })
      if (!session.ok) throw new ApiError('session_required', session.status)
      csrf = (await session.json()).csrf
    }
    const response = await fetch('/api/v0' + path, {
      ...options,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', 'X-Web-CSRF': csrf, ...options.headers },
    })
    let data
    try {
      data = await response.json()
    } catch {
      throw new ApiError('malformed_response', response.status)
    }
    if (!response.ok) {
      if (response.status === 403) csrf = undefined
      throw new ApiError(data.error?.code || 'request_failed', response.status)
    }
    if (!validResponse(path, options.method || 'GET', data)) throw new ApiError('malformed_response')
    return data
  } catch (e) {
    if (e instanceof ApiError) throw e
    throw new ApiError(e.name === 'AbortError' ? 'timeout' : 'disconnected')
  } finally {
    clearTimeout(timer)
  }
}
export function items(data) {
  if (!Array.isArray(data.items)) throw new ApiError('malformed_response')
  return data.items
}
export const post = (path, body = {}) => request(path, { method: 'POST', body: JSON.stringify(body) })
