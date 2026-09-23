#!/usr/bin/env bash
# Priya CLI — append-only output (no scroll regions), the pattern
# AIChat/Aider actually use, because DECSTBM scroll-region UIs are
# known to misbehave across terminals (Kitty/Windows Terminal/tmux).

set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER="$DIR/live_cli.py"
MODEL_NAME="gemini-3.8-live"

RED=$'\e[31m'; DIM=$'\e[2m'; CYAN=$'\e[36m'; BOLD=$'\e[1m'; RESET=$'\e[0m'

rule() { printf '%s%s%s\n' "$DIM" "$(printf '─%.0s' $(seq 1 "${COLUMNS:-$(tput cols)}"))" "$RESET"; }

cleanup() {
    [[ -n "${WORKER_PID:-}" ]] && kill "$WORKER_PID" 2>/dev/null
    [[ -n "${OUT_FIFO:-}" ]] && rm -f "$OUT_FIFO"
    [[ -n "${IN_FIFO:-}" ]] && rm -f "$IN_FIFO"
}
trap cleanup EXIT INT TERM

if [[ ! -f "$WORKER" ]]; then
    echo "worker not found: $WORKER" >&2
    exit 1
fi

echo
echo "  ${BOLD}${CYAN}Priya${RESET} ${DIM}· ${MODEL_NAME}${RESET}"
echo "  ${DIM}${PWD}${RESET}"
echo
rule

IN_FIFO=$(mktemp -u); OUT_FIFO=$(mktemp -u)
mkfifo "$IN_FIFO" "$OUT_FIFO"
python3 "$WORKER" < "$IN_FIFO" > "$OUT_FIFO" 2>/tmp/priya_worker.log &
WORKER_PID=$!
exec 3>"$IN_FIFO"
exec 4<>"$OUT_FIFO"

# synchronous: send, wait for the one reply line, print it, THEN prompt again.
# no background reader, no race between two writers on the same terminal.
while true; do
    if ! kill -0 "$WORKER_PID" 2>/dev/null; then
        echo "${RED}worker crashed — see /tmp/priya_worker.log${RESET}"
        break
    fi
    printf '%s›%s  ' "$RED" "$RESET"
    if ! IFS= read -r line; then
        break
    fi
    [[ "$line" == "q" ]] && break
    printf '%s\n' "$line" >&3

    if ! IFS= read -r -u 4 reply; then
        echo "${RED}worker closed the connection — see /tmp/priya_worker.log${RESET}"
        break
    fi
    printf '%s  ·%s %s\n\n' "$DIM" "$RESET" "$reply"
done
exec 3>&-
exec 4<&-
echo
rule
echo "${DIM}q to quit   ${MODEL_NAME}${RESET}"
