import { useEffect, useState } from "react";
import { View, Text, ScrollView, Pressable, KeyboardAvoidingView, Platform, Alert, Linking } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as WebBrowser from "expo-web-browser";
import { ArrowLeft, SignOut, MapPin, Receipt, Truck } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiGet, apiPost } from "@/src/api/client";
import { euro, dateDE } from "@/src/lib/format";
import { shopApi, shopSetToken, shopLogout, shopToken } from "@/src/shop/auth";
import { shareShopInvoicePdf, glsTrackUrl } from "@/src/lib/pdf";
import { Card, Input, Button, SectionTitle, Muted, EmptyState, InfoRow, StatusBadge } from "@/src/components/ui";

const EMPTY_ADDR = { name: "", phone: "", street: "", zip: "", city: "" };

export default function ShopKonto() {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<any>(null);
  const [orders, setOrders] = useState<any[]>([]);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const [addr, setAddr] = useState({ ...EMPTY_ADDR });
  const [addrSaving, setAddrSaving] = useState(false);
  const setA = (k: string) => (v: string) => setAddr((a) => ({ ...a, [k]: v }));

  const loadAccount = async () => {
    try {
      const t = await shopToken();
      if (!t) { setReady(true); return; }
      const me = await shopApi.me();
      setUser(me);
      setAddr({ ...EMPTY_ADDR, ...(me.address || {}) });
      setOrders(await shopApi.myOrders());
    } catch {
      await shopLogout();
    } finally {
      setReady(true);
    }
  };
  useEffect(() => { loadAccount(); }, []);

  const saveAddr = async () => {
    setAddrSaving(true);
    try {
      await shopApi.saveAddress(addr);
      Alert.alert("Gespeichert", "Deine Lieferadresse wurde gespeichert und wird beim nächsten Kauf vorausgefüllt.");
    } catch (e: any) {
      Alert.alert("Fehler", e.message || "Adresse konnte nicht gespeichert werden");
    } finally {
      setAddrSaving(false);
    }
  };

  const submit = async () => {
    setMsg(""); setLoading(true);
    try {
      const res = mode === "register"
        ? await shopApi.register({ name, email, password })
        : await shopApi.login({ email, password });
      await shopSetToken(res.access_token);
      setUser(res.user);
      setAddr({ ...EMPTY_ADDR, ...(res.user.address || {}) });
      setPassword("");
      setOrders(await shopApi.myOrders());
    } catch (e: any) {
      setMsg(e.message || "Fehler");
    } finally {
      setLoading(false);
    }
  };

  const onLogout = async () => {
    await shopLogout();
    setUser(null); setOrders([]);
  };

  const [payingId, setPayingId] = useState<string | null>(null);
  const payNow = async (orderId: string, token: string) => {
    setPayingId(orderId);
    const q = `?token=${encodeURIComponent(token || "")}`;
    try {
      const res = await apiPost(`/shop/orders/${orderId}/checkout${q}`, {});
      if (res?.url) {
        await WebBrowser.openBrowserAsync(res.url);
        for (let i = 0; i < 8; i++) {
          await new Promise((r) => setTimeout(r, 1500));
          const st = await apiGet(`/shop/orders/${orderId}/payment-status${q}`);
          if (st.status === "Bezahlt") break;
        }
        setOrders(await shopApi.myOrders());
      }
    } catch (e: any) {
      Alert.alert(
        "Zahlung nicht möglich",
        e.message || "Die Kartenzahlung ist erst nach Veröffentlichung der App aktiv.",
      );
    } finally {
      setPayingId(null);
    }
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} style={styles.backBtn} testID="back-button" hitSlop={8}>
          <ArrowLeft size={20} color={colors.onSurfaceSecondary} weight="bold" />
        </Pressable>
        <Text style={styles.title}>Mein Konto</Text>
      </View>

      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          {!ready ? null : user ? (
            <>
              <Card>
                <Text style={styles.hi}>Hallo {user.name} 👋</Text>
                <Muted>{user.email}</Muted>
                <Pressable testID="shop-logout" style={styles.logout} onPress={onLogout}>
                  <SignOut size={16} color={colors.error} weight="bold" />
                  <Text style={styles.logoutTxt}>Abmelden</Text>
                </Pressable>
              </Card>

              <SectionTitle style={{ marginTop: 4 }}>Meine Lieferadresse</SectionTitle>
              <Card>
                <Muted>Wird beim Checkout automatisch vorausgefüllt.</Muted>
                <Input testID="addr-name" value={addr.name} onChangeText={setA("name")} placeholder="Name" style={{ marginTop: 8 }} />
                <Input testID="addr-phone" value={addr.phone} onChangeText={setA("phone")} placeholder="Telefon (optional)" keyboardType="phone-pad" style={{ marginTop: 8 }} />
                <Input testID="addr-street" value={addr.street} onChangeText={setA("street")} placeholder="Straße & Nr." style={{ marginTop: 8 }} />
                <View style={{ flexDirection: "row", gap: 8, marginTop: 8 }}>
                  <View style={{ flex: 1 }}>
                    <Input testID="addr-zip" value={addr.zip} onChangeText={setA("zip")} placeholder="PLZ" keyboardType="numeric" />
                  </View>
                  <View style={{ flex: 2 }}>
                    <Input testID="addr-city" value={addr.city} onChangeText={setA("city")} placeholder="Ort" />
                  </View>
                </View>
                <Button testID="addr-save" title="Adresse speichern" loading={addrSaving} onPress={saveAddr} style={{ marginTop: 10 }} />
              </Card>

              <SectionTitle style={{ marginTop: 4 }}>Meine Bestellungen</SectionTitle>
              {orders.length === 0 ? (
                <EmptyState title="Noch keine Bestellungen" subtitle="Deine Shop-Bestellungen erscheinen hier" />
              ) : (
                orders.map((o) => {
                  const track = glsTrackUrl(o.trackingNumber, o.customer?.zip);
                  return (
                  <Card key={o.id} testID={`myorder-${o.id}`}>
                    <View style={styles.row}>
                      <Text style={styles.oId}>{o.id}</Text>
                      <StatusBadge status={o.paymentStatus} />
                    </View>
                    <Muted>{dateDE(o.createdAt)}</Muted>

                    <View style={styles.itemsBox}>
                      {(o.items ?? []).map((it: any, idx: number) => (
                        <View key={idx} style={styles.itemRow}>
                          <Text style={styles.itemName} numberOfLines={2}>{it.name}</Text>
                          <Text style={styles.itemQty}>{it.qty}×</Text>
                          <Text style={styles.itemPrice}>{euro(it.price * it.qty)}</Text>
                        </View>
                      ))}
                    </View>

                    {o.discount > 0 ? (
                      <InfoRow label={`Rabatt${o.discountPercent ? ` (${o.discountPercent}%)` : ""}`} value={`-${euro(o.discount)}`} />
                    ) : null}
                    <InfoRow label="Versand" value={o.shipping === 0 ? "Gratis" : euro(o.shipping)} />
                    {o.taxTotal != null ? <InfoRow label="enthaltene MwSt" value={euro(o.taxTotal)} /> : null}
                    <View style={styles.totalRow}>
                      <Text style={styles.totalLabel}>Gesamt</Text>
                      <Text style={styles.totalVal}>{euro(o.total)}</Text>
                    </View>

                    <Text style={styles.blockLabel}>Statusverlauf</Text>
                    <View style={styles.timeline}>
                      {(o.statusHistory ?? [{ status: o.status || "Neu", at: o.createdAt }]).map((h: any, i: number, arr: any[]) => {
                        const last = i === arr.length - 1;
                        return (
                          <View key={i} style={styles.tlRow}>
                            <View style={styles.tlLeft}>
                              <View style={[styles.tlDot, last && styles.tlDotActive]} />
                              {i < arr.length - 1 ? <View style={styles.tlLine} /> : null}
                            </View>
                            <View style={{ flex: 1, paddingBottom: last ? 0 : 12 }}>
                              <Text style={[styles.tlStatus, last && styles.tlStatusActive]}>{h.status}</Text>
                              <Text style={styles.tlDate}>{dateDE(h.at)}</Text>
                            </View>
                          </View>
                        );
                      })}
                    </View>

                    {o.trackingNumber ? (
                      <Pressable testID={`track-${o.id}`} style={styles.trackBtn} onPress={() => track && Linking.openURL(track)}>
                        <Truck size={16} color={colors.brandPrimary} weight="bold" />
                        <Text style={styles.trackText}>Sendung verfolgen · {o.trackingNumber}</Text>
                      </Pressable>
                    ) : null}

                    {o.customer?.street ? (
                      <View style={styles.addrBox}>
                        <View style={styles.addrHead}>
                          <MapPin size={15} color={colors.brandPrimary} weight="fill" />
                          <Text style={styles.addrTitle}>Lieferadresse</Text>
                        </View>
                        <Text style={styles.addrText}>{o.customer.name}</Text>
                        <Text style={styles.addrText}>{o.customer.street}</Text>
                        <Text style={styles.addrText}>{o.customer.zip} {o.customer.city}</Text>
                        {o.customer.phone ? <Text style={styles.addrText}>Tel. {o.customer.phone}</Text> : null}
                      </View>
                    ) : null}

                    <Pressable testID={`invoice-pdf-${o.id}`} style={styles.pdfBtn} onPress={() => shareShopInvoicePdf(o)}>
                      <Receipt size={16} color={colors.brandPrimary} weight="bold" />
                      <Text style={styles.pdfText}>Rechnung als PDF</Text>
                    </Pressable>

                    {o.paymentStatus !== "Bezahlt" ? (
                      <Button
                        testID={`pay-now-${o.id}`}
                        title="Jetzt bezahlen"
                        loading={payingId === o.id}
                        onPress={() => payNow(o.id, o.token)}
                        style={{ marginTop: 10 }}
                      />
                    ) : null}
                  </Card>
                  );
                })
              )}
            </>
          ) : (
            <Card>
              <View style={styles.tabs}>
                <Pressable testID="tab-login" style={[styles.tab, mode === "login" && styles.tabActive]} onPress={() => setMode("login")}>
                  <Text style={[styles.tabTxt, mode === "login" && styles.tabTxtActive]}>Anmelden</Text>
                </Pressable>
                <Pressable testID="tab-register" style={[styles.tab, mode === "register" && styles.tabActive]} onPress={() => setMode("register")}>
                  <Text style={[styles.tabTxt, mode === "register" && styles.tabTxtActive]}>Registrieren</Text>
                </Pressable>
              </View>
              {mode === "register" && (
                <>
                  <Text style={styles.label}>Name</Text>
                  <Input testID="acc-name" value={name} onChangeText={setName} placeholder="Vor- und Nachname" />
                </>
              )}
              <Text style={styles.label}>E-Mail</Text>
              <Input testID="acc-email" value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" placeholder="name@email.de" />
              <Text style={styles.label}>Passwort</Text>
              <Input testID="acc-password" value={password} onChangeText={setPassword} secureTextEntry placeholder="mind. 8 Zeichen" />
              {msg ? <Text style={styles.err}>{msg}</Text> : null}
              <Button testID="acc-submit" title={mode === "register" ? "Konto erstellen" : "Anmelden"} loading={loading} onPress={submit} style={{ marginTop: 10 }} />
              <Muted style={{ marginTop: 10 }}>Mit Konto siehst du deinen Bestellverlauf. Ein Kauf ist auch ohne Konto (als Gast) möglich.</Muted>
            </Card>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  container: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingVertical: 12 },
  backBtn: { width: 40, height: 40, borderRadius: 12, backgroundColor: c.surface, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 20, fontWeight: "800", color: c.onSurface },
  content: { padding: 16, gap: 12, paddingBottom: 40 },
  hi: { fontSize: 17, fontWeight: "800", color: c.onSurface },
  logout: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 12, paddingVertical: 10, borderRadius: 10, backgroundColor: c.surfaceTertiary },
  logoutTxt: { color: c.error, fontWeight: "700", fontSize: 14 },
  tabs: { flexDirection: "row", gap: 8, marginBottom: 8 },
  tab: { flex: 1, paddingVertical: 10, borderRadius: 10, alignItems: "center", backgroundColor: c.surfaceTertiary },
  tabActive: { backgroundColor: c.brandPrimary },
  tabTxt: { fontSize: 14, fontWeight: "700", color: c.onSurfaceSecondary },
  tabTxtActive: { color: c.onBrandPrimary },
  label: { fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary, marginTop: 8, marginBottom: 4 },
  err: { color: c.error, fontSize: 14, fontWeight: "600", marginTop: 8 },
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  oId: { fontSize: 16, fontWeight: "800", color: c.onSurface },
  oStatus: { fontSize: 13, fontWeight: "700", color: c.brandPrimary },
  itemsBox: { marginTop: 10, borderTopWidth: 1, borderTopColor: c.divider, paddingTop: 8 },
  itemRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 4 },
  itemName: { flex: 1, fontSize: 14, color: c.onSurface, fontWeight: "600" },
  itemQty: { fontSize: 13, color: c.muted, fontWeight: "700", minWidth: 30, textAlign: "right" },
  itemPrice: { fontSize: 14, color: c.onSurface, fontWeight: "700", minWidth: 70, textAlign: "right" },
  totalRow: { flexDirection: "row", justifyContent: "space-between", marginTop: 8, borderTopWidth: 1, borderTopColor: c.divider, paddingTop: 8 },
  totalLabel: { fontSize: 15, fontWeight: "800", color: c.onSurface },
  totalVal: { fontSize: 17, fontWeight: "800", color: c.brandPrimary },
  deliveryRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: 8 },
  deliveryLabel: { fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary },
  deliveryVal: { fontSize: 13, fontWeight: "800", color: c.onSurface },
  blockLabel: { fontSize: 12, fontWeight: "800", color: c.onSurfaceSecondary, marginTop: 12, marginBottom: 8, textTransform: "uppercase", letterSpacing: 0.5 },
  timeline: { paddingLeft: 2 },
  tlRow: { flexDirection: "row", gap: 10 },
  tlLeft: { alignItems: "center", width: 16 },
  tlDot: { width: 12, height: 12, borderRadius: 6, backgroundColor: c.border, marginTop: 2 },
  tlDotActive: { backgroundColor: c.brandPrimary },
  tlLine: { flex: 1, width: 2, backgroundColor: c.divider, marginTop: 2 },
  tlStatus: { fontSize: 14, fontWeight: "700", color: c.onSurfaceSecondary },
  tlStatusActive: { color: c.onSurface, fontWeight: "800" },
  tlDate: { fontSize: 12, color: c.muted, marginTop: 1 },
  trackBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, marginTop: 12, paddingVertical: 10, borderRadius: 10, backgroundColor: c.brandTertiary },
  trackText: { color: c.brandPrimary, fontWeight: "700", fontSize: 13 },
  pdfBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 10, paddingVertical: 10, borderRadius: 10, backgroundColor: c.surfaceTertiary },
  pdfText: { color: c.brandPrimary, fontWeight: "700", fontSize: 14 },
  addrBox: { marginTop: 10, padding: 10, borderRadius: 10, backgroundColor: c.surfaceTertiary },
  addrHead: { flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 4 },
  addrTitle: { fontSize: 13, fontWeight: "800", color: c.onSurface },
  addrText: { fontSize: 13, color: c.onSurfaceSecondary, lineHeight: 19 },
}));
