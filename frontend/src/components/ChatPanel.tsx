import { useState } from "react";
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
    <section className="chat-panel" aria-label="Interview conversation">
      <header>
        <h1>Interview Session</h1>
        {status === "streaming" && (
          <span className="status-banner">
            Agent is drafting a reply...
            <button type="button" onClick={onAbort}>
              Cancel
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
          Message the agent
        </label>
        <textarea
          id="chat-input"
          name="message"
          placeholder="Share requirements, constraints, or follow-up questions..."
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          disabled={status === "streaming"}
        />
        <button type="submit" disabled={status === "streaming"}>
          Send
        </button>
      </form>
    </section>
  );
}
