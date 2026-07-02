import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const indexHtml = readFileSync(resolve(here, '../index.html'), 'utf8')

test('index.html 不引入第三方字型 CDN', () => {
  expect(indexHtml).not.toContain('fonts.googleapis.com')
  expect(indexHtml).not.toContain('fonts.gstatic.com')
})
