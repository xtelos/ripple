import * as fs from 'fs'
import * as path from 'path'
import * as tsNamespace from 'typescript'
import { render } from '@testing-library/react'
import * as React from 'react'

import { isTestFile, missingFrom, typecheck } from './check_helpers'
import { Menu, MenuItem, Sidebar, SubMenu } from '../src'
import { useMenu } from '../src/hooks/useMenu'

const ts: typeof tsNamespace = (tsNamespace as any).default ?? tsNamespace
const REPO = path.resolve(process.env.EVAL_REPO_ROOT ?? process.cwd())
const h = React.createElement

// `transitionDuration` is eight properties spelled alike. The Menu's (MenuContextProps, which `useMenu()` hands to
// SubMenuContent) becomes `slideDuration`; the Sidebar's, the sidebar context's and the styled wrappers' keep their
// name, and a `<Menu transitionDuration>` is only found by asking the compiler which props type the element is
// checked against. Two uses are invisible to it: the context value is an object `Menu` builds without a type, and the
// value the hook test hands to the provider is another, so only running Menu shows whether the name reached them.
function programFiles(): { files: string[]; options: tsNamespace.CompilerOptions } {
  const config = ts.getParsedCommandLineOfConfigFile(path.join(REPO, 'tsconfig.json'), {}, {
    ...ts.sys,
    onUnRecoverableConfigFileDiagnostic: (d) => {
      throw new Error(ts.flattenDiagnosticMessageText(d.messageText, ' '))
    },
  })!
  const files = new Set(config.fileNames)
  // the website is not in the root project: it imports the package by name, which is mapped back to src here
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(path.join(REPO, dir), { withFileTypes: true })) {
      if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue
      const rel = path.join(dir, entry.name)
      if (entry.isDirectory()) walk(rel)
      else if (/\.tsx?$/.test(entry.name)) files.add(path.join(REPO, rel))
    }
  }
  walk('website')
  const options = { ...config.options, noEmit: true, baseUrl: REPO, paths: { 'react-pro-sidebar': ['src/index.ts'], '@/*': ['website/*'] } }
  return { files: [...files], options }
}

type Found = { owner: string; file: string; line: number }
let memo: { declared: Map<string, Found[]>; jsx: { tag: string; attr: string; file: string; line: number }[]; keys: Found[] } | undefined

function scan() {
  if (memo) return memo
  const { files, options } = programFiles()
  const program = ts.createProgram(files, options)
  const checker = program.getTypeChecker()
  const declared = new Map<string, Found[]>()
  const jsx: { tag: string; attr: string; file: string; line: number }[] = []
  const keys: Found[] = []
  for (const sf of program.getSourceFiles()) {
    const file = path.relative(REPO, sf.fileName).split(path.sep).join('/')
    if (file.startsWith('..') || file.includes('node_modules') || sf.isDeclarationFile) continue
    const visit = (node: tsNamespace.Node) => {
      const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1
      if (ts.isPropertySignature(node) && ts.isIdentifier(node.name) && ['transitionDuration', 'slideDuration'].includes(node.name.text)) {
        let owner = ''
        for (let p = node.parent; p; p = p.parent) {
          if ((ts.isInterfaceDeclaration(p) || ts.isTypeAliasDeclaration(p)) && p.name) {
            owner = p.name.text
            break
          }
        }
        declared.set(node.name.text, [...(declared.get(node.name.text) ?? []), { owner, file, line }])
      }
      if (ts.isJsxAttribute(node) && ts.isIdentifier(node.name) && ['transitionDuration', 'slideDuration'].includes(node.name.text)) {
        const element = node.parent.parent as tsNamespace.JsxOpeningLikeElement
        let sym = checker.getSymbolAtLocation(element.tagName)
        if (sym && sym.flags & ts.SymbolFlags.Alias) sym = checker.getAliasedSymbol(sym)
        const decl = sym?.declarations?.[0]
        const tag = decl ? `${path.relative(REPO, decl.getSourceFile().fileName).split(path.sep).join('/')}:${element.tagName.getText()}` : `?:${element.tagName.getText()}`
        jsx.push({ tag, attr: node.name.text, file, line })
      }
      if ((ts.isPropertyAssignment(node) || ts.isShorthandPropertyAssignment(node)) && ts.isIdentifier(node.name) && ['transitionDuration', 'slideDuration'].includes(node.name.text)) {
        keys.push({ owner: node.name.text, file, line })
      }
      ts.forEachChild(node, visit)
    }
    visit(sf)
  }
  memo = { declared, jsx, keys }
  return memo
}

const text = (file: string) => fs.readFileSync(path.join(REPO, file), 'utf8')
const css = () => [...document.querySelectorAll('style')].map((s) => s.textContent ?? '').join('\n')

it('the compiler finds slideDuration on MenuContextProps and transitionDuration on the other five types only', () => {
  const { declared } = scan()
  // the types are the sources': a test the agent adds declares none of its own, and if it does the old name is still checked on it
  expect((declared.get('slideDuration') ?? []).filter((d) => !isTestFile(d.file)).map((d) => `${d.owner} ${d.file}`)).toEqual(['MenuContextProps src/components/Menu.tsx'])
  const still = (declared.get('transitionDuration') ?? []).map((d) => d.owner).sort()
  expect(still).toEqual(['PlaygroundState', 'SidebarContextProps', 'SidebarProps', 'StyledMenuPrefixProps', 'StyledMenuSuffixProps', 'SubMenuContentProps'])
}, 120000)

