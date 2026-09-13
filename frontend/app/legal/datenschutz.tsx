import { LegalScreen } from "@/src/components/legal-screen";
import { DATENSCHUTZ } from "@/src/legal";

export default function Datenschutz() {
  return <LegalScreen title="Datenschutz" sections={DATENSCHUTZ} />;
}
