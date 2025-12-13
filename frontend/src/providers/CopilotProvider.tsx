import type { ReactNode } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import { appConfig, type InterviewScope } from "../config";

interface CopilotProviderProps {
  scope: InterviewScope;
  children: ReactNode;
}

export function CopilotProvider({ scope, children }: CopilotProviderProps) {
  const runtimeUrl = `${appConfig.apiBaseUrl}/${scope}`;

  return (
    <CopilotKit runtimeUrl={runtimeUrl}>{children}</CopilotKit>
  );
}
