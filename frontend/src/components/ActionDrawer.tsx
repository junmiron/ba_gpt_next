import { useLocalization } from "../providers/LocalizationProvider";
import type { SessionStatus } from "./ChatPanel";

interface ActionDrawerProps {
  status: SessionStatus;
  onRunTestAgent: () => void;
  onRequestExport: () => void;
}

export function ActionDrawer({ status, onRunTestAgent, onRequestExport }: ActionDrawerProps) {
  const disabled = status === "streaming";
  const { t } = useLocalization();

  return (
    <aside className="action-drawer" aria-label={t("drawer.ariaLabel")}>
      <header>
        <h2>{t("drawer.title")}</h2>
        <p>{t("drawer.description")}</p>
      </header>

      <section className="action-card">
        <h3>{t("drawer.testAgent.title")}</h3>
        <p>{t("drawer.testAgent.description")}</p>
        <button type="button" onClick={onRunTestAgent} disabled={disabled}>
          {t("drawer.testAgent.button")}
        </button>
      </section>

      <section className="action-card">
        <h3>{t("drawer.spec.title")}</h3>
        <p>{t("drawer.spec.description")}</p>
        <button type="button" onClick={onRequestExport} disabled={disabled}>
          {t("drawer.spec.button")}
        </button>
      </section>

    </aside>
  );
}
