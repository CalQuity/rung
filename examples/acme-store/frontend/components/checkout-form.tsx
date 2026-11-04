"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { RiBankCardLine, RiLockLine } from "@remixicon/react";

import { useCart } from "@/components/cart-provider";
import { OrderTotals } from "@/components/order-totals";
import { ProductArt } from "@/components/product-art";
import { EmptyState, ErrorState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError } from "@/lib/api";
import { formatPrice } from "@/lib/format";

type Errors = Partial<Record<"name" | "email" | "card_number", string>>;

function formatCard(value: string): string {
  return value
    .replace(/\D/g, "")
    .slice(0, 16)
    .replace(/(\d{4})(?=\d)/g, "$1 ");
}

function validate(name: string, email: string, card: string): Errors {
  const errors: Errors = {};
  if (!name.trim()) errors.name = "Enter your name.";
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) errors.email = "Enter a valid email address.";
  if (card.replace(/\D/g, "").length !== 16) errors.card_number = "Enter all 16 digits.";
  return errors;
}

export function CheckoutForm() {
  const router = useRouter();
  const { cart, error: cartError, reload } = useCart();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [card, setCard] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function placeOrder(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = validate(name, email, card);
    setErrors(found);
    if (Object.keys(found).length > 0) return;

    setSubmitting(true);
    setError(null);
    try {
      const order = await api.checkout({ name, email, card_number: card });
      await reload();
      router.push(`/orders/${order.number}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We could not place your order. Please try again.");
      setSubmitting(false);
    }
  }

  if (cartError && !cart) return <ErrorState message={cartError} onRetry={reload} />;
  if (!cart) return <Skeleton className="h-96" />;
  if (cart.items.length === 0 && !submitting) {
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
      <form onSubmit={placeOrder} noValidate aria-label="Checkout" className="space-y-6">
        <fieldset className="border p-5 [&>legend+*]:clear-left [&>*:not(legend)+*]:mt-4">
          <legend className="float-left mb-4 w-full text-sm font-semibold">Contact</legend>
          <Field id="name" label="Full name" error={errors.name}>
            <Input id="name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} {...errorProps("name", errors)} />
          </Field>
          <Field id="email" label="Email" error={errors.email}>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              {...errorProps("email", errors)}
            />
          </Field>
        </fieldset>

        <fieldset className="border p-5 [&>legend+*]:clear-left [&>*:not(legend)+*]:mt-4">
          <legend className="float-left mb-4 w-full text-sm font-semibold">Payment</legend>
          <Field
            id="card_number"
            label="Card number"
            error={errors.card_number}
            hint="This is a demo store: any 16 digits work, for example 4242 4242 4242 4242."
          >
            <div className="relative">
              <RiBankCardLine className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
              <Input
                id="card_number"
                inputMode="numeric"
                autoComplete="cc-number"
                placeholder="1234 5678 9012 3456"
                className="num pl-8"
                value={card}
                onChange={(e) => setCard(formatCard(e.target.value))}
                {...errorProps("card_number", errors, true)}
              />
            </div>
          </Field>
        </fieldset>

        {error && <ErrorState message={error} />}

        <div className="flex flex-wrap items-center justify-between gap-4">
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <RiLockLine className="size-3.5" aria-hidden="true" /> No real card is charged.
          </p>
          <Button type="submit" size="lg" className="min-w-44">
            {submitting ? "Placing order…" : `Place order · ${formatPrice(cart.total_cents)}`}
          </Button>
        </div>
      </form>

      <aside aria-label="Order summary" className="border p-5 lg:sticky lg:top-20">
        <h2 className="text-sm font-semibold">Order summary</h2>
        <ul className="mt-4 space-y-3">
          {cart.items.map((line) => (
            <li key={line.product.id} className="flex items-center gap-3 text-[13px]">
              <ProductArt {...line.product} size="sm" className="size-10 shrink-0 [&_span]:text-lg" />
              <span className="min-w-0 flex-1 truncate">
                {line.product.name} <span className="num text-muted-foreground">× {line.quantity}</span>
              </span>
              <span className="num">{formatPrice(line.line_total_cents)}</span>
            </li>
          ))}
        </ul>
        <div className="mt-4 border-t pt-4">
          <OrderTotals
            subtotal={cart.subtotal_cents}
            discount={cart.discount_cents}
            total={cart.total_cents}
            couponCode={cart.coupon?.code}
          />
        </div>
      </aside>
    </div>
  );
}

function errorProps(field: keyof Errors, errors: Errors, hasHint = false) {
  if (errors[field]) return { "aria-invalid": true, "aria-describedby": `${field}-error` };
  return hasHint ? { "aria-describedby": `${field}-hint` } : {};
}

function Field({ id, label, error, hint, children }: { id: string; label: string; error?: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error ? (
        <p id={`${id}-error`} className="text-xs text-destructive">
          {error}
        </p>
      ) : (
        hint && (
          <p id={`${id}-hint`} className="text-xs text-muted-foreground">
            {hint}
          </p>
        )
      )}
    </div>
  );
}
