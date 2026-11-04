"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { RiShoppingBag3Line } from "@remixicon/react";

import { useCart } from "@/components/cart-provider";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Shop" },
  { href: "/orders", label: "Orders" },
];

export function SiteHeader() {
  const pathname = usePathname();
  const { cart } = useCart();
  const count = cart?.item_count ?? 0;

  return (
    <header className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 sm:gap-6 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2" aria-label="Acme Store home">
          <span className="grid size-7 place-items-center bg-primary text-sm font-semibold text-primary-foreground">A</span>
          <span className="text-[15px] font-semibold tracking-tight whitespace-nowrap">Acme Store</span>
        </Link>
        <nav aria-label="Main" className="flex items-center gap-1">
          {NAV.map((item) => {
            const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex h-8 items-center px-2.5 text-[13px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                  active && "text-foreground font-medium shadow-[inset_0_-2px_0_var(--primary)]",
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <Link
          href="/cart"
          aria-label={`Cart, ${count} ${count === 1 ? "item" : "items"}`}
          className="ml-auto flex h-8 items-center gap-2 border px-2.5 sm:px-3 text-[13px] font-medium transition-colors hover:bg-muted"
        >
          <RiShoppingBag3Line className="size-4" aria-hidden="true" />
          Cart
          <span className={cn("num min-w-5 px-1 text-center text-xs", count ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
            {count}
          </span>
        </Link>
      </div>
    </header>
  );
}
