import type { Metadata } from "next";
import { Suspense } from "react";

import { OrderReceipt } from "@/components/order-receipt";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Order confirmed" };

export default function OrderPage() {
  return (
    <Suspense fallback={<Skeleton className="mx-auto h-96 max-w-2xl" />}>
      <OrderReceipt />
    </Suspense>
  );
}
