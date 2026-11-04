# Solutions

Spoilers: the deliberate bugs in Acme Store, how to see each one, and the fix.

## 1. A coupon can make the total negative

`cart_totals` in `backend/app/pricing.py` promises that "the total is never negative", but
`discount_cents` returns a fixed coupon's full amount whatever the subtotal is.

**See it:** add the Pour-Over Coffee Kit ($34.00) to an empty cart and apply `FRIENDS50`. The cart
total reads **-$16.00**, and checking out records a charge of -1600 cents.

**Fix:** cap the discount at the subtotal.

```python
def discount_cents(coupon: Coupon | None, subtotal_cents: int) -> int:
    if coupon is None or subtotal_cents <= 0:
        return 0
    if coupon.percent_off:
        return subtotal_cents * coupon.percent_off // 100
    return min(coupon.amount_off_cents, subtotal_cents)
```

A property test finds it quickly: for any lines and any coupon, `0 <= total_cents <= subtotal_cents`.

## 2. Placing an order twice charges the card twice

`POST /api/checkout` (`backend/app/routes/orders.py`) reads the open cart, creates an order, awaits
the payment provider (600 ms by default), and only then marks the cart as ordered. A second request
for the same cart that arrives during that wait sees the cart still open and does it all again. The
Place order button is not disabled while the request is in flight, so a double click is enough.
A client retrying after a timeout does the same.

**See it:** fill in the checkout form and double-click **Place order**. The Orders page lists two
orders for one cart, and the `charges` table holds two successful charges:

```bash
sqlite3 backend/acme.db "SELECT order_id, amount_cents, status FROM charges"
```

Or from the shell, with a cart in the session cookie jar:

```bash
for i in 1 2; do
  curl -s -b jar -c jar -X POST localhost:3000/api/checkout -H 'content-type: application/json' \
    -d '{"name":"A","email":"a@example.com","card_number":"4242424242424242"}' &
done; wait
```

**Fix:** claim the cart atomically before charging, and make the endpoint idempotent.

```python
claimed = conn.execute("UPDATE carts SET status = 'checking_out' WHERE id = ? AND status = 'open'", (cart_id,)).rowcount
if not claimed:
    raise HTTPException(409, "This cart is already being checked out.")
```

Set the cart back to `open` if the charge is declined. Also accept an `Idempotency-Key` header and
return the existing order for a key that was already used, and disable the button while the request
is pending.

## 3. The API accepts zero and negative quantities

The cart page's stepper never goes below 1, but `PATCH /api/cart/items/{product_id}` and
`POST /api/cart/items` take any integer. A quantity of 0 leaves a zero-priced line in the cart; a
negative quantity subtracts from the subtotal.

**See it:** with the Canvas Weekender and a Cedar & Smoke Candle in the cart,

```bash
curl -s -b jar -X PATCH localhost:3000/api/cart/items/5 -H 'content-type: application/json' \
  -d '{"quantity": -3}'
```

The subtotal drops from $146.00 to $74.00.

**Fix:** validate at the API boundary.

```python
class AddItem(BaseModel):
    product_id: int
    quantity: int = Field(default=1, ge=1, le=99)


class UpdateItem(BaseModel):
    quantity: int = Field(ge=1, le=99)
```

`line_total_cents` can also reject a quantity below 1, so the pricing module does not depend on
every caller validating.
