import { useMemo } from "react";
import { Pressable, ScrollView, View } from "react-native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { ArrowLeft, Bell, Check } from "phosphor-react-native";

import { apiGet, apiPost } from "@/src/api/client";
import { Button, Card, EmptyState, Muted } from "@/src/components/ui";
import { LocalizedText as Text, useI18n } from "@/src/i18n";
import { makeStyles, useTheme } from "@/src/theme";

type NotificationRow = {
  id: string;
  type: string;
  titleKey: string;
  messageKey: string;
  parameters?: Record<string, string | number>;
  createdAt: string;
  readAt?: string | null;
};

export default function Benachrichtigungen() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { locale, tf } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const rows = useQuery<NotificationRow[]>({ queryKey: ["notifications"], queryFn: () => apiGet("/notifications") });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["notifications"] });
  const readOne = useMutation({ mutationFn: (id: string) => apiPost(`/notifications/${id}/read`), onSuccess: refresh });
  const readAll = useMutation({ mutationFn: () => apiPost("/notifications/read-all"), onSuccess: refresh });
  const formatter = useMemo(() => new Intl.DateTimeFormat(locale, { dateStyle: "short", timeStyle: "short" }), [locale]);
  const unread = (rows.data ?? []).filter((row) => !row.readAt).length;

  return <View style={styles.root}>
    <View style={styles.header}>
      <Pressable onPress={() => router.back()} style={styles.back}><ArrowLeft size={22} color={colors.onSurface} /></Pressable>
      <View style={{ flex: 1 }}><Text style={styles.title}>Benachrichtigungen</Text><Muted>{tf("{count} ungelesen", { count: unread })}</Muted></View>
      {unread ? <Button title="Alle gelesen" kind="secondary" loading={readAll.isPending} onPress={() => readAll.mutate()} /> : null}
    </View>
    <ScrollView contentContainerStyle={styles.content}>
      {rows.isError ? <Card><Text style={styles.error}>Benachrichtigungen konnten nicht geladen werden</Text></Card> : null}
      {!rows.isLoading && !(rows.data ?? []).length ? <EmptyState title="Keine Benachrichtigungen" subtitle="Wichtige Hinweise erscheinen hier." /> : null}
      {(rows.data ?? []).map((row) => <Card key={row.id} style={!row.readAt ? styles.unread : undefined}>
        <View style={styles.row}>
          <Bell size={21} color={colors.brandPrimary} weight={!row.readAt ? "fill" : "regular"} />
          <View style={{ flex: 1 }}>
            <Text style={styles.rowTitle}>{tf(row.titleKey, row.parameters ?? {})}</Text>
            <Muted>{tf(row.messageKey, row.parameters ?? {})}</Muted>
            <Muted>{formatter.format(new Date(row.createdAt))}</Muted>
          </View>
          {!row.readAt ? <Pressable accessibilityLabel="Als gelesen markieren" onPress={() => readOne.mutate(row.id)} style={styles.read}><Check size={18} color={colors.success} /></Pressable> : null}
        </View>
      </Card>)}
    </ScrollView>
  </View>;
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: { padding: 20, paddingTop: 28, backgroundColor: c.surface, borderBottomWidth: 1, borderBottomColor: c.divider, flexDirection: "row", alignItems: "center", gap: 12, flexWrap: "wrap" },
  back: { width: 42, height: 42, borderRadius: 11, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 22, fontWeight: "900", color: c.onSurface },
  content: { padding: 20, gap: 12, paddingBottom: 40, width: "100%", maxWidth: 900, alignSelf: "center" },
  row: { flexDirection: "row", gap: 12, alignItems: "flex-start" },
  rowTitle: { fontSize: 15, fontWeight: "800", color: c.onSurface, marginBottom: 4 },
  unread: { borderColor: c.brandPrimary, borderWidth: 1 },
  read: { width: 36, height: 36, borderRadius: 10, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  error: { color: c.error, fontWeight: "700" },
}));
