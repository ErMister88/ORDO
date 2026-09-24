const uploadsSetting = process.env.EXPO_PUBLIC_UPLOADS_ENABLED;

if (uploadsSetting && uploadsSetting !== "true" && uploadsSetting !== "false") {
  throw new Error("EXPO_PUBLIC_UPLOADS_ENABLED muss 'true' oder 'false' sein.");
}

// Upload controls remain hidden until a deployment explicitly confirms that
// its private object storage is configured and operational.
export const uploadsEnabled = uploadsSetting === "true";
