"""Every GET route that writes something (a tax ID revealed, a payout file taken) refuses a
request a browser says another site asked for, as every change does (app.guard): the session
cookie is SameSite=Strict, which a sibling subdomain's page still counts as the same site.

The AI pen test of 11/06 found four such GETs, none checking. This walks the API's code, so a
new GET that writes and forgets the check fails here, whoever adds it."""

import ast
import pathlib
import re

API = pathlib.Path(__file__).resolve().parents[2] / "src" / "pms" / "api"
WRITES = re.compile(r"INSERT INTO|UPDATE trust_|DELETE FROM")
Function = ast.FunctionDef | ast.AsyncFunctionDef


def writes(function: Function, functions: dict[str, Function], seen: tuple[str, ...] = ()) -> bool:
    """Whether a function writes, or calls one of its module's functions that does."""
    if WRITES.search(ast.unparse(function)):
        return True
    called = {
        call.func.id
        for call in ast.walk(function)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    }
    return any(
        writes(functions[name], functions, (*seen, function.name))
        for name in called
        if name in functions and name not in seen and name != function.name
    )


def is_get(function: Function) -> bool:
    return any(
        isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "get"
        for d in function.decorator_list
    )


def gets_that_write() -> dict[str, Function]:
    found = {}
    for path in sorted(API.glob("*.py")):
        tree = ast.parse(path.read_text())
        functions = {n.name: n for n in ast.walk(tree) if isinstance(n, Function)}
        for function in functions.values():
            if is_get(function) and writes(function, functions):
                found[f"{path.name}:{function.name}"] = function
    return found


def test_every_get_that_writes_is_asked_from_this_site():
    found = gets_that_write()
    assert len(found) >= 4  # the scan sees them: the payout files, a reveal, the 1099 export
    unchecked = [
        name
        for name, function in found.items()
        if not any(
            isinstance(a.annotation, ast.Name) and a.annotation.id == "FromThisSite"
            for a in function.args.args
        )
    ]
    assert unchecked == []
