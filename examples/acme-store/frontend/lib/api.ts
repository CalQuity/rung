import type { Cart, CheckoutDetails, Order, Product } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

function detailMessage(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: unknown };
    if (typeof first.msg === "string") return first.msg;
  }
  return null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw new ApiError("Can't reach the store right now. Check your connection and try again.", 0);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const message = detailMessage(body?.detail) ?? "Something went wrong. Please try again.";
    throw new ApiError(message, response.status);
  }
  return (await response.json()) as T;
}

export const api = {
  products: () => request<Product[]>("/products"),
  cart: () => request<Cart>("/cart"),
  addItem: (productId: number, quantity = 1) =>
    request<Cart>("/cart/items", {
      method: "POST",
      body: JSON.stringify({ product_id: productId, quantity }),
    }),
  updateItem: (productId: number, quantity: number) =>
    request<Cart>(`/cart/items/${productId}`, {
      method: "PATCH",
      body: JSON.stringify({ quantity }),
    }),
  removeItem: (productId: number) => request<Cart>(`/cart/items/${productId}`, { method: "DELETE" }),
  applyCoupon: (code: string) =>
    request<Cart>("/cart/coupon", { method: "POST", body: JSON.stringify({ code }) }),
  removeCoupon: () => request<Cart>("/cart/coupon", { method: "DELETE" }),
  checkout: (details: CheckoutDetails) =>
    request<Order>("/checkout", { method: "POST", body: JSON.stringify(details) }),
  orders: () => request<Order[]>("/orders"),
  order: (number: string) => request<Order>(`/orders/${encodeURIComponent(number)}`),
};
