import { readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { expect, it } from 'vitest'

const css = readFileSync('src/assets/theme.css', 'utf8')

function tokens(selector) {
  const block = css.slice(css.indexOf(selector), css.indexOf('}', css.indexOf(selector)))
  return Object.fromEntries(
    [...block.matchAll(/(--color-[\w-]+):\s*(#[0-9a-f]{6})/gi)].map(m => [m[1], m[2]]),
  )
}

const luminance = hex => {
  const [r, g, b] = hex
    .slice(1)
    .match(/../g)
    .map(x => parseInt(x, 16) / 255)
    .map(c => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
const contrast = (a, b) => {
  const [x, y] = [luminance(a), luminance(b)].sort((m, n) => n - m)
  return (x + 0.05) / (y + 0.05)
}

const light = tokens(":root[data-theme='light']")
const dark = tokens(":root[data-theme='dark']")

it('uses green as the primary color in both themes', () => {
  expect(light['--color-primary'].toLowerCase()).toBe('#1b7f52')
  for (const theme of [light, dark]) {
    const [r, g, b] = theme['--color-primary']
      .slice(1)
      .match(/../g)
      .map(x => parseInt(x, 16))
    expect(g).toBeGreaterThan(r)
    expect(g).toBeGreaterThan(b)
  }
})

it('defines the same tokens in both themes', () => {
  expect(Object.keys(dark).sort()).toEqual(Object.keys(light).sort())
})

it('keeps text, primary actions and status colors readable', () => {
  for (const t of [light, dark]) {
    expect(contrast(t['--color-primary'], t['--color-on-primary'])).toBeGreaterThanOrEqual(4.5)
    expect(contrast(t['--color-primary'], t['--color-surface'])).toBeGreaterThanOrEqual(4.5)
    expect(contrast(t['--color-text'], t['--color-surface'])).toBeGreaterThanOrEqual(7)
    expect(contrast(t['--color-text-2'], t['--color-surface'])).toBeGreaterThanOrEqual(4.5)
    expect(contrast(t['--color-text-3'], t['--color-surface'])).toBeGreaterThanOrEqual(4.5)
    for (const tone of ['success', 'info', 'warning', 'error'])
      expect(contrast(t[`--color-${tone}`], t[`--color-${tone}-soft`]), tone).toBeGreaterThanOrEqual(4.5)
  }
})

it('ships no commercial template code and no retired product name', () => {
  const files = dir =>
    readdirSync(dir, { withFileTypes: true }).flatMap(e =>
      e.isDirectory() ? files(path.join(dir, e.name)) : [path.join(dir, e.name)],
    )
  const sources = [...files('src'), ...files('public'), 'index.html', 'package.json'].filter(f =>
    /\.(js|vue|css|html|json)$/.test(f),
  )
  for (const f of sources)
    expect(readFileSync(f, 'utf8'), f).not.toMatch(
      /vuexy|vuetify|pixinvent|themeselection|@core\/|@layouts|virtual.?office/i,
    )
})
