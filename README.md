# py-api-diff

**py-api-diff compares two trees of a Python project and lists every change to the public API that would break its users, with the module, the name and the line, before you release it.**

    pipx run --spec git+https://github.com/tick-ai/py-api-diff py-api-diff <old-folder> <new-folder>

```
$ py-api-diff old new
pkg/util.py:5: compatible function pkg.util.fresh: added
pkg/util.py:3: breaking function pkg.util.old: removed
pkg/util.py:3: breaking function pkg.util.parse: parameter lost its default: sep
pkg/util.py:3: compatible function pkg.util.parse: parameter added with a default: strict
changes: 4 (breaking: 2, compatible: 2)
$ echo $?
1
```

Here `old/pkg/util.py` holds `def parse(text, sep=",")` and `def old()`, and `new/pkg/util.py`
holds `def parse(text, sep, strict=False)` and `def fresh()`. Each change is marked **breaking** or
**compatible**; the exit status is 1 when anything breaks, so it works as a CI step. It uses only
the Python 3.11 standard library, parses the files with `ast` and never imports or runs them, and
the same two trees always give the same output.

## Why

A refactor renames a parameter, drops a default or moves a function. The tests still pass, and every
user's code breaks on the next release. py-api-diff finds these before you release: every public
item is found by one stated rule and every change is classed by one stated rule. Nothing is guessed.

## Run it

    pipx run --spec git+https://github.com/tick-ai/py-api-diff py-api-diff <old-folder> <new-folder>

or, from a clone of this repository (Python 3.11 or later, nothing to install):

    python3 py_api_diff.py [--src <path>] [--exclude <path>]... [--json] <old-folder> <new-folder>

It is not on PyPI yet, so `pip install py-api-diff` and `pipx install py-api-diff` do not work.
(From a clone, `pipx install .` installs the `py-api-diff` command.)

A one-minute first run: check out the last release into one folder and your branch into another
(`git worktree add ../old v1.2.0`), then run `py-api-diff ../old .`.

## Options

| option | meaning |
|---|---|
| `<old-folder> <new-folder>` | the two trees, in this order. Exactly two, both existing folders. They may be the same folder. |
| `--src <path>` | the source folder of a `src/` layout, below each tree. `<path>` is relative to **each** of the two folders and written with `/`; empty and `.` parts are dropped. It must be an existing folder in both. With it, only the `.py` files under it are read and module names are taken relative to it. Given twice, empty, starting with `/` or with a `..` part is a usage error. |
| `--exclude <path>` | leaves out a file or a folder (and everything under it) in both trees. `<path>` is relative to each tree's folder (not to the `--src` folder), written with `/`; empty and `.` parts are dropped (`./tests/` is `tests`). It is matched part by part from the start, so `tests` leaves out `<folder>/tests` and not `<folder>/pkg/tests`. No wildcards. A path that does not exist leaves out nothing; a path that is empty is a usage error. May be given many times. |
| `--json` | print one JSON document instead of the plain form. |

An option's value is always the next argument, whatever it looks like; `--src=x` is not a form (it
is an unknown option). The arguments may come in any order. There is no other option and no `--help`.

## Which files are read

Each tree is read from its base folder: the folder itself, or the folder joined with the `--src`
path. The tool walks every folder below it and considers the files whose name ends in `.py`, at any
depth. Skipped:

* a folder named `.venv`, `venv`, `__pycache__`, `.git`, `node_modules`, `build`, `dist` or
  `site-packages`, and a folder whose name starts with a dot, at any depth below the base folder
  (the base folder itself is never skipped), and everything under it;
* what `--exclude` names;
* symbolic links, to files and to folders (they are not followed and not read).

### Module names

* `a/b/c.py` is the module `a.b.c`. `a/b/__init__.py` is the module `a.b`. `x.py` is the module `x`.
* A `__init__.py` directly in the base folder is not a module: it is not read.
* A file is a module only when every part of its dotted name is a Python identifier
  (`str.isidentifier()`). `my-script.py`, `1x.py`, `.hidden.py` and anything under `my-dir/` are not
  modules and are not read.
