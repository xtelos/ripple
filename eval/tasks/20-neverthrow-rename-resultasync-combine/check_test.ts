import { expect, it } from 'vitest'

import { byFile, references, typecheck } from './check_helpers'
import { err, errAsync, ok, okAsync, Result, ResultAsync } from '../src'

// `combine` is the name of two different functions: Result.combine (synchronous, in a namespace in
// src/result.ts) and ResultAsync.combine (a static method in src/result-async.ts). Only the second one is
// renamed, so a use of `.combine` is stale unless its receiver is `Result`, and a declaration is stale
// unless it sits in the `Result` namespace.
const stale = () => references('combine').filter((r) => (r.kind === 'attr' || r.kind === 'def') && r.detail !== 'Result')

it('renamed, no alias, look-alikes untouched', () => {
  const Async = ResultAsync as any
  expect(typeof Async.gather).toBe('function')
  expect('combine' in ResultAsync).toBe(false)
  expect(Object.getOwnPropertyNames(ResultAsync)).not.toContain('combine')
  expect(typeof Result.combine).toBe('function')
  expect(typeof Async.combineWithAllErrors).toBe('function')
  expect(typeof (Result as any).gather).toBe('undefined')
})

it('no stale callers', () => {
  const left = stale()
  if (left.length) throw new Error(`combine on ResultAsync is still used: ${byFile(left)}`)
  const named = references('gather')
  const declared = named.filter((r) => r.kind === 'def')
  expect(declared.length).toBeGreaterThan(0)
  expect(declared.every((r) => r.file === 'src/result-async.ts' && r.detail === 'ResultAsync')).toBe(true)
  const called = new Set(named.filter((r) => r.kind === 'attr' && r.detail === 'ResultAsync').map((r) => r.file))
  // the tests, and the type-check file that no runner ever executes
  expect([...called]).toEqual(expect.arrayContaining(['tests/index.test.ts', 'tests/typecheck-tests.ts']))
})

it('Result.combine and combineWithAllErrors keep their names', () => {
  const sync = references('combine').filter((r) => r.kind === 'attr' && r.detail === 'Result')
  expect(sync.length).toBeGreaterThanOrEqual(53)
  const owners = new Set(references('combineWithAllErrors').filter((r) => r.kind === 'def').map((r) => `${r.file} ${r.detail}`))
  expect([...owners].sort()).toEqual(['src/result-async.ts ResultAsync', 'src/result.ts Result'])
})

it('every caller still works', async () => {
  const all = await ResultAsync.gather([okAsync(1), okAsync(2), okAsync(3)])
  expect(all._unsafeUnwrap()).toEqual([1, 2, 3])
  const failed = await ResultAsync.gather([okAsync(1), errAsync('boom'), errAsync('later')])
  expect(failed._unsafeUnwrapErr()).toBe('boom')
  const withAll = await ResultAsync.combineWithAllErrors([okAsync(1), errAsync('a'), errAsync('b')])
  expect(withAll._unsafeUnwrapErr()).toEqual(['a', 'b'])
  expect(Result.combine([ok(1), ok(2)])._unsafeUnwrap()).toEqual([1, 2])
  expect(Result.combine([ok(1), err('no')]).isErr()).toBe(true)
})

it('the repository still type-checks, the file the suite never runs included', () => {
  expect(typecheck('tsconfig.json')).toBe('')
  expect(typecheck('tests/tsconfig.tests.json')).toBe('')
})
