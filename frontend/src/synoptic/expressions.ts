/**
 * Expressions des règles d'affichage (`"when": "value > 28 && status == 'ok'"`).
 *
 * Mini-langage évalué sans `eval` ni `Function` : un texte du document ne peut jamais exécuter de
 * code. La grammaire est la même que celle vérifiée par l'API (`synoptic_expr.py`) ; les deux
 * s'appuient sur les mêmes cas de test (`expression_cases.json`).
 *
 *   or    := and (("||" | "or") and)*
 *   and   := not (("&&" | "and") not)*
 *   not   := ("!" | "not") not | cmp
 *   cmp   := add (("==" | "!=" | "<" | "<=" | ">" | ">=") add)*
 *   add   := mul (("+" | "-") mul)*
 *   mul   := unary (("*" | "/" | "%") unary)*
 *   unary := "-" unary | primary
 *   primary := nombre | "texte" | 'texte' | true | false | null | value | status | "(" or ")"
 */

export type ExprValue = number | string | boolean | null

export interface ExprVars {
  value: number | null
  status: string
}

export class ExpressionError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ExpressionError'
  }
}

type Node =
  | { kind: 'literal'; value: ExprValue }
  | { kind: 'variable'; name: 'value' | 'status' }
  | { kind: 'unary'; op: '!' | '-'; operand: Node }
  | { kind: 'binary'; op: string; left: Node; right: Node }

type Token = { kind: 'number' | 'string' | 'word' | 'op'; text: string }

const WORD_OPERATORS: Record<string, string> = { and: '&&', or: '||', not: '!' }
const OPERATORS = ['&&', '||', '==', '!=', '<=', '>=', '<', '>', '!', '+', '-', '*', '/', '%', '(', ')']

function tokenize(text: string): Token[] {
  const tokens: Token[] = []
  let i = 0
  while (i < text.length) {
    const char = text[i] as string
    if (/\s/.test(char)) {
      i++
      continue
    }
    const number = /^\d+(?:\.\d+)?/.exec(text.slice(i))
    if (number) {
      tokens.push({ kind: 'number', text: number[0] })
      i += number[0].length
      continue
    }
    if (char === "'" || char === '"') {
      const end = text.indexOf(char, i + 1)
      const body = end < 0 ? '' : text.slice(i + 1, end)
      if (end < 0 || body.includes('\n')) throw new ExpressionError('texte non terminé')
      tokens.push({ kind: 'string', text: body })
      i = end + 1
      continue
    }
    const word = /^[A-Za-z_][A-Za-z0-9_]*/.exec(text.slice(i))
    if (word) {
      const name = word[0]
      if (name in WORD_OPERATORS) tokens.push({ kind: 'op', text: WORD_OPERATORS[name] as string })
      else if (['value', 'status', 'true', 'false', 'null'].includes(name)) tokens.push({ kind: 'word', text: name })
      else throw new ExpressionError(`identifiant inconnu : ${name}`)
      i += name.length
      continue
    }
    const op = OPERATORS.find((candidate) => text.startsWith(candidate, i))
    if (!op) throw new ExpressionError(`caractère inattendu à la position ${i}`)
    tokens.push({ kind: 'op', text: op })
    i += op.length
  }
  return tokens
}

class Parser {
  private index = 0
  constructor(private readonly tokens: Token[]) {}

  parse(): Node {
    if (this.tokens.length === 0) throw new ExpressionError('expression vide')
    const node = this.or()
    if (this.index < this.tokens.length) throw new ExpressionError('expression incomplète ou jeton en trop')
    return node
  }

  private takeOp(...operators: string[]): string | null {
    const token = this.tokens[this.index]
    if (token?.kind === 'op' && operators.includes(token.text)) {
      this.index++
      return token.text
    }
    return null
  }

  private chain(next: () => Node, ...operators: string[]): Node {
    let left = next()
    for (let op = this.takeOp(...operators); op; op = this.takeOp(...operators)) {
      left = { kind: 'binary', op, left, right: next() }
    }
    return left
  }

