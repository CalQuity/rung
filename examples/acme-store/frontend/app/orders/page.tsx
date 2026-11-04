import type { Metadata } from "next";

import { OrderList } from "@/components/order-list";
import { PageHeader } from "@/components/states";

export const metadata: Metadata = { title: "Orders" };

export default function OrdersPage() {
  return (
    <>
      <PageHeader title="Orders" description="Orders placed from this browser." />
      <OrderList />
    </>
  );
}
