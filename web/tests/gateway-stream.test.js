// @vitest-environment node
import http from 'node:http'
import { once } from 'node:events'
import { afterEach, describe, expect, it } from 'vitest'
import { permitted, proxy } from '../scripts/gateway.mjs'

const TOKEN = 'backend-token-never-for-browsers'
const open = []
async function listen(handler) {
  const server = http.createServer(handler)
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  open.push(server)
  return `http://127.0.0.1:${server.address().port}`
}
afterEach(async () => {
  for (const server of open.splice(0)) {
    server.closeAllConnections()
    server.close()
  }
})
function gateway(backend, streams) {
  return listen((req, res) => proxy(req, res, { backend, token: TOKEN, streams }))
}
const until = async (check, ms = 3000) => {
  const deadline = Date.now() + ms
  while (!check()) {
    if (Date.now() > deadline) throw Error('condition not met in time')
    await new Promise(resolve => setTimeout(resolve, 10))
  }
}

describe('stream proxy', () => {
  it('opens only this one read route', () => {
    expect(permitted('GET', '/v0/stream')).toBe(true)
    expect(permitted('POST', '/v0/stream')).toBe(false)
    expect(permitted('GET', '/v0/stream/x')).toBe(false)
  })
  it('passes each event on as it arrives, not when the stream ends', async () => {
    let upstream
    const backend = await listen((req, res) => {
      upstream = res
      res.writeHead(200, { 'Content-Type': 'text/event-stream' })
      res.write('event: ready\ndata: {}\n\n')
    })
    const response = await fetch((await gateway(backend)) + '/api/v0/stream')
    expect(response.status).toBe(200)
    expect(response.headers.get('content-type')).toBe('text/event-stream')
    expect(response.headers.get('cache-control')).toBe('no-store')
    const reader = response.body.getReader()
    const text = async () => new TextDecoder().decode((await reader.read()).value)
    expect(await text()).toContain('event: ready')
    upstream.write('event: ping\ndata: {}\n\n') // still open: this is only here if nothing was buffered
    expect(await text()).toContain('event: ping')
    await reader.cancel()
  })
  it('keeps the stream past the timeout other routes have', async () => {
    // A plain request is cut at 15 seconds; here, the connect timer must be cleared once headers arrive.
    let upstream
    const backend = await listen((req, res) => {
      upstream = res
      res.writeHead(200, { 'Content-Type': 'text/event-stream' })
      res.write(': open\n\n')
    })
    const controllers = new Set()
    const response = await fetch((await gateway(backend, controllers)) + '/api/v0/stream')
    await response.body.getReader().read()
    expect(controllers.size).toBe(1)
    upstream.end()
  })
  it('forwards a numeric resume id and the query, adds the token, and keeps the token from the browser', async () => {
    const seen = []
    const backend = await listen((req, res) => {
      seen.push({ url: req.url, id: req.headers['last-event-id'], auth: req.headers.authorization })
      res.writeHead(200, { 'Content-Type': 'text/event-stream' })
      res.end('event: ready\ndata: {}\n\n')
    })
    const url = (await gateway(backend)) + '/api/v0/stream?after=4'
    const first = await fetch(url, { headers: { 'Last-Event-ID': '77' } })
    expect(await first.text()).not.toContain(TOKEN)
    expect([...first.headers.values()].join()).not.toContain(TOKEN)
    await (
      await fetch(url, { headers: { 'Last-Event-ID': '1\r\nX-Evil: 1' } }).catch(() => fetch(url))
    ).text()
    await (await fetch(url, { headers: { 'Last-Event-ID': 'abc' } })).text()
    expect(seen[0]).toEqual({ url: '/v0/stream?after=4', id: '77', auth: `Bearer ${TOKEN}` })
    expect(seen.at(-1).id).toBeUndefined()
  })
  it('ends the backend stream when the browser leaves, and forgets it', async () => {
    let gone = false
    const backend = await listen((req, res) => {
      res.writeHead(200, { 'Content-Type': 'text/event-stream' })
      res.write(': open\n\n')
      res.on('close', () => (gone = true))
    })
    const streams = new Set()
    const abort = new AbortController()
    const response = await fetch((await gateway(backend, streams)) + '/api/v0/stream', {
      signal: abort.signal,
    })
    await response.body.getReader().read()
    abort.abort()
    await until(() => gone && streams.size === 0)
  })
  it('lets a shutdown end open streams', async () => {
    let gone = false
    const backend = await listen((req, res) => {
      res.writeHead(200, { 'Content-Type': 'text/event-stream' })
      res.write(': open\n\n')
      res.on('close', () => (gone = true))
    })
    const streams = new Set()
    const response = await fetch((await gateway(backend, streams)) + '/api/v0/stream')
    const reader = response.body.getReader()
    await reader.read()
    for (const stream of streams) stream.abort()
    await until(() => gone)
    await reader.read().catch(() => {})
  })
  it('passes a refusal on with its status, so the browser stops and the page keeps polling', async () => {
    const backend = await listen((req, res) => {
      res.writeHead(503, { 'Content-Type': 'application/json' })
      res.end('{"error":{"code":"stream_unavailable"}}')
    })
    const response = await fetch((await gateway(backend)) + '/api/v0/stream')
    expect(response.status).toBe(503)
    expect(response.headers.get('content-type')).toBe('application/json')
    expect((await response.json()).error.code).toBe('stream_unavailable')
  })
  it('says so when the backend is gone', async () => {
    const response = await fetch((await gateway('http://127.0.0.1:9')) + '/api/v0/stream')
    expect(response.status).toBe(502)
    expect((await response.json()).error.code).toBe('backend_unavailable')
  })
  it('closes the page connection when the backend ends, so the browser reconnects', async () => {
    const backend = await listen((req, res) => {
      res.writeHead(200, { 'Content-Type': 'text/event-stream' })
      res.end('event: ready\ndata: {}\n\n')
    })
    const response = await fetch((await gateway(backend)) + '/api/v0/stream')
    expect(await response.text()).toContain('event: ready')
  })
  it('leaves ordinary routes as they were', async () => {
    const backend = await listen((req, res) => {
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end('{"status":"healthy"}')
    })
    const response = await fetch((await gateway(backend)) + '/api/v0/health')
    expect(await response.json()).toEqual({ status: 'healthy' })
  })
})
