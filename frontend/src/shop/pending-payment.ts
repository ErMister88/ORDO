import { storage } from "@/src/utils/storage";

const PENDING_SHOP_PAYMENT_KEY = "ordo_pending_shop_payment";
const PENDING_SHOP_PAYMENT_TTL_MS = 30 * 24 * 60 * 60 * 1000;

export type PendingShopPayment = {
  orderId: string;
  orderToken: string;
  total: number;
  createdAt: number;
};

export async function savePendingShopPayment(value: PendingShopPayment): Promise<boolean> {
  return storage.secureSet(PENDING_SHOP_PAYMENT_KEY, JSON.stringify(value));
}

export async function loadPendingShopPayment(): Promise<PendingShopPayment | null> {
  const raw = await storage.secureGet<string>(PENDING_SHOP_PAYMENT_KEY, "");
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as PendingShopPayment;
    if (
      typeof value.orderId === "string"
      && value.orderId.length > 0
      && typeof value.orderToken === "string"
      && value.orderToken.length > 0
      && typeof value.total === "number"
      && Number.isFinite(value.total)
      && typeof value.createdAt === "number"
      && value.createdAt <= Date.now()
      && Date.now() - value.createdAt <= PENDING_SHOP_PAYMENT_TTL_MS
    ) {
      return value;
    }
  } catch {
    // Invalid local recovery state is removed and never sent to the API.
  }
  await clearPendingShopPayment();
  return null;
}

export async function clearPendingShopPayment(): Promise<void> {
  await storage.secureRemove(PENDING_SHOP_PAYMENT_KEY);
}