* A folder with no `__init__.py` is a namespace package: it gives its name to the modules below it,
  but is not a module and has no item.
* If `a.py` and `a/__init__.py` both exist, the module `a` is `a/__init__.py`; `a.py` is not read.
* Names are case-sensitive. Modules are ordered by name as strings, code point by code point.

The **path** shown in the output is the file's path relative to the folder you gave (not to the
`--src` folder), with `/`: `src/pkg/mod.py` when `--src src` was given.

## What is public

A **public module** is a module none of whose dotted name parts starts with `_`. In a private module
(`pkg._impl`, `_pkg.x`, `pkg.__main__`) nothing is an API item, and it is not an item itself.

The **definitions** of a module are the `class`, `def` and `async def` statements directly in its
body; the **members** of a class are those directly in its body. A definition inside an `if`, `try`,
`with`, `for`, `while` or `match` block, inside a function or a lambda, or inside any other
statement, is not an item and is never compared. Assignments (`x = 1`, `f = g`, `__version__`),
imports, annotations and constants are not items either: py-api-diff compares definitions, not values
or re-exports.

* A function with a decorator `overload` or `x.overload`, or `x.setter` or `x.deleter`, is left out
  as if it were not written.
* If a name is defined more than once in a body, the last definition counts; whatever was inside an
  earlier class is ignored with it.
* **Module level.** With a literal `__all__`, a definition is public exactly when its name is one of
  its strings (even with a leading `_`); a name not listed is not public. Without one, a definition is
  public exactly when its name does not start with `_`. A module has a literal `__all__` when the last
  statement directly in its body that binds `__all__` is `__all__ = [...]` or `__all__ = (...)` (also
  annotated) whose elements are all string literals. `__all__ += [...]`, a name, a call, a starred
  element, a non-string or an annotation without a value mean no literal `__all__`. A string of
  `__all__` that names no definition gives no item (imports are not followed), and `__all__` never
  makes a private module public.
* **Class members** are public when their names do not start with `_`, or are magic (at least 5
  characters, starting and ending with `__`: `__init__`, `__call__`). A class is an item only when it
  is public in its module or a public member of a public class (a nested class, named `Outer.Inner`);
  a member of a private class is not public.

## What an API item is

Every item has a module, a name and a kind:

| kind | what | name |
|---|---|---|
| `module` | the public module itself | `""`, line 1 |
| `class` | a public class | `Outer.Inner` |
| `function` | a `def` or `async def` in the module body, whatever its decorators | `f` |
| `property` | a member function with the decorator name `property` | `Class.f` |
| `classmethod` | else, with the decorator name `classmethod` | `Class.f` |
| `staticmethod` | else, with the decorator name `staticmethod` | `Class.f` |
| `method` | any other member function | `Class.f` |

Only a bare decorator name counts (`@functools.cached_property` and `@abc.abstractmethod` change
nothing). The line is the line of `class`, `def` or `async`, not of a decorator.

A function, method, staticmethod or classmethod also has an **async** flag and its **parameters**, in
signature order, each with a name, a kind (`positional-only`, `positional-or-keyword`,
`var-positional`, `keyword-only`, `var-keyword`) and whether it has a default. The first positional
parameter of a `method` and of a `classmethod` (`self`, `cls`, whatever it is called) is not a
parameter of the item, so renaming `self` is not a change. A property has no parameters.

## Which changes are breaking

Items are compared by module and name. An item in the old API only is **removed**, in the new one
only is **added**; one in both with a different kind is **kind changed**; one in both with the same
kind is compared parameter by parameter. When a module or a class was removed, added or changed
kind, only it is reported, not what was in it.

