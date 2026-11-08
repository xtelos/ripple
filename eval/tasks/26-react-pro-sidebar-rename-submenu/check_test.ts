import * as fs from 'fs'
import * as path from 'path'
import * as tsNamespace from 'typescript'
import { fireEvent, render, screen } from '@testing-library/react'
import * as React from 'react'

import { isTestFile, missingFrom, typecheck } from './check_helpers'
import * as pkg from '../src'
import { Menu, MenuItem, Sidebar } from '../src'

const ts: typeof tsNamespace = (tsNamespace as any).default ?? tsNamespace
const REPO = path.resolve(process.env.EVAL_REPO_ROOT ?? process.cwd())
const h = React.createElement
const MenuGroup = (pkg as any).MenuGroup

// `SubMenu` is the component, written as `<SubMenu>` in tests, stories and the website's demos (108 elements), and it is
// also the name of its module (4 specifiers), of 12 test titles, of the storybook section of its stories and of the docs
// page, and of the code the website prints for people to copy. The compiler says which of those are the component: it is
// every identifier, and no string. So the check reads identifiers with the checker (the website is not in the root
// project, so it is added with the package name mapped back to src), strings and JSX text with the same parse, and the
// docs with a regular expression, because nothing else reads them.
const names = ['SubMenu', 'MenuGroup']

function programFiles(): { files: string[]; options: tsNamespace.CompilerOptions } {
  const config = ts.getParsedCommandLineOfConfigFile(path.join(REPO, 'tsconfig.json'), {}, {
    ...ts.sys,
    onUnRecoverableConfigFileDiagnostic: (d) => {
      throw new Error(ts.flattenDiagnosticMessageText(d.messageText, ' '))
    },
  })!
  const files = new Set(config.fileNames)
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(path.join(REPO, dir), { withFileTypes: true })) {
      if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue
      const rel = path.join(dir, entry.name)
      if (entry.isDirectory()) walk(rel)
      else if (/\.tsx?$/.test(entry.name)) files.add(path.join(REPO, rel))
    }
  }
  walk('website')
  return { files: [...files], options: { ...config.options, noEmit: true, baseUrl: REPO, paths: { 'react-pro-sidebar': ['src/index.ts'], '@/*': ['website/*'] } } }
}

type Ident = { name: string; file: string; line: number; role: 'decl' | 'import' | 'open' | 'close' | 'other'; target: string }
type Text = { file: string; line: number; where: 'specifier' | 'testtitle' | 'title' | 'other'; kind: 'string' | 'template' | 'jsxtext'; name: string }
let memo: { idents: Ident[]; texts: Text[] } | undefined

function scan() {
  if (memo) return memo
  const { files, options } = programFiles()
  const program = ts.createProgram(files, options)
  const checker = program.getTypeChecker()
  const idents: Ident[] = []
  const texts: Text[] = []
  const at = (n: tsNamespace.Node) => `${path.relative(REPO, n.getSourceFile().fileName).split(path.sep).join('/')}:${n.getSourceFile().getLineAndCharacterOfPosition(n.getStart()).line + 1}`
  for (const sf of program.getSourceFiles()) {
    const file = path.relative(REPO, sf.fileName).split(path.sep).join('/')
    if (file.startsWith('..') || file.includes('node_modules') || sf.isDeclarationFile) continue
    const visit = (node: tsNamespace.Node) => {
      const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1
      if (ts.isIdentifier(node) && names.includes(node.text)) {
        const p = node.parent
        let role: Ident['role'] = 'other'
        if (ts.isImportSpecifier(p)) role = 'import'
        else if (ts.isJsxOpeningElement(p) || ts.isJsxSelfClosingElement(p)) role = 'open'
        else if (ts.isJsxClosingElement(p)) role = 'close'
        else if (ts.isVariableDeclaration(p) && p.name === node) role = 'decl'
        let sym = checker.getSymbolAtLocation(node)
        if (sym && sym.flags & ts.SymbolFlags.Alias) sym = checker.getAliasedSymbol(sym)
        const decl = sym?.declarations?.[0]
        idents.push({ name: node.text, file, line, role, target: decl ? at(decl) : '?' })
      }
      let text: string | undefined
      let kind: Text['kind'] = 'string'
      if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) text = node.text
      else if (ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) [text, kind] = [node.text, 'template']
      else if (ts.isJsxText(node)) [text, kind] = [node.text, 'jsxtext']
      if (text !== undefined) {
        for (const name of names) {
          if (new RegExp(`\\b${name}\\b`).test(text)) {
            const p = node.parent
            let where: Text['where'] = 'other'
            if (ts.isImportDeclaration(p) || ts.isExportDeclaration(p)) where = 'specifier'
            else if (ts.isCallExpression(p) && ts.isIdentifier(p.expression) && ['describe', 'it', 'test'].includes(p.expression.text) && p.arguments[0] === node) where = 'testtitle'
            else if (ts.isPropertyAssignment(p) && ts.isIdentifier(p.name) && p.name.text === 'title') where = 'title'
            texts.push({ file, line, where, kind, name })
          }
        }
      }
      ts.forEachChild(node, visit)
    }
    visit(sf)
  }
  memo = { idents, texts }
  return memo
}

