import { useMemo } from "react";
import { Pressable, ScrollView, View } from "react-native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { ArrowLeft, ArrowsClockwise, CheckCircle, Warning, XCircle } from "phosphor-react-native";

import { apiGet, apiPost } from "@/src/api/client";
import { Button, Card, EmptyState, Muted, SectionTitle } from "@/src/components/ui";
import { LocalizedText as Text, useI18n } from "@/src/i18n";
import { makeStyles, useTheme } from "@/src/theme";

type CapabilityState = "available" | "degraded" | "unavailable" | "not_configured";
type Capability = { status: CapabilityState; message: string; expectedVersion?: number; appliedVersion?: number | null };
type StatusPayload = { status: "ready" | "not_ready"; ready: boolean; tenantId?: string | null; capabilities: Record<string, Capability> };
type Problem = {
  type: string;
  id?: string;
  status?: string;
  occurredAt?: string;
  errorReference?: string;
  message?: string;
  retryAllowed: boolean;
  reviewRequired: boolean;
};
type Job = { id: string; jobType: string; status: string; attempts: number; updatedAt?: string; lastErrorReference?: string; retryAllowed?: boolean };
type Reconciliation = { id: string; status: string; finishedAt: string; issueCount: number; counts: Record<string, number> } | null;
type CommunicationStatus = {
  mail: { pending: number; failed: number; dead: number };
  jobs: { dead: number };
  storage: { errors: number };
  worker: { status: string; lastSeenAt?: string | null };
};

const CAPABILITY_LABELS: Record<string, string> = {
  database: "Datenbank",
  schema: "Datenbankschema",
  payments: "Zahlungen",
  email: "E-Mail",
  storage: "Dateispeicher",
  background_jobs: "Background-Jobs",
  backups: "Backups",
  reconciliation: "Konsistenzprüfung",
  configuration: "Konfiguration",
};

const PROBLEM_LABELS: Record<string, string> = {
  operation: "Geschäftsvorgang",
  payment: "Zahlungsverarbeitung",
  background_job: "Hintergrundaufgabe",
  technical_error: "Technischer Fehler",
  reconciliation: "Konsistenzhinweis",
  email: "E-Mail-Zustellung",
};

const JOB_LABELS: Record<string, string> = {
  "reconcile.tenant": "Konsistenzprüfung",
  "email.deliver": "E-Mail-Zustellung",
};

const STATUS_LABELS: Record<string, string> = {
  failed: "Fehlgeschlagen",
  dead: "Manuelle Prüfung",
  failed_retryable: "Erneut versuchbar",
  failed_terminal: "Manuelle Prüfung",
  processing: "In Bearbeitung",
  WARNING: "Hinweis",
  ERROR: "Fehler",
  REQUIRES_REVIEW: "Prüfung nötig",
};

function CapabilityIcon({ status }: { status: CapabilityState }) {
  const { colors } = useTheme();
  if (status === "available") return <CheckCircle size={22} weight="fill" color={colors.success} />;
  if (status === "unavailable") return <XCircle size={22} weight="fill" color={colors.error} />;
  return <Warning size={22} weight="fill" color={colors.warning} />;
}

