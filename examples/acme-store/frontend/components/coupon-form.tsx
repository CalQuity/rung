"use client";

import { useState } from "react";
import { RiCheckboxCircleLine, RiCloseLine, RiCoupon3Line, RiErrorWarningLine } from "@remixicon/react";

import { useCart } from "@/components/cart-provider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api";
import type { Cart } from "@/lib/types";

type Message = { kind: "success" | "error"; text: string } | null;

export function CouponForm({ cart }: { cart: Cart }) {
  const { update } = useCart();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message>(null);

  async function apply(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!code.trim()) {
      setMessage({ kind: "error", text: "Enter a coupon code." });
      return;
    }
    setBusy(true);
    try {
      const next = await update(() => api.applyCoupon(code));
      setMessage({ kind: "success", text: `${next.coupon?.code} applied: ${next.coupon?.description}.` });
      setCode("");
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof ApiError ? err.message : "Could not apply that code." });
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await update(() => api.removeCoupon());
      setMessage(null);
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof ApiError ? err.message : "Could not remove the code." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      {cart.coupon ? (
        <div className="flex h-9 items-center gap-2 border bg-muted/50 pr-1 pl-2.5 text-[13px]">
          <RiCoupon3Line className="size-4 text-muted-foreground" aria-hidden="true" />
          <span className="num font-medium">{cart.coupon.code}</span>
          <span className="truncate text-muted-foreground">{cart.coupon.description}</span>
          <Button variant="ghost" size="icon-sm" className="ml-auto" aria-label="Remove coupon" disabled={busy} onClick={remove}>
            <RiCloseLine aria-hidden="true" />
          </Button>
        </div>
      ) : (
        <form onSubmit={apply} className="space-y-1.5" noValidate>
          <Label htmlFor="coupon-code">Coupon code</Label>
          <div className="flex gap-2">
            <Input
              id="coupon-code"
              name="coupon"
              autoComplete="off"
              placeholder="e.g. WELCOME10"
              value={code}
              aria-invalid={message?.kind === "error" || undefined}
              aria-describedby={message ? "coupon-message" : undefined}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              className="num uppercase"
            />
            <Button type="submit" variant="outline" className="h-9" disabled={busy}>
              {busy ? "Applying…" : "Apply"}
            </Button>
          </div>
        </form>
      )}
      {message && (
        <p
          id="coupon-message"
          role={message.kind === "error" ? "alert" : "status"}
          className={`flex items-center gap-1.5 text-xs ${message.kind === "error" ? "text-destructive" : "text-success"}`}
        >
          {message.kind === "error" ? (
            <RiErrorWarningLine className="size-3.5 shrink-0" aria-hidden="true" />
          ) : (
            <RiCheckboxCircleLine className="size-3.5 shrink-0" aria-hidden="true" />
          )}
          {message.text}
        </p>
      )}
    </div>
  );
}
