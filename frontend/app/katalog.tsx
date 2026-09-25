import { useMemo, useState } from "react";
import { Pressable, ScrollView, Text, View, useWindowDimensions } from "react-native";
import { Image } from "expo-image";
import { useQuery } from "@tanstack/react-query";
import { useLocalSearchParams, useRouter } from "expo-router";
import { ArrowLeft, CaretDown, ImageSquare, MagnifyingGlass } from "phosphor-react-native";

import { apiGet, apiPost, fileUrl } from "@/src/api/client";
import { Button, Card, Input, LoadingState, Muted, SectionTitle } from "@/src/components/ui";
import { euro } from "@/src/lib/format";
import { makeStyles, tokens, useTheme } from "@/src/theme";

export default function SalesCatalog() {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const { width } = useWindowDimensions();
  const params = useLocalSearchParams<{ companyId?: string }>();
  const [companyId, setCompanyId] = useState(params.companyId ?? "");
  const [companyOpen, setCompanyOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [brand, setBrand] = useState("");
  const [category, setCategory] = useState("");
  const [availability, setAvailability] = useState<"all" | "available">("all");
  const [qty, setQty] = useState<Record<string, string>>({});

  const products = useQuery({ queryKey: ["sales-catalog-products"], queryFn: () => apiGet("/products") });
  const companies = useQuery({ queryKey: ["companies"], queryFn: () => apiGet("/companies") });
  const categories = useQuery({ queryKey: ["product-categories"], queryFn: () => apiGet("/product-categories") });
  const visibleProducts = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (products.data ?? []).filter((product: any) => {
      if (product.active === false || product.b2bAvailable === false) return false;
      if (brand && product.brand !== brand) return false;
      if (category && product.categoryId !== category) return false;
      if (availability === "available" && product.stock != null && product.stock <= 0) return false;
      return !term || `${product.brand ?? ""} ${product.name ?? ""} ${product.description ?? ""}`.toLowerCase().includes(term);
    });
  }, [availability, brand, category, products.data, search]);
  const quote = useQuery({
    queryKey: ["sales-catalog-prices", companyId, visibleProducts.map((product: any) => `${product.id}:${qty[product.id] ?? product.minimumOrderQuantity ?? 1}`).join(",")],
    queryFn: () => apiPost("/pricing/b2b/quote", {
      companyId,
      items: visibleProducts.map((product: any) => ({
        productId: product.id,
        qty: Math.max(1, Number(qty[product.id] ?? product.minimumOrderQuantity ?? 1) || 1),
      })),
    }),
    enabled: Boolean(companyId && visibleProducts.length),
  });
  const prices = new Map((quote.data?.lines ?? []).map((line: any) => [line.productId, line]));
  const brands = Array.from(new Set((products.data ?? []).map((product: any) => product.brand).filter(Boolean))).sort() as string[];
  const selectedCompany = (companies.data ?? []).find((company: any) => company.id === companyId);
  const columns = width >= 1180 ? 3 : width >= 680 ? 2 : 1;

  if (products.isLoading || companies.isLoading) return <LoadingState label="Produktkatalog wird geladen…" />;
  return (
    <View style={styles.root}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} style={styles.iconButton}><ArrowLeft size={20} color={colors.onSurface} /></Pressable>
        <View style={{ flex: 1 }}><Text style={styles.title}>B2B-Produktkatalog</Text><Text style={styles.subtitle}>Kundenzeigbares Sortiment mit verbindlichen Serverpreisen</Text></View>
      </View>
      <ScrollView contentContainerStyle={styles.content}>
        <Card>
          <SectionTitle>Kunde auswählen</SectionTitle>
          <Pressable testID="catalog-company-picker" style={styles.picker} onPress={() => setCompanyOpen((value) => !value)}>
            <Text style={styles.pickerText}>{selectedCompany?.name ?? "Kunde für individuelle Preise wählen"}</Text><CaretDown size={18} color={colors.muted} />
          </Pressable>
          {companyOpen ? (companies.data ?? []).map((company: any) => <Pressable key={company.id} style={styles.option} onPress={() => { setCompanyId(company.id); setCompanyOpen(false); }}><Text style={styles.optionText}>{company.name}</Text></Pressable>) : null}
          <Muted>Ohne Kundenauswahl werden keine Preise angezeigt. Interne Kosten und Freigabegrenzen werden nicht geladen.</Muted>
        </Card>
        <View style={styles.search}><MagnifyingGlass size={18} color={colors.muted} /><Input value={search} onChangeText={setSearch} placeholder="Produkt, Marke oder Beschreibung suchen" style={{ flex: 1, borderWidth: 0, backgroundColor: "transparent" }} /></View>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
          <Chip label="Alle Marken" active={!brand} onPress={() => setBrand("")} />
          {brands.map((value) => <Chip key={value} label={value} active={brand === value} onPress={() => setBrand(value)} />)}
        </ScrollView>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
          <Chip label="Alle Kategorien" active={!category} onPress={() => setCategory("")} />
          {(categories.data ?? []).map((value: any) => <Chip key={value.id} label={value.name} active={category === value.id} onPress={() => setCategory(value.id)} />)}
          <Chip label="Sofort verfügbar" active={availability === "available"} onPress={() => setAvailability(availability === "available" ? "all" : "available")} />
        </ScrollView>
        <View style={[styles.grid, { gap: 14 }]}>
          {visibleProducts.map((product: any) => {
            const line: any = prices.get(product.id);
            const amount = Math.max(1, Number(qty[product.id] ?? product.minimumOrderQuantity ?? 1) || 1);
            return <View key={product.id} style={{ width: columns === 1 ? "100%" : `${100 / columns - 1.2}%` }}>
              <Card style={styles.productCard}>
                {product.imageUrl ? <Image source={{ uri: fileUrl(product.imageUrl) }} style={styles.image} contentFit="cover" /> : <View style={[styles.image, styles.emptyImage]}><ImageSquare size={34} color={colors.muted} /></View>}
                <Text style={styles.brand}>{product.brand}</Text><Text style={styles.productName}>{product.name}</Text>
                {product.description ? <Muted numberOfLines={3}>{product.description}</Muted> : null}
                <Muted>{[product.packagingUnit, product.packageQuantity, product.contentAmount && product.contentUnit ? `${product.contentAmount} ${product.contentUnit}` : ""].filter(Boolean).join(" · ") || product.unit}</Muted>
                <Text style={styles.price}>{companyId ? (line ? `${euro(line.finalUnitPrice)} / ${product.unit} netto` : "Preis wird ermittelt") : "Kunde für Preis wählen"}</Text>
                {product.stock != null ? <Muted>{product.stock > 0 ? "Verfügbar" : "Derzeit nicht auf Lager"}</Muted> : null}
                <Input value={String(amount)} onChangeText={(value) => setQty((current) => ({ ...current, [product.id]: value }))} keyboardType="decimal-pad" placeholder="Menge" />
                <View style={styles.actions}>
                  <Button title="Angebot" kind="secondary" disabled={!companyId || !line} onPress={() => router.push({ pathname: "/(tabs)/angebote", params: { companyId, productId: product.id, qty: String(amount) } })} style={{ flex: 1 }} />
                  <Button title="Bestellung" disabled={!companyId || !line} onPress={() => router.push({ pathname: "/(tabs)/bestellungen", params: { companyId, productId: product.id, qty: String(amount) } })} style={{ flex: 1 }} />
                </View>
              </Card>
            </View>;
          })}
        </View>
        {visibleProducts.length === 0 ? <Muted>Keine freigegebenen B2B-Produkte für diese Filter.</Muted> : null}
      </ScrollView>
    </View>
  );
}

