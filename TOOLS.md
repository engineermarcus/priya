**TOOL SET A (Implemented in Priya)**

# IMPLEMENTED: AGENTJOB

`agentjob` launches a non-blocking Gemini `gemini-3.5-flash-lite` coding
subagent. It has Priya-style `bash`, `Read`, and precise `Edit` tools, plus a
`narrate` tool that it uses for concise live milestones. The versioned runner
is `tools/job_runner.py`; transient job state and an append-only event journal
are stored in `.priya/jobs/` and are not tracked. Priya tails that journal
automatically, so the user sees narration and bash-like output while they can
continue chatting normally.

- `agentjob spawn "<task>" [workdir]` → `{job_id, workdir}`. Without a workdir,
  it works in Priya's starting directory.
- `agentjob status <job_id>` → `{status, reason, turn, workdir, ...}`.
- `agentjob log <job_id> [cursor]` → structured journal events after a cursor,
  with `next_cursor` for the next incremental poll. Live UI tailing is automatic.
- `agentjob send <job_id> "<message>"` → queues steering for the next turn.
- `agentjob stop <job_id>` → terminates the job process group and preserves its
  transcript/status files.

Transient agent/API failures retry automatically up to three times, with each
retry shown in the live journal. If the job remains failed, Priya takes over
with its local tools rather than abandoning the requested work.

Use `agentjob` as the default implementation path for any non-trivial front-end
change in this repository: pages, components, layout, styling, responsive
behavior, and browser interactions. Spawn even when the task is fully specified;
only tiny one-line UI fixes are small enough to do directly. Include concrete
requirements, relevant paths, constraints, and verification instructions. It is
non-blocking: spawn, continue independent inspection or conversation while the
live journal tails, then inspect the changed files and verify them before
reporting completion.
Use it for repository UI implementation; use the artifact tool for standalone
browser deliverables that do not belong in the source tree.

# IMPLEMENTED: BASH

`bash` runs a shell command in Priya's current working directory.

- `bash {command, timeout_s?}` → `{exit_code, stdout, stderr}`.
- `timeout_s` defaults to 60 seconds.
- `bash {command, background: true}` → starts a long-running process without
  waiting and returns its PID plus stdout/stderr log paths under
  `.priya/processes/`. Use this for servers and watchers; inspect its logs or
  process with a later `bash` call and stop it with its process-group PID when
  it is no longer needed.
- Stdout and stderr are streamed to the UI while the command runs, then the
  final result is returned (stdout is capped at 8,000 characters and stderr at
  4,000 characters).
- On timeout, the complete command process group is terminated; no background
  descendants are left running.

Its streaming, error-stream capture, timeout, and process-group cleanup were
manually verified.

# IMPLEMENTED: ARTIFACT

`artifact` turns a generated HTML result into a versioned interactive local web
page. Its repository-local implementation is `tools/artifact.py`; revisions,
server state, and logs are stored under `.priya/artifacts/` and ignored by Git.

- `artifact publish {name, title?, html}` → creates a new immutable HTML
  revision and returns its stable artifact URL.
- `artifact list`, `artifact history {name}`, and `artifact revert {name,
  version}` → inspect and restore revisions.
- `artifact start {port?}` / `artifact stop` → runs or stops a local server
  (default: `http://127.0.0.1:8765`). The stable artifact page polls once per
  second and refreshes its iframe in place after a publish or revert.
- `artifact share` → starts a Cloudflare quick tunnel only after explicit use
  of this action. It requires the separately installed `cloudflared` command.

Publish, live local serving, revision history, reversion, and server shutdown
were manually verified through the Live-tool adapter. The missing-cloudflared
error path was also verified; a real public tunnel still requires cloudflared
to be installed and tested on this machine.

# IMPLEMENTED: ASKUSERQUESTION

`askUserQuestion` pauses the current model tool round and presents one to four
structured decisions in the Priya TUI. Each question provides two to four
keyboard-navigable choices, and the normal input field accepts a custom answer.
After every question has an answer, Priya returns the ordered answers to the
same model call so it can continue with the user's decision rather than guess.

- `askUserQuestion {questions: [{header?, question, options: [{label, description}]}]}`
  → `{answers: [{question, answer}]}`.
- Use ↑/↓ and Enter to choose an option; press Tab to focus the input field and
  submit a free-text answer.
- Escape cancels the pending question along with the active response.

# IMPLEMENTED: CRONCREATE, CRONDELETE, CRONLIST

These tools schedule prompts only within the active Priya session. They are not
OS crontab entries, are discarded when Priya exits, and have a three-day safety
expiry. Cron expressions use Priya's local timezone and standard numeric
five-field syntax: `minute hour day-of-month month day-of-week`.

