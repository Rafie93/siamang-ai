import { readFileSync } from 'node:fs'
import { expect, it } from 'vitest'
import { version } from '../package.json'

it('keeps the interface on the same version as the backend', () => {
  const pyproject = readFileSync('../pyproject.toml', 'utf8')
  expect(pyproject).toContain(`version = "${version}"`)
})
