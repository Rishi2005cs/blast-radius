"""Coverage-based test selector for Blast Radius."""

import json
import os
import re
import sys
from pathlib import Path
from collections import defaultdict

DATA = Path(os.environ.get("DATA", "/data"))
COVERAGE_PATH = Path(os.environ.get("COVERAGE_MAP", "/app/coverage_map.json"))

EXPECTED_SQLGLOT_COMMIT = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
WORKER_COST_MILLISECONDS = 2000
MAXIMUM_WORKERS = 8

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

# Additional dependencies from the pinned source tree. These supplement
# line coverage for class-level declarations with no per-test execution context.
SOURCE_FILE_EXTRA_METHODS = {
    "sqlglot/optimizer/eliminate_joins.py": [
        "test_execute_tpcds",
        "test_eliminate_joins",
        "test_tpcds",
        "test_tpch",
    ],
    "sqlglot/dialects/tsql.py": ["test_simplify"],
    "sqlglot/parsers/bigquery.py": [
        "test_qualify_tables",
        "test_quote_identifiers",
    ],
}

SOURCE_FILE_EXTRA_TEST_FILES = {
    # Snowflake's NUMERIC_ESCAPES is a class-level tokenizer setting. Its
    # consumers are exercised by the Snowflake dialect suite.
    "sqlglot/dialects/snowflake.py": ["tests/dialects/test_snowflake.py"],
}

SOURCE_FILE_EXTRA_TEST_IDS = {
    # Source-specific dependencies that are not fully represented by line hits.
    "sqlglot/optimizer/eliminate_joins.py": [
        "tests/test_optimizer.py::TestOptimizer::test_optimize",
    ],
    "sqlglot/parsers/bigquery.py": [
        "tests/test_optimizer.py::TestOptimizer::test_optimize",
    ],
    "sqlglot/typing/snowflake.py": [
        "tests/test_optimizer.py::TestOptimizer::test_optimize",
    ],
    "sqlglot/expressions/ddl.py": [
        "tests/dialects/test_duckdb.py::TestDuckDB::test_create_trigger",
        "tests/dialects/test_postgres.py::TestPostgres::test_ddl",
        "tests/dialects/test_postgres.py::TestPostgres::test_postgres_create_trigger",
    ],
}

# Tests absent from line coverage are selected only when their real dependency
# is touched, rather than being added to every commit.
UNCOVERED_TEST_DEPENDENCIES = {
    214: ("dialect_loading",),
    1565: ("c_extension_build",),
    1566: ("c_extension_build",),
    1567: ("c_extension_build",),
    1619: ("typing_metadata",),
}

DIALECT_LOADING_PATHS = {
    "sqlglot/__init__.py",
    "sqlglot/dialects/__init__.py",
}
C_EXTENSION_BUILD_PATHS = {
    "sqlglotc/setup.py",
    "setup.py",
    "pyproject.toml",
    "setup.cfg",
    "MANIFEST.in",
}
TYPING_METADATA_PATHS = {
    "sqlglot/typing.py",
}


def normalize_path(path):
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def is_comment_or_blank(line):
    content = line[1:] if line.startswith(("+", "-")) else line
    stripped = content.strip()
    return not stripped or stripped.startswith("#") or stripped.startswith("--")


def parse_patch(patch_path):
    """Return changed files, comment-only status, and changed base-source lines."""
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

        if current_file is None or old_line is None or new_line is None:
            continue

        if line.startswith(" "):
            old_line += 1
            new_line += 1
        elif line.startswith("-"):
            changed_lines[current_file].add(old_line)
            old_line += 1
        elif line.startswith("+"):
            # Coverage is measured on the pinned/base tree. For an insertion,
            # use the old-line anchor rather than the shifted post-patch line.
            changed_lines[current_file].add(old_line)
            new_line += 1

    return changed, only_comments, changed_lines, code_changed_files


def build_maps(tests):
    by_file = defaultdict(list)
    method_to_nums = defaultdict(set)
    id_to_num = {}

    for test in tests:
        by_file[test["file"]].append(test)
        id_to_num[test["id"]] = test["number"]

        match = re.search(r"::(\w+)$", test["id"])
        if match:
            method_to_nums[match.group(1)].add(test["number"])

    return by_file, method_to_nums, id_to_num


