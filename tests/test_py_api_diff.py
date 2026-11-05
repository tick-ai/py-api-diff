"""Tests of py_api_diff.py: the tool is run as a child process on small trees."""

import ast
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "py_api_diff.py"


def write(root: Path, files: dict[str, str | bytes]) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_bytes(content.encode("utf-8"))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.old = self.root / "old"
        self.new = self.root / "new"
        self.old.mkdir()
        self.new.mkdir()

    def run_tool(self, *args: str, cwd: Path | None = None) -> tuple[int, str, str]:
        proc = subprocess.run(
            [sys.executable, str(TOOL), *args],
            cwd=str(cwd or self.root),
            capture_output=True,
            timeout=60,
        )
        return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")

    def diff(self, old: dict[str, str | bytes], new: dict[str, str | bytes], *opts: str) -> tuple[int, list[str]]:
        write(self.old, old)
        write(self.new, new)
        code, out, err = self.run_tool(*opts, str(self.old), str(self.new))
        self.assertEqual(err, "")
        return code, out.splitlines()

    def one(self, old_src: str, new_src: str, name: str = "m.py") -> list[str]:
        """Diff one module, return the lines without the summary."""
        code, lines = self.diff({name: old_src}, {name: new_src})
        self.assertTrue(lines[-1].startswith("changes: "))
        return lines[:-1]

    def assertUsage(self, *args: str) -> None:
        code, out, err = self.run_tool(*args)
        self.assertEqual(code, 2, (out, err))
        self.assertEqual(out, "")
        self.assertNotEqual(err.strip(), "")


class SourceRules(unittest.TestCase):
    def test_source_text(self) -> None:
        text = TOOL.read_bytes().decode("utf-8")
        self.assertTrue(text.startswith("#!/usr/bin/env python3\n"))
        tree = ast.parse(text)
        stdlib = sys.stdlib_module_names
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], stdlib)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                self.assertIn((node.module or "").split(".")[0], stdlib)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                params = a.posonlyargs + a.args + a.kwonlyargs
                params += [x for x in (a.vararg, a.kwarg) if x is not None]
                for p in params:
                    if p.arg not in ("self", "cls"):
                        self.assertIsNotNone(p.annotation, f"{node.name}({p.arg})")
                self.assertIsNotNone(node.returns, node.name)
        mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
        self.assertEqual(len(mains), 1)
        self.assertTrue(text.rstrip().endswith('if __name__ == "__main__":\n    raise SystemExit(main())'))

    def test_pyproject(self) -> None:
        import tomllib

        data = tomllib.loads((TOOL.parent / "pyproject.toml").read_text(encoding="utf-8"))
        proj = data["project"]
        self.assertEqual(proj["name"], "py-api-diff")
        self.assertEqual(proj["version"], "0.1.0")
        self.assertEqual(proj["requires-python"], ">=3.11")
        self.assertEqual(proj["license"], {"text": "Apache-2.0"})
        self.assertNotIn("dependencies", proj)
        self.assertEqual(proj["scripts"], {"py-api-diff": "py_api_diff:main"})
        self.assertEqual(data["tool"]["setuptools"]["py-modules"], ["py_api_diff"])


class Example(Base):
    OLD = {"pkg/util.py": 'def parse(text, sep=","):\n    return text.split(sep)\ndef old():\n    pass\n'}
    NEW = {
        "pkg/util.py": "import os\n\ndef parse(text, sep, strict=False):\n    return text.split(sep)\ndef fresh():\n    pass\n"
    }

    def test_plain_example(self) -> None:
        code, lines = self.diff(self.OLD, self.NEW)
        self.assertEqual(code, 1)
        self.assertEqual(
            lines,
            [
                "pkg/util.py:5: compatible function pkg.util.fresh: added",
                "pkg/util.py:3: breaking function pkg.util.old: removed",
                "pkg/util.py:3: breaking function pkg.util.parse: parameter lost its default: sep",
                "pkg/util.py:3: compatible function pkg.util.parse: parameter added with a default: strict",
                "changes: 4 (breaking: 2, compatible: 2)",
            ],
        )

    def test_json_example(self) -> None:
        write(self.old, self.OLD)
        write(self.new, self.NEW)
        code, out, err = self.run_tool("--json", str(self.old), str(self.new))
        self.assertEqual((code, err), (1, ""))
        self.assertTrue(out.endswith("}\n"))
        doc = json.loads(out)
        self.assertEqual(set(doc), {"tool", "version", "breaking", "compatible", "changes"})
        self.assertEqual((doc["tool"], doc["version"], doc["breaking"], doc["compatible"]), ("py-api-diff", "0.1.0", 2, 2))
        keys = {"module", "name", "kind", "file", "line", "breaking", "change", "parameter", "message"}
        for c in doc["changes"]:
            self.assertEqual(set(c), keys)
        self.assertEqual(
            doc["changes"][2],
            {
                "module": "pkg.util",
                "name": "parse",
                "kind": "function",
                "file": "pkg/util.py",
                "line": 3,
                "breaking": True,
                "change": "parameter-lost-default",
                "parameter": "sep",
                "message": "parameter lost its default: sep",
            },
        )
        self.assertIsNone(doc["changes"][0]["parameter"])
        self.assertEqual(doc["changes"][0]["change"], "added")

    def test_options_in_any_order(self) -> None:
        write(self.old, self.OLD)
        write(self.new, self.NEW)
        code, out, err = self.run_tool(str(self.old), "--json", str(self.new))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["breaking"], 2)

    def test_empty(self) -> None:
        code, lines = self.diff({}, {})
        self.assertEqual((code, lines), (0, ["changes: 0 (breaking: 0, compatible: 0)"]))
        code, out, err = self.run_tool("--json", str(self.old), str(self.new))
        self.assertEqual(
            json.loads(out),
            {"tool": "py-api-diff", "version": "0.1.0", "breaking": 0, "compatible": 0, "changes": []},
        )
        self.assertEqual(code, 0)

    def test_same_folder_and_exit_zero_with_compatible(self) -> None:
        write(self.old, {"a.py": "def f(): pass\n"})
        code, out, err = self.run_tool(str(self.old), str(self.old))
        self.assertEqual((code, out, err), (0, "changes: 0 (breaking: 0, compatible: 0)\n", ""))
        code, lines = self.diff({"a.py": "def f(): pass\n"}, {"a.py": "def f(): pass\ndef g(): pass\n"})
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1], "changes: 1 (breaking: 0, compatible: 1)")


