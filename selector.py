
"""Coverage-based test selector for Blast Radius."""

import json
import os
import re
import sys
from pathlib import Path
from collections import defaultdict

DATA = Path(os.environ.get("DATA", "/data"))
COVERAGE_PATH = Path(
    os.environ.get("COVERAGE_MAP", "/app/coverage_map.json")
)

EXPECTED_SQLGLOT_COMMIT = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"

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

SOURCE_FILE_EXTRA_METHODS = {
    "sqlglot/optimizer/eliminate_joins.py": [
        "test_execute_tpcds",
        "test_eliminate_joins",
        "test_optimize",
        "test_tpcds",
        "test_tpch",
    ],
    "sqlglot/dialects/tsql.py": [
        "test_simplify",
    ],
    "sqlglot/parsers/bigquery.py": [
        "test_optimize",
        "test_qualify_tables",
        "test_quote_identifiers",
    ],
    "sqlglot/typing/snowflake.py": [
        "test_optimize",
    ],
}


def normalize_path(path):
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def is_comment_or_blank(line):
    content = line[1:] if line.startswith(("+", "-")) else line
    stripped = content.strip()
    return (
        not stripped
        or stripped.startswith("#")
        or stripped.startswith("--")
    )


def parse_patch(patch_path):
    """Return changed files, comment-only status, and actual changed lines."""
    text = patch_path.read_text(encoding="utf-8", errors="replace")

    changed = set()
    changed_lines = defaultdict(set)
    code_changed_files = set()
    only_comments = True

    current_file = None
    old_line = None
    new_line = None

    for line in text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            current_file = None

            if len(parts) >= 4:
                path = normalize_path(parts[3])
                if path.startswith("b/"):
                    path = path[2:]

                if path != "/dev/null":
                    current_file = path
                    changed.add(path)

            old_line = None
            new_line = None
            continue

        if line.startswith("+++ "):
            path = line[4:].strip()

            if path.startswith("b/"):
                path = path[2:]
            if path != "/dev/null":
                current_file = normalize_path(path)
                changed.add(current_file)

            continue

        if line.startswith("--- "):
            continue

        if line.startswith("@@"):
            match = re.match(
                r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",
                line,
            )

            if match:
                old_line = int(match.group(1))
                new_line = int(match.group(3))
            continue

        if line.startswith(("+++", "---")):
            continue

        if line.startswith(("+", "-")):
            if not is_comment_or_blank(line):
                only_comments = False

                if current_file is not None:
                    code_changed_files.add(current_file)

        if current_file is None:
            continue

        if old_line is None or new_line is None:
            continue

        if line.startswith(" "):
            old_line += 1
            new_line += 1

        elif line.startswith("-"):
            changed_lines[current_file].add(old_line)
            old_line += 1

        elif line.startswith("+"):
            changed_lines[current_file].add(new_line)
            new_line += 1

    return changed, only_comments, changed_lines, code_changed_files


def build_maps(tests):
    by_file = defaultdict(list)
    method_to_num = {}

    for test in tests:
        by_file[test["file"]].append(test)

        match = re.search(r"::(\w+)$", test["id"])

        if match:
            method_to_num[match.group(1)] = test["number"]

    return by_file, method_to_num


def load_coverage(all_tests):
    """Load coverage data and identify tests missing source coverage."""

    if not COVERAGE_PATH.exists():
        print(
            "Coverage map missing; using legacy selection",
            file=sys.stderr,
        )
        return {}, set()

    try:
        data = json.loads(COVERAGE_PATH.read_text())

        if data.get("sqlglot_commit") != EXPECTED_SQLGLOT_COMMIT:
            raise ValueError("SQLGlot commit mismatch")

        files = data["files"]
        mapped_tests = set()

        for line_map in files.values():
            for test_numbers in line_map.values():
                mapped_tests.update(int(n) for n in test_numbers)

        all_numbers = {t["number"] for t in all_tests}
        uncovered_tests = all_numbers - mapped_tests

        print(
            f"Coverage loaded: {len(files)} files, "
            f"{len(mapped_tests)} tests, "
            f"{len(uncovered_tests)} uncovered tests",
            file=sys.stderr,
        )

        return files, uncovered_tests

    except Exception as exc:
        print(
            f"Invalid coverage map ({exc}); using legacy selection",
            file=sys.stderr,
        )
        return {}, set()


