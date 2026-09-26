# Priya Milestones & Agent Invariants

This document serves as the authoritative memory of completed milestones, architecture invariants, past bug fixes, and upcoming roadmap items.

> [!IMPORTANT]
> **READ BEFORE MODIFYING THE CODEBASE**:
> Never overwrite or regress the fixes documented in the **Critical Invariants** section.

---

## 1. System Architecture

Priya operates on a dual-process architecture:
1. **Frontend / UI (`priya.py`)**:
   - ANSI/VT100 terminal interface running in raw mode on alternate screen (`\x1b[?1049h`).
   - Renders turns, streaming Markdown, interactive collapsible tool calls, and dropdown overlays.
   - Communicates with the backend worker via bidirectional pipes with line-framed protocol messages (`<<SET_MODEL>>`, `<<ASK_USER_QUESTION>>`, `<<ASK_USER_ANSWER>>`, `<<EDIT_APPROVAL>>`, `<<TOOL_*>>`, `<<AGENTJOB_*>>`, `<<THINKING>>`, `<<END>>`).
2. **Backend Worker (`live_cli.py`)**:
   - Asynchronous Python event loop (`asyncio`) managing model streaming (Mistral, Gemini SSE), tool execution, scheduled cron jobs, background agent jobs, and swarm orchestration.
   - Loads authoritative tool store (`TOOLS-STORE.md`) into memory at startup.

---

## 2. Completed Milestones

### Milestone 1: Terminal TUI & Display Engine
- Alternate screen buffer management (`smcup` / `rmcup`), raw input handling, and terminal resize detection (`SIGWINCH`).
- Dynamic line cache (`_lines_cache`), virtual viewport calculation, and multi-turn message scrolling.
- Collapsible tool call nodes (`ToolNode`) with live streaming logs, compact mode toggle (`/compact`, `Ctrl+O`), and thinking blocks with elapsed timers.

### Milestone 2: Core Toolset & Surgical File Editing
- Implemented 14 essential agentic coding tools: `bash`, `Read`, `Edit`, `Glob`, `Grep`, `LSP`, `artifact`, `agentjob`, `askUserQuestion`, `EnterPlanMode`, `ExitPlanMode`, `CronCreate`, `CronList`, `ListMcpResourcesTool`.
- Surgical file modification with diff generation and user approval protocol (`<<EDIT_APPROVAL>>`).
- Background job runners (`agentjob`) and HTML artifact server previewing.

### Milestone 3: Command Navigation & Bottom-Left Dropdown Anchoring
- Replaced disruptive top-left placement with **bottom-left dropdown overlays**, anchored directly above the status and input bars (`start_row = max(1, status_row - box_h)`, `start_col = 2`).
- Auto-completion on `Enter` (replacing `Tab` for keyboards lacking Tab).
- Up/Down arrow iteration for all menus.

### Milestone 4: Interactive Questioning & Skip Handling
- Interactive multiple-choice tool (`askUserQuestion`) with keyboard navigation (`↑`/`↓` and number keys `1-9`).
- Custom answer input (`Other` / `Custom`).
- **Skip Handling**: Automatic injection of `Skip` option, keyboard shortcut `s` / `S`, and graceful JSON transmission (`{"skipped": True}`) to prevent tool hang.

### Milestone 5: Multi-Model Orchestration & Gemini Free Tier Support
- Model switcher (`/models`) unified into a single dropdown list (no multi-step disruption).
- Support for Mistral (`mistral-medium-latest`) and all available Gemini free tier models (`gemini-3.7-flash`, `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.6-flash`, `gemini-3.1-flash-lite`, `gemini-3.8-flash`).
- Extended thinking configuration (budget: `low`, `medium`, `high`).
- **Instant Quota Handling**: Immediate notification on Google API `429 Quota Exceeded` prompting model switch via `/models`, preventing silent background sleeping and UI freeze.

### Milestone 6: Swarm Orchestration & Background Agent Lifecycle
- Agent swarm manager (`tools/swarm.py`): `spawn_agent`, `send_input`, `wait_agent`, `close_agent`, `resume_agent`, and `spawn_agents_on_csv`.
- Full asynchronous inter-agent communication, process isolation, and concurrent dataset processing.