const read = (file: string) => fs.readFileSync(path.join(REPO, file), 'utf8')
const HISTORY = ['CHANGELOG.md', 'MIGRATION.md', 'website/pages/docs/changelog.mdx', 'website/pages/docs/migration.mdx']
function docs(): string[] {
  const out = ['README.md']
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(path.join(REPO, dir), { withFileTypes: true })) {
      if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue
      const rel = `${dir}/${entry.name}`
      if (entry.isDirectory()) walk(rel)
      else if (/\.mdx?$/.test(entry.name)) out.push(rel)
    }
  }
  walk('website')
  return out.filter((f) => !HISTORY.includes(f)).sort()
}

it('no identifier SubMenu is left, and every MenuGroup is the component the compiler finds in src/components/SubMenu.tsx', () => {
  const { idents } = scan()
  const stale = idents.filter((i) => i.name === 'SubMenu')
  if (stale.length) {
    const by = new Map<string, number>()
    for (const s of stale) by.set(s.file, (by.get(s.file) ?? 0) + 1)
    throw new Error(`SubMenu is still written: ${[...by].map(([f, n]) => `${f} x${n}`).join(', ')}`)
  }
  const now = idents.filter((i) => i.name === 'MenuGroup')
  const elsewhere = now.filter((i) => !i.target.startsWith('src/components/SubMenu.tsx:'))
  if (elsewhere.length) throw new Error(`MenuGroup is something else at ${elsewhere.map((i) => `${i.file}:${i.line}`).join(', ')}`)
  expect(now.filter((i) => i.role === 'decl').map((i) => i.file)).toEqual(['src/components/SubMenu.tsx'])
  // the sources and docs-side files are counted exactly. The tests (67 of the 237, in three files) are a floor: a test the
  // agent adds may write MenuGroup too, and the stale SubMenu check above is what holds in every file
  const outside = now.filter((i) => !isTestFile(i.file))
  const inTests = now.filter((i) => isTestFile(i.file))
  expect(outside.filter((i) => i.role === 'import').length).toBe(6)
  expect(outside.filter((i) => i.role === 'open').length).toBe(76)
  expect(outside.filter((i) => i.role === 'close').length).toBe(76)
  expect(outside.length).toBe(170)
  expect(inTests.filter((i) => i.role === 'import').length).toBeGreaterThanOrEqual(3)
  expect(inTests.filter((i) => i.role === 'open').length).toBeGreaterThanOrEqual(32)
  expect(inTests.filter((i) => i.role === 'close').length).toBeGreaterThanOrEqual(32)
  expect(inTests.length).toBeGreaterThanOrEqual(67)
  // the tests, the stories and the website's demos and playground all write it
  const files = [...new Set(now.map((i) => i.file))].sort()
  expect(files.filter((f) => !isTestFile(f))).toEqual([
    'src/components/SubMenu.tsx',
    'storybook/Playground.tsx',
    'storybook/stories/Menu.stories.tsx',
    'storybook/stories/SubMenu.stories.tsx',
    'website/components/PlaygroundClient.tsx',
    'website/components/demos/ExampleDemos.tsx',
    'website/components/demos/SidebarDemos.tsx',
  ])
  expect(missingFrom(files, ['tests/Menu.test.tsx', 'tests/SubMenu.test.tsx', 'tests/a11y.test.tsx'])).toEqual([])
}, 120000)

it('the barrel exports MenuGroup and not SubMenu', () => {
  expect(typeof MenuGroup === 'object' || typeof MenuGroup === 'function').toBe(true)
  expect('SubMenu' in pkg).toBe(false)
  // every other export that merely starts with SubMenu keeps its name
  expect(Object.keys(pkg).filter((k) => /SubMenu|MenuGroup/.test(k)).sort()).toEqual(['MenuGroup', 'SubMenuActiveContext', 'SubMenuFR'])
})

it('what names a module, a section or a test keeps the old spelling', () => {
  const { texts } = scan()
  const old = texts.filter((t) => t.name === 'SubMenu')
  const of = (where: Text['where']) => old.filter((t) => t.where === where).map((t) => `${t.file}`).sort()
  const outside = (list: string[]) => list.filter((f) => !isTestFile(f))
  const inTests = (list: string[]) => list.filter(isTestFile)
  expect(fs.existsSync(path.join(REPO, 'src/components/SubMenu.tsx'))).toBe(true)
  expect(fs.existsSync(path.join(REPO, 'src/components/MenuGroup.tsx'))).toBe(false)
  // the module path and the titles stay as they were; a test the agent adds may import the module or carry a title of its own
  expect(outside(of('specifier'))).toEqual(['src/index.ts'])
  expect(missingFrom(inTests(of('specifier')), ['tests/Menu.test.tsx', 'tests/SubMenu.test.tsx', 'tests/a11y.test.tsx'])).toEqual([])
  expect(outside(of('testtitle'))).toEqual([])
  expect(missingFrom(inTests(of('testtitle')), [...Array(5).fill('tests/Menu.test.tsx'), ...Array(4).fill('tests/SubMenu.test.tsx'), ...Array(3).fill('tests/a11y.test.tsx')])).toEqual([])
  expect(of('title')).toEqual(['storybook/stories/SubMenu.stories.tsx'])
  // the order of the storybook sections and the label of the docs page are names of sections too
  expect(read('.storybook/preview.ts')).toContain("order: ['Sidebar', 'Menu', 'MenuItem', 'SubMenu', 'Playground']")
  expect(read('website/pages/docs/components/_meta.tsx')).toContain("'sub-menu': 'SubMenu'")
  expect(old.filter((t) => t.where === 'other' && t.kind !== 'jsxtext').map((t) => t.file).filter((f) => !/\.stories\.tsx$|_meta|preview\.ts/.test(f))).toEqual([])
})

