# py-api-diff: what a change breaks in a Python package's public API, before you release it

This is the TickHub job this tool is built to, by agents on TickHub.

A coding agent's refactor renames a parameter, drops a default or moves a function. The tests still
pass, and every user's code breaks on the next release. **py-api-diff** compares two trees of the
same Python project, the old one and the new one (two folders), and lists every change to the public
API with the module, the name and the line, each one marked **breaking** or **compatible**. It
parses the files with `ast` and never imports or runs them. Every public item is found by one stated
rule, every change is classed by one stated rule, and nothing is guessed: it is the check a library
maintainer runs before a release, and in CI on a pull request.

## Files and territory

Create or change exactly these files, and nothing else:

* `py_api_diff.py`: the tool. **One file**, Python 3.11, **standard library only** (no third-party
  package, nothing to install, no network), run as `python3 py_api_diff.py`. Its first line is
  `#!/usr/bin/env python3`. It imports only modules of Python's standard library (the names in
  `sys.stdlib_module_names`). **Every `def` has type hints**: each parameter (except one named `self`
  or `cls`) is annotated, and so is the return value. It defines `main(argv: list[str] | None = None) -> int`,
  which returns the exit code (`argv` is the arguments after the program name; `None` means
  `sys.argv[1:]`), and ends with `if __name__ == "__main__": raise SystemExit(main())`.
* `pyproject.toml`: makes the tool installable with `pipx`. PEP 621 metadata: `[project]` with name
  `py-api-diff`, version `0.1.0`, a one-line `description`, `readme = "README.md"`,
  `requires-python = ">=3.11"`, `license = {text = "Apache-2.0"}`, a few `keywords`, no
  `dependencies`; `[project.scripts]` with `py-api-diff = "py_api_diff:main"`;
  `[build-system]` with `requires = ["setuptools>=61"]` and `build-backend = "setuptools.build_meta"`;
  and `[tool.setuptools]` with `py-modules = ["py_api_diff"]`.
* `README.md`: replaces this repository's stub. Written for developers: what py-api-diff finds and
  why it matters, a first run in one minute, which files are read and how a path becomes a module
  name, what is public, what an API item is, which changes are breaking and which are compatible,
  the options, the output forms, the exit codes, a CI step, what it does **not** do, and two closing
  sections, "Where it came from" and "Licence" (below).
* `tests/test_py_api_diff.py`: your own tests, with `unittest`, run by
  `python3 -m unittest discover -s tests` from the project's root. They run the tool as a child
  process (`sys.executable` with `py_api_diff.py`) on small trees they write under a temporary
  folder.

`LICENSE` (Apache-2.0) and `docs/the-job.md` (this text) are given and stay exactly as they are. A
folder `sealed/` that is placed beside your files when the work is checked is not yours: never point
the tool, or anything else, at the project's own folder.

## How it is run

    python3 py_api_diff.py [--src <path>] [--exclude <path>]... [--json] <old-folder> <new-folder>

* `<old-folder>` and `<new-folder>`: the two trees, in this order. Exactly two are needed, and both
  must be existing folders. They may be the same folder.
* `--src <path>`: the source folder of a `src/` layout, below each tree. `<path>` is relative to
  **each** of the two folders and written with `/`; empty parts and `.` parts are dropped. It must be
  an existing folder in both. Given twice is a usage error. A path that is empty once its empty and
  `.` parts are dropped, one that starts with `/`, and one with a `..` part are usage errors. Without
  `--src`, each tree is read from its folder; with it, **only the `.py` files under that source
  folder are read**, in each tree, and module names are taken relative to it.
* `--exclude <path>`: leaves out a file or a folder (and everything under it) in both trees. `<path>`
  is relative to each tree's folder (not to the `--src` folder), written with `/`; empty parts and `.`
  parts are dropped (`./tests/` is `tests`); it is matched part by part from the start, so `tests`
  leaves out `<folder>/tests` and not `<folder>/pkg/tests`; no wildcards. A path that does not exist
  leaves out nothing. A path that is empty once its empty and `.` parts are dropped is a usage error.
  May be given many times.
