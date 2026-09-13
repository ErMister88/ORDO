export type Tier = { minQty: number; price: number };

/** Returns the highest tier whose minQty is met by qty, or null. */
export function applicableTier(tiers: Tier[] | undefined | null, qty: number): Tier | null {
  if (!tiers?.length || !qty) return null;
  const sorted = [...tiers].sort((a, b) => a.minQty - b.minQty);
  let match: Tier | null = null;
  for (const t of sorted) {
    if (qty >= t.minQty) match = t;
  }
  return match;
}