class Files(Base):
    def names(self, files: dict[str, str | bytes], *opts: str) -> list[str]:
        """Modules (as item lines of 'added') of a tree against an empty old tree."""
        write(self.new, files)
        code, out, err = self.run_tool(*opts, str(self.old), str(self.new))
        self.assertEqual((code, err), (0, ""))
        return [l.split(" ")[3].rstrip(":") for l in out.splitlines()[:-1] if " module " in l]

    def test_module_names(self) -> None:
        got = self.names(
            {
                "__init__.py": "",
                "x.py": "",
                "pkg/__init__.py": "",
                "pkg/sub/c.py": "",
                "ns/inner/m.py": "",
                "pkg/sub/__init__.py": "",
            }
        )
        self.assertEqual(got, ["ns.inner.m", "pkg", "pkg.sub", "pkg.sub.c", "x"])

    def test_not_identifiers_not_read(self) -> None:
        bad = b"def ("  # would not parse
        got = self.names(
            {"my-script.py": bad, "1x.py": bad, ".hidden.py": bad, "my-dir/a.py": bad, "ok.py": "", "notpy.txt": bad, "ok2.pyc": bad}
        )
        self.assertEqual(got, ["ok"])

    def test_root_init_not_read(self) -> None:
        got = self.names({"__init__.py": b"def (", "a.py": ""})
        self.assertEqual(got, ["a"])

    def test_package_beats_module(self) -> None:
        write(self.old, {"a.py": "def f(): pass\n", "a/__init__.py": "def g(): pass\n"})
        write(self.new, {"a/__init__.py": "def g(): pass\n"})
        code, out, err = self.run_tool(str(self.old), str(self.new))
        self.assertEqual((code, out), (0, "changes: 0 (breaking: 0, compatible: 0)\n"))
        # a.py is not even parsed
        write(self.old, {"a.py": "def ("})
        code, out, err = self.run_tool(str(self.old), str(self.new))
        self.assertEqual(code, 0, err)

    def test_namespace_package_is_not_a_module(self) -> None:
        got = self.names({"ns/m.py": ""})
        self.assertEqual(got, ["ns.m"])

    def test_skipped_folders(self) -> None:
        bad = b"def ("
        files = {f"{d}/m.py": bad for d in (".venv", "venv", "__pycache__", ".git", "node_modules", "build", "dist", "site-packages", ".other")}
        files["pkg/build/m.py"] = bad
        files["pkg/.cache/m.py"] = bad
        files["keep.py"] = ""
        self.assertEqual(self.names(files), ["keep"])

    def test_base_folder_never_skipped(self) -> None:
        base = self.root / "build"
        (base / "x").mkdir(parents=True)
        (base / "x" / "m.py").write_text("def f(): pass\n")
        code, out, err = self.run_tool(str(base), str(base))
        self.assertEqual((code, err), (0, ""))

    def test_case_sensitive_names(self) -> None:
        self.assertEqual(self.names({"Mod.py": "", "mod.py": ""}), ["Mod", "mod"])

    def test_exclude(self) -> None:
        bad = b"def ("
        write(self.old, {})
        files = {"tests/t.py": bad, "pkg/tests/t.py": "", "pkg/m.py": "", "gen.py": bad}
        got = self.names(files, "--exclude", "tests", "--exclude", "./gen.py")
        self.assertEqual(got, ["pkg.m", "pkg.tests.t"])

    def test_exclude_variants(self) -> None:
        files = {"a/b/c.py": "", "a/d.py": "", "e.py": ""}
        self.assertEqual(self.names(files, "--exclude", "./a//b/"), ["a.d", "e"])
        self.assertEqual(self.names(files, "--exclude", "nothing/here"), ["a.b.c", "a.d", "e"])
        self.assertEqual(self.names(files, "--exclude", "a/b/c.py"), ["a.d", "e"])
        self.assertEqual(self.names(files, "--exclude", "--json"), ["a.b.c", "a.d", "e"])

    def test_exclude_both_trees(self) -> None:
        code, lines = self.diff({"t/a.py": "def f(): pass\n"}, {"t/a.py": "def g(): pass\n"}, "--exclude", "t")
        self.assertEqual((code, lines), (0, ["changes: 0 (breaking: 0, compatible: 0)"]))

    @unittest.skipIf(os.name == "nt", "symbolic links")
    def test_symlinks_not_followed(self) -> None:
        write(self.new, {"real/m.py": "", "real2.py": ""})
        os.symlink(self.new / "real", self.new / "linkdir")
        os.symlink(self.new / "real2.py", self.new / "link.py")
        code, out, err = self.run_tool(str(self.old), str(self.new))
        mods = [l.split(" ")[3].rstrip(":") for l in out.splitlines()[:-1] if " module " in l]
        self.assertEqual(mods, ["real.m", "real2"])

    def test_src(self) -> None:
        old = {"src/pkg/m.py": "def f(a): pass\n", "other/x.py": "def z(): pass\n"}
        new = {"src/pkg/m.py": "def f(): pass\n", "other/x.py": "def y(): pass\n"}
        code, lines = self.diff(old, new, "--src", "./src//")
        self.assertEqual(code, 1)
        self.assertEqual(
            lines,
            ["src/pkg/m.py:1: breaking function pkg.m.f: parameter removed: a", "changes: 1 (breaking: 1, compatible: 0)"],
        )

    def test_src_nested_and_exclude_relative_to_tree(self) -> None:
        old = {"a/src/pkg/m.py": "def f(a): pass\n", "a/src/pkg/t/n.py": "def g(): pass\n"}
        new = {"a/src/pkg/m.py": "def f(): pass\n", "a/src/pkg/t/n.py": "def h(): pass\n"}
        code, lines = self.diff(old, new, "--src", "a/src", "--exclude", "a/src/pkg/t")
        self.assertEqual([l for l in lines if "pkg.t" in l], [])
        self.assertEqual(lines[0], "a/src/pkg/m.py:1: breaking function pkg.m.f: parameter removed: a")
        # an exclude relative to the src folder leaves nothing out
        code, lines = self.diff(old, new, "--src", "a/src", "--exclude", "pkg/t")
        self.assertTrue(any("pkg.t.n.g" in l for l in lines))

    def test_src_excluded_entirely(self) -> None:
        code, lines = self.diff({"src/m.py": "def f(): pass\n"}, {"src/m.py": ""}, "--src", "src", "--exclude", "src")
        self.assertEqual((code, lines), (0, ["changes: 0 (breaking: 0, compatible: 0)"]))

    def test_src_ignores_files_outside(self) -> None:
        write(self.old, {"src/a.py": "", "b.py": b"def ("})
        write(self.new, {"src/a.py": "def f(): pass\n", "b.py": b"def ("})
        code, out, err = self.run_tool("--src", "src", str(self.old), str(self.new))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines()[0], "src/a.py:1: compatible function a.f: added")


