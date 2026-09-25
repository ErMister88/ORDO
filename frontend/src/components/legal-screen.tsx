import {
  View,
  ScrollView,
  Pressable,
} from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { ArrowLeft } from "phosphor-react-native";

import { makeStyles, useTheme } from "@/src/theme";
import { Card } from "@/src/components/ui";
import type { LegalSection } from "@/src/legal";
import { LocalizedText as Text } from "@/src/i18n";

export function LegalScreen({ title, sections }: { title: string; sections: LegalSection[] }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const insets = useSafeAreaInsets();

  return (
    <View style={styles.root}>
      <View style={[styles.header, { paddingTop: insets.top + 8 }]}>
        <Pressable onPress={() => router.back()} style={styles.iconBtn} testID="back-button" hitSlop={8}>
          <ArrowLeft size={20} color={colors.onSurfaceSecondary} weight="bold" />
        </Pressable>
        <Text style={styles.title}>{title}</Text>
      </View>
      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        <Card>
          {sections.map((s, i) => (
            <View key={i} style={i > 0 ? styles.section : undefined}>
              {s.heading ? <Text style={styles.heading}>{s.heading}</Text> : null}
              <Text style={styles.body}>{s.body}</Text>
            </View>
          ))}
        </Card>
      </ScrollView>
    </View>
  );
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  header: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingBottom: 14, backgroundColor: c.surface, borderBottomWidth: 1, borderBottomColor: c.divider },
  iconBtn: { width: 40, height: 40, borderRadius: 12, backgroundColor: c.surfaceTertiary, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 20, fontWeight: "800", color: c.onSurface },
  content: { padding: 20, paddingBottom: 40 },
  section: { marginTop: 18, borderTopWidth: 1, borderTopColor: c.divider, paddingTop: 16 },
  heading: { fontSize: 15, fontWeight: "800", color: c.onSurface, marginBottom: 6 },
  body: { fontSize: 14, color: c.onSurfaceSecondary, lineHeight: 21 },
}));
