"""The payment processor's webhook (migrations 20261022000158 and 20261022000159): Stripe's
signed events about the payments it took (src/pms/api/online_payments.py) and the cards and
accounts it saved for autopay (src/pms/api/autopay.py), each taken once. No session: the
signature, made with the endpoint's secret from the server's environment, is what lets it in.
NOT VERIFIED against Stripe itself, which needs keys.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from pms.api import autopay, online_payments
from pms.api.deps import Conn
from pms.payments import processor as processors

router = APIRouter()


@router.post("/api/payments/stripe/webhook")
async def stripe_webhook(request: Request, conn: Conn) -> dict[str, int]:
    """Stripe's events, signed with this endpoint's secret: how many were taken."""
    found = online_payments.processor_of(request)
    if found is None or found.name != "stripe":
        raise HTTPException(404, "this server takes no Stripe payments")
    body = await request.body()
    try:
        events = found.events(body, {k.lower(): v for k, v in request.headers.items()})
    except processors.BadSignature as bad:
        raise HTTPException(400, str(bad)) from None
    taken = 0
    for event in events:
        if event.outcome in ("saved", "not saved"):
            taken += autopay.take_saved(conn, "stripe", event)
        else:
            taken += online_payments.take(conn, "stripe", event)
    return {"taken": taken}
