import * as fs from 'fs'
import * as path from 'path'
import * as tsNamespace from 'typescript'

import { byFile, isTestFile, typecheck } from './check_helpers'
import { autoInjectable, container, injectAllWithTransform, injectable, injectWithTransform } from '../src'
import { isTransformDescriptor } from '../src/providers/injection-token'
import { INJECTION_TOKEN_METADATA_KEY } from '../src/reflection-helpers'
import Transform from '../src/types/transform'

const ts: typeof tsNamespace = (tsNamespace as any).default ?? tsNamespace
const REPO = path.resolve(process.env.EVAL_REPO_ROOT ?? process.cwd())

// `transform` is two different members. Transform.transform (src/types/transform.ts) is the method a transformer
// class implements and the container calls on whatever it resolves for a transform token; it becomes `convert`.
// TransformDescriptor.transform (src/providers/injection-token.ts) is the data the decorators store about a
// parameter, the token of the transformer, and it keeps its name: `this.resolve(param.transform).transform(...)`
// reads one and calls the other. The test classes that act as transformers declare `transform` without always
// saying `implements Transform`, and README.md shows two more, which no runner reads. So the check asks the
// compiler which member each `transform` and `convert` is, over every file of src and its tests.
type Hit = { file: string; line: number; role: 'declaration' | 'access' | 'key' | 'name'; owner: string }
let memo: Map<string, Hit[]> | undefined

function ownerOf(decl: tsNamespace.Node | undefined): string {
  if (!decl) return '?'
  for (let p = decl.parent; p; p = p.parent) {
    if ((ts.isClassLike(p) || ts.isInterfaceDeclaration(p)) && p.name) return p.name.text
  }
  return ''
}

function scan(): Map<string, Hit[]> {
  const config = ts.getParsedCommandLineOfConfigFile(path.join(REPO, 'src/__tests__/tsconfig.json'), {}, {
    ...ts.sys,
    onUnRecoverableConfigFileDiagnostic: (d) => {
      throw new Error(ts.flattenDiagnosticMessageText(d.messageText, ' '))
    },
  })!
  const program = ts.createProgram(config.fileNames, { ...config.options, noEmit: true })
  const checker = program.getTypeChecker()
  const found = new Map<string, Hit[]>([
    ['transform', []],
    ['convert', []],
  ])
  for (const sf of program.getSourceFiles()) {
    const file = path.relative(REPO, sf.fileName).split(path.sep).join('/')
    if (file.startsWith('..') || file.includes('node_modules') || sf.isDeclarationFile) continue
    const visit = (node: tsNamespace.Node) => {
      if (ts.isIdentifier(node) && found.has(node.text)) {
        const parent = node.parent
        const named = (parent as any).name === node
        let role: Hit['role'] = 'name'
        if (ts.isPropertyAccessExpression(parent) && named) role = 'access'
        else if ((ts.isMethodDeclaration(parent) || ts.isPropertySignature(parent) || ts.isPropertyDeclaration(parent) || ts.isMethodSignature(parent)) && named) role = 'declaration'
        else if (ts.isPropertyAssignment(parent) && named) role = 'key'
        const decl = checker.getSymbolAtLocation(node)?.declarations?.[0]
        const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1
        found.get(node.text)!.push({ file, line, role, owner: role === 'key' || role === 'name' ? '' : ownerOf(decl) })
      }
      ts.forEachChild(node, visit)
    }
    visit(sf)
  }
  return found
}
const hits = (name: string): Hit[] => {
  if (!memo) memo = scan()
  return memo.get(name)!
}

const asRefs = (list: Hit[]) => list.map((h) => ({ ...h, kind: 'attr' as const, detail: h.owner }))

it('renamed, no alias: only a transformer with convert is called', () => {
  class Bar {}
  class Old {
    public transform(): string {
      return 'old'
    }
  }
  class New implements Transform<Bar, string> {
    public convert(): string {
      return 'new'
    }
  }
  @injectable()
  class UsesNew {
    constructor(@injectWithTransform(Bar, New) public value: string) {}
  }
  @injectable()
  class UsesOld {
    constructor(@injectWithTransform(Bar, Old as any) public value: string) {}
  }
  expect(container.resolve(UsesNew).value).toBe('new')
  // the container no longer falls back on the old name
  expect(() => container.resolve(UsesOld)).toThrow()
  container.reset()
})

