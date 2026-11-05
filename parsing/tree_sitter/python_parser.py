# parsing/tree_sitter/python_parser.py

from __future__ import annotations

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

_PY_LANGUAGE = Language(tspython.language())

# Construct the parser with the language (constructor-based API)
_PY_PARSER = Parser(_PY_LANGUAGE)


def parse_python_source(source: str) -> Node | None:
    """Returns the root node of the parsed syntax tree for one file's source.
    Returns None on parse failure so callers can run fallbacks / record the file.
    """
    try:
        tree = _PY_PARSER.parse(source.encode("utf-8"))
        return tree.root_node
    except Exception:
        return None