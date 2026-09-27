import { useLocalSearchParams } from "expo-router";
import { CatalogScreen } from "@/src/shop/catalog-screen";
export default function SearchPage() { const { q = "" } = useLocalSearchParams<{ q?: string }>(); return <CatalogScreen dimension="search" query={q} />; }
