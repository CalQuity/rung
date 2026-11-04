"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { RiCheckboxCircleFill } from "@remixicon/react";

import { OrderTotals } from "@/components/order-totals";
import { ProductArt } from "@/components/product-art";
import { ErrorState } from "@/components/states";
import { buttonVariants } from "@/components/ui/button-variants";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError } from "@/lib/api";
import { formatDate, formatPrice } from "@/lib/format";
import type { Order } from "@/lib/types";
import { cn } from "@/lib/utils";

type State = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; order: Order };

export function OrderReceipt() {
  const { number } = useParams<{ number: string }>();
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let active = true;
    api
      .order(number)
      .then((order) => active && setState({ status: "ready", order }))
      .catch((err: unknown) => {
        if (active) setState({ status: "error", message: err instanceof ApiError ? err.message : "Could not load this order." });
      });
    return () => {
      active = false;
    };
  }, [number]);

  if (state.status === "loading") return <Skeleton className="mx-auto h-96 max-w-2xl" />;
  if (state.status === "error") return <ErrorState message={state.message} />;

  const { order } = state;
  const firstName = order.customer_name.split(" ")[0];

  return (
    <div className="mx-auto max-w-2xl">
      <div className="flex items-start gap-3">
        <RiCheckboxCircleFill className="mt-0.5 size-7 shrink-0 text-success" aria-hidden="true" />
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Thanks, {firstName}. Your order is confirmed.</h1>
          <p className="mt-1 text-[13px] text-muted-foreground">
            A receipt is on its way to <span className="text-foreground">{order.customer_email}</span>.
          </p>
        </div>
      </div>

      <section aria-label="Receipt" className="mt-8 border">
        <dl className="grid grid-cols-2 gap-px bg-border text-[13px] sm:grid-cols-3">
          <div className="bg-background p-4">
            <dt className="text-xs text-muted-foreground">Order number</dt>
            <dd className="num mt-1 text-[15px] font-semibold" data-testid="order-number">
              {order.number}
            </dd>
          </div>
          <div className="bg-background p-4">
            <dt className="text-xs text-muted-foreground">Placed</dt>
            <dd className="num mt-1">{formatDate(order.created_at)}</dd>
          </div>
          <div className="col-span-2 bg-background p-4 sm:col-span-1">
            <dt className="text-xs text-muted-foreground">Paid with</dt>
            <dd className="num mt-1">Card •••• {order.payment?.card_last4 ?? "----"}</dd>
          </div>
        </dl>
        <table className="w-full border-t text-[13px]">
          <caption className="sr-only">Items in order {order.number}</caption>
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th scope="col" className="px-4 py-2 font-normal">
                Item
              </th>
              <th scope="col" className="px-4 py-2 text-right font-normal">
                Qty
              </th>
              <th scope="col" className="px-4 py-2 text-right font-normal">
                Amount
              </th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {order.items.map((item) => (
              <tr key={item.product_id}>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-3">
                    <ProductArt emoji={item.emoji} color_from="#f4f2f4" color_to="#e7e3e8" size="sm" className="size-9 shrink-0 [&_span]:text-lg" />
                    <span>
                      {item.name}
                      <span className="num block text-xs text-muted-foreground">{formatPrice(item.unit_price_cents)} each</span>
                    </span>
                  </div>
                </td>
                <td className="num px-4 py-3 text-right">{item.quantity}</td>
                <td className="num px-4 py-3 text-right">{formatPrice(item.line_total_cents)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="border-t p-4 sm:ml-auto sm:max-w-xs">
          <OrderTotals
            subtotal={order.subtotal_cents}
            discount={order.discount_cents}
            total={order.total_cents}
            couponCode={order.coupon_code}
            totalTestId="order-total"
          />
        </div>
      </section>

      <div className="mt-6 flex justify-end gap-2">
        <Link href="/orders" className={cn(buttonVariants({ variant: "outline", size: "lg" }))}>
          View all orders
        </Link>
        <Link href="/" className={cn(buttonVariants({ size: "lg" }))}>
          Keep shopping
        </Link>
      </div>
    </div>
  );
}