it('the code the website prints for people to copy says MenuGroup', () => {
  const { texts } = scan()
  const printed = (name: string) => texts.filter((t) => t.name === name && /generateCode\.ts$|HeroCodePreview\.tsx$/.test(t.file)).map((t) => `${path.basename(t.file)} ${t.kind}`).sort()
  if (printed('SubMenu').length) throw new Error(`the website still prints SubMenu: ${printed('SubMenu').join(', ')}`)
  expect(printed('MenuGroup')).toEqual(['HeroCodePreview.tsx string', 'HeroCodePreview.tsx string', 'HeroCodePreview.tsx string', 'generateCode.ts template', 'generateCode.ts template'])
  const generated = read('website/components/playground/generateCode.ts')
  expect(generated).toMatch(/^\s+MenuGroup,$/m)
  expect(generated).toContain('<MenuGroup label="Charts">')
})

it('the docs show MenuGroup in their code samples, and the changelogs keep what happened under its old name', () => {
  const files = docs()
  const stale: string[] = []
  let tags = 0
  let imports = 0
  for (const f of files) {
    const text = read(f)
    const old = text.match(/<\/?SubMenu\b/g)?.length ?? 0
    const oldImport = text.split('\n').filter((l) => l.startsWith('import ') && /\bSubMenu\b/.test(l)).length
    if (old || oldImport) stale.push(`${f} (${old} tags, ${oldImport} imports)`)
    tags += text.match(/<\/?MenuGroup\b/g)?.length ?? 0
    imports += text.split('\n').filter((l) => l.startsWith('import ') && /\bMenuGroup\b/.test(l)).length
  }
  if (stale.length) throw new Error(`the docs still show SubMenu: ${stale.join(', ')}`)
  expect(tags).toBe(48)
  expect(imports).toBe(5)
  expect(read('CHANGELOG.md').match(/\bSubMenu\b/g)?.length).toBe(17)
  expect(read('website/pages/docs/changelog.mdx').match(/\bSubMenu\b/g)?.length).toBe(17)
  expect(HISTORY.filter((f) => /<\/?MenuGroup|MenuGroup/.test(read(f)))).toEqual([])
})

it('MenuGroup is a working submenu: it opens, closes, reports and takes a ref', () => {
  const opened: boolean[] = []
  const ref = React.createRef<HTMLLIElement>()
  render(
    h(Sidebar, null, h(Menu, null, h(MenuGroup, { label: 'Charts', ref, onOpenChange: (o: boolean) => opened.push(o) }, h(MenuItem, null, 'Pie')))),
  )
  const button = screen.getByRole('button', { name: /Charts/ })
  expect(button).toHaveAttribute('aria-expanded', 'false')
  fireEvent.click(button)
  expect(button).toHaveAttribute('aria-expanded', 'true')
  fireEvent.click(button)
  expect(button).toHaveAttribute('aria-expanded', 'false')
  expect(opened).toEqual([true, false])
  expect(ref.current?.tagName).toBe('LI')
  expect(ref.current).toHaveClass('ps-submenu-root')
})

it('MenuGroup keeps the behaviour that depends on its place: accordion and a nested group', () => {
  render(
    h(
      Sidebar,
      null,
      h(
        Menu,
        { accordion: true },
        h(MenuGroup, { label: 'One', defaultOpen: true }, h(MenuGroup, { label: 'Inner' }, h(MenuItem, { active: true }, 'Deep'))),
        h(MenuGroup, { label: 'Two' }, h(MenuItem, null, 'Other')),
      ),
    ),
  )
  const [one, inner, two] = ['One', 'Inner', 'Two'].map((n) => screen.getByRole('button', { name: new RegExp(n) }))
  expect(one).toHaveAttribute('aria-expanded', 'true')
  fireEvent.click(two)
  expect(two).toHaveAttribute('aria-expanded', 'true')
  expect(one).toHaveAttribute('aria-expanded', 'false')
  // an active item marks every group above it
  expect(inner.closest('li')).toHaveClass('ps-active')
  expect(one.closest('li')).toHaveClass('ps-active')
})

it('the repository still type-checks, the storybook included', () => {
  expect(typecheck('tsconfig.json')).toBe('')
}, 120000)
