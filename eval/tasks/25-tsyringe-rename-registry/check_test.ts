import * as fs from 'fs'
import * as path from 'path'

import { byFile, isTestFile, missingFrom, references, typecheck } from './check_helpers'
import * as tsyringe from '../src'
import { container, Lifecycle, registerAll } from '../src'

const REPO = path.resolve(process.env.EVAL_REPO_ROOT ?? process.cwd())
const read = (rel: string) => fs.readFileSync(path.join(REPO, rel), 'utf8')

// `registry` is the class decorator in src/decorators/registry.ts, exported by name from the barrel
// src/decorators/index.ts and applied as `@registry([...])`, never called as `registry(...)` by name: a use is a
// decoration. It becomes `registerAll`. Four other things are spelled alike and keep their name: the `registry`
// variable of the Registry tests (src/__tests__/registry.test.ts), and the module specifiers "./registry" and
// "./registry-base" of the Registry class, which a rename of the word would turn into paths of files that do not
// exist. The decorated uses are in the suite (global-container.test.ts and two fixtures) and in README.md, which
// shows the decorator in four examples and names it four times in the text, where no runner reads it.
const FIXTURES = [
  'src/__tests__/fixtures/03-test-case-A03-lazy-injects-B03-interface.ts',
  'src/__tests__/fixtures/03-test-case-B03-lazy-injects-A03-interface.ts',
]

it('renamed, no alias: the name is exported once and registers providers', () => {
  const all = tsyringe as any
  expect(typeof all.registerAll).toBe('function')
  expect('registry' in all).toBe(false)
  expect(Object.keys(all)).not.toContain('registry')
  expect(typeof (require('../src/decorators') as any).registerAll).toBe('function')
  expect((require('../src/decorators') as any).registry).toBeUndefined()
})

it('no stale uses: the only registry left is the Registry tests variable', () => {
  const left = references('registry').filter((r) => r.file !== 'src/__tests__/registry.test.ts')
  if (left.length) throw new Error(`the decorator registry is still used: ${byFile(left)}`)
  const named = references('registerAll')
  const declared = named.filter((r) => r.kind === 'def' && !isTestFile(r.file))
  expect(declared.map((r) => r.file)).toEqual(['src/decorators/registry.ts'])
  // the barrel's export and the three imports of the decorators from the barrel
  const imported = named.filter((r) => r.kind === 'import')
  expect(imported.filter((r) => r.file === 'src/decorators/index.ts').map((r) => r.detail)).toEqual(['./registry'])
  // the sources import it nowhere else; the suite does (and the agent's own tests may, which is why only the sources are exact)
  const importers = imported.filter((r) => r.file !== 'src/decorators/index.ts').map((r) => r.file)
  expect(importers.filter((f) => !isTestFile(f))).toEqual([])
  expect(missingFrom(importers, [...FIXTURES, 'src/__tests__/global-container.test.ts'])).toEqual([])
  // the decorations: ten in the suite, one in each of the two fixtures at least, and the default export, which is the only one outside tests
  const used = named.filter((r) => r.kind === 'name')
  expect(used.filter((r) => r.file === 'src/__tests__/global-container.test.ts').length).toBeGreaterThanOrEqual(10)
  expect(used.filter((r) => FIXTURES.includes(r.file)).length).toBeGreaterThanOrEqual(2)
  expect(used.filter((r) => !isTestFile(r.file)).map((r) => r.file)).toEqual(['src/decorators/registry.ts'])
})

it('the README examples and text, which no runner reads, are renamed too', () => {
  const readme = read('README.md')
  const old = readme.match(/.*@registry\b.*/g) ?? []
  if (old.length) throw new Error(`README.md still names @registry: ${old.map((l) => l.trim().slice(0, 40)).join(' | ')}`)
  expect(readme.match(/@registerAll\b/g)?.length).toBeGreaterThanOrEqual(8)
  expect(readme.match(/^@registerAll\(\[/gm)?.length).toBeGreaterThanOrEqual(4)
})

it('the Registry class, its modules and the Registry tests variable keep their names', () => {
  for (const rel of ['src/registry.ts', 'src/registry-base.ts', 'src/decorators/registry.ts']) expect(fs.existsSync(path.join(REPO, rel))).toBe(true)
  expect(read('src/dependency-container.ts')).toContain('import Registry from "./registry";')
  expect(read('src/interceptors.ts')).toContain('from "./registry-base"')
  expect(read('src/decorators/index.ts')).toContain('export {default as registerAll} from "./registry";')
  const tests = read('src/__tests__/registry.test.ts')
  expect(tests).toContain('let registry: Registry;')
  expect(references('registry').filter((r) => r.file === 'src/__tests__/registry.test.ts').length).toBeGreaterThanOrEqual(20)
})

it('every use still works', () => {
  class Dep {}
  class Other {}
  @registerAll([
    { token: 'value', useValue: 1 },
    { token: Dep, useClass: Dep, options: { lifecycle: Lifecycle.Singleton } },
    { token: 'alias', useToken: 'value' },
    { token: 'made', useFactory: () => new Other() },
  ])
  class Holder {}
  @registerAll()
  class Nothing {}
  expect(container.resolve('value')).toBe(1)
  expect(container.resolve('alias')).toBe(1)
  expect(container.resolve(Dep)).toBe(container.resolve(Dep))
  expect(container.resolve('made')).toBeInstanceOf(Other)
  expect(typeof Holder).toBe('function')
  expect(typeof Nothing).toBe('function')
  class Plain {}
  expect(registerAll([])(Plain)).toBe(Plain)
  container.reset()
})

it('the repository still type-checks, the fixtures and the typings included', () => {
  expect(typecheck('src/__tests__/tsconfig.json')).toBe('')
  expect(typecheck('tsconfig.json')).toBe('')
}, 120000)
