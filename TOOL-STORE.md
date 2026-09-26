# Priya Tool Store (`TOOL-STORE.md`)

> **Notice for Priya / Model**: This document is the canonical tool store and capability registry for Priya. Upon initial session startup, inspect and reference this registry to load all available tools and execution policies into memory. Whenever a new tool is introduced, registered, or updated, this file must be maintained and kept synchronized with `live_cli.py`.

---

## Tool Overview & Category Index

| Category | Tools | Count |
| :--- | :--- | :--- |
| **1. File System & Code Exploration** | `Read`, `Write`, `Edit`, `Glob`, `Grep`, `LSP` | 6 |
| **2. Shell Execution & Process Management** | `bash`, `Monitor` | 2 |
| **3. Web & Internet Intelligence** | `WebSearch`, `WebFetch`, `RemoteTrigger` | 3 |
| **4. Task Planning & Workflow Orchestration** | `TaskCreate`, `TaskList`, `TaskGet`, `TaskUpdate`, `TaskStop`, `TaskOutput`, `TodoWrite`, `Workflow` | 8 |
| **5. Interactive User Guidance & Decisions** | `askUserQuestion`, `PushNotification`, `SendMessage`, `SendUserFile`, `ShareOnboardingGuide` | 5 |
| **6. Autonomous Subagents & Artifacts** | `agentjob`, `artifact` | 2 |
| **7. Scheduling & Reminders** | `CronCreate`, `CronList`, `CronDelete`, `ScheduleWakeup` | 4 |
| **8. Project Extensibility & Discovery** | `Skill`, `ToolSearch`, `ListMcpResourcesTool`, `ReadMcpResourceTool`, `WaitForMcpServers` | 5 |
| **9. Session & Workspace Controls** | `EnterPlanMode`, `ExitPlanMode`, `EnterWorkTree`, `ExitWorkTree` | 4 |
| **10. Multi-Agent Swarms & Orchestration** | `spawn_agent`, `send_input`, `wait_agent`, `close_agent`, `resume_agent`, `spawn_agents_on_csv` | 6 |
| **Total Registered Tools** | | **46** |

---

## 1. File System & Code Exploration

### `Read`
- **Description**: Reads the exact textual contents of an existing UTF-8 file. Essential prior to invoking `Edit`.
- **Parameters**:
  - `path` *(string, required)*: Path to the target file.
- **Output**: `{path, content, lines, size_bytes}`
- **Plan Mode**: Allowed.

### `Write`
- **Description**: Creates a new file or completely overwrites an existing file with full text content. Automatically creates any missing parent directories.
- **Parameters**:
  - `path` *(string, required)*: Path to the file to create or overwrite.
  - `content` *(string, required)*: Complete text content.
- **Output**: `{path, bytes_written, lines_written, success}`
- **Plan Mode**: Blocked.

### `Edit`
- **Description**: Performs a surgical search-and-replace on an existing file. In interactive mode, generates a unified diff for user approval.
- **Parameters**:
  - `path` *(string, required)*: Path previously read with `Read`.
  - `old_string` *(string, required)*: Exact unique substring to find and replace.
  - `new_string` *(string, required)*: Replacement string.
  - `replace_all` *(boolean, optional, default: false)*: If true, replaces all occurrences.
- **Output**: `{path, replacements, status}`
- **Plan Mode**: Blocked.

### `Glob`
- **Description**: Fast filesystem discovery returning matching file paths without reading file contents.
- **Parameters**:
  - `patterns` *(array of strings, required)*: Glob patterns (e.g. `['**/*.py', '!**/venv/**']`). Supports `**`, `*`, `?`, and `!exclude`.
  - `max_results` *(integer, optional, default: 1000, max: 10000)*: Limit returned paths.
- **Output**: `{files, count, truncated, workdir}`
- **Plan Mode**: Allowed.

### `Grep`
- **Description**: Fast content searching using regex patterns (ripgrep-compatible) respecting ignore rules.
- **Parameters**:
  - `pattern` *(string, required)*: Regular expression pattern to search.
  - `path` *(string, optional)*: Specific file or subdirectory to scope search.
  - `case_sensitive` *(boolean, optional, default: true)*: Case sensitivity flag.
  - `max_results` *(integer, optional, default: 1000, max: 10000)*: Max matches.
- **Output**: `{matches: [{path, line_number, line}], count, truncated, scope}`
- **Plan Mode**: Allowed.

