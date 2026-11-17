import { describe, expect, it } from 'vitest'
import {
  WIREFRAME_POLICY,
  blockProblem,
  colorTokens,
  parseTokens,
  tokenCss,
  tokenVariables,
  tokensFrom,
  wireframePage,
} from '../src/domain/design.js'

const DOC =
  'Token.\n\n```mermaid\nflowchart LR\n```\n\n```JSON\n{"colors": {"text": "#111", "Primary Dark": "#0a5"}}\n```'

describe('design tokens', () => {
  it('come from the first JSON fence of the document, and only when it is an object', () => {
    expect(tokensFrom(DOC)).toEqual({ colors: { text: '#111', 'Primary Dark': '#0a5' } })
    expect(tokensFrom('```json\n[1]\n```')).toBeNull()
    expect(tokensFrom('```json\n{rusak\n```')).toBeNull()
    expect(tokensFrom('tanpa token')).toBeNull()
    expect(parseTokens('"teks"')).toBeNull()
  })
  it('become CSS variables named the way the Designer is told: nested keys joined with a hyphen', () => {
    const tokens = {
      colors: { text: '#111', 'Primary Dark': '#0a5' },
      typography: { body: { family: 'Inter, sans-serif', weight: 600 } },
      spacing: { sm: 8, md: '1rem' },
      radius: { card: 12 },
      ignored: { x: 1 },
    }
    expect(tokenVariables(tokens)).toEqual([
      ['--color-text', '#111'],
      ['--color-primary-dark', '#0a5'],
      ['--font-body-family', 'Inter, sans-serif'],
      ['--font-body-weight', '600'],
      ['--space-sm', '8px'],
      ['--space-md', '1rem'],
      ['--radius-card', '12px'],
    ])
    expect(tokenCss({ colors: { a: '#fff' } })).toBe('--color-a: #fff;')
  })
  it('never let a value leave its declaration', () => {
    const hostile = {
      colors: {
        ok: 'red',
        brace: 'red} body{display:none',
        semi: 'red; background: url(x)',
        tag: '</style><img src=x>',
        slash: 'red\\;',
        comment: 'red /* x',
        long: 'x'.repeat(201),
        '!!!': 'blue',
      },
    }
    expect(tokenVariables(hostile)).toEqual([['--color-ok', 'red']])
    expect(colorTokens({ colors: { a: '#abc', b: 'red', c: '#12345' } })).toEqual([
      ['a', '#abc'],
      ['c', '#12345'],
    ])
  })
})

describe('wireframe page', () => {
  it('loads nothing from anywhere and carries the tokens on :root', () => {
    const page = wireframePage('<main>Hai</main>', '--color-text: #111;')
    expect(page).toContain(`content="${WIREFRAME_POLICY}"`)
    expect(WIREFRAME_POLICY).toMatch(/default-src 'none'/)
    expect(WIREFRAME_POLICY).not.toMatch(/script-src|https?:/)
    expect(page.indexOf('Content-Security-Policy')).toBeLessThan(page.indexOf('<main>'))
    expect(page).toContain(':root { --color-text: #111; }')
  })
})

describe('an edited block', () => {
  it('is judged by the rules the backend applies', () => {
    expect(blockProblem('html', '<p>x</p>')).toBe('')
    expect(blockProblem('json', '{"colors": {}}')).toBe('')
    expect(blockProblem('json', '{"colors": ')).toMatch(/JSON/)
    expect(blockProblem('json', '[1]')).toMatch(/JSON/)
    expect(blockProblem('html', '  \n')).toMatch(/kosong/)
    expect(blockProblem('html', '<p>\n```\n')).toMatch(/```/)
    expect(blockProblem('html', 'x'.repeat(20001))).toMatch(/20.000/)
  })
})
