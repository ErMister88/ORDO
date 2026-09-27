import { useLocalSearchParams } from "expo-router";
import { CatalogScreen } from "@/src/shop/catalog-screen";
export default function CategoryPage() { const { slug } = useLocalSearchParams<{ slug: string }>(); return <CatalogScreen dimension="category" reference={slug} />; }
