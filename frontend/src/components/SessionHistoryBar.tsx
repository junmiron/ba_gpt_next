import type { ChangeEvent } from "react";
import { useLocalization } from "../providers/LocalizationProvider";
import type { SessionSummary } from "../services/session";

interface SessionHistoryBarProps {
  sessions: SessionSummary[];
  selectedSessionId: string | null;
  onSelectSession: (sessionId: string | null) => void;
  onRefreshSessions?: () => void;
  disabled?: boolean;
  isLoading?: boolean;
}

function formatTimestamp(value?: string | null): string | null {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed.toLocaleString();
}

export function SessionHistoryBar({
  sessions,
  selectedSessionId,
  onSelectSession,
  onRefreshSessions,
  disabled = false,
  isLoading = false,
}: SessionHistoryBarProps) {
  const { t } = useLocalization();
  const hasHistory = sessions.length > 0;

  const handleChange = (event: ChangeEvent<HTMLSelectElement>) => {
    const value = event.target.value.trim();
    onSelectSession(value || null);
  };

  return (
    <div className="session-history-bar" aria-label={t("history.ariaLabel")}>
      <div className="session-history-bar__info">
        <h2>{t("history.title")}</h2>
        <p>{t("history.description")}</p>
      </div>
      <div className="session-selector">
        <label htmlFor="session-history-select">{t("history.label")}</label>
        <div className="session-selector__controls">
          <select
            id="session-history-select"
            value={selectedSessionId ?? ""}
            onChange={handleChange}
            disabled={disabled}
          >
            <option value="">{t("history.currentOption")}</option>
            {sessions.map((summary) => (
              <option key={summary.id} value={summary.id}>
                {t("history.optionLabel", {
                  id: summary.id,
                  timestamp: formatTimestamp(summary.createdAt) ?? t("history.unknownTime"),
                  turns: t("history.turns", { count: summary.turnCount }),
                })}
              </option>
            ))}
          </select>
          {onRefreshSessions && (
            <button
              type="button"
              className="session-selector__refresh"
              onClick={onRefreshSessions}
              disabled={disabled}
            >
              {t("history.refresh")}
            </button>
          )}
        </div>
        {!hasHistory && !isLoading && (
          <p className="session-selector__hint">{t("history.noSessions")}</p>
        )}
        {isLoading && <p className="session-selector__hint">{t("history.loading")}</p>}
      </div>
    </div>
  );
}
