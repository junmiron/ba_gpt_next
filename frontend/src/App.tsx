import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { appConfig, type InterviewScope } from "./config";
import { CopilotProvider } from "./providers/CopilotProvider";
import {
  AguiSessionClient,
  type AgentEvent,
  type AgentMessage,
  type TestAgentPersona,
  type TestAgentResult,
} from "./services/session";
import { ActionDrawer } from "./components/ActionDrawer";
import { ChatPanel, type SessionStatus } from "./components/ChatPanel";
import { Sidebar } from "./components/Sidebar";

function createMessageId(role: string) {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `${role}_${crypto.randomUUID()}`;
  }
  return `${role}_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function formatPersonaSummary(persona: TestAgentPersona): string {
  const lines: string[] = [];

  if (persona.projectName && persona.company) {
    const role = persona.stakeholderRole || "Stakeholder";
    lines.push(
      `Simulated stakeholder: ${persona.projectName} (${role}) at ${persona.company}.`,
    );
  } else if (persona.stakeholderRole || persona.company) {
    const role = persona.stakeholderRole || "Stakeholder";
    const company = persona.company ? ` at ${persona.company}` : "";
    lines.push(`Simulated stakeholder role: ${role}${company}.`);
  }

  if (persona.context) {
    lines.push(`Context: ${persona.context}`);
  }
  if (persona.goals.length) {
    lines.push(`Goals: ${persona.goals.join("; ")}`);
  }
  if (persona.risks.length) {
    lines.push(`Risks: ${persona.risks.join("; ")}`);
  }
  if (persona.preferences.length) {
    lines.push(`Preferences: ${persona.preferences.join("; ")}`);
  }
  if (persona.tone) {
    lines.push(`Tone guidance: ${persona.tone}`);
  }

  return lines.length ? lines.join("\n") : "Simulated stakeholder interview in progress.";
}

function formatCompletionSummary(result: TestAgentResult): string {
  const lines: string[] = [];

  if (result.specPath) {
    lines.push(`Functional specification saved to ${result.specPath}`);
  }
  if (result.pdfPath) {
    lines.push(`PDF export: ${result.pdfPath}`);
  }
  if (result.recordId) {
    lines.push(`Transcript id: ${result.recordId}`);
  }
  if (result.reviewWarnings.length) {
    lines.push("Review warnings:");
    for (const note of result.reviewWarnings) {
      lines.push(` - ${note}`);
    }
  }

  return lines.join("\n");
}

export default function App() {
  const [scope, setScope] = useState<InterviewScope>(appConfig.defaultScope);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [status, setStatus] = useState<SessionStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  const sessionClient = useMemo(() => new AguiSessionClient(appConfig.apiBaseUrl, scope), [scope]);

  const messageRef = useRef<AgentMessage[]>(messages);
  const streamRef = useRef<{ abort: () => void; kind: "sse" | "test" } | null>(null);

  useEffect(() => {
    messageRef.current = messages;
  }, [messages]);

  const applyMessages = useCallback((updater: (existing: AgentMessage[]) => AgentMessage[]) => {
    setMessages((current) => {
      const next = updater(current);
      messageRef.current = next;
      return next;
    });
  }, []);

  const handleStreamEvent = useCallback((event: AgentEvent) => {
    switch (event.type) {
      case "RUN_STARTED":
        setStatus("streaming");
        break;
      case "TEXT_MESSAGE_START":
        applyMessages((current) => {
          const exists = current.some((message) => message.id === event.messageId);
          if (exists) {
            return current;
          }
          return [
            ...current,
            {
              id: event.messageId,
              role: "assistant",
              content: "",
            },
          ];
        });
        break;
      case "TEXT_MESSAGE_CONTENT":
        applyMessages((current) => {
          const index = current.findIndex((message) => message.id === event.messageId);
          if (index === -1) {
            return [
              ...current,
              {
                id: event.messageId,
                role: "assistant",
                content: event.delta,
              },
            ];
          }
          const clone = [...current];
          const message = clone[index];
          clone[index] = {
            ...message,
            content: `${message.content}${event.delta}`,
          };
          return clone;
        });
        break;
      case "TEXT_MESSAGE_END":
        // No-op. The corresponding content events already updated the message.
        break;
      case "RUN_FINISHED":
        setStatus("idle");
        break;
      case "RUN_ERROR":
        applyMessages((current) => [
          ...current,
          {
            id: createMessageId("system"),
            role: "system",
            content: `Agent error: ${event.message}`,
          },
        ]);
        setStatus("error");
        setError(event.message);
        break;
      default:
        break;
    }
  }, [applyMessages]);

  useEffect(() => () => streamRef.current?.abort(), []);

  useEffect(() => {
    streamRef.current?.abort();
    applyMessages(() => []);
    setError(null);
    setStatus("streaming");

    let cancelled = false;
    const stream = sessionClient.stream([]);
    streamRef.current = { abort: stream.abort, kind: "sse" };

    (async () => {
      try {
        for await (const event of stream.events) {
          if (cancelled) {
            break;
          }
          handleStreamEvent(event);
        }
        if (!cancelled) {
          setStatus("idle");
        }
      } catch (err) {
        if ((err as DOMException)?.name === "AbortError") {
          if (!cancelled) {
            setStatus("idle");
          }
        } else {
          console.error("Failed to initialize session", err);
          const message = err instanceof Error ? err.message : "Unable to start the interview session.";
          setStatus("error");
          setError(message);
          applyMessages((current) => [
            ...current,
            {
              id: createMessageId("system"),
              role: "system",
              content: `Startup error: ${message}`,
            },
          ]);
        }
      } finally {
        if (streamRef.current?.abort === stream.abort) {
          streamRef.current = null;
        }
      }
    })();

    return () => {
      cancelled = true;
      streamRef.current?.abort();
    };
  }, [applyMessages, handleStreamEvent, sessionClient]);

  const handleSend = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) {
        return;
      }

      streamRef.current?.abort();

      const userMessage: AgentMessage = {
        id: createMessageId("user"),
        role: "user",
        content: trimmed,
      };

      const history = [...messageRef.current, userMessage];
      applyMessages(() => history);
      setStatus("streaming");
      setError(null);

      const stream = sessionClient.stream(history);
      streamRef.current = { abort: stream.abort, kind: "sse" };

      try {
        for await (const event of stream.events) {
          handleStreamEvent(event);
        }
        setStatus((current) => (current === "streaming" ? "idle" : current));
      } catch (err) {
        if ((err as DOMException)?.name === "AbortError") {
          setStatus("idle");
          return;
        }
        console.error("AG-UI stream failure", err);
        const message = err instanceof Error ? err.message : "Unexpected failure while streaming.";
        setStatus("error");
        setError(message);
        applyMessages((current) => [
          ...current,
          {
            id: createMessageId("system"),
            role: "system",
            content: `Stream error: ${message}`,
          },
        ]);
      } finally {
        if (streamRef.current?.abort === stream.abort) {
          streamRef.current = null;
        }
      }
    },
    [applyMessages, handleStreamEvent, sessionClient]
  );

  const handleAbort = useCallback(() => {
    const entry = streamRef.current;
    if (!entry) {
      setStatus("idle");
      return;
    }
    streamRef.current = null;
    entry.abort();
    setStatus("idle");
    if (entry.kind === "test") {
      setError(null);
      applyMessages(() => [
        {
          id: createMessageId("system"),
          role: "system",
          content: "Test agent simulation cancelled.",
        },
      ]);
    }
  }, [applyMessages]);

  const handleRequestExport = useCallback(() => {
    console.info("Export spec requested.");
  }, []);

  const handleRequestDiagram = useCallback(() => {
    console.info("Diagram generation requested.");
  }, []);

  const handleOpenTranscript = useCallback(() => {
    console.info("Transcript archive requested.");
  }, []);

  const handleRunTestAgent = useCallback(async () => {
    streamRef.current?.abort();
    setStatus("streaming");
    setError(null);

    const placeholder: AgentMessage = {
      id: createMessageId("system"),
      role: "system",
      content: "Running simulated stakeholder interview...",
    };
    applyMessages(() => [placeholder]);

    const stream = sessionClient.streamTestAgent();
    streamRef.current = { abort: stream.abort, kind: "test" };

    const appendSystemMessage = (content: string) => {
      const text = content.trim();
      if (!text) {
        return;
      }
      applyMessages((current) => [
        ...current,
        {
          id: createMessageId("system"),
          role: "system",
          content: text,
        },
      ]);
    };

    const appendRoleMessage = (role: AgentMessage["role"], content: string) => {
      const text = content.trim();
      if (!text) {
        return;
      }
      applyMessages((current) => [
        ...current,
        {
          id: createMessageId(role),
          role,
          content: text,
        },
      ]);
    };

    let encounteredError = false;

    try {
      for await (const event of stream.events) {
        let terminate = false;

        switch (event.type) {
          case "persona": {
            const summary = formatPersonaSummary(event.persona);
            applyMessages((current) => {
              const updated: AgentMessage = {
                id: current[0]?.id ?? createMessageId("system"),
                role: "system",
                content: summary,
              };
              if (!current.length) {
                return [updated];
              }
              const clone = [...current];
              clone[0] = updated;
              return clone;
            });
            break;
          }
          case "message":
            appendRoleMessage(event.role, event.content);
            break;
          case "status":
            appendSystemMessage(event.content);
            break;
          case "specDraft":
            appendSystemMessage(`Functional specification draft:\n${event.content}`);
            break;
          case "specFinal":
            appendSystemMessage(`Functional specification confirmed:\n${event.content}`);
            break;
          case "reviewFeedback":
            appendSystemMessage(`Reviewer agent: ${event.content}`);
            break;
          case "reviewWarning":
            appendSystemMessage(`Review warning: ${event.note}`);
            break;
          case "reviewNote":
            appendSystemMessage(`Reviewer note: ${event.note}`);
            break;
          case "artifact":
            if (event.kind === "spec_markdown" && event.path) {
              appendSystemMessage(`Functional specification saved to ${event.path}`);
            } else if (event.kind === "spec_pdf" && event.path) {
              appendSystemMessage(`PDF export: ${event.path}`);
            } else if (event.kind === "transcript_record" && event.recordId) {
              appendSystemMessage(`Transcript id: ${event.recordId}`);
            } else if (event.kind) {
              const parts: string[] = [];
              if (event.path) {
                parts.push(`path=${event.path}`);
              }
              if (event.recordId) {
                parts.push(`record=${event.recordId}`);
              }
              appendSystemMessage(`${event.kind}${parts.length ? ` (${parts.join(", ")})` : ""}`);
            }
            break;
          case "complete":
            appendSystemMessage(formatCompletionSummary(event.result));
            break;
          case "error": {
            const message = event.message || "Test agent simulation failed.";
            appendSystemMessage(`Test agent error: ${message}`);
            setStatus("error");
            setError(message);
            encounteredError = true;
            terminate = true;
            break;
          }
          default:
            break;
        }

        if (terminate) {
          break;
        }
      }

      if (!encounteredError) {
        setStatus("idle");
      }
    } catch (err) {
      if ((err as DOMException)?.name === "AbortError") {
        setStatus("idle");
      } else {
        console.error("Test agent stream failed", err);
        const message = err instanceof Error ? err.message : "Unable to run the test agent simulation.";
        appendSystemMessage(`Test agent error: ${message}`);
        setStatus("error");
        setError(message);
      }
    } finally {
      if (streamRef.current?.abort === stream.abort) {
        streamRef.current = null;
      }
    }
  }, [applyMessages, sessionClient]);

  return (
    <CopilotProvider scope={scope}>
      <div className="app-shell">
        <Sidebar scope={scope} onScopeChange={setScope} status={status} />
        <ChatPanel messages={messages} status={status} error={error} onSend={handleSend} onAbort={handleAbort} />
        <ActionDrawer
          status={status}
          onRunTestAgent={handleRunTestAgent}
          onRequestExport={handleRequestExport}
          onRequestDiagram={handleRequestDiagram}
          onOpenTranscript={handleOpenTranscript}
        />
      </div>
    </CopilotProvider>
  );
}
