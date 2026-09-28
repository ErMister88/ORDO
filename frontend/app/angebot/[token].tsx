import { useState } from "react";
import { Linking, ScrollView, View, useWindowDimensions } from "react-native";
import { useLocalSearchParams } from "expo-router";
import { useMutation, useQuery } from "@tanstack/react-query";

import { API_BASE, publicApiGet, publicApiPost } from "@/src/api/client";
import { Button, Card, ErrorState, LoadingState, Muted, SectionTitle } from "@/src/components/ui";
import { LocalizedText as Text, useI18n } from "@/src/i18n";
import { makeStyles } from "@/src/theme";

type PublicOffer = {
  document: {
    offerNumber: string;
    offerDate: string;
    validUntil?: string | null;
    currency: string;
    provider: { name: string; legalName?: string | null };
    recipient: { name: string; contactName?: string };
    items: Array<{
      productName: string; sku?: string; quantity: number; unit: string;
      unitPriceMinor: number; netMinor: number; taxRate: number;
    }>;
    totals: { netMinor: number; taxMinor: number; grossMinor: number };
  };
  responseStatus: "OPEN" | "VIEWED" | "ACCEPTED" | "DECLINED" | "EXPIRED" | "CANCELLED";
};

export default function PublicOfferPage() {
  const styles = useStyles();
  const { width } = useWindowDimensions();
  const { locale, t, tf } = useI18n();
  const { token = "" } = useLocalSearchParams<{ token: string }>();
  const [decisionError, setDecisionError] = useState("");
  const offer = useQuery<PublicOffer>({
    queryKey: ["public-offer", token],
    queryFn: () => publicApiGet(`/public/offers/${encodeURIComponent(token)}`),
    enabled: Boolean(token),
    retry: false,
  });
  const decision = useMutation({
    mutationFn: (value: "accept" | "decline") => publicApiPost(`/public/offers/${encodeURIComponent(token)}/${value}`),
    onSuccess: () => { setDecisionError(""); void offer.refetch(); },
    onError: (error: Error) => setDecisionError(error.message),
  });
  const money = (minor: number, currency: string) => new Intl.NumberFormat(locale, {
    style: "currency", currency,
  }).format(minor / 100);

  if (offer.isPending) return <View style={styles.state}><LoadingState label="Angebot wird geladen…" /></View>;
  if (offer.isError || !offer.data) return <View style={styles.state}><ErrorState message={offer.error?.message || "Angebot nicht gefunden oder Link nicht mehr gültig."} onRetry={() => void offer.refetch()} /></View>;
  const data = offer.data;
  const doc = data.document;
  const terminal = data.responseStatus === "ACCEPTED" || data.responseStatus === "DECLINED";
  const compact = width < 600;

  return <ScrollView style={styles.root} contentContainerStyle={styles.scroll}>
    <View style={[styles.container, compact && styles.containerCompact]} testID="public-offer-page">
      <View style={styles.hero}>
        <Text style={styles.eyebrow}>{doc.provider.legalName || doc.provider.name}</Text>
        <Text style={styles.title}>{tf("Angebot {number}", { number: doc.offerNumber })}</Text>
        <Muted>{tf("Angebotsdatum: {date}", { date: doc.offerDate.slice(0, 10) })}{doc.validUntil ? ` · ${tf("Gültig bis: {date}", { date: doc.validUntil.slice(0, 10) })}` : ""}</Muted>
      </View>

      <Card>
        <SectionTitle>Empfänger</SectionTitle>
        <Text style={styles.recipient}>{doc.recipient.name}</Text>
        {doc.recipient.contactName ? <Muted>{doc.recipient.contactName}</Muted> : null}
      </Card>

      <Card>
        <SectionTitle>Positionen</SectionTitle>
        {doc.items.map((item, index) => <View key={`${item.sku}-${index}`} style={styles.line} testID={`public-offer-line-${index}`}>
          <View style={styles.lineMain}><Text style={styles.lineName}>{item.productName}</Text>{item.sku ? <Muted>{item.sku}</Muted> : null}</View>
          <View style={styles.lineValues}>
            <Muted>{tf("{quantity} {unit} × {price}", { quantity: item.quantity, unit: item.unit, price: money(item.unitPriceMinor, doc.currency) })}</Muted>
            <Text style={styles.lineTotal}>{money(item.netMinor, doc.currency)}</Text>
          </View>
          <Muted>{tf("zzgl. {rate}% Steuer", { rate: item.taxRate })}</Muted>
        </View>)}
        <View style={styles.totals}>
          <View style={styles.totalRow}><Muted>Summe netto</Muted><Text>{money(doc.totals.netMinor, doc.currency)}</Text></View>
          <View style={styles.totalRow}><Muted>Steuer</Muted><Text>{money(doc.totals.taxMinor, doc.currency)}</Text></View>
          <View style={styles.totalRow}><Text style={styles.grandLabel}>Gesamtsumme</Text><Text style={styles.grand}>{money(doc.totals.grossMinor, doc.currency)}</Text></View>
        </View>
      </Card>

      <Button title="PDF anzeigen / herunterladen" kind="secondary" onPress={() => void Linking.openURL(`${API_BASE}/api/public/offers/${encodeURIComponent(token)}/pdf?disposition=attachment`)} />

      {data.responseStatus === "ACCEPTED" ? <Card><Text style={styles.success}>Angebot angenommen</Text><Muted>Vielen Dank. Ihr Ansprechpartner sieht die Annahme in ORDO.</Muted></Card> : null}
      {data.responseStatus === "DECLINED" ? <Card><Text style={styles.declined}>Angebot abgelehnt</Text><Muted>Ihre Rückmeldung wurde gespeichert.</Muted></Card> : null}
      {!terminal ? <Card>
        <SectionTitle>Ihre Entscheidung</SectionTitle>
        <Muted>{t("Für die Antwort ist kein ORDO-Konto erforderlich.")}</Muted>
        <View style={[styles.decisions, compact && styles.decisionsCompact]}>
          <Button testID="public-offer-accept" title="Angebot annehmen" kind="success" style={{ flex: 1 }} disabled={decision.isPending} loading={decision.isPending && decision.variables === "accept"} onPress={() => decision.mutate("accept")} />
          <Button testID="public-offer-decline" title="Angebot ablehnen" kind="danger" style={{ flex: 1 }} disabled={decision.isPending} loading={decision.isPending && decision.variables === "decline"} onPress={() => decision.mutate("decline")} />
        </View>
        {decisionError ? <Text style={styles.error}>{decisionError}</Text> : null}
      </Card> : null}
    </View>
  </ScrollView>;
}