  private or = (): Node => this.chain(this.and, '||')
  private and = (): Node => this.chain(this.not, '&&')
  private not = (): Node =>
    this.takeOp('!') ? { kind: 'unary', op: '!', operand: this.not() } : this.cmp()
  private cmp = (): Node => this.chain(this.add, '==', '!=', '<=', '>=', '<', '>')
  private add = (): Node => this.chain(this.mul, '+', '-')
  private mul = (): Node => this.chain(this.unary, '*', '/', '%')
  private unary = (): Node =>
    this.takeOp('-') ? { kind: 'unary', op: '-', operand: this.unary() } : this.primary()

  private primary = (): Node => {
    const token = this.tokens[this.index]
    if (!token) throw new ExpressionError('expression incomplète')
    if (token.kind === 'number') {
      this.index++
      return { kind: 'literal', value: Number(token.text) }
    }
    if (token.kind === 'string') {
      this.index++
      return { kind: 'literal', value: token.text }
    }
    if (token.kind === 'word') {
      this.index++
      if (token.text === 'true') return { kind: 'literal', value: true }
      if (token.text === 'false') return { kind: 'literal', value: false }
      if (token.text === 'null') return { kind: 'literal', value: null }
      return { kind: 'variable', name: token.text as 'value' | 'status' }
    }
    if (this.takeOp('(')) {
      const inner = this.or()
      if (!this.takeOp(')')) throw new ExpressionError('parenthèse fermante attendue')
      return inner
    }
    throw new ExpressionError(`jeton inattendu : ${token.text}`)
  }
}

export interface Compiled {
  evaluate(vars: ExprVars): ExprValue
  /** Vrai si l'expression, évaluée, est vraie (une valeur nulle ou fausse ne déclenche pas la règle). */
  test(vars: ExprVars): boolean
}

/** Analyse une expression ; lève `ExpressionError` si elle est invalide. */
export function compile(text: string): Compiled {
  const tree = new Parser(tokenize(text)).parse()
  return {
    evaluate: (vars) => run(tree, vars),
    test: (vars) => truthy(run(tree, vars)),
  }
}

/** Vrai si `text` est une expression valide. */
export function isValidExpression(text: string): boolean {
  try {
    compile(text)
    return true
  } catch {
    return false
  }
}

function truthy(value: ExprValue): boolean {
  return value !== null && value !== false && value !== 0 && value !== ''
}

function run(node: Node, vars: ExprVars): ExprValue {
  switch (node.kind) {
    case 'literal':
      return node.value
    case 'variable':
      return vars[node.name]
    case 'unary': {
      const operand = run(node.operand, vars)
      if (node.op === '!') return !truthy(operand)
      return typeof operand === 'number' ? -operand : null
    }
    case 'binary':
      return binary(node.op, node.left, node.right, vars)
  }
}

function binary(op: string, leftNode: Node, rightNode: Node, vars: ExprVars): ExprValue {
  if (op === '&&') return truthy(run(leftNode, vars)) && truthy(run(rightNode, vars))
  if (op === '||') return truthy(run(leftNode, vars)) || truthy(run(rightNode, vars))
  const left = run(leftNode, vars)
  const right = run(rightNode, vars)
  switch (op) {
    case '==':
      return left === right
    case '!=':
      return left !== right
    case '<':
    case '<=':
    case '>':
    case '>=':
      // Comparer une valeur absente (point injoignable) ne déclenche aucune règle.
      if (typeof left !== 'number' || typeof right !== 'number') return false
      return op === '<' ? left < right : op === '<=' ? left <= right : op === '>' ? left > right : left >= right
    default: {
      if (typeof left !== 'number' || typeof right !== 'number') return null
      const result = op === '+' ? left + right : op === '-' ? left - right : op === '*' ? left * right : op === '/' ? left / right : left % right
      return Number.isFinite(result) ? result : null
    }
  }
}
