import { useState } from "react";
import { View, Text, ScrollView, Pressable, useWindowDimensions } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Image } from "expo-image";
import { ArrowLeft, ShoppingCart, ImageSquare, Plus, UserCircle, EnvelopeSimple, CheckCircle } from "phosphor-react-native";

import { makeStyles, tokens, useTheme } from "@/src/theme";
import { apiGet, fileUrl, API_BASE } from "@/src/api/client";
import { euro } from "@/src/lib/format";
import { useCart } from "@/src/shop/cart";
import { shopApi } from "@/src/shop/auth";
import { Card, EmptyState, Muted, Input, Button, PageContainer, LoadingState, ErrorState } from "@/src/components/ui";

function NewsletterCard({ percent }: { percent: number }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<null | { code?: string; percent?: number; pending?: boolean }>(null);
  const [err, setErr] = useState("");

  const submit = async () => {
    setErr("");
    if (!email.includes("@")) {
      setErr("Bitte eine gültige E-Mail eingeben.");
      return;
    }
    setBusy(true);
    try {
      const res = await shopApi.newsletter({ email: email.trim(), baseUrl: API_BASE });
      setDone(res.pending ? { pending: true } : { code: res.code, percent: res.percent });
    } catch (e: any) {
      setErr(e.message || "Anmeldung fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card testID="newsletter-card">
      {done?.pending ? (
        <View style={{ alignItems: "center" }}>
          <EnvelopeSimple size={34} color={colors.brandPrimary} weight="fill" />
          <Text style={styles.nlTitle}>Fast geschafft!</Text>
          <Muted style={{ textAlign: "center" }}>
            Wir haben dir eine E-Mail geschickt. Bitte bestätige deine Anmeldung – danach erhältst du deinen Rabattcode.
          </Muted>
        </View>
      ) : done?.code ? (
        <View style={{ alignItems: "center" }}>
          <CheckCircle size={34} color={colors.success} weight="fill" />
          <Text style={styles.nlTitle}>Willkommen! {done.percent}% Rabatt gesichert</Text>
          <Muted style={{ textAlign: "center" }}>Dein Rabattcode – auch per E-Mail verschickt:</Muted>
          <View style={styles.codeBox}>
            <Text style={styles.codeText} selectable testID="newsletter-code">{done.code}</Text>
          </View>
          <Muted style={{ textAlign: "center", marginTop: 6 }}>Einfach im Warenkorb eingeben und sparen.</Muted>
        </View>
      ) : (
        <>
          <View style={styles.nlHead}>
            <EnvelopeSimple size={20} color={colors.brandPrimary} weight="bold" />
            <Text style={styles.nlTitle}>Newsletter & {percent}% Rabatt</Text>
          </View>
          <Muted>Jetzt anmelden und {percent}% Rabatt auf deine Bestellungen erhalten.</Muted>
          <Input
            testID="newsletter-email"
            value={email}
            onChangeText={setEmail}
            placeholder="deine@email.de"
            autoCapitalize="none"
            keyboardType="email-address"
            style={{ marginTop: 10 }}
          />
          {err ? <Text style={styles.nlErr}>{err}</Text> : null}
          <Button testID="newsletter-submit" title={`Anmelden & ${percent}% sichern`} loading={busy} onPress={submit} style={{ marginTop: 10 }} />
        </>
      )}
    </Card>
  );
}

export default function Shop() {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const cart = useCart();
  const { width } = useWindowDimensions();
  const desktop = width >= tokens.layout.tablet;
  const products = useQuery({ queryKey: ["shop-products"], queryFn: () => apiGet("/shop/products") });
  const settings = useQuery({ queryKey: ["shop-settings"], queryFn: () => apiGet("/shop/settings") });

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 8 }]}>
        <Pressable onPress={() => router.back()} style={styles.iconBtn} testID="shop-back" hitSlop={8}>
          <ArrowLeft size={20} color={colors.onSurfaceSecondary} weight="bold" />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>S&S Kaffee-Shop</Text>
          <Text style={styles.subtitle}>Premium-Kaffee für zuhause</Text>
        </View>
        <Pressable onPress={() => router.push("/shop/konto")} style={styles.iconBtn} testID="shop-account-button" hitSlop={8}>
          <UserCircle size={22} color={colors.onSurfaceSecondary} weight="bold" />
        </Pressable>
        <Pressable onPress={() => router.push("/shop/warenkorb")} style={styles.cartBtn} testID="shop-cart-button" hitSlop={8}>
          <ShoppingCart size={20} color={colors.onBrandPrimary} weight="bold" />
          {cart.count > 0 && (
            <View style={styles.badge}>
              <Text style={styles.badgeText}>{cart.count}</Text>
            </View>
          )}
        </Pressable>
      </View>

      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        <PageContainer style={styles.page}>
        <View style={styles.shopHero}>
          <Text style={styles.eyebrow}>S&S COFFEE AND MORE</Text>
          <Text style={styles.heroTitle}>Kaffee, der im Alltag überzeugt.</Text>
          <Text style={styles.heroText}>Transparente Bruttopreise, flexible Mengen und auf Wunsch monatlich geliefert.</Text>
        </View>
        {settings.data && (
          <View style={styles.banner}>
            <Text style={styles.bannerText}>
              Gratis-Versand ab {euro(settings.data.freeShippingThreshold)} · sonst {euro(settings.data.shippingFee)} Versand
            </Text>
          </View>
        )}
        {settings.data?.newsletterDiscountEnabled ? (
          <NewsletterCard percent={settings.data?.newsletterDiscountPercent ?? 10} />
        ) : null}
        {products.isLoading ? <LoadingState label="Produkte werden geladen…" /> : products.isError ? <ErrorState onRetry={() => products.refetch()} /> : (products.data ?? []).length === 0 ? (
          <EmptyState title="Noch keine Produkte" subtitle="Der Shop wird gerade bestückt" />
        ) : (
          <View style={styles.productGrid}>
          {(products.data ?? []).map((p: any) => (
            <Card key={p.id} testID={`shop-product-${p.id}`} style={[styles.productCard, desktop && styles.productCardDesktop]}>
              <View style={styles.productBody}>
                {p.imageUrl ? (
                  <Image source={{ uri: fileUrl(p.imageUrl) }} style={styles.thumb} contentFit="cover" transition={150} />
                ) : (
                  <View style={[styles.thumb, styles.thumbEmpty]}>
                    <ImageSquare size={26} color={colors.muted} weight="duotone" />
                  </View>
                )}
                <View style={{ flex: 1 }}>
                  <Text style={styles.pName} numberOfLines={1}>
                    {p.brand} {p.name}
                  </Text>
                  {p.description ? <Muted numberOfLines={2}>{p.description}</Muted> : null}
                  <View style={styles.priceRow}>
                    <Text style={styles.price}>
                      {euro(p.b2cPrice)}
                      <Text style={styles.perUnit}> /{p.unit}</Text>
                    </Text>
                    <Text style={styles.vat}>inkl. {p.taxRate}% MwSt</Text>
                  </View>
                  {p.stock != null && p.stock <= 0 ? <Text style={styles.soldOut}>Zzt. nicht vorrätig</Text> : null}
                  {p.b2cTiers?.length ? (
                    <View style={styles.tiers}>
                      {p.b2cTiers.slice(0, 3).map((tier: any) => <Text key={tier.minQty} style={styles.tier}>ab {tier.minQty} · {euro(tier.price)}/{p.unit}</Text>)}
                    </View>
                  ) : null}
                </View>
              </View>
              <Pressable testID={`shop-add-${p.id}`} style={styles.addBtn} onPress={() => cart.add(p.id, 1)}>
                <Plus size={16} color={colors.onBrandPrimary} weight="bold" />
                <Text style={styles.addText}>In den Warenkorb</Text>
              </Pressable>
            </Card>
          ))}
          </View>
        )}
        <View style={styles.footer}>
          <View style={styles.footerLinks}>
            <Pressable testID="footer-impressum" onPress={() => router.push("/legal/impressum")}>
              <Text style={styles.footerLink}>Impressum</Text>
            </Pressable>
            <Text style={styles.footerDot}>·</Text>
            <Pressable testID="footer-datenschutz" onPress={() => router.push("/legal/datenschutz")}>
              <Text style={styles.footerLink}>Datenschutz</Text>
            </Pressable>
            <Text style={styles.footerDot}>·</Text>
            <Pressable testID="footer-agb" onPress={() => router.push("/legal/agb")}>
              <Text style={styles.footerLink}>AGB</Text>
            </Pressable>
            <Text style={styles.footerDot}>·</Text>
            <Pressable testID="footer-widerruf" onPress={() => router.push("/legal/widerruf")}>
              <Text style={styles.footerLink}>Widerruf</Text>
            </Pressable>
          </View>
        </View>
        </PageContainer>
      </ScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingBottom: 14, backgroundColor: c.surface, borderBottomWidth: 1, borderBottomColor: c.divider },
  iconBtn: { width: 40, height: 40, borderRadius: 12, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  cartBtn: { width: 44, height: 44, borderRadius: 12, backgroundColor: c.brandPrimary, alignItems: "center", justifyContent: "center" },
  badge: { position: "absolute", top: -4, right: -4, minWidth: 20, height: 20, paddingHorizontal: 4, borderRadius: 10, backgroundColor: c.error, alignItems: "center", justifyContent: "center" },
  badgeText: { color: c.onError, fontSize: 11, fontWeight: "800" },
  title: { fontSize: 20, fontWeight: "800", color: c.onSurface },
  subtitle: { fontSize: 13, color: c.muted, marginTop: 1 },
  content: { padding: tokens.spacing.lg, paddingBottom: 32 },
  page: { gap: 14 },
  shopHero: { backgroundColor: c.surfaceInverse, borderRadius: tokens.radius.lg, padding: 24, gap: 7 },
  eyebrow: { color: c.inverseAccent, fontSize: 10, fontWeight: "900", letterSpacing: 1.6 },
  heroTitle: { color: c.onSurfaceInverse, fontSize: 28, fontWeight: "800", letterSpacing: -0.7, maxWidth: 560 },
  heroText: { color: c.inverseMuted, fontSize: 14, lineHeight: 21, maxWidth: 620 },
  banner: { backgroundColor: c.brandTertiary, borderRadius: 12, padding: 12 },
  bannerText: { color: c.brandPrimary, fontWeight: "700", fontSize: 13, textAlign: "center" },
  nlHead: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 4 },
  nlTitle: { fontSize: 16, fontWeight: "800", color: c.onSurface, marginTop: 6 },
  nlErr: { color: c.error, fontSize: 13, fontWeight: "600", marginTop: 6 },
  codeBox: { marginTop: 10, paddingVertical: 12, paddingHorizontal: 20, borderRadius: 12, borderWidth: 2, borderStyle: "dashed", borderColor: c.brandPrimary, backgroundColor: c.brandTertiary },
  codeText: { fontSize: 22, fontWeight: "800", letterSpacing: 2, color: c.brandPrimary },
  productGrid: { flexDirection: "row", flexWrap: "wrap", gap: 14 },
  productCard: { width: "100%" },
  productCardDesktop: { width: "48.8%", flexGrow: 1, maxWidth: 520 },
  productBody: { flexDirection: "row", gap: 14, alignItems: "flex-start" },
  thumb: { width: 88, height: 88, borderRadius: 10, backgroundColor: c.surfaceTertiary },
  thumbEmpty: { alignItems: "center", justifyContent: "center" },
  pName: { fontSize: 16, fontWeight: "800", color: c.onSurface },
  priceRow: { flexDirection: "row", alignItems: "baseline", gap: 8, marginTop: 4 },
  price: { fontSize: 18, fontWeight: "800", color: c.onSurface },
  perUnit: { fontSize: 13, color: c.muted, fontWeight: "600" },
  vat: { fontSize: 12, color: c.muted },
  soldOut: { fontSize: 12, fontWeight: "700", color: c.error, marginTop: 2 },
  tiers: { flexDirection: "row", flexWrap: "wrap", gap: 5, marginTop: 8 },
  tier: { backgroundColor: c.brandTertiary, color: c.onBrandTertiary, fontSize: 10.5, fontWeight: "700", paddingHorizontal: 7, paddingVertical: 4, borderRadius: 6 },
  addBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 12, paddingVertical: 12, borderRadius: 12, backgroundColor: c.brandPrimary },
  addText: { color: c.onBrandPrimary, fontWeight: "700", fontSize: 14 },
  footer: { marginTop: 8, paddingTop: 16, borderTopWidth: 1, borderTopColor: c.divider },
  footerLinks: { flexDirection: "row", flexWrap: "wrap", justifyContent: "center", alignItems: "center", gap: 6 },
  footerLink: { fontSize: 13, color: c.muted, fontWeight: "600" },
  footerDot: { fontSize: 13, color: c.muted },
}));
