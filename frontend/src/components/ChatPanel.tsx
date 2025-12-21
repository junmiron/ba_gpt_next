import { useState } from "react";
import { useLocalization } from "../providers/LocalizationProvider";
import type { AgentMessage } from "../services/session";

export type SessionStatus = "idle" | "streaming" | "error";

interface ChatPanelProps {
  messages: AgentMessage[];
  status: SessionStatus;
  error: string | null;
  onSend: (text: string) => Promise<void> | void;
  onAbort: () => void;
}

export function ChatPanel({ messages, status, error, onSend, onAbort }: ChatPanelProps) {
  const [draft, setDraft] = useState("");
  const { t } = useLocalization();

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = draft.trim();
    if (!value || status === "streaming") {
      return;
    }
    await onSend(value);
    setDraft("");
  };

  return (
    <section className="chat-panel" aria-label={t("chat.ariaLabel")}>
      <header>
        <h1>{t("chat.title")}</h1>
        {status === "streaming" && (
          <span className="status-banner">
            {t("chat.status.streaming")}
            <button type="button" onClick={onAbort}>
              {t("chat.cancel")}
            </button>
          </span>
        )}
        {status === "error" && error && <span className="status-banner error">{error}</span>}
      </header>

      <div className="chat-scroll">
        {messages.map((message) => (
          <article key={message.id} className={`message ${message.role}`}>
            {message.content}
          </article>
        ))}
      </div>

      <form className="composer" onSubmit={handleSubmit}>
        <label className="visually-hidden" htmlFor="chat-input">
          {t("chat.label")}
        </label>
        <textarea
          id="chat-input"
          name="message"
          placeholder={t("chat.placeholder")}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          disabled={status === "streaming"}
        />
        <button type="submit" disabled={status === "streaming"}>
          {t("chat.send")}
        </button>
      </form>
    </section>
  );
}
