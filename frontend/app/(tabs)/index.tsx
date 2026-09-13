import { useCallback } from "react";
import { View, Text, ScrollView, RefreshControl, Pressable } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { SignOut, Warning, ArrowRight, Coffee, Package, UsersThree } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { useAuth } from "@/src/auth/auth";
import { apiGet } from "@/src/api/client";
import { euro, num } from "@/src/lib/format";
import { ScreenHeader, HeaderButton } from "@/src/components/screen-header";
import { Card, KPICard, SectionTitle, InfoRow, Muted } from "@/src/components/ui";

export default function Dashboard() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { user, signOut } = useAuth();
  const router = useRouter();

  const { data, isLoading, refetch, isRefetching } = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => apiGet("/dashboard"),
  });

  const onSignOut = useCallback(async () => {
    await signOut();
    router.replace("/login");
  }, []);

  const roleLabel =
    user?.role === "admin" ? "Administrator" : user?.role === "sales" ? "Vertrieb" : "Kundenportal";

  return (
    <View style={styles.root}>
      <ScreenHeader
        title={user?.role === "customer" ? "Willkommen" : "Übersicht"}
        subtitle={`${user?.name} · ${roleLabel}`}
        right={
          <View style={styles.headerBtns}>
            {user?.role === "admin" && (
              <HeaderButton onPress={() => router.push("/produkte")} testID="products-button">
                <Package size={20} color={colors.onSurfaceSecondary} weight="bold" />
              </HeaderButton>
            )}
            {user?.role === "admin" && (
              <HeaderButton onPress={() => router.push("/benutzer")} testID="users-button">
                <UsersThree size={20} color={colors.onSurfaceSecondary} weight="bold" />
              </HeaderButton>
            )}
            <HeaderButton onPress={onSignOut} testID="logout-button">
              <SignOut size={20} color={colors.onSurfaceSecondary} weight="bold" />
            </HeaderButton>
          </View>
        }
      />

      <ScrollView
        contentContainerStyle={styles.content}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={colors.brandPrimary} />
        }
      >
        {isLoading || !data ? (
          <Muted>Lädt…</Muted>
        ) : data.role === "customer" ? (
          <CustomerDash data={data} />
        ) : (
          <StaffDash data={data} onCustomer={(id: string) => router.push(`/kunde/${id}`)} />
        )}
      </ScrollView>
    </View>
  );
}

function StaffDash({ data, onCustomer }: { data: any; onCustomer: (id: string) => void }) {
  const styles = useStyles();
  const { colors } = useTheme();
  return (
    <>
      {/* Hero metric */}
      <View style={styles.hero} testID="hero-revenue">
        <View style={styles.heroIcon}>
          <Coffee size={24} color={colors.onBrand} weight="fill" />
        </View>
        <Text style={styles.heroLabel}>Umsatz diesen Monat</Text>
        <Text style={styles.heroValue}>{euro(data.revenueMonth)}</Text>
        <Text style={styles.heroSub}>{data.ordersCount} Bestellungen gesamt</Text>
      </View>

      <View style={styles.kpiGrid}>
        <KPICard testID="kpi-customers" label="Aktive Kunden" value={num(data.activeCustomers)} accent="primary" />
        <KPICard testID="kpi-kg" label="kg / Monat" value={num(data.totalKg)} accent="primary" />
      </View>
      <View style={styles.kpiGrid}>
        <KPICard testID="kpi-offers" label="Offene Angebote" value={num(data.openOffers)} accent="warning" />
        <KPICard testID="kpi-invoices" label="Offene Rechnungen" value={euro(data.openInvoices)} accent="error" />
      </View>

      {data.pendingApprovals > 0 && (
        <Pressable onPress={() => {}} testID="approval-alert">
          <View style={[styles.alert, { borderColor: colors.warning }]}>
            <View style={[styles.alertIcon, { backgroundColor: colors.warning }]}>
              <Warning size={18} color={colors.onWarning} weight="fill" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.alertTitle}>Preisfreigabe erforderlich</Text>
              <Text style={styles.alertSub}>
                {data.pendingApprovals} Angebot(e) warten auf Ihre Freigabe
              </Text>
            </View>
          </View>
        </Pressable>
      )}

      {data.followups?.length > 0 && (
        <>
          <SectionTitle style={{ marginTop: 8 }}>Kunden nachfassen</SectionTitle>
          <Card style={{ padding: 6 }}>
            {data.followups.map((f: any, i: number) => (
              <Pressable
                key={f.companyId}
                onPress={() => onCustomer(f.companyId)}
                testID={`followup-${f.companyId}`}
                style={[styles.followRow, i > 0 && styles.followRowBorder]}
              >
                <View style={[styles.overdueDot, { backgroundColor: colors.error }]} />
                <View style={{ flex: 1 }}>
                  <Text style={styles.followName}>{f.name}</Text>
                  <Text style={styles.followSub}>
                    {f.days == null ? "Noch keine Bestellung" : `${f.days} Tage seit letzter Bestellung`}
                  </Text>
                </View>
                <ArrowRight size={18} color={colors.muted} />
              </Pressable>
            ))}
          </Card>
        </>
      )}
    </>
  );
}

