import type { InterviewScope } from "../config";

export type AgentRole = "user" | "assistant" | "system" | "tool";

export interface AgentMessage {
  id: string;
  role: AgentRole;
  content: string;
}

export interface TestAgentOptions {
  seed?: number;
  persona?: Record<string, unknown>;
  language?: string;
}

export interface TestAgentPersona {
  projectName: string;
  company: string;
  stakeholderRole: string;
  context: string;
  goals: string[];
  risks: string[];
  preferences: string[];
  tone: string;
}

export interface TestAgentTranscriptTurn {
  question: string;
  answer: string;
}

export interface TestAgentResult {
  persona: TestAgentPersona;
  transcript: TestAgentTranscriptTurn[];
  closingFeedback: string;
  reviewWarnings: string[];
  recordId: string | null;
  specPath: string | null;
  pdfPath: string | null;
  language?: string | null;
}

export interface SpecDiagramAsset {
  path: string;
  svg?: string;
}

export interface SpecPreview {
  markdown: string;
  markdownPath: string | null;
  pdfPath: string | null;
  diagrams: SpecDiagramAsset[];
}

export interface TranscriptMessage {
  id: string;
  role: AgentRole;
  content: string;
}

export interface SpecFeedbackEntry {
  feedbackId: string;
  sessionId: string;
  message: string;
  createdAt: string;
}

export interface SessionSummary {
  id: string;
  scope: InterviewScope;
  createdAt: string;
  turnCount: number;
  specAvailable: boolean;
  pdfAvailable: boolean;
  feedbackCount: number;
}

export interface SessionDetail {
  id: string;
  scope: InterviewScope;
  createdAt: string;
  spec: SpecPreview;
  transcript: TranscriptMessage[];
  feedback: SpecFeedbackEntry[];
}

export type TestAgentStreamEvent =
  | {
      type: "persona";
      persona: TestAgentPersona;
    }
  | {
      type: "message";
      role: AgentRole;
      content: string;
    }
  | {
      type: "status";
      content: string;
    }
  | {
      type: "specDraft" | "specFinal" | "reviewFeedback";
      content: string;
    }
  | {
      type: "reviewWarning" | "reviewNote";
      note: string;
    }
  | {
      type: "artifact";
      kind: string;
      path?: string;
      recordId?: string;
    }
  | {
      type: "complete";
      result: TestAgentResult;
    }
  | {
      type: "error";
      message: string;
    };

export type AgentEvent =
  | {
      type: "RUN_STARTED";
      threadId: string;
      runId: string;
    }
  | {
      type: "TEXT_MESSAGE_START";
      messageId: string;
    }
  | {
      type: "TEXT_MESSAGE_CONTENT";
      messageId: string;
      delta: string;
    }
  | {
      type: "TEXT_MESSAGE_END";
      messageId: string;
    }
  | {
      type: "RUN_FINISHED";
      threadId: string;
      runId: string;
    }
  | {
      type: "RUN_ERROR";
      message: string;
    };

export interface StreamResult {
  events: AsyncGenerator<AgentEvent, void, void>;
  abort: () => void;
}

export interface TestAgentStreamResult {
  events: AsyncGenerator<TestAgentStreamEvent, void, void>;
  abort: () => void;
}

interface AguiMessagePayload {
  id: string;
  role: string;
  content: string;
}

interface StreamOptions {
  signal?: AbortSignal;
  state?: Record<string, unknown>;
  tools?: Array<Record<string, unknown>>;
}

function generateId(prefix: string) {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `${prefix}_${crypto.randomUUID()}`;
  }
  return `${prefix}_${Math.random().toString(16).slice(2)}`;
}

function toStringArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .map((item) => String(item ?? "").trim())
      .filter((entry) => entry.length > 0);
  }
  if (typeof value === "string") {
    return value
      .split(/[;\n]/)
      .map((segment) => segment.trim())
      .filter((segment) => segment.length > 0);
  }
  return [];
}

function normalizePersona(raw: Record<string, unknown> | unknown): TestAgentPersona {
  const personaRaw = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  return {
    projectName: String(personaRaw.project_name ?? "").trim(),
    company: String(personaRaw.company ?? "").trim(),
    stakeholderRole: String(personaRaw.stakeholder_role ?? "").trim(),
    context: String(personaRaw.context ?? "").trim(),
    goals: toStringArray(personaRaw.goals),
    risks: toStringArray(personaRaw.risks),
    preferences: toStringArray(personaRaw.preferences),
    tone: String(personaRaw.tone ?? "").trim(),
  };
}

