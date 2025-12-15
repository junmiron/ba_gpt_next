import type { SessionStatus } from "./ChatPanel";

interface ActionDrawerProps {
  status: SessionStatus;
  onRunTestAgent: () => void;
  onRequestExport: () => void;
}

export function ActionDrawer({ status, onRunTestAgent, onRequestExport }: ActionDrawerProps) {
  const disabled = status === "streaming";

  return (
    <aside className="action-drawer" aria-label="Recommended actions">
      <header>
        <h2>Artifacts &amp; Insights</h2>
        <p>Trigger downstream tasks while the interview progresses.</p>
      </header>

      <section className="action-card">
        <h3>Run Test Agent</h3>
        <p>Simulate the interview with an AI stakeholder to validate prompts and artifact generation.</p>
        <button type="button" onClick={onRunTestAgent} disabled={disabled}>
          Launch simulation
        </button>
      </section>

      <section className="action-card">
        <h3>View Functional Specification</h3>
        <p>Open the current draft as Markdown or PDF to review collected insights and diagrams.</p>
        <button type="button" onClick={onRequestExport} disabled={disabled}>
          View draft
        </button>
      </section>

    </aside>
  );
}
