# Research Log

Findings from the daily research pass, newest first. Exists so each run
builds on what the last one learned instead of re-researching the same
ground, and so a decision to *not* adopt something is recorded with its
reasoning rather than relitigated every week.

**Rules for entries**
- Date everything. Star counts, licenses and maintenance status all move.
- Record the **license by name** and flag source-available licenses dressed
  as open source (BSL, SSPL, Elastic, PolyForm, "modified Apache").
- Judge maintenance by **last commit**, not stars. Several 50k-star projects
  in this log are archived or frozen.
- "Nothing changed" is a valid entry. Do not manufacture findings.
- **Research informs design; it never licenses adding a dependency.** The
  zero-dependency constraint is a project decision, not a default to be
  overturned by a good-looking library.

---

## 2026-09-11 — Slice 1: model/provider landscape (OPEN RISK FOUND)

First run of the rotating check. **Finding: `llm_provider.py`'s OpenAI model
list is stale relative to the current generation, and at least one nearby ID
has a shutdown date inside the next two months.**

`_call_openai()` tries `gpt-4o` → `gpt-4-turbo` → `gpt-3.5-turbo`;
`_call_openai_tools()` tries `gpt-4o` → `gpt-4-turbo`. What search results
report (see confidence caveat below):

- `chatgpt-4o-latest` — deprecation announced 2025-11-18, **removed from the
  API 2026-02-17**. Not an ID we use, but it means the 4o family is being
  retired.
- `gpt-4-0314` — **shut down 2026-03-26**. `gpt-4-0613` — **scheduled
  shutdown 2026-10-23**, roughly six weeks out. Again not our exact IDs, but
  adjacent.
- Plain `gpt-4o` was reported as continuing in the API past the ChatGPT
  retirement, so it is probably still live — *probably* is doing real work in
  that sentence.
- Currently recommended family is **GPT-5.1** (e.g. `gpt-5.1-chat-latest`).

**Confidence: medium, NOT verified against the primary source.**
`developers.openai.com` is **blocked by this environment's network egress
proxy** (`EGRESS_BLOCKED`), so the authoritative deprecations page could not
be fetched. Everything above is from search-result summaries.

**Why this matters more than it looks:** when every OpenAI model in the list
fails, `_call_openai()` falls back to mock — and `generate()`'s mock fallback
is *silent*. A user with only `OPENAI_API_KEY` set would get mock output
while `get_status()` still named OpenAI as the provider. (`generate_with_tools()`
raises instead of falling back, so the tool path fails loudly — that
asymmetry was deliberate, and this finding is the argument for it.)

**Build candidates arising, for a future run:**
1. Resolve the model IDs against a reachable authoritative source, then
   update both OpenAI model lists — and re-check the Anthropic and NVIDIA IDs
   the same way.
2. Make the silent mock fallback in `generate()` visible in the result, the
   same way `used_tools` made a missing tool path visible. A caller should be
   able to tell that it got mock output because every real model failed.
3. Consider driving model IDs from `config.json` rather than hardcoding them
   in `llm_provider.py`, so a deprecation is a config edit rather than a code
   change.

