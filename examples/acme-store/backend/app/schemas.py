"""Request and response bodies."""

from pydantic import BaseModel, Field


class Product(BaseModel):
    id: int
    slug: str
    name: str
    description: str
    category: str
    price_cents: int
    emoji: str
    color_from: str
    color_to: str


class CartLine(BaseModel):
    product: Product
    quantity: int
    line_total_cents: int


class AppliedCoupon(BaseModel):
    code: str
    description: str


class Cart(BaseModel):
    id: str
    items: list[CartLine]
    item_count: int
    coupon: AppliedCoupon | None
    subtotal_cents: int
    discount_cents: int
    total_cents: int


class AddItem(BaseModel):
    product_id: int
    quantity: int = 1


class UpdateItem(BaseModel):
    quantity: int


class ApplyCoupon(BaseModel):
    code: str = Field(min_length=1, max_length=40)


class CheckoutRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    card_number: str


class OrderLine(BaseModel):
    product_id: int
    name: str
    emoji: str
    unit_price_cents: int
    quantity: int
    line_total_cents: int


class Payment(BaseModel):
    charge_id: str
    card_last4: str
    status: str


class Order(BaseModel):
    number: str
    status: str
    customer_name: str
    customer_email: str
    coupon_code: str | None
    items: list[OrderLine]
    subtotal_cents: int
    discount_cents: int
    total_cents: int
    payment: Payment | None
    created_at: str
