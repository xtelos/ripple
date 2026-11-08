import * as fs from 'fs'
import * as path from 'path'
import * as tsNamespace from 'typescript'
import { act, render, renderHook } from '@testing-library/react'
import * as React from 'react'

import { isTestFile, missingFrom, references, show, sources, typecheck } from './check_helpers'
import * as mediaQueryHook from '../src/hooks/useMediaQuery'
import { Sidebar } from '../src'

const ts: typeof tsNamespace = (tsNamespace as any).default ?? tsNamespace
const REPO = path.resolve(process.env.EVAL_REPO_ROOT ?? process.cwd())
const h = React.createElement

// `useMediaQuery` is spelled in two ways that are not a use of the hook: the module it lives in (three specifiers
// `../hooks/useMediaQuery` and the file name, which keep their name) and the title of the hook's describe block.
// And it is a use of the hook in a way no compiler follows: tests/Sidebar.test.tsx spies on the module's export with
// `vi.spyOn(mediaQueryHook, 'useMediaQuery')`, nine times, naming the export in a string. So the check reads names
// with the compiler, strings with the same parse, and then runs Sidebar against a spy on the new name.
type Str = { file: string; line: number; text: string; where: 'spyOn' | 'describe' | 'specifier' | 'other' }

function strings(): Str[] {
  const found: Str[] = []
  for (const [file, sf] of sources(REPO)) {
    const visit = (node: tsNamespace.Node) => {
      if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
        if (/useMediaQuery|useMatchMedia/.test(node.text)) {
          const parent = node.parent
          let where: Str['where'] = 'other'
          if (ts.isImportDeclaration(parent) || ts.isExportDeclaration(parent)) where = 'specifier'
          else if (ts.isCallExpression(parent) && ts.isPropertyAccessExpression(parent.expression) && parent.expression.name.text === 'spyOn' && parent.arguments[1] === node) where = 'spyOn'
          else if (ts.isCallExpression(parent) && ts.isIdentifier(parent.expression) && parent.expression.text === 'describe' && parent.arguments[0] === node) where = 'describe'
          found.push({ file, line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1, text: node.text, where })
        }
      }
      ts.forEachChild(node, visit)
    }
    visit(sf)
  }
  return found
}

it('no name useMediaQuery is left anywhere: declaration, imports and calls are useMatchMedia', () => {
  const stale = references('useMediaQuery', REPO)
  if (stale.length) throw new Error(`useMediaQuery is still used by name: ${show(stale)}`)
  const now = references('useMatchMedia', REPO)
  // exact in the sources; a test the agent adds may import and call the hook too, so the tests are a floor
  const imports = now.filter((r) => r.kind === 'import').map((r) => r.file).sort()
  expect(imports.filter((f) => !isTestFile(f))).toEqual(['src/components/Sidebar.tsx'])
  expect(missingFrom(imports, ['src/components/Sidebar.tsx', 'tests/hooks.test.tsx'])).toEqual([])
  // the declaration `export const useMatchMedia = ...` and the six calls are bare names to the helper
  const used = now.filter((r) => r.kind === 'name').map((r) => r.file).sort()
  expect(used.filter((f) => !isTestFile(f))).toEqual(['src/components/Sidebar.tsx', 'src/hooks/useMediaQuery.tsx'])
  expect(missingFrom(used, Array(5).fill('tests/hooks.test.tsx'))).toEqual([])
  expect(now.filter((r) => !isTestFile(r.file)).length).toBe(3)
  expect(now.length).toBeGreaterThanOrEqual(9)
})

it('the nine spies of Sidebar.test name the new export, and nothing spies on the old one', () => {
  const strs = strings()
  const spies = strs.filter((s) => s.where === 'spyOn')
  const old = spies.filter((s) => s.text === 'useMediaQuery')
  if (old.length) throw new Error(`vi.spyOn still names the old export: ${old.map((s) => `${s.file}:${s.line}`).join(', ')}`)
  expect(spies.filter((s) => !isTestFile(s.file))).toEqual([])
  expect(missingFrom(spies.map((s) => `${s.file} ${s.text}`), Array(9).fill('tests/Sidebar.test.tsx useMatchMedia'))).toEqual([])
})

