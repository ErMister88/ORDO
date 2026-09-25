import {
  View,
  ScrollView,
  Pressable,
} from "react-native";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { ArrowLeft } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiGet } from "@/src/api/client";
import { useAuth } from "@/src/auth/auth";
import { Card, EmptyState, Muted } from "@/src/components/ui";
import { getCurrentLocale, LocalizedText as Text, useI18n } from "@/src/i18n";

const LABELS: Record<string, string> = {
  login: "Anmeldung",
  "user.create": "Benutzer angelegt",
  "user.reset": "Passwort zurückgesetzt",
  "company.update": "Firma bearbeitet",
  "price.set": "Preis geändert",
  "order.status": "Bestellstatus geändert",
  "invoice.paid": "Rechnung bezahlt",
  "offer.approve": "Angebot freigegeben",
  "offer.accept": "Angebot angenommen",
};

function fmt(at: string) {
  try {
    const d = new Date(at);
    return d.toLocaleString(getCurrentLocale(), { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return at;
  }
}

export default function Audit() {
  useI18n();
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user } = useAuth();
  const rows = useQuery({ queryKey: ["audit"], queryFn: () => apiGet("/audit"), enabled: user?.role === "admin" });

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 8 }]}>
        <Pressable onPress={() => router.back()} style={styles.backBtn} testID="back-button" hitSlop={8}>
          <ArrowLeft size={20} color={colors.onSurfaceSecondary} weight="bold" />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Audit-Log</Text>
          <Text style={styles.subtitle}>Protokoll sensibler Aktionen</Text>
        </View>
      </View>

      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        {(rows.data ?? []).length === 0 ? (
          <EmptyState title="Noch keine Einträge" subtitle="Aktionen erscheinen hier" />
        ) : (
          (rows.data ?? []).map((r: any, idx: number) => (
            <Card key={idx} testID={`audit-${idx}`}>
              <View style={styles.rowTop}>
                <Text style={styles.action}>{LABELS[r.action] ?? r.action}</Text>
                <Text style={styles.time}>{fmt(r.at)}</Text>
              </View>
              <Muted>
                {r.userEmail ?? r.userId ?? "System"}
                {r.entity ? ` · ${r.entity}` : ""}
                {r.meta?.status ? ` · ${r.meta.status}` : ""}
                {r.meta?.role ? ` · ${r.meta.role}` : ""}
              </Muted>
            </Card>
          ))
        )}
      </ScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingBottom: 14, backgroundColor: c.surface, borderBottomWidth: 1, borderBottomColor: c.divider },
  backBtn: { width: 40, height: 40, borderRadius: 12, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 20, fontWeight: "800", color: c.onSurface },
  subtitle: { fontSize: 13, color: c.muted, marginTop: 1 },
  content: { padding: 20, gap: 10, paddingBottom: 32 },
  rowTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 2 },
  action: { fontSize: 15, fontWeight: "800", color: c.onSurface },
  time: { fontSize: 12, color: c.muted, fontWeight: "600" },
}));