def load_coverage(all_tests):
    """Load line coverage, source spans, and identify tests without line data."""
    if not COVERAGE_PATH.exists():
        print("Coverage map missing; using legacy selection", file=sys.stderr)
        return {}, {}, {}, set()

    try:
        data = json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))
        if data.get("sqlglot_commit") != EXPECTED_SQLGLOT_COMMIT:
            raise ValueError("SQLGlot commit mismatch")

        files = data["files"]
        source_spans = data.get("source_spans", {})
        source_metadata = data.get("source_metadata", {})
        mapped_tests = set()

        for line_map in files.values():
            for test_numbers in line_map.values():
                mapped_tests.update(int(n) for n in test_numbers)

        all_numbers = {t["number"] for t in all_tests}
        uncovered_tests = all_numbers - mapped_tests

        print(
            f"Coverage loaded: {len(files)} files, "
            f"{len(mapped_tests)} tests, "
            f"{len(uncovered_tests)} uncovered tests, "
            f"{sum(len(v) for v in source_spans.values())} source spans",
            file=sys.stderr,
        )
        return files, source_spans, source_metadata, uncovered_tests

    except Exception as exc:
        print(
            f"Invalid coverage map ({exc}); using legacy selection",
            file=sys.stderr,
        )
        return {}, {}, {}, set()


def select_legacy(changed, by_file, method_to_nums, all_tests):
    """Original selector rules, retained as a safety fallback."""
    selected = set()
    core_hit = False

    for file_path in changed:
        if file_path.startswith("tests/fixtures/"):
            rel = file_path[len("tests/fixtures/"):]
            stem = Path(rel).stem if "/" not in rel else rel.replace(".sql", "")

            for key in (stem, Path(file_path).stem, rel.replace(".sql", "")):
                method = FIXTURE_TO_METHOD.get(key)
                if method:
                    selected.update(method_to_nums.get(method, set()))

            base = Path(file_path).stem
            for prefix in ("test_", ""):
                selected.update(method_to_nums.get(prefix + base, set()))

        elif file_path.startswith("tests/"):
            selected.update(
                test["number"] for test in by_file.get(file_path, [])
            )

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
                selected.update(
                    test["number"] for test in by_file.get(test_file, [])
                )

            if "dialects" in parts:
                dialect = Path(file_path).stem
                test_file = f"tests/dialects/test_{dialect}.py"
                selected.update(
                    test["number"] for test in by_file.get(test_file, [])
                )
                selected.update(
                    test["number"]
                    for test in by_file.get("tests/dialects/test_dialect.py", [])
                )
            elif "generators" in parts:
                generator = Path(file_path).stem
                test_file = f"tests/dialects/test_{generator}.py"
                selected.update(
                    test["number"] for test in by_file.get(test_file, [])
                )
            elif "parsers" in parts:
                parser = Path(file_path).stem
                test_file = f"tests/dialects/test_{parser}.py"
                selected.update(
                    test["number"] for test in by_file.get(test_file, [])
                )
            elif "optimizer" in parts or "executor" in parts:
                selected.update(
                    test["number"]
                    for test in by_file.get("tests/test_executor.py", [])
                )
            else:
                core_hit = True

    if core_hit:
        selected.update(test["number"] for test in all_tests)

    return selected


def select_source_safety_tests(
    file_path,
    line_map,
    method_to_nums,
    id_to_num,
    by_file,
):
    """Select tests for static dependencies not reliably represented by line coverage."""
    selected = set()

    if file_path == "sqlglot/serde.py" and line_map:
        for test_numbers in line_map.values():
            selected.update(int(n) for n in test_numbers)

    for method in SOURCE_FILE_EXTRA_METHODS.get(file_path, []):
        selected.update(method_to_nums.get(method, set()))

    for test_id in SOURCE_FILE_EXTRA_TEST_IDS.get(file_path, []):
        number = id_to_num.get(test_id)
        if number is not None:
            selected.add(number)

    for test_file in SOURCE_FILE_EXTRA_TEST_FILES.get(file_path, []):
        selected.update(test["number"] for test in by_file.get(test_file, []))

    return selected