- `CronCreate {cron, prompt, recurring?}` → creates a job and returns its
  `job_id`, next run, expiry, and timezone. `recurring: false` runs at the next
  matching time only and then removes the job; it defaults to `true`.
- `CronList {}` → active jobs and their next scheduled local run.
- `CronDelete {job_id}` → removes an active job.
- Due prompts wait until Priya is between model turns, then appear as a
  scheduled turn and execute without interrupting an active response.

# IMPLEMENTED: READ, EDIT

`Read` and `Edit` provide a reviewable path for precise changes to existing
UTF-8 text files. A file must be read through `Read` and remain unchanged
before `Edit` can propose a replacement. Priya shows a unified diff and waits
for the user's approval; cancelling leaves the file untouched.

- `Read {path}` → file content and canonical path.
- `Edit {path, old_string, new_string, replace_all?}` → replaces one exact
  unique match, or every match only when `replace_all: true` is specified.
- Edits reject missing or ambiguous old text, stale reads, and changes made
  while approval is pending.

# IMPLEMENTED: GLOB

`Glob` discovers files from the live filesystem in Priya's current working
directory. It returns canonical paths for files only, without reading or
modifying their contents. Relative patterns honor the active WorkTree scope
and it remains available in Plan Mode.

- `Glob {patterns, max_results?}` → `{files, count, truncated, workdir}`.
- `**` crosses any number of directory levels; `*` stays within one level;
  `?` matches one character; `{js,ts}` matches either alternative.
- Prefix a pattern with `!` to exclude it. Include at least one positive
  pattern, for example `['src/**/*.{js,ts}', '!**/node_modules/**']`.
- Results are sorted, default to at most 1,000 returned files, and can be
  capped with `max_results` (up to 10,000).

# IMPLEMENTED: GREP

`Grep` searches live file contents with ripgrep-compatible regular expressions.
It is a read-only tool for debugging reported failures and checking that an
implementation was applied consistently. It is available in Plan Mode and
honors the active WorkTree scope.

- `Grep {pattern, path?, case_sensitive?, max_results?}` →
  `{matches: [{path, line_number, line}], count, truncated, scope}`.
- `path` limits the search to a file or directory inside the current working
  directory; it defaults to the whole current scope.
- Matching is case-sensitive by default. Set `case_sensitive: false` for a
  case-insensitive search. Results are live and capped at 1,000 by default,
  configurable up to 10,000.
- Searches respect ripgrep's normal ignore rules, such as `.gitignore`.

# IMPLEMENTED: LSP

`LSP` owns a persistent Language Server Protocol process per active workspace
and language, synchronizing source content before every semantic query.

- `LSP {action: 'definition'|'references'|'hover', path, line, character?}`
  resolves a symbol at one-based `line` and zero-based `character`.
- `LSP {action: 'document_symbols'|'diagnostics', path}` returns a semantic
  outline or language-server diagnostics. `workspace_symbols` additionally
  accepts `query` and a source `path` to choose the language server.
- `LSP {action: 'status'}` reports running servers. Python, JavaScript/
  TypeScript, Go, and Rust are auto-detected; if the needed executable is not
  installed, the tool returns its concrete name instead of falling back to text
  matching.

# IMPLEMENTED: LISTMCPRESOURCESTOOL

`ListMcpResourcesTool` discovers the live resource catalog of every configured
stdio MCP server. MCP definitions come from `PRIYA_MCP_SERVERS` (a JSON object)
or `.priya/mcp_servers.json` in the active workspace. Each server definition
uses `command`, optional `args`, optional `cwd`, and optional string `env`.

- `ListMcpResourcesTool {}` → `{servers, resources, resource_templates, count,
  template_count}`. Every resource/template is annotated with its `server`,
  alongside server-supplied URI, name, MIME type, description, and metadata.
- Connections are initialized once and persist for the active workspace.
  Listings paginate dynamically, so each call reflects current server state.
- An absent configuration returns an empty catalog. One failed server is
  reported in `servers` without hiding resources from healthy servers.

# IMPLEMENTED: PLAN MODE AND WORKTREE SCOPE

These are session-state controls. They do not edit files directly; they change
what later tools are allowed to do and where they operate.

- `EnterPlanMode {}` → enables read-only planning. `Read`, `CronList`, and the
  state controls still work, but `bash`, `Edit`, `agentjob`, `artifact`, and
  Cron mutations are blocked until Plan Mode exits.
- `ExitPlanMode {}` → returns Priya to normal executable mode.
- `EnterWorkTree {path}` → scopes relative `Read`, `Edit`, `bash`, and new
  `agentjob spawn` calls to an existing git worktree directory. Switching scope
  clears prior Read approvals so edits cannot reuse stale reads from another
  tree.
