#!/bin/bash
# Claude Code SessionStart 시 호출 — 터미널/IDE 앱을 감지해서
# ~/.config/token-alert/.notify_app 에 bundle ID를 저장한다.
# notify.sh 가 이 값을 읽어 terminal-notifier -activate 대상을 결정한다.
#
# 지원 터미널/IDE:
#   VS Code 계열  : VS Code, Cursor, Windsurf, Antigravity
#   독립 터미널   : iTerm2, WezTerm, Warp, Alacritty, Kitty, Hyper, Zed,
#                   Nova, Terminal.app
#   JetBrains     : IntelliJ, PyCharm, WebStorm, CLion, GoLand, Rider, 외 다수
#   멀티플렉서    : tmux, screen 안에서도 원본 터미널 추적

APP_FILE="$HOME/.config/token-alert/.notify_app"

# ─── TERM_PROGRAM 값 → bundle ID ──────────────────────────────────────────
_term_to_bundle() {
  case "$1" in
    vscode)         echo "com.microsoft.VSCode" ;;
    iTerm.app)      echo "com.googlecode.iterm2" ;;
    WezTerm)        echo "com.github.wez.wezterm" ;;
    WarpTerminal)   echo "dev.warp.Warp-Stable" ;;
    Hyper)          echo "co.zeit.hyper" ;;
    Apple_Terminal) echo "com.apple.Terminal" ;;
    zed)            echo "dev.zed.Zed" ;;
    # tmux / screen 은 터미널이 아니므로 빈 값 반환 → 다음 방법으로 넘어감
  esac
}

# ─── 프로세스 이름 → bundle ID ────────────────────────────────────────────
_proc_to_bundle() {
  case "$1" in
    # VS Code 계열
    *[Cc]ursor*)                        echo "com.todesktop.230313mzl4w4u92" ;;
    *[Ww]indsurf*)                      echo "com.exafunction.windsurf" ;;
    # Antigravity IDE (IDE) vs Antigravity (일반 앱) — IDE가 더 긴 이름이므로 먼저 매칭
    *[Aa]ntigravity*[Ii][Dd][Ee]*)     echo "com.google.antigravity-ide" ;;
    *[Aa]ntigravity*|*antigravity*)    echo "com.google.antigravity" ;;
    *[Ee]lectron*|*[Cc]ode*|*VSCode*) echo "com.microsoft.VSCode" ;;
    # 독립 터미널
    *[Ii][Tt]erm*)                 echo "com.googlecode.iterm2" ;;
    *[Ww]ez[Tt]erm*|*wezterm*)    echo "com.github.wez.wezterm" ;;
    *[Ww]arp*)                     echo "dev.warp.Warp-Stable" ;;
    *[Aa]lacritty*)                echo "io.alacritty" ;;
    *[Kk]itty*)                    echo "net.kovidgoyal.kitty" ;;
    *[Hh]yper*)                    echo "co.zeit.hyper" ;;
    *[Zz]ed*)                      echo "dev.zed.Zed" ;;
    *[Nn]ova*)                     echo "com.panic.Nova" ;;
    *[Tt]erminal*)                 echo "com.apple.Terminal" ;;
    # JetBrains IDEs
    *[Ii]ntelli[Jj]*|*idea*)       echo "com.jetbrains.intellij" ;;
    *[Pp]y[Cc]harm*|*pycharm*)     echo "com.jetbrains.pycharm" ;;
    *[Ww]eb[Ss]torm*|*webstorm*)   echo "com.jetbrains.webstorm" ;;
    *[Cc][Ll]ion*|*clion*)         echo "com.jetbrains.clion" ;;
    *[Gg]o[Ll]and*|*goland*)       echo "com.jetbrains.goland" ;;
    *[Rr]ider*)                    echo "com.jetbrains.rider" ;;
    *[Dd]ata[Gg]rip*|*datagrip*)   echo "com.jetbrains.datagrip" ;;
    *[Rr]uby[Mm]ine*|*rubymine*)   echo "com.jetbrains.rubymine" ;;
    *[Pp]hp[Ss]torm*|*phpstorm*)   echo "com.jetbrains.phpstorm" ;;
    *[Ff]leet*)                    echo "com.jetbrains.fleet" ;;
    *[Aa]pp[Cc]ode*|*appcode*)     echo "com.jetbrains.appcode" ;;
    *[Aa]ndroid[Ss]tudio*)         echo "com.google.android.studio" ;;
  esac
}

