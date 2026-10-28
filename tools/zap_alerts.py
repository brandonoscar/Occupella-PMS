"""Judges a ZAP report (its traditional JSON) for zap.yml: every alert of low risk or higher fails
the scan unless ci/zap-ignore.toml names its rule with why it is no risk here, maybe only on some
paths. Informational alerts are listed, never failed.

usage: python -m tools.zap_alerts REPORT.json [REPORT.json ...]
       python -m tools.zap_alerts --check   (only that every ignored rule says why)
"""

from __future__ import annotations

import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
IGNORES = ROOT / "ci/zap-ignore.toml"
RISKS = {0: "Informational", 1: "Low", 2: "Medium", 3: "High"}


@dataclass(frozen=True)
class Alert:
    rule: str
    name: str
    risk: int
    where: list[str]  # every place it was seen: "METHOD uri"


@dataclass(frozen=True)
class Ignore:
    reason: str
    paths: frozenset[str] = frozenset()  # only on these, when it names any


def path(place: str) -> str:
    """The path a place ("METHOD uri") was seen at, without its query or a trailing slash."""
    return urlsplit(place.rsplit(" ", 1)[-1]).path.rstrip("/") or "/"


def alerts(report: dict[str, Any]) -> list[Alert]:
    """Each alert in a report, by site, with every place it was seen."""
    found = []
    for site in report.get("site", []):
        for alert in site.get("alerts", []):
            found.append(
                Alert(
                    str(alert["pluginid"]),
                    str(alert.get("name") or alert.get("alert", "")),
                    int(alert["riskcode"]),
                    [
                        f"{i.get('method', '')} {i.get('uri', '')}".strip()
                        for i in alert.get("instances", [])
                    ],
                )
            )
    return found


def ignored(file: Path | None = None) -> dict[str, Ignore]:
    """Each ignored rule, why, and the paths it is ignored on if not everywhere
    (ci/zap-ignore.toml unless `file`). A rule ignored without a reason, or twice, is refused,
    and so are paths that aren't each a path from /."""
    file = file or IGNORES
    rules: dict[str, Ignore] = {}
    for entry in tomllib.loads(file.read_text()).get("ignore", []):
        rule, reason = str(entry.get("rule", "")), str(entry.get("reason", "")).strip()
        if not rule or not reason:
            raise ValueError(f"{file.name}: every ignored rule needs its rule and a reason")
        if rule in rules:
            raise ValueError(f"{file.name}: rule {rule} is ignored twice")
        paths = entry.get("paths")
        if paths is not None and (
            not isinstance(paths, list)
            or not paths
            or not all(isinstance(p, str) and p.startswith("/") for p in paths)
        ):
            raise ValueError(f"{file.name}: rule {rule}'s paths must each be a path from /")
        rules[rule] = Ignore(reason, frozenset(path(p) for p in paths or []))
    return rules


def judge(found: list[Alert], ignore: dict[str, Ignore]) -> tuple[list[str], list[str]]:
    """What fails the scan, and what is only listed (informational, or ignored with why). A rule
    ignored on some paths fails wherever else it was seen."""
    failing, listed = [], []
    for a in sorted(found, key=lambda a: (-a.risk, a.rule)):
        why = ignore.get(a.rule)
        left = [w for w in a.where if why is None or (why.paths and path(w) not in why.paths)]
        if why and not left and (a.where or not why.paths):
            listed.append(f"{line(a, a.where)} (ignored: {why.reason})")
        elif a.risk >= 1:
            failing.append(line(a, left))
        else:
            listed.append(line(a, a.where))
    return failing, listed


def line(a: Alert, places: list[str]) -> str:
    """An alert in a line, with the first few of its places."""
    return f"{RISKS.get(a.risk, a.risk)} [{a.rule}] {a.name}: {'; '.join(places[:3])}"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        ignore = ignored()
    except (ValueError, tomllib.TOMLDecodeError) as bad:
        print(f"FAIL: {bad}")
        return 1
    if args == ["--check"]:
        print(f"ok: {len(ignore)} rules ignored, each with why")
        return 0
    paths = [Path(p) for p in args]
    if not paths:
        print("usage: python -m tools.zap_alerts REPORT.json [REPORT.json ...] | --check")
        return 2
    found = [a for p in paths for a in alerts(json.loads(p.read_text()))]
    failing, listed = judge(found, ignore)
    for line in listed:
        print(f"  {line}")
    for line in failing:
        print(f"FAIL: {line}")
    if failing:
        print(f"{len(failing)} alerts of low risk or higher; fix each, or ignore its rule with why")
        return 1
    print(f"ok: {len(found)} alerts, none of low risk or higher that isn't ignored with why")
    return 0


if __name__ == "__main__":
    sys.exit(main())
