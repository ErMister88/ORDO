import { useEffect } from "react";
import { Platform } from "react-native";

function ensureMeta(selector: string, attribute: "name" | "property", value: string) {
  let element = document.querySelector(selector) as HTMLMetaElement | null;
  if (!element) {
    element = document.createElement("meta");
    element.setAttribute(attribute, value);
    document.head.appendChild(element);
  }
  return element;
}

export function useCommerceMetadata({ title, description = "", path, image = "" }: { title?: string; description?: string; path: string; image?: string }) {
  useEffect(() => {
    if (Platform.OS !== "web" || !title) return;
    document.title = title;
    ensureMeta('meta[name="description"]', "name", "description").content = description;
    ensureMeta('meta[property="og:title"]', "property", "og:title").content = title;
    ensureMeta('meta[property="og:description"]', "property", "og:description").content = description;
    ensureMeta('meta[property="og:type"]', "property", "og:type").content = "website";
    if (image) ensureMeta('meta[property="og:image"]', "property", "og:image").content = image;
    let canonical = document.querySelector('link[rel="canonical"]') as HTMLLinkElement | null;
    if (!canonical) {
      canonical = document.createElement("link");
      canonical.rel = "canonical";
      document.head.appendChild(canonical);
    }
    canonical.href = `${window.location.origin}${path}`;
    ensureMeta('meta[property="og:url"]', "property", "og:url").content = canonical.href;
  }, [description, image, path, title]);
}
