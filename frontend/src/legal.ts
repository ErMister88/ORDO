// Company + legal texts. Placeholders marked [ ... ] must be filled with real data
// and legal texts should be reviewed by a lawyer before going live.

export const COMPANY = {
  name: "S&S coffee and more GbR",
  street: "Barstenweg 3",
  zip: "92348",
  city: "Berg",
  vatId: "DE449803615",
  representatives: "Sergio Lucchetta und Salvatore Lucchetta",
  email: "Info@aiello-germany.de",
  phone: "+49 151 23500862",
};

export type LegalSection = { heading?: string; body: string };

const addr = `${COMPANY.name}\n${COMPANY.street}\n${COMPANY.zip} ${COMPANY.city}`;

export const IMPRESSUM: LegalSection[] = [
  { heading: "Angaben gemäß § 5 DDG", body: addr },
  { heading: "Vertreten durch", body: COMPANY.representatives },
  { heading: "Kontakt", body: `E-Mail: ${COMPANY.email}\nTelefon: ${COMPANY.phone}` },
  { heading: "Umsatzsteuer-Identifikationsnummer", body: `USt-IdNr. gemäß § 27 a UStG: ${COMPANY.vatId}` },
  {
    heading: "Verbraucherstreitbeilegung",
    body:
      "Die EU-Kommission stellt eine Plattform zur Online-Streitbeilegung (OS) bereit: https://ec.europa.eu/consumers/odr. " +
      "Wir sind nicht verpflichtet und nicht bereit, an einem Streitbeilegungsverfahren vor einer Verbraucherschlichtungsstelle teilzunehmen.",
  },
  {
    heading: "Haftung für Inhalte",
    body:
      "Als Diensteanbieter sind wir für eigene Inhalte in dieser App nach den allgemeinen Gesetzen verantwortlich. " +
      "Verpflichtungen zur Entfernung oder Sperrung der Nutzung von Informationen nach den allgemeinen Gesetzen bleiben unberührt.",
  },
];

export const DATENSCHUTZ: LegalSection[] = [
  {
    heading: "1. Verantwortlicher",
    body: `Verantwortlich für die Datenverarbeitung ist:\n${addr}\nE-Mail: ${COMPANY.email}`,
  },
  {
    heading: "2. Welche Daten wir verarbeiten",
    body:
      "• Bestelldaten (Name, Anschrift, E-Mail, Telefon, bestellte Artikel)\n" +
      "• Kontodaten bei Registrierung (Name, E-Mail, verschlüsseltes Passwort)\n" +
      "• Newsletter-Daten (E-Mail, Anmeldezeitpunkt)\n" +
      "• Zahlungsabwicklung über unseren Dienstleister Stripe\n" +
      "• Optional: Push-Token, wenn Sie Benachrichtigungen erlauben",
  },
  {
    heading: "3. Zwecke & Rechtsgrundlagen",
    body:
      "Wir verarbeiten Ihre Daten zur Vertragserfüllung (Art. 6 Abs. 1 lit. b DSGVO), zur Erfüllung rechtlicher " +
      "Pflichten (lit. c) sowie auf Grundlage Ihrer Einwilligung, z. B. beim Newsletter und bei Push-Nachrichten (lit. a).",
  },
  {
    heading: "4. Newsletter",
    body:
      "Für den Newsletter nutzen wir das Double-Opt-In-Verfahren: Nach Ihrer Anmeldung erhalten Sie eine E-Mail mit " +
      "Bestätigungslink. Erst nach Bestätigung versenden wir den Newsletter. Sie können Ihre Einwilligung jederzeit " +
      "mit Wirkung für die Zukunft widerrufen – über den Abmeldelink in jeder Newsletter-E-Mail oder per Nachricht an uns.",
  },
  {
    heading: "5. Push-Benachrichtigungen",
    body:
      "Wenn Sie Push-Benachrichtigungen erlauben, verarbeiten wir ein Gerät-Token, um Sie über Angebote zu informieren. " +
      "Grundlage ist Ihre Einwilligung (Art. 6 Abs. 1 lit. a DSGVO), die Sie jederzeit in den Geräteeinstellungen " +
      "widerrufen können.",
  },
  {
    heading: "6. Empfänger / Auftragsverarbeiter",
    body:
      "Zur Zahlungsabwicklung: Stripe. Zum E-Mail-Versand nutzen wir einen E-Mail-Dienstleister. " +
      "Mit diesen bestehen Verträge zur Auftragsverarbeitung, soweit erforderlich.",
  },
  {
    heading: "7. Speicherdauer",
    body:
      "Wir speichern personenbezogene Daten nur so lange, wie es für die genannten Zwecke erforderlich ist oder " +
      "gesetzliche Aufbewahrungsfristen (z. B. handels- und steuerrechtlich) dies vorschreiben.",
  },
  {
    heading: "8. Ihre Rechte",
    body:
      "Sie haben das Recht auf Auskunft, Berichtigung, Löschung, Einschränkung der Verarbeitung, Datenübertragbarkeit " +
      "sowie Widerspruch. Zudem können Sie sich bei einer Aufsichtsbehörde beschweren.",
  },
];