function normalizeSpecPreviewPayload(data: Record<string, unknown>): SpecPreview {
  const diagramsRaw = Array.isArray(data.diagrams) ? (data.diagrams as unknown[]) : [];
  const diagrams = diagramsRaw.reduce<SpecDiagramAsset[]>((acc, entry) => {
    if (!entry || typeof entry !== "object") {
      return acc;
    }
    const record = entry as Record<string, unknown>;
    const path = String(record.path ?? "").trim();
    if (!path) {
      return acc;
    }
    const svg = typeof record.svg === "string" ? record.svg : undefined;
    acc.push({ path, svg });
    return acc;
  }, []);

  return {
    markdown: String(data.markdown ?? ""),
    markdownPath: data.markdown_path == null ? null : String(data.markdown_path),
    pdfPath: data.pdf_path == null ? null : String(data.pdf_path),
    diagrams,
  };
}

function normalizeScopeValue(value: unknown, fallback: InterviewScope): InterviewScope {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (normalized === "project" || normalized === "process" || normalized === "change_request") {
    return normalized as InterviewScope;
  }
  return fallback;
}

function normalizeTestAgentResult(payload: Record<string, unknown>): TestAgentResult {
  const persona = normalizePersona(payload.persona);

  const transcriptRaw = Array.isArray(payload.transcript) ? (payload.transcript as unknown[]) : [];
  const transcript: TestAgentTranscriptTurn[] = transcriptRaw
    .map((entry) => {
      if (!entry || typeof entry !== "object") {
        return null;
      }
      const question = String((entry as Record<string, unknown>).question ?? "").trim();
      const answer = String((entry as Record<string, unknown>).answer ?? "").trim();
      if (!question && !answer) {
        return null;
      }
      return { question, answer };
    })
    .filter((turn): turn is TestAgentTranscriptTurn => Boolean(turn));

  const reviewWarningsRaw = Array.isArray(payload.review_warnings)
    ? (payload.review_warnings as unknown[])
    : [];
  const reviewWarnings = reviewWarningsRaw
    .map((item) => String(item ?? "").trim())
    .filter((entry) => entry.length > 0);

  const closingFeedback = String(payload.closing_feedback ?? "").trim();
  const recordIdValue = payload.record_id;
  const specPathValue = payload.spec_path;
  const pdfPathValue = payload.pdf_path;
  const languageValue = payload.language;

  return {
    persona,
    transcript,
    closingFeedback,
    reviewWarnings,
    recordId: recordIdValue == null ? null : String(recordIdValue),
    specPath: specPathValue == null ? null : String(specPathValue),
    pdfPath: pdfPathValue == null ? null : String(pdfPathValue),
    language: typeof languageValue === "string" ? languageValue : null,
  };
}

function parseErrorMessage(status: number, rawPayload: string | null, fallback: string): string {
  if (!rawPayload) {
    return fallback;
  }
  const trimmed = rawPayload.trim();
  if (!trimmed) {
    return fallback;
  }
  try {
    const parsed = JSON.parse(trimmed) as Record<string, unknown>;
    if (typeof parsed.detail === "string" && parsed.detail.trim()) {
      return parsed.detail.trim();
    }
  } catch {
    if (trimmed) {
      return trimmed;
    }
  }
  return fallback || `Request failed (${status})`;
}

