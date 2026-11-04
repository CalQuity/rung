"use client";

import Link from "next/link";
import { useState } from "react";
import { RiArrowRightLine, RiDeleteBinLine } from "@remixicon/react";

import { useCart } from "@/components/cart-provider";
import { CouponForm } from "@/components/coupon-form";
import { OrderTotals } from "@/components/order-totals";
import { ProductArt } from "@/components/product-art";
import { QuantityStepper } from "@/components/quantity-stepper";
import { EmptyState, ErrorState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { buttonVariants } from "@/components/ui/button-variants";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError } from "@/lib/api";
import { formatPrice } from "@/lib/format";
import type { CartLine } from "@/lib/types";
import { cn } from "@/lib/utils";

export function CartView() {
  const { cart, error, reload } = useCart();

  if (error && !cart) return <ErrorState message={error} onRetry={reload} />;
  if (!cart) {
    return (
      <div aria-busy="true" className="grid gap-8 lg:grid-cols-[1fr_360px]">
        <Skeleton className="h-64" />
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (cart.items.length === 0) {
    return (
      <EmptyState>
        Your cart is empty.{" "}
        <Link href="/" className="font-medium text-primary underline-offset-4 hover:underline">
          Browse the shop
        </Link>
      </EmptyState>
    );
  }

  return (
    <div className="grid items-start gap-8 lg:grid-cols-[1fr_360px]">
      <ul aria-label="Cart items" className="divide-y border-y">
        {cart.items.map((line) => (
          <CartLineRow key={line.product.id} line={line} />
        ))}
      </ul>
      <aside aria-label="Order summary" className="border p-5 lg:sticky lg:top-20">
        <h2 className="text-sm font-semibold">Order summary</h2>
        <div className="mt-4">
          <CouponForm cart={cart} />
        </div>
        <div className="mt-5 border-t pt-4">
          <OrderTotals
            subtotal={cart.subtotal_cents}
            discount={cart.discount_cents}
            total={cart.total_cents}
            couponCode={cart.coupon?.code}
            totalTestId="cart-total"
          />
        </div>
        <Link href="/checkout" className={cn(buttonVariants({ size: "lg" }), "mt-5 w-full")}>
          Checkout <RiArrowRightLine aria-hidden="true" />
        </Link>
      </aside>
    </div>
  );
}

function CartLineRow({ line }: { line: CartLine }) {
  const { update } = useCart();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { product } = line;

  async function run(mutation: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await mutation();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="flex gap-4 py-4">
      <ProductArt {...product} size="sm" className="size-20 shrink-0" />
      <div className="flex min-w-0 flex-1 flex-col gap-3 sm:flex-row sm:items-center">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-medium">{product.name}</h3>
          <p className="num mt-0.5 text-xs text-muted-foreground">{formatPrice(product.price_cents)} each</p>
          {error && (
            <p role="alert" className="mt-1 text-xs text-destructive">
              {error}
            </p>
          )}
        </div>
        <div className="flex items-center gap-3">
          <QuantityStepper
            name={product.name}
            quantity={line.quantity}
            busy={busy}
            onChange={(quantity) => run(() => update(() => api.updateItem(product.id, quantity)))}
          />
          <p className="num w-20 text-right text-sm font-medium">{formatPrice(line.line_total_cents)}</p>
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Remove ${product.name}`}
            disabled={busy}
            onClick={() => run(() => update(() => api.removeItem(product.id)))}
          >
            <RiDeleteBinLine aria-hidden="true" />
          </Button>
        </div>
      </div>
    </li>
  );
}
