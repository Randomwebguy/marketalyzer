"""Parse the Pine-like script language into a small syntax tree.

The language follows TradingView Pine Script v5 closely enough that common
indicators and strategies can be pasted in: ``//`` comments, ``:=``
reassignment, ``?:`` ternaries, ``and``/``or``/``not``, ``if``/``else if``/``else``
blocks indented by four spaces, ``var`` declarations, ``[a, b] = ...`` tuple
assignment, ``x[1]`` history references and ``f(x) => ...`` functions. Lines
indented by a non-multiple of four spaces continue the previous line, as in Pine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from marketalyzer.scripting.errors import ScriptError

MAX_SOURCE = 50_000
MAX_DEPTH = 24
INDENT = 4

TOKEN_RE = re.compile(
    r"""
     (?P<ws>[ \t]+)
    |(?P<comment>//.*)
    |(?P<num>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)
    |(?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
    |(?P<color>\#[0-9a-fA-F]{8}\b|\#[0-9a-fA-F]{6}\b)
    |(?P<name>[A-Za-z_][A-Za-z0-9_]*)
    |(?P<op>:=|\+=|-=|\*=|/=|%=|==|!=|<=|>=|=>|[-+*/%<>=?:()\[\],.])
    """,
    re.VERBOSE,
)
ESCAPES = {"n": "\n", "t": "\t", '"': '"', "'": "'", "\\": "\\"}
# A line ending in one of these continues on the next line.
CONTINUES = {
    "+", "-", "*", "/", "%", "?", ":", "=", ":=", "+=", "-=", "*=", "/=", "%=",
    "==", "!=", "<", ">", "<=", ">=", ",", "(", "[",
}  # fmt: skip
CONTINUE_WORDS = {"and", "or", "not"}
REASSIGN_OPS = {":=", "+=", "-=", "*=", "/=", "%="}
TYPE_WORDS = {"int", "float", "bool", "string", "color"}
QUALIFIERS = {"series", "simple", "const"}
DRAWING_TYPES = {"line", "label", "box", "table", "linefill", "polyline"}
UNSUPPORTED = {
    "for": "'for' döngüleri desteklenmiyor; seri fonksiyonlarını (ör. ta.highest, math.sum) kullanın.",
    "while": "'while' döngüleri desteklenmiyor.",
    "switch": "'switch' desteklenmiyor; iç içe '?:' ya da 'if/else if' kullanın.",
    "import": "Kütüphane içe aktarma ('import') desteklenmiyor.",
    "export": "'export' desteklenmiyor.",
    "type": "Kullanıcı tanımlı tipler ('type') desteklenmiyor.",
    "method": "'method' tanımları desteklenmiyor.",
    "return": "'return' yok; fonksiyonun son satırı döndürülen değerdir.",
    "break": "'break' desteklenmiyor.",
    "continue": "'continue' desteklenmiyor.",
}
BINARY_POWER = {
    "or": 2,
    "and": 3,
    "==": 4,
    "!=": 4,
    "<": 5,
    ">": 5,
    "<=": 5,
    ">=": 5,
    "+": 6,
    "-": 6,
    "*": 7,
    "/": 7,
    "%": 7,
}
UNARY_POWER = 8


@dataclass(slots=True)
class Token:
    """One lexical token with its 1-based position."""

    kind: str
    value: Any
    line: int
    col: int

    def is_op(self, value: str) -> bool:
        """Return whether this is the operator ``value``."""
        return self.kind == "op" and self.value == value

    def is_word(self, value: str) -> bool:
        """Return whether this is the name or keyword ``value``."""
        return self.kind == "name" and self.value == value

    def describe(self) -> str:
        """Describe the token for an error message."""
        return {
            "newline": "satır sonu",
            "indent": "girinti",
            "dedent": "blok sonu",
            "eof": "dosya sonu",
        }.get(self.kind, repr(self.value))


# --- Syntax tree -----------------------------------------------------------


@dataclass(eq=False, slots=True)
class Node:
    """Base class for syntax tree nodes; compared and hashed by identity."""

    line: int
    col: int


@dataclass(eq=False, slots=True)
class Num(Node):
    """A number literal."""

    value: float | int


@dataclass(eq=False, slots=True)
class Str(Node):
    """A string literal."""

    value: str


@dataclass(eq=False, slots=True)
class Bool(Node):
    """``true`` or ``false``."""

    value: bool


@dataclass(eq=False, slots=True)
class Na(Node):
    """The ``na`` (missing) value."""


@dataclass(eq=False, slots=True)
class Color(Node):
    """A ``#RRGGBB`` or ``#RRGGBBAA`` literal."""

    value: str


@dataclass(eq=False, slots=True)
class Name(Node):
    """A variable or a dotted built-in name such as ``strategy.long``."""

    id: str


@dataclass(eq=False, slots=True)
class Index(Node):
    """``target[offset]``: the value ``offset`` bars ago."""

    target: Node
    offset: Node


@dataclass(eq=False, slots=True)
class Unary(Node):
    """``-x``, ``+x`` or ``not x``."""

    op: str
    operand: Node


@dataclass(eq=False, slots=True)
class Binary(Node):
    """Arithmetic, comparison and logical operators."""

    op: str
    left: Node
    right: Node


@dataclass(eq=False, slots=True)
class Ternary(Node):
    """``cond ? a : b``."""

    cond: Node
    then: Node
    other: Node


@dataclass(eq=False, slots=True)
class TupleLit(Node):
    """``[a, b, c]``: a tuple to return or an options list."""

    items: list[Node]


@dataclass(eq=False, slots=True)
class Call(Node):
    """A function call with positional and keyword arguments."""

    func: str
    args: list[Node]
    kwargs: dict[str, Node]


@dataclass(eq=False, slots=True)
class Assign(Node):
    """``x = expr``, ``var x = expr`` or ``[a, b] = expr``."""

    targets: list[str]
    value: Node
    mode: str = "decl"  # "decl", "var" or "varip"


@dataclass(eq=False, slots=True)
class Reassign(Node):
    """``x := expr`` and compound forms such as ``x += expr``."""

    target: str
    op: str
    value: Node


@dataclass(eq=False, slots=True)
class If(Node):
    """``if`` with an optional ``else`` block (``else if`` nests another If)."""

    cond: Node
    body: list[Node]
    orelse: list[Node] = field(default_factory=list)


@dataclass(eq=False, slots=True)
class ExprStmt(Node):
    """An expression used as a statement, usually a call such as ``plot()``."""

    expr: Node


@dataclass(eq=False, slots=True)
class FuncDef(Node):
    """``name(a, b) => body``; the last statement of the body is the result."""

    name: str
    params: list[str]
    defaults: dict[str, Node]
    body: list[Node]


# --- Tokenizer -------------------------------------------------------------


def _indent_width(text: str) -> int:
    width = 0
    for char in text:
        if char == " ":
            width += 1
        elif char == "\t":
            width += INDENT
        else:
            break
    return width


def _unquote(text: str, line: int, col: int) -> str:
    body = text[1:-1]
    out = []
    chars = iter(body)
    for char in chars:
        if char == "\\":
            escaped = next(chars, "")
            out.append(ESCAPES.get(escaped, escaped))
        else:
            out.append(char)
    return "".join(out)


def _scan_line(text: str, line: int) -> list[Token]:
    tokens = []
    pos = 0
    while pos < len(text):
        match = TOKEN_RE.match(text, pos)
        if not match:
            raise ScriptError(f"Geçersiz karakter: {text[pos]!r}", line, pos + 1)
        kind = match.lastgroup
        value = match.group()
        col = pos + 1
        pos = match.end()
        if kind in ("ws", "comment"):
            continue
        if kind == "num":
            number = float(value)
            is_int = number.is_integer() and not any(c in value for c in ".eE")
            tokens.append(Token("num", int(number) if is_int else number, line, col))
        elif kind == "str":
            tokens.append(Token("str", _unquote(value, line, col), line, col))
        else:
            tokens.append(Token(kind, value, line, col))
    return tokens


def _opens_block(line_tokens: list[Token]) -> bool:
    first, last = line_tokens[0], line_tokens[-1]
    return first.is_word("if") or first.is_word("else") or last.is_op("=>")


def tokenize(source: str) -> list[Token]:
    """Split ``source`` into tokens, with Python-style layout tokens.

    Logical lines end with ``newline`` tokens and blocks are delimited by
    ``indent``/``dedent``. Lines inside brackets, lines after a trailing operator
    and lines indented by a non-multiple of four continue the previous line.
    """
    if len(source) > MAX_SOURCE:
        raise ScriptError(f"Script çok uzun (en fazla {MAX_SOURCE} karakter).")
    tokens: list[Token] = []
    indents = [0]
    depth = 0
    brackets: list[Token] = []
    logical: list[Token] = []
    for number, text in enumerate(source.splitlines(), 1):
        line_tokens = _scan_line(text, number)
        if not line_tokens:
            continue
        width = _indent_width(text)
        last = tokens[-1] if tokens else None
        continues = last is not None and (
            depth > 0
            or (last.kind == "op" and last.value in CONTINUES)
            or (last.kind == "name" and last.value in CONTINUE_WORDS)
        )
        if last is not None and not continues and width % INDENT:
            if logical and _opens_block(logical):
                raise ScriptError(
                    "Blok girintisi 4 boşluk (veya bir tab) olmalı.", number, 1
                )
            continues = True
        if not continues:
            if tokens:
                tokens.append(Token("newline", None, last.line, last.col + 1))
            if width > indents[-1]:
                indents.append(width)
                tokens.append(Token("indent", None, number, 1))
            while width < indents[-1]:
                indents.pop()
                tokens.append(Token("dedent", None, number, 1))
            if width != indents[-1]:
                raise ScriptError("Girinti önceki bloklarla hizalı değil.", number, 1)
            logical = []
        for token in line_tokens:
            if token.kind == "op" and token.value in "([":
                brackets.append(token)
            elif token.kind == "op" and token.value in ")]":
                expected = {"(": ")", "[": "]"}
                if not brackets or expected[brackets[-1].value] != token.value:
                    raise ScriptError(
                        f"Fazladan '{token.value}'.", token.line, token.col
                    )
                brackets.pop()
            depth = len(brackets)
            tokens.append(token)
            logical.append(token)
    if brackets:
        opening = brackets[-1]
        raise ScriptError(f"'{opening.value}' kapatılmamış.", opening.line, opening.col)
    end_line = tokens[-1].line if tokens else 1
    if tokens:
        tokens.append(Token("newline", None, end_line, tokens[-1].col + 1))
    tokens.extend(Token("dedent", None, end_line, 1) for _ in indents[1:])
    tokens.append(Token("eof", None, end_line + 1, 1))
    return tokens


# --- Parser ----------------------------------------------------------------


class Parser:
    """Recursive-descent parser with Pratt-style expression parsing."""

    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    # Token helpers ---------------------------------------------------------

    def peek(self, offset: int = 0) -> Token:
        """Return a token ahead of the cursor without consuming it."""
        index = min(self.pos + offset, len(self.tokens) - 1)
        return self.tokens[index]

    def advance(self) -> Token:
        """Consume and return the current token."""
        token = self.peek()
        self.pos = min(self.pos + 1, len(self.tokens) - 1)
        return token

    def error(self, message: str, token: Token | None = None) -> ScriptError:
        """Build an error located at ``token`` (default: the current token)."""
        token = token or self.peek()
        return ScriptError(message, token.line, token.col)

    def expect_op(self, value: str, message: str | None = None) -> Token:
        """Consume the operator ``value`` or fail."""
        token = self.peek()
        if not token.is_op(value):
            raise self.error(
                message or f"'{value}' bekleniyordu, {token.describe()} bulundu."
            )
        return self.advance()

    def expect_name(self, message: str) -> Token:
        """Consume a name or fail with ``message``."""
        token = self.peek()
        if token.kind != "name":
            raise self.error(message)
        return self.advance()

    def skip_newlines(self) -> None:
        """Skip empty logical lines."""
        while self.peek().kind == "newline":
            self.advance()

    def end_statement(self) -> None:
        """Require the end of a logical line after a simple statement."""
        token = self.peek()
        if token.kind == "newline":
            self.advance()
        elif token.kind not in ("dedent", "eof"):
            raise self.error(f"Beklenmeyen {token.describe()}; satır burada bitmeli.")

    # Statements --------------------------------------------------------------

    def program(self) -> list[Node]:
        """Parse a whole script."""
        statements = []
        self.skip_newlines()
        while self.peek().kind != "eof":
            if self.peek().kind == "dedent":
                self.advance()
                continue
            statements.append(self.statement(0))
            self.skip_newlines()
        return statements

    def block(self, depth: int) -> list[Node]:
        """Parse an indented block after a header line."""
        if self.peek().kind != "newline":
            raise self.error("Blok bir sonraki satırda, girintili başlamalı.")
        self.advance()
        if self.peek().kind != "indent":
            raise self.error("Blok içeriği 4 boşluk girintili olmalı.")
        self.advance()
        body = []
        while self.peek().kind not in ("dedent", "eof"):
            body.append(self.statement(depth + 1))
            self.skip_newlines()
        if self.peek().kind == "dedent":
            self.advance()
        return body

    def statement(self, depth: int) -> Node:
        """Parse one statement."""
        if depth > MAX_DEPTH:
            raise self.error("Bloklar çok derin iç içe geçmiş.")
        token = self.peek()
        if token.kind == "indent":
            raise self.error("Beklenmeyen girinti.")
        if token.kind == "name":
            word = token.value
            if word in UNSUPPORTED:
                raise self.error(UNSUPPORTED[word])
            if word == "if":
                return self.if_statement(depth)
            if word in ("var", "varip"):
                return self.declaration()
            if (word in TYPE_WORDS or word in QUALIFIERS or word in DRAWING_TYPES) and (
                self.typed_declaration_ahead()
            ):
                return self.declaration()
            following = self.peek(1)
            if following.is_op("(") and self.function_ahead():
                return self.function_def(depth)
            if following.is_op("="):
                return self.declaration()
            if following.kind == "op" and following.value in REASSIGN_OPS:
                return self.reassignment()
        if token.is_op("[") and self.tuple_target_ahead():
            return self.tuple_declaration()
        expr = self.expression()
        self.end_statement()
        return ExprStmt(token.line, token.col, expr)

    def typed_declaration_ahead(self) -> bool:
        """Return whether ``[qualifier] type name =`` follows."""
        offset = 0
        while self.peek(offset).kind == "name" and (
            self.peek(offset).value in QUALIFIERS
        ):
            offset += 1
        type_token = self.peek(offset)
        if type_token.kind != "name":
            return False
        if self.peek(offset + 1).is_op("["):
            raise self.error("Diziler (array) desteklenmiyor.", type_token)
        return self.peek(offset + 1).kind == "name" and self.peek(offset + 2).is_op("=")

    def declaration(self) -> Node:
        """Parse ``[var|varip] [qualifier] [type] name = expr``."""
        start = self.peek()
        mode = "decl"
        if start.value in ("var", "varip"):
            mode = self.advance().value
        while self.peek().kind == "name" and self.peek(1).kind == "name":
            word = self.advance()
            if word.value in DRAWING_TYPES:
                raise self.error(
                    f"'{word.value}' çizim nesneleri desteklenmiyor.", word
                )
            if self.peek().is_op("["):
                raise self.error("Diziler (array) desteklenmiyor.")
        name = self.expect_name("Değişken adı bekleniyordu.")
        self.expect_op("=", "Atama için '=' bekleniyordu.")
        value = self.expression()
        self.end_statement()
        return Assign(start.line, start.col, [name.value], value, mode)

    def tuple_target_ahead(self) -> bool:
        """Return whether ``[a, b, ...] =`` follows."""
        offset = 1
        while True:
            if self.peek(offset).kind != "name":
                return False
            closing = self.peek(offset + 1)
            if closing.is_op("]"):
                return self.peek(offset + 2).is_op("=")
            if not closing.is_op(","):
                return False
            offset += 2

    def tuple_declaration(self) -> Node:
        """Parse ``[a, b] = expr``."""
        start = self.expect_op("[")
        names = [self.expect_name("Değişken adı bekleniyordu.").value]
        while self.peek().is_op(","):
            self.advance()
            names.append(self.expect_name("Değişken adı bekleniyordu.").value)
        self.expect_op("]")
        self.expect_op("=")
        value = self.expression()
        self.end_statement()
        return Assign(start.line, start.col, names, value)

    def reassignment(self) -> Node:
        """Parse ``name := expr`` or a compound assignment."""
        name = self.advance()
        op = self.advance().value
        value = self.expression()
        self.end_statement()
        return Reassign(name.line, name.col, name.value, op, value)

    def if_statement(self, depth: int) -> Node:
        """Parse ``if``/``else if``/``else``."""
        start = self.advance()
        cond = self.expression()
        body = self.block(depth)
        orelse: list[Node] = []
        if self.peek().is_word("else"):
            self.advance()
            if self.peek().is_word("if"):
                orelse = [self.if_statement(depth)]
            else:
                orelse = self.block(depth)
        return If(start.line, start.col, cond, body, orelse)

    def function_ahead(self) -> bool:
        """Return whether ``name(...) =>`` follows."""
        offset = 2
        depth = 1
        while depth:
            token = self.peek(offset)
            if token.kind in ("newline", "eof"):
                return False
            if token.is_op("("):
                depth += 1
            elif token.is_op(")"):
                depth -= 1
            offset += 1
        return self.peek(offset).is_op("=>")

    def function_def(self, depth: int) -> Node:
        """Parse a single-line or block function definition."""
        name = self.advance()
        if depth:
            raise self.error("Fonksiyonlar yalnızca en dış seviyede tanımlanabilir.")
        self.expect_op("(")
        params: list[str] = []
        defaults: dict[str, Node] = {}
        while not self.peek().is_op(")"):
            while self.peek().kind == "name" and self.peek(1).kind == "name":
                self.advance()  # type annotations such as ``float x``
            param = self.expect_name("Parametre adı bekleniyordu.").value
            if param in params:
                raise self.error(f"'{param}' parametresi iki kez tanımlanmış.")
            params.append(param)
            if self.peek().is_op("="):
                self.advance()
                defaults[param] = self.expression()
            if not self.peek().is_op(","):
                break
            self.advance()
        self.expect_op(")")
        self.expect_op("=>")
        if self.peek().kind == "newline":
            body = self.block(depth)
        else:
            token = self.peek()
            expr = self.expression()
            self.end_statement()
            body = [ExprStmt(token.line, token.col, expr)]
        if not body or not isinstance(body[-1], ExprStmt):
            raise self.error(
                f"'{name.value}' fonksiyonunun son satırı bir değer olmalı.", name
            )
        return FuncDef(name.line, name.col, name.value, params, defaults, body)

    # Expressions -------------------------------------------------------------

    def expression(self, power: int = 0) -> Node:
        """Parse an expression whose operators bind tighter than ``power``."""
        left = self.prefix()
        while True:
            token = self.peek()
            if token.is_op("?") and power < 1:
                self.advance()
                then = self.expression()
                self.expect_op(":", "Üçlü ifadede ':' bekleniyordu.")
                other = self.expression()
                left = Ternary(token.line, token.col, left, then, other)
                continue
            op = token.value if token.kind in ("op", "name") else None
            if op in BINARY_POWER and BINARY_POWER[op] > power:
                self.advance()
                right = self.expression(BINARY_POWER[op])
                left = Binary(token.line, token.col, op, left, right)
                continue
            return left

    def prefix(self) -> Node:
        """Parse a literal, name, group, tuple or unary expression."""
        token = self.advance()
        if token.kind == "num":
            node: Node = Num(token.line, token.col, token.value)
        elif token.kind == "str":
            node = Str(token.line, token.col, token.value)
        elif token.kind == "color":
            node = Color(token.line, token.col, token.value.lower())
        elif token.is_op("("):
            node = self.expression()
            self.expect_op(")")
        elif token.is_op("["):
            items = [self.expression()]
            while self.peek().is_op(","):
                self.advance()
                items.append(self.expression())
            self.expect_op("]")
            node = TupleLit(token.line, token.col, items)
        elif token.is_op("-") or token.is_op("+"):
            operand = self.expression(UNARY_POWER)
            node = Unary(token.line, token.col, token.value, operand)
        elif token.is_word("not"):
            operand = self.expression(UNARY_POWER)
            node = Unary(token.line, token.col, "not", operand)
        elif token.kind == "name":
            node = self.name(token)
        else:
            raise self.error(f"Beklenmeyen {token.describe()}.", token)
        return self.postfix(node)

    def name(self, token: Token) -> Node:
        """Parse a name, joining dotted parts such as ``ta.sma``."""
        word = token.value
        if word in UNSUPPORTED:
            raise self.error(UNSUPPORTED[word], token)
        if word in ("if", "else", "var", "varip", "and", "or"):
            raise self.error(f"'{word}' burada kullanılamaz.", token)
        if word in ("true", "false"):
            return Bool(token.line, token.col, word == "true")
        parts = [word]
        while self.peek().is_op(".") and self.peek(1).kind == "name":
            self.advance()
            parts.append(self.advance().value)
        dotted = ".".join(parts)
        if dotted == "na" and not self.peek().is_op("("):
            return Na(token.line, token.col)
        return Name(token.line, token.col, dotted)

    def postfix(self, node: Node) -> Node:
        """Parse calls and history references after an expression."""
        while True:
            token = self.peek()
            if token.is_op("(") and isinstance(node, Name):
                self.advance()
                node = self.call(node)
            elif token.is_op("["):
                self.advance()
                offset = self.expression()
                self.expect_op("]")
                node = Index(token.line, token.col, node, offset)
            else:
                return node

    def call(self, func: Name) -> Node:
        """Parse call arguments after the opening parenthesis."""
        args: list[Node] = []
        kwargs: dict[str, Node] = {}
        while not self.peek().is_op(")"):
            token = self.peek()
            if token.kind == "name" and self.peek(1).is_op("="):
                self.advance()
                self.advance()
                if token.value in kwargs:
                    raise self.error(f"'{token.value}' argümanı iki kez verilmiş.")
                kwargs[token.value] = self.expression()
            else:
                if kwargs:
                    raise self.error(
                        "Konumsal argüman isimli argümandan sonra gelemez."
                    )
                args.append(self.expression())
            if not self.peek().is_op(","):
                break
            self.advance()
        self.expect_op(")", f"'{func.id}(' kapatılmamış; ')' bekleniyordu.")
        return Call(func.line, func.col, func.id, args, kwargs)


def parse(source: str) -> list[Node]:
    """Parse ``source`` into a list of statements."""
    return Parser(tokenize(source)).program()


def walk(node: Node):
    """Yield ``node`` and every node below it, depth first."""
    yield node
    if isinstance(node, Index):
        yield from walk(node.target)
        yield from walk(node.offset)
    elif isinstance(node, Unary):
        yield from walk(node.operand)
    elif isinstance(node, Binary):
        yield from walk(node.left)
        yield from walk(node.right)
    elif isinstance(node, Ternary):
        yield from walk(node.cond)
        yield from walk(node.then)
        yield from walk(node.other)
    elif isinstance(node, TupleLit):
        for item in node.items:
            yield from walk(item)
    elif isinstance(node, Call):
        for arg in node.args:
            yield from walk(arg)
        for arg in node.kwargs.values():
            yield from walk(arg)
    elif isinstance(node, (Assign, Reassign)):
        yield from walk(node.value)
    elif isinstance(node, ExprStmt):
        yield from walk(node.expr)
    elif isinstance(node, If):
        yield from walk(node.cond)
        for statement in (*node.body, *node.orelse):
            yield from walk(statement)
    elif isinstance(node, FuncDef):
        for default in node.defaults.values():
            yield from walk(default)
        for statement in node.body:
            yield from walk(statement)
