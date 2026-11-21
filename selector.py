"""Better selector for Blast Radius."""
import os
import json
import re
import sys
from pathlib import Path
from collections import defaultdict

DATA = Path(os.environ.get("DATA", "/data"))

# Fixture name → test method (derived from the pinned sqlglot source)
FIXTURE_TO_METHOD = {
    "isolate_table_selects": "test_isolate_table_selects",
    "qualify_tables": "test_qualify_tables",
    "normalize": "test_normalize",
    "qualify_columns__with_invisible": "test_qualify_columns__with_invisible",
    "pushdown_cte_alias_columns": "test_pushdown_cte_alias_columns",
    "qualify_columns__invalid": "test_qualify_columns__invalid",
    "normalize_identifiers": "test_normalize_identifiers",
    "quote_identifiers": "test_quote_identifiers",
    "pushdown_projections": "test_pushdown_projection",
    "simplify": "test_simplify",
    "unnest_subqueries": "test_unnest_subqueries",
    "pushdown_predicates": "test_pushdown_predicates",
    "optimize_joins": "test_optimize_joins",
    "eliminate_joins": "test_eliminate_joins",
    "eliminate_ctes": "test_eliminate_ctes",
    "eliminate_subqueries": "test_eliminate_subqueries",
    "canonicalize_internal_names": "test_canonicalize_internal_names",
    "canonicalize": "test_canonicalize",
    "tpc-h/tpc-h": "test_tpch",
    "tpc-ds/tpc-ds": "test_tpcds",
    "annotate_types": "test_annotate_types",
    "annotate_functions": "test_annotate_funcs",
    "merge_subqueries": "test_merge_subqueries",
    "qualify_columns_ddl": "test_qualify_columns",
    "qualify_columns": "test_qualify_columns",
}

def is_comment_or_blank(line: str) -> bool:
    content = line[1:] if line.startswith(("+", "-")) else line
    stripped = content.strip()
    return (not stripped) or stripped.startswith("#") or stripped.startswith("--")

def parse_patch(patch_path: Path):
    text = patch_path.read_text(encoding="utf-8", errors="replace")
    changed = set()
    only_comments = True
    for line in text.splitlines():
        if line.startswith("diff --git"):
            parts = line.split()
            if len(parts) >= 4:
                p = parts[3]
                if p.startswith("b/"):
                    p = p[2:]
                changed.add(p)
        elif line.startswith("+++ "):
            p = line[4:].strip()
            if p.startswith("b/"):
                p = p[2:]
            if p != "/dev/null":
                changed.add(p)
        elif line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            if not is_comment_or_blank(line):
                only_comments = False
    return changed, only_comments

def build_maps(tests):
    by_file = defaultdict(list)
    method_to_num = {}
    for t in tests:
        by_file[t["file"]].append(t)
        m = re.search(r"::(\w+)$", t["id"])
        if m:
            method_to_num[m.group(1)] = t["number"]
    return by_file, method_to_num

def select_for_files(changed, by_file, method_to_num, all_tests):
    selected = set()
    core_hit = False
    for f in changed:
        if f.startswith("tests/fixtures/"):
            rel = f[len("tests/fixtures/"):]
            stem = Path(rel).stem if "/" not in rel else rel.replace(".sql", "")
            for key in [stem, Path(f).stem, rel.replace(".sql", "")]:
                if key in FIXTURE_TO_METHOD:
                    meth = FIXTURE_TO_METHOD[key]
                    if meth in method_to_num:
                        selected.add(method_to_num[meth])
            base = Path(f).stem
            for prefix in ["test_", ""]:
                key = prefix + base
                if key in method_to_num:
                    selected.add(method_to_num[key])
        elif f.startswith("tests/"):
            if f in by_file:
                for t in by_file[f]:
                    selected.add(t["number"])
        elif f.startswith("sqlglot/"):
            parts = Path(f).parts
            # Always include important suites
            for tf in [
                "tests/test_optimizer.py",
                "tests/test_expressions.py",
                "tests/test_parser.py",
                "tests/test_generator.py",
                "tests/test_serde.py",
                "tests/test_transpile.py",
            ]:
                if tf in by_file:
                    for t in by_file[tf]:
                        selected.add(t["number"])
            if "dialects" in parts:
                dialect = Path(f).stem
                testf = f"tests/dialects/test_{dialect}.py"
                if testf in by_file:
                    for t in by_file[testf]:
                        selected.add(t["number"])
                if "tests/dialects/test_dialect.py" in by_file:
                    for t in by_file["tests/dialects/test_dialect.py"]:
                        selected.add(t["number"])
            elif "generators" in parts:
                gen = Path(f).stem
                testf = f"tests/dialects/test_{gen}.py"
                if testf in by_file:
                    for t in by_file[testf]:
                        selected.add(t["number"])
            elif "parsers" in parts:
                parser = Path(f).stem
                testf = f"tests/dialects/test_{parser}.py"
                if testf in by_file:
                    for t in by_file[testf]:
                        selected.add(t["number"])
            elif "optimizer" in parts or "executor" in parts:
                if "tests/test_executor.py" in by_file:
                    for t in by_file["tests/test_executor.py"]:
                        selected.add(t["number"])
            else:
                core_hit = True
    if core_hit:
        for t in all_tests:
            selected.add(t["number"])
    return selected

def split(tests_selected, worker_count=1):
    if not tests_selected:
        return []
    workers = [[] for _ in range(worker_count)]
    loads = [0.0] * worker_count
    for test in sorted(tests_selected, key=lambda t: t["seconds"], reverse=True):
        lightest = loads.index(min(loads))
        workers[lightest].append(test["number"])
        loads[lightest] += test["seconds"]
    return [w for w in workers if w]

def main():
    data = json.loads((DATA / "tests.json").read_text())
    tests = data["tests"]
    by_file, method_to_num = build_maps(tests)
    test_by_num = {t["number"]: t for t in tests}

    for patch in sorted((DATA / "commits").glob("*.patch")):
        changed, only_comments = parse_patch(patch)
        if only_comments:
            print(json.dumps({"commit": patch.stem, "workers": []}))
            print(f"{patch.stem}: only comments", file=sys.stderr)
            continue
        nums = select_for_files(changed, by_file, method_to_num, tests)
        selected = [test_by_num[n] for n in nums if n in test_by_num]
        nworkers = 1 if len(selected) < 30 else min(3, max(1, (len(selected) + 9) // 20))
        workers = split(selected, nworkers)
        print(json.dumps({"commit": patch.stem, "workers": workers}))
        print(f"{patch.stem}: {len(changed)} files -> {len(selected)} tests", file=sys.stderr)

if __name__ == "__main__":
    main()