import { useLocalSearchParams } from "expo-router";
import { CatalogScreen } from "@/src/shop/catalog-screen";
export default function BrandPage() { const { slug } = useLocalSearchParams<{ slug: string }>(); return <CatalogScreen dimension="brand" reference={slug} />; }
