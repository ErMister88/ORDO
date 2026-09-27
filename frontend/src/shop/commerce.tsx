import { useEffect, useState } from "react";
import { Image } from "expo-image";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { MagnifyingGlass, ImageSquare, ShoppingCart } from "phosphor-react-native";
import { Pressable, View, useWindowDimensions } from "react-native";

import { apiGet, fileUrl } from "@/src/api/client";
import { Button, Card, Input, Muted } from "@/src/components/ui";
import { euro } from "@/src/lib/format";
import { LocalizedText as Text, useI18n } from "@/src/i18n";
import { useCart } from "@/src/shop/cart";
import { makeStyles, useTheme } from "@/src/theme";

export type CommerceProduct = {
  id: string; slug: string; name: string; brand?: string; imageUrl?: string;
  b2cPrice: number; unit?: string; availability?: string; quickAdd?: boolean;
  directPurchaseAllowed?: boolean; variants?: Array<{ id: string; name: string }>;
  subscriptionAllowed?: boolean;
  basePrice?: { price: number; unit: string } | null;
};

export function SearchBox({ initial = "", autoFocus = false }: { initial?: string; autoFocus?: boolean }) {
  const [value, setValue] = useState(initial);
  const [debounced, setDebounced] = useState(initial);
  const router = useRouter();
  const styles = useStyles();
  const { colors } = useTheme();
  useEffect(() => { const handle = setTimeout(() => setDebounced(value.trim()), 180); return () => clearTimeout(handle); }, [value]);
  const suggestions = useQuery({
    queryKey: ["shop-search-suggest", debounced],
    queryFn: () => apiGet(`/shop/search/suggest?q=${encodeURIComponent(debounced)}`),
    enabled: debounced.length >= 2,
  });
  const submit = () => value.trim() && router.push({ pathname: "/shop/suche", params: { q: value.trim() } });
  const hasSuggestions = (suggestions.data?.products?.length ?? 0) + (suggestions.data?.brands?.length ?? 0) + (suggestions.data?.categories?.length ?? 0) > 0;
  return <View style={styles.searchWrap}>
    <View style={styles.searchRow}>
      <View style={{ flex: 1 }}><Input value={value} onChangeText={setValue} placeholder="Produkte, Marken, Kategorien oder SKU suchen" autoFocus={autoFocus} returnKeyType="search" onSubmitEditing={submit} /></View>
      <Pressable accessibilityLabel="Suche starten" style={styles.searchButton} onPress={submit}><MagnifyingGlass size={22} color={colors.onBrandPrimary} weight="bold" /></Pressable>
    </View>
    {debounced.length >= 2 && hasSuggestions ? <Card style={styles.suggestions}>
      {suggestions.data.brands?.map((item: any) => <Suggestion key={`brand-${item.id}`} label="Marke" item={item} onPress={() => router.push(`/marken/${item.slug}`)} />)}
      {suggestions.data.categories?.map((item: any) => <Suggestion key={`category-${item.id}`} label="Kategorie" item={item} onPress={() => router.push(`/shop/kategorie/${item.slug}`)} />)}
      {suggestions.data.products?.map((item: any) => <Suggestion key={`product-${item.id}`} label="Produkt" item={item} onPress={() => router.push(`/produkte/${item.slug}`)} />)}
      <Button title="Alle Ergebnisse" kind="secondary" onPress={submit} />
    </Card> : null}
  </View>;
}

function Suggestion({ label, item, onPress }: { label: string; item: any; onPress: () => void }) {
  const styles = useStyles();
  return <Pressable style={styles.suggestion} onPress={onPress}><Text style={styles.suggestionType}>{label}</Text><Text style={styles.suggestionName}>{item.name}</Text></Pressable>;
}

export function ProductCard({ product }: { product: CommerceProduct }) {
  const styles = useStyles();
  const cart = useCart();
  const router = useRouter();
  const { width } = useWindowDimensions();
  const { colors } = useTheme();
  const { tf } = useI18n();
  const complex = (product.variants?.length ?? 0) > 0 || product.directPurchaseAllowed === false || product.quickAdd === false;
  const available = product.availability !== "unavailable";
  return <Card style={[styles.productCard, width >= 1024 && styles.productCardDesktop]}>
    <Pressable onPress={() => router.push(`/produkte/${product.slug}`)}>
      {product.imageUrl ? <Image source={{ uri: fileUrl(product.imageUrl) }} style={styles.productImage} contentFit="cover" /> : <View style={[styles.productImage, styles.imageEmpty]}><ImageSquare size={34} color={colors.muted} /></View>}
      <Muted>{product.brand || " "}</Muted>
      <Text style={styles.productName}>{product.name}</Text>
      <Text style={styles.productPrice}>{euro(product.b2cPrice)}</Text>
      {product.basePrice ? <Muted>{tf("Grundpreis {price} / {unit}", { price: euro(product.basePrice.price), unit: product.basePrice.unit })}</Muted> : null}
      {!available ? <Text style={styles.unavailable}>Derzeit nicht verfügbar</Text> : null}
    </Pressable>
    {available && !complex ? <Button title="In den Warenkorb" onPress={() => cart.add(product.id, 1, { product: { id: product.id, slug: product.slug, name: product.name, brand: product.brand, imageUrl: product.imageUrl, unit: product.unit, subscriptionAllowed: product.subscriptionAllowed } })} /> : <Button title="Details ansehen" kind="secondary" onPress={() => router.push(`/produkte/${product.slug}`)} />}
  </Card>;
}

const useStyles = makeStyles((c) => ({
  searchWrap: { position: "relative", zIndex: 5 },
  searchRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  searchButton: { width: 48, height: 48, borderRadius: 12, backgroundColor: c.brandPrimary, alignItems: "center", justifyContent: "center" },
  suggestions: { position: "absolute", top: 56, left: 0, right: 0, zIndex: 20, gap: 4, borderWidth: 1, borderColor: c.borderStrong },
  suggestion: { flexDirection: "row", gap: 10, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: c.divider },
  suggestionType: { width: 72, color: c.muted, fontSize: 11, fontWeight: "800", textTransform: "uppercase" },
  suggestionName: { flex: 1, color: c.onSurface, fontWeight: "700" },
  productCard: { width: "100%", gap: 8 },
  productCardDesktop: { width: "31.8%", minWidth: 250, flexGrow: 1, maxWidth: 390 },
  productImage: { width: "100%", aspectRatio: 1.35, borderRadius: 12, backgroundColor: c.surfaceTertiary, marginBottom: 8 },
  imageEmpty: { alignItems: "center", justifyContent: "center" },
  productName: { color: c.onSurface, fontSize: 17, lineHeight: 22, fontWeight: "800", minHeight: 44 },
  productPrice: { color: c.onSurface, fontSize: 19, fontWeight: "900" },
  unavailable: { color: c.error, fontSize: 13, fontWeight: "700" },
}));
