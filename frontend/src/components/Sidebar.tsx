import { useMemo } from "react";
import type { InterviewScope } from "../config";
import type { LanguageCode } from "../localization/translations";
import { useLocalization } from "../providers/LocalizationProvider";
import type { SessionStatus } from "./ChatPanel";

interface SidebarProps {
  scope: InterviewScope;
  onScopeChange: (scope: InterviewScope) => void;
  status: SessionStatus;
}

export function Sidebar({ scope, onScopeChange, status }: SidebarProps) {
  const { t, language, setLanguage, availableLanguages } = useLocalization();

  const scopeOptions = useMemo(
    () => [
      {
        value: "project" as InterviewScope,
        label: t("scope.project.label"),
        description: t("scope.project.description"),
      },
      {
        value: "process" as InterviewScope,
        label: t("scope.process.label"),
        description: t("scope.process.description"),
      },
      {
        value: "change_request" as InterviewScope,
        label: t("scope.changeRequest.label"),
        description: t("scope.changeRequest.description"),
      },
    ],
    [t]
  );

  const selectedDescription =
    scopeOptions.find((option) => option.value === scope)?.description ?? t("sidebar.scopeHint");

  return (
    <aside className="sidebar" aria-label={t("sidebar.ariaLabel")}>
      <h2>{t("sidebar.title")}</h2>
      <p>{t("sidebar.description")}</p>

      <div className="language-selector">
        <label htmlFor="language-select">{t("sidebar.languageLabel")}</label>
        <select
          id="language-select"
          value={language}
          onChange={(event) => setLanguage(event.target.value as LanguageCode)}
        >
          {availableLanguages.map((item) => (
            <option key={item.code} value={item.code}>
              {item.label}
            </option>
          ))}
        </select>
      </div>

      <div className="scope-selector">
        <label htmlFor="scope-select">{t("sidebar.scopeLabel")}</label>
        <select
          id="scope-select"
          value={scope}
          disabled={status === "streaming"}
          onChange={(event) => onScopeChange(event.target.value as InterviewScope)}
        >
          {scopeOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <small>{selectedDescription}</small>
      </div>
    </aside>
  );
}