class Public(Base):
    def test_private_modules_and_names(self) -> None:
        old = {"pkg/__init__.py": "", "pkg/_impl.py": "", "_pkg/x.py": "", "pkg/__main__.py": ""}
        new = {
            "pkg/_impl.py": "def f(): pass\n",
            "_pkg/x.py": "def f(): pass\n",
            "pkg/__main__.py": "def f(): pass\n",
            "pkg/__init__.py": "def _p(): pass\ndef pub(): pass\nclass _C: pass\n",
        }
        code, lines = self.diff(old, new)
        self.assertEqual(
            lines,
            ["pkg/__init__.py:2: compatible function pkg.pub: added", "changes: 1 (breaking: 0, compatible: 1)"],
        )

    def test_magic_members_and_nested_classes(self) -> None:
        src = (
            "class A:\n"
            "    def __init__(self, x): pass\n"
            "    def __call__(self): pass\n"
            "    def _h(self): pass\n"
            "    def __mangled(self): pass\n"
            "    def ____(self): pass\n"
            "    class In:\n"
            "        def m(self): pass\n"
            "    class _Hid:\n"
            "        def m(self): pass\n"
            "class _P:\n"
            "    def m(self): pass\n"
        )
        lines = self.one("class A:\n    class In: pass\n    class _Hid: pass\nclass _P: pass\n", src)
        self.assertEqual(
            [l.split(": ", 1)[1] for l in lines],
            [
                "compatible method m.A.In.m: added",
                "compatible method m.A.__call__: added",
                "compatible method m.A.__init__: added",
            ],
        )
        lines = self.one("class A: pass\n", src)
        self.assertEqual(
            [l.split(": ", 1)[1] for l in lines],
            [
                "compatible class m.A.In: added",
                "compatible method m.A.__call__: added",
                "compatible method m.A.__init__: added",
            ],
        )

    def test_all_literal(self) -> None:
        src = "__all__ = ['_a', 'b', 'ghost']\ndef _a(): pass\ndef b(): pass\ndef c(): pass\n"
        names = [l.split(": ")[1].split()[-1] for l in self.one("", src)]
        self.assertEqual(names, ["m._a", "m.b"])

    def test_all_literal_tuple_annotated_and_empty(self) -> None:
        for decl in ("__all__ = ('b',)", "__all__: list[str] = ['b']", "__all__ = x = ['b']\n__all__ = ['b']"):
            lines = self.one("", decl + "\ndef b(): pass\ndef c(): pass\n")
            self.assertEqual(len(lines), 1, decl)
        lines = self.one("", "__all__ = []\ndef b(): pass\n")
        self.assertEqual(lines, [])

    def test_all_not_literal(self) -> None:
        for decl in (
            "__all__ = ['b']\n__all__ += ['c']",
            "__all__ = names",
            "__all__ = ['b', *more]",
            "__all__ = ['b', 1]",
            "__all__ = ['b']\n__all__: list[str]",
            "__all__ = ['b'] + ['c']",
            "__all__ = x = ['b']",
            "if X:\n    __all__ = ['b']",
        ):
            lines = self.one("", decl + "\ndef b(): pass\ndef _c(): pass\n")
            self.assertEqual(len(lines), 1, (decl, lines))
            self.assertTrue(lines[0].endswith("m.b: added"), (decl, lines))

    def test_last_all_counts(self) -> None:
        lines = self.one("", "__all__ = ['a']\n__all__ = ['b']\ndef a(): pass\ndef b(): pass\n")
        self.assertEqual([l.split()[-2] for l in lines], ["m.b:"])

    def test_all_does_not_make_module_public(self) -> None:
        lines = self.one("", "__all__ = ['f']\ndef f(): pass\n", name="_m.py")
        self.assertEqual(lines, [])

    def test_not_items(self) -> None:
        src = (
            "import os\nfrom x import f\nX = 1\nf2 = f\n__version__ = '1'\nY: int = 2\n"
            "if True:\n    def a(): pass\n    class C: pass\n"
            "else:\n    def b(): pass\n"
            "try:\n    def c(): pass\nexcept E:\n    def d(): pass\nfinally:\n    def e(): pass\n"
            "with w:\n    def g(): pass\n"
            "for i in r:\n    def h(): pass\n"
            "while t:\n    def i(): pass\n"
            "match v:\n    case 1:\n        def j(): pass\n"
            "def outer():\n    def inner(): pass\n    class K: pass\n"
            "fn = lambda: 1\n"
            "class D:\n    x = 1\n    if t:\n        def m(self): pass\n"
            "    def f(self):\n        def deep(): pass\n"
        )
        lines = self.one("class D: pass\n", src)
        self.assertEqual(
            [l.split(": ", 1)[1] for l in lines],
            [
                "compatible method m.D.f: added",
                "compatible function m.outer: added",
            ],
        )

    def test_overload_and_setters_left_out(self) -> None:
        src = (
            "from typing import overload\nimport typing\n"
            "@overload\ndef f(a: int): ...\n"
            "@typing.overload\ndef f(a: str): ...\n"
            "def f(a): pass\n"
            "@overload\ndef only_stub(): ...\n"
            "class C:\n"
            "    @property\n    def v(self): pass\n"
            "    @v.setter\n    def v(self, x): pass\n"
            "    @v.deleter\n    def v(self): pass\n"
        )
        lines = self.one("class C: pass\n", src)
        self.assertEqual(
            [(l.split(":")[0:2], l.split(": ", 1)[1]) for l in lines],
            [
                (["m.py", "12"], "compatible property m.C.v: added"),
                (["m.py", "7"], "compatible function m.f: added"),
            ],
        )

    def test_overload_does_not_replace_earlier(self) -> None:
        old = "def f(a): pass\n"
        new = "def f(a): pass\nfrom typing import overload\n@overload\ndef f(b): ...\n"
        self.assertEqual(self.one(old, new), [])

    def test_last_definition_counts(self) -> None:
        old = "def f(a): pass\ndef f(b): pass\nclass C:\n    def m(self, x): pass\n    def m(self, y): pass\n"
        new = "def f(b): pass\nclass C:\n    def m(self, y): pass\n"
        self.assertEqual(self.one(old, new), [])
        old = "class C:\n    def gone(self): pass\nclass C:\n    pass\n"
        new = "class C:\n    pass\n"
        self.assertEqual(self.one(old, new), [])
        old = "class f: pass\ndef f(): pass\n"
        new = "def f(): pass\n"
        self.assertEqual(self.one(old, new), [])

    def test_line_is_def_not_decorator(self) -> None:
        lines = self.one("", "@deco\n@deco2\nasync def f(): pass\n@deco\nclass C: pass\n")
        self.assertEqual(lines, ["m.py:5: compatible class m.C: added", "m.py:3: compatible function m.f: added"])


