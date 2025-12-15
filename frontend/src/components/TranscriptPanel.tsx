import type { InterviewScope } from "../config";
import type { SessionDetail, TranscriptMessage } from "../services/session";

interface TranscriptPanelProps {
  session: SessionDetail | null;
  scope: InterviewScope;
  isLoading: boolean;
  onClose: () => void;
}

function formatTimestamp(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed.toLocaleString();
}

export function TranscriptPanel({ session, scope, isLoading, onClose }: TranscriptPanelProps) {
  const transcript: TranscriptMessage[] = session?.transcript ?? [];
  const createdAtText = formatTimestamp(session?.createdAt);

  return (
    <section className="chat-panel" aria-label="Transcript archive">
      <header>
        <div>
          <h1>{session ? `Transcript — ${session.id}` : "Transcript Archive"}</h1>
          <p className="transcript-meta">
            {session
              ? `Scope: ${session.scope} · ${createdAtText ?? "Unknown date"}`
              : `Scope: ${scope}`}
          </p>
        </div>
        <button type="button" className="link-button" onClick={onClose}>
          Back to interview
        </button>
      </header>

      <div className="chat-scroll">
        {isLoading ? (
          <p className="transcript-status">Loading transcript…</p>
        ) : transcript.length > 0 ? (
          transcript.map((message) => (
            <article key={message.id} className={`message ${message.role}`}>
              {message.content}
            </article>
          ))
        ) : (
          <p className="transcript-status">No transcript content is available for this session yet.</p>
        )}
      </div>
    </section>
  );
}
