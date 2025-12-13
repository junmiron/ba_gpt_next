import type { InterviewScope } from "../config";
import type { SessionStatus } from "./ChatPanel";

interface SidebarProps {
  scope: InterviewScope;
  onScopeChange: (scope: InterviewScope) => void;
  status: SessionStatus;
}

const scopeOptions: Array<{ value: InterviewScope; label: string; description: string }> = [
  {
    value: "project",
    label: "Project Discovery",
    description: "Elicit goals, stakeholders, and constraints for a net-new initiative.",
  },
  {
    value: "process",
    label: "Process Optimization",
    description: "Map current workflows and pain points to unlock efficiency gains.",
  },
  {
    value: "change_request",
    label: "Change Request",
    description: "Capture scoped updates for an existing product or service area.",
  },
];

export function Sidebar({ scope, onScopeChange, status }: SidebarProps) {
  return (
    <aside className="sidebar" aria-label="Session settings">
      <h2>Business Analyst Assistant</h2>
      <p>
        Lead an interview to draft a functional specification. The assistant captures transcripts, iterates on
        deliverables, and syncs artifacts to the shared workspace.
      </p>

      <div className="scope-selector">
        <label htmlFor="scope-select">Interview scope</label>
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
        <small>
          {
            scopeOptions.find((option) => option.value === scope)?.description ??
            "Select a scope to tailor the interview agenda."
          }
        </small>
      </div>
    </aside>
  );
}
