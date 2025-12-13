export type InterviewScope = "project" | "process" | "change_request";

const DEFAULT_SCOPE: InterviewScope = (import.meta.env.VITE_DEFAULT_SCOPE ?? "project") as InterviewScope;

export const appConfig = {
  apiBaseUrl: (import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8081").replace(/\/$/, ""),
  defaultScope: DEFAULT_SCOPE,
} as const;
