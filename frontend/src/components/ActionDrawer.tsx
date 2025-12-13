import type { SessionStatus } from "./ChatPanel";

interface ActionDrawerProps {
  status: SessionStatus;
  onRunTestAgent: () => void;
  onRequestExport: () => void;
  onRequestDiagram: () => void;
  onOpenTranscript: () => void;
}

export function ActionDrawer({ status, onRunTestAgent, onRequestExport, onRequestDiagram, onOpenTranscript }: ActionDrawerProps) {
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
        <h3>Export Functional Specification</h3>
        <p>Generate the latest Markdown/PDF draft using the current interview transcript.</p>
        <button type="button" onClick={onRequestExport} disabled={disabled}>
          Export draft
        </button>
      </section>

      <section className="action-card">
        <h3>Visualize Workflow</h3>
        <p>Create a system diagram outlining the to-be process for stakeholder review.</p>
        <button type="button" onClick={onRequestDiagram} disabled={disabled}>
          Build diagram
        </button>
      </section>

      <section className="action-card">
        <h3>View Transcript</h3>
        <p>Open the persisted transcript store to audit prior sessions.</p>
        <button type="button" onClick={onOpenTranscript}>
          Open archive
        </button>
      </section>
    </aside>
  );
}
