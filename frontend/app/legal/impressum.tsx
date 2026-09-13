import { LegalScreen } from "@/src/components/legal-screen";
import { IMPRESSUM } from "@/src/legal";

export default function Impressum() {
  return <LegalScreen title="Impressum" sections={IMPRESSUM} />;
}