# ─── 프로세스 트리를 올라가며 터미널/IDE 찾기 ────────────────────────────
_walk_process_tree() {
  local pid=$$
  local bundle result
  for _ in $(seq 1 25); do
    local parent comm
    parent=$(ps -p "$pid" -o ppid= 2>/dev/null | tr -d ' ')
    comm=$(ps -p "$pid" -o comm= 2>/dev/null | xargs basename 2>/dev/null)
    result=$(_proc_to_bundle "$comm")
    if [ -n "$result" ]; then
      echo "$result"; return
    fi
    [ -z "$parent" ] || [ "$parent" -le 1 ] && break
    pid=$parent
  done
}

# ─── tmux showenv 에서 특정 키 추출 ──────────────────────────────────────
_tmux_env_get() {
  # $1 = 변수명, $2 = 미리 읽어둔 tmux showenv 출력
  printf '%s\n' "$2" | grep "^${1}=" | cut -d= -f2-
}

# ─── 메인 감지 함수 ───────────────────────────────────────────────────────
detect() {

  # ── Step 1: TERM_PROGRAM 직접 매핑 (tmux/screen 제외) ─────────────────
  if [ -n "$TERM_PROGRAM" ] && \
     [ "$TERM_PROGRAM" != "tmux" ] && \
     [ "$TERM_PROGRAM" != "screen" ]; then
    local b; b=$(_term_to_bundle "$TERM_PROGRAM")
    [ -n "$b" ] && echo "$b" && return
  fi

  # ── Step 2: 터미널별 전용 환경변수 ────────────────────────────────────

  # VS Code 계열 (Cursor / Windsurf / Antigravity IDE / Antigravity 구별)
  if [ -n "$VSCODE_PID" ] || [ -n "$VSCODE_IPC_HOOK" ] || \
     [ -n "$VSCODE_INJECTION_POINT" ] || [ -n "$VSCODE_NLS_CONFIG" ]; then
    local ipc="${VSCODE_IPC_HOOK:-}${VSCODE_INJECTION_POINT:-}"
    if echo "$ipc" | grep -qi "cursor"; then
      echo "com.todesktop.230313mzl4w4u92"; return
    elif echo "$ipc" | grep -qi "windsurf"; then
      echo "com.exafunction.windsurf"; return
    elif echo "$ipc" | grep -qi "antigravity.ide\|antigravity-ide"; then
      # IPC 경로가 "Antigravity IDE" 또는 "antigravity-ide" 포함 → IDE
      echo "com.google.antigravity-ide"; return
    elif echo "$ipc" | grep -qi "antigravity"; then
      # 일반 Antigravity 앱
      echo "com.google.antigravity"; return
    fi
    echo "com.microsoft.VSCode"; return
  fi

  # Antigravity IDE 전용 환경변수 (VSCODE 계열 변수가 없는 경우 대비)
  if [ -n "$ANTIGRAVITY_PID" ] || [ -n "$ANTIGRAVITY_IPC_HOOK" ]; then
    echo "com.google.antigravity-ide"; return
  fi

  # iTerm2
  if [ -n "$ITERM_SESSION_ID" ] || [ -n "$ITERM_PROFILE" ]; then
    echo "com.googlecode.iterm2"; return
  fi

  # WezTerm
  if [ -n "$WEZTERM_PANE" ] || [ -n "$WEZTERM_UNIX_SOCKET" ]; then
    echo "com.github.wez.wezterm"; return
  fi

  # Warp
  if [ -n "$WARP_IS_LOCAL_SHELL_SESSION" ] || [ -n "$WARP_HONOR_PS1" ]; then
    echo "dev.warp.Warp-Stable"; return
  fi

  # Kitty
  if [ -n "$KITTY_WINDOW_ID" ] || [ "$TERM" = "xterm-kitty" ]; then
    echo "net.kovidgoyal.kitty"; return
  fi

  # Alacritty
  if [ "$TERM" = "alacritty" ]; then
    echo "io.alacritty"; return
  fi

  # Zed
  if [ -n "$ZED_TERM" ] || [ "$TERM_PROGRAM" = "zed" ]; then
    echo "dev.zed.Zed"; return
  fi

  # JetBrains — 공통 변수로 감지 후 프로세스 트리에서 구체 IDE 판별
  if [ "$TERMINAL_EMULATOR" = "JetBrains-JediTerm" ] || \
     [ -n "$JETBRAINS_REMOTE_RUN" ]; then
    local b; b=$(_walk_process_tree)
    # JetBrains 계열이 아닌 값이 나오면 무시하고 IntelliJ 폴백
    if echo "$b" | grep -q "jetbrains\|google.android"; then
      echo "$b"; return
    fi
    echo "com.jetbrains.intellij"; return
  fi

  # ── Step 3: tmux 안이면 tmux showenv 에서 원본 변수 탐색 ──────────────
  if [ -n "$TMUX" ]; then
    local te
    te=$(tmux showenv 2>/dev/null)

    local t_term t_vscode t_ipc t_iterm t_wez t_warp t_kitty t_zed t_agy
    t_term=$(_tmux_env_get "TERM_PROGRAM"              "$te")
    t_vscode=$(_tmux_env_get "VSCODE_PID"              "$te")
    t_ipc=$(_tmux_env_get "VSCODE_IPC_HOOK"            "$te")
    t_iterm=$(_tmux_env_get "ITERM_SESSION_ID"         "$te")
    t_wez=$(_tmux_env_get "WEZTERM_PANE"               "$te")
    t_warp=$(_tmux_env_get "WARP_IS_LOCAL_SHELL_SESSION" "$te")
    t_kitty=$(_tmux_env_get "KITTY_WINDOW_ID"          "$te")
    t_zed=$(_tmux_env_get "ZED_TERM"                   "$te")
    t_agy=$(_tmux_env_get "ANTIGRAVITY_PID"            "$te")

    if [ -n "$t_term" ] && [ "$t_term" != "tmux" ]; then
      local b; b=$(_term_to_bundle "$t_term")
      [ -n "$b" ] && echo "$b" && return
    fi
    [ -n "$t_vscode" ] && {
      echo "$t_ipc" | grep -qi "cursor"                            && echo "com.todesktop.230313mzl4w4u92" && return
      echo "$t_ipc" | grep -qi "windsurf"                         && echo "com.exafunction.windsurf" && return
      echo "$t_ipc" | grep -qi "antigravity.ide\|antigravity-ide" && echo "com.google.antigravity-ide" && return
      echo "$t_ipc" | grep -qi "antigravity"                      && echo "com.google.antigravity" && return
      echo "com.microsoft.VSCode"; return
    }
    [ -n "$t_agy" ]   && echo "com.google.antigravity-ide" && return
    [ -n "$t_iterm" ]  && echo "com.googlecode.iterm2" && return
    [ -n "$t_wez" ]    && echo "com.github.wez.wezterm" && return
    [ -n "$t_warp" ]   && echo "dev.warp.Warp-Stable" && return
    [ -n "$t_kitty" ]  && echo "net.kovidgoyal.kitty" && return
    [ -n "$t_zed" ]    && echo "dev.zed.Zed" && return
  fi

  # ── Step 4: 프로세스 트리 탐색 ────────────────────────────────────────
  local b; b=$(_walk_process_tree)
  [ -n "$b" ] && echo "$b" && return

  # ── Step 5: 세션 시작 시점 포그라운드 앱 (osascript) ──────────────────
  # 알려진 터미널/IDE bundle ID 만 허용 — 다른 앱이 포그라운드일 때 오탐 방지
  local frontmost
  frontmost=$(osascript \
    -e 'tell application "System Events"' \
    -e '  get bundle identifier of first application process whose frontmost is true' \
    -e 'end tell' 2>/dev/null)
  case "$frontmost" in
    com.microsoft.VSCode|\
    com.todesktop.*|\
    com.exafunction.windsurf|\
    com.google.antigravity-ide|\
    com.google.antigravity|\
    com.googlecode.iterm2|\
    com.github.wez.wezterm|\
    dev.warp.*|\
    io.alacritty|\
    net.kovidgoyal.kitty|\
    co.zeit.hyper|\
    dev.zed.Zed|\
    com.panic.Nova|\
    com.apple.Terminal|\
    com.jetbrains.*|\
    com.google.android.studio)
      echo "$frontmost"; return ;;
  esac

  # ── Step 6: 최후 폴백 ─────────────────────────────────────────────────
  echo "com.apple.Terminal"
}

detect > "$APP_FILE"
