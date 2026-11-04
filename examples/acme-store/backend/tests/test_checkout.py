import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

CUSTOMER = {"name": "Ada Lovelace", "email": "ada@example.com", "card_number": "4242 4242 4242 4242"}


def _fill_cart(client: TestClient, products: dict[str, dict]) -> None:
    client.post("/api/cart/items", json={"product_id": products["canvas-weekender"]["id"]})
    client.post("/api/cart/items", json={"product_id": products["cedar-candle"]["id"], "quantity": 2})


def test_checkout_creates_a_paid_order(client: TestClient, products: dict[str, dict], settings: Settings) -> None:
    _fill_cart(client, products)
    client.post("/api/cart/coupon", json={"code": "WELCOME10"})

    response = client.post("/api/checkout", json=CUSTOMER)
    assert response.status_code == 201
    order = response.json()
    assert order["number"].startswith("ACME-")
    assert order["status"] == "paid"
    assert order["subtotal_cents"] == 12800 + 2 * 1800
    assert order["total_cents"] == order["subtotal_cents"] - order["discount_cents"]
    assert order["payment"]["card_last4"] == "4242"

    with sqlite3.connect(settings.database_path) as conn:
        charges = conn.execute("SELECT amount_cents, status FROM charges").fetchall()
    assert charges == [(order["total_cents"], "succeeded")]


def test_checkout_empties_the_cart(client: TestClient, products: dict[str, dict]) -> None:
    _fill_cart(client, products)
    client.post("/api/checkout", json=CUSTOMER)
    assert client.get("/api/cart").json()["items"] == []


def test_order_history(client: TestClient, products: dict[str, dict]) -> None:
    _fill_cart(client, products)
    number = client.post("/api/checkout", json=CUSTOMER).json()["number"]

    assert [o["number"] for o in client.get("/api/orders").json()] == [number]
    assert client.get(f"/api/orders/{number}").json()["customer_name"] == "Ada Lovelace"
    assert client.get("/api/orders/ACME-9999").status_code == 404


def test_checkout_rejects_an_empty_cart(client: TestClient) -> None:
    assert client.post("/api/checkout", json=CUSTOMER).status_code == 400


def test_checkout_rejects_a_short_card_number(client: TestClient, products: dict[str, dict]) -> None:
    _fill_cart(client, products)
    response = client.post("/api/checkout", json=CUSTOMER | {"card_number": "4242"})
    assert response.status_code == 422


def test_declined_card(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "declined.db", payments_latency_ms=0, payments_fail_rate=1.0)
    with TestClient(create_app(settings)) as client:
        client.post("/api/cart/items", json={"product_id": 2})
        response = client.post("/api/checkout", json=CUSTOMER)
        assert response.status_code == 402
        assert client.get("/api/orders").json() == []
        assert len(client.get("/api/cart").json()["items"]) == 1