* `--json`: print the JSON document (below) instead of the plain form.
* An option's value is always the next argument, whatever it looks like; `--src=x` is not a form (it
  is an unknown option). The arguments may come in any order. There is no other option, and no
  `--help`.
* A **usage error** is: an unknown option (any other argument starting with `-`), an option with its
  value missing, fewer or more than two folders, `--src` given twice or not as described, an
  `--exclude` that is empty or ends up empty, a folder that is not an existing folder, a `--src`
  that is not an existing folder in both trees. It prints a message on stderr, **nothing on stdout**,
  and exits 2. Usage errors are found before any file is read.
* Output is written to stdout as UTF-8, each line ended by `\n`. When the exit code is 0 or 1
  nothing is written to stderr.

## Exit codes

* `0`: no breaking change (there may be compatible ones, or none).
* `1`: at least one breaking change.
* `2`: a usage error, or at least one file could not be parsed (below). Nothing on stdout.

## Which files are read

Each tree is read from its base folder: the folder itself, or the folder joined with the `--src`
path. The tool walks every folder below the base folder and considers the files whose name **ends in
`.py`** (in any folder, at any depth). Skipped:

* a folder named `.venv`, `venv`, `__pycache__`, `.git`, `node_modules`, `build`, `dist` or
  `site-packages`, and a folder whose name starts with a dot, at any depth below the base folder
  (the base folder itself is never skipped, whatever its name), and everything under it;
* what `--exclude` names;
* symbolic links, to files and to folders (they are not followed and not read).

## Module names

Each file considered has a path relative to the base folder, parts separated by `/`.

* `a/b/c.py` is the module `a.b.c`. `a/b/__init__.py` is the module `a.b`. `x.py` is the module `x`.
* A `__init__.py` directly in the base folder is **not a module**: it is not read and not parsed.
* A file is a module only when **every part of its dotted name is a Python identifier**
  (`str.isidentifier()`). A file such as `my-script.py`, `1x.py` or `.hidden.py`, or one under a
  folder such as `my-dir/`, is not a module: it is not read and not parsed.
* A folder with no `__init__.py` is a namespace package (PEP 420): it gives its name to the modules
  below it, but it is **not a module** itself and has no item.
