# Money and snapshot rollout

## New writes

New commercial writes persist authoritative amounts as integer minor units and
an explicit uppercase ISO currency. Existing major-unit number fields remain as
API compatibility fields during the transition. Item snapshots carry product
label, SKU when available, description, unit, quantity, unit price, line total,
tax rate, cost basis when available, price source, currency, and attribution.

## Migration 4

Migration 4 is additive. It derives minor-unit amounts only from values already
embedded in the same tenant-scoped document and uses that tenant's configured
default currency. It does not fetch current products, prices, costs, customers,
or tax rules to fill historical facts. It does not mark legacy lines as complete
immutable snapshots. Its dry-run plan reports tenantless legacy documents by
collection; those documents remain unchanged and require the separately
reviewed tenant backfill before a money expansion can be applied to them.

The migration follows the existing guarded migration runner and has not been
executed against staging or production as part of this work package.

## Rollout phases

1. **Expand:** deploy dual-read/dual-write code and Migration 4.
2. **Backfill:** run Migration 4 only after backup and dry-run review.
3. **Verify:** compare major/minor amounts and report legacy documents that lack
   complete descriptive snapshots.
4. **Switch:** use minor-unit fields as authoritative; this is already true for
   new writes.
5. **Enforce:** add schema validation only after all legacy gaps are resolved.
6. **Contract:** remove compatibility major-unit fields in a separately reviewed
   future package.

Legacy offers and subscriptions without complete item snapshots are rejected
when an operation would create a new binding order. Missing historical names,
tax rates, or costs must be resolved by an explicit owner-approved process; the
application does not substitute current mutable product data.
