# MiroFish — Reverse-engineered PRD

*A reconstruction of the requirements document that would have produced [666ghj/MiroFish](https://github.com/666ghj/MiroFish), based on a read of the public repo (backend/, config, services, API surface, requirements pins).*

---

## 0. Document metadata

| Field | Value |
|---|---|
| Product name | MiroFish |
| Tagline | "A Simple and Universal Swarm Intelligence Engine, Predicting Anything" |
| License (impl.) | AGPL-3.0 |
| Status | v0.1 prototype, public on GitHub |
| Author of this PRD | Reverse-engineered from source by Annix Geo H2 research, 2026-05-29 |
| Primary repo path | `666ghj/MiroFish` |

---

## 1. Vision

A general-purpose "rehearse the future" engine. The user uploads **seed material** (news, policy drafts, financial filings, narrative text). The system **automatically** stands up a high-fidelity parallel digital world populated by hundreds–thousands of LLM agents, each with persona and memory derived from the seed. Agents act on simulated social platforms (Twitter and/or Reddit) for N rounds. After the run, a **ReportAgent** queries the simulation's memory graph and produces a prediction report. The user can **chat with any agent** in the simulated world post-hoc.

**Why this shape instead of "ask Claude to predict X":** the simulation produces *grounded, traceable evidence* (specific agents said specific things at specific rounds), so the prediction is auditable rather than a single LLM's hot take.

---

## 2. Problem statement

LLM "prediction" today collapses into one prompt-and-response. There is no:

- traceability (you cannot inspect *why* the model said what it said),
- scenario branching (no way to ask "what if a new fact drops at hour 12?"),
- stakeholder texture (a single LLM smooths over how different demographics actually react),
- artifacts to chat with after the fact (the prediction is dead the moment it's printed).

Decision-makers (PMs deciding launches, communications teams stress-testing announcements, policy teams modelling public reaction, fund managers war-gaming earnings reactions) need a low-risk **simulated rehearsal** they can interrogate.

---

## 3. Goals & non-goals

### Goals

| # | Goal |
|---|---|
| G1 | Convert arbitrary seed material into a structured knowledge graph in < 5 min for ~50KB of seed text |
| G2 | Auto-generate 50–500 distinct LLM-backed agent personas grounded in entities in that graph |
| G3 | Run a multi-round social-platform simulation (Twitter and/or Reddit semantics) where agent actions and memories are persisted |
| G4 | Produce a structured prediction report from the post-simulation memory graph, with quoted agent evidence |
| G5 | Let users chat with any individual agent after the run |
| G6 | Be deployable by one developer with Docker Compose + two API keys (LLM + Zep) |

### Non-goals

| # | Non-goal |
|---|---|
| NG1 | Domain-specific physics or numerical simulation (this is social-behaviour simulation only) |
| NG2 | Real-time streaming during a simulation (sim runs as a subprocess, file-IPC checkpoint model) |
| NG3 | Mid-simulation variable injection in v0.1 (config shows `INTERVIEW`, `BATCH_INTERVIEW`, `CLOSE_ENV` only — no inject/pause/resume) |
| NG4 | On-prem LLMs (designed for OpenAI-compatible cloud LLMs and Zep Cloud) |
| NG5 | Multi-tenant SaaS isolation (single-user dev tool first) |

---

## 4. Target users

1. **Communications / PR strategist** — wants to know how a press release will land before it ships.
2. **Product manager** — wants to rehearse the user reaction to a price/feature change.
3. **Policy researcher** — wants to model demographic spread of a draft policy.
4. **Quant / IR analyst** — wants to rehearse retail reaction to earnings narrative.
5. **Creative / narrative writer** — wants a populated "world" to query for plot consistency.

The common job-to-be-done: *"I have a written artifact. Show me what the world looks like after it lands."*

---

## 5. User journey (golden path)

1. **Create project**, name it ("Q3 launch", "Bill 218 reaction").
2. **Upload seed material** — PDFs / Markdown / TXT (≤ 50MB, file types: pdf/md/txt/markdown).
3. System **chunks** the text (default 500 tokens, 50 overlap) and **submits to Zep Cloud** as `EpisodeData`. Zep extracts entities and edges into a graph. User sees a progress bar via async task polling.
4. User reviews the entities, optionally **filters by type** (person, org, journalist, university, …) before persona generation.
5. **Prepare simulation:** system enriches each entity via Zep graph search and calls the LLM (default `gpt-4o-mini`) to generate an `OasisAgentProfile` per entity. Individual entities get demographics + MBTI + interests + memories. Group/institutional entities get an official-account persona. Up to 5 parallel workers.
6. User picks **platforms** (Twitter, Reddit, or both in parallel) and a **simulation requirement** in natural language ("how will moderate Republicans respond over 48h?"). LLM converts requirement into a `SimulationParameters` config (total hours, minutes per round, activity multipliers, hot topics, scheduled events).
7. User clicks **Run**. Backend spawns a subprocess: `run_twitter_simulation.py` | `run_reddit_simulation.py` | `run_parallel_simulation.py`. The subprocess uses **OASIS** (`camel-oasis==0.2.5`) to step agents through rounds. Each agent's actions are written to `actions.jsonl`; the `ZepGraphMemoryUpdater` batches them (5 actions/batch) and writes natural-language episodes back to the Zep graph: e.g. `agent_name: 点赞了user的帖子：『content』`.
8. Backend tails the log files and updates `SimulationRunState` (current_round / total_rounds). UI polls the status endpoint.
9. On completion, user clicks **Generate report**. **ReportAgent** runs a ReAct loop:
   - PLAN: GPT decomposes the user requirement into 2–5 sections (JSON output).
   - For each section: up to 5 tool calls picked from `insight_forge`, `panorama_search`, `quick_search`, `interview_agents`.
     - `insight_forge` decomposes the section's query into up to 5 sub-questions, runs Zep `graph.search` with cross-encoder rerank on each.
     - `panorama_search` separates **current valid facts** from **historical/expired facts** using Zep edge `valid_at` / `invalid_at` / `expired_at`.
     - `interview_agents` writes a file-IPC `INTERVIEW` or `BATCH_INTERVIEW` command to the running subprocess's `ipc_commands/` directory; the sim writes a reply to `ipc_responses/` (60s timeout).
   - Section is written as Markdown with **embedded agent quotations** ("Agent states: original text"). No internal headers — bold-only.
10. User browses the rendered report. UI offers **chat with any agent**: send a message → file-IPC `INTERVIEW` to the persistent simulation environment → agent reply.

---

## 6. Functional requirements

### 6.1 Phase 1 — Graph Building

- **F-1.1** Accept upload of PDF / MD / TXT files (≤ 50MB total).
- **F-1.2** Parse via `PyMuPDF` (PDFs) and `charset-normalizer` (text encoding detection).
- **F-1.3** Chunk text at configurable size (default 500) with overlap (default 50).
- **F-1.4** Auto-generate an **ontology** of entity & edge types appropriate to the seed material (`ontology_generator.py`).
- **F-1.5** Push chunks to Zep Cloud as `EpisodeData` via `graph.add_batch()`. Zep performs entity & relationship extraction internally — MiroFish does **not** call an LLM directly for extraction.
- **F-1.6** Expose async task with progress callback; allow inspection of nodes/edges via REST.

### 6.2 Phase 2 — Environment Setup

- **F-2.1** For each entity, run Zep `graph.search()` to enrich with facts and related nodes.
- **F-2.2** Call OpenAI-compatible chat model to generate `OasisAgentProfile`. Fields: `user_id`, `user_name`, `bio`, `persona`, `karma`, `friend_count`, `follower_count`, `statuses_count`, `age`, `gender`, `MBTI`, `country`, `profession`, `interested_topics[]`, `source_entity_uuid`, `source_entity_type`.
- **F-2.3** Different persona prompt templates for **individual** vs **group/institutional** entities (groups get `gender="other"`, `age=30` defaults, organisational tone).
- **F-2.4** Rule-based fallback if LLM call fails.
- **F-2.5** Export profiles in two formats: **Reddit JSON** (`username` key) and **Twitter CSV** with configurable fieldnames.
- **F-2.6** Parallel generation, configurable worker pool, default 5.

### 6.3 Phase 3 — Simulation

- **F-3.1** Generate `SimulationParameters` via LLM from the user's natural-language requirement. Fields include `total_simulation_hours`, `minutes_per_round`, `agents_per_hour_min/max`, per-agent `AgentActivityConfig` (activity multipliers, sentiment bias, stance, influence weight), initial posts, scheduled events, hot topics, narrative direction, platform-specific algorithm weights and viral thresholds.
- **F-3.2** Total rounds derived from `total_hours * 60 / minutes_per_round`.
- **F-3.3** Backend launches one of three subprocess scripts (`run_twitter_simulation.py`, `run_reddit_simulation.py`, `run_parallel_simulation.py`), powered by OASIS.
- **F-3.4** Agent action vocabulary configurable per platform: 6 Twitter actions, 13 Reddit actions (defaults in `Config`).
- **F-3.5** All actions written to `actions.jsonl`. `ZepGraphMemoryUpdater` batches them (`BATCH_SIZE=5`), converts to natural-language episodes via `to_episode_text()`, and writes them to the Zep graph so they become queryable evidence.
- **F-3.6** Simulation state persisted as JSON checkpoints (`run_state.json`, `state.json`); Flask reconstructs status by re-reading.
- **F-3.7** File-IPC channel: Flask writes JSON commands to `ipc_commands/`, sim polls and writes responses to `ipc_responses/`. Command set: `INTERVIEW`, `BATCH_INTERVIEW`, `CLOSE_ENV`. (No inject/pause/resume in v0.1 — documented as a gap.)
- **F-3.8** Lifecycle: `CREATED → PREPARING → READY → RUNNING → PAUSED|STOPPED|COMPLETED|FAILED` (manager defines all states; `RUNNING→COMPLETED` is the only fully wired path in v0.1).

### 6.4 Phase 4 — Report generation

- **F-4.1** PLAN system prompt instructs the LLM to write from a "god's perspective" on a simulated future, returning JSON with 2–5 sections.
- **F-4.2** For each section, run a ReAct loop with `MAX_TOOL_CALLS_PER_SECTION=5` and `MAX_REFLECTION_ROUNDS=2`, temperature `0.5`.
- **F-4.3** Tool set:
  - `insight_forge` — LLM decomposes query into ≤5 sub-questions; each runs `graph.search(limit=15)` with cross-encoder rerank.
  - `panorama_search` — returns `PanoramaResult` separating **current valid** vs **historical/expired** facts (using Zep edge temporal fields).
  - `quick_search` — single lightweight graph query.
  - `interview_agents` — actual IPC interview against the running sim environment.
- **F-4.4** Section content must originate from simulation events and agent quotes; markdown headers prohibited inside a section (bold only); 3–5 tool calls minimum per section.
- **F-4.5** On hitting the tool-call ceiling, force a Final Answer.

### 6.5 Phase 5 — Deep interaction

- **F-5.1** Chat with individual agent via `INTERVIEW`.
- **F-5.2** Chat with `ReportAgent` to drill into report claims.
- **F-5.3** Environment remains live until explicit `CLOSE_ENV`.

### 6.6 Public API surface (Flask, default `0.0.0.0:5001`)

Routes confirmed in `api/simulation.py` (+ companion `graph.py`, `report.py`):

| Method | Path | Purpose |
|---|---|---|
| GET | `/entities/<graph_id>` | filter entities, optional enrich |
| GET | `/entities/<graph_id>/<uuid>` | entity detail |
| GET | `/entities/<graph_id>/by-type/<type>` | entities by type |
| POST | `/create` | create simulation (project_id, graph_id, enable_twitter, enable_reddit) |
| POST | `/prepare` | async prepare (entity reading, profile gen, config gen) |
| POST | `/prepare/status` | progress 0–100 |
| GET | `/<simulation_id>` | full state + run_instructions when READY |
| GET | `/list?project_id=` | list per project |
| GET | `/history?limit=20` | enriched history with report_id, files |
| GET | `/<simulation_id>/profiles?platform=` | agent profiles |

---

## 7. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | Python 3.11–3.12 only |
| NFR-2 | Stateless Flask process; all durable state in files + Zep Cloud |
| NFR-3 | Default LLM `gpt-4o-mini`; supports any OpenAI-compatible endpoint via `LLM_BASE_URL` (the README explicitly recommends **Alibaba Qwen-plus via Bailian** as a cost trade-off) |
| NFR-4 | Subprocess simulation must survive Flask restart (state.json reloadable) |
| NFR-5 | File-IPC round-trip ≤ 60s |
| NFR-6 | Docker Compose deploy with two secrets: `LLM_API_KEY`, `ZEP_API_KEY` |
| NFR-7 | i18n: locale files in `/locales/` and `utils/locale.py` (zh + en) |
| NFR-8 | Cost caution: README warns that simulations >40 rounds run up significant LLM bills |

---

## 8. Technical architecture

```
┌────────────┐    files     ┌──────────────────┐    Zep SDK     ┌──────────────┐
│  Frontend  │ ───────────► │  Flask backend   │ ──────────────►│  Zep Cloud   │
│   (Vue)    │ ◄─────────── │  (run.py:5001)   │ ◄──────────────│ (GraphRAG +  │
└────────────┘   REST/json  │                  │                │  agent mem)  │
                            │  - api/          │                └──────────────┘
                            │  - services/     │
                            │  - models/       │  spawn         ┌──────────────┐
                            │  - utils/        │ ─────────────► │ Subprocess:  │
                            └──────┬───────────┘                │ OASIS sim    │
                                   │   ipc_commands/            │ run_*.py     │
                                   │   ipc_responses/           │ (camel-oasis │
                                   └────────────────────────────┤  0.2.5)      │
                                                                └──────┬───────┘
                                                                       │ actions.jsonl
                                                                       │ run_state.json
                                                                       ▼
                                                                  (filesystem)
```

**Key insight:** MiroFish is essentially **a Flask orchestrator over Zep Cloud + OASIS**. The "secret sauce" is not in the simulation code (that's CAMEL-AI's OASIS) and not in the graph extraction (that's Zep). It is in the **glue layer**: how entities become personas, how persona action logs round-trip back into the graph as queryable evidence, and the **ReportAgent tool design** (`insight_forge` / `panorama_search` / `interview_agents`).

---

## 9. Data model (essential)

```python
SimulationState(
    simulation_id, project_id,
    enable_twitter: bool, enable_reddit: bool,
    entity_count: int, profile_count: int,
    current_round: int, total_rounds: int,
    status: enum[CREATED, PREPARING, READY, RUNNING,
                 PAUSED, STOPPED, COMPLETED, FAILED],
    twitter_status, reddit_status,
    created_at, updated_at, error: str | None,
)

OasisAgentProfile(
    user_id, user_name, name, bio, persona,
    karma, friend_count, follower_count, statuses_count,
    age, gender, mbti, country, profession,
    interested_topics: list[str],
    source_entity_uuid, source_entity_type, created_at,
)

AgentActivity(
    platform: 'twitter'|'reddit',
    agent_name, action_type, args: dict,
)
# → to_episode_text() → Zep graph episode

EdgeInfo(
    ...,
    valid_at, invalid_at, expired_at,  # → is_expired, is_invalid
)
```

---

## 10. Dependencies & risks

| Dep | Version pin | Risk |
|---|---|---|
| Zep Cloud SDK | `==3.13.0` (exact) | Vendor lock-in; entity extraction quality is a black box |
| CAMEL-OASIS | `==0.2.5` (exact) | Pre-1.0; breaking changes per release |
| CAMEL-AI | `==0.2.78` (exact) | same |
| Flask | `>=3.0.0` | safe |
| OpenAI SDK | `>=1.0.0` | safe — but **LLM_BASE_URL** is what makes this provider-agnostic |
| PyMuPDF | `>=1.24.0` | AGPL — note the whole project is AGPL-3.0 |

**Top product risks**

1. **Cost.** README warns ">40 rounds" gets expensive. Hundreds of agents × rounds × tool calls compound fast.
2. **Variable injection gap.** Marketing claim says users "dynamically inject variables and simulate various scenarios" but the IPC layer in v0.1 only supports `INTERVIEW`/`BATCH_INTERVIEW`/`CLOSE_ENV`. There is no `inject_event` or `set_state` command — scenario branching is implemented at *config time*, not *runtime*. Closes the gap by relaunching with a modified config.
3. **Zep vendor coupling.** No fallback path if Zep changes pricing or API. All entity extraction, memory, and the temporal validity model live there.
4. **Subprocess fragility.** File-IPC with polling and 60s timeout is simple but brittle — crashed subprocess leaves stale `state.json`.

---

## 11. Success metrics

| Metric | Target |
|---|---|
| Time from upload to READY (50 KB seed, default config) | ≤ 5 min |
| Cost per 20-round simulation, 50 agents, dual-platform | ≤ ~$5 on `gpt-4o-mini` (per README cost guidance) |
| Report sections grounded in ≥3 agent quotes each | 100% of sections |
| Subprocess crash recoverability | state.json reloadable on restart |
| Agent-chat round-trip latency | < 60s (IPC timeout) |

---

## 12. Out of scope (explicitly)

- Domain-specific numerical simulation (physics, chemistry, finance Monte Carlo).
- Real-time mid-simulation variable injection (v0.2 work).
- Multi-tenant SaaS, RBAC, audit logging.
- Self-hosted graph store as Zep replacement.
- Native iOS/Android — Vue web only.

---

## 13. Open questions / follow-ups

1. How is **ontology** auto-generated (`ontology_generator.py`)? Is it one LLM call per project, or a fixed template?
2. What's the **dual-platform parallelism** model? Two independent OASIS environments and the ReportAgent reconciles, or shared agent identities posting to both?
3. How does `panorama_search` decide that a fact has "expired"? Is `invalid_at` written by Zep automatically when newer edges contradict, or by MiroFish on the round-tripped episodes?
4. Is `report.py` storing the rendered report in Zep too, or only on disk?