### `LSP`
- **Description**: Semantic code intelligence via language servers (definitions, references, hover docstrings, outline symbols, and compiler/linter diagnostics).
- **Parameters**:
  - `action` *(string, required)*: One of `'definition'`, `'references'`, `'hover'`, `'document_symbols'`, `'workspace_symbols'`, `'diagnostics'`, `'status'`.
  - `path` *(string, required)*: Target source file.
  - `line` *(integer, optional)*: 1-based line number.
  - `character` *(integer, optional)*: 0-based character offset.
  - `query` *(string, optional)*: Query string for workspace symbol searches.
- **Output**: Detailed semantic payload matching requested action.
- **Plan Mode**: Allowed.

---

## 2. Shell Execution & Process Management

### `bash`
- **Description**: Executes command-line programs in Priya's active working directory with real-time output streaming.
- **Parameters**:
  - `command` *(string, required)*: Shell command line to execute.
  - `timeout_s` *(integer, optional, default: 60)*: Execution timeout in seconds.
  - `background` *(boolean, optional, default: false)*: When true, starts process in background and returns PID and log paths.
- **Output**: `{exit_code, stdout, stderr, process_id?, pid?}`
- **Plan Mode**: Blocked (read-only analysis only).

### `Monitor`
- **Description**: Inspects, tails logs from, or terminates active background processes spawned via `bash {background: true}` or arbitrary OS PIDs.
- **Parameters**:
  - `process_id` *(string, optional)*: Background process identifier.
  - `pid` *(integer, optional)*: Operating system process ID.
  - `action` *(string, optional, default: 'status')*: `'status'`, `'logs'`, or `'kill'`.
  - `lines` *(integer, optional, default: 20)*: Tail log lines to return.
- **Output**: `{process_id, pid, running, stdout_tail, stderr_tail, command}`
- **Plan Mode**: Allowed for inspection; `kill` action blocked.

---

## 3. Web & Internet Intelligence

### `WebSearch`
- **Description**: Live internet web search via DuckDuckGo, returning titles, snippets, and clean target URLs.
- **Parameters**:
  - `query` *(string, required)*: Search query keywords.
  - `max_results` *(integer, optional, default: 8)*: Maximum results to return.
- **Output**: `{query, results: [{title, url, snippet}], count}`
- **Plan Mode**: Allowed.

### `WebFetch`
- **Description**: Downloads and extracts clean readable text/markdown from public web URLs, stripping clutter, scripts, and navigation.
- **Parameters**:
  - `url` *(string, required)*: Target URL to fetch.
  - `max_length` *(integer, optional, default: 16000)*: Max character length.
- **Output**: `{url, title, status_code, content, truncated, length}`
- **Plan Mode**: Allowed.

### `RemoteTrigger`
- **Description**: Fires an HTTP webhook or API request to remote services, local development servers, or reload endpoints.
- **Parameters**:
  - `url` *(string, required)*: Target endpoint URL.
  - `method` *(string, optional, default: 'POST')*: HTTP method (`GET`, `POST`, `PUT`, `DELETE`).
  - `headers` *(object, optional)*: Key-value HTTP request headers.
  - `data` *(string or object, optional)*: Request body payload.
  - `timeout_s` *(integer, optional, default: 15)*: Timeout in seconds.
- **Output**: `{url, method, status_code, body, elapsed_s}`
- **Plan Mode**: Blocked.

---

## 4. Task Planning & Workflow Orchestration

### `TaskCreate`
- **Description**: Creates a structured task in the session task list with initial status `pending`.
- **Parameters**:
  - `subject` *(string, required)*: Clear title or goal of the task.
  - `description` *(string, optional)*: Detailed task requirements and context.
- **Output**: `{task_id, subject, status: 'pending'}`
- **Plan Mode**: Allowed.

### `TaskList`
- **Description**: Retrieves all session tasks, their identifiers, subjects, and current progress statuses.
- **Parameters**: None.
- **Output**: `{tasks: [{task_id, subject, status, description, created_at, updated_at}]}`
- **Plan Mode**: Allowed.

### `TaskGet`
- **Description**: Retrieves full details and history for a specific session task.
- **Parameters**:
  - `task_id` *(string, required)*: Task identifier.
- **Output**: `{task_id, subject, status, description, outputs, artifacts}`
- **Plan Mode**: Allowed.

### `TaskUpdate`
- **Description**: Updates progress, status, or details of a session task.
- **Parameters**:
  - `task_id` *(string, required)*: Task identifier.
  - `status` *(string, optional)*: `'pending'`, `'in_progress'`, `'completed'`, `'cancelled'`.
  - `subject` *(string, optional)*: Updated subject.
  - `description` *(string, optional)*: Updated description.