async function* parseServerSentEvents(stream: ReadableStream<Uint8Array>): AsyncGenerator<Record<string, unknown>, void, void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });

      let separatorIndex = buffer.indexOf("\n\n");
      while (separatorIndex !== -1) {
        const rawChunk = buffer.slice(0, separatorIndex);
        buffer = buffer.slice(separatorIndex + 2);

        const dataLines: string[] = [];
        for (const line of rawChunk.split("\n")) {
          const trimmed = line.trim();
          if (trimmed.startsWith("data:")) {
            dataLines.push(trimmed.slice(5).trim());
          }
        }

        if (dataLines.length > 0) {
          const serialized = dataLines.join("\n");
          if (serialized) {
            try {
              yield JSON.parse(serialized) as Record<string, unknown>;
            } catch (error) {
              console.warn("Failed to parse SSE payload", error);
            }
          }
        }

        separatorIndex = buffer.indexOf("\n\n");
      }
    }

    if (buffer.trim()) {
      for (const fragment of buffer.split("\n\n")) {
        const dataLines: string[] = [];
        for (const line of fragment.split("\n")) {
          const trimmed = line.trim();
          if (trimmed.startsWith("data:")) {
            dataLines.push(trimmed.slice(5).trim());
          }
        }
        if (dataLines.length > 0) {
          const serialized = dataLines.join("\n");
          if (serialized) {
            try {
              yield JSON.parse(serialized) as Record<string, unknown>;
            } catch (error) {
              console.warn("Failed to parse trailing SSE payload", error);
            }
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function toAguiMessages(messages: AgentMessage[]): AguiMessagePayload[] {
  return messages.map((message) => ({
    id: message.id,
    role: message.role,
    content: message.content,
  }));
}

function mapAguiEvent(event: Record<string, unknown>): AgentEvent | null {
  const type = typeof event.type === "string" ? event.type : "";

  switch (type) {
    case "RUN_STARTED":
      return {
        type,
        threadId: String(event.threadId ?? ""),
        runId: String(event.runId ?? ""),
      };
    case "TEXT_MESSAGE_START":
      return {
        type,
        messageId: String(event.messageId ?? generateId("message")),
      };
    case "TEXT_MESSAGE_CONTENT":
      return {
        type,
        messageId: String(event.messageId ?? generateId("message")),
        delta: String(event.delta ?? ""),
      };
    case "TEXT_MESSAGE_END":
      return {
        type,
        messageId: String(event.messageId ?? generateId("message")),
      };
    case "RUN_FINISHED":
      return {
        type,
        threadId: String(event.threadId ?? ""),
        runId: String(event.runId ?? ""),
      };
    case "RUN_ERROR":
      return {
        type,
        message: String(event.message ?? "Unknown error"),
      };
    default:
      return null;
  }
}

function mapTestAgentStreamEvent(event: Record<string, unknown>): TestAgentStreamEvent | null {
  const type = typeof event.type === "string" ? event.type : "";

  switch (type) {
    case "persona":
      return {
        type: "persona",
        persona: normalizePersona(event.persona),
      };
    case "message": {
      const rawRole = String(event.role ?? "assistant").trim();
      const role: AgentRole = (rawRole === "user" || rawRole === "assistant" || rawRole === "system" || rawRole === "tool")
        ? rawRole
        : "assistant";
      const content = String(event.content ?? "");
      if (!content) {
        return null;
      }
      return { type: "message", role, content };
    }
    case "status":
      return { type: "status", content: String(event.content ?? "") };
    case "spec_draft":
      return { type: "specDraft", content: String(event.content ?? "") };
    case "spec_final":
      return { type: "specFinal", content: String(event.content ?? "") };
    case "review_feedback":
      return { type: "reviewFeedback", content: String(event.content ?? "") };
    case "review_warning":
      return { type: "reviewWarning", note: String(event.note ?? event.content ?? "") };
    case "review_note":
      return { type: "reviewNote", note: String(event.note ?? event.content ?? "") };
    case "artifact": {
      const kind = String(event.kind ?? "").trim();
      const path = event.path == null ? undefined : String(event.path);
      const recordId = event.recordId == null ? undefined : String(event.recordId);
      return { type: "artifact", kind, path, recordId };
    }
    case "complete":
      if (event.result && typeof event.result === "object") {
        return {
          type: "complete",
          result: normalizeTestAgentResult(event.result as Record<string, unknown>),
        };
      }
      return null;
    case "error":
      return {
        type: "error",
        message: String(event.message ?? "Unexpected test agent error."),
      };
    default:
      return null;
  }
}

export class AguiSessionClient {
  private threadId = generateId("thread");
  private runCounter = 0;

  constructor(
    private readonly baseUrl: string,
    private readonly scope: InterviewScope,
    private readonly language?: string,
  ) {}

  getThreadId(): string {
    return this.threadId;
  }

  startNewConversation(): void {
    this.threadId = generateId("thread");
    this.runCounter = 0;
  }

  stream(messages: AgentMessage[], options: StreamOptions = {}): StreamResult {
    const controller = new AbortController();
    const mergedSignal = options.signal;

    if (mergedSignal) {
      if (mergedSignal.aborted) {
        controller.abort();
      } else {
        mergedSignal.addEventListener("abort", () => controller.abort(), { once: true });
      }
    }

    const runId = generateId("run");
    this.runCounter += 1;

    const mergedState = { ...(options.state ?? {}) };
    const stateLanguage =
      (options.state && typeof options.state.language === "string" && options.state.language.trim())
        ? String(options.state.language).trim()
        : this.language;
    if (stateLanguage) {
      mergedState.language = stateLanguage;
    }

    const payload = {
      thread_id: this.threadId,
      run_id: runId,
      messages: toAguiMessages(messages),
      state: mergedState,
      tools: options.tools,
    };

    const endpoint = `${this.baseUrl}/${this.scope}`;

    if (import.meta.env.DEV) {
      console.debug("AG-UI stream request", {
        endpoint,
        threadId: this.threadId,
        runId,
        state: mergedState,
      });
    }

    const events = (async function* (this: AguiSessionClient, signal: AbortSignal): AsyncGenerator<AgentEvent, void, void> {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify(payload),
        signal,
      });

      if (!response.ok) {
        throw new Error(`AG-UI request failed (${response.status})`);
      }

      if (!response.body) {
        throw new Error("Streaming response body is not available.");
      }

      for await (const rawEvent of parseServerSentEvents(response.body)) {
        const mapped = mapAguiEvent(rawEvent);
        if (mapped?.type === "RUN_STARTED" && mapped.threadId) {
          this.threadId = mapped.threadId;
        }
        if (mapped) {
          yield mapped;
        }
      }
    }).call(this, controller.signal);

    return {
      events,
      abort: () => controller.abort(),
    };
  }

  async runTestAgent(options: TestAgentOptions = {}): Promise<TestAgentResult> {
    const payload: TestAgentOptions = { ...(options ?? {}) };
    if (!payload.language && this.language) {
      payload.language = this.language;
    }

    const response = await fetch(`${this.baseUrl}/test-agent/${this.scope}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const rawPayload = await response.text();

    if (!response.ok) {
      const message = parseErrorMessage(
        response.status,
        rawPayload,
        `Test agent request failed (${response.status})`,
      );
      throw new Error(message);
    }

    let data: Record<string, unknown> = {};
    if (rawPayload) {
      try {
        const parsed = JSON.parse(rawPayload);
        if (parsed && typeof parsed === "object") {
          data = parsed as Record<string, unknown>;
        }
      } catch (error) {
        console.warn("Failed to parse test agent payload", error);
      }
    }

    return normalizeTestAgentResult(data);
  }

  streamTestAgent(options: TestAgentOptions = {}): TestAgentStreamResult {
    const controller = new AbortController();
    const endpoint = `${this.baseUrl}/test-agent/${this.scope}/stream`;

    const events = (async function* (this: AguiSessionClient, signal: AbortSignal): AsyncGenerator<TestAgentStreamEvent, void, void> {
      const payload: TestAgentOptions = { ...(options ?? {}) };
      if (!payload.language && this.language) {
        payload.language = this.language;
      }

      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify(payload),
        signal,
      });

      if (!response.ok) {
        const rawError = await response.text();
        const message = parseErrorMessage(
          response.status,
          rawError,
          `Test agent stream failed (${response.status})`,
        );
        throw new Error(message);
      }

      if (!response.body) {
        throw new Error("Streaming response body is not available.");
      }

      for await (const rawEvent of parseServerSentEvents(response.body)) {
        const mapped = mapTestAgentStreamEvent(rawEvent);
        if (mapped) {
          yield mapped;
        }
      }
    }).call(this, controller.signal);

    return {
      events,
      abort: () => controller.abort(),
    };
  }

  async fetchSpecPreview(threadId: string, refresh = false): Promise<SpecPreview> {
    const response = await fetch(`${this.baseUrl}/spec/${this.scope}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        thread_id: threadId,
        refresh,
      }),
    });

    const rawPayload = await response.text();

    if (!response.ok) {
      const message = parseErrorMessage(
        response.status,
        rawPayload,
        `Unable to generate functional specification (${response.status})`,
      );
      throw new Error(message);
    }

    let data: Record<string, unknown> = {};
    if (rawPayload) {
      try {
        const parsed = JSON.parse(rawPayload);
        if (parsed && typeof parsed === "object") {
          data = parsed as Record<string, unknown>;
        }
      } catch (error) {
        console.warn("Failed to parse spec preview payload", error);
      }
    }

    return normalizeSpecPreviewPayload(data);
  }

  buildSpecPdfUrl(threadId: string): string {
    const search = new URLSearchParams({ thread_id: threadId, t: Date.now().toString() });
    return `${this.baseUrl}/spec/${this.scope}/pdf?${search.toString()}`;
  }

  async listSessions(limit = 10): Promise<SessionSummary[]> {
    const params = new URLSearchParams({ limit: String(limit) });
    params.set("scope", this.scope);
    const response = await fetch(`${this.baseUrl}/sessions?${params.toString()}`);
    if (!response.ok) {
      throw new Error(`Unable to list sessions (${response.status})`);
    }
    const payload = await response.json();
    if (!Array.isArray(payload)) {
      return [];
    }
    return payload.map((item) => {
      const record = item as Record<string, unknown>;
      return {
        id: String(record.id ?? ""),
        scope: normalizeScopeValue(record.scope, this.scope),
        createdAt: String(record.created_at ?? ""),
        turnCount: Number(record.turn_count ?? 0),
        specAvailable: Boolean(record.spec_available),
        pdfAvailable: Boolean(record.pdf_available),
        feedbackCount: Number(record.feedback_count ?? 0),
      };
    }).filter((summary) => summary.id.length > 0);
  }

  async getSessionDetail(sessionId: string): Promise<SessionDetail> {
    const response = await fetch(`${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}`);
    if (!response.ok) {
      const message = await response.text();
      throw new Error(parseErrorMessage(response.status, message, `Session lookup failed (${response.status})`));
    }
    const data = await response.json();
    const specData = typeof data.spec === "object" && data.spec ? (data.spec as Record<string, unknown>) : {};
    const transcriptRaw = Array.isArray(data.transcript) ? (data.transcript as unknown[]) : [];
    const feedbackRaw = Array.isArray(data.feedback) ? (data.feedback as unknown[]) : [];

    const transcript: TranscriptMessage[] = transcriptRaw
      .map((entry, index) => {
        if (!entry || typeof entry !== "object") {
          return null;
        }
        const record = entry as Record<string, unknown>;
        const role = String(record.role ?? "assistant") as AgentRole;
        const content = String(record.content ?? "").trim();
        if (!content) {
          return null;
        }
        return {
          id: `history_${sessionId}_${index}`,
          role,
          content,
        };
      })
      .filter((item): item is TranscriptMessage => Boolean(item));

    const feedback: SpecFeedbackEntry[] = feedbackRaw
      .map((entry) => {
        if (!entry || typeof entry !== "object") {
          return null;
        }
        const record = entry as Record<string, unknown>;
        const feedbackId = String(record.feedback_id ?? "").trim();
        const message = String(record.message ?? "").trim();
        if (!feedbackId || !message) {
          return null;
        }
        return {
          feedbackId,
          sessionId: String(record.session_id ?? sessionId),
          message,
          createdAt: String(record.created_at ?? ""),
        };
      })
      .filter((item): item is SpecFeedbackEntry => Boolean(item));

    return {
      id: String(data.id ?? sessionId),
      scope: normalizeScopeValue(data.scope, this.scope),
      createdAt: String(data.created_at ?? ""),
      spec: normalizeSpecPreviewPayload(specData),
      transcript,
      feedback,
    };
  }

  async submitSpecFeedback(sessionId: string, message: string): Promise<SpecFeedbackEntry> {
    const response = await fetch(`${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}/feedback`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ message }),
    });

    const raw = await response.text();

    if (!response.ok) {
      throw new Error(parseErrorMessage(response.status, raw, `Unable to submit feedback (${response.status})`));
    }

    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object") {
        const record = parsed as Record<string, unknown>;
        return {
          feedbackId: String(record.feedback_id ?? ""),
          sessionId: String(record.session_id ?? sessionId),
          message: String(record.message ?? ""),
          createdAt: String(record.created_at ?? ""),
        };
      }
    } catch (error) {
      console.warn("Failed to parse feedback response", error);
    }

    return {
      feedbackId: `${sessionId}-${Date.now()}`,
      sessionId,
      message,
      createdAt: new Date().toISOString(),
    };
  }
}
