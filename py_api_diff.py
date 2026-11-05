#!/usr/bin/env python3
"""py-api-diff: what a change breaks in a Python package's public API.

Compares two trees of the same Python project, found by parsing the files with
``ast`` (nothing is imported or run), and lists every change to the public API,
each one marked breaking or compatible.  Python 3.11, standard library only.
"""

import ast
import json
import os
import sys
import warnings
from typing import Any, NamedTuple

VERSION = "0.1.0"

SKIPPED_DIRS = frozenset(
    {".venv", "venv", "__pycache__", ".git", "node_modules", "build", "dist", "site-packages"}
)
FUNCTION_KINDS = frozenset({"function", "method", "staticmethod", "classmethod"})
POSITIONAL_KINDS = frozenset({"positional-only", "positional-or-keyword"})

USAGE = (
    "usage: py_api_diff.py [--src <path>] [--exclude <path>]... [--json] "
    "<old-folder> <new-folder>"
)


class UsageError(Exception):
    """A command line that cannot be run."""


class Options(NamedTuple):
    src: tuple[str, ...] | None
    excludes: frozenset[tuple[str, ...]]
    as_json: bool
    old: str
    new: str


class Param(NamedTuple):
    name: str
    kind: str
    has_default: bool


class Item(NamedTuple):
    module: str
    name: str
    kind: str
    file: str
    line: int
    is_async: bool
    params: tuple[Param, ...]


class Change(NamedTuple):
    module: str
    name: str
    kind: str
    file: str
    line: int
    breaking: bool
    change: str
    parameter: str | None
    message: str


class ParseFailure(NamedTuple):
    tree: str
    path: str
    line: int
    column: int
    message: str


# --------------------------------------------------------------------------
# command line


def split_path(raw: str) -> tuple[str, ...]:
    """The parts of a path written with '/', empty and '.' parts dropped."""
    return tuple(p for p in raw.split("/") if p not in ("", "."))


def parse_args(argv: list[str]) -> Options:
    src: tuple[str, ...] | None = None
    excludes: set[tuple[str, ...]] = set()
    as_json = False
    folders: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--json":
            as_json = True
        elif arg in ("--src", "--exclude"):
            if i + 1 >= len(argv):
                raise UsageError(f"option {arg} needs a value")
            value = argv[i + 1]
            i += 1
            parts = split_path(value)
            if arg == "--src":
                if src is not None:
                    raise UsageError("--src given more than once")
                if value.startswith("/"):
                    raise UsageError(f"--src must be relative, not {value!r}")
                if ".." in parts:
                    raise UsageError(f"--src must not have a '..' part: {value!r}")
                if not parts:
                    raise UsageError(f"--src is empty: {value!r}")
                src = parts
            else:
                if not parts:
                    raise UsageError(f"--exclude is empty: {value!r}")
                excludes.add(parts)
        elif arg.startswith("-"):
            raise UsageError(f"unknown option: {arg}")
        else:
            folders.append(arg)
        i += 1
    if len(folders) != 2:
        raise UsageError(f"expected exactly two folders, got {len(folders)}")
    for folder in folders:
        if not os.path.isdir(folder):
            raise UsageError(f"not a folder: {folder}")
    if src is not None:
        for folder in folders:
            if not os.path.isdir(os.path.join(folder, *src)):
                raise UsageError(f"--src {'/'.join(src)} is not a folder in {folder}")
    return Options(src, frozenset(excludes), as_json, folders[0], folders[1])


# --------------------------------------------------------------------------
# which files are read


def is_dotted_identifier(dotted: str) -> bool:
    return all(part.isidentifier() for part in dotted.split("."))


def module_name_of(parts: tuple[str, ...]) -> str | None:
    """The module name of a file given by its path parts below the base, or None."""
    if not parts or not parts[-1].endswith(".py"):
        return None
    stem = parts[-1][:-3]
    if stem == "__init__":
        names = parts[:-1]
        if not names:
            return None
    else:
        names = parts[:-1] + (stem,)
    dotted = ".".join(names)
    return dotted if is_dotted_identifier(dotted) else None


