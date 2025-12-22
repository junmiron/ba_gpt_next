import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { appConfig, type InterviewScope } from "./config";
import { CopilotProvider } from "./providers/CopilotProvider";
import {
  AguiSessionClient,
  type AgentEvent,
  type AgentMessage,
  type TestAgentPersona,
  type TestAgentResult,
  type SpecPreview,
  type SessionSummary,
  type SessionDetail,
  type SpecFeedbackEntry,
} from "./services/session";
import { ActionDrawer } from "./components/ActionDrawer";
import { ChatPanel, type SessionStatus } from "./components/ChatPanel";
import { SpecPanel } from "./components/SpecPanel";
import { TranscriptPanel } from "./components/TranscriptPanel";
import { Sidebar } from "./components/Sidebar";
import { SessionHistoryBar } from "./components/SessionHistoryBar";
import { useLocalization } from "./providers/LocalizationProvider";

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
  const { t, language } = useLocalization();
  const [scope, setScope] = useState<InterviewScope>(appConfig.defaultScope);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [status, setStatus] = useState<SessionStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"chat" | "spec" | "transcript">("chat");
  const [specPreview, setSpecPreview] = useState<SpecPreview | null>(null);
  const [showSpecDialog, setShowSpecDialog] = useState(false);
  const [isSpecLoading, setIsSpecLoading] = useState(false);
  const [sessionSummaries, setSessionSummaries] = useState<SessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [selectedSessionDetail, setSelectedSessionDetail] = useState<SessionDetail | null>(null);
  const [specSource, setSpecSource] = useState<"current" | "historical">("current");
  const [isHistoryLoading, setIsHistoryLoading] = useState(false);
  const [isFeedbackSubmitting, setIsFeedbackSubmitting] = useState(false);
  const [currentFeedbackEntries, setCurrentFeedbackEntries] = useState<SpecFeedbackEntry[]>([]);

  const sessionClient = useMemo(
    () => new AguiSessionClient(appConfig.apiBaseUrl, scope, language),
    [scope, language],
  );

  const messageRef = useRef<AgentMessage[]>(messages);
  const streamRef = useRef<{ abort: () => void; kind: "sse" | "test" } | null>(null);

  useEffect(() => {
    setViewMode("chat");
    setSpecPreview(null);
    setShowSpecDialog(false);
    setSelectedSessionId(null);
    setSelectedSessionDetail(null);
    setSpecSource("current");
    setCurrentFeedbackEntries([]);
  }, [scope]);

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
        setSpecSource("current");
        setSelectedSessionId(null);
        setSelectedSessionDetail(null);
        setCurrentFeedbackEntries([]);
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
    if (status === "streaming") {
      setViewMode("chat");
    }
  }, [status]);

  useEffect(() => {
    streamRef.current?.abort();
    applyMessages(() => []);
    setError(null);
    setStatus("streaming");

    let cancelled = false;
    const stream = sessionClient.stream([], { state: { language } });
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
  }, [applyMessages, handleStreamEvent, sessionClient, language]);

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

      const stream = sessionClient.stream(history, { state: { language } });
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
    [applyMessages, handleStreamEvent, sessionClient, language]
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
    if (status === "streaming") {
      return;
    }
    setShowSpecDialog(true);
  }, [status]);

  const fetchSpecPreview = useCallback(
    async (forceRefresh = false) => {
      setIsSpecLoading(true);
      try {
        const threadId = sessionClient.getThreadId();
        const preview = await sessionClient.fetchSpecPreview(threadId, forceRefresh);
        setSpecPreview(preview);
        setError(null);
        setStatus((current) => (current === "error" ? "idle" : current));
        return preview;
      } catch (err) {
        console.error("Specification preview failed", err);
        const message = err instanceof Error ? err.message : "Unable to load the functional specification.";
        setError(message);
        setStatus((current) => (current === "streaming" ? current : "error"));
        return null;
      } finally {
        setIsSpecLoading(false);
      }
    },
    [sessionClient]
  );

  const handleViewMarkdown = useCallback(async () => {
    const preview = await fetchSpecPreview(false);
    setShowSpecDialog(false);
    if (!preview) {
      return;
    }
    setSelectedSessionId(null);
    setSelectedSessionDetail(null);
    setSpecSource("current");
    setViewMode("spec");
  }, [fetchSpecPreview]);

  const handleViewPdf = useCallback(async () => {
    const preview = await fetchSpecPreview(false);
    setShowSpecDialog(false);
    if (!preview) {
      return;
    }
    if (!preview.pdfPath) {
      const message = t("errors.pdfUnavailable");
      setError(message);
      setStatus((current) => (current === "streaming" ? current : "error"));
      return;
    }
    const pdfUrl = sessionClient.buildSpecPdfUrl(sessionClient.getThreadId());
    window.open(pdfUrl, "_blank", "noopener");
  }, [fetchSpecPreview, sessionClient, t]);

  const handleCloseSpecPanel = useCallback(() => {
    setViewMode("chat");
  }, []);

  const handleCloseTranscript = useCallback(() => {
    setViewMode("chat");
  }, []);

  const refreshSessionSummaries = useCallback(async () => {
    setSessionsLoading(true);
    try {
      const summaries = await sessionClient.listSessions(25);
      setSessionSummaries(summaries);
    } catch (err) {
      console.error("Failed to load session summaries", err);
    } finally {
      setSessionsLoading(false);
    }
  }, [sessionClient]);

  useEffect(() => {
    refreshSessionSummaries().catch(() => undefined);
  }, [refreshSessionSummaries]);

  const handleSelectSession = useCallback(async (sessionId: string | null) => {
    if (!sessionId) {
      setSelectedSessionId(null);
      setSelectedSessionDetail(null);
      setSpecSource("current");
      setViewMode("spec");
      return;
    }
    setSelectedSessionId(sessionId);
    setSelectedSessionDetail(null);
    setSpecSource("historical");
    setIsHistoryLoading(true);
    try {
      const detail = await sessionClient.getSessionDetail(sessionId);
      setSelectedSessionDetail(detail);
      setError(null);
      setStatus((current) => (current === "error" ? "idle" : current));
      setViewMode("spec");
    } catch (err) {
      console.error("Failed to load session detail", err);
      const message = err instanceof Error ? err.message : t("errors.sessionSelect");
      setError(message);
      setStatus((current) => (current === "streaming" ? current : "error"));
      setSpecSource("current");
    } finally {
      setIsHistoryLoading(false);
    }
  }, [sessionClient, t]);

  const handleRefreshSessions = useCallback(() => {
    refreshSessionSummaries().catch(() => undefined);
  }, [refreshSessionSummaries]);

  const handleSubmitFeedback = useCallback(
    async (message: string) => {
      const trimmed = message.trim();
      if (!trimmed) {
        throw new Error(t("spec.feedback.emptyError"));
      }

      const currentSessionId = specSource === "historical" ? selectedSessionId : sessionClient.getThreadId();
      if (!currentSessionId) {
        throw new Error(t("errors.sessionIdMissing"));
      }

      setIsFeedbackSubmitting(true);
      try {
        const entry = await sessionClient.submitSpecFeedback(currentSessionId, trimmed);
        if (specSource === "historical" && selectedSessionId) {
          let refreshed = false;
          try {
            const detail = await sessionClient.getSessionDetail(selectedSessionId);
            setSelectedSessionDetail(detail);
            refreshed = true;
          } catch (detailError) {
            console.error("Failed to refresh session detail after feedback", detailError);
          }
          if (!refreshed) {
            setSelectedSessionDetail((current) => {
              if (!current || current.id !== selectedSessionId) {
                return current;
              }
              return {
                ...current,
                feedback: [...current.feedback, entry],
              };
            });
          }
          refreshSessionSummaries().catch(() => undefined);
        } else {
          setCurrentFeedbackEntries((current) => [...current, entry]);
        }
      } catch (err) {
        console.error("Failed to submit specification feedback", err);
        const message = err instanceof Error ? err.message : t("errors.feedbackSubmit");
        throw new Error(message);
      } finally {
        setIsFeedbackSubmitting(false);
      }
    },
    [refreshSessionSummaries, selectedSessionId, sessionClient, specSource, t]
  );

  const handleRunTestAgent = useCallback(async () => {
    streamRef.current?.abort();
    setViewMode("chat");
    setShowSpecDialog(false);
    setSpecPreview(null);
    setStatus("streaming");
    setError(null);

    const placeholder: AgentMessage = {
      id: createMessageId("system"),
      role: "system",
      content: "Running simulated stakeholder interview...",
    };
    applyMessages(() => [placeholder]);

    const stream = sessionClient.streamTestAgent({ language });
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

  const buildDiagramKeys = useCallback((rawPath: string): string[] => {
    const normalized = rawPath.replace(/\\+/g, "/");
    const candidates = new Set<string>();

    const addVariants = (base: string) => {
      if (!base) {
        return;
      }
      candidates.add(base);
      if (!base.startsWith("./")) {
        candidates.add(`./${base}`);
      }
      if (!base.startsWith(".\\")) {
        candidates.add(`.\\${base}`);
      }
      if (!base.startsWith("/")) {
        candidates.add(`/${base}`);
      }
      if (!base.startsWith("\\")) {
        candidates.add(`\\${base}`);
      }
    };

    addVariants(normalized);
    addVariants(normalized.replace(/\//g, "\\"));

    return Array.from(candidates);
  }, []);

  const displaySpec = specSource === "historical" ? selectedSessionDetail?.spec : specPreview;

  const diagramSources = useMemo(() => {
    if (!displaySpec) {
      return {} as Record<string, string>;
    }
    const entries: Record<string, string> = {};
    for (const diagram of displaySpec.diagrams) {
      if (!diagram.path || !diagram.svg) {
        continue;
      }
      const sanitizedSvg = diagram.svg.replace(/<\?xml[^>]*?>/i, "").trim();
      const keys = buildDiagramKeys(diagram.path);
      keys.forEach((key) => {
        entries[key] = sanitizedSvg;
      });
      if (import.meta.env.DEV) {
        console.debug("Registered diagram", { path: diagram.path, keys, length: sanitizedSvg.length });
      }
    }
    return entries;
  }, [displaySpec, buildDiagramKeys]);

  const selectedSessionSummary = useMemo(() => {
    if (!selectedSessionId) {
      return null;
    }
    return sessionSummaries.find((item) => item.id === selectedSessionId) ?? null;
  }, [selectedSessionId, sessionSummaries]);

  const historicalFeedback = specSource === "historical" && selectedSessionDetail ? selectedSessionDetail.feedback : [];
  const feedbackEntries = specSource === "historical" ? historicalFeedback : currentFeedbackEntries;
  const sessionContextKey = specSource === "historical"
    ? selectedSessionId ?? "historical"
    : sessionClient.getThreadId();

  return (
    <CopilotProvider scope={scope}>
      <div className="app-shell">
        <Sidebar scope={scope} onScopeChange={setScope} status={status} />
        <div className="main-content">
          <SessionHistoryBar
            sessions={sessionSummaries}
            selectedSessionId={selectedSessionId}
            onSelectSession={handleSelectSession}
            onRefreshSessions={handleRefreshSessions}
            disabled={sessionsLoading || isHistoryLoading}
            isLoading={sessionsLoading}
          />
          {viewMode === "spec" ? (
            <SpecPanel
              markdown={displaySpec?.markdown ?? ""}
              diagrams={diagramSources}
              onClose={handleCloseSpecPanel}
              sessionContextKey={sessionContextKey}
              selectedSessionSummary={specSource === "historical" ? selectedSessionSummary : null}
              isHistoryLoading={specSource === "historical" ? isHistoryLoading : false}
              feedbackEntries={feedbackEntries}
              onSubmitFeedback={handleSubmitFeedback}
              isFeedbackSubmitting={isFeedbackSubmitting}
            />
          ) : viewMode === "transcript" ? (
            <TranscriptPanel
              session={selectedSessionDetail}
              scope={scope}
              isLoading={isHistoryLoading}
              onClose={handleCloseTranscript}
            />
          ) : (
            <ChatPanel messages={messages} status={status} error={error} onSend={handleSend} onAbort={handleAbort} />
          )}
        </div>
        <ActionDrawer
          status={status}
          onRunTestAgent={handleRunTestAgent}
          onRequestExport={handleRequestExport}
        />
      </div>
      {showSpecDialog && (
        <div className="modal-backdrop" role="presentation">
          <div className="modal-card" role="dialog" aria-modal="true" aria-label="View functional specification">
            <h2>View Functional Specification</h2>
            <p>Select the format you would like to open.</p>
            {isSpecLoading && <p className="modal-note">Preparing the latest draft...</p>}
            <div className="modal-actions">
              <button type="button" onClick={handleViewMarkdown} disabled={isSpecLoading}>
                View Markdown
              </button>
              <button type="button" onClick={handleViewPdf} disabled={isSpecLoading}>
                Open PDF
              </button>
            </div>
            <button type="button" className="link-button" onClick={() => setShowSpecDialog(false)} disabled={isSpecLoading}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </CopilotProvider>
  );
}
