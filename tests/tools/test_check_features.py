"""tools/check_features.py: every feature is verified in a browser, and stays verified."""

import json
import tomllib

import pytest

from tools import check_features

OPERATIONS = {
    "GET /api/rent",
    "POST /api/rent/charge-rent",
    "GET /api/reconcile/{bank_account_id}",
    "POST /api/reconcile/statements",
    "POST /api/reconcile/matches/{match_id}/undo",
    "GET /health",
}
RENT_TEST = "features.spec.ts › staff charge this month's rent"


def registry(**changes):
    feature = {
        "id": "rent-run",
        "statement": "Staff charge rent.",
        "operations": ["GET /api/rent", "POST /api/rent/charge-rent"],
        "browser": [RENT_TEST],
    }
    month_end = {
        "id": "month-end",
        "statement": "Staff reconcile.",
        "operations": [
            "GET /api/reconcile/{bank_account_id}",
            "POST /api/reconcile/statements",
            "POST /api/reconcile/matches/{match_id}/undo",
        ],
        "browser": ["portal.spec.ts › staff reconcile"],
    }
    return {
        "outside": {"GET /health": "the health check"},
        "feature": [{**feature, **changes}, month_end],
    }


def test_the_repos_own_registry_claims_everything_the_api_serves():
    operations = check_features.served(check_features.ROOT)
    on_file = tomllib.loads((check_features.ROOT / check_features.FILE).read_text())
    # The API as served, the fake partners' routes included.
    assert {"GET /health", "POST /api/rent/charge-rent", "POST /api/fake-esign/sign"} <= operations
    assert check_features.check(on_file, operations) == []


def test_a_complete_registry_passes():
    assert check_features.check(registry(), OPERATIONS) == []


def test_an_operation_no_feature_claims_fails():
    problems = check_features.check(registry(operations=["GET /api/rent"]), OPERATIONS)
    assert problems == ["POST /api/rent/charge-rent is served but no feature claims it."]


def test_an_operation_the_api_doesnt_serve_fails():
    named = ["GET /api/rent", "POST /api/rent/charge-rent", "POST /api/rent/gone"]
    problems = check_features.check(registry(operations=named), OPERATIONS)
    assert problems == ["rent-run: the API serves no POST /api/rent/gone."]


@pytest.mark.parametrize(
    "field, problem",
    [
        ("statement", "rent-run: missing `statement`."),
        ("browser", "rent-run: names no browser test."),
    ],
)
def test_a_feature_needs_a_statement_and_a_browser_test(field, problem):
    assert check_features.check(registry(**{field: []}), OPERATIONS) == [problem]


def test_a_feature_needs_an_operation():
    problems = check_features.check(registry(operations=[]), OPERATIONS)
    assert "rent-run: names no API operation." in problems


def test_two_features_may_not_share_an_id():
    problems = check_features.check(registry(id="month-end"), OPERATIONS)
    assert problems == ["month-end: two features have this id."]


def test_what_is_outside_the_features_is_served_unclaimed_and_says_why():
    outside = registry()
    outside["outside"] = {"GET /health": "", "GET /gone": "why", "GET /api/rent": "why"}
    assert check_features.check(outside, OPERATIONS) == [
        "[outside]: GET /health needs a reason.",
        "[outside]: the API serves no GET /gone.",
        "[outside]: GET /api/rent is claimed by a feature too.",
    ]


LISTING = {
    "suites": [
        {
            "title": "features.spec.ts",
            "specs": [{"title": "staff charge this month's rent"}],
            "suites": [],
        },
        {
            "title": "portal.spec.ts",
            "specs": [{"title": "staff reconcile"}],
            "suites": [{"title": "a group", "specs": [{"title": "inner"}], "suites": []}],
        },
    ]
}


def test_browser_tests_are_read_from_playwrights_own_list():
    assert check_features.browser_tests(LISTING) == {
        RENT_TEST,
        "portal.spec.ts › staff reconcile",
        "portal.spec.ts › a group › inner",
    }


def test_a_named_browser_test_must_exist_and_every_test_must_be_named():
    tests = check_features.browser_tests(LISTING) - {RENT_TEST}
    assert check_features.check_browser(registry(), tests) == [
        f"rent-run: browser test {RENT_TEST!r} does not exist.",
        "browser test 'portal.spec.ts › a group › inner' is named by no feature.",
    ]


