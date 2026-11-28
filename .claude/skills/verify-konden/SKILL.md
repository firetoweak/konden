---
name: verify-konden
description: "Drive konden Web the way a user does — workspace, Agent roster and chat, colleague tools, private messages, landing and sync, group chat, file and command execution, and Bot renaming — and capture proof. Use when changing Web, Bot names, model settings, or when a change needs a real-app check before it ships."
---

# Verify konden

Drive the Web app a person actually uses. `web_chat.py` serves it against an empty home; that path is part of the map. Chat and tool calls also need a configured model. Web sessions are persistent Bots under `$KONDEN_HOME/state/bots/`; a linked code project needs a Git repository with a first commit and lives under `projects/<name>/`. Integration worktrees live under `$KONDEN_HOME/state/coding_bot/`. Subagents share their parent's files and can run commands — verify shared execution and explicit reclamation.

## Acceptance basis

Read `features/README.md` and the feature's **需求依据** before choosing assertions. Project `AGENTS.md` binds, including explicitly registered exceptions. Product positioning defines the user value; architecture rules and runtime semantics define required behavior. If the implementation contradicts an explicit requirement, keep the requirement and report a product gap — never rewrite the expected result to match the code. Conflicting current documents stay an unresolved basis conflict; history and future directions are not acceptance requirements.

A path exercised is feature coverage, not proof that every requirement passed. Record version, requirement/scenario, evidence, result and unverified scope.

## Launch

```sh
python .claude/skills/verify-konden/run.py start
```

One run owns the directory, port and process it reports; never reuse them, and never start on `~/.konden`. It prints JSON: `url`, `run_dir`, `evidence_dir`, `home`, `workspace`, `pid`, `ok`, and Doctor's report. `ok: true` is the only readiness signal — that is `doctor.py` exiting 0, not the uvicorn log line. If the log stops first, read `log` and do not drive the port.

`live_model_ready: false` means no usable provider for the default model. Skip chat send, the 测试 button, and coding tool rounds; say so in the note and do not type anyway. `start --require-model` makes that a blocker instead.

Rerun the same check with `run.py status --run-dir <dir>`. A non-zero exit means do not click: a wrong home, a dead pid, or a page that is not konden is a different instance.

Missing `konden-env` or `web/dist`? Run `scripts/setup.sh` (`scripts\setup.ps1` on Windows) once; if only the bundle is stale, `( cd web && npm ci && npm run build )` is enough.

## Drive

UI copy is Chinese; match accessible names exactly. Viewport at least 1280×800 — below the `sm` breakpoint the sidebar hides behind `打开 Agent 名册`.

Use the session's browser automation; this repository ships no browser binary. Resolve every target by role and accessible name, never by coordinates or DOM order. After switching Bots or resizing, take a fresh snapshot and confirm the conversation and model selector before filling the composer. Wait for uploads and model saves to settle: a server-saved switch needs one click at its current value, then the confirmed value. Scope repeated names such as `保存记忆` to its drawer.

The conversation timeline is a virtual list and dialogs are transitions; both need `requestAnimationFrame` and `ResizeObserver`. A hidden browser pane never fires them — no turns render and `新建工作区`, `外观`, `新建群聊` never open. Probe before trusting an empty page; if the pane cannot stay visible, record those entries `unreachable` with that prerequisite, not as product failures.

Start from `features/README.md`, then the feature file, and drive the entry point it names — colleague tools, private messages, landing and sync, group chat, inbox, routines. Pending items highlight the Bot in the sidebar; there is no standalone inbox page. Wait for the sidebar indicator to reach role `status` / name `已连接`; `正在连接…` means not ready, so rerun Doctor instead of typing.

`curl` is only read-only confirmation written beside an action; posting to the API does not prove the page. A green `pytest`, `-m process`, or live-model run is not Web proof.

## Evidence

`run.py evidence --run-dir <dir> --feature <id>` creates the directory; cleanup never deletes it. Per driven entry point save `snapshot.txt` (accessibility snapshot with the resulting state on screen), `page.png` (that same state, `新建 Bot` visible on desktop, `打开 Agent 名册` on mobile), `state.json` (the read-only HTTP body the feature file names), and `note.txt` (feature id, entry point, `passed` or `skipped` plus the Doctor field that blocked it).

A skipped live-model step is neither a pass nor a pass via pytest. Name the result: sidebar control plus `GET /api/workspaces`; transcript plus `GET /api/sessions/<id>`; for a coding round, the files in the Agent's worktree.

## Stop

```sh
python .claude/skills/verify-konden/run.py stop --run-dir <dir>
```

Stops only the recorded process — session workers sit in its kill-on-close job — and deletes the run directory. Evidence survives; reopen the screenshot and `state.json` before claiming anything.

## Maintenance

When labels, routes or flows change, run `/maintain-verification-skill` and correct this map from the page you actually drove.