- **Output**: `{task_id, updated_fields, task}`
- **Plan Mode**: Allowed.

### `TaskStop`
- **Description**: Cancels or stops an active session task, setting its status to `'cancelled'`.
- **Parameters**:
  - `task_id` *(string, required)*: Task identifier.
- **Output**: `{task_id, status: 'cancelled'}`
- **Plan Mode**: Allowed.

### `TaskOutput`
- **Description**: Attaches completion notes, output summaries, and generated artifact paths to a task, marking it completed.
- **Parameters**:
  - `task_id` *(string, required)*: Task identifier.
  - `output` *(string, required)*: Completion note or deliverable summary.
  - `artifacts` *(array of strings, optional)*: File paths to created deliverables.
- **Output**: `{task_id, task}`
- **Plan Mode**: Allowed.

### `TodoWrite`
- **Description**: Manages and persists a markdown task checklist file (`TODO.md`) in the workspace.
- **Parameters**:
  - `todos` *(array of objects, required)*: `[{task: string, done: boolean}]`.
  - `path` *(string, optional, default: 'TODO.md')*: Target markdown file path.
  - `merge` *(boolean, optional, default: true)*: Merge with existing checklist items.
- **Output**: `{path, total, completed, pending}`
- **Plan Mode**: Blocked.

### `Workflow`
- **Description**: Executes a sequential pipeline of predefined tool actions, halting upon failure and collecting step outputs.
- **Parameters**:
  - `action` *(string, required)*: `'run'`, `'status'`, or `'list'`.
  - `steps` *(array of objects, optional)*: `[{name: string, tool: string, parameters: object}]`.
  - `workflow_id` *(string, optional)*: Workflow ID for status queries.
- **Output**: `{workflow_id, status, total_steps, completed_steps, step_results}`
- **Plan Mode**: Blocked for state-modifying workflows.

---

## 5. Interactive User Guidance & Decisions

### `askUserQuestion`
- **Description**: Pauses generation to prompt the user with 1–4 structured questions with interactive selectable options. Custom user input is supported via an automatic "Other" option.
- **Parameters**:
  - `questions` *(array of objects, required)*: `[{header?: string, question: string, options: [{label: string, description?: string}]}]`.
- **Output**: `{answers: [{question: string, answer: string}]}`
- **Plan Mode**: Allowed.

### `PushNotification`
- **Description**: Sends a desktop alert (`notify-send` on Linux) or terminal notification to alert the user of important events.
- **Parameters**:
  - `title` *(string, required)*: Alert title.
  - `message` *(string, required)*: Alert message text.
  - `urgency` *(string, optional, default: 'normal')*: `'low'`, `'normal'`, or `'critical'`.
- **Output**: `{delivered, method, title, message}`
- **Plan Mode**: Allowed.

### `SendMessage`
- **Description**: Sends a steering instruction to an active background `agentjob` subagent, or displays a priority announcement to the user.
- **Parameters**:
  - `target` *(string, required)*: `'agentjob'` or `'user'`.
  - `message` *(string, required)*: Message content.
  - `target_id` *(string, optional)*: Subagent job ID when `target='agentjob'`.
- **Output**: `{delivered, target, message}`
- **Plan Mode**: Allowed for user announcements.

### `SendUserFile`
- **Description**: Stages and exports a workspace file into `.priya/exports/` for easy user access, download, or review.
- **Parameters**:
  - `path` *(string, required)*: Workspace file path to export.
  - `description` *(string, optional)*: Explanation of the exported file.
- **Output**: `{path, export_path, filename, size_bytes, description}`
- **Plan Mode**: Allowed.

### `ShareOnboardingGuide`
- **Description**: Analyzes repository structure, configurations, and build scripts to generate a beginner-friendly `ONBOARDING.md` developer guide.
- **Parameters**:
  - `target_path` *(string, optional)*: Directory to analyze (defaults to workspace).
  - `save_to_file` *(boolean, optional, default: true)*: Whether to write `ONBOARDING.md`.
- **Output**: `{path, saved, detected_stack, guide}`
- **Plan Mode**: Allowed (reads only; file writing skipped in Plan Mode if requested).

---

## 6. Autonomous Subagents & Artifacts

