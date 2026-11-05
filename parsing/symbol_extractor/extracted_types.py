"""
Shared, language-agnostic extraction result shapes.

Moved out of python_symbols.py (where they originally lived) so that
typescript_symbols.py and any future language extractor can import
them without importing from a module named "python_symbols" — these
dataclasses were never Python-specific to begin with; every language
extractor produces the same shape, which is what lets graph/builder/
stay completely language-unaware.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExtractedFunction:
    name: str
    line_range: tuple[int, int]          # 1-indexed, inclusive
    parent_class: str | None = None      # set if this is a method
    called_names: list[str] = field(default_factory=list)  # unresolved call targets found in the body
    # NEW — feeds parsing/semantic/attribute_call_resolver.py:
    attribute_calls: list[tuple[str, str]] = field(default_factory=list)
    # (base_name, method_name) pairs, e.g. [("self", "validate"), ("repo", "save")]
    # for `self.validate()` / `repo.save()`. Previously these were
    # skipped entirely (not even recorded as unresolved) — this is
    # what makes them resolvable at all.
    local_instantiations: dict[str, str] = field(default_factory=dict)
    # {variable_name: ClassName} for `repo = UserRepository()` style
    # assignments within this function body — lets attribute_calls
    # resolve `repo.save()` to UserRepository.save, not just `self.x()`.
    referenced_names: list[str] = field(default_factory=list)
    # Names referenced as a VALUE, not called — JSX usage (<Foo />)
    # and object-literal registry values ({ home: HomePage }).
    is_route_handler: bool = False
    # True if this function is decorated with something shaped like
    # `@thing.get(...)` / `@thing.post(...)` etc. (FastAPI/Flask-style
    # route registration). Shape-only detection — doesn't know what
    # `thing` is bound to or extract the path/method. Exists purely so
    # analysis/dead_code/dead_code_analysis.py can grant these functions
    # root status: decorators aren't otherwise parsed into any edge,
    # so a route handler with no direct in-graph caller would otherwise
    # always show up as (falsely) dead code.


@dataclass
class ExtractedClass:
    name: str
    line_range: tuple[int, int]
    base_class_names: list[str] = field(default_factory=list)

@dataclass
class ExtractedInterface:
    name: str
    line_range: tuple[int, int]
    properties: list[dict[str, str | bool | None]] = field(default_factory=list)

@dataclass
class ExtractedImport:
    module: str            # e.g. "app.services.user" or "./validators"
    imported_names: list[str] = field(default_factory=list)  # empty for plain `import x`
    line: int = 0


@dataclass
class ExtractedFile:
    functions: list[ExtractedFunction] = field(default_factory=list)
    classes: list[ExtractedClass] = field(default_factory=list)
    imports: list[ExtractedImport] = field(default_factory=list)
    interfaces: list[ExtractedInterface] = field(default_factory=list)
    # {variable_name: ClassName} for `name = ClassName()` assignments
    # sitting at FILE scope (not inside any function or class body) —
    # e.g. `cache_manager = CacheManager()` at the bottom of a module,
    # then imported and used elsewhere as `cache_manager.get_stats()`.
    # Consumed by graph/builder/repo_builder.py's cross-file
    # attribute-call pass.
    module_level_instantiations: dict[str, str] = field(default_factory=dict)
    # Top-level object-literal registry values, e.g. `const routes =
    # { home: HomePage }` — not inside any function, so it needs its
    # own file-scope field, same split as module_level_instantiations
    # above vs. ExtractedFunction.local_instantiations.
    module_level_references: list[str] = field(default_factory=list)
        # Object-literal services, e.g. `export const emailService = { send() {} }`
    # -> {"emailService": ["send"]}. Lets the builder resolve
    # `emailService.send()` calls from other files.
    module_level_objects: dict[str, list[str]] = field(default_factory=dict)
        # `router.post('/x', controller.handler)` -> [("controller", "handler")]
    module_level_member_refs: list[tuple[str, str]] = field(default_factory=list)