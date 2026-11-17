// The Designer's documents as the page draws them: tokens become CSS variables, a wireframe becomes a sealed page.
// Both are model output, so nothing here is trusted: names and values are filtered, and the page runs no script.

const FENCE = /^```\s*([A-Za-z]+)\s*$/

/** The language of a fence line, lower-cased, or '' for a bare fence. */
const fenceLanguage = line => (FENCE.exec(line)?.[1] || '').toLowerCase()

/** The first JSON fence of the design tokens document, as an object; null when there is none or it is not valid. */
export function tokensFrom(content) {
  const lines = (content || '').split('\n')
  for (let i = 0; i < lines.length; i++) {
    if (!lines[i].startsWith('```') || fenceLanguage(lines[i]) !== 'json') continue
    const body = []
    for (i++; i < lines.length && !lines[i].startsWith('```'); i++) body.push(lines[i])
    return parseTokens(body.join('\n'))
  }
  return null
}

export function parseTokens(text) {
  try {
    const value = JSON.parse(text)
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null
  } catch {
    return null
  }
}

const GROUPS = { colors: 'color', typography: 'font', spacing: 'space', radius: 'radius' }
const PIXELS = new Set(['space', 'radius'])
// A value may not leave its declaration: no braces, semicolons, angle brackets, backslashes, or comments.
const SAFE_VALUE = /^[^{};<>\\]*$/

const slug = key =>
  String(key)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')

/** Every token as [variable, value], nested keys joined with a hyphen, the way the Designer is told to name them. */
export function tokenVariables(tokens) {
  const out = []
  const walk = (prefix, value, unit) => {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      for (const [key, inner] of Object.entries(value))
        if (slug(key)) walk(`${prefix}-${slug(key)}`, inner, unit)
      return
    }
    const text = typeof value === 'number' && unit ? `${value}px` : String(value ?? '')
    if (text && text.length <= 200 && SAFE_VALUE.test(text) && !text.includes('/*')) out.push([prefix, text])
  }
  for (const [group, name] of Object.entries(GROUPS))
    if (tokens?.[group]) walk(`--${name}`, tokens[group], PIXELS.has(name))
  return out
}

export const tokenCss = tokens =>
  tokenVariables(tokens)
    .map(([name, value]) => `${name}: ${value};`)
    .join(' ')

/** The color tokens that are plain hex, for swatches. */
export const colorTokens = tokens =>
  Object.entries(tokens?.colors || {}).filter(([, v]) => typeof v === 'string' && /^#[0-9a-f]{3,8}$/i.test(v))

// No network, no script, no forms: only the inline style and pictures written into the wireframe itself.
export const WIREFRAME_POLICY = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:"

/** The page a wireframe is shown in, with the tokens as variables on :root. */
export function wireframePage(html, css = '') {
  return (
    '<!doctype html><html><head><meta charset="utf-8">' +
    `<meta http-equiv="Content-Security-Policy" content="${WIREFRAME_POLICY}">` +
    '<meta name="viewport" content="width=device-width, initial-scale=1">' +
    `<style>:root { ${css} } body { margin: 0; font-family: system-ui, sans-serif; }</style>` +
    `</head><body>${html || ''}</body></html>`
  )
}

/** Whether an edited block may be saved, as the backend will judge it; '' when it may. */
export function blockProblem(lang, source) {
  const body = (source || '').replace(/\r\n?/g, '\n').replace(/^\n+|\n+$/g, '')
  if (!body.trim()) return 'Isi tidak boleh kosong.'
  if (body.length > 20000) return 'Terlalu panjang: paling banyak 20.000 karakter.'
  if (body.split('\n').some(l => l.startsWith('```'))) return 'Baris tidak boleh diawali ```.'
  if (lang === 'json' && !parseTokens(body)) return 'JSON belum valid, atau bukan sebuah objek.'
  return ''
}