### `agentjob`
- **Description**: Launches and steers an autonomous, non-blocking front-end coding subagent running in the background. Streams milestones via a live journal.
- **Parameters**:
  - `action` *(string, required)*: `'spawn'`, `'status'`, `'log'`, `'send'`, or `'stop'`.
  - `task` *(string, optional)*: Task instructions when spawning.
  - `workdir` *(string, optional)*: Isolated working directory.
  - `job_id` *(string, optional)*: Target job identifier.
  - `message` *(string, optional)*: Steering message for `'send'`.
  - `cursor` *(string, optional)*: Event log cursor for incremental polling.
- **Output**: Job metadata, status, or journal events.
- **Plan Mode**: Blocked.

### `artifact`
- **Description**: Manages versioned, interactive local HTML previews with auto-reloading and optional public tunnel sharing.
- **Parameters**:
  - `action` *(string, required)*: `'publish'`, `'list'`, `'history'`, `'revert'`, `'start'`, `'stop'`, or `'share'`.
  - `name` *(string, optional)*: Stable lowercase slug identifier.
  - `title` *(string, optional)*: Document title.
  - `html` *(string, optional)*: Standalone HTML page content.
  - `port` *(integer, optional, default: 8765)*: Local HTTP port.
  - `version` *(integer, optional)*: Target revision for revert.
- **Output**: Artifact URL, revision details, or server status.
- **Plan Mode**: Blocked for server control and mutations.

---

## 7. Scheduling & Reminders

### `CronCreate`
- **Description**: Schedules a recurring or one-shot prompt within the active Priya session using 5-field cron syntax.
- **Parameters**:
  - `cron` *(string, required)*: Five-field cron expression (`minute hour dom month dow`).
  - `prompt` *(string, required)*: Prompt text to execute when scheduled.
  - `recurring` *(boolean, optional, default: true)*: When false, runs once and auto-deletes.
- **Output**: `{job_id, cron, prompt, next_run, expiry}`
- **Plan Mode**: Blocked.

### `CronList`
- **Description**: Lists all active in-session scheduled cron jobs and their upcoming fire times.
- **Parameters**: None.
- **Output**: `{jobs: [{job_id, cron, prompt, recurring, next_run}]}`
- **Plan Mode**: Allowed.

### `CronDelete`
- **Description**: Cancels and removes an active scheduled cron job.
- **Parameters**:
  - `job_id` *(string, required)*: Job identifier to delete.
- **Output**: `{job_id, deleted: true}`
- **Plan Mode**: Blocked.

### `ScheduleWakeup`
- **Description**: Sets a one-shot countdown timer in seconds that automatically wakes up Priya and submits a reminder prompt.
- **Parameters**:
  - `delay_seconds` *(integer, required)*: Delay duration in seconds.
  - `prompt` *(string, required)*: Prompt to trigger upon timer expiry.
- **Output**: `{wakeup_id, delay_seconds, fire_time, prompt}`
- **Plan Mode**: Allowed.

---

## 8. Project Extensibility & Tool Discovery

### `Skill`
- **Description**: Discovers, inspects, and executes project automation routines and custom workflows stored under `.priya/skills/`.
- **Parameters**:
  - `action` *(string, required)*: `'list'`, `'get'`, or `'run'`.
  - `skill_name` *(string, optional)*: Target skill name.
  - `args` *(string or object, optional)*: Execution arguments.
- **Output**: `{skills: [...]}` or execution stdout/stderr.
- **Plan Mode**: Allowed for `'list'` and `'get'`; `'run'` blocked.

### `ToolSearch`
- **Description**: Semantically searches all registered Priya tools, descriptions, parameter schemas, and capabilities.
- **Parameters**:
  - `query` *(string, required)*: Natural language keyword or capability query.
- **Output**: `{query, count, matches: [{name, description, parameters, score}]}`
- **Plan Mode**: Allowed.

### `ListMcpResourcesTool`
- **Description**: Discovers and catalogs resources exposed by configured Model Context Protocol (MCP) servers.
- **Parameters**: None.
- **Output**: `{servers, resources, count}`
- **Plan Mode**: Allowed.

### `ReadMcpResourceTool`
- **Description**: Retrieves text or binary content from a specific MCP server resource URI.
- **Parameters**:
  - `uri` *(string, required)*: Resource URI string.
  - `server` *(string, optional)*: Server identifier.
- **Output**: `{server, uri, contents: [...]}`
- **Plan Mode**: Allowed.

### `WaitForMcpServers`
- **Description**: Blocks until background MCP servers complete their initialization handshakes.
- **Parameters**:
  - `timeout_s` *(integer, optional, default: 10)*: Maximum wait time in seconds.
