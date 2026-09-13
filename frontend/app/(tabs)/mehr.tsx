import { View, Text, ScrollView, Pressable } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { SignOut, FileText, Receipt, Export, Package, UsersThree, Lock, CaretRight, ArrowsClockwise, ClockCounterClockwise, Storefront, Scales, ShieldCheck } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { useAuth } from "@/src/auth/auth";
import { apiGet } from "@/src/api/client";
import { euro, num, dateDE } from "@/src/lib/format";
import { shareInvoicePdf } from "@/src/lib/pdf";
import { ScreenHeader } from "@/src/components/screen-header";
import { Card, InfoRow, StatusBadge, SectionTitle, EmptyState, Muted } from "@/src/components/ui";

export default function Mehr() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { user, signOut } = useAuth();
  const router = useRouter();

  const contracts = useQuery({ queryKey: ["contracts"], queryFn: () => apiGet("/contracts") });
  const invoices = useQuery({ queryKey: ["invoices"], queryFn: () => apiGet("/invoices") });
  const products = useQuery({ queryKey: ["products"], queryFn: () => apiGet("/products") });
  const company = useQuery({
    queryKey: ["company", user?.companyId],
    queryFn: () => apiGet(`/companies/${user?.companyId}`),
    enabled: !!user?.companyId,
  });

  const prodMap: Record<string, any> = {};
  (products.data ?? []).forEach((p: any) => (prodMap[p.id] = p));

  const onSignOut = async () => {
    await signOut();
    router.replace("/login");
  };

  return (
    <View style={styles.root}>
      <ScreenHeader title="Mehr" subtitle={user?.name} />
      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        <View style={styles.sectionHead}>
          <FileText size={18} color={colors.brandPrimary} weight="fill" />
          <SectionTitle>Verträge</SectionTitle>
        </View>
        {(contracts.data ?? []).length === 0 ? (
          <EmptyState title="Keine Verträge" />
        ) : (
          (contracts.data ?? []).map((ct: any) => {
            const p = prodMap[ct.productId];
            return (
              <Card key={ct.id} testID={`contract-${ct.id}`}>
                <Text style={styles.title}>{ct.machine || "Kaffeeliefervertrag"}</Text>
                <Muted>{ct.id}</Muted>
                {p ? <InfoRow label="Produkt" value={`${p.brand} ${p.name}`} /> : null}
                <InfoRow label="Preis" value={`${euro(ct.price)}/kg`} />
                <InfoRow label="Mindestabnahme" value={`${num(ct.minQtyMonth)} kg/Monat`} />
                <InfoRow label="Start" value={dateDE(ct.start)} />
                <InfoRow label="Laufzeit" value={`${ct.termMonths} Monate`} />
                {ct.machineRate ? <InfoRow label="Maschine" value={`${euro(ct.machineRate)}/Monat`} /> : null}
              </Card>
            );
          })
        )}

        <View style={[styles.sectionHead, { marginTop: 8 }]}>
          <Receipt size={18} color={colors.brandPrimary} weight="fill" />
          <SectionTitle>Rechnungen</SectionTitle>
        </View>
        {(invoices.data ?? []).length === 0 ? (
          <EmptyState title="Keine Rechnungen" />
        ) : (
          (invoices.data ?? []).map((inv: any) => (
            <Card key={inv.id} testID={`invoice-${inv.id}`}>
              <View style={styles.invTop}>
                <Text style={styles.title}>{inv.id}</Text>
                <StatusBadge status={inv.status} />
              </View>
              <Muted>
                {dateDE(inv.date)} · {euro(inv.amount)}
              </Muted>
              <Pressable
                testID={`invoice-pdf-${inv.id}`}
                style={styles.pdfBtn}
                onPress={() => shareInvoicePdf(inv, company.data)}
              >
                <Export size={16} color={colors.brandPrimary} weight="bold" />
                <Text style={styles.pdfText}>Öffnen / Herunterladen</Text>
              </Pressable>
            </Card>
          ))
        )}

        {user?.role === "admin" && (
          <>
            <View style={[styles.sectionHead, { marginTop: 8 }]}>
              <UsersThree size={18} color={colors.brandPrimary} weight="fill" />
              <SectionTitle>Verwaltung</SectionTitle>
            </View>
            <Pressable testID="link-produkte" onPress={() => router.push("/produkte")}>
              <Card>
                <View style={styles.linkRow}>
                  <Package size={20} color={colors.brandPrimary} weight="bold" />
                  <Text style={styles.linkText}>Produkte & Preise</Text>
                  <CaretRight size={18} color={colors.muted} />
                </View>
              </Card>
            </Pressable>
            <Pressable testID="link-benutzer" onPress={() => router.push("/benutzer")}>
              <Card>
                <View style={styles.linkRow}>
                  <UsersThree size={20} color={colors.brandPrimary} weight="bold" />
                  <Text style={styles.linkText}>Benutzer verwalten</Text>
                  <CaretRight size={18} color={colors.muted} />
                </View>
              </Card>
            </Pressable>
            <Pressable testID="link-abos" onPress={() => router.push("/abos")}>
              <Card>
                <View style={styles.linkRow}>
                  <ArrowsClockwise size={20} color={colors.brandPrimary} weight="bold" />
                  <Text style={styles.linkText}>Abo-Bestellungen</Text>
                  <CaretRight size={18} color={colors.muted} />
                </View>
              </Card>
            </Pressable>
            <Pressable testID="link-audit" onPress={() => router.push("/audit")}>
              <Card>
                <View style={styles.linkRow}>
                  <ClockCounterClockwise size={20} color={colors.brandPrimary} weight="bold" />
                  <Text style={styles.linkText}>Audit-Log</Text>
                  <CaretRight size={18} color={colors.muted} />
                </View>
              </Card>
            </Pressable>
            <Pressable testID="link-shop-admin" onPress={() => router.push("/shop-admin")}>
              <Card>
                <View style={styles.linkRow}>
                  <Storefront size={20} color={colors.brandPrimary} weight="bold" />
                  <Text style={styles.linkText}>Shop-Verwaltung</Text>
                  <CaretRight size={18} color={colors.muted} />
                </View>
              </Card>
            </Pressable>
          </>
        )}

        <View style={[styles.sectionHead, { marginTop: 8 }]}>
          <Lock size={18} color={colors.brandPrimary} weight="fill" />
          <SectionTitle>Konto</SectionTitle>
        </View>
        <Pressable testID="link-passwort" onPress={() => router.push("/passwort-aendern")}>
          <Card>
            <View style={styles.linkRow}>
              <Lock size={20} color={colors.brandPrimary} weight="bold" />
              <Text style={styles.linkText}>Passwort ändern</Text>
              <CaretRight size={18} color={colors.muted} />
            </View>
          </Card>
        </Pressable>

        <View style={[styles.sectionHead, { marginTop: 8 }]}>
          <Scales size={18} color={colors.brandPrimary} weight="fill" />
          <SectionTitle>Rechtliches</SectionTitle>
        </View>
        <Pressable testID="link-impressum" onPress={() => router.push("/legal/impressum")}>
          <Card>
            <View style={styles.linkRow}>
              <FileText size={20} color={colors.brandPrimary} weight="bold" />
              <Text style={styles.linkText}>Impressum</Text>
              <CaretRight size={18} color={colors.muted} />
            </View>
          </Card>
        </Pressable>
        <Pressable testID="link-datenschutz" onPress={() => router.push("/legal/datenschutz")}>
          <Card>
            <View style={styles.linkRow}>
              <ShieldCheck size={20} color={colors.brandPrimary} weight="bold" />
              <Text style={styles.linkText}>Datenschutz</Text>
              <CaretRight size={18} color={colors.muted} />
            </View>
          </Card>
        </Pressable>
        <Pressable testID="link-agb" onPress={() => router.push("/legal/agb")}>
          <Card>
            <View style={styles.linkRow}>
              <FileText size={20} color={colors.brandPrimary} weight="bold" />
              <Text style={styles.linkText}>AGB</Text>
              <CaretRight size={18} color={colors.muted} />
            </View>
          </Card>
        </Pressable>
        <Pressable testID="link-widerruf" onPress={() => router.push("/legal/widerruf")}>
          <Card>
            <View style={styles.linkRow}>
              <FileText size={20} color={colors.brandPrimary} weight="bold" />
              <Text style={styles.linkText}>Widerrufsbelehrung</Text>
              <CaretRight size={18} color={colors.muted} />
            </View>
          </Card>
        </Pressable>

        <Pressable style={styles.logout} onPress={onSignOut} testID="logout-button">
          <SignOut size={18} color={colors.error} weight="bold" />
          <Text style={styles.logoutText}>Abmelden</Text>
        </Pressable>
      </ScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  content: { padding: 20, gap: 12, paddingBottom: 32 },
  sectionHead: { flexDirection: "row", alignItems: "center", gap: 8 },
  title: { fontSize: 15, fontWeight: "800", color: c.onSurface },
  invTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  pdfBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    marginTop: 6,
    paddingVertical: 10,
    borderRadius: 12,
    backgroundColor: c.brandTertiary,
  },
  pdfText: { color: c.brandPrimary, fontWeight: "700", fontSize: 14 },
  linkRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  linkText: { flex: 1, fontSize: 15, fontWeight: "700", color: c.onSurface },
  logout: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    marginTop: 16,
    paddingVertical: 14,
    borderRadius: 14,
    backgroundColor: c.surface,
    borderWidth: 1,
    borderColor: c.border,
  },
  logoutText: { color: c.error, fontWeight: "700", fontSize: 15 },
}));