def select_span_tests(file_path, changed_line, line_map, source_spans):
    """Select tests that execute the smallest enclosing function or class."""
    spans = source_spans.get(file_path, [])
    if not spans:
        return set()

    containing = [
        span for span in spans
        if span["start"] <= changed_line <= span["end"]
    ]

    if not containing:
        # An inserted comment can sit immediately before a function definition.
        containing = [
            span for span in spans
            if 0 <= span["start"] - changed_line <= 2
        ]

    if not containing:
        return set()

    span = min(
        containing,
        key=lambda item: (
            item["end"] - item["start"],
            0 if item.get("kind") == "function" else 1,
        ),
    )

    selected = set()
    for line_number, test_numbers in line_map.items():
        line = int(line_number)
        if span["start"] <= line <= span["end"]:
            selected.update(int(n) for n in test_numbers)

    return selected


def comment_change_is_sensitive(file_path, changed_line, source_spans, source_metadata):
    """Comments can affect runtime when they displace docstrings or continuations."""
    if changed_line <= 2:
        return True

    for span in source_spans.get(file_path, []):
        docstring_start = span.get("docstring_start")
        docstring_end = span.get("docstring_end")
        if (
            docstring_start is not None
            and docstring_start - 1 <= changed_line <= docstring_end
        ):
            return True

    metadata = source_metadata.get(file_path, {})
    continuations = set(metadata.get("continuations", []))
    encoding_lines = set(metadata.get("encoding_lines", []))

    if changed_line - 1 in continuations or changed_line in continuations:
        return True
    if changed_line in encoding_lines or changed_line - 1 in encoding_lines:
        return True

    return False


def select_by_coverage(
    changed_files,
    code_changed_files,
    changed_lines,
    coverage_files,
    source_spans,
    source_metadata,
    by_file,
    method_to_nums,
    id_to_num,
    all_tests,
):
    selected = set()

    for file_path in changed_files:
        if not file_path.startswith("sqlglot/"):
            continue

        line_map = coverage_files.get(file_path)
        if line_map is None:
            selected.update(
                select_legacy(
                    {file_path},
                    by_file,
                    method_to_nums,
                    all_tests,
                )
            )
            continue

        candidate_lines = changed_lines.get(file_path, set())
        unresolved_lines = False
        comment_only_file = file_path not in code_changed_files
        file_spans = source_spans.get(file_path, [])
        sensitive_comment_found = False

        for line_number in candidate_lines:
            if comment_only_file:
                if not comment_change_is_sensitive(
                    file_path,
                    line_number,
                    source_spans,
                    source_metadata,
                ):
                    continue
                sensitive_comment_found = True

            direct_hits = {
                int(number)
                for number in line_map.get(str(line_number), [])
            }
            nearby_hits = set()

            for nearby in (line_number - 1, line_number + 1):
                if nearby > 0:
                    nearby_hits.update(
                        int(number)
                        for number in line_map.get(str(nearby), [])
                    )

            if direct_hits or nearby_hits:
                selected.update(direct_hits)
                selected.update(nearby_hits)
                continue

            span_hits = select_span_tests(
                file_path,
                line_number,
                line_map,
                source_spans,
            )
            if span_hits:
                selected.update(span_hits)
                continue

            if comment_only_file:
                # Plain module-level comments do not change Python behavior.
                # Keep a fallback for comments that can displace a module
                # docstring or sit inside a class/function with no line hits.
                structural_context = any(
                    span["kind"] in ("function", "class")
                    and span["start"] <= line_number <= span["end"]
                    for span in file_spans
                ) or any(
                    span["kind"] == "module_docstring"
                    and span["start"] - 1 <= line_number <= span["end"] + 1
                    for span in file_spans
                )
                if structural_context:
                    unresolved_lines = True
                continue

            unresolved_lines = True

        source_extras = (
            select_source_safety_tests(
                file_path,
                line_map,
                method_to_nums,
                id_to_num,
                by_file,
            )
            if not comment_only_file or sensitive_comment_found
            else set()
        )
        selected.update(source_extras)

        if candidate_lines and unresolved_lines and not source_extras:
            for test_numbers in line_map.values():
                selected.update(int(n) for n in test_numbers)

    return selected


