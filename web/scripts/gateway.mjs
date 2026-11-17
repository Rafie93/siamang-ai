import { timingSafeEqual } from 'node:crypto'
import { once } from 'node:events'
const STREAM = '/v0/stream'
export function permitted(method, path) {
  if (method === 'GET')
    return /^\/v0\/(health|capabilities|stream|settings\/models|openapi\.json|portfolio|office|projects(?:\/prj_[A-Za-z0-9]+(?:\/(features|intake|usage|drafts(?:\/dft_[A-Za-z0-9]+)?))?)?|tasks(?:\/tsk_[A-Za-z0-9]+(?:\/(events|execution-events|reports|documents|document-edits|messages|usage))?)?|approvals)$/.test(
      path,
    )
  return (
    method === 'POST' &&
    /^\/v0\/(settings\/models|tasks(?:\/tsk_[A-Za-z0-9]+\/(run|cancel|retry|design-decision|work-division|messages|publish|design-edits\/send|documents\/[a-z_]+\/diagrams\/\d+))?|projects(?:\/prj_[A-Za-z0-9]+\/(features|clone|intake\/messages|intake\/approve|drafts(?:\/dft_[A-Za-z0-9]+\/(messages|confirm|discard))?))?|approvals\/req_[A-Za-z0-9]+)$/.test(
      path,
    )
  )
}
export const SESSION_COOKIE = 'web_session'

export function sessionCookie(header) {
  const prefix = `${SESSION_COOKIE}=`
  const part = header
    ?.split(';')
    .map(x => x.trim())
    .find(x => x.startsWith(prefix))
  return part?.slice(prefix.length)
}

export function equal(a, b) {
  return typeof a === 'string' && a.length === b.length && timingSafeEqual(Buffer.from(a), Buffer.from(b))
}
export function trusted(req, host) {
  return (
    req.headers.host === host &&
    (!req.headers.origin || req.headers.origin === `http://${host}`) &&
    !['cross-site', 'same-site'].includes(req.headers['sec-fetch-site'])
  )
}
/** Passes the event stream on as it arrives, for as long as both sides stay. `streams` lets a shutdown end them. */
async function proxyStream(req, res, { backend, token, streams }, url) {
  const abort = new AbortController()
  const connecting = setTimeout(() => abort.abort(), 15000)
  res.on('close', () => abort.abort())
  streams?.add(abort)
  const last = req.headers['last-event-id']
  try {
    const upstream = await fetch(backend + STREAM + url.search, {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'text/event-stream',
        ...(/^\d{1,18}$/.test(last || '') ? { 'Last-Event-ID': last } : {}),
      },
      redirect: 'error',
      signal: abort.signal,
    })
    clearTimeout(connecting)
    if (!upstream.ok) {
      res
        .writeHead(upstream.status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
        .end(await upstream.text())
      return
    }
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-store',
      'X-Accel-Buffering': 'no',
    })
    res.flushHeaders()
    for await (const chunk of upstream.body)
      if (!res.write(chunk)) await once(res, 'drain', { signal: abort.signal })
    res.end()
  } catch {
    if (res.headersSent) res.destroy()
    else
      res
        .writeHead(502, { 'Content-Type': 'application/json' })
        .end(JSON.stringify({ error: { code: 'backend_unavailable' } }))
  } finally {
    clearTimeout(connecting)
    streams?.delete(abort)
  }
}
export async function proxy(req, res, options) {
  const { backend, token } = options
  const url = new URL(req.url, 'http://local')
  const path = url.pathname.slice(4)
  if (!permitted(req.method, path)) {
    res.writeHead(404).end()
    return
  }
  if (path === STREAM) return proxyStream(req, res, options, url)
  try {
    let body
    if (req.method === 'POST') {
      let size = 0
      const chunks = []
      for await (const chunk of req) {
        size += chunk.length
        if (size > 65536) {
          res.writeHead(413).end()
          return
        }
        chunks.push(chunk)
      }
      body = Buffer.concat(chunks)
    }
    const upstream = await fetch(backend + path + url.search, {
      method: req.method,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body,
      redirect: 'error',
      signal: AbortSignal.timeout(15000),
    })
    const text = await upstream.text()
    res
      .writeHead(upstream.status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
      .end(text)
  } catch {
    res
      .writeHead(502, { 'Content-Type': 'application/json' })
      .end(JSON.stringify({ error: { code: 'backend_unavailable' } }))
  }
}
