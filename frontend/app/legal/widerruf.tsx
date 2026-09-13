import { LegalScreen } from "@/src/components/legal-screen";
import { WIDERRUF } from "@/src/legal";

export default function Widerruf() {
  return <LegalScreen title="Widerrufsbelehrung" sections={WIDERRUF} />;
}