const useStyles = makeStyles((c) => ({
  root: { flex: 1, backgroundColor: c.surfaceSecondary },
  scroll: { alignItems: "center", paddingHorizontal: 18, paddingTop: 28, paddingBottom: 48 },
  container: { width: "100%", maxWidth: 920, gap: 14 },
  containerCompact: { gap: 10 },
  hero: { gap: 6, paddingVertical: 12 },
  eyebrow: { color: c.brandPrimary, fontSize: 14, fontWeight: "800" },
  title: { color: c.onSurface, fontSize: 30, fontWeight: "900", letterSpacing: -0.8 },
  recipient: { color: c.onSurface, fontSize: 18, fontWeight: "800" },
  line: { gap: 5, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: c.divider },
  lineMain: { gap: 2 },
  lineName: { color: c.onSurface, fontSize: 15, fontWeight: "800" },
  lineValues: { flexDirection: "row", flexWrap: "wrap", justifyContent: "space-between", gap: 8 },
  lineTotal: { color: c.onSurface, fontWeight: "800" },
  totals: { gap: 8, marginTop: 12 },
  totalRow: { flexDirection: "row", justifyContent: "space-between", gap: 12 },
  grandLabel: { color: c.onSurface, fontSize: 17, fontWeight: "900" },
  grand: { color: c.brandPrimary, fontSize: 19, fontWeight: "900" },
  decisions: { flexDirection: "row", gap: 10 },
  decisionsCompact: { flexDirection: "column" },
  success: { color: c.success, fontSize: 18, fontWeight: "900" },
  declined: { color: c.error, fontSize: 18, fontWeight: "900" },
  error: { color: c.error, fontWeight: "700" },
  state: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, backgroundColor: c.surfaceSecondary },
}));
