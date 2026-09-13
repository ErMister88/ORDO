import { useState } from "react";
import {
  View,
  Text,
  ScrollView,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  useWindowDimensions,
} from "react-native";
import { useRouter } from "expo-router";
import { Image } from "expo-image";
import { LinearGradient } from "expo-linear-gradient";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Coffee } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { useAuth } from "@/src/auth/auth";
import { Button, Input } from "@/src/components/ui";

const HERO =
  "https://images.unsplash.com/photo-1653668168018-0ee2c5756bca?crop=entropy&cs=srgb&fm=jpg&q=85&w=1200";

const DEMO = [
  { label: "Admin", email: "admin@ss-coffee.de", password: "Admin#2026" },
  { label: "Vertrieb", email: "vertrieb@ss-coffee.de", password: "Sales#2026" },
  { label: "Kunde", email: "kunde@ss-coffee.de", password: "Kunde#2026" },
];

export default function Login() {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { height } = useWindowDimensions();
  const { signIn } = useAuth();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    setError("");
    if (!email || !password) {
      setError("Bitte E-Mail und Passwort eingeben");
      return;
    }
    setLoading(true);
    try {
      const u = await signIn(email, password);
      router.replace(u.must_change_password ? "/passwort-aendern?forced=1" : "/(tabs)");
    } catch (e: any) {
      setError(e.message || "Anmeldung fehlgeschlagen");
    } finally {
      setLoading(false);
    }
  };

  const fillDemo = (d: (typeof DEMO)[number]) => {
    setEmail(d.email);
    setPassword(d.password);
    setError("");
  };

  return (
    <View style={styles.root}>
      <View style={[styles.hero, { height: height * 0.4 }]}>
        <Image source={{ uri: HERO }} style={styles.heroImg} contentFit="cover" />
        <LinearGradient
          colors={["rgba(11,27,61,0.55)", "rgba(11,27,61,0.96)"]}
          style={styles.heroOverlay}
        />
        <View style={[styles.heroContent, { paddingTop: insets.top + 24 }]}>
          <View style={styles.logoBadge}>
            <Coffee size={26} color={colors.onBrand} weight="fill" />
          </View>
          <Text style={styles.brandTitle}>ORDO Connect</Text>
          <Text style={styles.brandSub}>by S&S · B2B Vertriebsportal</Text>
        </View>
      </View>

      <KeyboardAvoidingView
        style={styles.sheetWrap}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        keyboardVerticalOffset={0}
      >
        <ScrollView
          style={styles.sheet}
          contentContainerStyle={[styles.sheetContent, { paddingBottom: insets.bottom + 24 }]}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          <Text style={styles.title}>Anmelden</Text>
          <Text style={styles.subtitle}>Melden Sie sich bei Ihrem Konto an</Text>

          <View style={styles.form}>
            <Text style={styles.fieldLabel}>E-Mail</Text>
            <Input
              testID="login-email-input"
              value={email}
              onChangeText={setEmail}
              placeholder="name@firma.de"
              autoCapitalize="none"
              keyboardType="email-address"
              autoCorrect={false}
            />
            <Text style={styles.fieldLabel}>Passwort</Text>
            <Input
              testID="login-password-input"
              value={password}
              onChangeText={setPassword}
              placeholder="••••••••"
              secureTextEntry
            />

            {error ? (
              <Text testID="login-error" style={styles.error}>
                {error}
              </Text>
            ) : null}

            <Button
              testID="login-submit-button"
              title="Anmelden"
              onPress={submit}
              loading={loading}
              style={{ marginTop: 4 }}
            />
            <Pressable
              testID="forgot-password-link"
              onPress={() => router.push("/passwort-vergessen")}
              hitSlop={8}
              style={{ alignSelf: "center", marginTop: 10 }}
            >
              <Text style={styles.forgotLink}>Passwort vergessen?</Text>
            </Pressable>
            <Pressable
              testID="to-shop-link"
              onPress={() => router.push("/shop")}
              hitSlop={8}
              style={styles.shopLink}
            >
              <Text style={styles.shopLinkText}>🛒  Zum Kaffee-Shop (ohne Login)</Text>
            </Pressable>
          </View>

          <Text style={styles.demoTitle}>Demo-Konten</Text>
          <View style={styles.demoRow}>
            {DEMO.map((d) => (
              <Pressable
                key={d.email}
                testID={`demo-chip-${d.label.toLowerCase()}`}
                style={styles.demoChip}
                onPress={() => fillDemo(d)}
              >
                <Text style={styles.demoChipText}>{d.label}</Text>
              </Pressable>
            ))}
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.brand },
  hero: { width: "100%" },
  heroImg: { ...StyleSheetAbsolute() },
  heroOverlay: { ...StyleSheetAbsolute() },
  heroContent: { flex: 1, paddingHorizontal: 24, justifyContent: "center" },
  logoBadge: {
    width: 54,
    height: 54,
    borderRadius: 18,
    backgroundColor: "rgba(255,255,255,0.16)",
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 16,
  },
  brandTitle: { fontSize: 30, fontWeight: "800", color: c.onBrand, letterSpacing: -0.5 },
  brandSub: { fontSize: 15, color: "rgba(255,255,255,0.78)", marginTop: 4, fontWeight: "500" },
  sheetWrap: { flex: 1, marginTop: -28 },
  sheet: { flex: 1, backgroundColor: c.surface, borderTopLeftRadius: 28, borderTopRightRadius: 28 },
  sheetContent: { padding: 24, gap: 4 },
  title: { fontSize: 24, fontWeight: "800", color: c.onSurface, letterSpacing: -0.4 },
  subtitle: { fontSize: 15, color: c.muted, marginBottom: 12 },
  form: { gap: 8 },
  fieldLabel: { fontSize: 13, fontWeight: "700", color: c.onSurfaceSecondary, marginTop: 6 },
  forgotLink: { color: c.brandPrimary, fontWeight: "700", fontSize: 14 },
  shopLink: { alignSelf: "center", marginTop: 16, paddingVertical: 12, paddingHorizontal: 18, borderRadius: 12, backgroundColor: c.brandTertiary },
  shopLinkText: { color: c.brandPrimary, fontWeight: "800", fontSize: 15 },
  error: { color: c.error, fontSize: 14, fontWeight: "600", marginTop: 4 },
  demoTitle: { fontSize: 13, fontWeight: "700", color: c.muted, marginTop: 24, marginBottom: 10 },
  demoRow: { flexDirection: "row", gap: 10 },
  demoChip: {
    flex: 1,
    backgroundColor: c.brandTertiary,
    borderRadius: 14,
    paddingVertical: 13,
    alignItems: "center",
  },
  demoChipText: { color: c.onBrandTertiary, fontWeight: "700", fontSize: 14 },
}));

function StyleSheetAbsolute() {
  return { position: "absolute" as const, top: 0, left: 0, right: 0, bottom: 0 };
}
