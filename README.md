# Business Analyst Interview Agent

This project implements an interviewing AI agent using the Microsoft Agent Framework (MAF). The agent emulates a professional Business Analyst that guides stakeholders through structured discovery conversations covering project scope, process scope, or change request scope. The collected insights are used to assemble a functional specification draft.

## Features

- Guided interview experience tailored to the selected scope type.
- Dynamic questioning that leverages an LLM and conversation memory.
- Functional specification synthesis that highlights objectives, stakeholders, assumptions, requirements, and open issues.
- Deterministic specification output with numbered sections for scope, current state (AS-IS), personas, and requirements.
- Reference-numbered specification table capturing business rules and data dependencies.
- Automated reviewer agent that validates subject coverage and requirement table quality before finalizing the draft.
- Command line interface suitable for demos or quick feedback loops.
- Built-in transcript archive that writes JSONL logs, mirrors sessions into Redis, and ships with search/report utilities.
- Agenda-driven questioning that walks through nine core business-analysis subjects,
  with a configurable per-subject question cap and automatic wrap-up once everything
  is covered.
- LLM-driven stakeholder simulator to perform repeatable interview runs.

## Getting Started

### 1. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -e .
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and provide the required values. At minimum you must supply model endpoint credentials compatible with Microsoft Agent Framework.

```bash
cp .env.example .env
```

| Variable | Description |
| -------- | ----------- |
| `MAF_MODEL_PROVIDER` | Provider identifier, e.g. `azure-openai` or `openai`. |
| `MAF_MODEL` | Chat completion model name. |
| `MAF_MODEL_ENDPOINT` | HTTPS endpoint base URL (if using Azure OpenAI). |
| `MAF_MODEL_API_KEY` | API key or token with access to the model endpoint. |
| `MAF_DEFAULT_SCOPE` | Optional default interview scope (`project`, `process`, `change_request`). |
| `MAF_TRANSCRIPT_JSONL` | Optional path to the JSONL transcript archive (default: `./outputs/transcripts.jsonl`). |
| `MAF_REDIS_URL` | Optional Redis connection string for transcript storage (default: `redis://localhost:6379/0`). |
| `MAF_SUBJECT_MAX_QUESTIONS` | Maximum number of questions to ask per subject before moving on (default: `3`). |
| `MAF_REVIEW_MAX_PASSES` | Maximum number of automated reviewer retries before surfacing unresolved items (default: `3`). |

> **Note:** The legacy `MAF_TRANSCRIPT_DB` variable is still honored and treated as the JSONL
> archive path. Make sure a Redis instance is reachable at the configured URL (for local
> development, `docker run -p 6379:6379 redis/redis-stack` works well).

### 4. Run the agent CLI

```bash
python -m ba_interview_agent
```

The CLI will ask for the interview scope, then orchestrate the conversation. When the interview ends, a functional specification draft will be displayed and saved to `./outputs/`.
Type `done` (or `no further questions`) when you have provided enough answers. Each interview is appended to the JSONL archive and mirrored to Redis for retrieval.

The agent follows a nine-subject agenda and tracks how many questions have been asked
for the current subject. By default it asks up to three questions per subject,
automatically proceeds to the next topic, and ends the interview after every
subject is complete. Override the cap with the `MAF_SUBJECT_MAX_QUESTIONS`
environment variable or per run via `python -m ba_interview_agent
--subject-max-questions 2`. After the functional specification is shown, the
interviewer offers a final opportunity to add clarifications and will regenerate
the draft if you supply extra notes.

### 5. Launch the DevUI (optional)

Start the Microsoft Agent Framework DevUI to run the agent through a web UI:

```bash
python -m ba_interview_agent --devui
```

By default the DevUI listens on `http://127.0.0.1:8080` and opens a browser window automatically. Use `--devui-host`, `--devui-port`, or `--devui-no-auto-open` to adjust the server settings. Supply `--scope` to expose only a single interview scope in the UI. Pass `--devui-tracing` (or set `ENABLE_OTEL=true`) to enable OpenTelemetry tracing in the DevUI.

### 6. Explore transcripts (optional)

```bash
python -m ba_interview_agent transcripts list
python -m ba_interview_agent transcripts search "integration"
python -m ba_interview_agent transcripts report --scope project
```

The transcript tooling pulls from Redis when available and falls back to the JSONL archive. Use it to validate saved sessions, troubleshoot Redis entries, or capture quick activity summaries.
Transcript entries now include a `subject` field so you can search questions by the agenda topic when exploring historical interviews.

### 7. Simulate interviews with stakeholder personas (optional)

Kick off automated runs that answer questions using an LLM-generated stakeholder:

```bash
python -m ba_interview_agent simulate --count 3 --seed 42
```

Each simulation spins up a fresh persona tailored to the selected scope. Provide
`--persona-file persona.json` to pin the persona details or `--quiet` to suppress
per-question logs when running batch simulations.

### Automated specification review

Every time the functional specification is generated, a reviewer agent checks that all
agenda subjects are represented and that the "Functional Requirements" table follows
the required format (Spec ID | Specification Description | Business Rules/Data
Dependency with sequential FR-1, FR-2, … identifiers). If anything is missing, the CLI
and simulator prompt for additional answers before regenerating the draft. To prevent
infinite loops, reviewer retries are capped by `MAF_REVIEW_MAX_PASSES`; once the limit is
reached (or the reviewer repeats the same request), any outstanding items are surfaced so
you can address them manually.

## Project Structure

```
src/
  ba_interview_agent/
    __init__.py
    __main__.py
    cli.py
    config.py
    devui.py
    interview_agent.py
    maf_client.py
    prompts.py
    test_agent.py
    transcript_archive.py
    transcript_store.py
    transcripts_cli.py
outputs/
  (generated specs)
```

## Linting & Tests

```bash
pip install -e .[dev]
pytest
```

## Next Steps

- Extend scope packs with domain-specific questioning strategies.
- Build a lightweight frontend (web or Teams) for richer stakeholder interactions.
- Add richer analytics dashboards or visualizations for transcript insights.
