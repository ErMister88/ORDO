import {
  Pressable,
  ScrollView,
  View,
} from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useLocalSearchParams, useRouter } from "expo-router";
import { ArrowLeft, ArrowRight } from "phosphor-react-native";

import { apiGet } from "@/src/api/client";
import { Card, ErrorState, InfoRow, KPICard, LoadingState, Muted, PageContainer, SectionTitle } from "@/src/components/ui";
import { dateDE, euro, num } from "@/src/lib/format";
import { makeStyles, tokens, useTheme } from "@/src/theme";
import { LocalizedText as Text, useI18n } from "@/src/i18n";

export default function SalesRepDetail() {
  useI18n();
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const { id, period = "month" } = useLocalSearchParams<{ id: string; period?: string }>();
  const detail = useQuery({ queryKey: ["sales-rep-detail", id, period], queryFn: () => apiGet(`/dashboard/sales/${id}?period=${period}`) });
  const salesStaff = useQuery({ queryKey: ["sales-staff"], queryFn: () => apiGet("/staff/sales") });
  return <View style={styles.root}>
    <View style={styles.header}><Pressable onPress={() => router.back()} style={styles.back}><ArrowLeft size={20} color={colors.onSurface} /></Pressable><View><Text style={styles.title}>Vertriebsanalyse</Text><Text style={styles.subtitle}>Zeitraum wie im Dashboard: {period === "month" ? "Monat" : period === "quarter" ? "Quartal" : "Jahr"}</Text></View></View>
    <ScrollView contentContainerStyle={styles.content}><PageContainer>
      {detail.isLoading ? <LoadingState label="Vertriebsdaten werden geladen…" /> : detail.isError ? <ErrorState onRetry={() => detail.refetch()} /> : <>
        <SectionTitle>{(salesStaff.data ?? []).find((row: any) => row.id === id)?.name || detail.data.salesRep.name}</SectionTitle>
        <View style={styles.kpis}><KPICard label="Umsatz" value={euro(detail.data.salesRep.revenue)} /><KPICard label="Bestellungen" value={num(detail.data.salesRep.orders)} /><KPICard label="Kunden" value={num(detail.data.salesRep.activeCustomers)} /><KPICard label="Neukunden" value={num(detail.data.salesRep.newCustomers)} /><KPICard label="Menge" value={num(detail.data.salesRep.quantity)} />{detail.data.salesRep.marginDataComplete ? <KPICard label="Deckungsbeitrag" value={euro(detail.data.salesRep.margin)} accent="success" /> : null}</View>
        {!detail.data.salesRep.marginDataComplete ? <Muted>Deckungsbeitrag wird nicht ausgewiesen, weil historische Kostendaten nicht vollständig sind.</Muted> : null}
        <SectionTitle>Kunden im Zeitraum</SectionTitle>
        {detail.data.customers.map((customer: any) => <Pressable key={customer.companyId} onPress={() => router.push({ pathname: "/kunde/[id]", params: { id: customer.companyId, period } })}><Card><View style={styles.customerTop}><View style={{ flex: 1 }}><Text style={styles.customerName}>{customer.name}</Text><Muted>{customer.lastOrderAt ? `Letzter Auftrag ${dateDE(customer.lastOrderAt)}` : "Kein Auftrag im Zeitraum"}</Muted></View><ArrowRight size={18} color={colors.muted} /></View><InfoRow label="Umsatz" value={euro(customer.revenue)} /><InfoRow label="Bestellungen" value={num(customer.orders)} /><InfoRow label="Menge" value={num(customer.quantity)} />{customer.marginDataComplete ? <InfoRow label="Deckungsbeitrag" value={euro(customer.margin)} /> : null}</Card></Pressable>)}
      </>}
    </PageContainer></ScrollView>
  </View>;
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: { flexDirection: "row", alignItems: "center", gap: 12, padding: 18, backgroundColor: c.surface, borderBottomWidth: 1, borderBottomColor: c.divider },
  back: { width: 40, height: 40, borderRadius: 12, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 21, fontWeight: "900", color: c.onSurface },
  subtitle: { fontSize: 13, color: c.muted },
  content: { padding: tokens.spacing.lg, paddingBottom: 36 },
  kpis: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  customerTop: { flexDirection: "row", alignItems: "center", gap: 10 },
  customerName: { fontSize: 16, fontWeight: "900", color: c.onSurface },
}));