export const AGB: LegalSection[] = [
  {
    heading: "§ 1 Geltungsbereich",
    body:
      "Diese Allgemeinen Geschäftsbedingungen gelten für alle Bestellungen über den S&S Kaffee-Shop in dieser App " +
      `gegenüber ${COMPANY.name}.`,
  },
  {
    heading: "§ 2 Vertragspartner, Vertragsschluss",
    body:
      "Der Kaufvertrag kommt zustande mit " + COMPANY.name + ". Mit dem Absenden der Bestellung geben Sie ein " +
      "verbindliches Angebot ab. Wir bestätigen den Eingang der Bestellung per E-Mail (Bestellbestätigung).",
  },
  {
    heading: "§ 3 Preise und Versandkosten",
    body:
      "Alle Preise verstehen sich inklusive der gesetzlichen Mehrwertsteuer (7% auf Kaffeeprodukte, 19% auf Maschinen). " +
      "Etwaige Versandkosten werden im Warenkorb gesondert ausgewiesen.",
  },
  {
    heading: "§ 4 Zahlung",
    body:
      "Die Zahlung erfolgt sicher über unseren Zahlungsdienstleister Stripe (u. a. Kreditkarte, PayPal, Sofortüberweisung), " +
      "sofern die jeweilige Methode verfügbar ist.",
  },
  {
    heading: "§ 5 Lieferung",
    body: "Die Lieferung erfolgt an die von Ihnen angegebene Lieferadresse.",
  },
  {
    heading: "§ 6 Widerrufsrecht",
    body:
      "Verbrauchern steht ein gesetzliches Widerrufsrecht zu. Einzelheiten entnehmen Sie bitte der Widerrufsbelehrung.",
  },
  {
    heading: "§ 7 Eigentumsvorbehalt",
    body: "Die Ware bleibt bis zur vollständigen Bezahlung unser Eigentum.",
  },
  {
    heading: "§ 8 Gewährleistung",
    body:
      "Es gilt das gesetzliche Mängelhaftungsrecht. Bei berechtigten Mängeln haben Sie im Rahmen der gesetzlichen " +
      "Bestimmungen Anspruch auf Nacherfüllung, Rücktritt oder Minderung.",
  },
  {
    heading: "§ 9 Streitbeilegung",
    body:
      "Plattform der EU-Kommission zur Online-Streitbeilegung: https://ec.europa.eu/consumers/odr. Wir sind zur " +
      "Teilnahme an einem Streitbeilegungsverfahren vor einer Verbraucherschlichtungsstelle weder verpflichtet noch bereit.",
  },
];

export const WIDERRUF: LegalSection[] = [
  {
    heading: "Widerrufsrecht",
    body:
      "Sie haben das Recht, binnen vierzehn Tagen ohne Angabe von Gründen diesen Vertrag zu widerrufen. Die " +
      "Widerrufsfrist beträgt vierzehn Tage ab dem Tag, an dem Sie oder ein von Ihnen benannter Dritter, der nicht " +
      "der Beförderer ist, die Waren in Besitz genommen haben bzw. hat.",
  },
  {
    heading: "Ausübung des Widerrufs",
    body:
      "Um Ihr Widerrufsrecht auszuüben, müssen Sie uns (" + COMPANY.name + ", " + COMPANY.street + ", " +
      COMPANY.zip + " " + COMPANY.city + ", E-Mail: " + COMPANY.email + ") mittels einer eindeutigen Erklärung " +
      "(z. B. per Post oder E-Mail) über Ihren Entschluss, diesen Vertrag zu widerrufen, informieren.",
  },
  {
    heading: "Folgen des Widerrufs",
    body:
      "Wenn Sie diesen Vertrag widerrufen, haben wir Ihnen alle Zahlungen, die wir von Ihnen erhalten haben, " +
      "einschließlich der Lieferkosten, unverzüglich und spätestens binnen vierzehn Tagen zurückzuzahlen.",
  },
  {
    heading: "Muster-Widerrufsformular",
    body:
      "An " + COMPANY.name + ", " + COMPANY.street + ", " + COMPANY.zip + " " + COMPANY.city + ":\n" +
      "Hiermit widerrufe(n) ich/wir den von mir/uns abgeschlossenen Vertrag über den Kauf der folgenden Waren:\n" +
      "— Bestellt am / erhalten am:\n— Name des/der Verbraucher(s):\n— Anschrift:\n— Datum, Unterschrift (nur bei Papier):",
  },
];
