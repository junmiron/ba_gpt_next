import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { defaultLanguage, translations } from "../localization/translations";
import type { LanguageCode, TranslationKey } from "../localization/translations";

interface LocalizationContextValue {
  language: LanguageCode;
  setLanguage: (language: LanguageCode) => void;
  t: (key: TranslationKey, params?: Record<string, string | number>) => string;
  availableLanguages: Array<{ code: LanguageCode; label: string }>;
}

const LocalizationContext = createContext<LocalizationContextValue | undefined>(undefined);
const LANGUAGE_STORAGE_KEY = "ba_interview_language";

function formatTemplate(template: string, params?: Record<string, string | number>): string {
  if (!params) {
    return template;
  }
  return template.replace(/\{(.*?)\}/g, (match, token) => {
    const raw = params[token];
    return raw == null ? match : String(raw);
  });
}

export function LocalizationProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<LanguageCode>(() => {
    if (typeof window === "undefined") {
      return defaultLanguage;
    }
    const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (stored && (stored === "en" || stored === "es")) {
      return stored as LanguageCode;
    }
    return defaultLanguage;
  });

  const setLanguage = useCallback((code: LanguageCode) => {
    setLanguageState(code);
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(LANGUAGE_STORAGE_KEY, code);
      } catch {
        // Ignore storage errors (e.g., private mode).
      }
    }
  }, []);

  const translate = useCallback(
    (key: TranslationKey, params?: Record<string, string | number>) => {
      const languageTable = translations[language] ?? translations[defaultLanguage];
      const fallbackTable = translations[defaultLanguage];
      const template = languageTable[key] ?? fallbackTable[key] ?? key;
      return formatTemplate(template, params);
    },
    [language]
  );

  const availableLanguages = useMemo(() => {
    const languageTable = translations[language] ?? translations[defaultLanguage];
    return (Object.keys(translations) as LanguageCode[]).map((code) => {
      const labelKey = code === "en" ? "language.english" : "language.spanish";
      const label = languageTable[labelKey] ?? translations[defaultLanguage][labelKey];
      return {
        code,
        label,
      };
    });
  }, [language]);

  const value = useMemo<LocalizationContextValue>(
    () => ({ language, setLanguage, t: translate, availableLanguages }),
    [language, translate, availableLanguages, setLanguage]
  );

  return <LocalizationContext.Provider value={value}>{children}</LocalizationContext.Provider>;
}

export function useLocalization(): LocalizationContextValue {
  const context = useContext(LocalizationContext);
  if (!context) {
    throw new Error("useLocalization must be used within a LocalizationProvider");
  }
  return context;
}
