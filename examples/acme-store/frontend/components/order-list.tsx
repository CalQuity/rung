"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { RiArrowRightSLine } from "@remixicon/react";

import { EmptyState, ErrorState } from "@/components/states";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError } from "@/lib/api";
import { formatDate, formatPrice } from "@/lib/format";
import type { Order } from "@/lib/types";

type State = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; orders: Order[] };

export function OrderList() {
  const [state, setState] = useState<State>({ status: "loading" });

  const load = useCallback(() => {
    let active = true;
    api
      .orders()
      .then((orders) => active && setState({ status: "ready", orders }))
      .catch((err: unknown) => {
        if (active) setState({ status: "error", message: err instanceof ApiError ? err.message : "Could not load your orders." });
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => load(), [load]);

  if (state.status === "loading") {
    return (
      <div aria-busy="true" className="space-y-px">
        <Skeleton className="h-16" />
        <Skeleton className="h-16" />
      </div>
    );
  }
  if (state.status === "error") return <ErrorState message={state.message} onRetry={load} />;
  if (state.orders.length === 0) {
    return (
      <EmptyState>
        No orders yet.{" "}
        <Link href="/" className="font-medium text-primary underline-offset-4 hover:underline">
          Start shopping
        </Link>
      </EmptyState>
    );
  }

  return (
    <ul aria-label="Orders" className="divide-y border-y">
      {state.orders.map((order) => {
        const count = order.items.reduce((sum, item) => sum + item.quantity, 0);
        return (
          <li key={order.number}>
            <Link href={`/orders/${order.number}`} className="flex items-center gap-4 px-2 py-4 transition-colors hover:bg-muted/50">
              <div className="flex -space-x-1" aria-hidden="true">
                {order.items.slice(0, 3).map((item) => (
                  <span key={item.product_id} className="grid size-9 place-items-center border bg-muted text-lg">
                    {item.emoji}
                  </span>
                ))}
              </div>
              <div className="min-w-0 flex-1">
                <p className="num text-[13px] font-semibold">{order.number}</p>
                <p className="truncate text-xs text-muted-foreground">
                  {formatDate(order.created_at)} · {count} {count === 1 ? "item" : "items"} · {order.items.map((i) => i.name).join(", ")}
                </p>
              </div>
              <p className="num text-sm font-medium">{formatPrice(order.total_cents)}</p>
              <RiArrowRightSLine className="size-4 text-muted-foreground" aria-hidden="true" />
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
