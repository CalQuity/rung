"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { Cart } from "@/lib/types";

type CartContextValue = {
  cart: Cart | null;
  error: string | null;
  reload: () => Promise<void>;
  /** Run a cart mutation and keep the returned cart. Throws the API error so callers can show it inline. */
  update: (mutation: () => Promise<Cart>) => Promise<Cart>;
  replace: (cart: Cart) => void;
};

const CartContext = createContext<CartContextValue | null>(null);

export function CartProvider({ children }: { children: React.ReactNode }) {
  const [cart, setCart] = useState<Cart | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setCart(await api.cart());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load your cart.");
    }
  }, []);

  useEffect(() => {
    let active = true;
    api
      .cart()
      .then((next) => {
        if (active) setCart(next);
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof ApiError ? err.message : "Could not load your cart.");
      });
    return () => {
      active = false;
    };
  }, []);

  const update = useCallback(async (mutation: () => Promise<Cart>) => {
    const next = await mutation();
    setCart(next);
    setError(null);
    return next;
  }, []);

  const value = useMemo(() => ({ cart, error, reload, update, replace: setCart }), [cart, error, reload, update]);
  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart(): CartContextValue {
  const value = useContext(CartContext);
  if (!value) throw new Error("useCart must be used inside <CartProvider>");
  return value;
}
