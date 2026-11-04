import { ProductGrid } from "@/components/product-grid";
import { PageHeader } from "@/components/states";

export default function HomePage() {
  return (
    <>
      <PageHeader title="Shop" description="Small goods for desk, kitchen and home. Free returns within 30 days." />
      <ProductGrid />
    </>
  );
}