- `ExitWorkTree {}` → returns Priya to the directory from which it was started and
  clears prior Read approvals again.

# IMPLEMENTED: WRITE

`Write` creates a new file or completely replaces the contents of an existing file.
It creates any required parent directories automatically and computes the file digest
so subsequent `Edit` calls in the session can target it without requiring an explicit `Read`.

- `Write {path, content}` → `{path, bytes_written, lines_written, success}`.
- Blocked in Plan Mode.

# IMPLEMENTED: WEBSEARCH

`WebSearch` performs live DuckDuckGo web searches and returns structured search results
including titles, clean target URLs, and snippets.

- `WebSearch {query, max_results?}` → `{query, results: [{title, url, snippet}], count}`.
- `max_results` defaults to 8. Available in Plan Mode.

# IMPLEMENTED: WEBFETCH

`WebFetch` downloads and parses public web pages, stripping scripts, styles, navigation,
and headers/footers to return clean, readable markdown/text content.

- `WebFetch {url, max_length?}` → `{url, title, status_code, content, truncated, length}`.
- `max_length` defaults to 16,000 characters. Available in Plan Mode.

# IMPLEMENTED: TASKCREATE, TASKLIST, TASKGET, TASKUPDATE, TASKSTOP

Session-scoped task tracking and structured planning for complex multi-turn workflows.
Tasks help maintain clarity and progress across steps.

- `TaskCreate {subject, description?}` → creates task with status `"pending"`.
- `TaskList {}` → returns all session tasks and their statuses.
- `TaskGet {task_id}` → inspects a specific task.
- `TaskUpdate {task_id, status?, subject?, description?}` → updates task status (`pending`, `in_progress`, `completed`, `cancelled`).
- `TaskStop {task_id}` → sets status to `"cancelled"`.
- Available in Plan Mode.

# IMPLEMENTED: READMCPRESOURCETOOL

`ReadMcpResourceTool` fetches resource contents from configured MCP servers by URI.

- `ReadMcpResourceTool {uri, server?}` → `{server, uri, contents: [...]}`.
- Available in Plan Mode.

# IMPLEMENTED: MONITOR

`Monitor` inspects active or background processes started by `bash {background: true}` or checks arbitrary OS PIDs.
It reports running status, exit codes, and recent stdout/stderr log output.

- `Monitor {process_id?, pid?, action?, lines?}` → `{process_id, pid, running, stdout_tail, stderr_tail, command}`.
- `action`: `"status"`, `"logs"`, or `"kill"`.
- `lines` defaults to 20.
- Available in Plan Mode for inspection; kill action blocked in Plan Mode.

# IMPLEMENTED: PUSHNOTIFICATION

`PushNotification` sends a desktop notification or terminal alert to the user.

- `PushNotification {title, message, urgency?}` → `{delivered, method, title, message}`.
- Supports `notify-send` on Linux desktops and falls back to terminal OSC 9 / bell alerts.
- Available in Plan Mode.

# IMPLEMENTED: REMOTETRIGGER

`RemoteTrigger` fires an HTTP request or webhook to external services, APIs, or local dev reload endpoints.

- `RemoteTrigger {url, method?, headers?, data?, timeout_s?}` → `{url, method, status_code, body, elapsed_s}`.
- `method` defaults to `"POST"`.

# IMPLEMENTED: REPORTFINDINGS

`ReportFindings` generates structured audit, testing, or research reports and writes them into `.priya/reports/`.

- `ReportFindings {title, summary, findings: [{title, severity, description, file?, line?}], recommendations?}` → `{report_id, path, findings_count, title}`.
- Formats markdown summary tables and severity breakdowns.
- Available in Plan Mode.

# IMPLEMENTED: SCHEDULEWAKEUP

`ScheduleWakeup` schedules a one-shot delayed wakeup prompt or reminder that fires after a specified delay in seconds.

- `ScheduleWakeup {delay_seconds, prompt}` → `{wakeup_id, delay_seconds, fire_time, prompt}`.
- Asynchronously sleeps and submits the prompt to Priya's prompt queue upon timer expiry.

# IMPLEMENTED: SENDMESSAGE

`SendMessage` sends a message or steering instruction to a background subagent (`agentjob`) or presents an announcement to the user.

- `SendMessage {target: 'agentjob'|'user', message, target_id?}` → `{delivered, target, message}`.
- Available in Plan Mode for user messages.

# IMPLEMENTED: SENDUSERFILE

`SendUserFile` stages and exports a file from the workspace into `.priya/exports/` for the user to open, download, or review.

- `SendUserFile {path, description?}` → `{path, export_path, filename, size_bytes, description}`.
- Available in Plan Mode.