| change | class | message |
|---|---|---|
| item removed (also: made private, left out of `__all__`) | breaking | `removed` |
| item added | compatible | `added` |
| kind changed (`method` to `property`, `function` to `class`, ...) | breaking | `kind changed from <old kind> to <new kind>` |
| `def` became `async def`, or the other way round | breaking | `became async` / `no longer async` |
| parameter removed (a renamed one included) | breaking | `parameter removed: <p>` |
| parameter kind changed | breaking | `parameter kind changed: <p> from <old kind> to <new kind>` |
| positional parameter moved | breaking | `parameter moved: <p> from position <i> to <j>` |
| parameter lost its default | breaking | `parameter lost its default: <p>` |
| parameter gained a default | compatible | `parameter gained a default: <p>` |
| parameter added with a default, or `*args` / `**kwargs` | compatible | `parameter added with a default: <p>` / `parameter added: <p>` |
| parameter added without a default | breaking | `parameter added without a default: <p>` |

`<p>` is the parameter's name, with `*` or `**` before a var-positional or var-keyword name. For one
item the async finding comes first, then one pass over the old parameters (removed, kind changed, or
moved and then default), then one pass over the new parameters that are not in the old signature.
Positions are counted from 1 over the kept parameters. py-api-diff does not guess: a renamed
parameter is a removed one and an added one, and a parameter inserted before others makes each of
them show as moved. A function moved to another module is a removal in the old module and an addition
in the new one, even if the old module still imports it.

## Output

Findings are listed by module name, then by the whole dotted name of the item (a class before its
members), then in the order above for one item.

### Plain (the default)

    <file>:<line>: <breaking|compatible> <kind> <qualified name>: <message>
    changes: <n> (breaking: <b>, compatible: <c>)

`<qualified name>` is the module name for the module item, else `<module>.<name>`. The file and line
are from the new tree, or from the old one for a removed item. With no change, and with no Python
file in either tree, the whole output is `changes: 0 (breaking: 0, compatible: 0)`.

### `--json`

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

`parameter` is the `<p>` of the message, or `null` when the change is not about a parameter. `change`
is one of `removed`, `added`, `kind-changed`, `async-changed`, `parameter-removed`,
`parameter-kind-changed`, `parameter-moved`, `parameter-lost-default`, `parameter-gained-default`,
`parameter-added`. With no change the document is
`{"tool": "py-api-diff", "version": "0.1.0", "breaking": 0, "compatible": 0, "changes": []}`.

## Exit codes

| code | meaning |
|---|---|
| `0` | no breaking change (there may be compatible ones, or none) |
| `1` | at least one breaking change |
| `2` | a usage error, or at least one file could not be parsed; nothing on stdout |

A usage error (unknown option, an option without its value, not two folders, a folder that does not
exist, a bad `--src` or `--exclude`) prints a message on stderr and is found before any file is read.
Every module file in both trees, private modules included, is parsed before anything is printed. A
file that cannot be parsed is reported on stderr as `<tree>/<path>:<line>:<column>: <message>`, where
`<tree>` is `old` or `new`: first the old tree's, then the new tree's, each ordered by path. With
exit 0 or 1 nothing is written to stderr.

## In CI

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
- name: Public API check
  run: |
    git worktree add ../base origin/${{ github.base_ref }}
    pipx run --spec git+https://github.com/tick-ai/py-api-diff py-api-diff --src src ../base .
```

The step fails when the pull request breaks the public API of the base branch.

## What it does not do

* It does not import or run your code, follow imports or re-exports, or resolve `from x import *`.
* It does not compare annotations, default values (only whether there is a default), decorators other
  than the ones above, base classes, class attributes, constants, `__slots__`, whether a property has
  a setter, function bodies or docstrings.
* It does not look at definitions under `if`, `try` or other blocks, or inside functions.
* It does not guess renames or moves: it reports what is defined where.
* It does not read files that are not modules, private modules, skipped folders, excluded paths or
  symbolic links.

## Where it came from

Built by agents on TickHub, as TickHub job `dff6dc77-6665-4f95-961d-87588c555336`. The job, the
hand-ins and the receipt are on TickHub's public log:
https://tick.company/hub/#/job/dff6dc77-6665-4f95-961d-87588c555336. `docs/the-job.md` is the job as
it was given.

AI does the work. Tick makes it trustworthy. https://tick.company

## Licence

Apache-2.0, Copyright 2026 Parsa Esmaeili, see `LICENSE`.
