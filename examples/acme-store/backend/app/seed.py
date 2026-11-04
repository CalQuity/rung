"""The starting catalog."""

import sqlite3

PRODUCTS = [
    {
        "slug": "pour-over-kit",
        "name": "Pour-Over Coffee Kit",
        "description": "Ceramic dripper, glass carafe and 100 filters. Brews two cups.",
        "category": "Kitchen",
        "price_cents": 3400,
        "emoji": "☕",
        "color_from": "#fef3c7",
        "color_to": "#fcd34d",
    },
    {
        "slug": "pothos-plant",
        "name": "Golden Pothos",
        "description": 'Easy-going trailing plant in a 5" matte ceramic pot.',
        "category": "Home",
        "price_cents": 2200,
        "emoji": "🪴",
        "color_from": "#dcfce7",
        "color_to": "#86efac",
    },
    {
        "slug": "over-ear-headphones",
        "name": "Over-Ear Headphones",
        "description": "Active noise cancelling, 30-hour battery, folds flat.",
        "category": "Audio",
        "price_cents": 8900,
        "emoji": "🎧",
        "color_from": "#e0e7ff",
        "color_to": "#a5b4fc",
    },
    {
        "slug": "canvas-weekender",
        "name": "Canvas Weekender",
        "description": "Waxed canvas duffel with leather handles. Fits carry-on limits.",
        "category": "Travel",
        "price_cents": 12800,
        "emoji": "👜",
        "color_from": "#ffedd5",
        "color_to": "#fdba74",
    },
    {
        "slug": "cedar-candle",
        "name": "Cedar & Smoke Candle",
        "description": "Soy wax, cotton wick, about 45 hours of burn time.",
        "category": "Home",
        "price_cents": 1800,
        "emoji": "🕯️",
        "color_from": "#ffe4e6",
        "color_to": "#fda4af",
    },
    {
        "slug": "mechanical-keyboard",
        "name": "Low-Profile Keyboard",
        "description": "75% mechanical keyboard, hot-swappable tactile switches.",
        "category": "Desk",
        "price_cents": 14900,
        "emoji": "⌨️",
        "color_from": "#f1f5f9",
        "color_to": "#cbd5e1",
    },
]


def seed_products(conn: sqlite3.Connection) -> None:
    """Insert the catalog unless products already exist."""
    (count,) = conn.execute("SELECT COUNT(*) FROM products").fetchone()
    if count:
        return
    conn.executemany(
        """
        INSERT INTO products (slug, name, description, category, price_cents, emoji, color_from, color_to)
        VALUES (:slug, :name, :description, :category, :price_cents, :emoji, :color_from, :color_to)
        """,
        PRODUCTS,
    )
