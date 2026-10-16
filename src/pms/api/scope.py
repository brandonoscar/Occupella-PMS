"""Narrowing a list to a property group or to one property (the founder's punch list,
2026-10-16): staff pick it once, top left of every list page, and the page passes it on as
`?group=` or `?property=`. A list keeps the rows whose property is in it; nothing chosen, all.

A group or property of another PMC narrows to nothing: each list already keeps to the session's
PMC, and a group holds only its own PMC's properties.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import Query

Group = Annotated[UUID | None, Query(alias="group")]
Property = Annotated[UUID | None, Query(alias="property")]


def within(column: str) -> str:
    """A condition that `column`, a property's id, is in the scope of `params()`."""
    return (
        f"(%(scope_group)s::uuid IS NULL"
        f" OR {column} IN (SELECT trust_group_properties(%(scope_group)s::uuid)))"
        f" AND (%(scope_property)s::uuid IS NULL OR {column} = %(scope_property)s::uuid)"
    )


def params(group: UUID | None, property_id: UUID | None) -> dict[str, Any]:
    return {"scope_group": group, "scope_property": property_id}
