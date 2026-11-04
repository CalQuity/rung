"""Cart pricing: line totals, coupons and the order total.

Everything here is pure and works in integer cents.
"""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Coupon:
    code: str
    description: str
    percent_off: int = 0
    amount_off_cents: int = 0
    min_subtotal_cents: int = 0


@dataclass(frozen=True)
class Totals:
    subtotal_cents: int
    discount_cents: int
    total_cents: int


COUPONS: dict[str, Coupon] = {
    c.code: c
    for c in (
        Coupon("WELCOME10", "10% off your order", percent_off=10),
        Coupon("FRIENDS50", "$50 off, from a friend", amount_off_cents=5000),
        Coupon("BIGSPENDER", "20% off orders over $200", percent_off=20, min_subtotal_cents=20000),
    )
}


def find_coupon(code: str) -> Coupon | None:
    """Look up a coupon by code, ignoring case and surrounding spaces."""
    return COUPONS.get(code.strip().upper())


def coupon_error(coupon: Coupon, subtotal_cents: int) -> str | None:
    """Why `coupon` cannot be used on a cart with this subtotal, or None if it can."""
    if subtotal_cents < coupon.min_subtotal_cents:
        return f"{coupon.code} needs a subtotal of at least ${coupon.min_subtotal_cents / 100:.2f}."
    return None


def line_total_cents(unit_price_cents: int, quantity: int) -> int:
    """Price of one cart line."""
    return unit_price_cents * quantity


def discount_cents(coupon: Coupon | None, subtotal_cents: int) -> int:
    """How much `coupon` takes off a cart with this subtotal."""
    if coupon is None:
        return 0
    if coupon.percent_off:
        return subtotal_cents * coupon.percent_off // 100
    return coupon.amount_off_cents


def cart_totals(lines: Iterable[tuple[int, int]], coupon: Coupon | None = None) -> Totals:
    """Subtotal, discount and total for `(unit_price_cents, quantity)` lines.

    The total is never negative: a discount can bring an order down to zero, not below it.
    """
    subtotal = sum(line_total_cents(price, qty) for price, qty in lines)
    discount = discount_cents(coupon, subtotal)
    return Totals(subtotal_cents=subtotal, discount_cents=discount, total_cents=subtotal - discount)