class Kinds(Base):
    def test_item_kinds(self) -> None:
        src = (
            "class C:\n"
            "    @property\n    def p(self): pass\n"
            "    @classmethod\n    def c(cls): pass\n"
            "    @staticmethod\n    def s(): pass\n"
            "    @functools.cached_property\n    def cp(self): pass\n"
            "    @abc.abstractmethod\n    def ab(self): pass\n"
            "    def m(self): pass\n"
            "@property\ndef top(): pass\n"
            "@staticmethod\ndef top2(): pass\n"
        )
        kinds = {" ".join(l.split()[2:4]) for l in self.one("class C: pass\n", src)}
        self.assertEqual(
            kinds,
            {
                "property m.C.p:",
                "classmethod m.C.c:",
                "staticmethod m.C.s:",
                "method m.C.cp:",
                "method m.C.ab:",
                "method m.C.m:",
                "function m.top:",
                "function m.top2:",
            },
        )

    def test_kind_changed(self) -> None:
        old = (
            "class A:\n    def m(self): pass\n    def s(self): pass\n    def t(self, a): pass\n"
            "class K:\n    def inner(self): pass\n"
            "def f(): pass\ndef g(): pass\n"
        )
        new = (
            "class A:\n    @property\n    def m(self): pass\n    @staticmethod\n    def s(): pass\n    @classmethod\n    def t(cls, b): pass\n"
            "def K(): pass\n"
            "class f:\n    def x(self): pass\n"
            "class g: pass\n"
        )
        lines = self.one(old, new)
        self.assertEqual(
            lines,
            [
                "m.py:3: breaking property m.A.m: kind changed from method to property",
                "m.py:5: breaking staticmethod m.A.s: kind changed from method to staticmethod",
                "m.py:7: breaking classmethod m.A.t: kind changed from method to classmethod",
                "m.py:8: breaking function m.K: kind changed from class to function",
                "m.py:9: breaking class m.f: kind changed from function to class",
                "m.py:11: breaking class m.g: kind changed from function to class",
            ],
        )

    def test_kind_changed_json(self) -> None:
        write(self.old, {"m.py": "def f(): pass\n"})
        write(self.new, {"m.py": "class f: pass\n"})
        code, out, err = self.run_tool("--json", str(self.old), str(self.new))
        doc = json.loads(out)
        c = doc["changes"][0]
        self.assertEqual((c["change"], c["kind"], c["parameter"], c["breaking"]), ("kind-changed", "class", None, True))

    def test_module_and_class_removed_added_not_expanded(self) -> None:
        code, lines = self.diff(
            {"pkg/a.py": "class C:\n    def m(self): pass\ndef f(): pass\n", "pkg/b.py": "class K:\n    class In:\n        def z(self): pass\n"},
            {"pkg/c.py": "def g(): pass\n", "pkg/b.py": "K = 1\n"},
        )
        self.assertEqual(
            lines,
            [
                "pkg/a.py:1: breaking module pkg.a: removed",
                "pkg/b.py:1: breaking class pkg.b.K: removed",
                "pkg/c.py:1: compatible module pkg.c: added",
                "changes: 3 (breaking: 2, compatible: 1)",
            ],
        )
        self.assertEqual(code, 1)

    def test_class_added_members_not_expanded_and_removed_member(self) -> None:
        old = "class C:\n    def a(self): pass\n    def b(self): pass\n"
        new = "class C:\n    def a(self): pass\n    def c(self): pass\nclass D:\n    def x(self): pass\n"
        self.assertEqual(
            self.one(old, new),
            [
                "m.py:3: breaking method m.C.b: removed",
                "m.py:3: compatible method m.C.c: added",
                "m.py:4: compatible class m.D: added",
            ],
        )

    def test_made_private_is_removed(self) -> None:
        self.assertEqual(self.one("def f(): pass\n", "def _f(): pass\n"), ["m.py:1: breaking function m.f: removed"])
        self.assertEqual(
            self.one("def f(): pass\ndef g(): pass\n", "__all__ = ['g']\ndef f(): pass\ndef g(): pass\n"),
            ["m.py:1: breaking function m.f: removed"],
        )

    def test_made_module_private_is_removed(self) -> None:
        code, lines = self.diff({"pkg/m.py": "def f(): pass\n"}, {"pkg/_m.py": "def f(): pass\n"})
        self.assertEqual(lines[0], "pkg/m.py:1: breaking module pkg.m: removed")
        self.assertEqual(len(lines), 2)

    def test_moved_between_modules(self) -> None:
        code, lines = self.diff(
            {"a.py": "def f(): pass\n", "b.py": ""},
            {"a.py": "from b import f\n", "b.py": "def f(): pass\n"},
        )
        self.assertEqual(
            lines,
            ["a.py:1: breaking function a.f: removed", "b.py:1: compatible function b.f: added", "changes: 2 (breaking: 1, compatible: 1)"],
        )

    def test_not_compared(self) -> None:
        old = (
            "'''doc'''\nclass A(B):\n    x = 1\n    @property\n    def p(self) -> int: return 1\n    @p.setter\n    def p(self, v): pass\n"
            "@deco\ndef f(a: int = 1, *, b: str = 'x') -> int:\n    return 1\n"
        )
        new = (
            "class A(C, metaclass=M):\n    __slots__ = ()\n    x = 2\n    @property\n    def p(self) -> str: return 'a'\n"
            "@other\n@deco2(1)\ndef f(a: str = 2, *, b: bytes = None) -> None:\n    raise E\n"
            "CONST = 5\n"
        )
        self.assertEqual(self.one(old, new), [])


