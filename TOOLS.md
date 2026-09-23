**TOOL SET A**

# IMPLEMENTED: AGENTJOB

`agentjob` launches a non-blocking Gemini `gemini-3.5-flash-lite` coding
subagent with shell access. The versioned runner is `tools/job_runner.py`;
transient job state and logs are stored in `.priya/jobs/` and are not tracked.

- `agentjob spawn "<task>" [workdir]` → `{job_id, workdir}`. Without a workdir,
  it works in the repository root.
- `agentjob status <job_id>` → `{status, reason, turn, workdir, ...}`.
- `agentjob log <job_id>` → current job transcript snapshot.
- `agentjob send <job_id> "<message>"` → queues steering for the next turn.
- `agentjob stop <job_id>` → terminates the job process group and preserves its
  transcript/status files.

Use it for substantial independent work. It is non-blocking: spawn, then poll
status or read the log rather than waiting in the foreground. The complete
lifecycle above was manually verified against the local runner.

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

# COMING SOON

- CronCreate
- CronDelete
- CronList
- Edit
- EnterPlanMode
- EnterWorktree
- ExitPlanMode
- ExitWorktree
- Glob
- Grep
- LSP
- ListMcpResourcesTool
- Monitor
- PushNotification
- Read
- ReadMcpResourceTool
- RemoteTrigger
- ReportFindings
- ScheduleWakeup
- SendMessage
- SendUserFile
- ShareOnboardingGuide
- Skill
- TaskCreate
- TaskGet
- TaskList
- TaskOutput
- TaskStop
- TaskUpdate
- TodoWrite (disabled by default, superseded by Task*)
- ToolSearch
- WaitForMcpServers
- WebFetch
- WebSearch
- Workflow
- Write



**TOOL SET B**

- browser_subagent (dispatcher)
- browser_navigate / open_browser_url
- read_browser_page
- browser_click_element
- browser_select_option
- browser_press_key
- browser_scroll
- browser_scroll_up
- browser_scroll_down
- browser_resize_window
- capture_browser_screenshot
- execute_browser_javascript
- list_browser_pages
- browser_input
- browser_get_dom
- browser_move_mouse
- click_browser_pixel
- browser_drag_pixel_to_pixel
- capture_browser_console_logs

**TOOL SET C**

**Files & code (5)**
- `read_file`
- `list_dir`
- `glob_file_search`
- `rg` (ripgrep search)
- `git`

**Editing (1)**
- `apply_patch`

**Execution (1)**
- `shell_command`

**Search/info (2)**
- `web_search`
- `tool_search`

**Images (2)**
- `view_image`
- `image_gen`

**Planning (1)**
- `update_plan`

**Multi-agent orchestration (6)**
- `spawn_agent`
- `send_input`
- `wait_agent`
- `close_agent`
- `resume_agent`
- `spawn_agents_on_csv`

**Parallel dispatch (1)**
- `multi_tool_use.parallel`
