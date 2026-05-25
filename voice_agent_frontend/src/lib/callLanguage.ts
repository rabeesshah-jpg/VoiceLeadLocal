/** BCP-47-style codes sent to POST /api/calls/start/ */
export type CallLanguage = "en" | "ar";

export const CALL_LANGUAGES: { value: CallLanguage; label: string }[] = [
  { value: "en", label: "English" },
  { value: "ar", label: "Arabic" },
];

export const DEFAULT_CALL_LANGUAGE: CallLanguage = "en";