class Params(Base):
    def p(self, old: str, new: str, prefix: str = "def f") -> list[str]:
        lines = self.one(f"{prefix}({old}): pass\n", f"{prefix}({new}): pass\n")
        return [l.split(": ", 1)[1] for l in lines]

    def test_removed_renamed(self) -> None:
        self.assertEqual(self.p("a, b", "a"), ["breaking function m.f: parameter removed: b"])
        self.assertEqual(
            self.p("a, b", "a, c"),
            ["breaking function m.f: parameter removed: b", "breaking function m.f: parameter added without a default: c"],
        )
        self.assertEqual(
            self.p("a, /", "b, /"),
            ["breaking function m.f: parameter removed: a", "breaking function m.f: parameter added without a default: b"],
        )

    def test_moved(self) -> None:
        self.assertEqual(
            self.p("a, b", "b, a"),
            ["breaking function m.f: parameter moved: a from position 1 to 2", "breaking function m.f: parameter moved: b from position 2 to 1"],
        )
        self.assertEqual(
            self.p("a, b", "x, a, b"),
            [
                "breaking function m.f: parameter moved: a from position 1 to 2",
                "breaking function m.f: parameter moved: b from position 2 to 3",
                "breaking function m.f: parameter added without a default: x",
            ],
        )
        self.assertEqual(
            self.p("a, b", "a, b, x=1"), ["compatible function m.f: parameter added with a default: x"]
        )
        # keyword-only parameters never move
        self.assertEqual(self.p("*, a, b", "*, b, a"), [])

    def test_kind_changed(self) -> None:
        self.assertEqual(self.p("a, b", "a, *, b"), ["breaking function m.f: parameter kind changed: b from positional-or-keyword to keyword-only"])
        self.assertEqual(self.p("a, /, b", "a, b"), ["breaking function m.f: parameter kind changed: a from positional-only to positional-or-keyword"])
        self.assertEqual(self.p("*args", "args"), ["breaking function m.f: parameter kind changed: *args from var-positional to positional-or-keyword"])
        self.assertEqual(self.p("**kw", "*, kw"), ["breaking function m.f: parameter kind changed: **kw from var-keyword to keyword-only"])
        # kind changed hides default and position findings for that parameter
        out = self.p("a, b=1", "b, *, a")
        self.assertEqual(out[0], "breaking function m.f: parameter kind changed: a from positional-or-keyword to keyword-only")
        self.assertNotIn("lost its default: a", " ".join(out))
        self.assertNotIn("moved: a", " ".join(out))

    def test_defaults(self) -> None:
        self.assertEqual(self.p("a=1", "a"), ["breaking function m.f: parameter lost its default: a"])
        self.assertEqual(self.p("a", "a=1"), ["compatible function m.f: parameter gained a default: a"])
        self.assertEqual(self.p("*, a=1", "*, a"), ["breaking function m.f: parameter lost its default: a"])
        self.assertEqual(self.p("*, a", "*, a=None"), ["compatible function m.f: parameter gained a default: a"])
        self.assertEqual(self.p("a=1", "a=2"), [])
        self.assertEqual(self.p("a, b=1", "a=0, b=1"), ["compatible function m.f: parameter gained a default: a"])

    def test_moved_and_lost_default_order(self) -> None:
        self.assertEqual(
            self.p("a, b=1", "b, a"),
            [
                "breaking function m.f: parameter moved: a from position 1 to 2",
                "breaking function m.f: parameter moved: b from position 2 to 1",
                "breaking function m.f: parameter lost its default: b",
            ],
        )

    def test_added(self) -> None:
        self.assertEqual(self.p("a", "a, b"), ["breaking function m.f: parameter added without a default: b"])
        self.assertEqual(self.p("a", "a, *, b"), ["breaking function m.f: parameter added without a default: b"])
        self.assertEqual(self.p("a", "a, *, b=1"), ["compatible function m.f: parameter added with a default: b"])
        self.assertEqual(self.p("a", "a, *args"), ["compatible function m.f: parameter added: *args"])
        self.assertEqual(self.p("a", "a, **kwargs"), ["compatible function m.f: parameter added: **kwargs"])
        self.assertEqual(
            self.p("a, *args, **kwargs", "a"),
            ["breaking function m.f: parameter removed: *args", "breaking function m.f: parameter removed: **kwargs"],
        )
        out = self.p("a", "a, *args, k=1, **kw")
        self.assertEqual(
            out,
            [
                "compatible function m.f: parameter added: *args",
                "compatible function m.f: parameter added with a default: k",
                "compatible function m.f: parameter added: **kw",
            ],
        )

    def test_self_and_cls_left_out(self) -> None:
        old = "class C:\n    def m(self, a): pass\n    @classmethod\n    def c(cls, a): pass\n    @staticmethod\n    def s(self, a): pass\n"
        new = "class C:\n    def m(me, a): pass\n    @classmethod\n    def c(klass, a): pass\n    @staticmethod\n    def s(me, a): pass\n"
        lines = self.one(old, new)
        self.assertEqual(
            [l.split(": ", 1)[1] for l in lines],
            [
                "breaking staticmethod m.C.s: parameter removed: self",
                "breaking staticmethod m.C.s: parameter added without a default: me",
            ],
        )

    def test_method_without_positional(self) -> None:
        old = "class C:\n    def m(*args): pass\n    def n(self, /, a): pass\n    def o(self=1, a=2): pass\n"
        new = "class C:\n    def m(*args, x): pass\n    def n(self, /): pass\n    def o(me=1, a=2): pass\n"
        self.assertEqual(
            [l.split(": ", 1)[1] for l in self.one(old, new)],
            [
                "breaking method m.C.m: parameter added without a default: x",
                "breaking method m.C.n: parameter removed: a",
            ],
        )

    def test_function_keeps_self(self) -> None:
        self.assertEqual(self.p("self, a", "a"), ["breaking function m.f: parameter removed: self", "breaking function m.f: parameter moved: a from position 2 to 1"])

    def test_async(self) -> None:
        lines = self.one("def f(a): pass\nasync def g(a): pass\n", "async def f(): pass\ndef g(a, b): pass\n")
        self.assertEqual(
            [l.split(": ", 1)[1] for l in lines],
            [
                "breaking function m.f: became async",
                "breaking function m.f: parameter removed: a",
                "breaking function m.g: no longer async",
                "breaking function m.g: parameter added without a default: b",
            ],
        )

    def test_async_json_codes(self) -> None:
        write(self.old, {"m.py": "def f(a=1): pass\n"})
        write(self.new, {"m.py": "async def f(a, *args, k=1): pass\n"})
        code, out, err = self.run_tool("--json", str(self.old), str(self.new))
        got = [(c["change"], c["parameter"], c["breaking"]) for c in json.loads(out)["changes"]]
        self.assertEqual(
            got,
            [
                ("async-changed", None, True),
                ("parameter-lost-default", "a", True),
                ("parameter-added", "*args", False),
                ("parameter-added", "k", False),
            ],
        )

    def test_json_all_codes(self) -> None:
        write(self.old, {"m.py": "def f(a, b, c=1, d=None, e=0): pass\n"})
        write(self.new, {"m.py": "def f(b, a, c, d=2, *, e=0, g): pass\n"})
        code, out, err = self.run_tool("--json", str(self.old), str(self.new))
        got = [(c["change"], c["parameter"]) for c in json.loads(out)["changes"]]
        self.assertEqual(
            got,
            [
                ("parameter-moved", "a"),
                ("parameter-moved", "b"),
                ("parameter-lost-default", "c"),
                ("parameter-kind-changed", "e"),
                ("parameter-added", "g"),
            ],
        )
        self.assertEqual(code, 1)


