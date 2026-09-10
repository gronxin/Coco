# Codex Token Analyzer Implementation Plan

**Goal:** Local-only Chinese token analytics with task/turn/call drilldown, historical compatibility, diagnostics, API-equivalent costs and subscription allocation.

**Architecture:** Read-only Codex JSONL and optional state SQLite metadata → sanitized incremental event store → deduplicated accounting ledger → FastAPI → React/TypeScript/ECharts. Source data and credentials are never modified. No model calls or remote telemetry.

**Tech Stack:** Python 3.9+, SQLite, FastAPI, React, TypeScript, Vite, ECharts.

## Work packages
- [x] Parser/store: source discovery, transactional cursors, partial lines, replacements, sanitized events, cumulative checkpoints, globally unique calls, unassigned summaries and explicit conflicts. Unit fixtures verify mixed/new/legacy logs, fork inheritance, child relationships and replay.
- [x] Analytics/API: shared filtered ledger feeds task/turn/call views, trends, CSV, price coverage, effective dated decimal pricing, whole-month subscription denominator, settings and sync status. Test API parity, timezones, currencies and validation.
- [x] Frontend: Chinese sidebar overview/tasks/analysis/settings, filter bar, charts, expandable turns/calls, editable prices/subscription/source, errors/loading/empty states, responsive accessible layout, locally bundled assets.
- [x] Acceptance: build + tests, browser interaction checks, read-only audit of 10 actual tasks and source hashes, replay/restart checks, latency and offline-request checks. Evidence and limitations are recorded in `docs/verification.md`.

## Shared API contract
GET /api/overview, /api/tasks, /api/analysis, /api/export.csv accept start/end inclusive YYYY-MM-DD (Asia/Shanghai), project, model, search. GET /api/tasks adds sort=tokens|time|cost and direction=asc|desc. All monetary aggregates are maps by currency, not mixed totals.

Metrics: {input_tokens:number|null,cached_input_tokens:number|null,output_tokens:number|null,reasoning_output_tokens:number|null,total_tokens:number,cache_hit_rate:number|null,api_costs:{[currency]:number},subscription_costs:{[currency]:number},priced_tokens:number,pricing_coverage:number,call_count:number,summary_tokens:number,issues:number}. Null means unknown. total_tokens uses reported totals; any mismatch is flagged.

GET /api/overview → {metrics,today,week,month,daily:[{date,...metrics}],by_model:[{name,...metrics}],by_project:[{name,...metrics}]}. GET /api/tasks → {items:[{id,title,project,model,updated_at,parent_id,...metrics}],total}. GET /api/options → {projects:string[],models:string[]}.

GET /api/tasks/{id} accepts filters and include_children=true|false → {task:{id,title,project,parent_id,...metrics},turns:[{id,...metrics,calls:Call[]}],children:[{id,title}],issues:[{code,message,path,line}],reconciliation:object}. Call={id,task_id,turn_id,timestamp,model,kind:'call'|'summary',source_path,source_line,...metrics}. Summaries must be visibly distinguished from model calls. GET /api/analysis → {high_calls:Call[],daily,by_model,by_project,signals:[{task_id,turn_id,message,evidence:object}]}.

GET /api/settings → {source_root,prices:Price[],subscriptions:Subscription[],pricing_note}. PUT /api/settings {source_root}. PUT /api/prices Price={model,currency,effective_from,input_per_million,cached_input_per_million,output_per_million,cache_write_per_million?:number|null,source}; same model/date/currency upserts. DELETE /api/prices/{id}. PUT /api/subscriptions {month:'YYYY-MM',amount,currency}; same month replaces. DELETE /api/subscriptions/{month}. GET /api/status → {syncing,last_sync,last_error,files,events,calls,summary_tokens,issues:[...],source_root,database_path,progress:{done,total}}. POST /api/sync starts asynchronous scan. CSV scope=tasks|calls.

## Accounting decisions
Canonical request ID deduplicates across files; original owner wins over inherited copies. New records supply calls. Cumulative checkpoints establish non-overlapping coverage: positive unexplained differences are summary rows; negative/conflicting values are issues, never silently clamped into a complete account. Cumulative resets start explicit uncertain segments. Unknown timeframe/model remains unknown rather than attributed to final task metadata. Child totals are opt-in at task detail; global totals count every canonical call once.

API rates are per million tokens with cached input separated from uncached input. Cache-write pricing must be explicit if present. No alias matching. Seed only official verified prices; unsupported models remain unpriced. Subscription allocation uses each month's full local ledger regardless of current filters. Incomplete and unassigned usage retains allocation weight. Current month is provisional. CSV escapes formula-like strings.
