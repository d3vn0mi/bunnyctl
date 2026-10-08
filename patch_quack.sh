#!/bin/sh
# patch_quack.sh - inject BUNNY_CHAR_DELAY per-character HID delay into QUACK
# Idempotent: backs up /usr/local/bunny/bin/QUACK to QUACK.orig on first run,
# then always rebuilds the patched copy from the backup.
#
# Usage (from serial session or arming-mode shell on the Bunny):
#   sh /usr/local/bunny/udisk/patch_quack.sh
#
# The patch adds, immediately after each hidg_write() call in the STRING loop:
#   _d = int(os.environ.get('BUNNY_CHAR_DELAY', '0') or '0')
#   if _d > 0:
#       time.sleep(_d * 0.001)
#
# Activate in a profile:
#   export BUNNY_CHAR_DELAY=5     # 5 ms between each typed character
# Use bunnyctl to set per-profile: bunnyctl speed <profile> 5
# ---------------------------------------------------------------------------

QUACK=/usr/local/bunny/bin/QUACK
ORIG="${QUACK}.orig"

[ -f "$QUACK" ] || { echo "ERROR: $QUACK not found"; exit 1; }

# First run: save original
[ -f "$ORIG" ] || { cp "$QUACK" "$ORIG" && echo "saved original -> $ORIG"; }

# Already patched?
if grep -q BUNNY_CHAR_DELAY "$QUACK"; then
    echo "already patched (BUNNY_CHAR_DELAY already present in $QUACK)"
    exit 0
fi

# Apply patch via embedded Python 2 script
python2 - "$ORIG" "$QUACK" <<'__PY__'
import sys
src, dst = sys.argv[1], sys.argv[2]

with open(src) as f:
    text = f.read()

# Ensure 'import os' is present (QUACK uses time but may not import os)
if 'import os' not in text:
    text = 'import os\n' + text

lines = text.split('\n')
out, done, in_s = [], False, False

for line in lines:
    out.append(line)
    # Track entry into the STRING command block
    if "cmd == 'STRING'" in line:
        in_s = True
    # Inject delay immediately after hidg_write(elements) inside STRING loop
    if in_s and 'hidg_write(elements)' in line and not done:
        p = ' ' * (len(line) - len(line.lstrip()))
        out.append(p + "_d = int(os.environ.get('BUNNY_CHAR_DELAY', '0') or '0')")
        out.append(p + "if _d > 0:")
        out.append(p + "    time.sleep(_d * 0.001)")
        done = True

if not done:
    sys.stderr.write('patch_quack ERROR: STRING/hidg_write block not found in QUACK\n')
    sys.stderr.write('  Is this the expected QUACK version? Check: grep -n hidg_write ' + src + '\n')
    sys.exit(1)

with open(dst, 'w') as f:
    f.write('\n'.join(out))

print('patch applied ok')
__PY__

if [ $? -ne 0 ]; then
    echo "ERROR: python2 patch step failed - QUACK not modified"
    exit 1
fi

chmod +x "$QUACK"
echo "QUACK char-delay patch applied successfully"
echo "Set 'export BUNNY_CHAR_DELAY=<ms>' in a profile to enable per-character sleep."