# IMPLEMENTED: SHAREONBOARDINGGUIDE

`ShareOnboardingGuide` analyzes the repository structure, config files, and build scripts to generate a beginner-friendly `ONBOARDING.md` guide.

- `ShareOnboardingGuide {target_path?, save_to_file?}` → `{path, saved, detected_stack, guide}`.
- Automatically discovers Node.js, Python, Rust, Go, Make, Docker, and other stacks.

# IMPLEMENTED: SKILL

`Skill` discovers, inspects, and executes custom project automation routines from `.priya/skills/`.

- `Skill {action: 'list'|'get'|'run', skill_name?, args?}` → `{skills: [...]}` or execution output.
- Execution blocked in Plan Mode.

# IMPLEMENTED: TASKOUTPUT

`TaskOutput` records and attaches deliverables, completion notes, and generated artifact file paths to a session task.

- `TaskOutput {task_id, output, artifacts?}` → `{task_id, task}`.
- Automatically transitions tasks from `pending`/`in_progress` to `completed`.
- Available in Plan Mode.

# IMPLEMENTED: TODOWRITE

`TodoWrite` manages and persists a markdown task checklist (`TODO.md`) in the workspace.

- `TodoWrite {todos: [{task, done}], path?, merge?}` → `{path, total, completed, pending}`.
- Supports merging with existing checklist items.

# IMPLEMENTED: TOOLSEARCH

`ToolSearch` enables semantic and keyword discovery across all registered tools, descriptions, and parameters.

- `ToolSearch {query}` → `{query, count, matches: [{name, description, parameters, score}]}`.
- Available in Plan Mode.

# IMPLEMENTED: WAITFORMCPSERVERS

`WaitForMcpServers` waits for background Model Context Protocol (MCP) servers to complete initialization.

- `WaitForMcpServers {timeout_s?}` → `{ready, servers, elapsed_s}`.
- Available in Plan Mode.

# IMPLEMENTED: WORKFLOW

`Workflow` executes a sequence of automated tool steps in order, halting on error and returning structured step results.

- `Workflow {action: 'run'|'status'|'list', steps?, workflow_id?}` → `{workflow_id, status, total_steps, completed_steps, step_results}`.



**TOOL SET B (Browser Automation - Candidate)**

- `browser_subagent`: Autonomous browser task controller & dispatcher
- `browser_navigate`: Navigate active page to URL
- `read_browser_page`: Extract rendered markdown/text from live browser DOM
- `browser_click_element`: Click DOM element by CSS selector or XPath
- `browser_select_option`: Select option in dropdown element
- `browser_input`: Type text into targeted input/textarea
- `browser_press_key`: Send keyboard key events (Enter, Escape, Tab, Backspace)
- `browser_scroll`: Scroll viewport or element by direction (`up`, `down`, `top`, `bottom`) and amount
- `browser_resize_window`: Set browser viewport dimensions
- `capture_browser_screenshot`: Capture viewport or full-page PNG screenshot
- `execute_browser_javascript`: Execute JavaScript snippet within page context
- `list_browser_pages`: List active browser tabs/contexts
- `browser_get_dom`: Retrieve accessibility tree or structured DOM hierarchy
- `browser_move_mouse`: Move mouse cursor to coordinate
- `click_browser_pixel`: Click at exact (x, y) screen/viewport coordinates
- `browser_drag_pixel_to_pixel`: Drag from start coordinate to end coordinate
- `capture_browser_console_logs`: Stream and fetch browser console logs/errors

---

**TOOL SET C (Advanced System, Multimodal & Swarms - Candidate)**

*(Tools from Set A like `bash`, `Read`, `Glob`, `Grep`, `WebSearch`, `ToolSearch`, and task/plan updates are excluded to eliminate redundancy.)*

**Git & Patching (2)**
- `git`: Structured Git repository operations (status, diff, commit, branch, checkout, log)
- `apply_patch`: Apply unified diff / multi-file patch directly to workspace

**Multimodal & Images (2)**
- `view_image`: Inspect image metadata, dimensions, and encode for multimodal vision models
- `image_gen`: Text-to-image synthesis and generation via external image model API

**Multi-Agent Swarm & Orchestration (6 - IMPLEMENTED in Priya)**
- `spawn_agent`: Launch independent background worker agent with role, prompt, and isolated context
- `send_input`: Send steering message or payload to active background agent
- `wait_agent`: Wait for agent completion or milestone event
- `close_agent`: Stop and clean up an active agent instance
- `resume_agent`: Resume paused or interrupted agent
- `spawn_agents_on_csv`: Batch spawn parallel worker agents across CSV dataset rows

**Parallel Execution (1)**
- `multi_tool_use.parallel`: Execute multiple independent tool calls simultaneously in parallel

