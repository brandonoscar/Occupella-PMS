"""Every staff page that takes a day answers the calendar's first and last days in words, never
with a server error. The nightly API fuzz found the Dashboard answering 0001-01-01 with one (a
month back from it is year 0); this asks each operation that takes a day in its query, and no
path, the same of each such parameter, alone, as a signed-in staff member. Operations with an
id in their path refuse an unknown id before reading a day; the fuzz covers those."""

from people import join_as_staff

from pms.api import create_app

ENDS = ("0001-01-01", "9999-12-31")


def operations_with_days(settings):
    schema = create_app(settings).openapi()
    for path, item in schema["paths"].items():
        get = item.get("get")
        if get is None or "{" in path:
            continue
        for parameter in get.get("parameters", []):
            kinds = parameter["schema"].get("anyOf", [parameter["schema"]])
            if parameter["in"] == "query" and any(k.get("format") == "date" for k in kinds):
                yield path, parameter["name"]


def test_some_operations_take_a_day(settings):
    assert len(list(operations_with_days(settings))) >= 10


def test_each_answers_the_calendars_ends_without_a_server_error(
    client, conn, app_conn, pmc, settings
):
    join_as_staff(client, conn, app_conn, pmc)
    failures = []
    for path, name in operations_with_days(settings):
        for day in ENDS:
            answer = client.get(path, params={name: day})
            if answer.status_code >= 500:
                failures.append(f"GET {path}?{name}={day}: {answer.status_code}")
    assert failures == []