function CustomerDash({ data }: { data: any }) {
  const styles = useStyles();
  const { colors } = useTheme();
  return (
    <>
      <View style={styles.hero} testID="hero-customer">
        <View style={styles.heroIcon}>
          <Coffee size={24} color={colors.onBrand} weight="fill" />
        </View>
        <Text style={styles.heroLabel}>{data.companyName}</Text>
        <Text style={styles.heroValue}>{num(data.monthlyKg)} kg</Text>
        <Text style={styles.heroSub}>Ihr Monatsverbrauch</Text>
      </View>

      <View style={styles.kpiGrid}>
        <KPICard label="Mindestabnahme" value={`${num(data.minQtyMonth)} kg`} accent="primary" />
        <KPICard
          label="Offene Rechnungen"
          value={euro(data.openInvoices)}
          accent={data.openInvoices > 0 ? "warning" : "success"}
        />
      </View>

      {data.contract && (
        <>
          <SectionTitle style={{ marginTop: 8 }}>Ihr Vertrag</SectionTitle>
          <Card testID="customer-contract-card">
            <Text style={styles.cardTitle}>{data.contract.machine || "Kaffeeliefervertrag"}</Text>
            <InfoRow label="Preis" value={`${euro(data.contract.price)}/kg`} />
            <InfoRow label="Mindestabnahme" value={`${num(data.contract.minQtyMonth)} kg/Monat`} />
            <InfoRow label="Laufzeit" value={`${data.contract.termMonths} Monate`} />
            {data.contract.serviceRate ? (
              <InfoRow label="Service" value={`${euro(data.contract.serviceRate)}/Monat`} />
            ) : null}
          </Card>
        </>
      )}
    </>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  content: { padding: 20, paddingBottom: 36, gap: 16 },
  headerBtns: { flexDirection: "row", gap: 8 },
  hero: {
    backgroundColor: c.brand,
    borderRadius: 24,
    padding: 22,
    gap: 2,
    borderWidth: 1,
    borderColor: c.border,
  },
  heroIcon: {
    width: 46,
    height: 46,
    borderRadius: 14,
    backgroundColor: "rgba(255,255,255,0.15)",
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 12,
  },
  heroLabel: { fontSize: 14, color: "rgba(255,255,255,0.78)", fontWeight: "600" },
  heroValue: { fontSize: 38, fontWeight: "800", color: c.onBrand, letterSpacing: -1, marginTop: 2 },
  heroSub: { fontSize: 13, color: "rgba(255,255,255,0.68)", marginTop: 6 },
  kpiGrid: { flexDirection: "row", gap: 14 },
  alert: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: c.surface,
    borderRadius: 18,
    padding: 16,
    borderWidth: 1,
  },
  alertIcon: { width: 36, height: 36, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  alertTitle: { fontSize: 15, fontWeight: "700", color: c.onSurface },
  alertSub: { fontSize: 13, color: c.muted, marginTop: 2 },
  cardTitle: { fontSize: 16, fontWeight: "800", color: c.onSurface, marginBottom: 4 },
  followRow: { flexDirection: "row", alignItems: "center", gap: 12, padding: 12 },
  followRowBorder: { borderTopWidth: 1, borderTopColor: c.divider },
  overdueDot: { width: 8, height: 8, borderRadius: 999 },
  followName: { fontSize: 15, fontWeight: "700", color: c.onSurface },
  followSub: { fontSize: 13, color: c.muted, marginTop: 1 },
}));
