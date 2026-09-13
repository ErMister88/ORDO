import { useState } from "react";
import { View, Text, ScrollView, Pressable, KeyboardAvoidingView, Platform } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { ArrowLeft, CheckCircle } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiPost } from "@/src/api/client";
import { Card, Input, Button, SectionTitle, Muted } from "@/src/components/ui";

export default function PasswortVergessen() {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const requestCode = async () => {
    setMsg("");
    if (!email.trim() || !email.includes("@")) {
      setMsg("Bitte gültige E-Mail eingeben");
      return;
    }
    setLoading(true);
    try {
      await apiPost("/auth/password/forgot", { email: email.trim() });
      setStep(2);
    } catch (e: any) {
      setMsg(e.message || "Fehler");
    } finally {
      setLoading(false);
    }
  };

  const doReset = async () => {
    setMsg("");
    if (code.trim().length !== 6) {
      setMsg("Bitte den 6-stelligen Code eingeben");
      return;
    }
    if (pw.length < 8) {
      setMsg("Passwort muss mindestens 8 Zeichen haben");
      return;
    }
    if (pw !== pw2) {
      setMsg("Passwörter stimmen nicht überein");
      return;
    }
    setLoading(true);
    try {
      await apiPost("/auth/password/reset", { email: email.trim(), code: code.trim(), newPassword: pw });
      setStep(3);
    } catch (e: any) {
      setMsg(e.message || "Fehler");
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
        <Pressable onPress={() => router.back()} style={styles.backBtn} testID="back-button" hitSlop={8}>
          <ArrowLeft size={20} color={colors.onSurfaceSecondary} weight="bold" />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Passwort zurücksetzen</Text>
          <Text style={styles.subtitle}>Per Sicherheitscode via E-Mail</Text>
        </View>
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          {step === 1 && (
            <Card testID="forgot-step1">
              <SectionTitle>E-Mail eingeben</SectionTitle>
              <Muted>Wir senden Ihnen einen 6-stelligen Code an Ihre hinterlegte E-Mail.</Muted>
              <Text style={styles.label}>E-Mail</Text>
              <Input
                testID="forgot-email"
                value={email}
                onChangeText={setEmail}
                placeholder="name@firma.de"
                autoCapitalize="none"
                keyboardType="email-address"
                autoCorrect={false}
              />
              {msg ? <Text style={styles.err}>{msg}</Text> : null}
              <Button testID="forgot-submit" title="Code senden" loading={loading} onPress={requestCode} style={{ marginTop: 8 }} />
            </Card>
          )}

          {step === 2 && (
            <Card testID="forgot-step2">
              <SectionTitle>Code & neues Passwort</SectionTitle>
              <Muted>Falls ein Konto zu {email} existiert, haben wir einen Code gesendet (30 Min. gültig).</Muted>
              <Text style={styles.label}>Sicherheitscode</Text>
              <Input testID="reset-code" value={code} onChangeText={setCode} placeholder="123456" keyboardType="number-pad" maxLength={6} />
              <Text style={styles.label}>Neues Passwort</Text>
              <Input testID="reset-pw" value={pw} onChangeText={setPw} placeholder="mind. 8 Zeichen" secureTextEntry />
              <Text style={styles.label}>Passwort bestätigen</Text>
              <Input testID="reset-pw2" value={pw2} onChangeText={setPw2} placeholder="wiederholen" secureTextEntry />
              {msg ? <Text style={styles.err}>{msg}</Text> : null}
              <Button testID="reset-submit" title="Passwort setzen" loading={loading} onPress={doReset} style={{ marginTop: 8 }} />
              <Pressable onPress={requestCode} hitSlop={8} style={{ alignSelf: "center", marginTop: 12 }}>
                <Text style={styles.resend}>Code erneut senden</Text>
              </Pressable>
            </Card>
          )}

          {step === 3 && (
            <Card testID="forgot-step3">
              <View style={{ alignItems: "center", gap: 10, paddingVertical: 8 }}>
                <CheckCircle size={48} color={colors.success} weight="fill" />
                <Text style={styles.doneTitle}>Passwort geändert</Text>
                <Muted>Sie können sich jetzt mit Ihrem neuen Passwort anmelden.</Muted>
                <Button testID="back-to-login" title="Zur Anmeldung" onPress={() => router.replace("/login")} style={{ marginTop: 8, alignSelf: "stretch" }} />
              </View>
            </Card>
          )}
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
  title: { fontSize: 22, fontWeight: "800", color: c.onSurface, letterSpacing: -0.5 },
  subtitle: { fontSize: 14, color: c.muted, marginTop: 2 },
  content: { padding: 20, gap: 12, paddingBottom: 32 },
  label: { fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary, marginTop: 10, marginBottom: 4 },
  err: { color: c.error, fontSize: 14, fontWeight: "600", marginTop: 8 },
  resend: { color: c.brandPrimary, fontWeight: "700", fontSize: 14 },
  doneTitle: { fontSize: 18, fontWeight: "800", color: c.onSurface },
}));
