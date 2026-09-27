import { useLocalSearchParams } from "expo-router";
import { CatalogScreen } from "@/src/shop/catalog-screen";
export default function CollectionPage() { const { slug } = useLocalSearchParams<{ slug: string }>(); return <CatalogScreen dimension="collection" reference={slug} />; }
