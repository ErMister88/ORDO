import { useEffect, useState } from "react";
import {
  View,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { CheckCircle, Clock, XCircle } from "phosphor-react-native";

import { Button, Card, Muted } from "@/src/components/ui";
import { apiGet } from "@/src/api/client";
import { clearPendingShopPayment, loadPendingShopPayment } from "@/src/shop/pending-payment";
import { makeStyles, tokens, useTheme } from "@/src/theme";
import { LocalizedText as Text } from "@/src/i18n";


export default function ZahlungsRueckkehr() {
  const styles = useStyles();
  const { colors } = useTheme();
  const router = useRouter();
  const { status, type, id } = useLocalSearchParams<{ status?: string; type?: string; id?: string }>();
  const cancelled = status === "cancel";
  const [confirmed, setConfirmed] = useState(false);

  useEffect(() => {
    if (cancelled || !id || !type) return;
    let active = true;
    const path = type === "invoice"
      ? `/invoices/${id}/payment-status`
      : type === "machine_request"
        ? `/machine-requests/${id}/payment-status`
        : type === "shop_order"
          ? `/shop/orders/${id}/payment-status`
          : null;
    if (!path) return;
    const check = async () => {
      for (let attempt = 0; attempt < 8 && active; attempt += 1) {
        const pending = type === "shop_order" ? await loadPendingShopPayment() : null;
        if (type === "shop_order" && pending?.orderId !== id) return;
        const headers: Record<string, string> = pending
          ? { "X-Order-Token": pending.orderToken }
          : {};
        try {
          const result = await apiGet<{ status: string }>(path, headers);
          if (result.status === "Bezahlt") {
            if (type === "shop_order") await clearPendingShopPayment();
            if (active) setConfirmed(true);
            return;
          }
        } catch {
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    };
    void check();
    return () => { active = false; };
  }, [cancelled, id, type]);

  return <View style={styles.root}>
    <Card style={styles.card} testID="payment-return">
      {cancelled
        ? <XCircle size={46} color={colors.warning} weight="fill" />
        : <Clock size={46} color={colors.brandPrimary} weight="fill" />}
      <Text style={styles.title}>
        {cancelled ? "Zahlung nicht abgeschlossen" : confirmed ? "Zahlung bestätigt" : "Zahlung wird bestätigt"}
      </Text>
      <Muted style={styles.copy}>
        {cancelled
          ? "Es wurde hierdurch keine Zahlung bestätigt. Ein bereits gestarteter Zahlungsvorgang kann weiterhin geprüft werden."
          : confirmed
            ? "ORDO hat die Zahlung serverseitig bestätigt."
            : "ORDO wartet auf die sichere Bestätigung des Zahlungsdienstes. Der aktuelle Status erscheint anschließend bei Ihrer Bestellung oder Rechnung."}
      </Muted>
      {confirmed ? <CheckCircle size={20} color={colors.success} /> : null}
      <Button title="Zurück zu ORDO" onPress={() => router.replace("/")} style={styles.button} />
    </Card>
  </View>;
}

const useStyles = makeStyles((c) => ({
  root: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: tokens.spacing.lg,
    backgroundColor: c.surfaceSecondary,
  },
  card: { width: "100%", maxWidth: 520, alignItems: "center", gap: 12, padding: 28 },
  title: { color: c.onSurface, fontSize: 24, fontWeight: "800", textAlign: "center" },
  copy: { textAlign: "center", lineHeight: 21 },
  button: { width: "100%", marginTop: 8 },
}));