Sources: [OpenAI API deprecations](https://developers.openai.com/api/docs/deprecations)
(blocked from this environment),
[VentureBeat on the GPT-4o API end date](https://venturebeat.com/ai/openai-is-ending-api-access-to-fan-favorite-gpt-4o-model-in-february-2026),
[OpenAI help centre: retiring GPT-4o](https://help.openai.com/en/articles/20001051-retiring-gpt-4o-and-other-chatgpt-models).

**Note for future runs:** some primary sources are egress-blocked here. Record
whether a finding is primary-source-verified or search-summary-only, and do
not upgrade the second into the first.

---

## 2026-09-11 — Baseline sweep: orchestration, platforms, infrastructure

Prompted by the question of whether an off-the-shelf framework should take
over this project. Three parallel research passes.

### Standing conclusion

**Keep the code; add capability underneath it.** Nothing surveyed models
departments, budgets, capacity or compensation — every platform would force
that domain logic to be re-expressed as nodes, which is precisely the
rigidity Flowise cited when it archived. The domain logic is the asset; the
hole was that agents couldn't *do* anything. Hence the tool-calling work
(checkpoints 34/36) rather than a migration.

### Orchestration frameworks

| Project | License | State (2026-09-11) | Verdict |
|---|---|---|---|
| Pydantic AI | MIT | 19.9k★, active | **Viable** if a framework is ever wanted — owns only the agent loop |
| OpenAI Agents SDK | MIT | 29.3k★, active | **Viable**, same reason. Swarm (its predecessor) is dead |
| Temporal | MIT (server + SDKs) | 23.0k★, active; `temporalio[openai-agents]` GA 2026-03-23 | Real, but heavy for one user — see DBOS below |
| LangGraph | library MIT, **`langgraph-api` Elastic 2.0** | 41.4k★ | **Trap.** The durable server you'd actually want needs a paid key in production |
| Microsoft AutoGen | MIT | 60.9k★ but **last push 2026-04-15**, maintenance mode | Dead end. Successor: `microsoft/agent-framework` (MIT, 1.0 GA 2026-04-03) |
| AG2 | Apache-2.0 | **4.9k★** | AutoGen fork trading on borrowed reputation |
| CrewAI | MIT | 58.4k★, active | Opinionated role-play crews; thin on durability |
| Agno | Apache-2.0 (relicensed Feb 2026) | 42.1k★ | Framework free; **self-hosted control plane is Enterprise-only** |
| AgentScope | Apache-2.0 | 31.3k★, active | Genuinely free; thinner Western community |
| Letta | Apache-2.0 | 24.7k★ | A memory/agent *server*, not an orchestrator |
| Strands | Apache-2.0 | **renamed** `sdk-python` → `harness-sdk` | Rename = churn signal |
| Mastra | Apache-2.0 core + **proprietary `ee/`** | 27.9k★ | TypeScript only — wrong language |

### Self-hosted platforms

- **Archived / dead:** Flowise (archived 2026-08-13 — its sunset note says low-code graph builders "hit the limit" as coding agents got good), AgentGPT (archived 2026-01-28), SuperAGI (last push 2025-01-22).
- **License traps:** n8n is **Sustainable Use License**, non-OSI — internal business use fine, but Projects/RBAC/SSO/log-streaming are Enterprise and 2026 added per-execution fees to self-hosted Business. Dify is "**modified Apache-2.0**" banning multi-tenant operation — a Sustainable-Use clause wearing an Apache hat; 9-container compose, 4–8 GB. Open WebUI stopped being OSI open source at v0.6.6 (Apr 2025). AutoGPT's platform is PolyForm Shield.
- **Clean licenses:** Activepieces (MIT core + proprietary `ee/`), OpenHands (MIT, 87k★, v1.16.0 2026-08-27), AnythingLLM (MIT), Langflow (MIT), Windmill (AGPL).
- **What a platform would actually replace:** scheduler, credential vault, connector catalog, retries, run-history UI. Nothing replaces the domain logic.

### Infrastructure

- **LLM gateway — do NOT adopt LiteLLM.** MIT core, and it does what `llm_provider.py` hand-rolls. But: **2026-03-24 supply-chain compromise** — PyPI `litellm` 1.82.7/1.82.8 shipped a malicious `litellm_init.pth` that executed on *every Python process start* and exfiltrated SSH keys, cloud credentials and `.env` API keys (TeamPCP campaign, via a poisoned Trivy in LiteLLM's CI; yanked in ~3h). Also mid-Rust-migration through Dec 2026. Our five HTTP shapes already work. Alternative if ever needed: **Bifrost** (Apache-2.0, single Go binary, no Python dependency).
- **Observability — Langfuse is the one worth adopting** when there is real work to observe. **MIT for all product features** since June 2025, no seat/retention caps self-hosted; only SCIM, audit logs, project-RBAC and UI customization are EE. Cost: v3+ is **six containers** (web, worker, Postgres, ClickHouse, Redis, MinIO), ~4 vCPU / 8 GB. **Legacy Ingestion API sunsets 2026-11-16** — send OTLP with `x-langfuse-ingestion-version: 4`. Avoid Phoenix (**Elastic 2.0**, not OSI) and Helicone (Apache but maintenance mode since the Mintlify acquisition, March 2026).
- **OTel GenAI semconv is NOT stable.** As of v1.42.0 (2026-06-12) `gen_ai.*` moved to a dedicated repo with no releases/tags; nothing marked Stable as of July 2026, no 1.0, no timeline. Emit raw OTLP from stdlib `urllib` using `gen_ai.*` names — if the convention churns, you change string constants.
- **Durable execution — DBOS over Temporal at this scale.** `dbos` (PyPI, 2.x) is **MIT**, an in-process *library*: `@DBOS.workflow` / `@DBOS.step` decorators plus a Postgres connection string, no new service. Temporal is MIT but a separate cluster + Elasticsearch (~$400–900/mo for a small prod deploy). **Restate's runtime is BSL-1.1**; **Inngest's server is SSPL**. Only relevant once Postgres is on the table.
- **Memory — skip all of them.** Letta/Mem0/Cognee are Apache-2.0 but are services wrapping an embedding table. **Zep retired its self-hosted Community Edition (April 2025, more Feb 2026)** — only Graphiti remains OSS and now needs Neo4j/FalkorDB/Kuzu. Postgres + pgvector is the right shape for one user.
- **Evals — promptfoo**, MIT, used as an *external CLI binary* so it costs nothing in dependency terms. **Acquired by OpenAI (announced 2026-03-09)**, publicly committed to staying open source — worth watching for a license change. DeepEval and Inspect AI are pytest-first, which does not fit this repo's bare-script test convention.
- **MCP is settled.** Spec **2026-07-28** current, SDKs shipped alongside, formal ≥12-month deprecation-to-removal lifecycle, Python SDK stable, 10k+ public servers. Exposing this system as an MCP server remains the cheapest high-value integration — and over stdio it is JSON-RPC on stdin/stdout, ~200 stdlib lines, so it need not cost a dependency either.

### Open watch items

- `llm_provider.py` hardcodes model IDs (`claude-sonnet-5`, `claude-opus-5`, `gpt-4o`, `gpt-4-turbo`, `nvidia/nemotron-3-ultra-550b-a55b`). A deprecation degrades the system to mock **silently**. Re-check periodically.
- promptfoo's license post-acquisition.
- OTel GenAI semconv reaching 1.0.