it('<Menu> takes slideDuration, and every transitionDuration left is on a Sidebar or a styled wrapper', () => {
  const { jsx } = scan()
  const onMenu = jsx.filter((j) => j.tag === 'src/components/Menu.tsx:Menu')
  const stale = onMenu.filter((j) => j.attr === 'transitionDuration')
  if (stale.length) throw new Error(`<Menu transitionDuration> is still written: ${stale.map((j) => `${j.file}:${j.line}`).join(', ')}`)
  // the stories are exact; a test the agent adds may set the new prop on a <Menu> (the suite never does, which is the point of the task)
  expect(onMenu.filter((j) => !isTestFile(j.file)).map((j) => `${j.file} ${j.attr}`)).toEqual(['storybook/stories/Menu.stories.tsx slideDuration'])
  // the Sidebar's own prop is untouched wherever it is set: tests, stories, the website's demos and playground
  const sidebar = jsx.filter((j) => j.attr === 'transitionDuration' && j.tag === 'src/components/Sidebar.tsx:Sidebar').map((j) => j.file)
  expect(sidebar.filter((f) => !isTestFile(f)).sort()).toEqual(['storybook/stories/Sidebar.stories.tsx', 'website/components/PlaygroundClient.tsx', 'website/components/demos/SidebarDemos.tsx'])
  expect(missingFrom(sidebar, ['tests/Sidebar.test.tsx'])).toEqual([])
  // and the styled wrappers still receive theirs inside src
  const wrappers = jsx.filter((j) => j.attr === 'transitionDuration' && j.tag.startsWith('src/styles/')).length
  expect(wrappers).toBeGreaterThanOrEqual(1)
}, 120000)

it('the context value Menu builds and the one the hook test builds carry slideDuration', () => {
  const { keys } = scan()
  // the hook test builds one context value; a test of the agent's own in that file may build more, and none says the old name
  const inTests = keys.filter((k) => k.file === 'tests/hooks.test.tsx')
  expect(inTests.filter((k) => k.owner === 'transitionDuration')).toEqual([])
  expect(inTests.filter((k) => k.owner === 'slideDuration').length).toBeGreaterThanOrEqual(1)
  const inMenu = keys.filter((k) => k.file === 'src/components/Menu.tsx')
  expect(inMenu.map((k) => k.owner)).toEqual(['slideDuration'])
}, 120000)

it('useMenu hands SubMenuContent the duration of <Menu slideDuration>, and the old prop does nothing', () => {
  const seen: any[] = []
  const Probe = () => {
    seen.push(useMenu())
    return null
  }
  render(h(Menu, { slideDuration: 640 } as any, h(Probe)))
  expect(seen.at(-1)).toMatchObject({ slideDuration: 640 })
  expect('transitionDuration' in seen.at(-1)).toBe(false)
  render(h(Menu, { transitionDuration: 877 } as any, h(Probe)))
  expect(seen.at(-1).slideDuration).toBe(300)
  expect(css()).not.toContain('877ms')
})

it('the slide of a submenu follows Menu, the fade of a prefix and the sidebar follow Sidebar', () => {
  const tree = h(
    Sidebar,
    { transitionDuration: 515 } as any,
    h(Menu, { slideDuration: 733 } as any, h(SubMenu, { label: 'Charts', prefix: h('i', null, 'p') } as any, h(MenuItem, { prefix: h('b', null, 'q') } as any, 'Pie'))),
  )
  render(tree)
  const styles = css()
  expect(styles).toMatch(/transition:\s*grid-template-rows 733ms ease/)
  expect(styles).toMatch(/transition:\s*width,\s*left,\s*right,\s*515ms/)
  expect(styles).toMatch(/transition:\s*opacity 515ms/)
  expect(styles).not.toMatch(/grid-template-rows 515ms/)
  expect(styles).not.toMatch(/opacity 733ms/)
})

it('Menu.stories still names its story for the prop it shows, and the history keeps the old name', () => {
  expect(text('storybook/stories/Menu.stories.tsx')).toContain("TransitionDuration.storyName = 'transitionDuration'")
  expect(text('website/pages/docs/changelog.mdx')).toContain("Fixed Menu's `transitionDuration` collision with Sidebar's `transitionDuration`")
  expect(text('CHANGELOG.md').match(/transitionDuration/g)?.length).toBe(4)
})

it('the three documents of the Menu prop say slideDuration, the Sidebar rows keep transitionDuration', () => {
  const readme = text('README.md')
  expect(readme.match(/<td>transitionDuration<\/td>/g)?.length).toBe(1)
  // the cell is renamed; the sentence after it is the author's to reword ("Slide duration ...")
  expect(readme).toMatch(/<td>slideDuration<\/td>\s*<td><code>number<\/code><\/td>\s*<td>/)
  const menu = text('website/pages/docs/components/menu.mdx')
  if (/transitionDuration/.test(menu)) throw new Error('website/pages/docs/components/menu.mdx still names transitionDuration')
  expect(menu.match(/slideDuration/g)?.length).toBeGreaterThanOrEqual(4)
  const api = text('website/pages/api-reference.mdx')
  const [sidebarSection, menuSection] = [api.slice(api.indexOf('## Sidebar'), api.indexOf('## Menu\n')), api.slice(api.indexOf('## Menu\n'), api.indexOf('## MenuItem'))]
  expect(sidebarSection).toContain('| `transitionDuration` |')
  expect(menuSection).toContain('| `slideDuration` |')
  if (menuSection.includes('transitionDuration')) throw new Error('the Menu table of website/pages/api-reference.mdx still names transitionDuration')
  expect(text('website/pages/docs/components/sidebar.mdx').match(/transitionDuration/g)?.length).toBe(2)
})

it('the repository still type-checks, the storybook included', () => {
  expect(typecheck('tsconfig.json')).toBe('')
}, 120000)
