# parsing/tree_sitter/typescript_parser.py

from __future__ import annotations

import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser

# Create Language objects
_TS_LANGUAGE = Language(tsts.language_typescript())
_TSX_LANGUAGE = Language(tsts.language_tsx())
_JS_LANGUAGE = Language(tsjs.language())

# Construct parsers with the language in the constructor
_TS_PARSER = Parser(_TS_LANGUAGE)
_TSX_PARSER = Parser(_TSX_LANGUAGE)
_JS_PARSER = Parser(_JS_LANGUAGE)


def parse_typescript_source(source: str) -> Node | None:
    try:
        tree = _TS_PARSER.parse(source.encode("utf-8"))
        return tree.root_node
    except Exception:
        return None


def parse_tsx_source(source: str) -> Node | None:
    try:
        tree = _TSX_PARSER.parse(source.encode("utf-8"))
        return tree.root_node
    except Exception:
        return None


def parse_javascript_source(source: str) -> Node | None:
    try:
        tree = _JS_PARSER.parse(source.encode("utf-8"))
        return tree.root_node
    except Exception:
        return None