"""Judges a ZAP report (its traditional JSON) for zap.yml: every alert of low risk or higher fails
the scan unless ci/zap-ignore.toml names its rule with why it is no risk here. Informational
alerts are listed, never failed.

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

ROOT = Path(__file__).resolve().parent.parent
IGNORES = ROOT / "ci/zap-ignore.toml"
RISKS = {0: "Informational", 1: "Low", 2: "Medium", 3: "High"}


@dataclass(frozen=True)
class Alert:
    rule: str
    name: str
    risk: int
    where: list[str]


def alerts(report: dict[str, Any]) -> list[Alert]:
    """Each alert in a report, by site, with the first few places it was seen."""
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
                        for i in alert.get("instances", [])[:3]
                    ],
                )
            )
    return found


def ignored(path: Path | None = None) -> dict[str, str]:
    """Each ignored rule and why (ci/zap-ignore.toml unless `path`). A rule ignored without a
    reason is refused."""
    path = path or IGNORES
    rules: dict[str, str] = {}
    for entry in tomllib.loads(path.read_text()).get("ignore", []):
        rule, reason = str(entry.get("rule", "")), str(entry.get("reason", "")).strip()
        if not rule or not reason:
            raise ValueError(f"{path.name}: every ignored rule needs its rule and a reason")
        rules[rule] = reason
    return rules


def judge(found: list[Alert], ignore: dict[str, str]) -> tuple[list[str], list[str]]:
    """What fails the scan, and what is only listed (informational, or ignored with why)."""
    failing, listed = [], []
    for a in sorted(found, key=lambda a: (-a.risk, a.rule)):
        line = f"{RISKS.get(a.risk, a.risk)} [{a.rule}] {a.name}: {'; '.join(a.where)}"
        if a.rule in ignore:
            listed.append(f"{line} (ignored: {ignore[a.rule]})")
        elif a.risk >= 1:
            failing.append(line)
        else:
            listed.append(line)
    return failing, listed


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
