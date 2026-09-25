import { useState } from "react";
import {
  View,
  ScrollView,
  Pressable,
  KeyboardAvoidingView,
  Platform,
} from "react-native";
import { useRouter, useLocalSearchParams } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { ArrowLeft } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { apiPost } from "@/src/api/client";
import { useAuth } from "@/src/auth/auth";
import { Card, Input, Button, SectionTitle } from "@/src/components/ui";
import { LocalizedText as Text } from "@/src/i18n";

export default function PasswortAendern() {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { signOut } = useAuth();
  const { forced } = useLocalSearchParams<{ forced?: string }>();
  const isForced = forced === "1";

  const [current, setCurrent] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    setMsg("");
    if (!current) {
      setMsg("Bitte aktuelles Passwort eingeben");
      return;
    }
    if (pw.length < 8) {
      setMsg("Neues Passwort muss mindestens 8 Zeichen haben");
      return;
    }
    if (pw !== pw2) {
      setMsg("Passwörter stimmen nicht überein");
      return;
    }
    setLoading(true);
    try {
      await apiPost("/auth/password/change", { currentPassword: current, newPassword: pw });
      await signOut();
      router.replace("/login?passwordChanged=1");
    } catch (e: any) {
      setMsg(e.message || "Fehler");
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
        <Pressable
          onPress={async () => {
            if (isForced) {
              await signOut();
              router.replace("/login");
            } else {
              router.back();
            }
          }}
          style={styles.backBtn}
          testID="back-button"
          hitSlop={8}
        >
          <ArrowLeft size={20} color={colors.onSurfaceSecondary} weight="bold" />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Passwort ändern</Text>
          <Text style={styles.subtitle}>{isForced ? "Bitte neues Passwort vergeben" : "Für Ihr Konto"}</Text>
        </View>
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          <Card testID="change-form">
            <SectionTitle>Neues Passwort festlegen</SectionTitle>
            <Text style={styles.label}>Aktuelles Passwort</Text>
            <Input testID="change-current" value={current} onChangeText={setCurrent} placeholder="••••••••" secureTextEntry />
            <Text style={styles.label}>Neues Passwort</Text>
            <Input testID="change-new" value={pw} onChangeText={setPw} placeholder="mind. 8 Zeichen" secureTextEntry />
            <Text style={styles.label}>Neues Passwort bestätigen</Text>
            <Input testID="change-new2" value={pw2} onChangeText={setPw2} placeholder="wiederholen" secureTextEntry />
            {msg ? <Text style={styles.err}>{msg}</Text> : null}
            <Button testID="change-submit" title="Passwort ändern" loading={loading} onPress={submit} style={{ marginTop: 8 }} />
          </Card>
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
}));