def select_legacy(changed, by_file, method_to_num, all_tests):
    """Original selector rules, retained as a safety fallback."""

    selected = set()
    core_hit = False

    for file_path in changed:
        if file_path.startswith("tests/fixtures/"):
            rel = file_path[len("tests/fixtures/"):]
            stem = (
                Path(rel).stem
                if "/" not in rel
                else rel.replace(".sql", "")
            )

            for key in [
                stem,
                Path(file_path).stem,
                rel.replace(".sql", ""),
            ]:
                if key in FIXTURE_TO_METHOD:
                    method = FIXTURE_TO_METHOD[key]
                    if method in method_to_num:
                        selected.add(method_to_num[method])

            base = Path(file_path).stem

            for prefix in ["test_", ""]:
                key = prefix + base

                if key in method_to_num:
                    selected.add(method_to_num[key])

        elif file_path.startswith("tests/"):
            if file_path in by_file:
                for test in by_file[file_path]:
                    selected.add(test["number"])

        elif file_path.startswith("sqlglot/"):
            parts = Path(file_path).parts

            important_suites = [
                "tests/test_optimizer.py",
                "tests/test_expressions.py",
                "tests/test_parser.py",
                "tests/test_generator.py",
                "tests/test_serde.py",
                "tests/test_transpile.py",
            ]

            for test_file in important_suites:
                for test in by_file.get(test_file, []):
                    selected.add(test["number"])

            if "dialects" in parts:
                dialect = Path(file_path).stem
                test_file = f"tests/dialects/test_{dialect}.py"

                for test in by_file.get(test_file, []):
                    selected.add(test["number"])

                for test in by_file.get(
                    "tests/dialects/test_dialect.py", []
                ):
                    selected.add(test["number"])

            elif "generators" in parts:
                generator = Path(file_path).stem
                test_file = f"tests/dialects/test_{generator}.py"

                for test in by_file.get(test_file, []):
                    selected.add(test["number"])

            elif "parsers" in parts:
                parser = Path(file_path).stem
                test_file = f"tests/dialects/test_{parser}.py"

                for test in by_file.get(test_file, []):
                    selected.add(test["number"])

            elif "optimizer" in parts or "executor" in parts:
                for test in by_file.get("tests/test_executor.py", []):
                    selected.add(test["number"])

            else:
                core_hit = True

    if core_hit:
        selected.update(t["number"] for t in all_tests)

    return selected


def select_source_safety_tests(
    file_path,
    line_map,
    method_to_num,
):
    """Select additional tests for known indirect source dependencies."""

    selected = set()

    if file_path == "sqlglot/serde.py" and line_map:
        for test_numbers in line_map.values():
            selected.update(int(n) for n in test_numbers)

    for method in SOURCE_FILE_EXTRA_METHODS.get(file_path, []):
        test_number = method_to_num.get(method)

        if test_number is not None:
            selected.add(test_number)

    return selected


def select_by_coverage(
    code_changed_files,
    changed_lines,
    coverage_files,
    by_file,
    method_to_num,
    all_tests,
):
    selected = set()

    for file_path in code_changed_files:
        if not file_path.startswith("sqlglot/"):
            continue

        line_map = coverage_files.get(file_path)

        if line_map is None:
            selected.update(
                select_legacy(
                    {file_path},
                    by_file,
                    method_to_num,
                    all_tests,
                )
            )
            continue

        candidate_lines = changed_lines.get(file_path, set())

        # First check whether any changed line has direct coverage.
        direct_hits = set()

        for line_number in candidate_lines:
            for test_number in line_map.get(str(line_number), []):
                direct_hits.add(int(test_number))

        selected.update(direct_hits)

        # If changed lines have no direct coverage, use all tests
        # mapped to this source file as a safety fallback.
        if candidate_lines and not direct_hits:
            for test_numbers in line_map.values():
                selected.update(int(n) for n in test_numbers)

        else:
            # Keep a one-line safety margin around changed lines.
            for line_number in candidate_lines:
                for nearby in (
                    line_number - 1,
                    line_number + 1,
                ):
                    if nearby > 0:
                        for test_number in line_map.get(str(nearby), []):
                            selected.add(int(test_number))

        selected.update(
            select_source_safety_tests(
                file_path,
                line_map,
                method_to_num,
            )
        )

    return selected


def split(tests_selected, worker_count=1):
    if not tests_selected:
        return []

    workers = [[] for _ in range(worker_count)]
    loads = [0.0] * worker_count

    for test in sorted(
        tests_selected,
        key=lambda t: t["seconds"],
        reverse=True,
    ):
        lightest = loads.index(min(loads))
        workers[lightest].append(test["number"])
        loads[lightest] += test["seconds"]

    return [worker for worker in workers if worker]


def main():
    data = json.loads((DATA / "tests.json").read_text())
    tests = data["tests"]

    by_file, method_to_num = build_maps(tests)
    test_by_num = {t["number"]: t for t in tests}
    coverage_files, uncovered_tests = load_coverage(tests)

    for patch in sorted((DATA / "commits").glob("*.patch")):
        changed, only_comments, changed_lines, code_changed_files = (
            parse_patch(patch)
        )

        if not changed:
            print(json.dumps({
                "commit": patch.stem,
                "workers": [],
            }))
            continue

        # For comment-only commits, use the changed files too.
        # This avoids automatically skipping all tests.
        selection_files = (
            changed if only_comments else code_changed_files
        )

        if only_comments:
            print(
                f"{patch.stem}: comment-only change; "
                "using file-based safety selection",
                file=sys.stderr,
            )

        non_source = {
            file_path
            for file_path in selection_files
            if not file_path.startswith("sqlglot/")
        }

        selected = select_legacy(
            non_source,
            by_file,
            method_to_num,
            tests,
        )

        selected.update(
            select_by_coverage(
                selection_files,
                changed_lines,
                coverage_files,
                by_file,
                method_to_num,
                tests,
            )
        )

        # Protect tests without source coverage.
        selected.update(uncovered_tests)

        selected = {
            number
            for number in selected
            if number in test_by_num
        }

        selected_tests = [
            test_by_num[number]
            for number in selected
        ]

        worker_count = (
            1 if len(selected_tests) < 30
            else min(3, max(1, (len(selected_tests) + 9) // 20))
        )

        workers = split(selected_tests, worker_count)

        print(json.dumps({
            "commit": patch.stem,
            "workers": workers,
        }))

        print(
            f"{patch.stem}: {len(changed)} files -> "
            f"{len(selected_tests)} tests",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
