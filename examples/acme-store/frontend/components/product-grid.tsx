"use client";

import { useEffect, useState } from "react";
import { RiAddLine, RiCheckLine } from "@remixicon/react";

import { useCart } from "@/components/cart-provider";
import { ProductArt } from "@/components/product-art";
import { ErrorState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError } from "@/lib/api";
import { formatPrice } from "@/lib/format";
import type { Product } from "@/lib/types";

type State = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; products: Product[] };

export function ProductGrid() {
  const [state, setState] = useState<State>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    api
      .products()
      .then((products) => active && setState({ status: "ready", products }))
      .catch((err: unknown) => {
        if (active) setState({ status: "error", message: err instanceof ApiError ? err.message : "Could not load products." });
      });
    return () => {
      active = false;
    };
  }, [attempt]);

  if (state.status === "error") {
    return (
      <ErrorState
        message={state.message}
        onRetry={() => {
          setState({ status: "loading" });
          setAttempt((n) => n + 1);
        }}
      />
    );
  }

  return (
    <section aria-label="Products" aria-busy={state.status === "loading"}>
      <ul className="grid gap-px border bg-border sm:grid-cols-2 lg:grid-cols-3">
        {state.status === "loading"
          ? Array.from({ length: 6 }, (_, i) => (
              <li key={i} className="bg-background p-4">
                <Skeleton className="aspect-[16/10] w-full" />
                <Skeleton className="mt-4 h-4 w-2/3" />
                <Skeleton className="mt-2 h-3 w-full" />
                <Skeleton className="mt-6 h-8 w-full" />
              </li>
            ))
          : state.products.map((product) => (
              <li key={product.id} className="bg-background">
                <ProductCard product={product} />
              </li>
            ))}
      </ul>
    </section>
  );
}

function ProductCard({ product }: { product: Product }) {
  const { update } = useCart();
  const [status, setStatus] = useState<"idle" | "adding" | "added" | "error">("idle");
  const titleId = `product-${product.id}-name`;

  async function add() {
    setStatus("adding");
    try {
      await update(() => api.addItem(product.id));
      setStatus("added");
      setTimeout(() => setStatus("idle"), 1500);
    } catch {
      setStatus("error");
    }
  }

  return (
    <article aria-labelledby={titleId} className="group flex h-full flex-col p-4">
      <ProductArt {...product} className="aspect-[16/10] w-full" />
      <div className="mt-4 flex flex-1 flex-col">
        <p className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{product.category}</p>
        <div className="mt-1 flex items-baseline justify-between gap-3">
          <h2 id={titleId} className="text-[15px] font-semibold tracking-tight">
            {product.name}
          </h2>
          <p className="num text-[15px] font-medium">{formatPrice(product.price_cents)}</p>
        </div>
        <p className="mt-1.5 flex-1 text-[13px] leading-relaxed text-muted-foreground">{product.description}</p>
        <Button className="mt-4 w-full" size="lg" onClick={add} disabled={status === "adding"}>
          {status === "added" ? <RiCheckLine aria-hidden="true" /> : <RiAddLine aria-hidden="true" />}
          Add to cart
        </Button>
        <p role="status" className="sr-only">
          {status === "added" ? `${product.name} added to your cart` : ""}
        </p>
        {status === "error" && (
          <p role="alert" className="mt-2 text-xs text-destructive">
            Could not add this item. Try again.
          </p>
        )}
      </div>
    </article>
  );
}
