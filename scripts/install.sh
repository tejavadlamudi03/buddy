#!/bin/bash
# Sets up Buddy on an Ubuntu laptop in one go, choosing what suits its hardware. Safe to run again.
#   scripts/install.sh                  checks the laptop, then installs everything
#   scripts/install.sh --check          only checks the laptop and says what it would choose
#   scripts/install.sh --profile=NAME   full, light or basic, instead of the one chosen for this laptop
#   scripts/install.sh --no-ai          the same as --profile=basic: built-in commands only
#   scripts/install.sh --yes            doesn't stop to ask before installing system packages
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
OS_RELEASE=${BUDDY_OS_RELEASE:-/etc/os-release}
XORG_SESSION=${BUDDY_XORG_SESSION:-/usr/share/xsessions/ubuntu-xorg.desktop}
PACKAGES=(curl zstd gcc libc6-dev linux-libc-dev python3-dev pipewire-bin ffmpeg poppler-utils libreoffice-writer libreoffice-calc libreoffice-impress python3-gi gir1.2-atspi-2.0 at-spi2-core)
CHROME_URL=https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
MIN_VIDEO_MB=7500  # an "8 GB" card reports a little under 8192
AI_MEMORY_GB=15  # "16 GB" reports a little under
ONLY_CHECK="" PROFILE="" ASSUME_YES="" PROBLEMS=0 WHY=""

for option in "$@"; do
    case "$option" in
        --check) ONLY_CHECK=1 ;;
        --no-ai) PROFILE=basic ;;
        --profile=full|--profile=light|--profile=basic) PROFILE=${option#--profile=} ;;
        --yes) ASSUME_YES=1 ;;
        *) echo "Unknown option: $option. See the top of this script." >&2; exit 2 ;;
    esac
done

ok() { echo "  ok       $1"; }
warn() { echo "  warning  $1"; }
problem() { echo "  PROBLEM  $1"; PROBLEMS=$((PROBLEMS + 1)); }
check_system() {
    local id="" version=""
    if [ -r "$OS_RELEASE" ]; then
        id=$(. "$OS_RELEASE"; echo "${ID:-}")
        version=$(. "$OS_RELEASE"; echo "${VERSION_ID:-}")
    fi
    if [ "$id" != ubuntu ]; then
        problem "This isn't Ubuntu (found '${id:-unknown}'). Buddy only works on Ubuntu with GNOME."
    elif [ "$version" = 24.04 ]; then
        ok "Ubuntu 24.04"
    else
        warn "Ubuntu $version hasn't been tested. Buddy is built on 24.04, and newer versions may have no Xorg session."
    fi
    if [ "$(uname -m)" = x86_64 ]; then ok "64-bit Intel or AMD processor"; else problem "Needs a 64-bit Intel or AMD processor, not $(uname -m)."; fi
}

check_session() {
    local type=${BUDDY_SESSION_TYPE:-}
    if [ -z "$type" ] && command -v loginctl >/dev/null; then
        local session
        session=$(loginctl 2>/dev/null | awk '/seat0/ {print $1; exit}')
        [ -n "$session" ] && type=$(loginctl show-session "$session" -p Type --value 2>/dev/null)
    fi
    type=${type:-${XDG_SESSION_TYPE:-unknown}}
    case "$type" in
        x11) ok "Xorg session" ;;
        wayland)
            if [ -e "$XORG_SESSION" ]; then
                problem "You're in a Wayland session, which stops apps controlling each other. Log out, click your name, choose 'Ubuntu on Xorg' under the gear icon at the bottom right, log in, and run this again."
            else
                problem "This Ubuntu has no Xorg session, and Buddy can't control a Wayland desktop. The simplest fix is Ubuntu 24.04. See 'If there's no Xorg' in the README."
            fi
            ;;
        *) warn "Couldn't tell the session type ('$type'). Buddy needs 'Ubuntu on Xorg' when you use it." ;;
    esac
}

video_memory_mb() {
    command -v nvidia-smi >/dev/null || return 0
    nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | sort -n | tail -n 1 | tr -dc 0-9
}

