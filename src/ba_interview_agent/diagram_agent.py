"""Process diagram generation with BPMN-inspired styling."""

import logging
import subprocess
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape as html_escape
from pathlib import Path
from shutil import which
from typing import Iterable, List, Sequence, Literal

from .as_is_agent import AsIsProcess
from .to_be_agent import ToBeProcess

DiagramFormat = Literal["svg", "png", "pdf", "dot"]
ProcessModel = AsIsProcess | ToBeProcess

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DiagramArtifact:
    """Result of rendering a single process diagram."""

    process_name: str
    dot_path: Path
    image_path: Path
    relative_path: str
    renderer: str
    dot_source: str


class DiagramExportError(RuntimeError):
    """Raised when a diagram cannot be rendered."""


class ProcessDiagramAgent:
    """Render AS-IS and TO-BE process diagrams with BPMN-like visuals."""

    _SUPPORTED_FORMATS: tuple[DiagramFormat, ...] = (
        "svg",
        "png",
        "pdf",
        "dot",
    )

    def __init__(
        self,
        output_dir: Path,
        image_format: DiagramFormat = "svg",
    ) -> None:
        if image_format not in self._SUPPORTED_FORMATS:
            raise DiagramExportError(
                (
                    "Unsupported image format '{format}'. Supported formats: "
                    "{choices}."
                ).format(
                    format=image_format,
                    choices=", ".join(self._SUPPORTED_FORMATS),
                )
            )
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._format: DiagramFormat = image_format
        logger.info(
            "ProcessDiagramAgent ready (directory=%s, format=%s)",
            self._output_dir,
            self._format,
        )

    def render_processes(
        self,
        processes: Sequence[ProcessModel],
        *,
        group_prefix: str,
        context_label: str,
    ) -> List[DiagramArtifact]:
        """Render diagrams for each supplied process."""

        if not processes:
            return []

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        artifacts: List[DiagramArtifact] = []

        for process in processes:
            try:
                artifact = self._render_single(
                    process=process,
                    group_prefix=group_prefix,
                    context_label=context_label,
                    timestamp=timestamp,
                )
            except DiagramExportError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise DiagramExportError(
                    "Failed to render diagram for '{name}': {error}."
                    .format(name=process.name, error=exc)
                ) from exc

            artifacts.append(artifact)
        return artifacts

    def _render_single(
        self,
        *,
        process: ProcessModel,
        group_prefix: str,
        context_label: str,
        timestamp: str,
    ) -> DiagramArtifact:
        happy_steps = self._normalize_steps(
            process.happy_path,
            default="Primary path pending confirmation.",
        )
        unhappy_steps = self._normalize_steps(
            process.unhappy_path,
            default="Exception path pending confirmation.",
            allow_empty=True,
        )

        dot_source = self._build_bpmn_dot(
            process_name=process.name,
            context_label=context_label,
            happy_steps=happy_steps,
            unhappy_steps=unhappy_steps,
        )
        safe_group = self._slugify(group_prefix or "process")
        safe_name = self._slugify(process.name)
        dot_filename = f"{safe_group}_{safe_name}_{timestamp}.dot"
        dot_path = self._output_dir / dot_filename
        try:
            dot_path.write_text(dot_source, encoding="utf-8")
        except OSError as exc:
            raise DiagramExportError(
                (
                    "Unable to write DOT representation for '{name}': {error}."
                ).format(name=process.name, error=exc)
            ) from exc

        if self._format == "dot":
            image_path = dot_path
        else:
            desired_image_path = dot_path.with_suffix(f".{self._format}")
            try:
                import graphviz  # type: ignore

                source = graphviz.Source(dot_source)
                output_base = str(desired_image_path.with_suffix(""))
                rendered_path = source.render(
                    output_base,
                    format=self._format,
                    cleanup=True,
                )
            except ImportError as exc:
                logger.info(
                    "python-graphviz unavailable; using Graphviz CLI "
                    "fallback: %s",
                    exc,
                )
                image_path = self._render_with_cli(dot_path)
            except Exception as exc:  # noqa: BLE001
                raise DiagramExportError(
                    (
                        "Graphviz export failed while rendering '{name}': "
                        "{error}."
                    ).format(name=process.name, error=exc)
                ) from exc
            else:
                image_path = Path(rendered_path)
        logger.info(
            "Rendered process diagram '%s' to %s",
            process.name,
            image_path,
        )

        return DiagramArtifact(
            process_name=process.name,
            dot_path=dot_path,
            image_path=image_path,
            relative_path=self._as_relative_path(image_path),
            renderer=f"BPMNGraphviz::{self._format}",
            dot_source=dot_source,
        )

    def _build_bpmn_dot(
        self,
        *,
        process_name: str,
        context_label: str,
        happy_steps: Sequence[str],
        unhappy_steps: Sequence[str],
    ) -> str:
        """Create a DOT representation with BPMN-inspired styling."""

        lines: List[str] = ["digraph ProcessDiagram {"]
        lines.append(
            "  graph [rankdir=TB, bgcolor=white, pad=0.6, nodesep=0.6, "
            "ranksep=0.95, fontname=Helvetica, splines=ortho];"
        )
        lines.append(
            "  node [fontname=Helvetica, fontsize=11, shape=rect, "
            "style=\"rounded,filled\", fillcolor=\"#E3F2FD\", "
            "color=\"#1565C0\", penwidth=1.2, width=3.0];"
        )
        lines.append(
            "  edge [color=\"#1565C0\", penwidth=1.2, arrowsize=0.8];"
        )

        start_id = "node_start"
        end_id = "node_end"
        lines.append(
            "  \"{id}\" [shape=circle, width=0.70, height=0.70, "
            "style=filled, fillcolor=\"#C8E6C9\", color=\"#2E7D32\", "
            "label=\"Start\"];".format(id=start_id)
        )
        lines.append(
            "  \"{id}\" [shape=circle, peripheries=2, width=0.70, "
            "height=0.70, style=filled, fillcolor=\"#FFCDD2\", "
            "color=\"#C62828\", label=\"End\"];".format(id=end_id)
        )

        lanes: list[tuple[str, str, list[str]]] = []
        if happy_steps:
            lanes.append(("Happy Path", "happy", list(happy_steps)))
        if unhappy_steps:
            lanes.append(("Exception Path", "unhappy", list(unhappy_steps)))

        lane_last_nodes: list[str] = []
        for lane_label, lane_prefix, steps in lanes:
            cluster_suffix = self._slugify(lane_prefix).replace("-", "_")
            cluster_id = f"cluster_{cluster_suffix or lane_prefix}"
            lines.append(f"  subgraph {cluster_id} {{")
            lines.append("    style=rounded;")
            lines.append("    color=\"#B0BEC5\";")
            lines.append("    penwidth=1.0;")
            lines.append(
                "    fontname=Helvetica; fontsize=12; labelloc=\"t\"; "
                "labeljust=\"l\"; label=\"{label}\";".format(
                    label=lane_label
                )
            )

            previous_node_id: str | None = None
            for index, step_text in enumerate(steps, start=1):
                node_id = self._node_id(lane_prefix, index)
                label_html = self._task_label(lane_label, index, step_text)
                lines.append(
                    "    \"{node}\" [label=<{label}>];".format(
                        node=node_id,
                        label=label_html,
                    )
                )
                if previous_node_id is None:
                    lines.append(f'  "{start_id}" -> "{node_id}";')
                else:
                    lines.append(f'  "{previous_node_id}" -> "{node_id}";')
                previous_node_id = node_id

            if previous_node_id:
                lane_last_nodes.append(previous_node_id)

            lines.append("  }")

        if not lanes:
            # No explicit steps; connect start directly to end with context.
            context_note = self._task_label(
                "Process",
                1,
                self._format_step_text(
                    context_label or process_name,
                    prefix="Awaiting confirmation",
                ),
            )
            placeholder_id = self._node_id("placeholder", 1)
            lines.append(
                "  \"{node}\" [label=<{label}>];".format(
                    node=placeholder_id,
                    label=context_note,
                )
            )
            lines.append(f'  "{start_id}" -> "{placeholder_id}";')
            lane_last_nodes.append(placeholder_id)

        for terminal_node in lane_last_nodes:
            lines.append(f'  "{terminal_node}" -> "{end_id}";')

        # Provide subtle annotation under the diagram title.
        title_id = "diagram_title"
        title_label = self._task_label(
            "Process",
            0,
            self._format_step_text(
                f"{process_name} · {context_label}"
                if context_label
                else process_name
            ),
        )
        lines.append(
            "  \"{node}\" [shape=plaintext, label=<{label}>, "
            "fontname=Helvetica, fontsize=13];".format(
                node=title_id,
                label=title_label,
            )
        )
        lines.append(
            f'  "{title_id}" -> "{start_id}" '
            "[style=invis, weight=2];"
        )
        lines.append(f'  "{start_id}" -> "{end_id}" [style=invis, weight=0];')

        lines.append("}")
        return "\n".join(lines)

    def _render_with_cli(self, dot_path: Path) -> Path:
        dot_executable = which("dot")
        if not dot_executable:
            raise DiagramExportError(
                (
                    "Graphviz CLI 'dot' not found on PATH and python-graphviz "
                    "is missing."
                )
            )

        image_path = dot_path.with_suffix(f".{self._format}")
        command = [
            dot_executable,
            f"-T{self._format}",
            str(dot_path),
            "-o",
            str(image_path),
        ]
        result = subprocess.run(  # noqa: S603
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise DiagramExportError(
                (
                    "Graphviz CLI failed with exit {code}: {stderr}."
                ).format(code=result.returncode, stderr=result.stderr.strip())
            )
        if result.stderr.strip():
            logger.debug("Graphviz CLI stderr: %s", result.stderr.strip())
        return image_path

    def _normalize_steps(
        self,
        steps: Iterable[str],
        *,
        default: str,
        allow_empty: bool = False,
    ) -> List[str]:
        normalized = [
            self._clean_step(step)
            for step in steps
            if self._clean_step(step)
        ]
        if not normalized and not allow_empty:
            normalized = [default]
        return normalized

    @staticmethod
    def _clean_step(step: str) -> str:
        text = str(step).strip()
        return text.replace("\n", " ").replace("\"", "'") if text else ""

    def _format_step_text(self, text: str, *, prefix: str = "") -> str:
        trimmed = textwrap.shorten(
            text.strip() or "(unspecified)",
            width=72,
            placeholder="...",
        )
        safe = trimmed.replace("\n", " ").replace("\"", "'")
        return f"{prefix}{safe}" if prefix else safe

    def _task_label(self, lane_label: str, index: int, text: str) -> str:
        header = f"{lane_label}"
        if index > 0:
            header = f"{lane_label} #{index}"
        body = html_escape(text.strip() or "(unspecified)")
        header_escaped = html_escape(header)
        return (
            "<TABLE BORDER=\"0\" CELLBORDER=\"0\" CELLPADDING=\"6\">"
            f"<TR><TD ALIGN=\"left\"><B>{header_escaped}</B></TD></TR>"
            f"<TR><TD ALIGN=\"left\">{body}</TD></TR></TABLE>"
        )

    def _node_id(self, lane_prefix: str, index: int) -> str:
        base = self._slugify(f"{lane_prefix}-{index}").replace("-", "_")
        if not base:
            base = f"{lane_prefix}_{index}".replace("-", "_")
        return f"node_{base}"

    def _slugify(self, text: str) -> str:
        ascii_text = text.encode("ascii", "ignore").decode("ascii")
        cleaned = [
            char.lower() if char.isalnum() else "-"
            for char in ascii_text
        ]
        collapsed = "".join(cleaned).strip("-")
        while "--" in collapsed:
            collapsed = collapsed.replace("--", "-")
        return collapsed or "process"

    def _as_relative_path(self, path: Path) -> str:
        output_root = self._output_dir
        parent = output_root.parent
        try:
            return str(path.relative_to(parent))
        except ValueError:
            try:
                return str(path.relative_to(output_root))
            except ValueError:
                return str(path)
