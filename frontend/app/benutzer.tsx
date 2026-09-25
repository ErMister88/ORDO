import { useState } from "react";
import {
  View,
  ScrollView,
  Pressable,
  KeyboardAvoidingView,
  Platform,
} from "react-native";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { ArrowLeft, Plus, Key, CaretDown, ShieldCheck } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiGet, apiPost } from "@/src/api/client";
import { Card, Input, Button, SectionTitle, Muted } from "@/src/components/ui";
import { LocalizedText as Text } from "@/src/i18n";

type Role = "sales" | "customer";
type CompanyMode = "existing" | "new";

export default function Benutzer() {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const qc = useQueryClient();

  const users = useQuery({ queryKey: ["users"], queryFn: () => apiGet("/users") });
  const companies = useQuery({ queryKey: ["companies"], queryFn: () => apiGet("/companies") });

  const [showForm, setShowForm] = useState(false);
  const [role, setRole] = useState<Role>("sales");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [companyMode, setCompanyMode] = useState<CompanyMode>("existing");
  const [companyId, setCompanyId] = useState("");
  const [showComp, setShowComp] = useState(false);
  const [newComp, setNewComp] = useState({ name: "", city: "", email: "" });
  const [msg, setMsg] = useState("");
  const [credential, setCredential] = useState<{ name: string; email: string; password: string } | null>(null);

  const resetForm = () => {
    setRole("sales");
    setName("");
    setEmail("");
    setCompanyMode("existing");
    setCompanyId("");
    setNewComp({ name: "", city: "", email: "" });
    setMsg("");
  };

  const create = useMutation({
    mutationFn: () => {
      const body: any = { name: name.trim(), email: email.trim(), role };
      if (role === "customer") {
        if (companyMode === "new") body.newCompany = newComp;
        else body.companyId = companyId;
      }
      return apiPost("/users", body);
    },
    onSuccess: (u: any) => {
      qc.invalidateQueries({ queryKey: ["users"] });
      qc.invalidateQueries({ queryKey: ["companies"] });
      setCredential({ name: u.name, email: u.email, password: u.initialPassword });
      setShowForm(false);
      resetForm();
    },
    onError: (e: any) => setMsg(e.message || "Fehler beim Anlegen"),
  });

  const resetPw = useMutation({
    mutationFn: (id: string) => apiPost(`/users/${id}/reset`, {}),
    onSuccess: (r: any) => setCredential({ name: r.email, email: r.email, password: r.initialPassword }),
  });

  const submit = () => {
    setMsg("");
    if (!name.trim()) return setMsg("Bitte Name eingeben");
    if (!email.trim() || !email.includes("@")) return setMsg("Bitte gültige E-Mail eingeben");
    if (role === "customer") {
      if (companyMode === "existing" && !companyId) return setMsg("Bitte eine Firma wählen");
      if (companyMode === "new" && !newComp.name.trim()) return setMsg("Bitte Firmennamen eingeben");
    }
    create.mutate();
  };

  const selectedCompany = (companies.data ?? []).find((c: any) => c.id === companyId);
  const roleLabel = (r: string) => (r === "admin" ? "Admin" : r === "sales" ? "Vertrieb" : "Kunde");

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
        <Pressable onPress={() => router.back()} style={styles.backBtn} testID="back-button" hitSlop={8}>
          <ArrowLeft size={20} color={colors.onSurfaceSecondary} weight="bold" />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Benutzer</Text>
          <Text style={styles.subtitle}>Konten anlegen & verwalten</Text>
        </View>
        <Pressable
          onPress={() => {
            setCredential(null);
            resetForm();
            setShowForm((s) => !s);
          }}
          style={styles.addBtn}
          testID="add-user-button"
          hitSlop={8}
        >
          <Plus size={20} color={colors.onBrandPrimary} weight="bold" />
        </Pressable>
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          {credential && (
            <Card testID="credential-card" style={{ borderWidth: 1.5, borderColor: colors.success }}>
              <View style={styles.credHead}>
                <ShieldCheck size={22} color={colors.success} weight="fill" />
                <Text style={styles.credTitle}>Zugangsdaten (einmalig sichtbar)</Text>
              </View>
              <Muted>Notieren Sie dieses Passwort jetzt – es wird nicht erneut angezeigt.</Muted>
              <View style={styles.credBox}>
                <Text style={styles.credLabel}>E-Mail</Text>
                <Text style={styles.credValue} selectable testID="cred-email">{credential.email}</Text>
                <Text style={[styles.credLabel, { marginTop: 8 }]}>Passwort</Text>
                <Text style={styles.credPw} selectable testID="cred-password">{credential.password}</Text>
              </View>
              <Button title="Verstanden" kind="secondary" onPress={() => setCredential(null)} style={{ marginTop: 10 }} testID="cred-close" />
            </Card>
          )}

          {showForm && (
            <Card testID="user-form">
              <SectionTitle>Neuer Benutzer</SectionTitle>

              <Text style={styles.label}>Kontotyp</Text>
              <View style={styles.segment}>
                {(["sales", "customer"] as Role[]).map((r) => (
                  <Pressable
                    key={r}
                    testID={`role-${r}`}
                    style={[styles.segBtn, role === r && styles.segBtnActive]}
                    onPress={() => setRole(r)}
                  >
                    <Text style={[styles.segText, role === r && styles.segTextActive]}>
                      {r === "sales" ? "Vertrieb" : "Kunde"}
                    </Text>
                  </Pressable>
                ))}
              </View>

              <Text style={styles.label}>Name</Text>
              <Input testID="user-name" value={name} onChangeText={setName} placeholder="Vor- und Nachname / Firma" />
              <Text style={styles.label}>E-Mail</Text>
              <Input
                testID="user-email"
                value={email}
                onChangeText={setEmail}
                placeholder="name@firma.de"
                autoCapitalize="none"
                keyboardType="email-address"
                autoCorrect={false}
              />

              {role === "customer" && (
                <>
                  <Text style={styles.label}>Firma</Text>
                  <View style={styles.segment}>
                    {(["existing", "new"] as CompanyMode[]).map((m) => (
                      <Pressable
                        key={m}
                        testID={`compmode-${m}`}
                        style={[styles.segBtn, companyMode === m && styles.segBtnActive]}
                        onPress={() => setCompanyMode(m)}
                      >
                        <Text style={[styles.segText, companyMode === m && styles.segTextActive]}>
                          {m === "existing" ? "Bestehende" : "Neue Firma"}
                        </Text>
                      </Pressable>
                    ))}
                  </View>

                  {companyMode === "existing" ? (
                    <>
                      <Pressable style={styles.select} testID="select-company" onPress={() => setShowComp((s) => !s)}>
                        <Text style={styles.selectText}>{selectedCompany?.name ?? "Firma wählen"}</Text>
                        <CaretDown size={16} color={colors.muted} />
                      </Pressable>
                      {showComp &&
                        (companies.data ?? []).map((c: any) => (
                          <Pressable
                            key={c.id}
                            testID={`company-opt-${c.id}`}
                            style={styles.option}
                            onPress={() => {
                              setCompanyId(c.id);
                              setShowComp(false);
                            }}
                          >
                            <Text style={styles.optionText}>{c.name}</Text>
                          </Pressable>
                        ))}
                    </>
                  ) : (
                    <>
                      <Input testID="newcomp-name" value={newComp.name} onChangeText={(v) => setNewComp((s) => ({ ...s, name: v }))} placeholder="Firmenname" style={{ marginTop: 4 }} />
                      <View style={styles.row}>
                        <View style={{ flex: 1 }}>
                          <Input testID="newcomp-city" value={newComp.city} onChangeText={(v) => setNewComp((s) => ({ ...s, city: v }))} placeholder="Ort" />
                        </View>
                        <View style={{ flex: 1 }}>
                          <Input testID="newcomp-email" value={newComp.email} onChangeText={(v) => setNewComp((s) => ({ ...s, email: v }))} placeholder="Firmen-E-Mail" autoCapitalize="none" keyboardType="email-address" />
                        </View>
                      </View>
                    </>
                  )}
                </>
              )}

              {msg ? <Text style={styles.err}>{msg}</Text> : null}
              <Button testID="user-submit" title="Benutzer anlegen" loading={create.isPending} onPress={submit} style={{ marginTop: 10 }} />
            </Card>
          )}

          <SectionTitle style={{ marginTop: 4 }}>Alle Benutzer ({users.data?.length ?? 0})</SectionTitle>
          {(users.data ?? []).map((u: any) => (
            <Card key={u.id} testID={`user-${u.id}`}>
              <View style={styles.userRow}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.userName}>{u.name}</Text>
                  <Muted>{u.email}</Muted>
                  <Muted>
                    {roleLabel(u.role)}
                    {u.companyName ? ` · ${u.companyName}` : ""}
                  </Muted>
                </View>
                {u.role !== "admin" && (
                  <Pressable
                    testID={`reset-user-${u.id}`}
                    style={styles.resetBtn}
                    onPress={() => resetPw.mutate(u.id)}
                    hitSlop={6}
                  >
                    <Key size={16} color={colors.brandPrimary} weight="bold" />
                    <Text style={styles.resetText}>Passwort</Text>
                  </Pressable>
                )}
              </View>
            </Card>
          ))}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: {
    flexDirection: "row",
    alignItems: "flex-end",
    paddingHorizontal: 20,
    paddingBottom: 14,
    backgroundColor: c.surface,
    borderBottomWidth: 1,
    borderBottomColor: c.divider,
    gap: 12,
  },
  backBtn: { width: 44, height: 44, borderRadius: 12, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  addBtn: { width: 44, height: 44, borderRadius: 12, backgroundColor: c.brandPrimary, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 24, fontWeight: "800", color: c.onSurface, letterSpacing: -0.5 },
  subtitle: { fontSize: 14, color: c.muted, marginTop: 2 },
  content: { padding: 20, gap: 12, paddingBottom: 32 },
  row: { flexDirection: "row", gap: 12 },
  label: { fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary, marginTop: 10, marginBottom: 4 },
  err: { color: c.error, fontSize: 14, fontWeight: "600", marginTop: 8 },
  segment: { flexDirection: "row", gap: 8 },
  segBtn: { flex: 1, paddingVertical: 12, borderRadius: 12, alignItems: "center", backgroundColor: c.surfaceTertiary },
  segBtnActive: { backgroundColor: c.brandPrimary },
  segText: { fontSize: 14, fontWeight: "700", color: c.onSurfaceSecondary },
  segTextActive: { color: c.onBrandPrimary },
  select: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: c.surfaceTertiary,
    borderRadius: 14,
    paddingHorizontal: 16,
    paddingVertical: 14,
    marginTop: 4,
  },
  selectText: { fontSize: 15, fontWeight: "600", color: c.onSurface },
  option: { backgroundColor: c.surfaceTertiary, borderRadius: 10, paddingHorizontal: 16, paddingVertical: 12, marginTop: 4 },
  optionText: { fontSize: 15, color: c.onSurfaceSecondary },
  userRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  userName: { fontSize: 15, fontWeight: "800", color: c.onSurface },
  resetBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 12,
    backgroundColor: c.brandTertiary,
  },
  resetText: { color: c.brandPrimary, fontWeight: "700", fontSize: 13 },
  credHead: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 4 },
  credTitle: { fontSize: 15, fontWeight: "800", color: c.onSurface },
  credBox: { backgroundColor: c.surfaceTertiary, borderRadius: 12, padding: 14, marginTop: 8 },
  credLabel: { fontSize: 12, fontWeight: "700", color: c.muted },
  credValue: { fontSize: 15, fontWeight: "600", color: c.onSurface, marginTop: 2 },
  credPw: { fontSize: 20, fontWeight: "800", color: c.brandPrimary, marginTop: 2, letterSpacing: 1 },
}));
