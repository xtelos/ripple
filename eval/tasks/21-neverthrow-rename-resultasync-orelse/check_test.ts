import * as fs from 'fs'
import * as path from 'path'
import * as tsNamespace from 'typescript'
import { expect, it } from 'vitest'

import { byFile, isTestFile, references, typecheck } from './check_helpers'
import { err, errAsync, ok, okAsync, ResultAsync } from '../src'

const ts: typeof tsNamespace = (tsNamespace as any).default ?? tsNamespace
const REPO = path.resolve(process.env.EVAL_REPO_ROOT ?? process.cwd())

// `orElse` is the name of four different methods: Ok.orElse, Err.orElse and the orElse of the IResult
// interface (src/result.ts, all kept) and ResultAsync.orElse (src/result-async.ts, the one renamed). A call
// is `x.orElse(...)` for a variable or an expression of any of them, so no syntax tells them apart: the
// check asks the compiler what type each receiver has, over every .ts file of src and tests (the type-check
// file tests/typecheck-tests.ts, which no runner executes, included).
type Use = { file: string; line: number; receiver: string; owners: string[] }
let memo: Use[] | undefined
const typed = (): Use[] => (memo ??= compute())
function compute(): Use[] {
  const files: string[] = []
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(path.join(REPO, dir), { withFileTypes: true })) {
      const rel = path.posix.join(dir, entry.name)
      if (entry.isDirectory()) walk(rel)
      else if (/\.ts$/.test(entry.name) && !entry.name.endsWith('.d.ts')) files.push(path.join(REPO, rel))
    }
  }
  walk('src')
  walk('tests')
  const config = ts.getParsedCommandLineOfConfigFile(path.join(REPO, 'tests/tsconfig.tests.json'), {}, {
    ...ts.sys,
    onUnRecoverableConfigFileDiagnostic: (d) => {
      throw new Error(ts.flattenDiagnosticMessageText(d.messageText, ' '))
    },
  })!
  const program = ts.createProgram(files, { ...config.options, noEmit: true })
  const checker = program.getTypeChecker()
  const found: Use[] = []
  for (const sf of program.getSourceFiles()) {
    if (!files.includes(path.resolve(sf.fileName))) continue
    const visit = (node: tsNamespace.Node) => {
      let receiver: tsNamespace.Expression | undefined
      if (ts.isPropertyAccessExpression(node) && ['orElse', 'recoverWith'].includes(node.name.text)) receiver = node.expression
      if (ts.isElementAccessExpression(node) && ts.isStringLiteralLike(node.argumentExpression) && ['orElse', 'recoverWith'].includes(node.argumentExpression.text)) {
        receiver = node.expression
      }
      if (receiver) {
        const type = checker.getTypeAtLocation(receiver)
        const owners = (type.isUnion() ? type.types : [type]).map((t) => t.getSymbol()?.getName() ?? (t.flags & ts.TypeFlags.Any ? 'any' : checker.typeToString(t)))
        const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1
        const name = ts.isPropertyAccessExpression(node) ? node.name.text : (node.argumentExpression as tsNamespace.StringLiteralLike).text
        found.push({ file: path.relative(REPO, sf.fileName).split(path.sep).join('/'), line, receiver: `${name} on ${receiver.getText().slice(0, 40)}`, owners })
      }
      ts.forEachChild(node, visit)
    }
    visit(sf)
  }
  return found
}

it('renamed, no alias, look-alikes untouched', () => {
  const Async = okAsync(1) as any
  expect(typeof Async.recoverWith).toBe('function')
  expect('orElse' in ResultAsync.prototype).toBe(false)
  expect(Object.getOwnPropertyNames(ResultAsync.prototype)).not.toContain('orElse')
  expect(typeof Async.orElse).toBe('undefined')
  expect(typeof (ok(1) as any).orElse).toBe('function')
  expect(typeof (err(1) as any).orElse).toBe('function')
  expect(typeof (ok(1) as any).recoverWith).toBe('undefined')
  expect(typeof (err(1) as any).recoverWith).toBe('undefined')
})

it('no stale callers', { timeout: 120_000 }, () => {
  const stale = typed().filter((s) => s.owners.includes('ResultAsync') && s.receiver.startsWith('orElse'))
  if (stale.length) throw new Error(`orElse on a ResultAsync is still used: ${byFile(stale.map((s) => ({ ...s, kind: 'attr' as const, detail: '' })))}`)
  const unknown = typed().filter((s) => s.receiver.startsWith('orElse') && s.owners.some((o) => !['Ok', 'Err', 'IResult'].includes(o)))
  if (unknown.length) throw new Error(`orElse on a receiver the compiler does not type as a Result: ${unknown.map((s) => `${s.file}:${s.line} ${s.owners}`).join(', ')}`)
  const renamed = typed().filter((s) => s.receiver.startsWith('recoverWith'))
  expect(renamed.every((s) => s.owners.length === 1 && s.owners[0] === 'ResultAsync')).toBe(true)
  // the tests, and the type-check file that no runner ever executes (a test the agent adds may call it too: only these two are required)
  const callers = [...new Set(renamed.map((s) => s.file))].sort()
  expect(callers).toEqual(expect.arrayContaining(['tests/index.test.ts', 'tests/typecheck-tests.ts']))
  expect(callers.filter((f) => !isTestFile(f))).toEqual([])
  const declared = references('recoverWith').filter((r) => r.kind === 'def')
  expect(declared.length).toBeGreaterThan(0)
  expect(declared.every((r) => r.file === 'src/result-async.ts' && r.detail === 'ResultAsync')).toBe(true)
})

it('Ok.orElse, Err.orElse and IResult.orElse keep their names', () => {
  const owners = references('orElse').filter((r) => r.kind === 'def').map((r) => `${r.file} ${r.detail}`)
  expect([...new Set(owners)].sort()).toEqual(['src/result.ts Err', 'src/result.ts IResult', 'src/result.ts Ok'])
  const calls = typed().filter((s) => s.receiver.startsWith('orElse'))
  expect(calls.length).toBeGreaterThanOrEqual(10)
})

it('every caller still works', async () => {
  const recovered = await errAsync<number, string>('boom').recoverWith((e) => okAsync(e.length))
  expect(recovered._unsafeUnwrap()).toBe(4)
  const mapped = await errAsync<number, string>('boom').recoverWith((e) => err(e + '!'))
  expect(mapped._unsafeUnwrapErr()).toBe('boom!')
  const skipped = await okAsync<number, string>(7).recoverWith(() => okAsync(0))
  expect(skipped._unsafeUnwrap()).toBe(7)
  expect(ok<number, string>(1).orElse(() => ok(2))._unsafeUnwrap()).toBe(1)
  expect(err<number, string>('x').orElse((e) => ok(e.length))._unsafeUnwrap()).toBe(1)
})

it('the repository still type-checks, the file the suite never runs included', { timeout: 120_000 }, () => {
  expect(typecheck('tsconfig.json')).toBe('')
  expect(typecheck('tests/tsconfig.tests.json')).toBe('')
})
