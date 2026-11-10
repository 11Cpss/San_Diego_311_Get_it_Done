"""Repo-wide pytest hooks for the lesson system.

Some core functions are stubs that you write during the lessons (see
`lessons/00_START_HERE.md`). A stub raises

    NotImplementedError("[lesson 2] ...")

or, for SQL / Terraform / YAML files, contains a line with ``LESSON <n> STUB``.

By default a test that runs into an unfinished stub is reported as SKIPPED
("unfinished [lesson 2] ..."), so the normal `pytest` run and CI stay green while
you work through the lessons. `python scripts/lessons.py check <n>` runs the
lesson's tests with ``--lesson-strict``, which turns those skips into failures.
"""

from pathlib import Path

import pytest

LESSON_TAG = "[lesson "
REPO_ROOT = Path(__file__).resolve().parent


def pytest_addoption(parser):
    parser.addoption(
        "--lesson-strict",
        action="store_true",
        default=False,
        help="Fail (instead of skip) tests that hit an unfinished lesson stub.",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if item.config.getoption("--lesson-strict"):
        return
    if not report.failed or call.excinfo is None:
        return
    if call.excinfo.errisinstance(NotImplementedError) and LESSON_TAG in str(call.excinfo.value):
        report.outcome = "skipped"
        message = f"Skipped: unfinished {call.excinfo.value}"
        report.longrepr = (str(item.path), item.location[1] or 0, message)


def lesson_stub_pending(path, lesson: int) -> bool:
    """True if a non-Python file still contains its ``LESSON <n> STUB`` marker."""
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    return f"LESSON {lesson} STUB" in text


@pytest.fixture
def skip_if_lesson_pending(request):
    """Use in tests of SQL/Terraform/YAML lesson files:

    def test_x(skip_if_lesson_pending):
        skip_if_lesson_pending("dbt/models/silver/requests_clean.sql", 7)
    """

    def _check(path, lesson: int):
        if lesson_stub_pending(path, lesson) and not request.config.getoption("--lesson-strict"):
            pytest.skip(f"unfinished [lesson {lesson}] {path}")

    return _check
