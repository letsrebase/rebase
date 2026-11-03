// Reads the source files named on the command line, parses each with the repository's
// own `typescript` package and prints what the English lint needs, as JSON on stdout:
//
//   { "<path>": { "identifiers": [[name, line, col], ...], "comments": [[text, line, col], ...] } }
//
// Identifiers are the names the program declares or uses (including JSX attribute and
// tag names); string literals, template text and JSX text are not identifiers and never
// appear, which is what keeps the copy a person reads out of the check. Comments are
// `//` and block comments with their markers removed. The rules live in english_lint.py.
import { readFileSync } from 'node:fs'
import ts from 'typescript'

function scanFile(path) {
  const text = readFileSync(path, 'utf8')
  const kind = /\.(tsx|jsx)$/.test(path) ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  const source = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true, kind)
  const identifiers = []
  const comments = []
  const seen = new Set()

  function addComments(ranges) {
    for (const range of ranges ?? []) {
      if (seen.has(range.pos)) continue
      seen.add(range.pos)
      const raw = text.slice(range.pos, range.end)
      const body =
        range.kind === ts.SyntaxKind.SingleLineCommentTrivia
          ? raw.replace(/^\/\/+/, '')
          : raw.replace(/^\/\*+/, '').replace(/\*+\/$/, '').replace(/^\s*\*+ ?/gm, '')
      const { line, character } = source.getLineAndCharacterOfPosition(range.pos)
      comments.push([body, line + 1, character + 1])
    }
  }

  function visit(node) {
    if (node.kind !== ts.SyntaxKind.JsxText) {
      addComments(ts.getLeadingCommentRanges(text, node.pos))
      addComments(ts.getTrailingCommentRanges(text, node.end))
    }
    if (ts.isIdentifier(node) || ts.isPrivateIdentifier(node)) {
      const { line, character } = source.getLineAndCharacterOfPosition(node.getStart(source))
      identifiers.push([node.text, line + 1, character + 1])
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
  // Comments after the last token hang off the end-of-file token.
  addComments(ts.getLeadingCommentRanges(text, source.endOfFileToken.pos))
  return { identifiers, comments }
}

const result = {}
for (const path of process.argv.slice(2)) {
  try {
    result[path] = scanFile(path)
  } catch (error) {
    result[path] = { error: String(error) }
  }
}
process.stdout.write(JSON.stringify(result))
