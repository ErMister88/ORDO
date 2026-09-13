import { View, Text, ScrollView, Pressable } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Image } from "expo-image";
import { ArrowLeft, ShoppingCart, ImageSquare, Plus, UserCircle } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiGet, fileUrl } from "@/src/api/client";
import { euro } from "@/src/lib/format";
import { useCart } from "@/src/shop/cart";
import { Card, EmptyState, Muted } from "@/src/components/ui";

export default function Shop() {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const cart = useCart();
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
        {settings.data && (
          <View style={styles.banner}>
            <Text style={styles.bannerText}>
              Gratis-Versand ab {euro(settings.data.freeShippingThreshold)} · sonst {euro(settings.data.shippingFee)} Versand
            </Text>
          </View>
        )}
        {(products.data ?? []).length === 0 ? (
          <EmptyState title="Noch keine Produkte" subtitle="Der Shop wird gerade bestückt" />
        ) : (
          (products.data ?? []).map((p: any) => (
            <Card key={p.id} testID={`shop-product-${p.id}`}>
              <View style={styles.row}>
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
                </View>
              </View>
              <Pressable testID={`shop-add-${p.id}`} style={styles.addBtn} onPress={() => cart.add(p.id, 1)}>
                <Plus size={16} color={colors.onBrandPrimary} weight="bold" />
                <Text style={styles.addText}>In den Warenkorb</Text>
              </Pressable>
            </Card>
          ))
        )}
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
  content: { padding: 20, gap: 12, paddingBottom: 32 },
  banner: { backgroundColor: c.brandTertiary, borderRadius: 12, padding: 12 },
  bannerText: { color: c.brandPrimary, fontWeight: "700", fontSize: 13, textAlign: "center" },
  row: { flexDirection: "row", gap: 12, alignItems: "center" },
  thumb: { width: 64, height: 64, borderRadius: 12, backgroundColor: c.surfaceTertiary },
  thumbEmpty: { alignItems: "center", justifyContent: "center" },
  pName: { fontSize: 16, fontWeight: "800", color: c.onSurface },
  priceRow: { flexDirection: "row", alignItems: "baseline", gap: 8, marginTop: 4 },
  price: { fontSize: 18, fontWeight: "800", color: c.onSurface },
  perUnit: { fontSize: 13, color: c.muted, fontWeight: "600" },
  vat: { fontSize: 12, color: c.muted },
  soldOut: { fontSize: 12, fontWeight: "700", color: c.error, marginTop: 2 },
  addBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 12, paddingVertical: 12, borderRadius: 12, backgroundColor: c.brandPrimary },
  addText: { color: c.onBrandPrimary, fontWeight: "700", fontSize: 14 },
}));