class Ordering(Base):
    def test_order_by_module_then_name(self) -> None:
        files = {
            "b.py": "def z(): pass\nclass A:\n    def m(self): pass\ndef a(): pass\n",
            "a/__init__.py": "def q(): pass\n",
            "a_b.py": "",
            "a/c.py": "",
        }
        code, lines = self.diff({}, files)
        self.assertEqual(
            [l.split()[-2].rstrip(":") for l in lines[:-1]], ["a", "a.c", "a_b", "b"]
        )
        empty = {"b.py": "", "a/__init__.py": "", "a_b.py": "", "a/c.py": ""}
        code, lines = self.diff(empty, files)
        self.assertEqual(
            [l.split()[-2].rstrip(":") for l in lines[:-1]], ["a.q", "b.A", "b.a", "b.z"]
        )

    def test_class_before_members_and_names_by_string(self) -> None:
        old = "class A:\n    pass\nclass A2: pass\n"
        new = "def f(): pass\nclass A2: pass\nclass A:\n    class B:\n        def m(self): pass\n    def x(self): pass\n    def _y(self): pass\n    class _Z: pass\n"
        lines = self.one(old, new)
        self.assertEqual(
            [l.split()[-2].rstrip(":") for l in lines],
            ["m.A.B", "m.A.x", "m.f"],
        )

    def test_changes_of_class_and_members_sorted(self) -> None:
        old = "class A:\n    def a(self, x): pass\n    def b(self): pass\nclass A2:\n    def c(self): pass\n"
        new = "class A:\n    def a(self): pass\n    def c(self): pass\nclass A2:\n    def c(self, y): pass\n"
        lines = self.one(old, new)
        self.assertEqual(
            [l.split(": ", 1)[1] for l in lines],
            [
                "breaking method m.A.a: parameter removed: x",
                "breaking method m.A.b: removed",
                "compatible method m.A.c: added",
                "breaking method m.A2.c: parameter added without a default: y",
            ],
        )


