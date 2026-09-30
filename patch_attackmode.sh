#!/bin/sh
# Patch stock ATTACKMODE to honor BUNNY_IPRODUCT / BUNNY_IMANUFACTURER env vars.
# Values are single-quote-wrapped so the double-quotes survive shell parsing and
# reach the kernel module param parser intact - required for values with spaces
# (e.g. "Cruzer Blade"), otherwise the kernel splits on the space and insmod fails.
# Rebuilds from ATTACKMODE.orig each run (idempotent; also repairs a prior patch).
F=/usr/local/bunny/bin/ATTACKMODE
[ -f "$F.orig" ] || cp "$F" "$F.orig"
N=$(grep -n 'insmod_cmd="$insmod_cmd $mod_params"' "$F.orig" | head -1 | cut -d: -f1)
[ -z "$N" ] && { echo anchor-not-found; exit 1; }
{
  head -n "$N" "$F.orig"
  cat <<'INS'
	[ "x$BUNNY_IPRODUCT" != "x" ] && insmod_cmd="$insmod_cmd iProduct='\"$BUNNY_IPRODUCT\"'"
	[ "x$BUNNY_IMANUFACTURER" != "x" ] && insmod_cmd="$insmod_cmd iManufacturer='\"$BUNNY_IMANUFACTURER\"'"
INS
  tail -n +"$((N+1))" "$F.orig"
} > "$F"
echo patched
grep -n 'BUNNY_' "$F"