### Milestone 7: SQLite Chat Persistence, `/delete` & Onboarding API Key Manager
- Local SQLite database (`chat_db.py`) storing chats and messages at `~/.priya/chats.db`, isolated by project directory (`os.getcwd()`).
- Automated message logging for user prompts and assistant completions with auto-titling from the first user prompt.
- Interactive `/delete` command featuring bottom-left dropdown selection of past chats with metadata (timestamps, message counts, model, active session marker), with immediate SQLite deletion and automatic session reset if the active chat is deleted.
- Environment & API key manager (`env_manager.py`) providing startup detection for `MISTRAL_API_KEY`, `GEMINI_API_KEY`, and project `.env`.
- Step-by-step onboarding walkthrough with direct links for Mistral (`console.mistral.ai`) and Google AI Studio (`aistudio.google.com`).
- On-demand `/onboarding` command to configure, save, and hot-reload API keys into `.env` and the running worker via line-framed `<<SET_KEYS>>` protocol.

---

## 3. CRITICAL INVARIANTS & ANTI-REGRESSION RULES

> [!CAUTION]
> **DO NOT OVERWRITE THESE FIXES UNDER ANY CIRCUMSTANCES.**

### Rule 1: Terminal Mouse Selection, Highlight-to-Copy & Paste
- **Code Location**: [`priya.py`](file:///home/marcus/priya/priya.py) lines 76-77.
- **Required Escape Codes**:
  ```python
  def enable_mouse():  return CSI + "?1000l" + CSI + "?1006l" + CSI + "?1007h" + CSI + "?2004h"
  def disable_mouse(): return CSI + "?1007l" + CSI + "?2004l"
  ```
- **Why**:
  - `?1000l` & `?1006l` MUST disable mouse button intercepting. If `?1000h` is enabled, the terminal intercepts all mouse clicks and drags, **breaking native terminal highlight-to-copy and right-click paste**!
  - `?1007h` enables Alternate Scroll Mode so mouse wheel scrolling generates up/down arrow keys without grabbing mouse clicks.
  - `?2004h` enables bracketed paste mode, which `read_key` catches as `("PASTE", text)`.

### Rule 2: Dropdown Overlay Placement
- **Code Location**: [`Screen._redraw_locked()`](file:///home/marcus/priya/priya.py)
- **Positioning**:
  ```python
  box_h = len(drop_lines)
  start_row = max(1, status_row - box_h)
  start_col = 2
  ```
- **Why**: Keeps dropdowns directly above the input bar where the user is typing, preventing cursor and text occlusion.

### Rule 3: Single-Step Model Selection
- **Code Location**: [`_handle_cmd_models()`](file:///home/marcus/priya/priya.py)
- **Why**: Presenting model + effort tiers in a single list eliminates step splitting and UI flicker. Selecting any option and hitting `Enter` immediately sets the model.

### Rule 4: Question Tool Skip & Worker Non-Blocking
- **Code Location**: [`priya.py`](file:///home/marcus/priya/priya.py) and [`live_cli.py`](file:///home/marcus/priya/live_cli.py)
- **Why**: If a user does not want to answer a question, pressing `s` or selecting `Skip` sends `{"id": ..., "skipped": True}`. `live_cli.py` resolves the future immediately and returns `{"skipped": True, ...}` to the LLM.

### Rule 5: Gemini 429 Quota Reached Handling
- **Code Location**: [`live_cli.py`](file:///home/marcus/priya/live_cli.py)
- **Why**: Free-tier Gemini models (such as `gemini-3.8-flash` with a 20 req/day limit) return HTTP 429 with retry delays of 50+ seconds. **DO NOT** sleep 50 seconds in a background thread. Immediately report the quota exhaustion to the user with a prompt to use `/models` and close the turn with `<<END>>`.

---

## 4. Upcoming Roadmap & Next Milestones

### Milestone 7: Toolset B Implementation
- Review `TOOLS.md` and `TOOLS-STORE.md` to identify next non-redundant toolset.
- Ensure strict segregation: what was implemented in Set A must not be duplicated.
- Expand worker and schema bindings in both Mistral and Gemini tool declarations.

### Milestone 8: Multi-Agent Collaboration Enhancements
- Visual swarm inspector in TUI.
- Cross-agent message bus streaming directly into child turn logs.
- Persistent session checkpoints and resume capability across restarts.
