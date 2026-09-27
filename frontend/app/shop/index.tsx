import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { Image } from "expo-image";
import { ArrowLeft, ImageSquare, ShoppingCart } from "phosphor-react-native";
import { Pressable, ScrollView, View, useWindowDimensions } from "react-native";

import { apiGet, fileUrl } from "@/src/api/client";
import { Card, EmptyState, LoadingState, ErrorState, PageContainer, SectionTitle, Muted } from "@/src/components/ui";
import { LocalizedText as Text, useI18n } from "@/src/i18n";
import { ProductCard, SearchBox } from "@/src/shop/commerce";
import { useCart } from "@/src/shop/cart";
import { useCommerceMetadata } from "@/src/shop/seo";
import { makeStyles, tokens, useTheme } from "@/src/theme";

export default function ShopHome() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { width } = useWindowDimensions();
  const router = useRouter();
  const cart = useCart();
  const { tf } = useI18n();
  const discovery = useQuery({ queryKey: ["shop-discovery"], queryFn: () => apiGet("/shop/discovery") });
  const curated = useQuery({ queryKey: ["shop-home-products"], queryFn: () => apiGet("/shop/catalog?sort=newest&pageSize=6") });
  const roots = (discovery.data?.categories ?? []).filter((item: any) => !item.parentId);
  const hasConfiguredHero = (discovery.data?.blocks ?? []).some((block: any) => block.type === "hero" && block.active !== false);
  useCommerceMetadata({ title: "ORDO Shop", description: tf("Entdecke unser Sortiment nach Kategorien, Marken und Regionen.", {}), path: "/shop" });
  return <View style={styles.root}>
    <View style={styles.header}>
      <Pressable onPress={() => router.back()} style={styles.iconButton}><ArrowLeft size={21} color={colors.onSurface} /></Pressable>
      <View style={{ flex: 1 }}><Text style={styles.title}>Shop</Text><Muted>Entdecke unser Sortiment</Muted></View>
      <Pressable onPress={() => router.push("/shop/warenkorb")} style={styles.cartButton}><ShoppingCart size={22} color={colors.onBrandPrimary} weight="bold" />{cart.count ? <Text style={styles.badge}>{cart.count}</Text> : null}</Pressable>
    </View>
    <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <PageContainer style={styles.page}>
        <SearchBox />
        {!hasConfiguredHero ? <View style={styles.hero}><Text style={styles.eyebrow}>ORDO COMMERCE</Text><Text style={styles.heroTitle}>Gute Produkte. Klar sortiert.</Text><Text style={styles.heroText}>Starte mit einer Kategorie oder finde Produkte direkt über die Suche.</Text></View> : null}
        {discovery.isLoading ? <LoadingState /> : discovery.isError ? <ErrorState message="Shop konnte nicht geladen werden" onRetry={() => { void discovery.refetch(); }} /> : <>
          <SectionTitle>Kategorien</SectionTitle>
          {roots.length ? <View style={styles.categoryGrid}>{roots.map((category: any) => <Pressable key={category.id} style={[styles.categoryCard, width >= 768 && styles.categoryCardWide]} onPress={() => router.push(`/shop/kategorie/${category.slug || category.id}`)}>
            {category.imageUrl ? <Image source={{ uri: fileUrl(category.imageUrl) }} style={styles.categoryImage} contentFit="cover" /> : <View style={[styles.categoryImage, styles.emptyImage]}><ImageSquare size={28} color={colors.muted} /></View>}
            <View style={{ flex: 1 }}><Text style={styles.categoryName}>{category.name}</Text>{category.description ? <Muted numberOfLines={2}>{category.description}</Muted> : null}</View>
          </Pressable>)}</View> : <EmptyState title="Kategorien werden vorbereitet" subtitle="Nutze bis dahin Suche und Empfehlungen." />}
          {(discovery.data?.blocks ?? []).map((block: any) => <HomepageBlock key={block.id} block={block} discovery={discovery.data} />)}
          <SectionTitle>Neu im Shop</SectionTitle>
          <View style={styles.productGrid}>{(curated.data?.items ?? []).map((product: any) => <ProductCard key={product.id} product={product} />)}</View>
          <SectionTitle>Shoppen nach Marke</SectionTitle>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>{(discovery.data?.brands ?? []).map((brand: any) => <Pressable key={brand.id} style={styles.chip} onPress={() => router.push(`/marken/${brand.slug || brand.id}`)}><Text style={styles.chipText}>{brand.name}</Text></Pressable>)}</ScrollView>
          {(discovery.data?.regions ?? []).length ? <><SectionTitle>Shoppen nach Region</SectionTitle><ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>{discovery.data.regions.map((region: any) => <Pressable key={region.id} style={styles.chip} onPress={() => router.push(`/regionen/${region.slug || region.id}`)}><Text style={styles.chipText}>{region.name}</Text></Pressable>)}</ScrollView></> : null}
        </>}
      </PageContainer>
    </ScrollView>
  </View>;
}