- **Output**: `{ready, servers, elapsed_s}`
- **Plan Mode**: Allowed.

---

## 9. Session & Workspace Controls

### `EnterPlanMode`
- **Description**: Switches Priya into read-only planning mode, disallowing write, bash execution, or destructive actions while allowing analytical tools.
- **Parameters**: None.
- **Output**: `{plan_mode: true}`

### `ExitPlanMode`
- **Description**: Exits read-only planning mode and restores standard execution capability.
- **Parameters**: None.
- **Output**: `{plan_mode: false}`

### `EnterWorkTree`
- **Description**: Scopes relative operations (`Read`, `Edit`, `bash`, `agentjob`) to an existing git worktree directory.
- **Parameters**:
  - `path` *(string, required)*: Target worktree path.
- **Output**: `{worktree: path}`

### `ExitWorkTree`
- **Description**: Reverts Priya's working scope back to the initial workspace directory.
- **Parameters**: None.
- **Output**: `{workdir: path}`

---

## 10. Multi-Agent Swarms & Orchestration

### `spawn_agent`
- **Description**: Launches an autonomous background worker subagent with a designated role, instruction prompt, and isolated context. Returns immediately with an agent ID for asynchronous coordination.
- **Parameters**:
  - `role` *(string, required)*: Specialized role of the worker (e.g., `researcher`, `tester`, `reviewer`, `analyst`).
  - `prompt` *(string, required)*: Detailed instructions and execution criteria for the agent.
  - `workdir` *(string, optional)*: Working directory scope for the agent (defaults to current directory).
  - `model` *(string, optional)*: Model override for the agent (e.g. `gemini-3.8-flash` or `mistral-medium-latest`).
- **Output**: `{agent_id, role, prompt, workdir, model, status: 'running'}`
- **Plan Mode**: Blocked.

### `send_input`
- **Description**: Sends steering instructions, real-time guidance, feedback, or input payloads to an active running agent in the swarm.
- **Parameters**:
  - `agent_id` *(string, required)*: The target agent ID returned by `spawn_agent`.
  - `message` *(string, required)*: The steering message or input payload.
- **Output**: `{agent_id, delivered: true, status, message}`
- **Plan Mode**: Blocked.

### `wait_agent`
- **Description**: Waits for a background agent to complete execution, fail, or reach a timeout threshold, retrieving its latest results and logs.
- **Parameters**:
  - `agent_id` *(string, required)*: The target agent ID to wait for.
  - `timeout_s` *(integer, optional)*: Maximum wait duration in seconds (default: 60).
- **Output**: `{agent_id, status, done: boolean, result, logs}`
- **Plan Mode**: Allowed.

### `close_agent`
- **Description**: Stops, cancels, and cleans up an active background worker agent and its associated task resources.
- **Parameters**:
  - `agent_id` *(string, required)*: The target agent ID to terminate.
- **Output**: `{agent_id, status: 'closed', closed: true}`
- **Plan Mode**: Allowed.

### `resume_agent`
- **Description**: Resumes a paused, cancelled, or stopped agent, optionally providing appended instructions or new steering directives.
- **Parameters**:
  - `agent_id` *(string, required)*: The target agent ID to resume.
  - `additional_prompt` *(string, optional)*: Extra instructions or goals to guide the resumed agent.
- **Output**: `{agent_id, status: 'running', resumed: true}`
- **Plan Mode**: Blocked.

### `spawn_agents_on_csv`
- **Description**: Batch spawns a parallel swarm of worker agents across the rows of a CSV dataset, formatting the prompt template with column values and enforcing concurrency limits.
- **Parameters**:
  - `csv_path` *(string, required)*: Path to the CSV dataset file.
  - `prompt_template` *(string, required)*: Prompt template containing `{column_name}` placeholders.
  - `role` *(string, optional)*: Role assigned to each worker agent (default: `batch_worker`).
  - `concurrency` *(integer, optional)*: Maximum concurrent active agents running in parallel (default: 3).
  - `workdir` *(string, optional)*: Working directory scope for spawned agents.
- **Output**: `{csv_path, total_rows, agents_spawned, agent_ids, status: 'spawned'}`
- **Plan Mode**: Blocked.

---

## Maintenance & Update Policy

Whenever a new tool is implemented in `live_cli.py`:
1. Register its schema in `live_cli.TOOLS`.
2. Add its implementation handler in `live_cli.py`.
3. Add its complete description, schema, parameter table, and Plan Mode policy to **`TOOL-STORE.md`**.
4. Update the help guide table in `priya.py` if relevant.