@pytest.mark.parametrize(
    "method, path, operation",
    [
        ("GET", "/api/rent?as_of=2026-10-01", "GET /api/rent"),
        ("POST", "/api/reconcile/statements", "POST /api/reconcile/statements"),
        ("GET", "/api/reconcile/0d57161f-4cad", "GET /api/reconcile/{bank_account_id}"),
        ("POST", "/api/reconcile/matches/7/undo", "POST /api/reconcile/matches/{match_id}/undo"),
        ("POST", "/api/rent", None),
        ("GET", "/api/reconcile/a/b", None),
        ("GET", "/assets/index.js", None),
    ],
)
def test_a_request_is_matched_to_its_operation(method, path, operation):
    assert check_features.operation_of(method, path, OPERATIONS) == operation


def test_the_literal_route_wins_over_a_parameter():
    both = {"GET /api/owner/{part}", "GET /api/owner/year-end"}
    assert check_features.operation_of("GET", "/api/owner/year-end", both) == (
        "GET /api/owner/year-end"
    )


def access(method, path, status):
    return f'INFO:     127.0.0.1:43782 - "{method} {path} HTTP/1.1" {status} OK'


EVERY_CALL = [
    access("GET", "/api/rent", 200),
    access("POST", "/api/rent/charge-rent", 201),
    access("GET", "/api/reconcile/b1", 200),
    access("POST", "/api/reconcile/statements", 409),  # refused is still called
    access("POST", "/api/reconcile/matches/m1/undo", 200),
]


def test_every_claimed_operation_called_without_a_server_error_passes():
    log = "\n".join(["INFO:     Uvicorn running", *EVERY_CALL])
    assert check_features.check_log(registry(), OPERATIONS, log) == []


def test_an_operation_never_called_by_the_browser_tests_fails():
    log = "\n".join(EVERY_CALL[:-1])
    assert check_features.check_log(registry(), OPERATIONS, log) == [
        "month-end: POST /api/reconcile/matches/{match_id}/undo was never called by the browser"
        " tests."
    ]


def test_a_server_error_fails():
    log = "\n".join([*EVERY_CALL, access("GET", "/api/rent?as_of=x", 500)])
    assert check_features.check_log(registry(), OPERATIONS, log) == [
        "GET /api/rent was answered 500."
    ]


def test_a_feature_may_not_be_removed_or_lose_a_browser_test():
    base = registry()
    assert check_features.compare(base, registry()) == []
    # A test renamed is still a test.
    assert check_features.compare(base, registry(browser=["features.spec.ts › renamed"])) == []
    fewer = registry()
    fewer["feature"][1]["browser"] = []
    gone = registry()
    gone["feature"] = gone["feature"][:1]
    assert check_features.compare(base, fewer) == [
        "month-end has fewer browser tests than on the base branch."
    ]
    assert check_features.compare(base, gone) == [
        f"month-end was removed from {check_features.FILE}."
    ]


def test_main_passes_on_the_repo_and_checks_a_browser_run(tmp_path, capsys):
    on_file = tomllib.loads((check_features.ROOT / check_features.FILE).read_text())
    named = sorted({test for f in on_file["feature"] for test in f["browser"]})
    specs: dict[str, list[dict[str, str]]] = {}
    for test in named:
        spec, title = test.split(" › ", 1)
        specs.setdefault(spec, []).append({"title": title})
    listing = tmp_path / "list.json"
    listing.write_text(
        json.dumps({"suites": [{"title": s, "specs": t, "suites": []} for s, t in specs.items()]})
    )
    log = tmp_path / "api.log"
    log.write_text("")

    assert check_features.main([]) == 0
    assert check_features.main(["--browser-tests", str(listing)]) == 0
    # A browser run that called nothing verifies nothing.
    assert check_features.main(["--api-log", str(log)]) == 1
    assert "was never called by the browser tests" in capsys.readouterr().out


def test_main_against_a_base_commit(repo, capsys):
    text = (check_features.ROOT / check_features.FILE).read_text()
    repo.write(check_features.FILE, text)
    base = repo.commit("base")
    on_file = tomllib.loads(text)
    removed = on_file["feature"][-1]["id"]
    # The last feature's block is the end of the file.
    repo.write(check_features.FILE, text[: text.rindex("[[feature]]")])
    repo.commit("drop a feature")

    assert check_features.main(["--root", str(repo.path), "--base", base]) == 1
    out = capsys.readouterr().out
    assert f"FAIL: {removed} was removed from {check_features.FILE}." in out