function HomepageBlock({ block, discovery }: { block: any; discovery: any }) {
  const styles = useStyles();
  const router = useRouter();
  if (block.type === "hero" || block.type === "text_image") return <Card style={styles.editorial}>{block.imageUrl ? <Image source={{ uri: fileUrl(block.imageUrl) }} style={styles.blockImage} contentFit="cover" /> : null}<View style={{ flex: 1 }}>{block.title ? <Text style={styles.blockTitle}>{block.title}</Text> : null}{block.text ? <Muted>{block.text}</Muted> : null}</View></Card>;
  if (block.type === "products") return <View><SectionTitle>{block.title || "Ausgewählte Produkte"}</SectionTitle><View style={styles.productGrid}>{(block.products ?? []).map((product: any) => <ProductCard key={product.id} product={product} />)}</View></View>;
  const entries = block.type === "brands" ? discovery.brands : block.type === "collection" ? discovery.collections.filter((item: any) => !block.targetId || item.id === block.targetId) : discovery.categories.filter((item: any) => !block.targetId || item.id === block.targetId);
  return <View><SectionTitle>{block.title || (block.type === "brands" ? "Marken" : block.type === "collection" ? "Collection" : "Kategorien")}</SectionTitle><View style={styles.chips}>{entries.map((entry: any) => <Pressable key={entry.id} style={styles.chip} onPress={() => router.push(block.type === "brands" ? `/marken/${entry.slug || entry.id}` : block.type === "collection" ? `/collections/${entry.slug || entry.id}` : `/shop/kategorie/${entry.slug || entry.id}`)}><Text style={styles.chipText}>{entry.name}</Text></Pressable>)}</View></View>;
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingTop: 14, paddingBottom: 12, backgroundColor: c.surface, borderBottomWidth: 1, borderBottomColor: c.divider },
  iconButton: { width: 44, height: 44, borderRadius: 12, alignItems: "center", justifyContent: "center", backgroundColor: c.surfaceTertiary },
  cartButton: { minWidth: 48, height: 44, borderRadius: 12, alignItems: "center", justifyContent: "center", backgroundColor: c.brandPrimary },
  badge: { position: "absolute", right: -4, top: -5, minWidth: 19, textAlign: "center", color: c.onError, backgroundColor: c.error, borderRadius: 10, overflow: "hidden", fontSize: 11, fontWeight: "900" },
  title: { fontSize: 22, color: c.onSurface, fontWeight: "900" },
  content: { padding: tokens.spacing.lg, paddingBottom: 48 }, page: { gap: 18 },
  hero: { backgroundColor: c.surfaceInverse, padding: 28, borderRadius: 18, gap: 8 },
  eyebrow: { color: c.inverseAccent, fontWeight: "900", fontSize: 11, letterSpacing: 1.5 },
  heroTitle: { color: c.onSurfaceInverse, fontSize: 30, fontWeight: "900" }, heroText: { color: c.inverseMuted, fontSize: 15, lineHeight: 22 },
  categoryGrid: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
  categoryCard: { width: "100%", flexDirection: "row", alignItems: "center", gap: 14, backgroundColor: c.surface, borderRadius: 14, padding: 12, borderWidth: 1, borderColor: c.border },
  categoryCardWide: { width: "48.8%", flexGrow: 1 }, categoryImage: { width: 78, height: 78, borderRadius: 12, backgroundColor: c.surfaceTertiary }, emptyImage: { alignItems: "center", justifyContent: "center" },
  categoryName: { color: c.onSurface, fontSize: 18, fontWeight: "900" },
  productGrid: { flexDirection: "row", flexWrap: "wrap", gap: 14 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 9 }, chip: { paddingHorizontal: 15, paddingVertical: 10, borderRadius: 999, backgroundColor: c.surface, borderWidth: 1, borderColor: c.border }, chipText: { color: c.onSurface, fontWeight: "800" },
  editorial: { flexDirection: "row", alignItems: "center", gap: 16 }, blockImage: { width: 120, height: 90, borderRadius: 12 }, blockTitle: { color: c.onSurface, fontSize: 20, fontWeight: "900", marginBottom: 5 },
}));
