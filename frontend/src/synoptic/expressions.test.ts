import { describe, expect, it } from 'vitest'
import cases from './expression_cases.json'
import { compile, ExpressionError, isValidExpression } from './expressions'

const vars = (value: number | null, status = 'ok') => ({ value, status })

describe('grammaire (cas partagés avec l\'API)', () => {
  it.each(cases.valid)('accepte %j', (text) => {
    expect(() => compile(text)).not.toThrow()
    expect(isValidExpression(text)).toBe(true)
  })

  it.each(cases.invalid)('refuse %j', (text) => {
    expect(() => compile(text)).toThrow(ExpressionError)
    expect(isValidExpression(text)).toBe(false)
  })
})

describe('évaluation', () => {
  it('compare une valeur à un seuil', () => {
    const rule = compile('value > 28')
    expect(rule.test(vars(28.1))).toBe(true)
    expect(rule.test(vars(28))).toBe(false)
    expect(compile('value >= 28').test(vars(28))).toBe(true)
    expect(compile('value <= 5').test(vars(5))).toBe(true)
    expect(compile('value < 5').test(vars(5))).toBe(false)
  })

  it("compare l'état, en texte", () => {
    expect(compile("status != 'ok'").test(vars(1, 'comm_lost'))).toBe(true)
    expect(compile("status != 'ok'").test(vars(1, 'ok'))).toBe(false)
    expect(compile('status == "fault"').test(vars(1, 'fault'))).toBe(true)
  })

  it('combine avec et / ou / non, dans la priorité usuelle', () => {
    expect(compile('value > 28 && status == "ok"').test(vars(30, 'ok'))).toBe(true)
    expect(compile('value > 28 and status == "ok"').test(vars(30, 'fault'))).toBe(false)
    expect(compile('value < 1 || value > 9').test(vars(10))).toBe(true)
    expect(compile('value < 1 or value > 9').test(vars(5))).toBe(false)
    expect(compile('!(value == 0)').test(vars(3))).toBe(true)
    expect(compile('not value == 0').test(vars(0))).toBe(false)
    // && lie plus fort que || : a || (b && c)
    expect(compile('value == 1 || value == 2 && status == "x"').test(vars(1))).toBe(true)
    expect(compile('(value == 1 || value == 2) && status == "x"').test(vars(1))).toBe(false)
  })

  it('calcule avec les quatre opérations et le modulo', () => {
    expect(compile('(value + 1) * 2').evaluate(vars(4))).toBe(10)
    expect(compile('value - 1').evaluate(vars(4))).toBe(3)
    expect(compile('value / 4').evaluate(vars(10))).toBe(2.5)
    expect(compile('value % 3').evaluate(vars(10))).toBe(1)
    expect(compile('-value').evaluate(vars(4))).toBe(-4)
    expect(compile('2 + 3 * 4').evaluate(vars(0))).toBe(14) // priorité de * sur +
    expect(compile('10 - 2 - 3').evaluate(vars(0))).toBe(5) // associativité à gauche
  })

  it('traite une valeur absente sans jamais déclencher de règle', () => {
    for (const text of ['value > 0', 'value < 100', 'value >= 0', 'value <= 0']) {
      expect(compile(text).test(vars(null, 'comm_lost'))).toBe(false)
    }
    expect(compile('value == null').test(vars(null))).toBe(true)
    expect(compile('value != null').test(vars(null))).toBe(false)
    expect(compile('value + 1').evaluate(vars(null))).toBeNull()
    expect(compile('-value').evaluate(vars(null))).toBeNull()
  })

  it('renvoie null plutôt que Infinity ou NaN', () => {
    expect(compile('value / 0').evaluate(vars(1))).toBeNull()
    expect(compile('value % 0').evaluate(vars(1))).toBeNull()
    expect(compile('"a" + 1').evaluate(vars(1))).toBeNull()
  })

  it('n\'exécute jamais de code : un texte de document reste un texte', () => {
    for (const hostile of ['alert(1)', 'window.alert(1)', 'constructor', '__proto__', 'this', 'globalThis', 'value.constructor']) {
      expect(() => compile(hostile)).toThrow(ExpressionError)
    }
    // Un objet global ne peut pas être atteint : seuls value et status existent.
    expect(() => compile('process')).toThrow(/identifiant inconnu/)
    expect(compile("status == 'constructor'").test(vars(1, 'constructor'))).toBe(true)
  })

  it('reste rapide sur une expression profondément imbriquée', () => {
    const text = '('.repeat(100) + 'value > 1' + ')'.repeat(100)
    expect(compile(text).test(vars(2))).toBe(true)
  })

  it('décrit l\'erreur', () => {
    expect(() => compile('')).toThrow(/vide/)
    expect(() => compile('value >')).toThrow(/incomplète/)
    expect(() => compile('value > (1')).toThrow(/parenthèse/)
    expect(() => compile("status == 'ok")).toThrow(/non terminé/)
    expect(() => compile('foo > 1')).toThrow(/identifiant inconnu : foo/)
    expect(() => compile('value $ 1')).toThrow(/caractère inattendu/)
  })
})
