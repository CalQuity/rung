import type { Metadata } from "next";

import { CheckoutForm } from "@/components/checkout-form";
import { PageHeader } from "@/components/states";

export const metadata: Metadata = { title: "Checkout" };

export default function CheckoutPage() {
  return (
    <>
      <PageHeader title="Checkout" />
      <CheckoutForm />
    </>
  );
}