class ParseErrors(Base):
    def test_syntax_error_reported(self) -> None:
        write(self.old, {"a.py": "x = 1\n", "_p.py": "def f(:\n"})
        write(self.new, {"pkg/b.py": "x = (1,\n\ny = 2 ++\n", "a.py": "def ok(): pass\n"})
        code, out, err = self.run_tool(str(self.old), str(self.new))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        lines = err.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("old/_p.py:1:"), lines)
        self.assertRegex(lines[0], r"^old/_p\.py:1:\d+: .+")
        self.assertRegex(lines[1], r"^new/pkg/b\.py:\d+:\d+: .+")

    def test_line_and_column(self) -> None:
        write(self.old, {"a.py": "x = 1\ny = = 2\n"})
        write(self.new, {})
        code, out, err = self.run_tool(str(self.old), str(self.new))
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("old/a.py:2:5: "), err)

    def test_order_old_before_new_sorted_by_path(self) -> None:
        bad = "def (\n"
        write(self.old, {"z.py": bad, "a/m.py": bad, "B.py": bad})
        write(self.new, {"y.py": bad, "c.py": bad})
        code, out, err = self.run_tool(str(self.old), str(self.new))
        self.assertEqual(
            [l.split(":")[0] for l in err.splitlines()],
            ["old/B.py", "old/a/m.py", "old/z.py", "new/c.py", "new/y.py"],
        )

    def test_src_path_in_report(self) -> None:
        write(self.old, {"src/a.py": "def (\n", "x.py": ""})
        write(self.new, {"src/a.py": ""})
        code, out, err = self.run_tool("--src", "./src", str(self.old), str(self.new))
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("old/src/a.py:1:"), err)

    def test_skipped_and_excluded_not_parsed(self) -> None:
        write(self.old, {"venv/a.py": "def (", "t/a.py": "def (", "ok.py": ""})
        write(self.new, {"ok.py": ""})
        code, out, err = self.run_tool("--exclude", "t", str(self.old), str(self.new))
        self.assertEqual((code, err), (0, ""))

    def test_encodings_bom_crlf(self) -> None:
        code, lines = self.diff(
            {"a.py": b"# -*- coding: latin-1 -*-\r\ndef f(a):\r\n    return '\xe9'\r\n", "b.py": b"\xef\xbb\xbfdef g(): pass\r\n"},
            {"a.py": b"# -*- coding: latin-1 -*-\r\ndef f(a, b):\r\n    return '\xe9'\r\n", "b.py": b"\xef\xbb\xbfdef g(): pass\r\n"},
        )
        self.assertEqual(code, 1)
        self.assertEqual(
            lines,
            ["a.py:2: breaking function a.f: parameter added without a default: b", "changes: 1 (breaking: 1, compatible: 0)"],
        )

    def test_utf8_names_output(self) -> None:
        write(self.old, {"pkg/mod.py": ""})
        write(self.new, {"pkg/mod.py": "def caf\u00e9(): pass\n"})
        code, out, err = self.run_tool(str(self.old), str(self.new))
        self.assertIn("pkg.mod.caf\u00e9: added", out)

    def test_undecodable_file(self) -> None:
        write(self.old, {})
        write(self.new, {"a.py": b"x = '\xff\xfe'\n", "b.py": b"x = 1\x00\n"})
        code, out, err = self.run_tool(str(self.old), str(self.new))
        self.assertEqual((code, out), (2, ""))
        lines = err.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[0], r"^new/a\.py:\d+:\d+: ")
        self.assertRegex(lines[1], r"^new/b\.py:\d+:\d+: ")

    def test_nothing_printed_when_parse_error_even_with_breaking(self) -> None:
        write(self.old, {"a.py": "def f(): pass\n"})
        write(self.new, {"a.py": "", "b.py": "def ("})
        code, out, err = self.run_tool("--json", str(self.old), str(self.new))
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("new/b.py:1:"))

    def test_no_warnings_on_stderr(self) -> None:
        code, lines = self.diff({"a.py": "x = '\\d'\nif x is 1: pass\n"}, {"a.py": "x = '\\d'\nif x is 1: pass\n"})
        self.assertEqual(code, 0)


