# twin-route.bash — send commands typed in this terminal to the twin PC when the rules say so.
# Sourced at the end of ~/.bashrc. Rules: ~/.config/twin-route/rules.toml · off: twin route off
# Design: ~/projects/twinPC/docs/superpowers/specs/2026-09-23-twin-task-routing-design.md
[[ $- == *i* ]] || return 0

_TR_DIR=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
_TR_ORIG=""   # typed line whose rewritten form must be replaced in history
_TR_ADD=""    # typed line to add to history when nothing was executed (cancel / refused)

# twin's state (up|unlock|off) from the SSH banner, cached 5 s; TWIN_ROUTE_STAGE overrides (tests)
_twin_route_stage() {
  if [[ -n ${TWIN_ROUTE_STAGE:-} ]]; then echo "$TWIN_ROUTE_STAGE"; return; fi
  local cache=${XDG_RUNTIME_DIR:-/tmp}/twin-route.stage now t st b
  printf -v now '%(%s)T' -1
  if [[ -f $cache ]] && read -r t st < "$cache" && (( now - t < 5 )); then echo "$st"; return; fi
  b=$(timeout 1.5 bash -c 'exec 3<>/dev/tcp/10.42.0.11/22 && head -c 64 <&3' 2>/dev/null | tr -d '\r' | head -1)
  case $b in *dropbear*) st=unlock ;; SSH-*) st=up ;; *) st=off ;; esac
  echo "$now $st" > "$cache"; echo "$st"
}

# is ~/twin mounted? reads the mount table only — never stats the (possibly dead) mount
_twin_route_mounted() { ${TWIN_ROUTE_MOUNT_CHECK:-findmnt -rn -M "$HOME/twin"} >/dev/null 2>&1; }

_twin_route_enter() {
  _TR_ORIG=""; _TR_ADD=""
  local line=$READLINE_LINE out decision reason st key ql
  [[ -n ${line//[[:space:]]/} ]] || return 0
  [[ ${TWIN_ROUTE:-on} != off && ! -e ${XDG_CONFIG_HOME:-$HOME/.config}/twin-route/disabled ]] || return 0
  bash -n <<<"$line" 2>/dev/null || return 0   # incomplete line (for … do, if … then, f() {): leave it alone
  out=$(timeout 0.3 "$_TR_DIR/twin-route" --cwd "$PWD" -- "$line" 2>/dev/null) || return 0   # fail-safe
  decision=${out%% *}; reason=${out#* }

  if [[ $decision != twin ]]; then
    if [[ $reason == forced && $line =~ ^[[:space:]]*local[[:space:]]+(.*)$ ]]; then
      READLINE_LINE=${BASH_REMATCH[1]}; _TR_ORIG=$line
    elif [[ $reason == files-not-on-twin ]]; then
      printf '\e[2m→ here (needs files in %s — move the project into ~/twin to run it on twin)\e[0m\n' "$PWD"
    fi
    return 0
  fi

  # aliases, functions and builtins (z, ll, pushd, pwd…) only exist in / act on this shell
  local w; read -r -a w <<<"$line"
  while [[ ${w[0]:-} == [A-Za-z_]*=* ]]; do w=("${w[@]:1}"); done
  case $(type -t -- "${w[0]:-}") in alias|function|builtin) return 0 ;; esac

  if [[ $reason == workspace ]] && ! _twin_route_mounted; then
    printf '\e[33m~/twin is not mounted (twin off?) — twin wake · systemctl --user restart twin-mount\e[0m\n'
    READLINE_LINE=""; _TR_ADD=$line; return 0
  fi

  printf -v ql '%q' "$line"
  st=$(_twin_route_stage)
  if [[ $st != up ]]; then
    printf '\ntwin is %s — [h]ere / [w]ake twin / [c]ancel? ' "$st"
    read -rsn1 key </dev/tty; echo
    case $key in
      h|H) return 0 ;;
      w|W) READLINE_LINE="twin wake && twin-exec -- $ql" ;;
      *)   READLINE_LINE=""; _TR_ADD=$line; return 0 ;;
    esac
  else
    READLINE_LINE="twin-exec -- $ql"
  fi
  _TR_ORIG=$line
  printf '\e[2m→ twin (%s)\e[0m\n' "$reason"
}

# runs before the existing history -a/-c/-r in PROMPT_COMMAND: history shows what was typed
_twin_route_history() {
  local rc=$?                                  # hand $? on unchanged (starship's precmd runs after us)
  if [[ -n $_TR_ORIG ]]; then history -d -1 2>/dev/null; history -s -- "$_TR_ORIG"; fi
  if [[ -n $_TR_ADD ]]; then history -s -- "$_TR_ADD"; fi
  _TR_ORIG=""; _TR_ADD=""
  return $rc
}

# Enter = rewrite (bind -x) then accept-line; bind -x alone cannot accept a line
# (the "$_" argument keeps $_ intact: bash sets $_ to this invocation's last argument afterwards)
bind -x '"\C-x\C-t": _twin_route_enter "$_"'
bind '"\C-x\C-a": accept-line'
bind '"\C-m": "\C-x\C-t\C-x\C-a"'
bind '"\C-j": "\C-x\C-t\C-x\C-a"'
[[ $PROMPT_COMMAND == *_twin_route_history* ]] || PROMPT_COMMAND="_twin_route_history${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