# Chooses rather than refuses: a weaker laptop gets a smaller model, or the built-in commands.
check_hardware() {
    local memory cores free_gb video chosen
    memory=${BUDDY_MEMORY_GB:-$(awk '/MemTotal/ {print int($2 / 1000000)}' /proc/meminfo)}
    cores=$(nproc)
    free_gb=${BUDDY_FREE_GB:-$(df -BG --output=avail "$HOME" | tail -n 1 | tr -dc 0-9)}
    video=$(video_memory_mb)
    [ "$memory" -ge 7 ] || problem "Only $memory GB of memory. Buddy needs at least 8 GB."
    [ "$free_gb" -ge 5 ] || problem "Only $free_gb GB of free disk. Buddy needs at least 5 GB."
    ok "$memory GB of memory, $cores processor threads, $free_gb GB of free disk"
    if [ -n "$video" ]; then ok "NVIDIA graphics card with $video MB of video memory"; else warn "No working NVIDIA card found. If you have one: sudo ubuntu-drivers install, then restart."; fi

    if [ "$memory" -lt "$AI_MEMORY_GB" ]; then
        chosen=basic WHY="the local AI needs 16 GB of memory"
    elif [ "$free_gb" -lt 12 ]; then
        chosen=basic WHY="the local AI needs about 12 GB of free disk"
    elif [ -n "$video" ] && [ "$video" -ge "$MIN_VIDEO_MB" ] && [ "$free_gb" -ge 15 ]; then
        chosen=full WHY="this graphics card runs the larger model well"
    elif [ -n "$video" ] && [ "$video" -ge "$MIN_VIDEO_MB" ]; then
        chosen=light WHY="the larger model needs 15 GB of free disk"
    elif [ -n "$video" ]; then
        chosen=light WHY="the larger model needs 8 GB of video memory"
    else
        chosen=light WHY="with no suitable graphics card, the smaller model runs on the processor, more slowly"
    fi
    if [ -n "$PROFILE" ]; then
        [ "$PROFILE" = "$chosen" ] || WHY="you asked for it. For this laptop I'd have chosen $chosen, because $WHY"
    else
        PROFILE=$chosen
    fi
    echo
    case "$PROFILE" in
        full) echo "Setup: full. Gemma 4 (about 6.6 GB) with Telugu, because $WHY." ;;
        light) echo "Setup: light. A smaller Gemma 4 (about 4.6 GB) with Telugu, because $WHY." ;;
        basic) echo "Setup: basic. Built-in commands only, with no AI chat, file reading or Telugu, because $WHY." ;;
    esac
}

install_packages() {
    local missing=() package sudo=""
    for package in "${PACKAGES[@]}"; do
        dpkg -s "$package" >/dev/null 2>&1 || missing+=("$package")
    done
    local need_chrome=""
    command -v google-chrome >/dev/null || need_chrome=1
    if [ ${#missing[@]} -eq 0 ] && [ -z "$need_chrome" ]; then
        ok "System packages are already installed"
        return 0
    fi
    [ "$(id -u)" -eq 0 ] || sudo=sudo
    echo "  To install with apt: ${missing[*]:-}${need_chrome:+ google-chrome}"
    if [ -z "$ASSUME_YES" ]; then
        read -r -p "  Install these now? This may ask for your password. [y/N] " answer
        case "$answer" in [yY]*) ;; *) echo "Stopped, with nothing installed."; exit 1 ;; esac
    fi
    $sudo apt-get update -qq || return 1
    if [ ${#missing[@]} -gt 0 ]; then
        DEBIAN_FRONTEND=noninteractive $sudo apt-get install -y -qq --no-install-recommends "${missing[@]}" || return 1
    fi
    if [ -n "$need_chrome" ]; then
        local deb
        deb=$(mktemp --suffix=.deb)
        curl -fsSL -o "$deb" "$CHROME_URL" || return 1
        chmod 644 "$deb"
        DEBIAN_FRONTEND=noninteractive $sudo apt-get install -y -qq "$deb" || return 1
        rm -f "$deb"
    fi
}

step() { echo; echo "== $1"; }
stop_on_failure() {
    echo >&2
    echo "Setup stopped at: $1. Fix that and run this script again, and it picks up from there." >&2
    exit 1
}

unset PYTHONPATH  # a sourced ROS environment breaks Python packages

echo "Checking this laptop"
check_system
check_session
check_hardware
if [ "$PROBLEMS" -gt 0 ]; then
    echo
    echo "$PROBLEMS problem(s) to fix first. Nothing has been installed."
    exit 1
fi
if [ -n "$ONLY_CHECK" ]; then
    echo
    echo "This laptop is ready. Run scripts/install.sh to install Buddy."
    exit 0
fi

step "System packages"
install_packages || stop_on_failure "system packages"

step "Python and Buddy"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh || stop_on_failure "installing uv"
fi
cd "$ROOT" || exit 1
uv sync --quiet || stop_on_failure "installing Buddy's Python packages"
ok "Buddy is installed"

MODEL=$(.venv/bin/buddy use-profile "$PROFILE") || stop_on_failure "choosing the settings"
if [ "$PROFILE" != basic ]; then
    step "Local AI: $MODEL"
    scripts/install-local-ai.sh "$MODEL" || stop_on_failure "the local AI"
    step "Telugu speech model (about 750 MB when done)"
    scripts/install-telugu.sh || stop_on_failure "the Telugu speech model"
fi

step "App menu and auto-start"
.venv/bin/buddy install-launcher || stop_on_failure "the app menu entry"
if systemctl --user show-environment >/dev/null 2>&1; then
    .venv/bin/buddy install-autostart || stop_on_failure "auto-start"
    systemctl --user restart buddy.service || warn "Buddy didn't start. Try: .venv/bin/buddy"
else
    warn "No desktop session here, so auto-start wasn't set up. Later, run: .venv/bin/buddy install-autostart"
fi

step "Final check"
if ! .venv/bin/buddy check; then
    echo
    echo "Buddy is installed, but the check above found a problem to fix. .venv/bin/buddy check runs it again."
fi

echo
echo "Open Buddy from the app menu."
echo "For WhatsApp, enter your number under Settings, then run: .venv/bin/buddy whatsapp-login"
echo "It needs a separate WhatsApp account for this laptop. See 'WhatsApp' in the README."
