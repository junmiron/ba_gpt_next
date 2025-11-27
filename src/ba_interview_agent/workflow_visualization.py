"""Workflow visualization helpers using Microsoft Agent Framework."""

import argparse
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
from typing import Optional, Sequence

from agent_framework import (
    Executor,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    WorkflowViz,
    handler,
)

from .config import AppSettings
from .diagram_agent import DiagramFormat

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkflowVisualizationArtifact:
    """Represents exported workflow visualization assets."""

    dot_path: Path
    mermaid_path: Path
    image_path: Optional[Path]
    renderer: str
    dot_source: str
    mermaid_source: str


class WorkflowVisualizationError(RuntimeError):
    """Raised when the workflow visualization cannot be produced."""


class _WorkflowStepExecutor(Executor):
    """Simple executor used to convey workflow structure for visualization."""

    def __init__(self, executor_id: str, label: str) -> None:
        super().__init__(id=executor_id)
        self._label = label

    @handler
    async def handle(self, message: str, ctx: WorkflowContext[str]) -> None:
        _ = message  # Handlers receive string messages to satisfy type checks.
        await ctx.send_message(self._label)


def build_interview_workflow() -> Workflow:
    """Construct the interview workflow definition used across the app."""

    step_definitions = [
        ("kickoff", "Kickoff interview and establish context"),
        ("interview_loop", "Iterative questioning and transcript capture"),
        ("summarize", "Summarize conversation into structured spec"),
        (
            "as_is_confirmation",
            "Confirm AS-IS processes and bullet points",
        ),
        (
            "future_state_confirmation",
            "Confirm TO-BE processes and outcomes",
        ),
        (
            "process_diagrams",
            "Generate process diagrams (WorkflowViz + Graphviz)",
        ),
        ("review", "Run functional specification review agent"),
        ("persist_outputs", "Persist transcript, summary, and artifacts"),
        ("closing", "Deliver final specification and closing message"),
    ]

    executors = [
        _WorkflowStepExecutor(step_id, label)
        for step_id, label in step_definitions
    ]

    builder = WorkflowBuilder(
        name="Business Analyst Interview Workflow",
        description=(
            "Guided workflow that conducts stakeholder interviews, "
            "captures requirements, and produces a functional "
            "specification."
        ),
    )
    builder.set_start_executor(executors[0])
    builder.add_chain(executors)
    workflow = builder.build()
    workflow.id = "ba-interview-workflow"
    return workflow


class WorkflowVisualizer:
    """Builds and exports the Business Analyst interview workflow diagram."""

    def __init__(
        self,
        *,
        output_dir: Path,
        image_format: DiagramFormat = "svg",
    ) -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._format: DiagramFormat = image_format

    def render(self) -> WorkflowVisualizationArtifact:
        workflow = self._build_workflow()
        viz = WorkflowViz(workflow)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        base_name = self._output_dir / f"ba_interview_workflow_{timestamp}"

        dot_source = viz.to_digraph()
        dot_path = base_name.with_suffix(".dot")
        dot_path.write_text(dot_source, encoding="utf-8")

        mermaid_source = viz.to_mermaid()
        mermaid_path = base_name.with_suffix(".mmd")
        mermaid_path.write_text(mermaid_source, encoding="utf-8")

        image_path: Optional[Path] = None
        renderer = "WorkflowViz"
        if self._format == "dot":
            logger.info(
                "Workflow visualization exported as DOT only: %s",
                dot_path,
            )
        else:
            try:
                rendered = viz.export(
                    format=self._format,
                    filename=str(base_name.with_suffix("")),
                )
                image_path = Path(rendered)
                renderer = f"WorkflowViz::{self._format}"
            except ImportError:
                logger.info(
                    "python-graphviz unavailable; falling back to "
                    "Graphviz CLI for workflow export.",
                )
                image_path = self._render_with_cli(dot_path)
                renderer = "graphviz-cli"
            except ValueError as exc:
                raise WorkflowVisualizationError(
                    (
                        "Workflow visualization export failed due to invalid "
                        "parameters: {error}."
                    ).format(error=exc)
                ) from exc

        return WorkflowVisualizationArtifact(
            dot_path=dot_path,
            mermaid_path=mermaid_path,
            image_path=image_path,
            renderer=renderer,
            dot_source=dot_source,
            mermaid_source=mermaid_source,
        )

    def _build_workflow(self) -> Workflow:
        return build_interview_workflow()

    def _render_with_cli(self, dot_path: Path) -> Path:
        dot_executable = which("dot")
        if not dot_executable:
            raise WorkflowVisualizationError(
                (
                    "Graphviz CLI 'dot' not found on PATH and "
                    "python-graphviz is missing."
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
            raise WorkflowVisualizationError(
                (
                    "Graphviz CLI failed with exit {code}: {stderr}."
                ).format(code=result.returncode, stderr=result.stderr.strip())
            )
        if result.stderr.strip():
            logger.debug("Graphviz CLI stderr: %s", result.stderr.strip())
        return image_path


def run_workflow_visualization_cli(
    settings: AppSettings,
    argv: Sequence[str],
) -> None:
    """CLI helper that renders the interview workflow visualization."""

    parser = argparse.ArgumentParser(
        prog="ba-interview-agent workflow-viz",
        description="Render the Business Analyst agent workflow diagram.",
    )
    parser.add_argument(
        "--format",
        choices=["svg", "png", "pdf", "dot"],
        default="svg",
        help="Output format for the rendered image (default: svg).",
    )
    parser.add_argument(
        "--output-dir",
        help=(
            "Directory for workflow visualization files. Defaults to the "
            "agent output directory under 'workflow'."
        ),
    )
    args = parser.parse_args(list(argv))

    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else settings.output_dir / "workflow"
    )
    visualizer = WorkflowVisualizer(
        output_dir=output_dir,
        image_format=args.format,
    )
    artifact = visualizer.render()

    print(f"Workflow DOT written to: {artifact.dot_path}")
    print(f"Workflow Mermaid definition written to: {artifact.mermaid_path}")
    if artifact.image_path is not None:
        print(
            "Workflow image exported to: {path} (renderer={renderer})".format(
                path=artifact.image_path,
                renderer=artifact.renderer,
            )
        )
    else:
        print("Image export skipped because --format dot was selected.")
