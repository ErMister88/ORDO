import { useEffect, useMemo, useState } from "react";
import {
  View,
  ScrollView,
  Pressable,
  KeyboardAvoidingView,
  Platform,
} from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as WebBrowser from "expo-web-browser";
import { ArrowLeft, Minus, Plus, Trash, CheckCircle, CheckSquare, Square } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiGet, apiPost, apiPostIdempotent } from "@/src/api/client";
import { euro } from "@/src/lib/format";
import { useCart } from "@/src/shop/cart";
import { shopApi } from "@/src/shop/auth";
import {
  clearPendingShopPayment,
  loadPendingShopPayment,
  PendingShopPayment,
  savePendingShopPayment,
} from "@/src/shop/pending-payment";
import { Card, Input, Button, SectionTitle, Muted } from "@/src/components/ui";
import { LocalizedText as Text, localizedAlert, useI18n } from "@/src/i18n";

export default function Warenkorb() {
  const { tf, language } = useI18n();
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const cart = useCart();

  const products = useQuery({ queryKey: ["shop-products"], queryFn: () => apiGet("/shop/products") });

  const [form, setForm] = useState({ name: "", email: "", phone: "", street: "", zip: "", city: "" });
  const set = (k: string) => (v: string) => setForm((f) => ({ ...f, [k]: v }));
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<null | { id: string; total: number; paid: boolean }>(null);
  const [pendingPayment, setPendingPayment] = useState<PendingShopPayment | null>(null);
  const [promoInput, setPromoInput] = useState("");
  const [promo, setPromo] = useState<null | { code: string; percent: number }>(null);
  const [promoMsg, setPromoMsg] = useState("");
  const [promoBusy, setPromoBusy] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [subscription, setSubscription] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const me = await shopApi.me();
        const a = me?.address;
        if (a && (a.street || a.name)) {
          setForm((f) => ({
            name: f.name || a.name || me.name || "",
            email: f.email || me.email || "",
            phone: f.phone || a.phone || "",
            street: f.street || a.street || "",
            zip: f.zip || a.zip || "",
            city: f.city || a.city || "",
          }));
        } else if (me?.email) {
          setForm((f) => ({ ...f, name: f.name || me.name || "", email: f.email || me.email }));
        }
      } catch {
        /* guest checkout – no prefill */
      }
    })();
  }, []);

  useEffect(() => {
    loadPendingShopPayment().then((pending) => {
      if (pending) {
        setPendingPayment(pending);
        setDone({ id: pending.orderId, total: pending.total, paid: false });
      }
    });
  }, []);

  const applyPromo = async () => {
    setPromoMsg("");
    const code = promoInput.trim();
    if (!code) return;
    setPromoBusy(true);
    try {
      const res = await shopApi.validateCode(code);
      if (res.valid) {
        setPromo({ code: code.toUpperCase(), percent: res.percent });
        setPromoMsg(tf("{percent}% Rabatt aktiviert", { percent: res.percent }));
      } else {
        setPromo(null);
        setPromoMsg(res.detail || "Code ungültig");
      }
    } catch (e: any) {
      setPromo(null);
      setPromoMsg(e.message || "Code konnte nicht geprüft werden");
    } finally {
      setPromoBusy(false);
    }
  };

  const pMap: Record<string, any> = {};
  (products.data ?? []).forEach((p: any) => (pMap[p.id] = p));

  const lines = useMemo(
    () => cart.items.map((i) => ({ ...i, p: pMap[i.productId] })).filter((l) => l.p),
    [cart.items, products.data],
  );
  const quote = useQuery({
    queryKey: ["shop-quote", cart.items, promo?.code ?? "", subscription],
    queryFn: () => apiPost("/shop/quote", {
      items: cart.items.map((i) => ({ productId: i.productId, qty: i.qty })),
      promoCode: promo?.code, subscription,
    }),
    enabled: cart.items.length > 0,
  });
  const subtotal = quote.data?.merchandise ?? 0;
  const discountPercent = quote.data?.discountPercent ?? 0;
  const discount = quote.data?.discount ?? 0;
  const discounted = quote.data?.subtotal ?? 0;
  const threshold = quote.data?.freeShippingThreshold ?? 0;
  const shipping = quote.data?.shipping ?? 0;
  const total = quote.data?.total ?? 0;
  const vatByRate = quote.data?.taxBreakdown ?? {};
  const quoteLines: Record<string, any> = {};
  (quote.data?.lines ?? []).forEach((line: any) => { quoteLines[line.productId] = line; });

  const continuePayment = async (pending: PendingShopPayment) => {
    const orderHeaders = { "X-Order-Token": pending.orderToken };
    const res = await apiPostIdempotent(`/shop/orders/${pending.orderId}/checkout`, {}, orderHeaders);
    let paid = false;
    if (res?.url) {
      await WebBrowser.openBrowserAsync(res.url);
      for (let i = 0; i < 8; i++) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        const status = await apiGet(`/shop/orders/${pending.orderId}/payment-status`, orderHeaders);
        if (status.status === "Bezahlt") {
          paid = true;
          break;
        }
      }
    }
    if (paid) {
      await clearPendingShopPayment();
      setPendingPayment(null);
    }
    setDone({ id: pending.orderId, total: pending.total, paid });
    return paid;
  };

  const retryPendingPayment = async () => {
    if (!pendingPayment) return;
    setBusy(true);
    try {
      const paid = await continuePayment(pendingPayment);
      if (!paid) {
        localizedAlert("Zahlung wird bestätigt", "ORDO wartet noch auf die sichere Bestätigung des Zahlungsdienstes.");
      }
    } catch (error) {
      localizedAlert("Zahlung nicht möglich", error instanceof Error ? error.message : "Bitte versuche es erneut.");
    } finally {
      setBusy(false);
    }
  };

  const checkout = async () => {
    if (!form.name.trim() || !form.email.includes("@")) {
      localizedAlert("Angaben fehlen", "Bitte Name und gültige E-Mail angeben.");
      return;
    }
    if (!accepted) {
      localizedAlert("Bestätigung nötig", "Bitte akzeptiere AGB und Widerrufsbelehrung.");
      return;
    }
    setBusy(true);
    try {
      const order = await shopApi.createOrder({
        items: cart.items.map((i) => ({ productId: i.productId, qty: i.qty })),
        customer: form,
        promoCode: promo?.code,
        subscription,
      });
      const pending = {
        orderId: order.id,
        orderToken: order.token,
        total: order.total,
        createdAt: Date.now(),
      } satisfies PendingShopPayment;
      const recoverySaved = await savePendingShopPayment(pending);
      setPendingPayment(pending);
      cart.clear();
      if (!recoverySaved) {
        localizedAlert(
          "Zahlung nur in dieser Sitzung fortsetzbar",
          "Der lokale Speicher ist nicht verfügbar. Bitte diese Seite bis zum Abschluss der Zahlung geöffnet lassen.",
        );
      }
      try {
        const paid = await continuePayment(pending);
        if (!paid) {
          localizedAlert("Zahlung wird bestätigt", "ORDO wartet noch auf die sichere Bestätigung des Zahlungsdienstes.");
        }
      } catch (error) {
        localizedAlert(
          "Bestellung erfasst",
          error instanceof Error
            ? `Deine Bestellung wurde angelegt. Die Zahlung kann sicher fortgesetzt werden: ${error.message}`
            : "Deine Bestellung wurde angelegt. Die Zahlung kann sicher fortgesetzt werden.",
        );
        setDone({ id: pending.orderId, total: pending.total, paid: false });
      }
    } catch (e: any) {
      localizedAlert("Fehler", e.message || "Bestellung fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 8 }]}>
        <Pressable onPress={() => router.back()} style={styles.iconBtn} testID="cart-back" hitSlop={8}>
          <ArrowLeft size={20} color={colors.onSurfaceSecondary} weight="bold" />
        </Pressable>
        <Text style={styles.title}>Warenkorb</Text>
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          {done ? (
            <Card testID="shop-done">
              <CheckCircle size={40} color={colors.success} weight="fill" />
              <Text style={styles.doneTitle}>Danke für deine Bestellung!</Text>
              <Muted>
                {tf("Bestellnummer {id} · Summe {total} · {status}", {
                  id: done.id,
                  total: euro(done.total),
                  status: done.paid ? tf("bezahlt", {}) : tf("Zahlung wird sicher bestätigt", {}),
                })}
              </Muted>
              {!done.paid && pendingPayment ? (
                <Button title="Zahlung fortsetzen" loading={busy} onPress={retryPendingPayment} style={{ marginTop: 12 }} />
              ) : null}
              <Button title="Zurück zum Shop" onPress={() => router.replace("/shop")} style={{ marginTop: 12 }} />
            </Card>
          ) : lines.length === 0 ? (
            <Card>
              <Muted>Dein Warenkorb ist leer.</Muted>
              <Button title="Zum Shop" kind="secondary" onPress={() => router.replace("/shop")} style={{ marginTop: 10 }} />
            </Card>
          ) : (
            <>
              <Card>
                {lines.map((l) => (
                  <View key={l.productId} style={styles.line} testID={`cart-line-${l.productId}`}>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.lName} numberOfLines={1}>
                        {l.p.brand} {l.p.name}
                      </Text>
                      <Muted>{tf("{price} /{unit} inkl. MwSt.", { price: euro(quoteLines[l.productId]?.finalUnitPrice ?? l.p.b2cPrice), unit: l.p.unit })}</Muted>
                    </View>
                    <Pressable testID={`cart-minus-${l.productId}`} style={styles.step} onPress={() => cart.setQty(l.productId, l.qty - 1)} hitSlop={6}>
                      <Minus size={14} color={colors.onSurface} weight="bold" />
                    </Pressable>
                    <Text style={styles.qty}>{l.qty}</Text>
                    <Pressable testID={`cart-plus-${l.productId}`} style={styles.step} onPress={() => cart.setQty(l.productId, l.qty + 1)} hitSlop={6}>
                      <Plus size={14} color={colors.onSurface} weight="bold" />
                    </Pressable>
                    <Pressable testID={`cart-del-${l.productId}`} onPress={() => cart.remove(l.productId)} hitSlop={6} style={{ marginLeft: 8 }}>
                      <Trash size={16} color={colors.error} weight="bold" />
                    </Pressable>
                  </View>
                ))}
                <View style={styles.sumRow}>
                  <Text style={styles.sumLabel}>Zwischensumme</Text>
                  <Text style={styles.sumVal}>{euro(subtotal)}</Text>
                </View>
                {discountPercent > 0 ? (
                  <View style={styles.sumRow}>
                    <Text style={[styles.sumLabel, { color: colors.success }]}>{tf("Rabatt ({percent}%)", { percent: discountPercent })}</Text>
                    <Text style={[styles.sumVal, { color: colors.success }]}>-{euro(discount)}</Text>
                  </View>
                ) : null}
                <View style={styles.sumRow}>
                  <Text style={styles.sumLabel}>Versand</Text>
                  <Text style={styles.sumVal}>{shipping === 0 ? "Gratis" : euro(shipping)}</Text>
                </View>
                {shipping > 0 ? (
                  <Muted>{tf("Noch {amount} bis zum Gratis-Versand", { amount: euro(threshold - discounted) })}</Muted>
                ) : null}
                {Object.entries(vatByRate).map(([rate, amt]) => (
                  <View key={rate} style={styles.sumRow}>
                    <Text style={styles.vatLabel}>{tf("inkl. MwSt {rate}%", { rate })}</Text>
                    <Text style={styles.vatLabel}>{euro(amt as number)}</Text>
                  </View>
                ))}
                <View style={styles.totalRow}>
                  <Text style={styles.totalLabel}>Gesamt</Text>
                  <Text style={styles.totalVal}>{euro(total)}</Text>
                </View>
              </Card>

              <Pressable testID="subscription-toggle" style={styles.termsRow} onPress={() => setSubscription((value) => !value)}>
                {subscription ? <CheckSquare size={24} color={colors.brandPrimary} weight="fill" /> : <Square size={24} color={colors.muted} />}
                <Text style={styles.termsText}>Als monatliches Abo bestellen. Der konfigurierte Abo-Rabatt wird serverseitig nach der Mengenstaffel berechnet.</Text>
              </Pressable>

              <SectionTitle style={{ marginTop: 4 }}>Rabattcode</SectionTitle>
              <Card>
                <View style={styles.promoRow}>
                  <View style={{ flex: 1 }}>
                    <Input
                      testID="promo-input"
                      value={promoInput}
                      onChangeText={setPromoInput}
                      placeholder="z. B. SS-XXXXXX"
                      autoCapitalize="characters"
                    />
                  </View>
                  <Button
                    testID="promo-apply"
                    title={promo ? "Geändert" : "Einlösen"}
                    kind="secondary"
                    loading={promoBusy}
                    onPress={applyPromo}
                    style={{ width: 120 }}
                  />
                </View>
                {promoMsg ? (
                  <Text style={[styles.promoMsg, { color: promo ? colors.success : colors.error }]} testID="promo-msg">
                    {promoMsg}
                  </Text>
                ) : (
                  <Muted style={{ marginTop: 8 }}>Newsletter-Abonnenten erhalten einen Rabattcode per E-Mail.</Muted>
                )}
              </Card>

              <SectionTitle style={{ marginTop: 4 }}>Lieferadresse</SectionTitle>
              <Card>
                <Input testID="cust-name" value={form.name} onChangeText={set("name")} placeholder="Name" />
                <Input testID="cust-email" value={form.email} onChangeText={set("email")} placeholder="E-Mail" autoCapitalize="none" keyboardType="email-address" style={{ marginTop: 8 }} />
                <Input testID="cust-phone" value={form.phone} onChangeText={set("phone")} placeholder="Telefon (optional)" keyboardType="phone-pad" style={{ marginTop: 8 }} />
                <Input testID="cust-street" value={form.street} onChangeText={set("street")} placeholder="Straße & Nr." style={{ marginTop: 8 }} />
                <View style={styles.rowGap}>
                  <View style={{ flex: 1 }}>
                    <Input testID="cust-zip" value={form.zip} onChangeText={set("zip")} placeholder="PLZ" keyboardType="numeric" />
                  </View>
                  <View style={{ flex: 2 }}>
                    <Input testID="cust-city" value={form.city} onChangeText={set("city")} placeholder="Ort" />
                  </View>
                </View>
              </Card>

              <Pressable testID="accept-terms" style={styles.termsRow} onPress={() => setAccepted((a) => !a)}>
                {accepted ? (
                  <CheckSquare size={24} color={colors.brandPrimary} weight="fill" />
                ) : (
                  <Square size={24} color={colors.muted} weight="regular" />
                )}
                <Text style={styles.termsText}>
                  Ich akzeptiere die{" "}
                  <Text style={styles.termsLink} onPress={() => router.push("/legal/agb")}>AGB</Text>
                  {" "}und habe die{" "}
                  <Text style={styles.termsLink} onPress={() => router.push("/legal/widerruf")}>Widerrufsbelehrung</Text>
                  {language === "de" ? " zur Kenntnis genommen." : "."}
                </Text>
              </Pressable>

              {quote.error ? <Text style={{ color: colors.error }}>{(quote.error as Error).message}</Text> : null}
              <Button testID="shop-checkout" title={tf("Kostenpflichtig bestellen · {total}", { total: euro(total) })} loading={busy || quote.isLoading} disabled={!accepted || !quote.data} onPress={checkout} />
              <Muted>Kartenzahlung über Stripe. Im Vorschaumodus ist die Zahlung noch nicht aktiv.</Muted>
            </>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingBottom: 14, backgroundColor: c.surface, borderBottomWidth: 1, borderBottomColor: c.divider },
  iconBtn: { width: 40, height: 40, borderRadius: 12, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 20, fontWeight: "800", color: c.onSurface },
  content: { padding: 20, gap: 12, paddingBottom: 40 },
  line: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: c.divider },
  lName: { fontSize: 15, fontWeight: "700", color: c.onSurface },
  step: { width: 30, height: 30, borderRadius: 8, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  qty: { minWidth: 22, textAlign: "center", fontSize: 15, fontWeight: "800", color: c.onSurface },
  sumRow: { flexDirection: "row", justifyContent: "space-between", marginTop: 10 },
  sumLabel: { fontSize: 14, color: c.muted, fontWeight: "600" },
  sumVal: { fontSize: 14, fontWeight: "700", color: c.onSurface },
  totalRow: { flexDirection: "row", justifyContent: "space-between", marginTop: 10, borderTopWidth: 1, borderTopColor: c.divider, paddingTop: 10 },
  vatLabel: { fontSize: 12, color: c.muted, fontWeight: "600" },
  totalLabel: { fontSize: 16, fontWeight: "800", color: c.onSurface },
  totalVal: { fontSize: 20, fontWeight: "800", color: c.brandPrimary },
  rowGap: { flexDirection: "row", gap: 8, marginTop: 8 },
  doneTitle: { fontSize: 18, fontWeight: "800", color: c.onSurface, marginTop: 8 },
  promoRow: { flexDirection: "row", gap: 8, alignItems: "center" },
  promoMsg: { fontSize: 13, fontWeight: "700", marginTop: 8 },
  termsRow: { flexDirection: "row", gap: 10, alignItems: "flex-start", marginTop: 4, marginBottom: 4 },
  termsText: { flex: 1, fontSize: 13, color: c.onSurfaceSecondary, lineHeight: 19 },
  termsLink: { color: c.brandPrimary, fontWeight: "700", textDecorationLine: "underline" },
}));
