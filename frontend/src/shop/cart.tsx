import React, { createContext, useCallback, useContext, useMemo, useState } from "react";

export type CartItem = { productId: string; qty: number };

type CartValue = {
  items: CartItem[];
  count: number;
  add: (productId: string, qty?: number) => void;
  setQty: (productId: string, qty: number) => void;
  remove: (productId: string) => void;
  clear: () => void;
};

const CartContext = createContext<CartValue | null>(null);

export function CartProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<CartItem[]>([]);

  const add = useCallback((productId: string, qty = 1) => {
    setItems((c) => {
      const ex = c.find((i) => i.productId === productId);
      if (ex) return c.map((i) => (i.productId === productId ? { ...i, qty: i.qty + qty } : i));
      return [...c, { productId, qty }];
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