function Chip({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  const styles = useStyles();
  return <Pressable onPress={onPress} style={[styles.chip, active && styles.chipActive]}><Text style={[styles.chipText, active && styles.chipTextActive]}>{label}</Text></Pressable>;
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: { flexDirection: "row", alignItems: "center", gap: 12, padding: 18, backgroundColor: c.surface, borderBottomWidth: 1, borderBottomColor: c.divider },
  iconButton: { width: 40, height: 40, borderRadius: 12, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 21, fontWeight: "900", color: c.onSurface },
  subtitle: { fontSize: 13, color: c.muted, marginTop: 2 },
  content: { width: "100%", maxWidth: 1320, alignSelf: "center", padding: tokens.spacing.lg, gap: 12, paddingBottom: 36 },
  picker: { minHeight: 46, borderWidth: 1, borderColor: c.border, borderRadius: 12, paddingHorizontal: 14, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  pickerText: { color: c.onSurface, fontWeight: "700" },
  option: { padding: 12, borderBottomWidth: 1, borderBottomColor: c.divider },
  optionText: { color: c.onSurface, fontWeight: "700" },
  search: { flexDirection: "row", alignItems: "center", gap: 8, borderRadius: 14, backgroundColor: c.surface, borderWidth: 1, borderColor: c.border, paddingHorizontal: 12 },
  chips: { gap: 8, paddingVertical: 2 },
  chip: { borderRadius: 999, paddingHorizontal: 14, paddingVertical: 8, borderWidth: 1, borderColor: c.border, backgroundColor: c.surface },
  chipActive: { backgroundColor: c.brandPrimary, borderColor: c.brandPrimary },
  chipText: { color: c.onSurfaceSecondary, fontWeight: "700", fontSize: 13 },
  chipTextActive: { color: c.onBrandPrimary },
  grid: { flexDirection: "row", flexWrap: "wrap" },
  productCard: { minHeight: 500, gap: 8 },
  image: { width: "100%", height: 190, borderRadius: 14 },
  emptyImage: { backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  brand: { color: c.brandPrimary, fontSize: 12, fontWeight: "900", textTransform: "uppercase", letterSpacing: 0.6 },
  productName: { color: c.onSurface, fontSize: 18, fontWeight: "900" },
  price: { color: c.onSurface, fontSize: 16, fontWeight: "900", marginTop: 4 },
  actions: { flexDirection: "row", gap: 8, marginTop: "auto" },
}));
