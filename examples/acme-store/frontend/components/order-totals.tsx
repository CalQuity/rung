import { formatPrice } from "@/lib/format";

type Props = {
  subtotal: number;
  discount: number;
  total: number;
  couponCode?: string | null;
  totalTestId?: string;
};

export function OrderTotals({ subtotal, discount, total, couponCode, totalTestId }: Props) {
  return (
    <dl className="space-y-2 text-[13px]">
      <div className="flex justify-between">
        <dt className="text-muted-foreground">Subtotal</dt>
        <dd className="num">{formatPrice(subtotal)}</dd>
      </div>
      {discount !== 0 && (
        <div className="flex justify-between text-success">
          <dt>Discount{couponCode ? ` (${couponCode})` : ""}</dt>
          <dd className="num">−{formatPrice(discount)}</dd>
        </div>
      )}
      <div className="flex justify-between">
        <dt className="text-muted-foreground">Shipping</dt>
        <dd>Free</dd>
      </div>
      <div className="flex items-baseline justify-between border-t pt-3 text-[15px] font-semibold">
        <dt>Total</dt>
        <dd className="num text-lg" data-testid={totalTestId}>
          {formatPrice(total)}
        </dd>
      </div>
    </dl>
  );
}
