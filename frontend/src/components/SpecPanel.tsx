import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { SessionSummary, SpecFeedbackEntry } from "../services/session";
import { useLocalization } from "../providers/LocalizationProvider";

interface SpecPanelProps {
  markdown: string;
  diagrams: Record<string, string>;
  onClose: () => void;
  sessionContextKey: string;
  selectedSessionSummary?: SessionSummary | null;
  isHistoryLoading?: boolean;
  feedbackEntries?: SpecFeedbackEntry[];
  onSubmitFeedback?: (message: string) => Promise<void> | void;
  isFeedbackSubmitting?: boolean;
}

function normalizeKey(input: string): string {
  let value = input.trim();
  if (!value) {
    return "";
  }

  if (!/^data:/i.test(value)) {
    if (/^[a-z]+:\/\//i.test(value) || value.startsWith("//")) {
      try {
        const base = typeof window !== "undefined" ? window.location.origin : undefined;
        const url = base ? new URL(value, base) : new URL(value);
        if (!base || !url.origin || url.origin === base) {
          value = url.pathname;
        }
      } catch {
        // Ignore URL parsing failures and keep original value.
      }
    }

    if (value.includes("%")) {
      try {
        value = decodeURIComponent(value);
      } catch {
        // Leave encoded value if decode fails.
      }
    }
  }

  value = value.replace(/\\/g, "/");

  while (value.startsWith("./")) {
    value = value.slice(2);
  }
  value = value.replace(/^\/+/, "");

  return value;
}

function formatTimestamp(value?: string | null): string | null {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed.toLocaleString();
}

export function SpecPanel({
  markdown,
  diagrams,
  onClose,
  sessionContextKey,
  selectedSessionSummary,
  isHistoryLoading,
  feedbackEntries,
  onSubmitFeedback,
  isFeedbackSubmitting,
}: SpecPanelProps) {
  const { t } = useLocalization();
  const assetMap = useMemo(() => {
    const map = new Map<string, string>();
    Object.entries(diagrams).forEach(([key, value]) => {
      const normalized = normalizeKey(key);
      if (normalized) {
        map.set(normalized, value);
      }
    });
    return map;
  }, [diagrams]);

  const feedbackList = useMemo(() => {
    const entries = [...(feedbackEntries ?? [])];
    entries.sort((a, b) => {
      const aTime = new Date(a.createdAt).getTime();
      const bTime = new Date(b.createdAt).getTime();
      return aTime - bTime;
    });
    return entries;
  }, [feedbackEntries]);

  const [feedbackDraft, setFeedbackDraft] = useState("");
  const [feedbackAlert, setFeedbackAlert] = useState<{ type: "error" | "success"; text: string } | null>(null);

  useEffect(() => {
    setFeedbackDraft("");
    setFeedbackAlert(null);
  }, [sessionContextKey]);

  const components = useMemo<Components>(() => ({
    img(imageProps) {
      const src = imageProps.src ?? "";
      const normalized = normalizeKey(src);
      const svgMarkup = normalized ? assetMap.get(normalized) : undefined;
      if (import.meta.env.DEV) {
        if (svgMarkup) {
          console.debug("Rendering diagram", { src, normalized, kind: "inline" });
        } else {
          console.debug("Unresolved diagram source", {
            src,
            normalized,
            knownKeys: Array.from(assetMap.keys()).slice(0, 5),
            keyCount: assetMap.size,
          });
        }
      }
      if (svgMarkup) {
        return (
          <span
            className="inline-svg-diagram"
            role="img"
            aria-label={imageProps.alt ?? "Diagram"}
            dangerouslySetInnerHTML={{ __html: svgMarkup }}
          />
        );
      }
      return (
        <img
          {...imageProps}
          alt={imageProps.alt ?? ""}
          loading="lazy"
        />
      );
    },
  }), [assetMap]);

  const handleFeedbackSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!onSubmitFeedback) {
      return;
    }
    const value = feedbackDraft.trim();
    if (!value) {
      setFeedbackAlert({ type: "error", text: t("spec.feedback.emptyError") });
      return;
    }
    try {
      await onSubmitFeedback(value);
      setFeedbackDraft("");
      setFeedbackAlert({ type: "success", text: t("spec.feedback.success") });
    } catch (err) {
      const message = err instanceof Error ? err.message : t("spec.feedback.error");
      setFeedbackAlert({ type: "error", text: message });
    }
  };

  const selectedMeta = selectedSessionSummary
    ? {
        id: selectedSessionSummary.id,
        createdAt: formatTimestamp(selectedSessionSummary.createdAt),
        turnCount: selectedSessionSummary.turnCount,
        feedbackCount: selectedSessionSummary.feedbackCount,
      }
    : null;

  return (
    <section className="spec-panel" aria-label={t("spec.ariaLabel")}>
      <header>
        <div>
          <h1>{t("spec.title")}</h1>
          <p>{t("spec.description")}</p>
        </div>
        <button type="button" className="link-button" onClick={onClose}>
          {t("spec.back")}
        </button>
      </header>

      {selectedMeta && (
        <div className="session-meta">
          <p>
            <strong>{t("spec.sessionMetaSession")}</strong> {selectedMeta.id}
            {selectedMeta.createdAt ? ` · ${selectedMeta.createdAt}` : ""}
            {typeof selectedMeta.turnCount === "number"
              ? ` · ${t("spec.sessionMetaTurns", { count: selectedMeta.turnCount })}`
              : ""}
          </p>
          <p>
            <strong>{t("spec.sessionMetaFeedback")}</strong> {selectedMeta.feedbackCount}
          </p>
        </div>
      )}
      {isHistoryLoading && (
        <p className="session-meta session-meta--loading">{t("spec.sessionLoading")}</p>
      )}

      <div className="spec-scroll markdown-body">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={components}
          urlTransform={(uri) => uri ?? ""}
        >
          {markdown || t("spec.markdownFallback")}
        </ReactMarkdown>
      </div>

      {onSubmitFeedback && (
        <section className="feedback-panel" aria-label={t("spec.feedback.title")}>
          <h2>{t("spec.feedback.title")}</h2>
          {feedbackList.length > 0 ? (
            <ul className="feedback-list">
              {feedbackList.map((entry) => (
                <li key={entry.feedbackId}>
                  <p>{entry.message}</p>
                  <span>{formatTimestamp(entry.createdAt) ?? t("spec.feedback.unknownTimestamp")}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="feedback-empty">{t("spec.feedback.empty")}</p>
          )}

          <form className="feedback-form" onSubmit={handleFeedbackSubmit}>
            <label htmlFor="feedback-input">{t("spec.feedback.label")}</label>
            <textarea
              id="feedback-input"
              value={feedbackDraft}
              onChange={(event) => setFeedbackDraft(event.target.value)}
              placeholder={t("spec.feedback.placeholder")}
              disabled={isFeedbackSubmitting}
            />
            <div className="feedback-actions">
              {feedbackAlert && (
                <span className={`feedback-alert ${feedbackAlert.type}`}>{feedbackAlert.text}</span>
              )}
              <button type="submit" disabled={isFeedbackSubmitting}>
                {isFeedbackSubmitting ? t("spec.feedback.submitting") : t("spec.feedback.submit")}
              </button>
            </div>
          </form>
        </section>
      )}
    </section>
  );
}
