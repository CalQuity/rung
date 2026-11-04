import type { Product } from "@/lib/types";
import { cn } from "@/lib/utils";

type ArtProps = Pick<Product, "emoji" | "color_from" | "color_to"> & { className?: string; size?: "sm" | "lg" };

export function ProductArt({ emoji, color_from, color_to, className, size = "lg" }: ArtProps) {
  return (
    <div
      aria-hidden="true"
      className={cn("relative grid place-items-center overflow-hidden", className)}
      style={{ background: `linear-gradient(135deg, ${color_from} 0%, ${color_to} 140%)` }}
    >
      <div
        className="absolute inset-0 opacity-30"
        style={{ backgroundImage: "radial-gradient(circle at 30% 20%, white 0, transparent 55%)" }}
      />
      <span className={cn("relative drop-shadow-sm select-none", size === "lg" ? "text-6xl" : "text-4xl")}>{emoji}</span>
    </div>
  );
}
