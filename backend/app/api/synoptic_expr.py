"""Grammaire des expressions de règles (`when`), partagée avec le viewer (`expressions.ts`).

    or    := and (("||" | "or") and)*
    and   := not (("&&" | "and") not)*
    not   := ("!" | "not") not | cmp
    cmp   := add (("==" | "!=" | "<" | "<=" | ">" | ">=") add)*
    add   := mul (("+" | "-") mul)*
    mul   := unary (("*" | "/" | "%") unary)*
    unary := "-" unary | primary
    primary := nombre | "texte" | 'texte' | true | false | null | value | status | "(" or ")"

Le serveur ne fait que vérifier la syntaxe : l'évaluation est faite par le viewer, sans `eval`.
"""

from __future__ import annotations

import re

IDENTIFIERS = frozenset({"value", "status"})
LITERALS = frozenset({"true", "false", "null"})
WORD_OPERATORS = {"and": "&&", "or": "||", "not": "!"}
_TOKEN = re.compile(
    r"""\s*(?:
        (?P<number>\d+(?:\.\d+)?)
      | (?P<string>'[^'\n]*'|"[^"\n]*")
      | (?P<word>[A-Za-z_][A-Za-z0-9_]*)
      | (?P<op>&&|\|\||==|!=|<=|>=|<|>|!|\+|-|\*|/|%|\(|\))
    )""",
    re.VERBOSE,
)


class ExpressionError(ValueError):
    pass


def _tokens(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        if text[position:].strip() == "":
            break
        match = _TOKEN.match(text, position)
        if match is None or match.end() == position:
            raise ExpressionError(f"caractère inattendu à la position {position}")
        position = match.end()
        kind = match.lastgroup or ""
        value = match.group(kind)
        if kind == "word":
            if value in WORD_OPERATORS:
                tokens.append(("op", WORD_OPERATORS[value]))
            elif value in IDENTIFIERS or value in LITERALS:
                tokens.append(("word", value))
            else:
                raise ExpressionError(f"identifiant inconnu : {value}")
        else:
            tokens.append((kind, value))
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.index = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take_op(self, *operators: str) -> str | None:
        token = self.peek()
        if token and token[0] == "op" and token[1] in operators:
            self.index += 1
            return token[1]
        return None

    def parse(self) -> None:
        if not self.tokens:
            raise ExpressionError("expression vide")
        self.or_()
        if self.peek() is not None:
            raise ExpressionError("expression incomplète ou jeton en trop")

    def or_(self) -> None:
        self.and_()
        while self.take_op("||"):
            self.and_()

    def and_(self) -> None:
        self.not_()
        while self.take_op("&&"):
            self.not_()

    def not_(self) -> None:
        if self.take_op("!"):
            self.not_()
        else:
            self.cmp()

    def cmp(self) -> None:
        self.add()
        while self.take_op("==", "!=", "<=", ">=", "<", ">"):
            self.add()

    def add(self) -> None:
        self.mul()
        while self.take_op("+", "-"):
            self.mul()

    def mul(self) -> None:
        self.unary()
        while self.take_op("*", "/", "%"):
            self.unary()

    def unary(self) -> None:
        if self.take_op("-"):
            self.unary()
        else:
            self.primary()

    def primary(self) -> None:
        token = self.peek()
        if token is None:
            raise ExpressionError("expression incomplète")
        kind, value = token
        if kind in ("number", "string", "word"):
            self.index += 1
        elif self.take_op("("):
            self.or_()
            if not self.take_op(")"):
                raise ExpressionError("parenthèse fermante attendue")
        else:
            raise ExpressionError(f"jeton inattendu : {value}")


def check_expression(text: str) -> None:
    """Lève `ExpressionError` si `text` n'est pas une expression valide."""
    _Parser(_tokens(text)).parse()
