from fastapi import APIRouter, HTTPException

from app import pricing
from app.carts import load_cart, open_cart_id
from app.deps import Db, SessionId
from app.schemas import AddItem, ApplyCoupon, Cart, UpdateItem

router = APIRouter(prefix="/api/cart", tags=["cart"])


@router.get("")
def get_cart(conn: Db, sid: SessionId) -> Cart:
    return load_cart(conn, open_cart_id(conn, sid))


@router.post("/items")
def add_item(body: AddItem, conn: Db, sid: SessionId) -> Cart:
    if conn.execute("SELECT 1 FROM products WHERE id = ?", (body.product_id,)).fetchone() is None:
        raise HTTPException(404, "Product not found")
    cart_id = open_cart_id(conn, sid)
    conn.execute(
        """
        INSERT INTO cart_items (cart_id, product_id, quantity) VALUES (?, ?, ?)
        ON CONFLICT (cart_id, product_id) DO UPDATE SET quantity = quantity + excluded.quantity
        """,
        (cart_id, body.product_id, body.quantity),
    )
    return load_cart(conn, cart_id)


@router.patch("/items/{product_id}")
def update_item(product_id: int, body: UpdateItem, conn: Db, sid: SessionId) -> Cart:
    cart_id = open_cart_id(conn, sid)
    updated = conn.execute(
        "UPDATE cart_items SET quantity = ? WHERE cart_id = ? AND product_id = ?",
        (body.quantity, cart_id, product_id),
    )
    if updated.rowcount == 0:
        raise HTTPException(404, "That item is not in your cart")
    return load_cart(conn, cart_id)


@router.delete("/items/{product_id}")
def remove_item(product_id: int, conn: Db, sid: SessionId) -> Cart:
    cart_id = open_cart_id(conn, sid)
    conn.execute("DELETE FROM cart_items WHERE cart_id = ? AND product_id = ?", (cart_id, product_id))
    return load_cart(conn, cart_id)


@router.post("/coupon")
def apply_coupon(body: ApplyCoupon, conn: Db, sid: SessionId) -> Cart:
    cart_id = open_cart_id(conn, sid)
    coupon = pricing.find_coupon(body.code)
    if coupon is None:
        raise HTTPException(400, f"{body.code.strip().upper()} is not a valid code.")
    error = pricing.coupon_error(coupon, load_cart(conn, cart_id).subtotal_cents)
    if error:
        raise HTTPException(400, error)
    conn.execute("UPDATE carts SET coupon_code = ? WHERE id = ?", (coupon.code, cart_id))
    return load_cart(conn, cart_id)


@router.delete("/coupon")
def remove_coupon(conn: Db, sid: SessionId) -> Cart:
    cart_id = open_cart_id(conn, sid)
    conn.execute("UPDATE carts SET coupon_code = NULL WHERE id = ?", (cart_id,))
    return load_cart(conn, cart_id)
