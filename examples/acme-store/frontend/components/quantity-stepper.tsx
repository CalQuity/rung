"use client";

import { RiAddLine, RiSubtractLine } from "@remixicon/react";

import { Button } from "@/components/ui/button";

type Props = {
  name: string;
  quantity: number;
  busy?: boolean;
  onChange: (quantity: number) => void;
};

export function QuantityStepper({ name, quantity, busy, onChange }: Props) {
  return (
    <div role="group" aria-label={`Quantity of ${name}`} className="inline-flex h-8 items-stretch border">
      <Button
        variant="ghost"
        size="icon-sm"
        className="h-full"
        aria-label={`Decrease quantity of ${name}`}
        disabled={busy || quantity <= 1}
        onClick={() => onChange(quantity - 1)}
      >
        <RiSubtractLine aria-hidden="true" />
      </Button>
      <output aria-live="polite" className="num grid w-9 place-items-center border-x text-[13px]">
        {quantity}
      </output>
      <Button
        variant="ghost"
        size="icon-sm"
        className="h-full"
        aria-label={`Increase quantity of ${name}`}
        disabled={busy || quantity >= 99}
        onClick={() => onChange(quantity + 1)}
      >
        <RiAddLine aria-hidden="true" />
      </Button>
    </div>
  );
}
