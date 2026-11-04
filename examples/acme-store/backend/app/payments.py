"""A stand-in for a card payment provider.

It behaves like a remote API: every charge takes a while, and some can be declined.
`PAYMENTS_LATENCY_MS` sets how long a charge takes and `PAYMENTS_FAIL_RATE` (0 to 1) how often
one is declined.
"""

import asyncio
import random
import sqlite3
import uuid
from dataclasses import dataclass

from app.config import Settings


class PaymentDeclined(Exception):
    """The provider refused the charge."""


@dataclass(frozen=True)
class Charge:
    id: str
    amount_cents: int
    card_last4: str
    status: str


async def charge_card(
    conn: sqlite3.Connection, settings: Settings, *, order_id: int, amount_cents: int, card_number: str
) -> Charge:
    """Charge the card and record the result in the `charges` table.

    Raises:
        PaymentDeclined: the provider declined the card. The declined attempt is still recorded.
    """
    await asyncio.sleep(settings.payments_latency_ms / 1000)
    declined = random.random() < settings.payments_fail_rate
    charge = Charge(
        id=f"ch_{uuid.uuid4().hex[:16]}",
        amount_cents=amount_cents,
        card_last4=card_number[-4:],
        status="declined" if declined else "succeeded",
    )
    conn.execute(
        "INSERT INTO charges (id, order_id, amount_cents, card_last4, status) VALUES (?, ?, ?, ?, ?)",
        (charge.id, order_id, charge.amount_cents, charge.card_last4, charge.status),
    )
    if declined:
        raise PaymentDeclined("Your card was declined. Try another card.")
    return charge
