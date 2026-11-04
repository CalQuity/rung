import re
import sqlite3

from fastapi import APIRouter, HTTPException, Request

from app.carts import load_cart, open_cart_id
from app.deps import Db, SessionId
from app.payments import PaymentDeclined, charge_card
from app.schemas import CheckoutRequest, Order, OrderLine, Payment

router = APIRouter(prefix="/api", tags=["orders"])


def _order_number(order_id: int) -> str:
    return f"ACME-{1000 + order_id}"


def _load_order(conn: sqlite3.Connection, row: sqlite3.Row) -> Order:
    items = conn.execute("SELECT * FROM order_items WHERE order_id = ?", (row["id"],)).fetchall()
    charge = conn.execute(
        "SELECT * FROM charges WHERE order_id = ? ORDER BY created_at DESC LIMIT 1", (row["id"],)
    ).fetchone()
    return Order(
        number=row["number"],
        status=row["status"],
        customer_name=row["customer_name"],
        customer_email=row["customer_email"],
        coupon_code=row["coupon_code"],
        items=[
            OrderLine(
                product_id=i["product_id"],
                name=i["name"],
                emoji=i["emoji"],
                unit_price_cents=i["unit_price_cents"],
                quantity=i["quantity"],
                line_total_cents=i["unit_price_cents"] * i["quantity"],
            )
            for i in items
        ],
        subtotal_cents=row["subtotal_cents"],
        discount_cents=row["discount_cents"],
        total_cents=row["total_cents"],
        payment=Payment(charge_id=charge["id"], card_last4=charge["card_last4"], status=charge["status"])
        if charge
        else None,
        created_at=row["created_at"],
    )


@router.post("/checkout", status_code=201)
async def checkout(
    body: CheckoutRequest,
    request: Request,
    conn: Db,
    sid: SessionId,
) -> Order:
    card_number = re.sub(r"[\s-]", "", body.card_number)
    if not re.fullmatch(r"\d{16}", card_number):
        raise HTTPException(422, "Enter a 16-digit card number.")

    cart_id = open_cart_id(conn, sid)
    cart = load_cart(conn, cart_id)
    if not cart.items:
        raise HTTPException(400, "Your cart is empty.")

    cursor = conn.execute(
        """
        INSERT INTO orders (session_id, cart_id, customer_name, customer_email, coupon_code,
                            subtotal_cents, discount_cents, total_cents)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sid,
            cart_id,
            body.name.strip(),
            body.email.strip(),
            cart.coupon.code if cart.coupon else None,
            cart.subtotal_cents,
            cart.discount_cents,
            cart.total_cents,
        ),
    )
    order_id = cursor.lastrowid
    assert order_id is not None
    conn.execute("UPDATE orders SET number = ? WHERE id = ?", (_order_number(order_id), order_id))
    conn.executemany(
        """
        INSERT INTO order_items (order_id, product_id, name, emoji, unit_price_cents, quantity)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (order_id, line.product.id, line.product.name, line.product.emoji, line.product.price_cents, line.quantity)
            for line in cart.items
        ],
    )

    try:
        await charge_card(
            conn,
            request.app.state.settings,
            order_id=order_id,
            amount_cents=cart.total_cents,
            card_number=card_number,
        )
    except PaymentDeclined as exc:
        conn.execute("UPDATE orders SET status = 'payment_failed' WHERE id = ?", (order_id,))
        raise HTTPException(402, str(exc)) from exc

    conn.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (order_id,))
    conn.execute("UPDATE carts SET status = 'ordered' WHERE id = ?", (cart_id,))
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return _load_order(conn, row)


@router.get("/orders")
def list_orders(conn: Db, sid: SessionId) -> list[Order]:
    rows = conn.execute(
        "SELECT * FROM orders WHERE session_id = ? AND status = 'paid' ORDER BY id DESC", (sid,)
    ).fetchall()
    return [_load_order(conn, row) for row in rows]


@router.get("/orders/{number}")
def get_order(number: str, conn: Db, sid: SessionId) -> Order:
    row = conn.execute("SELECT * FROM orders WHERE number = ? AND session_id = ?", (number, sid)).fetchone()
    if row is None:
        raise HTTPException(404, "Order not found")
    return _load_order(conn, row)
