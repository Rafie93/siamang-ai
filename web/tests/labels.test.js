import { describe, it, expect } from 'vitest'
import {
  packageNote,
  attemptText,
  failureText,
  composeAnswers,
  openQuestions,
  parseInline,
  parseMarkdown,
  phaseHistory,
  projectStep,
  repositorySlug,
  withoutOpenQuestions,
} from '../src/domain/labels.js'
import { validResponse } from '../src/services/contract.js'
describe('project steps', () => {
  it('names what the owner must do next', () => {
    const p = (state, spec) => ({ repository: { state }, spec_state: spec })
    expect(projectStep(p('cloning', 'thinking'))).toMatchObject({ step: 0, action: false })
    expect(projectStep({ repository: { state: 'failed', error: 'repository_empty' } })).toMatchObject({
      step: 0,
      action: true,
      error: 'repository_empty',
    })
    expect(projectStep(p('ready', 'gathering'))).toMatchObject({ step: 2, action: true })
    expect(projectStep(p('ready', 'pending_approval'))).toMatchObject({ step: 3, action: true })
    expect(projectStep(p('ready', 'approved'))).toMatchObject({ step: 4, action: false })
  })
  it('accepts what the API accepts', () => {
    for (const ok of [
      'a/b',
      'https://github.com/Rafie93/Rafie93.github.io',
      'https://github.com/a/b.git',
      'https://github.com/a/b/',
    ])
      expect(repositorySlug(ok)).toBeTruthy()
    for (const bad of ['', 'b', 'http://github.com/a/b', 'https://evil.test/a/b', 'a/b/c', 'a/b;rm'])
      expect(repositorySlug(bad)).toBeNull()
    expect(repositorySlug('https://github.com/a/b.git')).toBe('a/b')
  })
})
describe('answers', () => {
  const qs = [
    { question: 'Warna?', options: ['Hijau', 'Biru'] },
    { question: 'Bahasa?', options: [] },
    { question: 'Font?', options: [] },
  ]
  it('ties each answer to its question and leaves the rest to the Lead', () => {
    const text = composeAnswers(qs, { 0: ['Hijau', 'Biru'] }, { 1: ' EN ' }, 'tanpa animasi')
    expect(text).toContain('1. Warna?\nJawaban: Hijau; Biru')
    expect(text).toContain('2. Bahasa?\nJawaban: EN')
    expect(text).toContain('3. Font?\nJawaban: Terserah Amang')
    expect(text).toContain('Catatan: tanpa animasi')
  })
  it('stays inside the API limit', () =>
    expect(composeAnswers(qs, {}, { 0: 'x'.repeat(5000) }).length).toBeLessThanOrEqual(4000))
  it('moves only open questions out of the thread', () => {
    const m = [
      { role: 'lead', questions: qs },
      { role: 'owner', message: 'x' },
      { role: 'lead', message: 'y', questions: qs },
    ]
    expect(openQuestions(m)).toEqual(qs)
    expect(withoutOpenQuestions(m)[2].questions).toEqual([])
    expect(withoutOpenQuestions(m)[0].questions).toEqual(qs)
    expect(openQuestions(m.slice(0, 2))).toEqual([])
  })
})
it('keeps a repeated step as separate rows and counts the attempt', () => {
  const rows = phaseHistory([
    { phase: 'analysis', attempt: 1 },
    { phase: 'analysis', attempt: 2, retry_reason: 'timeout', remaining: 3 },
    { phase: 'analysis', attempt: 2, retry_reason: 'timeout', remaining: 3 },
  ])
  expect(rows.map(e => e.attempt)).toEqual([1, 2])
  expect(attemptText(rows[0])).toBe('')
  expect(attemptText(rows[1])).toBe(
    'percobaan ke-2 · setelah gagal: Model terlalu lama menjawab. · mengulang 3 dokumen',
  )
})
it('says when a retry kept what an earlier run finished', () => {
  expect(attemptText({ phase: 'planning', reused: 8 })).toBe('8 dokumen dari run sebelumnya dipakai ulang')
  expect(attemptText({ phase: 'analysis', attempt: 1, reused: 0 })).toBe('')
})
it('says which stage failed and after how many tries', () => {
  expect(failureText({ cause: 'timeout', stage: 'analysis', attempts: 3 })).toBe(
    'Arsitek merancang gagal setelah 3 percobaan. Model terlalu lama menjawab.',
  )
  expect(failureText({ cause: 'rate_limited' })).toBe('Batas pemakaian model tercapai. Coba lagi nanti.')
})
it('labels a package with its wave and how often it was tried', () => {
  expect(packageNote({ wave: 0, attempt: 1 })).toBe('')
  expect(packageNote({ wave: 2, attempt: 1 })).toBe('gelombang 2')
  expect(packageNote({ wave: 3, attempt: 2 })).toBe('gelombang 3 · percobaan ke-2')
})
it('does not show a first try twice', () =>
  expect(phaseHistory([{ phase: 'briefing' }, { phase: 'briefing', attempt: 1 }]).length).toBe(1))
it('shows each phase once per run of it', () =>
  expect(
    phaseHistory([
      { phase: 'queued' },
      { phase: 'programming' },
      { phase: 'programming' },
      { phase: 'qa' },
      { phase: 'programming' },
    ]).map(e => e.phase),
  ).toEqual(['queued', 'programming', 'qa', 'programming']))
describe('markdown from a model', () => {
  it('reads the blocks of a specification', () => {
    const b = parseMarkdown(
      '# Judul\n\nSatu\ndua\n\n- a\n- b\n\n1. x\n\n```js\nalert(1)\n```\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n> kutipan',
    )
    expect(b.map(x => x.type)).toEqual(['heading', 'paragraph', 'list', 'list', 'code', 'table', 'quote'])
    expect(b[1].text).toBe('Satu dua')
    expect(b[5].rows).toEqual([['1', '2']])
    expect(b[3].ordered).toBe(true)
  })
  it('only links to http pages', () => {
    expect(parseInline('[a](javascript:alert(1))').some(p => p.type === 'link')).toBe(false)
    expect(parseInline('[a](https://x.test/y)')[0]).toEqual({
      type: 'link',
      text: 'a',
      href: 'https://x.test/y',
    })
    expect(parseInline('**b** `c`').map(p => p.type)).toEqual(['strong', 'text', 'code'])
  })
  it('separates Mermaid fences from other code', () => {
    expect(
      parseMarkdown('```mermaid\nflowchart TD\n  A --> B\n```\n\n```js\nmermaid\n```').map(x => x.type),
    ).toEqual(['mermaid', 'code'])
    expect(parseMarkdown('```mermaid\nflowchart TD\n```')[0].text).toBe('flowchart TD')
  })
  it('survives a code fence that never closes', () =>
    expect(parseMarkdown('```\nabc')[0]).toEqual({ type: 'code', text: 'abc' }))
})
it('checks the conversation responses', () => {
  expect(validResponse('/projects/p/drafts', 'POST', { draft_id: 'dft_a', state: 'thinking' })).toBe(true)
  expect(validResponse('/projects/p/drafts', 'POST', {})).toBe(false)
  expect(validResponse('/projects/p/drafts/d/messages', 'POST', { state: 'thinking' })).toBe(true)
  expect(
    validResponse('/projects/p/drafts/d/confirm', 'POST', {
      task: { task_id: 't', status: 'queued', description: 'x' },
      scheduled: true,
    }),
  ).toBe(true)
  expect(
    validResponse('/projects/p/drafts', 'GET', { items: [{ draft_id: 'd', state: 'ready', messages: [] }] }),
  ).toBe(true)
})
