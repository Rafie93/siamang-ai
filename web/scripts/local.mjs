import http from 'node:http'
import { spawn, execFileSync } from 'node:child_process'
import { randomBytes, createHash } from 'node:crypto'
import { readFileSync, writeFileSync, mkdirSync, existsSync, chmodSync } from 'node:fs'
import { homedir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { proxy, trusted, equal, sessionCookie, SESSION_COOKIE } from './gateway.mjs'
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
process.chdir(root)
// Projects must never live inside Siamang's own repository, so the data folder stays outside it (see WEB_DATA).
const backendRoot = path.resolve(process.env.SIAMANG_ROOT || path.join(root, '..'))
if (!existsSync(path.join(backendRoot, 'pyproject.toml'))) {
  console.error(`Backend not found at ${backendRoot}. Set SIAMANG_ROOT.`)
  process.exit(1)
}
const data = path.resolve(process.env.WEB_DATA || path.join(homedir(), '.siamang', 'web'))
mkdirSync(data, { recursive: true, mode: 0o700 })
chmodSync(data, 0o700)
const keyFile = path.join(data, 'seal-key')
if (!existsSync(keyFile)) writeFileSync(keyFile, randomBytes(32).toString('hex'), { mode: 0o600 })
const token = process.env.SIAMANG_TOKEN || randomBytes(32).toString('hex')
const port = Number(process.env.PORT || 5180),
  apiPort = Number(process.env.SIAMANG_PORT || 0),
  host = `127.0.0.1:${port}`
let backend
const env = {
  ...process.env,
  PYTHONDONTWRITEBYTECODE: '1',
  WEB_DATA: data,
  SIAMANG_CONFIG: path.join(backendRoot, 'siamang.toml'),
  SIAMANG_STORAGE_PATH: path.join(data, 'office.db'),
  SIAMANG_API_SEAL_KEY: readFileSync(keyFile, 'utf8').trim(),
  SIAMANG_API_IDENTITIES: JSON.stringify({
    local: {
      token_sha256: createHash('sha256').update(token).digest('hex'),
      permissions: ['read', 'submit', 'run', 'cancel', 'retry', 'approve', 'configure'],
      projects: ['*'],
    },
  }),
  SIAMANG_API_ORIGINS: '[]',
}
const python = path.join(backendRoot, '.venv/bin/python')
if (!existsSync(python)) {
  console.error(
    `Python environment not found: ${python}\nCreate it first; see the Quick Start in the README.`,
  )
  process.exit(1)
}
execFileSync(python, [path.join(root, 'scripts/setup.py')], { env, cwd: backendRoot, stdio: 'inherit' })
// Refuse an occupied backend port rather than connecting to an unrelated service.
const probe = http.createServer()
await new Promise((resolve, reject) => {
  probe.once('error', reject)
  probe.listen(apiPort, '127.0.0.1', resolve)
})
const selectedPort = probe.address().port
backend = `http://127.0.0.1:${selectedPort}`
await new Promise(r => probe.close(r))
const child = spawn(python, ['-m', 'siamang.api.main', '--port', String(selectedPort)], {
  env,
  cwd: backendRoot,
  stdio: ['ignore', 'inherit', 'inherit'],
})
process.on('exit', () => child.kill('SIGTERM'))
process.on('uncaughtException', () => {
  child.kill('SIGTERM')
  console.error('Local startup failed; check ports and configuration.')
  process.exit(1)
})
let vite
const production = process.argv.includes('--production')
if (!production) {
  const { createServer } = await import('vite')
  vite = await createServer({
    server: { host: '127.0.0.1', middlewareMode: true, hmr: { host: '127.0.0.1', port: port + 10000 } },
    appType: 'spa',
  })
}
const session = randomBytes(32).toString('hex'),
  csrf = randomBytes(32).toString('hex')
const server = http.createServer(async (req, res) => {
  res.setHeader(
    'Content-Security-Policy',
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' ws://127.0.0.1:" +
      (port + 10000) +
      "; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
  )
  res.setHeader('X-Content-Type-Options', 'nosniff')
  res.setHeader('Referrer-Policy', 'no-referrer')
  res.setHeader('Cache-Control', 'no-store')
  if (!trusted(req, host)) {
    res.writeHead(403).end('Local origin required')
    return
  }
  const url = new URL(req.url, `http://${host}`)
  if (url.pathname === '/session' && req.method === 'GET') {
    res.setHeader('Set-Cookie', `${SESSION_COOKIE}=${session}; HttpOnly; SameSite=Strict; Path=/`)
    res.setHeader('Content-Type', 'application/json')
    res.end(JSON.stringify({ csrf, mode: 'local-development' }))
    return
  }
  if (url.pathname.startsWith('/api/')) {
    const cookie = sessionCookie(req.headers.cookie)
    if (
      !equal(cookie, session) ||
      (req.method === 'POST' &&
        (!equal(req.headers['x-web-csrf'], csrf) || req.headers.origin !== `http://${host}`))
    ) {
      res.writeHead(403, { 'Content-Type': 'application/json' }).end('{"error":{"code":"session_required"}}')
      return
    }
    return proxy(req, res, { backend, token })
  }
  if (vite) {
    vite.middlewares(req, res)
    return
  }
  if (req.method !== 'GET') {
    res.writeHead(405).end()
    return
  }
  let pathname
  try {
    pathname = decodeURIComponent(url.pathname)
  } catch {
    res.writeHead(400).end()
    return
  }
  const dist = path.join(root, 'dist'),
    file = path.resolve(dist, '.' + pathname)
  if (!file.startsWith(dist + path.sep) && file !== dist) {
    res.writeHead(403).end()
    return
  }
  try {
    const target = existsSync(file) && path.extname(file) ? file : path.join(dist, 'index.html')
    res.setHeader(
      'Content-Type',
      {
        '.html': 'text/html',
        '.js': 'text/javascript',
        '.css': 'text/css',
        '.svg': 'image/svg+xml',
        '.woff2': 'font/woff2',
        '.woff': 'font/woff',
        '.png': 'image/png',
        '.ico': 'image/x-icon',
      }[path.extname(target)] || 'application/octet-stream',
    )
    res.end(readFileSync(target))
  } catch {
    res.writeHead(404).end('Build first: npm run build')
  }
})
server.listen(port, '127.0.0.1', () =>
  console.log(`Siamang web: http://${host} • local operator • isolated database`),
)
let closing = false
async function close() {
  if (closing) return
  closing = true
  server.close()
  await vite?.close()
  child.kill('SIGTERM')
  setTimeout(() => {
    child.kill('SIGKILL')
    process.exit()
  }, 1500).unref()
}
process.on('SIGINT', close)
process.on('SIGTERM', close)
server.on('error', () => {
  close()
  process.exitCode = 1
})
child.on('exit', () => {
  if (!closing) {
    console.error('SIAMANG stopped')
    close()
    process.exitCode = 1
  }
})