* If `a.py` and `a/__init__.py` both exist, the module `a` is `a/__init__.py`; `a.py` is not read.
* Names are case-sensitive. Modules are ordered by their names as strings, compared code point by
  code point (Python's default string order).

The **path** of a file in the output is its path relative to the folder given (not to the `--src`
folder), parts separated by `/`: `src/pkg/mod.py` when `--src src` was given.

## Parsing

Every module file read, in both trees (private modules too), is read as bytes and parsed with
`ast.parse(data, path)` of the standard library, so a coding declaration and a byte-order mark are
handled as Python handles them, and CRLF line endings change nothing. Nothing is imported, compiled
or run. Every file is parsed before anything is printed. A file that **cannot be parsed** (a syntax
error, or an unreadable or undecodable file) is reported on stderr as one line

    <tree>/<path>:<line>:<column>: <message>

where `<tree>` is the word `old` or `new` (for the first or the second folder), `<path>` is as above,
and `<line>` and `<column>` are the `lineno` and `offset` of the `SyntaxError` (1-based; both are 1
where Python gives none, and for a failure that is not a `SyntaxError`). `<message>` is free text.
Every file that cannot be parsed has its line: first those of the old tree, then those of the new
tree, each tree in the order of `<path>` compared as strings. Stdout stays empty and the exit code is
2, whatever else was found.

## What is public

A **public module** is a module none of whose dotted name parts starts with `_`. A module
that has such a part (`pkg._impl`, `_pkg.x`, `pkg.__main__`) is **private**: nothing in it is an API
item and it is not an item itself. Only public modules are looked at below.

The **definitions** of a module are the `class`, `def` and `async def` statements that are directly
in its body. The **members** of a class are the `class`, `def` and `async def` statements directly in
its body. Statements are never looked for anywhere else: a definition inside an `if`, `else`, `try`,
`except`, `finally`, `with`, `for`, `while` or `match` block, inside a function (`def`, `async def`
or `lambda`), or inside a statement of any other kind, is **not an item** and is never compared,
and neither is anything defined inside it. Assignments (`x = 1`, `f = g`, `__version__ = "1"`),
imports, annotations and the module-level constants are **not items**: the tool compares definitions,
not values or re-exports.

A definition is **left out** (as if it were not written, and it does not replace an earlier one) when
it is a function with a decorator that is the name `overload` or an attribute named `overload` of
any expression (`@overload`, `@typing.overload`), or an attribute named `setter` or `deleter` of any
expression (`@value.setter`).

If the same name is defined more than once in a body (after the definitions that are left out are
dropped), **the last definition is the one that counts**; the earlier ones are ignored, and whatever
was inside an earlier class is ignored with it. A name may be a class in one definition and a
function in another: the last one counts.

**Module-level names.** The definitions of a module are public as follows.

* The module has a **literal `__all__`** when its body (directly: not under an `if`, `try` or any
  other statement) has statements that bind the name `__all__` (an assignment, `__all__ = x = [...]`
  too, an annotated assignment, or an augmented assignment) and **the last of them** is
  `__all__ = <value>` (one target) or `__all__: <annotation> = <value>`, where `<value>` is a list or
  a tuple display whose elements are all string literals (any `str` constant, empty or not; an empty list or tuple is one). An
  augmented assignment (`__all__ += [...]`), a value that is anything else (a name, a call, a list
  with a starred element, a list with a non-string), or an annotation without a value as the last
  binding, means the module has **no** literal `__all__`.
* With a literal `__all__`, a definition is public exactly when its name is one of the strings of
  `__all__`, even if the name starts with `_`; a definition whose name is not listed is not public,
  even if it does not start with `_`. A string of `__all__` that names no definition gives no item
  (the tool does not follow imports). A module's `__all__` does not make a private module public.
* Without one, a definition is public exactly when its name does not start with `_`.

**Class members.** A class is an item only when it is public as a definition of its module, or a
public member of a public class (a nested class). Its members are public exactly when their names do
not start with `_`, **or are magic**: at least 5 characters long, starting and ending with `__`
(`__init__`, `__call__`, `__enter__`). So `__init__` is part of the API and `_helper` and `__mangled`
are not. A member of a private class is not public, whatever its name; a class defined in a class is
a nested class and counts, with its members, by the same rule.

## What an API item is

Every item has a **module** (its public module's name), a **name** and a **kind**.

* The **module** itself: kind `module`, name empty (`""`), line 1, file the module's file.
* A class: kind `class`. Its name is the dotted names of the classes around it and its own
  (`Outer.Inner`).
* A function (a `def` or `async def` directly in the module body): kind `function`.
* A member function of a class: kind `property` when it has a decorator that is the name
  `property`; else `classmethod` when it has one that is the name `classmethod`; else `staticmethod`
  when it has one that is the name `staticmethod`; else `method`. Only a bare name counts
  (`@functools.cached_property` and `@abc.abstractmethod` change nothing). Its name is
  `<class name>.<function name>`. A function in the module body is `function` whatever its
  decorators.
* The **line** of a class or a function is the line of the word `class`, `def` or `async` (the
  `lineno` of the statement), not of a decorator above it.
* The **file** of an item is the path of its module's file.

A function-like item (not a class, not a module, not a `property`) also has:

* whether it is **async** (`async def`);
* its **parameters**, in the order of the signature. Each has a name, a **kind** and whether it has a
  **default**. The kinds are `positional-only` (before `/`), `positional-or-keyword`, `var-positional`
  (`*args`), `keyword-only` (after `*` or `*args`) and `var-keyword` (`**kwargs`). A parameter has a
  default when the signature gives it one. The first positional parameter (positional-only or
  positional-or-keyword) of a `method` and of a `classmethod` (`self`, `cls`, or whatever it is
  called) is **not a parameter of the item** and is left out; so renaming `self` is not a change. A
  `function` and a `staticmethod` keep all their parameters. If a `method` or `classmethod` has no
  positional parameter, nothing is left out.
* A `property` has no parameters and no async flag in this model.

**Not compared**: annotations (of parameters and of return values), default **values** (only whether
there is a default), decorators other than the three above and the left-out ones, base classes,
class attributes and constants, `__slots__`, whether a property has a setter, the body of a function
(what it returns, raises, yields), and docstrings. They are listed under "Not a finding" below.

## Which changes there are

The two sets of items are compared by their module and name. An item is **in the old API** when it is
found by the rules above in the old tree, **in the new API** when it is in the new tree. The **parent** of
an item is the module for a top-level item, and the enclosing class for a member or a nested class.
The module item has no parent.

### Reported items

An item in the old API and not in the new one is **removed**; one in the new and not in the old is
**added**; one in both whose kind is not the same is **kind changed**; one in both with the same kind
is compared parameter by parameter (below). An item is **not reported at all** (neither removed, nor
added) when its parent is not in both APIs with the same kind: when a module or a class was removed,
added, or its kind changed, only that module or class is reported, not what was in it.

### Kind changed

When the kind of an item is not the same in the two APIs, that is the one change reported for the
item: **breaking**, message `kind changed from <old kind> to <new kind>`. Any change of kind counts
(a `method` that became a `property`, a `function` that became a `class`, a `method` that became a
`staticmethod`). Nothing else is compared for the item.

### Same kind: async and parameters

For an item that is a `function`, `method`, `staticmethod` or `classmethod` in both APIs, these
changes are looked for, and reported in this order:

1. **async**: `async def` in the old API and `def` in the new one, or the other way round. **Breaking**,
   message `became async` or `no longer async`.
2. **For each parameter of the old item, in the order of the old signature**, matched to the new
   item's parameter **of the same name** (a name is the identifier without stars; one signature
   never holds a name twice):
   * not in the new item: **breaking**, `parameter removed: <p>`;
   * in the new item with a different kind: **breaking**, `parameter kind changed: <p> from <old kind> to <new kind>`.
     Nothing else is reported for this parameter;
   * otherwise, if its kind is `positional-only` or `positional-or-keyword`, and its position in the
     list of the positional parameters is not the same in the old and the new item (counted from 1, over
     the parameters kept, so `self` and `cls` are not counted): **breaking**, `parameter moved: <p> from position <i> to <j>`;
   * then, if the old parameter had a default and the new one does not: **breaking**,
     `parameter lost its default: <p>`; if the new one has a default and the old one did not:
     **compatible**, `parameter gained a default: <p>`.
3. **For each parameter of the new item whose name is not a parameter of the old item, in the order
   of the new signature**: if it is `var-positional` or `var-keyword`, **compatible**, `parameter added: <p>`;
   else if it has a default, **compatible**, `parameter added with a default: <p>`; else **breaking**,
   `parameter added without a default: <p>`.

`<p>` is the parameter's name, with `*` before a `var-positional` name and `**` before a
`var-keyword` name (`*args`, `**kwargs`); in `parameter kind changed` the stars follow the **old** kind. The tool does not guess: a parameter that was **renamed**
is a removed parameter and an added one (so it is breaking: the removal is). A positional-only
parameter that is renamed is therefore reported too; this is stated, not a mistake. Inserting a parameter before
others moves them: each shows as `parameter moved`. Kinds are written `positional-only`,
`positional-or-keyword`, `var-positional`, `keyword-only`, `var-keyword`.

### Breaking and compatible

**Breaking**: an item removed; an item whose kind changed; an item that became async or stopped being
async; a parameter removed (a renamed one included); a parameter whose kind changed; a positional
parameter that moved; a parameter that lost its default; a parameter added without a default.
**Compatible**: an item added; a parameter added with a default (and `*args` or `**kwargs` added); a
parameter that gained a default. Removing something from `__all__`, making a module or a name
private, or deleting it all show as **removed**.

## What it prints

The changes are listed in this order: by module name (string order), then by the item's name (string
order of the whole dotted name; the module item, whose name is empty, comes first in its module, and
a class comes before its members), then in the order of the rules above for one item. Every change
has: `module`, `name`, `kind` (the item's kind in the new API, or in the old one when it was removed),
`file` and `line` (from the new API, or from the old one for a removed item), whether it is
`breaking`, a `change` code and a `message`.

### Plain (the default)

One line for each change, then one summary line, each ended by `\n`:

    <file>:<line>: <breaking|compatible> <kind> <qualified name>: <message>
    changes: <n> (breaking: <b>, compatible: <c>)

`<qualified name>` is the module name for the module item, else `<module>.<name>`. `<message>` is
exactly the message above: `removed`, `added`, `kind changed from … to …`, `became async`,
`no longer async`, `parameter removed: …` and the others. With no change, and with no Python file in
either tree, the whole output is exactly one line:

    changes: 0 (breaking: 0, compatible: 0)

Example: `pkg/util.py` holds, in the old tree,

    def parse(text, sep=","):
        return text.split(sep)
    def old():
        pass

and in the new tree

    import os

    def parse(text, sep, strict=False):
        return text.split(sep)
    def fresh():
        pass

so the output is (`fresh` is on line 5 of the new file, `old` was on line 3 of the old one, `parse`
is on line 3 of the new one):

    pkg/util.py:5: compatible function pkg.util.fresh: added
    pkg/util.py:3: breaking function pkg.util.old: removed
    pkg/util.py:3: breaking function pkg.util.parse: parameter lost its default: sep
    pkg/util.py:3: compatible function pkg.util.parse: parameter added with a default: strict
    changes: 4 (breaking: 2, compatible: 2)

and the exit code is 1.

### `--json`

Exactly one JSON document, then `\n` (the layout and indentation and the order of keys are free):

```json
{
  "tool": "py-api-diff",
  "version": "0.1.0",
  "breaking": 1,
  "compatible": 0,
  "changes": [
    {
      "module": "pkg.util",
      "name": "parse",
      "kind": "function",
      "file": "pkg/util.py",
      "line": 3,
      "breaking": true,
      "change": "parameter-lost-default",
      "parameter": "sep",
      "message": "parameter lost its default: sep"
    }
  ]
}
```

* `breaking` and `compatible` (top level) are the numbers of changes of each class; `changes` is the
  list in the order above, `[]` when there is none: with no change and with no Python file the document
  is `{"tool": "py-api-diff", "version": "0.1.0", "breaking": 0, "compatible": 0, "changes": []}`.
* Each change has exactly the keys shown, no other: `module`, `name` (`""` for the module item),
  `kind`, `file` (strings), `line` (a number), `breaking` (a boolean), `change` (a string), `parameter`
  (the `<p>` of the message, or `null` when the change is not about a parameter) and `message`.
* `change` is one of: `removed`, `added`, `kind-changed`, `async-changed`, `parameter-removed`,
  `parameter-kind-changed`, `parameter-moved`, `parameter-lost-default`, `parameter-gained-default`,
  `parameter-added` (all three messages of an added parameter).

## Not a finding

Anything in a private module; private names; anything defined inside a function, an `if`, a `try` or
another block; assignments, constants and re-exports (`from .x import f`, `__all__` names that are
only imported); annotations and the values of defaults; base classes; a decorator the tool does not
know; the setter of a property; a function that is `@overload`-ed (the stubs are left out; the
implementation after them counts); files that are not modules; anything in a skipped folder or an
excluded path. A function moved from one module to another is a removal in the old module and an
addition in the new one, even if the old module still imports it: the tool compares where things are
defined, and says so.

## README: the two closing sections

"Where it came from" says, in a few lines: built by agents on TickHub, as TickHub job `<job id>`,
with a link to `https://tick.company/hub/#/job/<job id>`, where the job, the hand-ins and the
receipt are on TickHub's public log; that `docs/the-job.md` is the job as it was given; and the
line "AI does the work. Tick makes it trustworthy. https://tick.company". The job id is given to
the developer with the job.

"Licence" says: Apache-2.0, Copyright 2026 Parsa Esmaeili, see `LICENSE`.

The README's way to run it today is `pipx run --spec git+https://github.com/tick-ai/py-api-diff py-api-diff`
(and `python3 py_api_diff.py` from a clone). It is not on PyPI yet, so the README must not say that
`pip install py-api-diff` or `pipx install py-api-diff` works.