export default function Systembetrieb() {
  const styles = useStyles();
  const { colors } = useTheme();
  const { locale, tf } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const status = useQuery<StatusPayload>({ queryKey: ["operations-status"], queryFn: () => apiGet("/operations/status"), refetchInterval: 30000 });
  const problems = useQuery<Problem[]>({ queryKey: ["operations-problems"], queryFn: () => apiGet("/operations/problems"), refetchInterval: 30000 });
  const jobs = useQuery<Job[]>({ queryKey: ["operations-jobs"], queryFn: () => apiGet("/operations/jobs") });
  const reconciliation = useQuery<Reconciliation>({ queryKey: ["operations-reconciliation"], queryFn: () => apiGet("/operations/reconciliation/latest") });
  const communication = useQuery<CommunicationStatus>({ queryKey: ["operations-communications"], queryFn: () => apiGet("/operations/communications"), refetchInterval: 30000 });

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["operations-status"] }),
      queryClient.invalidateQueries({ queryKey: ["operations-problems"] }),
      queryClient.invalidateQueries({ queryKey: ["operations-jobs"] }),
      queryClient.invalidateQueries({ queryKey: ["operations-reconciliation"] }),
      queryClient.invalidateQueries({ queryKey: ["operations-communications"] }),
    ]);
  };
  const runCheck = useMutation({ mutationFn: () => apiPost("/operations/reconciliation/run"), onSuccess: refresh });
  const retryJob = useMutation({ mutationFn: (id: string) => apiPost(`/operations/jobs/${id}/retry`), onSuccess: refresh });
  const retryPayment = useMutation({ mutationFn: (id: string) => apiPost(`/payment-events/${id}/retry`), onSuccess: refresh });
  const formatter = useMemo(() => new Intl.DateTimeFormat(locale, { dateStyle: "short", timeStyle: "short" }), [locale]);
  const date = (value?: string) => value ? formatter.format(new Date(value)) : "–";
  const openProblems = problems.data ?? [];
  const failedJobs = (jobs.data ?? []).filter((job) => job.status === "failed" || job.status === "dead");
  const loadError = [status.error, problems.error, jobs.error, reconciliation.error, communication.error].find(Boolean);
  const actionError = runCheck.error || retryJob.error || retryPayment.error;

  return <View style={styles.root}>
    <View style={styles.header}>
      <Pressable onPress={() => router.back()} style={styles.back} testID="operations-back"><ArrowLeft size={22} color={colors.onSurface} /></Pressable>
      <View style={{ flex: 1 }}><Text style={styles.title}>Systembetrieb</Text><Muted>Technischer Zustand und Handlungsbedarf</Muted></View>
      <Pressable onPress={refresh} style={styles.back} testID="operations-refresh"><ArrowsClockwise size={21} color={colors.brandPrimary} /></Pressable>
    </View>
    <ScrollView contentContainerStyle={styles.content}>
      {loadError ? <Card testID="operations-load-error">
        <Text style={styles.warningText}>Betriebsdaten konnten nicht vollständig geladen werden</Text>
        <Muted>{loadError instanceof Error ? loadError.message : "Bitte später erneut versuchen."}</Muted>
      </Card> : null}
      {actionError ? <Card testID="operations-action-error">
        <Text style={styles.warningText}>Aktion konnte nicht ausgeführt werden</Text>
        <Muted>{actionError instanceof Error ? actionError.message : "Bitte später erneut versuchen."}</Muted>
      </Card> : null}
      <Card testID="operations-overview">
        <View style={styles.summary}>
          {status.data?.ready ? <CheckCircle size={30} weight="fill" color={colors.success} /> : <Warning size={30} weight="fill" color={colors.warning} />}
          <View style={{ flex: 1 }}>
            <Text style={styles.summaryTitle}>{status.data?.ready ? "ORDO ist einsatzbereit" : "ORDO benötigt Aufmerksamkeit"}</Text>
            <Muted>{status.isLoading ? "Systemstatus wird geprüft…" : tf("{count} offene Hinweise", { count: openProblems.length })}</Muted>
            {status.data?.tenantId ? <Muted>{tf("Mandant {id}", { id: status.data.tenantId })}</Muted> : null}
          </View>
        </View>
      </Card>

      <SectionTitle>Systemstatus</SectionTitle>
      <View style={styles.grid}>
        {Object.entries(status.data?.capabilities ?? {}).map(([key, capability]) => <Card key={key} style={styles.capability}>
          <View style={styles.capabilityHead}><CapabilityIcon status={capability.status} /><Text style={styles.capabilityTitle}>{CAPABILITY_LABELS[key] ?? key}</Text></View>
          <Muted>{capability.message}</Muted>
          {capability.expectedVersion !== undefined ? <Muted>Schema {capability.appliedVersion ?? "–"} / {capability.expectedVersion}</Muted> : null}
        </Card>)}
      </View>

      <SectionTitle>Kommunikation & Worker</SectionTitle>
      <View style={styles.grid}>
        <Card style={styles.capability}><Text style={styles.capabilityTitle}>Ausstehende E-Mails</Text><Text style={styles.metric}>{communication.data?.mail.pending ?? "–"}</Text></Card>
        <Card style={styles.capability}><Text style={styles.capabilityTitle}>Fehlgeschlagene E-Mails</Text><Text style={styles.metric}>{(communication.data?.mail.failed ?? 0) + (communication.data?.mail.dead ?? 0)}</Text></Card>
        <Card style={styles.capability}><Text style={styles.capabilityTitle}>Tote Jobs</Text><Text style={styles.metric}>{communication.data?.jobs.dead ?? "–"}</Text></Card>
        <Card style={styles.capability}><Text style={styles.capabilityTitle}>Speicherfehler</Text><Text style={styles.metric}>{communication.data?.storage.errors ?? "–"}</Text></Card>
      </View>

      <View style={styles.sectionRow}><SectionTitle>Konsistenzprüfung</SectionTitle><Button title="Jetzt prüfen" kind="secondary" loading={runCheck.isPending} onPress={() => runCheck.mutate()} /></View>
      <Card testID="reconciliation-summary">
        {reconciliation.data ? <>
          <View style={styles.problemHead}><Text style={styles.problemTitle}>{reconciliation.data.status === "OK" ? "Keine Abweichungen gefunden" : "Prüfung benötigt Aufmerksamkeit"}</Text><Text style={styles.code}>{reconciliation.data.issueCount}</Text></View>
          <Muted>{date(reconciliation.data.finishedAt)}</Muted>
        </> : <Muted>Noch keine Konsistenzprüfung vorhanden.</Muted>}
      </Card>

      <SectionTitle>Offene Probleme</SectionTitle>
      {openProblems.length === 0 ? <EmptyState title="Keine offenen technischen Probleme" subtitle="Alle überwachten Vorgänge sind unauffällig." /> : openProblems.map((problem, index) => <Card key={`${problem.type}-${problem.id ?? index}`} testID={`operations-problem-${index}`}>
        <View style={styles.problemHead}><Text style={styles.problemTitle}>{problem.message ?? PROBLEM_LABELS[problem.type] ?? "Technischer Hinweis"}</Text><Text style={styles.code}>{STATUS_LABELS[problem.status ?? ""] ?? "Prüfen"}</Text></View>
        <Muted>{date(problem.occurredAt)}{problem.errorReference ? ` · Referenz ${problem.errorReference}` : ""}</Muted>
        {problem.reviewRequired ? <Text style={styles.warningText}>Manuelle Prüfung erforderlich</Text> : null}
        {problem.retryAllowed && problem.type === "payment" && problem.id ? <Button title="Sicher erneut versuchen" kind="secondary" loading={retryPayment.isPending} onPress={() => retryPayment.mutate(problem.id!)} style={{ marginTop: 10 }} /> : null}
      </Card>)}

      <SectionTitle>Fehlgeschlagene Jobs</SectionTitle>
      {failedJobs.length === 0 ? <EmptyState title="Keine fehlgeschlagenen Jobs" /> : failedJobs.map((job) => <Card key={job.id}>
        <View style={styles.problemHead}><Text style={styles.problemTitle}>{JOB_LABELS[job.jobType] ?? "Hintergrundaufgabe"}</Text><Text style={styles.code}>{STATUS_LABELS[job.status] ?? "Prüfen"}</Text></View>
        <Muted>{date(job.updatedAt)} · Versuch {job.attempts}{job.lastErrorReference ? ` · Referenz ${job.lastErrorReference}` : ""}</Muted>
        {job.retryAllowed ? <Button title="Sicher erneut versuchen" kind="secondary" loading={retryJob.isPending} onPress={() => retryJob.mutate(job.id)} style={{ marginTop: 10 }} /> : null}
      </Card>)}
    </ScrollView>
  </View>;
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: { padding: 20, paddingTop: 28, backgroundColor: c.surface, borderBottomWidth: 1, borderBottomColor: c.divider, flexDirection: "row", alignItems: "center", gap: 12 },
  back: { width: 42, height: 42, borderRadius: 11, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 22, fontWeight: "900", color: c.onSurface },
  content: { padding: 20, gap: 12, paddingBottom: 40 },
  summary: { flexDirection: "row", alignItems: "center", gap: 14 },
  summaryTitle: { fontSize: 18, fontWeight: "900", color: c.onSurface },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
  capability: { flexGrow: 1, flexBasis: 220, gap: 7 },
  capabilityHead: { flexDirection: "row", alignItems: "center", gap: 9 },
  capabilityTitle: { fontSize: 15, fontWeight: "800", color: c.onSurface },
  metric: { fontSize: 24, fontWeight: "900", color: c.brandPrimary },
  sectionRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" },
  problemHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 },
  problemTitle: { flex: 1, fontSize: 15, fontWeight: "800", color: c.onSurface },
  code: { fontSize: 12, fontWeight: "800", color: c.brandPrimary, backgroundColor: c.brandTertiary, borderRadius: 8, paddingHorizontal: 8, paddingVertical: 4 },
  warningText: { marginTop: 8, color: c.warning, fontWeight: "700" },
}));