class Usage(Base):
    def test_usage_errors(self) -> None:
        o, n = str(self.old), str(self.new)
        (self.old / "src").mkdir()
        self.assertUsage()
        self.assertUsage(o)
        self.assertUsage(o, n, o)
        self.assertUsage("--bogus", o, n)
        self.assertUsage("-x", o, n)
        self.assertUsage("-", o, n)
        self.assertUsage("--help")
        self.assertUsage("--src=src", o, n)
        self.assertUsage(o, n, "--src")
        self.assertUsage(o, n, "--exclude")
        self.assertUsage(o, n, "--json", "--exclude")
        self.assertUsage(o, str(self.root / "missing"))
        self.assertUsage(o, str(self.root / "missing" / ".." / "old"))
        self.assertUsage("--json", o, n, "--json-x")

    def test_file_is_not_a_folder(self) -> None:
        write(self.root, {"f.txt": "x"})
        self.assertUsage(str(self.old), str(self.root / "f.txt"))

    def test_src_errors(self) -> None:
        o, n = str(self.old), str(self.new)
        (self.old / "src").mkdir()
        (self.new / "src").mkdir()
        (self.old / "only").mkdir()
        write(self.new, {"file": "x"})
        self.assertUsage("--src", "src", "--src", "src", o, n)
        self.assertUsage("--src", "", o, n)
        self.assertUsage("--src", ".", o, n)
        self.assertUsage("--src", "././/", o, n)
        self.assertUsage("--src", "/src", o, n)
        self.assertUsage("--src", str(self.old / "src"), o, n)
        self.assertUsage("--src", "..", o, n)
        self.assertUsage("--src", "src/../src", o, n)
        self.assertUsage("--src", "nope", o, n)
        self.assertUsage("--src", "only", o, n)
        self.assertUsage("--src", "file", o, n)

    def test_exclude_errors(self) -> None:
        o, n = str(self.old), str(self.new)
        self.assertUsage("--exclude", "", o, n)
        self.assertUsage("--exclude", ".", o, n)
        self.assertUsage("--exclude", "./", o, n)
        self.assertUsage("--exclude", "//", o, n)
        self.assertUsage("--exclude", "ok", "--exclude", "./.", o, n)

    def test_option_value_is_next_argument(self) -> None:
        (self.old / "--json").mkdir()
        (self.new / "--json").mkdir()
        write(self.old, {"--json/m.py": "def f(): pass\n"})
        write(self.new, {"--json/m.py": "def f(a): pass\n"})
        code, out, err = self.run_tool("--src", "--json", str(self.old), str(self.new))
        self.assertEqual((code, err), (1, ""))
        self.assertTrue(out.startswith("--json/m.py:1: breaking function m.f"), out)

    def test_usage_error_before_reading(self) -> None:
        write(self.old, {"a.py": "def ("})
        self.assertUsage("--bogus", str(self.old), str(self.new))

    def test_relative_folders_and_cwd(self) -> None:
        write(self.old, {"m.py": "def f(): pass\n"})
        write(self.new, {"m.py": "def f(a): pass\n"})
        code, out, err = self.run_tool("old", "new", cwd=self.root)
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines()[0], "m.py:1: breaking function m.f: parameter added without a default: a")

    def test_main_argv_in_process(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("py_api_diff_under_test", TOOL)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(mod.main(["--bogus"]), 2)
        self.assertIn("unknown option", err.getvalue())


if __name__ == "__main__":
    unittest.main()
