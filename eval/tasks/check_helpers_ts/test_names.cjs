#!/usr/bin/env node
// The grader's view of a test file, for eval/check_node.py: for each test (an it/test call, with the
// describe titles above it joined by " > ") every identifier its call uses or defines, plus the
// modifiers that change whether it runs (skip, todo, only, fails, skipIf, runIf, x-prefixed names, a
// ctx.skip() call). A test's own title is in its names when it is a bare identifier ('works'), so each test also
// reports `title` for a caller that needs its names without it. "<module>" holds the identifiers outside every test
// (imports, helpers, hooks).
//
//   node test_names.cjs <repo> <absolute file>...      prints one JSON object keyed by file
//
// The compiler is the one in the repo's own node_modules, so the parse matches what the repo's
// toolchain accepts. A file that does not parse (or cannot be read) is {"error": "..."}.

const fs = require('fs')
const path = require('path')

const repo = path.resolve(process.argv[2])
const ts = require(require.resolve('typescript', { paths: [repo] }))

const SUITES = new Set(['describe', 'suite', 'xdescribe', 'fdescribe'])
const CASES = new Set(['it', 'test', 'xit', 'xtest', 'fit'])
const IDENT = /^[A-Za-z_$][\w$]*$/
const MOD_BY_PROPERTY = { skip: 'skip', todo: 'todo', only: 'only', skipIf: 'skip', runIf: 'skip', fails: 'fails' }
const MOD_BY_ROOT = { xit: 'skip', xtest: 'skip', xdescribe: 'skip', fit: 'only', fdescribe: 'only' }

function chainOf(callee) {
  // it.skip, it.each(table), it.skipIf(cond): the root name and the properties walked to reach it
  const props = []
  let node = callee
  for (;;) {
    if (ts.isPropertyAccessExpression(node)) {
      props.push(node.name.text)
      node = node.expression
    } else if (ts.isCallExpression(node)) {
      node = node.expression
    } else {
      break
    }
  }
  return ts.isIdentifier(node) ? { root: node.text, props } : null
}

function isCalleeOfAnotherCall(call) {
  let node = call
  let parent = node.parent
  while (parent && ts.isPropertyAccessExpression(parent) && parent.expression === node) {
    node = parent
    parent = parent.parent
  }
  return Boolean(parent && ts.isCallExpression(parent) && parent.expression === node)
}

function titleOf(arg) {
  if (!arg) return null
  if (ts.isStringLiteral(arg) || ts.isNoSubstitutionTemplateLiteral(arg)) return arg.text
  if (ts.isTemplateExpression(arg)) return arg.getText().slice(1, -1)
  if (ts.isIdentifier(arg) || ts.isPropertyAccessExpression(arg)) return arg.getText()
  return null
}

function classify(call) {
  const chain = chainOf(call.expression)
  if (!chain) return null
  const kind = SUITES.has(chain.root) ? 'suite' : CASES.has(chain.root) ? 'case' : null
  if (!kind) return null
  const title = titleOf(call.arguments[0])
  if (title === null) return null
  const mods = []
  if (MOD_BY_ROOT[chain.root]) mods.push(MOD_BY_ROOT[chain.root])
  for (const p of chain.props) if (MOD_BY_PROPERTY[p]) mods.push(MOD_BY_PROPERTY[p])
  const fn = [...call.arguments].reverse().find((a) => ts.isArrowFunction(a) || ts.isFunctionExpression(a))
  return { kind, title, mods, fn }
}

function identifiers(node, into) {
  const visit = (n) => {
    if (ts.isIdentifier(n) || ts.isPrivateIdentifier(n)) into.add(n.text)
    else if ((ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) && IDENT.test(n.text)) into.add(n.text)
    ts.forEachChild(n, visit)
  }
  visit(node)
}

function callsSkipOnContext(fn) {
  // vitest's test context: it('x', (ctx) => { ctx.skip() })
  if (!fn) return false
  const names = new Set(fn.parameters.map((p) => (ts.isIdentifier(p.name) ? p.name.text : null)).filter(Boolean))
  let found = false
  const visit = (n) => {
    if (
      ts.isCallExpression(n) &&
      ts.isPropertyAccessExpression(n.expression) &&
      n.expression.name.text === 'skip' &&
      ts.isIdentifier(n.expression.expression) &&
      names.has(n.expression.expression.text)
    ) {
      found = true
    }
    ts.forEachChild(n, visit)
  }
  visit(fn)
  return found
}

function analyse(file) {
  let text
  try {
    text = fs.readFileSync(file, 'utf8')
  } catch (err) {
    return { error: `cannot read: ${err.message}` }
  }
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true)
  const diagnostics = sf.parseDiagnostics
  if (!Array.isArray(diagnostics)) return { error: 'this typescript does not expose parse diagnostics' }
  if (diagnostics.length) {
    const first = diagnostics[0]
    const line = sf.getLineAndCharacterOfPosition(first.start || 0).line + 1
    return { error: `does not parse: ${ts.flattenDiagnosticMessageText(first.messageText, ' ')} (line ${line})` }
  }
  const tests = {}
  const moduleNames = new Set()
  const unique = (key) => {
    if (!(key in tests)) return key
    let n = 2
    while (`${key} #${n}` in tests) n += 1
    return `${key} #${n}`
  }

  const visit = (node, chain, inherited) => {
    if (ts.isCallExpression(node) && !isCalleeOfAnotherCall(node)) {
      const info = classify(node)
      if (info) {
        const mods = [...inherited, ...info.mods]
        if (info.kind === 'case') {
          const names = new Set()
          identifiers(node, names)
          const effective = callsSkipOnContext(info.fn) ? [...mods, 'skip'] : mods
          tests[unique([...chain, info.title].join(' > '))] = {
            names: [...names].sort(),
            title: info.title,
            mods: [...new Set(effective)].sort(),
            start: node.getStart(sf),
            end: node.getEnd(),
          }
          return
        }
        identifiers(node.expression, moduleNames)
        if (info.fn) ts.forEachChild(info.fn, (c) => visit(c, [...chain, info.title], mods))
        return
      }
    }
    if (ts.isIdentifier(node) || ts.isPrivateIdentifier(node)) moduleNames.add(node.text)
    else if ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) && IDENT.test(node.text)) moduleNames.add(node.text)
    ts.forEachChild(node, (c) => visit(c, chain, inherited))
  }
  visit(sf, [], [])
  return { module: [...moduleNames].sort(), tests }
}

const out = {}
for (const file of process.argv.slice(3)) out[file] = analyse(file)
process.stdout.write(JSON.stringify(out))
