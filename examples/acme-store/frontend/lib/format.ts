const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

export function formatPrice(cents: number): string {
  return usd.format(cents / 100);
}

/** SQLite timestamps are UTC without a zone: "2026-11-04 12:30:00". */
export function formatDate(timestamp: string): string {
  const date = new Date(`${timestamp.replace(" ", "T")}Z`);
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}