def collect_files(
    folder: str, src: tuple[str, ...] | None, excludes: frozenset[tuple[str, ...]]
) -> dict[str, str]:
    """Map module name -> path (relative to the folder given, with '/')."""
    prefix: tuple[str, ...] = src if src is not None else ()
    for k in range(1, len(prefix) + 1):
        if prefix[:k] in excludes:
            return {}
    base = os.path.join(folder, *prefix) if prefix else folder
    candidates: dict[str, list[tuple[str, bool]]] = {}
    stack: list[tuple[str, ...]] = [()]
    while stack:
        rel = stack.pop()
        directory = os.path.join(base, *rel) if rel else base
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            parts = rel + (entry.name,)
            if prefix + parts in excludes:
                continue
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in SKIPPED_DIRS or entry.name.startswith("."):
                        continue
                    if not is_dotted_identifier(entry.name):
                        continue
                    stack.append(parts)
                elif entry.is_file(follow_symlinks=False):
                    name = module_name_of(parts)
                    if name is not None:
                        path = "/".join(prefix + parts)
                        candidates.setdefault(name, []).append(
                            (path, parts[-1] == "__init__.py")
                        )
            except OSError:
                continue
    chosen: dict[str, str] = {}
    for name, options in candidates.items():
        packages = sorted(path for path, is_init in options if is_init)
        chosen[name] = packages[0] if packages else sorted(p for p, _ in options)[0]
    return chosen


# --------------------------------------------------------------------------
# parsing


def parse_file(folder: str, path: str) -> tuple[ast.Module | None, tuple[int, int, str] | None]:
    """Parse one module file; returns (tree, None) or (None, (line, column, message))."""
    try:
        with open(os.path.join(folder, *path.split("/")), "rb") as handle:
            data = handle.read()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(data, path), None
    except SyntaxError as exc:
        line = exc.lineno if isinstance(exc.lineno, int) and exc.lineno else 1
        column = exc.offset if isinstance(exc.offset, int) and exc.offset else 1
        return None, (line, column, one_line(exc.msg or "invalid syntax"))
    except Exception as exc:  # unreadable, undecodable, too deeply nested ...
        return None, (1, 1, one_line(f"{type(exc).__name__}: {exc}"))


def one_line(text: Any) -> str:
    return " ".join(str(text).split())


# --------------------------------------------------------------------------
# what is public, what an item is


Definition = ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef


def is_left_out(node: ast.AST) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "overload":
            return True
        if isinstance(dec, ast.Attribute) and dec.attr in ("overload", "setter", "deleter"):
            return True
    return False


def definitions(body: list[ast.stmt]) -> dict[str, Definition]:
    """The definitions directly in a body; the last one of a name counts."""
    found: dict[str, Definition] = {}
    for node in body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if not is_left_out(node):
                found[node.name] = node
    return found


def binds_all(node: ast.stmt) -> bool:
    def in_target(target: ast.expr) -> bool:
        if isinstance(target, ast.Name):
            return target.id == "__all__"
        if isinstance(target, (ast.Tuple, ast.List)):
            return any(in_target(t) for t in target.elts)
        if isinstance(target, ast.Starred):
            return in_target(target.value)
        return False

    if isinstance(node, ast.Assign):
        return any(in_target(t) for t in node.targets)
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        return in_target(node.target)
    return False


def literal_all(body: list[ast.stmt]) -> frozenset[str] | None:
    """The strings of a literal __all__, or None when the module has none."""
    last: ast.stmt | None = None
    for node in body:
        if binds_all(node):
            last = node
    value: ast.expr | None = None
    if isinstance(last, ast.Assign):
        if len(last.targets) == 1 and isinstance(last.targets[0], ast.Name):
            value = last.value
    elif isinstance(last, ast.AnnAssign):
        if isinstance(last.target, ast.Name):
            value = last.value
    if not isinstance(value, (ast.List, ast.Tuple)):
        return None
    names: set[str] = set()
    for element in value.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            names.add(element.value)
        else:
            return None
    return frozenset(names)


def is_magic(name: str) -> bool:
    return len(name) >= 5 and name.startswith("__") and name.endswith("__")


def is_public_member(name: str) -> bool:
    return not name.startswith("_") or is_magic(name)