it('the module, its three specifiers and the describe title keep the old spelling', () => {
  expect(fs.existsSync(path.join(REPO, 'src/hooks/useMediaQuery.tsx'))).toBe(true)
  expect(fs.existsSync(path.join(REPO, 'src/hooks/useMatchMedia.tsx'))).toBe(false)
  const strs = strings()
  // a test the agent adds may import the module (it keeps its path) or title a describe block after the hook, by either name
  const specifiers = strs.filter((s) => s.where === 'specifier')
  expect(specifiers.filter((s) => !isTestFile(s.file)).map((s) => `${s.file} ${s.text}`)).toEqual(['src/components/Sidebar.tsx ../hooks/useMediaQuery'])
  expect(specifiers.filter((s) => s.text.includes('useMatchMedia'))).toEqual([])
  expect(missingFrom(specifiers.map((s) => `${s.file} ${s.text}`), ['tests/Sidebar.test.tsx ../src/hooks/useMediaQuery', 'tests/hooks.test.tsx ../src/hooks/useMediaQuery'])).toEqual([])
  const described = strs.filter((s) => s.where === 'describe')
  expect(described.filter((s) => !isTestFile(s.file))).toEqual([])
  expect(missingFrom(described.map((s) => `${s.file} ${s.text}`), ['tests/hooks.test.tsx useMediaQuery'])).toEqual([])
  // any other string that spells either name: none in the sources, and none of the old name in a test (a title that says the new one is a use)
  expect(strs.filter((s) => s.where === 'other' && (!isTestFile(s.file) || s.text.includes('useMediaQuery')))).toEqual([])
})

it('the old export is gone and the new one is the hook', () => {
  expect('useMediaQuery' in mediaQueryHook).toBe(false)
  expect(Object.keys(mediaQueryHook)).toEqual(['useMatchMedia'])
  const mql = (matches: boolean) => {
    const listeners = new Set<() => void>()
    const media = {
      matches,
      media: '',
      addEventListener: (_: string, cb: () => void) => listeners.add(cb),
      removeEventListener: (_: string, cb: () => void) => listeners.delete(cb),
      fire(next: boolean) {
        media.matches = next
        listeners.forEach((cb) => cb())
      },
    }
    window.matchMedia = vi.fn().mockImplementation(() => media) as unknown as typeof window.matchMedia
    return media
  }
  const query = mql(false)
  const { result } = renderHook(() => mediaQueryHook.useMatchMedia('(max-width: 600px)'))
  expect(result.current).toBe(false)
  act(() => query.fire(true))
  expect(result.current).toBe(true)
  expect(renderHook(() => mediaQueryHook.useMatchMedia()).result.current).toBe(false)
})

it('Sidebar reads the hook through the module, so a spy on the new name changes its layout', () => {
  const spy = vi.spyOn(mediaQueryHook, 'useMatchMedia').mockImplementation(() => true)
  const calls: boolean[] = []
  const { container, rerender } = render(h(Sidebar, { breakPoint: 'md', onBreakPoint: (broken: boolean) => calls.push(broken) }, 'x'))
  expect(container.querySelector('.ps-sidebar-root')).toHaveClass('ps-broken')
  expect(spy).toHaveBeenCalledWith('(max-width: 768px)')
  expect(calls).toEqual([true])
  spy.mockImplementation(() => false)
  rerender(h(Sidebar, { breakPoint: 'md', onBreakPoint: (broken: boolean) => calls.push(broken) }, 'x'))
  expect(container.querySelector('.ps-sidebar-root')).not.toHaveClass('ps-broken')
  expect(calls).toEqual([true, false])
  spy.mockRestore()
})

it('the repository still type-checks', () => {
  expect(typecheck('tsconfig.json')).toBe('')
}, 120000)