it('the descriptor still names the transformer token in a property called transform', () => {
  class Bar {}
  class Convert {
    public convert(bar: Bar, suffix: string): string {
      return suffix
    }
  }
  class Holder {
    constructor(@injectWithTransform(Bar, Convert, 'x') _value: string, @injectAllWithTransform('Bars', Convert, 'y', 'z') _values: string) {}
  }
  const stored = Reflect.getOwnMetadata(INJECTION_TOKEN_METADATA_KEY, Holder)
  expect(stored[0]).toEqual({ token: Bar, transform: Convert, transformArgs: ['x'] })
  expect(stored[1]).toEqual({ token: 'Bars', multiple: true, transform: Convert, transformArgs: ['y', 'z'] })
  expect(isTransformDescriptor(stored[0])).toBe(true)
  expect(isTransformDescriptor({ token: 'a', transform: 'b' })).toBe(true)
  expect(isTransformDescriptor({ token: 'a', convert: 'b' })).toBe(false)
})

it('no stale uses: every transform left is the descriptor, every convert is the transformer method', () => {
  const stale = hits('transform').filter((h) => (h.role === 'declaration' || h.role === 'access') && h.owner !== 'TransformDescriptor')
  if (stale.length) throw new Error(`transform on a Transform is still used: ${byFile(asRefs(stale))}`)
  const descriptor = hits('transform').filter((h) => h.role === 'declaration' && h.owner === 'TransformDescriptor')
  expect(descriptor.map((h) => h.file)).toEqual(['src/providers/injection-token.ts'])
  const reads = hits('transform').filter((h) => h.role === 'access' && !isTestFile(h.file))
  expect(reads.length).toBeGreaterThanOrEqual(6)
  expect([...new Set(reads.map((h) => h.file))].sort()).toEqual(['src/decorators/auto-injectable.ts', 'src/dependency-container.ts'])

  // the new name is counted exactly in the sources: a test the agent adds may declare and call it too
  const declared = hits('convert').filter((h) => h.role === 'declaration')
  expect(declared.filter((h) => !isTestFile(h.file)).map((h) => `${h.file} ${h.owner}`)).toEqual(['src/types/transform.ts Transform'])
  // the test classes that are transformers, with or without `implements Transform`
  expect(declared.filter((h) => h.file === 'src/__tests__/transform.test.ts').length).toBeGreaterThanOrEqual(9)
  const calls = hits('convert').filter((h) => h.role === 'access' && !isTestFile(h.file))
  const unresolved = calls.filter((h) => h.owner !== 'Transform')
  if (unresolved.length) throw new Error(`convert is called on something that is not a Transform: ${byFile(asRefs(unresolved))}`)
  // the container's own resolution of a transform token, and the decorator that resolves one without a container
  expect([...new Set(calls.map((h) => h.file))].sort()).toEqual(['src/decorators/auto-injectable.ts', 'src/dependency-container.ts'])
  expect(calls.length).toBe(6)
}, 120000)

it('the README examples of a transformer, which no runner reads, are renamed too', () => {
  const readme = fs.readFileSync(path.join(REPO, 'README.md'), 'utf8')
  const old = readme.match(/^\s*(?:public\s+)?transform\s*\(.*$/gm) ?? []
  if (old.length) throw new Error(`README.md still shows a transformer with transform(): ${old.map((l) => l.trim().slice(0, 50)).join(' | ')}`)
  expect(readme.match(/^\s*public convert\(/gm)?.length).toBe(2)
  // the decorators keep their names
  expect(readme).toContain('@injectWithTransform(FeatureFlags, FeatureFlagsTransformer')
  expect(readme).toContain('@injectAllWithTransform(Foo, FooTransform)')
})

it('every use still works: the container, @autoInjectable, arguments from the decorator', () => {
  class Foo {
    constructor(public bar: string) {}
  }
  class FooBar implements Transform<Foo, string> {
    public convert(foo: Foo, suffix: string): string {
      return foo.bar + suffix
    }
  }
  class Join implements Transform<string[], string> {
    public convert(items: string[], glue: string, end: string): string {
      return items.join(glue) + end
    }
  }
  container.register('foo', { useValue: new Foo('f') })
  container.register('item', { useValue: 'a' })
  container.register('item', { useValue: 'b' })

  @injectable()
  class Single {
    constructor(@injectWithTransform('foo', FooBar, '!') public value: string) {}
  }
  @injectable()
  class Many {
    constructor(@injectAllWithTransform('item', Join, '-', '.') public value: string) {}
  }
  @autoInjectable()
  class AutoSingle {
    constructor(@injectWithTransform('foo', FooBar, '?') public value?: string) {}
  }
  @autoInjectable()
  class AutoMany {
    constructor(@injectAllWithTransform('item', Join, '+', '=') public value?: string) {}
  }
  expect(container.resolve(Single).value).toBe('f!')
  expect(container.resolve(Many).value).toBe('a-b.')
  expect(new AutoSingle().value).toBe('f?')
  expect(new AutoMany().value).toBe('a+b=')
  container.reset()
})

it('the repository still type-checks, the transformers of the tests included', () => {
  expect(typecheck('src/__tests__/tsconfig.json')).toBe('')
  expect(typecheck('tsconfig.json')).toBe('')
}, 120000)
