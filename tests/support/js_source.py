"""Read shipped JavaScript in tests, independent of how it is laid out.

``function_source`` returns one named function, however many lines it spans.
While it matches parentheses and braces it skips strings, template literals,
comments and regular-expression literals. It is a test helper, not a parser:
Node still compiles what it returns, so a wrong cut fails loudly.

``compact`` removes all whitespace so a test can look for a fragment of code
whether or not the source was reformatted.
"""

from __future__ import annotations

import re

# Characters after which "/" starts a regular expression rather than division.
_REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_KEYWORDS = ("return", "typeof", "case", "in", "of", "void", "delete", "throw")


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _regex_allowed(text: str, index: int) -> bool:
    before = text[:index].rstrip()
    if not before or before[-1] in _REGEX_AFTER:
        return True
    return any(
        before.endswith(word) and not (before[: -len(word)][-1:].isalnum())
        for word in _REGEX_KEYWORDS
    )


def _skip_literal(text: str, index: int) -> int | None:
    """End index of a string, comment, template or regex starting at index, else None."""
    char, following = text[index], text[index + 1 : index + 2]
    if char == "/" and following == "/":
        end = text.find("\n", index)
        return len(text) if end < 0 else end
    if char == "/" and following == "*":
        end = text.find("*/", index + 2)
        if end < 0:
            raise ValueError("unterminated comment")
        return end + 2
    if char in "'\"":
        i = index + 1
        while i < len(text) and text[i] != char:
            if text[i] == "\n":
                raise ValueError("unterminated string")
            i += 2 if text[i] == "\\" else 1
        return i + 1
    if char == "`":
        i = index + 1
        while i < len(text) and text[i] != "`":
            if text[i] == "\\":
                i += 2
            elif text.startswith("${", i):
                i = _balanced(text, i + 1, "{", "}")
            else:
                i += 1
        return i + 1
    if char == "/" and _regex_allowed(text, index):
        i, in_class = index + 1, False
        while i < len(text):
            c = text[i]
            if c == "\\":
                i += 2
                continue
            if c == "\n":
                raise ValueError("unterminated regular expression")
            if c == "[":
                in_class = True
            elif c == "]":
                in_class = False
            elif c == "/" and not in_class:
                i += 1
                while i < len(text) and text[i].isalpha():
                    i += 1
                return i
            i += 1
        raise ValueError("unterminated regular expression")
    return None


def _balanced(text: str, index: int, opening: str, closing: str) -> int:
    """Index just after the bracket that closes the one at ``index``."""
    if text[index] != opening:
        raise ValueError("expected " + opening)
    depth, i = 0, index
    while i < len(text):
        skipped = _skip_literal(text, i)
        if skipped is not None:
            i = skipped
            continue
        if text[i] == opening:
            depth += 1
        elif text[i] == closing:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced " + opening + closing)


def function_source(text: str, name: str) -> str:
    """The full declaration of the top-level function ``name`` (async or not)."""
    match = re.search(r"(?m)^[ \t]*((?:async\s+)?function\s+" + re.escape(name) + r"\s*)\(", text)
    if match is None:
        raise LookupError("function not found: " + name)
    after_parameters = _balanced(text, match.end() - 1, "(", ")")
    body = after_parameters
    while text[body].isspace():
        body += 1
    return text[match.start(1) : _balanced(text, body, "{", "}")]
