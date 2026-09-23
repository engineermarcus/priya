**TOOL SET A**

#  AGENT

**Agent Job (background subagent via CLI)**
- `agentjob spawn "<task>" [workdir]` -> {job_id, workdir}
- `agentjob status <job_id>` -> current state (running/done/failed/stopped + reason)
- `agentjob log <job_id> [--follow]`
- `agentjob send <job_id> "<message>"` — steer at next turn boundary
- `agentjob stop <job_id>` — hard kill, preserves partial workdir state
Use for any substantial subtask that can run independently while you continue other work. Always spawn, never block — check status periodically instead of waiting.

# COMING SOON

- Artifact
- AskUserQuestion
- Bash
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
- NotebookEdit
- PowerShell
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

