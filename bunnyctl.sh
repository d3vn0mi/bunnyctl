#!/bin/sh
# bunnyctl v0.2 - on-device chameleon profile helper (Bash Bunny Mark I)
# ---------------------------------------------------------------------------
# Runs ON the Bunny (armv7l / Debian 8). Operates directly on the udisk files
# the dispatcher payloads read at boot. No serial, no host required.
#
# Two payload banks, one shared profile pool:
#   mode.txt  -> SW1 (far end)   profile
#   mode2.txt -> SW2 (middle)    profile
#
# Install: place at /usr/local/bunny/bin/bunnyctl and `chmod +x`.
#          /usr/local/bunny/bin is not on PATH by default - call by full path
#          or symlink into /usr/local/bin. (SW1's dispatcher self-installs it.)
#
# CHANGELOG
#   v0.1  mode / gpio / status / profiles / profile
#   v0.2  second bank: mode2 (SW2/mode2.txt); status shows both banks
# ---------------------------------------------------------------------------
MODE1="/usr/local/bunny/udisk/mode.txt"
MODE2="/usr/local/bunny/udisk/mode2.txt"
PROFILES="/usr/local/bunny/udisk/profiles"

cmd="$1"; shift
case "$cmd" in
  mode)
    if [ -z "$1" ]; then
        echo "SW1: $(cat $MODE1 2>/dev/null || echo not set)"
    else
        printf "%s\n" "$1" > "$MODE1" && echo "SW1 mode -> $1"
    fi ;;
  mode2)
    if [ -z "$1" ]; then
        echo "SW2: $(cat $MODE2 2>/dev/null || echo not set)"
    else
        printf "%s\n" "$1" > "$MODE2" && echo "SW2 mode -> $1"
    fi ;;
  gpio)
    for p in PA8 PL4 PL3; do
        printf "%s=%s\n" "$p" "$(cat /sys/class/gpio_sw/$p/data 2>/dev/null)"
    done ;;
  status)
    echo "SW1 (far) mode: $(cat $MODE1 2>/dev/null || echo not set)"
    echo "SW2 (mid) mode: $(cat $MODE2 2>/dev/null || echo not set)"
    for p in PA8 PL4 PL3; do printf "%s=%s  " "$p" "$(cat /sys/class/gpio_sw/$p/data 2>/dev/null)"; done; echo
    echo "profiles: $(ls $PROFILES 2>/dev/null | sed 's/\.sh$//' | tr '\n' ' ' || echo none)" ;;
  profiles)
    ls "$PROFILES" 2>/dev/null | sed 's/\.sh$//' ;;
  profile)
    [ -z "$1" ] && { echo "usage: bunnyctl profile <name>"; exit 1; }
    cat "$PROFILES/$1.sh" 2>/dev/null || echo "not found: $1" ;;
  *)
    echo "usage: bunnyctl <mode [name]|mode2 [name]|gpio|status|profiles|profile <name>>" ;;
esac
