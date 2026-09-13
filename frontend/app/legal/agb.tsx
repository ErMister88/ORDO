import { LegalScreen } from "@/src/components/legal-screen";
import { AGB } from "@/src/legal";

export default function Agb() {
  return <LegalScreen title="AGB" sections={AGB} />;
}