def select_uncovered_safety_tests(
    changed_files,
    code_changed_files,
    changed_lines,
    source_spans,
    uncovered_tests,
):
    """Select no-line-coverage tests only when their real dependencies changed."""
    selected = set()

    dialect_loading_changed = bool(code_changed_files & DIALECT_LOADING_PATHS)

    # Dialect.py is imported by sqlglot, but method bodies are not executed by
    # the lazy-load check. Only module/class-body changes can affect its import.
    dialect_module_changed = "sqlglot/dialects/dialect.py" in code_changed_files
    if dialect_module_changed:
        fp = "sqlglot/dialects/dialect.py"
        file_spans = source_spans.get(fp, [])
        for line_number in changed_lines.get(fp, set()):
            inside_function = any(
                span["kind"] == "function"
                and span["start"] <= line_number <= span["end"]
                for span in file_spans
            )
            if not inside_function:
                dialect_loading_changed = True
                break

    c_extension_changed = (
        any(path.startswith("sqlglotc/") for path in code_changed_files)
        or bool(code_changed_files & C_EXTENSION_BUILD_PATHS)
    )
    typing_metadata_changed = (
        any(
            path.startswith("sqlglot/typing/")
            or path.startswith("sqlglot/expressions/")
            for path in code_changed_files
        )
        or bool(code_changed_files & TYPING_METADATA_PATHS)
    )

    dependency_flags = {
        "dialect_loading": dialect_loading_changed,
        "c_extension_build": c_extension_changed,
        "typing_metadata": typing_metadata_changed,
    }

    for test_number in uncovered_tests:
        dependencies = UNCOVERED_TEST_DEPENDENCIES.get(test_number, ())
        if any(dependency_flags.get(name, False) for name in dependencies):
            selected.add(test_number)

    return selected


def split(tests_selected):
    """Choose the worker count with the lowest exact simulated cost."""
    if not tests_selected:
        return []

    duration_ms = {
        test["number"]: int(round(test["seconds"] * 1000))
        for test in tests_selected
    }
    ordered = sorted(
        tests_selected,
        key=lambda test: duration_ms[test["number"]],
        reverse=True,
    )

    best_workers = None
    best_cost = None

    maximum_workers = min(MAXIMUM_WORKERS, len(ordered))
    for worker_count in range(1, maximum_workers + 1):
        workers = [[] for _ in range(worker_count)]
        loads = [0] * worker_count

        for test in ordered:
            lightest = min(range(worker_count), key=lambda index: loads[index])
            workers[lightest].append(test["number"])
            loads[lightest] += duration_ms[test["number"]]

        cost = max(loads) + WORKER_COST_MILLISECONDS * worker_count

        if best_cost is None or cost < best_cost:
            best_cost = cost
            best_workers = workers

    return [worker for worker in best_workers if worker]


def main():
    data = json.loads((DATA / "tests.json").read_text(encoding="utf-8"))
    tests = data["tests"]

    by_file, method_to_nums, id_to_num = build_maps(tests)
    test_by_num = {test["number"]: test for test in tests}
    coverage_files, source_spans, source_metadata, uncovered_tests = load_coverage(tests)

    for patch in sorted((DATA / "commits").glob("*.patch")):
        changed, only_comments, changed_lines, code_changed_files = (
            parse_patch(patch)
        )

        if not changed:
            print(json.dumps({"commit": patch.stem, "workers": []}))
            continue

        if only_comments:
            print(
                f"{patch.stem}: comment-only patch; selecting affected "
                "function tests where available",
                file=sys.stderr,
            )

        non_source = {
            file_path for file_path in changed
            if not file_path.startswith("sqlglot/")
        }

        selected = select_legacy(
            non_source,
            by_file,
            method_to_nums,
            tests,
        )

        selected.update(
            select_by_coverage(
                changed,
                code_changed_files,
                changed_lines,
                coverage_files,
                source_spans,
                source_metadata,
                by_file,
                method_to_nums,
                id_to_num,
                tests,
            )
        )

        selected.update(
            select_uncovered_safety_tests(
                changed,
                code_changed_files,
                changed_lines,
                source_spans,
                uncovered_tests,
            )
        )

        selected = {
            number for number in selected
            if number in test_by_num
        }

        selected_tests = [test_by_num[number] for number in selected]
        workers = split(selected_tests)

        print(json.dumps({
            "commit": patch.stem,
            "workers": workers,
        }))

        print(
            f"{patch.stem}: {len(changed)} files -> "
            f"{len(selected_tests)} tests, {len(workers)} workers",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
