"""Reading and building carts."""

import sqlite3
import uuid

from app import pricing, schemas


def open_cart_id(conn: sqlite3.Connection, session_id: str) -> str:
    """The session's open cart, created if it has none."""
    row = conn.execute(
        "SELECT id FROM carts WHERE session_id = ? AND status = 'open' ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    if row:
        return row["id"]
    cart_id = uuid.uuid4().hex
    conn.execute("INSERT INTO carts (id, session_id) VALUES (?, ?)", (cart_id, session_id))
    return cart_id


def load_cart(conn: sqlite3.Connection, cart_id: str) -> schemas.Cart:
    """The cart with its lines and totals."""
    cart = conn.execute("SELECT * FROM carts WHERE id = ?", (cart_id,)).fetchone()
    rows = conn.execute(
        """
        SELECT p.*, ci.quantity
        FROM cart_items ci JOIN products p ON p.id = ci.product_id
        WHERE ci.cart_id = ?
        ORDER BY p.id
        """,
        (cart_id,),
    ).fetchall()
    lines = [
        schemas.CartLine(
            product=schemas.Product(**{k: row[k] for k in schemas.Product.model_fields}),
            quantity=row["quantity"],
            line_total_cents=pricing.line_total_cents(row["price_cents"], row["quantity"]),
        )
        for row in rows
    ]
    coupon = pricing.find_coupon(cart["coupon_code"]) if cart["coupon_code"] else None
    totals = pricing.cart_totals(((line.product.price_cents, line.quantity) for line in lines), coupon)
    return schemas.Cart(
        id=cart_id,
        items=lines,
        item_count=sum(line.quantity for line in lines),
        coupon=schemas.AppliedCoupon(code=coupon.code, description=coupon.description) if coupon else None,
        subtotal_cents=totals.subtotal_cents,
        discount_cents=totals.discount_cents,
        total_cents=totals.total_cents,
    )
