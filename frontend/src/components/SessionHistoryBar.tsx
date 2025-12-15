import type { ChangeEvent } from "react";
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
  const hasHistory = sessions.length > 0;

  const handleChange = (event: ChangeEvent<HTMLSelectElement>) => {
    const value = event.target.value.trim();
    onSelectSession(value || null);
  };

  return (
    <div className="session-history-bar" aria-label="Completed session selector">
      <div className="session-history-bar__info">
        <h2>Session History</h2>
        <p>Open previous interviews to review specs, transcripts, and feedback.</p>
      </div>
      <div className="session-selector">
        <label htmlFor="session-history-select">Choose a session</label>
        <div className="session-selector__controls">
          <select
            id="session-history-select"
            value={selectedSessionId ?? ""}
            onChange={handleChange}
            disabled={disabled}
          >
            <option value="">Current session</option>
            {sessions.map((summary) => (
              <option key={summary.id} value={summary.id}>
                {summary.id} — {formatTimestamp(summary.createdAt) ?? "unknown"} · {summary.turnCount} turns
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
              Refresh
            </button>
          )}
        </div>
        {!hasHistory && !isLoading && <p className="session-selector__hint">No completed sessions found yet.</p>}
        {isLoading && <p className="session-selector__hint">Loading sessions…</p>}
      </div>
    </div>
  );
}
