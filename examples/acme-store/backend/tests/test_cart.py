from fastapi.testclient import TestClient


def test_lists_the_catalog(client: TestClient) -> None:
    products = client.get("/api/products").json()
    assert len(products) == 6
    assert all(p["price_cents"] > 0 for p in products)


def test_new_visitor_gets_an_empty_cart(client: TestClient) -> None:
    response = client.get("/api/cart")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert "acme_sid" in response.cookies


def test_add_update_and_remove_items(client: TestClient, products: dict[str, dict]) -> None:
    kit, plant = products["pour-over-kit"], products["pothos-plant"]
    client.post("/api/cart/items", json={"product_id": kit["id"]})
    cart = client.post("/api/cart/items", json={"product_id": plant["id"], "quantity": 2}).json()
    assert cart["item_count"] == 3
    assert cart["subtotal_cents"] == kit["price_cents"] + 2 * plant["price_cents"]

    cart = client.patch(f"/api/cart/items/{kit['id']}", json={"quantity": 3}).json()
    assert cart["item_count"] == 5

    cart = client.delete(f"/api/cart/items/{plant['id']}").json()
    assert [line["product"]["slug"] for line in cart["items"]] == ["pour-over-kit"]
    assert cart["total_cents"] == 3 * kit["price_cents"]


def test_adding_the_same_product_twice_increments_quantity(client: TestClient, products: dict[str, dict]) -> None:
    kit = products["pour-over-kit"]
    client.post("/api/cart/items", json={"product_id": kit["id"]})
    cart = client.post("/api/cart/items", json={"product_id": kit["id"]}).json()
    assert len(cart["items"]) == 1
    assert cart["items"][0]["quantity"] == 2


def test_unknown_product(client: TestClient) -> None:
    assert client.post("/api/cart/items", json={"product_id": 999}).status_code == 404


def test_apply_and_remove_coupon(client: TestClient, products: dict[str, dict]) -> None:
    client.post("/api/cart/items", json={"product_id": products["over-ear-headphones"]["id"]})
    cart = client.post("/api/cart/coupon", json={"code": "welcome10"}).json()
    assert cart["coupon"]["code"] == "WELCOME10"
    assert cart["discount_cents"] == 890
    assert cart["total_cents"] == 8010

    cart = client.delete("/api/cart/coupon").json()
    assert cart["coupon"] is None
    assert cart["total_cents"] == 8900


def test_invalid_coupon(client: TestClient) -> None:
    response = client.post("/api/cart/coupon", json={"code": "FREESTUFF"})
    assert response.status_code == 400
    assert "not a valid code" in response.json()["detail"]


def test_coupon_minimum_spend(client: TestClient, products: dict[str, dict]) -> None:
    client.post("/api/cart/items", json={"product_id": products["cedar-candle"]["id"]})
    response = client.post("/api/cart/coupon", json={"code": "BIGSPENDER"})
    assert response.status_code == 400
