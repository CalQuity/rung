from app.pricing import COUPONS, cart_totals, coupon_error, discount_cents, find_coupon, line_total_cents


def test_line_total_multiplies_price_by_quantity() -> None:
    assert line_total_cents(1999, 3) == 5997


def test_totals_without_coupon() -> None:
    totals = cart_totals([(3400, 1), (2200, 2)])
    assert totals.subtotal_cents == 7800
    assert totals.discount_cents == 0
    assert totals.total_cents == 7800


def test_percent_coupon_rounds_down_to_the_cent() -> None:
    assert discount_cents(COUPONS["WELCOME10"], 3405) == 340


def test_welcome10_takes_ten_percent_off() -> None:
    totals = cart_totals([(3400, 1), (8900, 1)], COUPONS["WELCOME10"])
    assert totals.subtotal_cents == 12300
    assert totals.discount_cents == 1230
    assert totals.total_cents == 11070


def test_fixed_coupon_on_a_large_cart() -> None:
    totals = cart_totals([(12800, 1)], COUPONS["FRIENDS50"])
    assert totals.discount_cents == 5000
    assert totals.total_cents == 7800


def test_find_coupon_ignores_case_and_spaces() -> None:
    assert find_coupon("  welcome10 ") == COUPONS["WELCOME10"]
    assert find_coupon("NOPE") is None


def test_minimum_spend() -> None:
    coupon = COUPONS["BIGSPENDER"]
    assert coupon_error(coupon, 19999) is not None
    assert coupon_error(coupon, 20000) is None
