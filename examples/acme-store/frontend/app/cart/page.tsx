import type { Metadata } from "next";

import { CartView } from "@/components/cart-view";
import { PageHeader } from "@/components/states";

export const metadata: Metadata = { title: "Cart" };

export default function CartPage() {
  return (
    <>
      <PageHeader title="Your cart" />
      <CartView />
    </>
  );
}
