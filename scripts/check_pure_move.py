#!/usr/bin/env python3
"""Check that a refactor only moved top-level definitions between runtime modules.

Every top-level function, class and simple constant of ``runtime/*.py`` is
fingerprinted by the hash of its AST dump (no line numbers). The baseline is a
committed JSON file taken from a git ref before the refactor. The candidate is
the working tree. A pure move leaves the multiset of ``(name, hash)`` pairs
unchanged: nothing missing, nothing added, nothing edited. Only imports and
module-level wiring may differ. Import statements inside a function body are
ignored in the fingerprint (a moved function must import from the module that
now owns the name); the retargeted ones are listed so a reviewer can check them.

  scripts/check_pure_move.py                       compare the tree with the baseline
  scripts/check_pure_move.py --write-baseline REF  regenerate the baseline from a git ref
  scripts/check_pure_move.py --base REF            compare against a git ref directly

Exit status 0 means the move is pure. The summary shows how many definitions
were compared, how many changed module, and how many mismatched.
"""
from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"
BASELINE = Path(__file__).resolve().parent / "pure_move_baseline.json"
# Module-level wiring that a split must edit: the frozen helper snapshot list, and the
# ``__file__`` override that keeps printed command hints naming the CLI entry module.
WIRING = frozenset({"HELPER_FILES", "__file__"})


def names_of(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.Assign):
        return [target.id for target in node.targets if isinstance(target, ast.Name)]
    return []


class _StripImports(ast.NodeTransformer):
    def visit_Import(self, node):
        return ast.Pass()

    def visit_ImportFrom(self, node):
        return ast.Pass()


def local_imports(node) -> list:
    found = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.ImportFrom):
            found += [f"{sub.module}.{alias.name}" for alias in sub.names]
        elif isinstance(sub, ast.Import):
            found += [alias.name for alias in sub.names]
    return sorted(found)


def fingerprints(sources: dict) -> list:
    """``[(name, hash, module, local_imports)]`` for every top-level definition."""
    result = []
    for module, text in sorted(sources.items()):
        for node in ast.parse(text).body:
            for name in names_of(node):
                imports = local_imports(node)
                stripped = _StripImports().visit(ast.parse(ast.unparse(node)).body[0]) if imports else node
                digest = hashlib.sha256(ast.dump(stripped).encode("utf-8")).hexdigest()
                result.append((name, digest, module, imports))
    return result


def git_sources(ref: str) -> dict:
    listing = subprocess.check_output(["git", "-C", str(ROOT), "ls-tree", "--name-only", ref,
                                       "runtime/"], text=True).split()
    sources = {}
    for entry in listing:
        if entry.endswith(".py"):
            text = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{ref}:{entry}"],
                                           text=True)
            sources[Path(entry).stem] = text
    return sources


def tree_sources() -> dict:
    return {path.stem: path.read_text(encoding="utf-8") for path in sorted(RUNTIME.glob("*.py"))}


def compare(base: list, candidate: list) -> dict:
    base_pairs = collections.Counter((name, digest) for name, digest, _, _ in base)
    cand_pairs = collections.Counter((name, digest) for name, digest, _, _ in candidate)
    missing = collections.Counter({key: n for key, n in (base_pairs - cand_pairs).items()
                                   if key[0] not in WIRING})
    added = collections.Counter({key: n for key, n in (cand_pairs - base_pairs).items()
                                 if key[0] not in WIRING})
    wiring = sorted({name for name, _ in list((base_pairs - cand_pairs)) + list((cand_pairs - base_pairs))
                     if name in WIRING})
    changed = sorted({name for name, _ in missing} & {name for name, _ in added})
    base_modules = collections.defaultdict(set)
    cand_modules = collections.defaultdict(set)
    base_imports, cand_imports = {}, {}
    for name, digest, module, imports in base:
        base_modules[(name, digest)].add(module)
        base_imports[(name, digest)] = imports
    for name, digest, module, imports in candidate:
        cand_modules[(name, digest)].add(module)
        cand_imports[(name, digest)] = imports
    retargeted = sorted(f"{key[0]}: {base_imports[key]} -> {cand_imports[key]}"
                        for key in base_pairs & cand_pairs
                        if base_imports.get(key) != cand_imports.get(key))
    moved = sorted(f"{name}: {'/'.join(sorted(base_modules[key]))} -> {'/'.join(sorted(cand_modules[key]))}"
                   for key in base_pairs & cand_pairs
                   for name in [key[0]] if base_modules[key] != cand_modules[key])
    return {"base": sum(base_pairs.values()), "candidate": sum(cand_pairs.values()),
            "moved": moved, "retargeted": retargeted, "changed": changed, "wiring": wiring,
            "missing": sorted(name for name, _ in missing.elements()),
            "added": sorted(name for name, _ in added.elements())}


def load_baseline() -> list:
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    return [tuple(row) for row in data["definitions"]]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write-baseline", metavar="REF")
    parser.add_argument("--base", metavar="REF", help="compare with a git ref instead of the baseline file")
    parser.add_argument("--verbose", action="store_true", help="list every moved definition")
    args = parser.parse_args(argv)
    if args.write_baseline:
        rows = fingerprints(git_sources(args.write_baseline))
        sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", args.write_baseline],
                                      text=True).strip()
        BASELINE.write_text(json.dumps({"ref": sha, "definitions": rows}, indent=0,
                                       sort_keys=True) + "\n", encoding="utf-8")
        print(f"baseline written: {len(rows)} definitions from {sha}")
        return 0
    base = fingerprints(git_sources(args.base)) if args.base else load_baseline()
    report = compare(base, fingerprints(tree_sources()))
    print(f"definitions in base: {report['base']}; in candidate: {report['candidate']}")
    print(f"moved to another module: {len(report['moved'])}")
    print(f"function-local imports retargeted: {len(report['retargeted'])}")
    print(f"module-level wiring edited (allowed): {', '.join(report['wiring']) or 'none'}")
    print(f"changed (AST differs): {len(report['changed'])}; missing: {len(report['missing'])}; "
          f"added: {len(report['added'])}")
    for label in ("changed", "missing", "added"):
        for name in report[label]:
            print(f"  {label}: {name}")
    if args.verbose:
        for line in report["moved"]:
            print("  moved:", line)
    for line in report["retargeted"]:
        print("  retargeted import:", line)
    pure = not (report["changed"] or report["missing"] or report["added"])
    print("PURE MOVE" if pure else "NOT A PURE MOVE")
    return 0 if pure else 1


if __name__ == "__main__":
    raise SystemExit(main())
