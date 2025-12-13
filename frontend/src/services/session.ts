export type AgentRole = "user" | "assistant" | "system" | "tool";

export interface AgentMessage {
  id: string;
  role: AgentRole;
  content: string;
}

export interface TestAgentOptions {
  seed?: number;
  persona?: Record<string, unknown>;
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

  return {
    persona,
    transcript,
    closingFeedback,
    reviewWarnings,
    recordId: recordIdValue == null ? null : String(recordIdValue),
    specPath: specPathValue == null ? null : String(specPathValue),
    pdfPath: pdfPathValue == null ? null : String(pdfPathValue),
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

  constructor(private readonly baseUrl: string, private readonly scope: string) {}

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

    const payload = {
      thread_id: this.threadId,
      run_id: runId,
      messages: toAguiMessages(messages),
      state: options.state,
      tools: options.tools,
    };

    const endpoint = `${this.baseUrl}/${this.scope}`;

    const events = (async function* (signal: AbortSignal): AsyncGenerator<AgentEvent, void, void> {
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
    const response = await fetch(`${this.baseUrl}/test-agent/${this.scope}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(options ?? {}),
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

    const events = (async function* (signal: AbortSignal): AsyncGenerator<TestAgentStreamEvent, void, void> {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify(options ?? {}),
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
}
