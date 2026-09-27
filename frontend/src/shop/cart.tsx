import React, { createContext, useCallback, useContext, useMemo, useState } from "react";

export type CartProductSnapshot = { id: string; slug?: string; name: string; brand?: string; imageUrl?: string; unit?: string; subscriptionAllowed?: boolean };
export type CartItem = { productId: string; qty: number; variantId?: string; variantName?: string; product?: CartProductSnapshot };

type CartValue = {
  items: CartItem[];
  count: number;
  add: (productId: string, qty?: number, options?: Pick<CartItem, "variantId" | "variantName" | "product">) => void;
  setQty: (productId: string, qty: number) => void;
  remove: (productId: string) => void;
  clear: () => void;
};

const CartContext = createContext<CartValue | null>(null);

export function CartProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<CartItem[]>([]);

  const add = useCallback((productId: string, qty = 1, options: Pick<CartItem, "variantId" | "variantName" | "product"> = {}) => {
    setItems((c) => {
      const ex = c.find((i) => i.productId === productId);
      if (ex) return c.map((i) => {
        if (i.productId !== productId) return i;
        const replacingVariant = Boolean(options.variantId) && options.variantId !== i.variantId;
        return { ...i, ...options, qty: replacingVariant ? qty : i.qty + qty };
      });
      return [...c, { productId, qty, ...options }];
    });
  }, []);
  const setQty = useCallback((productId: string, qty: number) => {
    setItems((c) => c.map((i) => (i.productId === productId ? { ...i, qty: Math.max(1, qty) } : i)));
  }, []);
  const remove = useCallback((productId: string) => {
    setItems((c) => c.filter((i) => i.productId !== productId));
  }, []);
  const clear = useCallback(() => setItems([]), []);

  const value = useMemo(
    () => ({ items, count: items.reduce((a, i) => a + i.qty, 0), add, setQty, remove, clear }),
    [items, add, setQty, remove, clear],
  );
  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart() {
  const ctx = useContext(CartContext);
  if (!ctx) throw new Error("useCart must be used within CartProvider");
  return ctx;
}