def member_kind(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    names = {d.id for d in node.decorator_list if isinstance(d, ast.Name)}
    for kind in ("property", "classmethod", "staticmethod"):
        if kind in names:
            return kind
    return "method"


def signature(node: ast.FunctionDef | ast.AsyncFunctionDef, drop_first: bool) -> tuple[Param, ...]:
    args = node.args
    positional: list[Param] = []
    plain = [(a.arg, "positional-only") for a in args.posonlyargs]
    plain += [(a.arg, "positional-or-keyword") for a in args.args]
    first_default = len(plain) - len(args.defaults)
    for index, (name, kind) in enumerate(plain):
        positional.append(Param(name, kind, index >= first_default))
    if drop_first and positional:
        positional = positional[1:]
    params = positional
    if args.vararg is not None:
        params.append(Param(args.vararg.arg, "var-positional", False))
    for a, default in zip(args.kwonlyargs, args.kw_defaults):
        params.append(Param(a.arg, "keyword-only", default is not None))
    if args.kwarg is not None:
        params.append(Param(args.kwarg.arg, "var-keyword", False))
    return tuple(params)


def function_item(
    module: str,
    name: str,
    kind: str,
    path: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Item:
    if kind == "property":
        return Item(module, name, kind, path, node.lineno, False, ())
    params = signature(node, kind in ("method", "classmethod"))
    return Item(
        module, name, kind, path, node.lineno, isinstance(node, ast.AsyncFunctionDef), params
    )


def add_class(
    items: dict[tuple[str, str], Item], module: str, path: str, qualified: str, node: ast.ClassDef
) -> None:
    items[(module, qualified)] = Item(module, qualified, "class", path, node.lineno, False, ())
    for name, member in definitions(node.body).items():
        if not is_public_member(name):
            continue
        member_name = f"{qualified}.{name}"
        if isinstance(member, ast.ClassDef):
            add_class(items, module, path, member_name, member)
        else:
            kind = member_kind(member)
            items[(module, member_name)] = function_item(module, member_name, kind, path, member)


def module_items(module: str, path: str, tree: ast.Module) -> dict[tuple[str, str], Item]:
    items: dict[tuple[str, str], Item] = {}
    items[(module, "")] = Item(module, "", "module", path, 1, False, ())
    listed = literal_all(tree.body)
    for name, node in definitions(tree.body).items():
        if listed is not None:
            if name not in listed:
                continue
        elif name.startswith("_"):
            continue
        if isinstance(node, ast.ClassDef):
            add_class(items, module, path, name, node)
        else:
            items[(module, name)] = function_item(module, name, "function", path, node)
    return items


def is_public_module(name: str) -> bool:
    return not any(part.startswith("_") for part in name.split("."))


# --------------------------------------------------------------------------
# comparing


def display(param: Param) -> str:
    if param.kind == "var-positional":
        return "*" + param.name
    if param.kind == "var-keyword":
        return "**" + param.name
    return param.name


def positions(params: tuple[Param, ...]) -> dict[str, int]:
    result: dict[str, int] = {}
    for param in params:
        if param.kind in POSITIONAL_KINDS:
            result[param.name] = len(result) + 1
    return result


def compare_function(old: Item, new: Item) -> list[tuple[str, bool, str | None, str]]:
    """The findings for one item of the same function-like kind in both APIs."""
    found: list[tuple[str, bool, str | None, str]] = []
    if old.is_async != new.is_async:
        found.append(
            ("async-changed", True, None, "became async" if new.is_async else "no longer async")
        )
    old_by_name = {p.name: p for p in old.params}
    new_by_name = {p.name: p for p in new.params}
    old_pos = positions(old.params)
    new_pos = positions(new.params)
    for param in old.params:
        label = display(param)
        other = new_by_name.get(param.name)
        if other is None:
            found.append(("parameter-removed", True, label, f"parameter removed: {label}"))
            continue
        if other.kind != param.kind:
            found.append(
                (
                    "parameter-kind-changed",
                    True,
                    label,
                    f"parameter kind changed: {label} from {param.kind} to {other.kind}",
                )
            )
            continue
        if param.kind in POSITIONAL_KINDS and old_pos[param.name] != new_pos[param.name]:
            found.append(
                (
                    "parameter-moved",
                    True,
                    label,
                    f"parameter moved: {label} from position {old_pos[param.name]}"
                    f" to {new_pos[param.name]}",
                )
            )
        if param.has_default and not other.has_default:
            found.append(
                ("parameter-lost-default", True, label, f"parameter lost its default: {label}")
            )
        elif other.has_default and not param.has_default:
            found.append(
                (
                    "parameter-gained-default",
                    False,
                    label,
                    f"parameter gained a default: {label}",
                )
            )
    for param in new.params:
        if param.name in old_by_name:
            continue
        label = display(param)
        if param.kind in ("var-positional", "var-keyword"):
            found.append(("parameter-added", False, label, f"parameter added: {label}"))
        elif param.has_default:
            found.append(
                ("parameter-added", False, label, f"parameter added with a default: {label}")
            )
        else:
            found.append(
                ("parameter-added", True, label, f"parameter added without a default: {label}")
            )
    return found


def compare(
    old: dict[tuple[str, str], Item], new: dict[tuple[str, str], Item]
) -> list[Change]:
    changes: list[Change] = []
    for key in sorted(set(old) | set(new)):
        module, name = key
        before = old.get(key)
        after = new.get(key)
        if name:
            parent_key = (module, name.rpartition(".")[0])
            parent_old = old.get(parent_key)
            parent_new = new.get(parent_key)
            if parent_old is None or parent_new is None or parent_old.kind != parent_new.kind:
                continue
        if after is None and before is not None:
            changes.append(
                Change(module, name, before.kind, before.file, before.line, True,
                       "removed", None, "removed")
            )
        elif before is None and after is not None:
            changes.append(
                Change(module, name, after.kind, after.file, after.line, False,
                       "added", None, "added")
            )
        elif before is not None and after is not None:
            if before.kind != after.kind:
                changes.append(
                    Change(module, name, after.kind, after.file, after.line, True,
                           "kind-changed", None,
                           f"kind changed from {before.kind} to {after.kind}")
                )
            elif after.kind in FUNCTION_KINDS:
                for code, breaking, parameter, message in compare_function(before, after):
                    changes.append(
                        Change(module, name, after.kind, after.file, after.line, breaking,
                               code, parameter, message)
                    )
    return changes


# --------------------------------------------------------------------------
# output


def encode(text: str) -> bytes:
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        return text.encode("utf-8", "surrogateescape" if _escapable(text) else "backslashreplace")


def _escapable(text: str) -> bool:
    return all(not ("\ud800" <= ch <= "\udbff" or "\ude00" <= ch <= "\udfff") for ch in text)


def emit(stream: Any, text: str) -> None:
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        buffer.write(encode(text))
        buffer.flush()
    else:
        stream.write(text)
        stream.flush()


def qualified(change: Change) -> str:
    return f"{change.module}.{change.name}" if change.name else change.module


def render_plain(changes: list[Change]) -> str:
    lines = [
        f"{c.file}:{c.line}: {'breaking' if c.breaking else 'compatible'} "
        f"{c.kind} {qualified(c)}: {c.message}"
        for c in changes
    ]
    breaking = sum(1 for c in changes if c.breaking)
    lines.append(
        f"changes: {len(changes)} (breaking: {breaking}, compatible: {len(changes) - breaking})"
    )
    return "\n".join(lines) + "\n"


def render_json(changes: list[Change]) -> str:
    breaking = sum(1 for c in changes if c.breaking)
    document = {
        "tool": "py-api-diff",
        "version": VERSION,
        "breaking": breaking,
        "compatible": len(changes) - breaking,
        "changes": [
            {
                "module": c.module,
                "name": c.name,
                "kind": c.kind,
                "file": c.file,
                "line": c.line,
                "breaking": c.breaking,
                "change": c.change,
                "parameter": c.parameter,
                "message": c.message,
            }
            for c in changes
        ],
    }
    try:
        text = json.dumps(document, ensure_ascii=False, indent=2)
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = json.dumps(document, ensure_ascii=True, indent=2)
    return text + "\n"


# --------------------------------------------------------------------------
# main


def read_tree(
    label: str, folder: str, options: Options
) -> tuple[dict[tuple[str, str], Item], list[ParseFailure]]:
    files = collect_files(folder, options.src, options.excludes)
    items: dict[tuple[str, str], Item] = {}
    failures: list[ParseFailure] = []
    for name in sorted(files):
        path = files[name]
        tree, failure = parse_file(folder, path)
        if tree is None and failure is not None:
            failures.append(ParseFailure(label, path, failure[0], failure[1], failure[2]))
        elif tree is not None and is_public_module(name):
            items.update(module_items(name, path, tree))
    failures.sort(key=lambda f: f.path)
    return items, failures


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        options = parse_args(args)
    except UsageError as exc:
        emit(sys.stderr, f"py-api-diff: error: {exc}\n{USAGE}\n")
        return 2
    old_items, old_failures = read_tree("old", options.old, options)
    new_items, new_failures = read_tree("new", options.new, options)
    failures = old_failures + new_failures
    if failures:
        emit(
            sys.stderr,
            "".join(
                f"{f.tree}/{f.path}:{f.line}:{f.column}: {f.message}\n" for f in failures
            ),
        )
        return 2
    changes = compare(old_items, new_items)
    emit(sys.stdout, render_json(changes) if options.as_json else render_plain(changes))
    return 1 if any(c.breaking for c in changes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
