from fastapi import APIRouter, HTTPException

from app.deps import Db
from app.schemas import Product

router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("")
def list_products(conn: Db) -> list[Product]:
    rows = conn.execute("SELECT * FROM products ORDER BY id").fetchall()
    return [Product(**dict(row)) for row in rows]


@router.get("/{slug}")
def get_product(slug: str, conn: Db) -> Product:
    row = conn.execute("SELECT * FROM products WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise HTTPException(404, "Product not found")
    return Product(**dict(row))
