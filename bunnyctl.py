#!/usr/bin/env python3
"""
bunnyctl v0.8 - Hak5 Bash Bunny serial control & audit
======================================================
Drives a Bash Bunny over its USB serial console (CDC-ACM) non-interactively:
finds the port, wakes the line (DTR hangup toggle), logs in, and runs commands
or canned workflows, returning clean output.

Requires : Python 3.6+, pyserial   ->  python3 -m pip install --user pyserial
Tested on: Bash Bunny Mark I (OpenSSH 6.7 / Debian 8), macOS + Linux hosts.

Usage (options go AFTER the subcommand):
  bunnyctl.py probe                          detect port, dump what the console returns
  bunnyctl.py gpio                           read switch GPIOs (PA8/PL4/PL3)
  bunnyctl.py exec "uname -a"                run one command, print its output
  bunnyctl.py audit                          read-only inventory / security audit
  bunnyctl.py setup-ssh --pubkey ~/.ssh/bunny.pub [--pubkey ~/.ssh/bunny_rsa.pub]
  --- chameleon flow ---
  bunnyctl.py modes                          list ATTACKMODE tokens for profile scripts
  bunnyctl.py mode                           show current profile (mode.txt)
  bunnyctl.py mode kbd1                      set SW1 (far) profile (no validation)
  bunnyctl.py mode2 kbd2                     set SW2 (middle) profile (no validation)
  bunnyctl.py status                         switch position + both bank profiles
  bunnyctl.py hostid                         how the attached Bunny enumerates on THIS host
  --- profile management ---
  bunnyctl.py profiles                       list profiles on device
  bunnyctl.py profile kbd1                   show a profile script
  bunnyctl.py profile-push kbd1 myscript.sh  push a local script as a named profile
  bunnyctl.py push local.sh /path/on/dev --chmod   push any file to the device (chunked)
  --- identifiers (keyboard or storage) ---
  bunnyctl.py ids kbd1                        show class + VID/PID/SN (line) + MAN/PROD (env)
  bunnyctl.py ids kbd1 --vid 0x413c --pid 0x2113 --man "Dell" --prod "KB216 Wired Keyboard"
  bunnyctl.py ids usb_sandisk --sn "1234ABCD"
      (VID/PID/SN edit the ATTACKMODE line; MAN/PROD edit BUNNY_* env exports -
       needs patch_attackmode.sh applied on the device)
  --- keyboard typing speed ---
  bunnyctl.py patch-quack                     patch QUACK on-device: add BUNNY_CHAR_DELAY support
  bunnyctl.py speed kbd1                      show current char delay for profile
  bunnyctl.py speed kbd1 5                    set char delay to 5 ms (0 = disabled)
  --- file delivery via keyboard ---
  bunnyctl.py type-file secret.bin sw1_deliver          generate + push a file-typing profile
  bunnyctl.py type-file payload.bin sw2_deliver --width 60 --speed 8 --out /tmp/p.bin

Common options: --port --baud --user --password --timeout --no-wake
Port autodetects /dev/cu.usbmodem* (macOS) or /dev/ttyACM* (Linux) when --port omitted.

Chameleon flow (this unit): arming = switch nearest USB (setup / serial / udisk).
Two operational banks, each a dispatcher that reads its own mode file and sources
the named profile:
  switch1 = FAR end -> mode.txt   (self-installs on-device bunnyctl if missing)
  switch2 = MIDDLE  -> mode2.txt
Set a bank's profile in arming mode, flip to that switch, replug into the target.
Both banks draw from the same profiles/ pool. macOS won't bind the Bunny's ECM
ethernet, so SSH must originate from a Linux host.

Profile scripts live at /usr/local/bunny/udisk/profiles/<name>.sh on the device.
mode.txt / mode2.txt hold profile names; the matching dispatcher sources them.

CHANGELOG
  v0.1  initial consolidation (probe + gpio + exec + audit + setup-ssh)
  v0.2  chameleon flow: modes / mode / status / hostid
  v0.3  profile system: profiles / profile / profile-push; mode accepts profile names
  v0.4  second payload bank: mode2 (SW2/mode2.txt); status shows both banks
  v0.5  ids: view/edit ATTACKMODE identifiers (VID/PID/MAN/PROD/SN/class) per profile
  v0.6  chunked base64 push (put_file) - fixes tty MAX_CANON mangling on big files;
        new 'push' command; profile-push now chunked + syncs
  v0.7  ids: MAN/PROD now driven via BUNNY_* env exports (stock ATTACKMODE has no
        PROD_ and uppercases MAN_); VID/PID/SN stay on the ATTACKMODE line.
        Requires the ATTACKMODE env patch (patch_attackmode.sh) on the device.
  v0.8  reliability: disable tty echo + empty prompt after login so run() reads
        clean output (ends the false "device reads ..." warnings from UART-
        corrupted echo markers); mode/mode2 now sync the FAT after writing so the
        dispatcher can't read a stale value (the intermittent "reapply" issue).
  v0.9  patch-quack: idempotent patch for on-device QUACK interpreter to read
        BUNNY_CHAR_DELAY env and sleep between HID keystrokes — fixes dropped
        characters at high typing speed. speed: per-profile char-delay setting.
        type-file: generate + push a complete keyboard file-delivery profile
        (base64 heredoc + sha1sum verify) from a local file in one command.
"""
import sys, time, glob, random, argparse

VERSION = "0.9"

# mode.txt  -> SW1 (far end) active profile; switch1/payload.txt reads it at boot.
# mode2.txt -> SW2 (middle)  active profile; switch2/payload.txt reads it at boot.
MODE_PATH     = "/usr/local/bunny/udisk/mode.txt"
MODE2_PATH    = "/usr/local/bunny/udisk/mode2.txt"
PROFILES_PATH = "/usr/local/bunny/udisk/profiles"

# Reference tokens for writing profile scripts (mouse NOT supported on stock fw).
MODE_TOKENS = {
    "HID": "keyboard",
    "STORAGE": "mass storage",
    "SERIAL": "serial console",
    "ECM_ETHERNET": "network adapter (macOS/Linux)",
    "RNDIS_ETHERNET": "network adapter (Windows)",
    "AUTO_ETHERNET": "RNDIS with ECM fallback",
}

# Read-only audit checklist: (label, shell command)
AUDIT = [
    ("uname",        "uname -a"),
    ("version",      "cat /version.txt 2>/dev/null; echo ---; ls /usr/local/bunny 2>/dev/null | tr '\\n' ' '"),
    ("clock",        "date; cat /proc/uptime 2>/dev/null"),
    ("payload_tree", "ls -laR /usr/local/bunny/udisk/payloads 2>/dev/null"),
    ("payloads",     "for f in $(find /usr/local/bunny/udisk/payloads -name payload.txt 2>/dev/null); do echo \"# $f\"; cat \"$f\"; echo; done"),
    ("loot",         "ls -laR /usr/local/bunny/udisk/loot 2>/dev/null | head -80"),
    ("bigfiles",     "find /usr/local/bunny/udisk -type f -size +20k 2>/dev/null -exec ls -lh {} \\;"),
    ("persist",      "cat /etc/rc.local 2>/dev/null; echo ---CRON---; crontab -l 2>/dev/null; ls -la /etc/cron* 2>/dev/null"),
    ("sshkeys",      "ls -la /root/.ssh 2>/dev/null; echo ---AUTH---; cat /root/.ssh/authorized_keys 2>/dev/null"),
    ("accounts",     "cat /etc/passwd"),
    ("procs",        "ps w 2>/dev/null || ps 2>/dev/null"),
    ("listen",       "netstat -tlnp 2>/dev/null || netstat -tln 2>/dev/null"),
    ("gpio",         "for p in PA8 PL4 PL3; do printf '%s=' $p; cat /sys/class/gpio_sw/$p/data 2>/dev/null; done; echo"),
    ("profiles",     "ls %s 2>/dev/null | sed 's/\\.sh$//' | sort" % PROFILES_PATH),
    ("desc_ko",      "(strings /usr/local/bunny/lib/bunny_gadget.ko 2>/dev/null || tr -c '[:print:]' '\\n' < /usr/local/bunny/lib/bunny_gadget.ko) | grep -iE 'skylab|chicony|keyboard|manufacturer' | head"),
    ("dmesg",        "dmesg 2>/dev/null | grep -iE 'gadget|switch|payload|g_ether|cdc|udc' | tail -20"),
]


def find_port():
    for pat in ("/dev/cu.usbmodem*", "/dev/ttyACM*", "/dev/ttyUSB*", "/dev/tty.usbmodem*"):
        m = sorted(glob.glob(pat))
        if m:
            return m[0]
    return None


class Bunny:
    def __init__(self, port, baud=115200, user="root", password="hak5bunny", timeout=8, wake=True):
        self.port, self.baud = port, baud
        self.user, self.pw = user, password
        self.timeout, self.wake = timeout, wake
        self.ser = None

    def open(self):
        try:
            import serial
        except ImportError:
            sys.exit("pyserial not installed. Run: python3 -m pip install --user pyserial")
        self.ser = serial.Serial(self.port, self.baud, timeout=0.2)
        if self.wake:
            try:
                self.ser.dtr = False; self.ser.rts = False; time.sleep(0.7)
                self.ser.dtr = True;  self.ser.rts = True
            except Exception:
                pass
            time.sleep(1.2)
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        return self

    def _read_until(self, marker, timeout):
        end = time.time() + timeout
        buf = b""
        mb = marker.encode() if marker else None
        while time.time() < end:
            c = self.ser.read(4096)
            if c:
                buf += c
                if mb and mb in buf:
                    break
            else:
                time.sleep(0.03)
        return buf

    def _send(self, s):
        self.ser.write((s + "\r").encode())
        self.ser.flush()

    @staticmethod
    def _mark(name="x"):
        n = random.randint(1000, 9999)
        return ("END_%s_%d" % (name, n), "EN''D_%s_%d" % (name, n))

    def login(self):
        self.ser.write(b"\x03\r"); self.ser.flush(); time.sleep(0.4)
        pre = self._read_until(None, 2.0)
        low = pre.lower()
        if b"login:" in low:
            self._send(self.user); self._read_until("assword", 3)
            self._send(self.pw);   self._read_until("# ", 6)
        elif b"assword" in low:
            self._send(self.pw);   self._read_until("# ", 6)
        return pre

    def ready(self):
        lit, typed = self._mark("RDY")
        self._send("echo " + typed)
        ok = lit.encode() in self._read_until(lit, 5)
        if ok:
            # Silence command echo + prompt so run() reads clean output. Without
            # this, run() must strip the echoed command by matching its marker,
            # which UART noise corrupts -> false "device reads ..." warnings.
            self._send("stty -echo 2>/dev/null; export PS1='' PS2=''")
            self._read_until(None, 1.0)
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass
        return ok

    def run(self, cmd, timeout=None):
        lit, typed = self._mark()
        self._send(cmd + " ; echo " + typed)
        raw = self._read_until(lit, timeout or self.timeout).decode(errors="replace")
        if typed in raw:
            raw = raw.split(typed, 1)[1]
        i = raw.rfind(lit)
        if i != -1:
            raw = raw[:i]
        return raw.strip("\r\n")

    def put_file(self, dest, data, chunk=180, make_exec=False):
        """Write bytes to dest on the device via chunked base64 append.
        Each serial line stays < ~255 chars to respect the tty canonical-mode
        line limit (MAX_CANON) - long single-line writes get mangled otherwise."""
        import base64
        b64 = base64.b64encode(data).decode()
        tmp = "/tmp/.bctl_push.b64"
        self.run(": > %s" % tmp)
        for i in range(0, len(b64), chunk):
            self.run("printf %%s %s >> %s" % (b64[i:i+chunk], tmp))
        self.run("base64 -d %s > %s" % (tmp, dest))
        self.run("rm -f %s" % tmp)
        if make_exec:
            self.run("chmod +x %s" % dest)
        try:
            return int(self.run("wc -c < %s 2>/dev/null" % dest).strip() or "0")
        except ValueError:
            return -1

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


def connect(args):
    port = args.port or find_port()
    if not port:
        sys.exit("No serial port found - specify --port /dev/...")
    b = Bunny(port, args.baud, args.user, args.password, args.timeout, not args.no_wake)
    try:
        b.open()
    except Exception as e:
        sys.exit("open failed on %s: %r" % (port, e))
    return b, port


def _ready_or_die(b, port):
    b.login()
    if not b.ready():
        b.close()
        sys.exit("shell not responding on %s (try replugging / --no-wake off)" % port)


def cmd_probe(args):
    b, port = connect(args)
    b.ser.write(b"\x03\r"); b.ser.flush(); time.sleep(0.4)
    out = b._read_until(None, 4)
    print("port    :", port)
    print("rx_bytes:", len(out))
    print("tail    :", repr(out[-600:]))
    b.close()


def cmd_gpio(args):
    b, port = connect(args); _ready_or_die(b, port)
    print(b.run("for p in PA8 PL4 PL3; do printf '%s=' $p; cat /sys/class/gpio_sw/$p/data 2>/dev/null; done; echo"))
    print("(PA8=switch1  PL4=switch2  PL3=switch3/arming ; value 0 = that position selected)")
    b.close()


def cmd_exec(args):
    b, port = connect(args); _ready_or_die(b, port)
    print(b.run(args.command, args.timeout))
    b.close()


def cmd_audit(args):
    b, port = connect(args); _ready_or_die(b, port)
    print("# bunnyctl v%s audit   port=%s\n" % (VERSION, port))
    for name, c in AUDIT:
        print("===== %s =====" % name)
        print(b.run(c))
        print()
    b.close()


def cmd_setup_ssh(args):
    keys = []
    for pf in (args.pubkey or []):
        try:
            with open(pf) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        keys.append(line)
        except OSError as e:
            sys.exit("cannot read %s: %r" % (pf, e))
    if not keys:
        sys.exit("provide at least one --pubkey FILE")
    b, port = connect(args); _ready_or_die(b, port)
    print(b.run("mkdir -p /root/.ssh && chmod 700 /root/.ssh && echo MKOK"))
    for k in keys:
        body = k.split()[1] if len(k.split()) > 1 else k
        print(b.run("grep -qF '%s' /root/.ssh/authorized_keys 2>/dev/null || echo '%s' >> /root/.ssh/authorized_keys; echo KEY_OK" % (body, k)))
    print(b.run("chmod 600 /root/.ssh/authorized_keys; wc -l /root/.ssh/authorized_keys"))
    ip = args.ip
    ipaddr = ip.split("/")[0]
    print(b.run("ip addr add %s dev eth0 2>/dev/null; ip link set eth0 up; ip -o addr show eth0" % ip))
    print(b.run("grep -q %s /etc/rc.local 2>/dev/null || printf '#!/bin/sh -e\\nip addr add %s dev eth0 2>/dev/null || true\\nip link set eth0 up 2>/dev/null || true\\nexit 0\\n' > /etc/rc.local; chmod +x /etc/rc.local; cat /etc/rc.local" % (ipaddr, ip)))
    print(b.run("grep -iE '^(PermitRootLogin|PubkeyAuthentication)' /etc/ssh/sshd_config"))
    print("\nDone. From a Linux host on the gadget net:  ssh root@%s" % ipaddr)
    b.close()


# ---- chameleon flow (v0.2+) --------------------------------------------------

def cmd_modes(args):
    print("ATTACKMODE tokens for use in profile scripts (mouse NOT supported on stock fw):")
    for k, d in MODE_TOKENS.items():
        print("  %-15s %s" % (k, d))
    print()
    print("Combine tokens on one ATTACKMODE line, e.g.:")
    print("  ATTACKMODE HID STORAGE")
    print("  ATTACKMODE STORAGE VID_0x0781 PID_0x5567 MAN_\"SanDisk\" PROD_\"Cruzer Blade\"")
    print()
    print("Profile names (set with 'bunnyctl mode <name>'):")
    print("  default       HID STORAGE, no keystrokes")
    print("  kbd1          HID, runs payload#1 QUACK sequence")
    print("  kbd2          HID, runs payload#2 QUACK sequence")
    print("  usb_sandisk   STORAGE only, enumerates as SanDisk Cruzer Blade")
    print()
    print("Use 'bunnyctl profiles' to list profiles actually on the device.")


def _mode_io(args, path, bank_label, switch_hint):
    """Shared get/set for a bank's mode file (no token validation - value is a profile name)."""
    value = " ".join(args.value).strip() if args.value else None
    b, port = connect(args); _ready_or_die(b, port)
    if value is None:
        cur = b.run("cat %s 2>/dev/null" % path).strip()
        b.close()
        print("%s profile: %s" % (bank_label, cur if cur else "(empty -> default)"))
        return
    b.run("printf '%%s\\n' '%s' > %s" % (value, path))
    b.run("sync")                       # flush FAT so the dispatcher can't read a stale value
    back = b.run("cat %s 2>/dev/null" % path).strip()
    b.close()
    if back == value:
        print("%s profile set -> %s   (flip to %s and replug to use it)" % (bank_label, back, switch_hint))
    else:
        print("WARNING: wrote '%s' but device reads '%s' - retry the command" % (value, back))


def cmd_mode(args):
    """SW1 (far end) profile in mode.txt."""
    _mode_io(args, MODE_PATH, "SW1", "switch1 / FAR end")


def cmd_mode2(args):
    """SW2 (middle) profile in mode2.txt."""
    _mode_io(args, MODE2_PATH, "SW2", "switch2 / MIDDLE")


def cmd_status(args):
    import re
    b, port = connect(args); _ready_or_die(b, port)
    gpio    = b.run("for p in PA8 PL4 PL3; do printf '%s=' $p; cat /sys/class/gpio_sw/$p/data 2>/dev/null; done")
    mode    = b.run("cat %s 2>/dev/null" % MODE_PATH).strip()
    mode2   = b.run("cat %s 2>/dev/null" % MODE2_PATH).strip()
    profile = ""
    if mode:
        profile = b.run("cat %s/%s.sh 2>/dev/null || echo PROFILE_NOT_FOUND" % (PROFILES_PATH, mode)).strip()
    profile2 = ""
    if mode2:
        profile2 = b.run("cat %s/%s.sh 2>/dev/null || echo PROFILE_NOT_FOUND" % (PROFILES_PATH, mode2)).strip()
    b.close()

    def g(k):
        mm = re.search(k + r"=\s*([01])", gpio)
        return mm.group(1) if mm else "?"
    pa8, pl4, pl3 = g("PA8"), g("PL4"), g("PL3")
    if pa8 == "0":
        pos = "switch1  (FAR end)   -> chameleon"
    elif pl4 == "0":
        pos = "switch2  (MIDDLE)    -> fallback"
    elif pl3 == "0":
        pos = "arming   (USB side)  -> setup / serial"
    else:
        pos = "unknown (no line reads 0)"
    print("port          : %s" % port)
    print("switch now    : %s" % pos)
    print("gpio          : PA8=%s PL4=%s PL3=%s" % (pa8, pl4, pl3))
    print("switch1 (far) : profile = %s" % (mode if mode else "(empty -> default)"))
    if profile and "PROFILE_NOT_FOUND" not in profile:
        print("  profile script:")
        for line in profile.splitlines():
            print("    %s" % line)
    elif mode:
        print("  WARNING: profile '%s' not found on device (run: bunnyctl profiles)" % mode)
    print("switch2 (mid) : profile = %s" % (mode2 if mode2 else "(empty -> default)"))
    if profile2 and "PROFILE_NOT_FOUND" not in profile2:
        print("  profile script:")
        for line in profile2.splitlines():
            print("    %s" % line)
    elif mode2:
        print("  WARNING: profile '%s' not found on device (run: bunnyctl profiles)" % mode2)


def cmd_hostid(args):
    import subprocess, platform
    s = platform.system()
    try:
        if s == "Darwin":
            out = subprocess.check_output(["system_profiler", "SPUSBDataType"], text=True, stderr=subprocess.DEVNULL)
        elif s == "Linux":
            out = subprocess.check_output(["lsusb", "-v"], text=True, stderr=subprocess.DEVNULL)
        else:
            print("hostid: unsupported host OS: %s" % s); return
    except Exception as e:
        print("hostid failed (%r) - is the tool available on PATH?" % e); return
    lines = out.splitlines()
    keys = ("f000", "skylab", "gadget serial", "chicony")
    hits = [i for i, l in enumerate(lines) if any(k in l.lower() for k in keys)]
    if not hits:
        print("No Bash Bunny (VID f000 / Skylab) visible on this host's USB right now.")
        return
    lo, hi = max(0, hits[0] - 8), min(len(lines), hits[-1] + 4)
    print("\n".join(l.rstrip() for l in lines[lo:hi]))


# ---- profile management (v0.3) -----------------------------------------------

def cmd_profiles(args):
    b, port = connect(args); _ready_or_die(b, port)
    out = b.run("ls %s 2>/dev/null | sed 's/\\.sh$//' | sort" % PROFILES_PATH)
    b.close()
    if out.strip():
        print("profiles on device:")
        for p in out.strip().splitlines():
            print("  %s" % p.strip())
    else:
        print("no profiles found at %s" % PROFILES_PATH)


def cmd_profile(args):
    b, port = connect(args); _ready_or_die(b, port)
    out = b.run("cat %s/%s.sh 2>/dev/null || echo __NOT_FOUND__" % (PROFILES_PATH, args.name))
    b.close()
    if "__NOT_FOUND__" in out:
        sys.exit("profile not found: %s   (run: bunnyctl profiles)" % args.name)
    print(out)


def cmd_profile_push(args):
    if args.file:
        try:
            with open(args.file, "rb") as f:
                content = f.read()
        except OSError as e:
            sys.exit("cannot read %s: %r" % (args.file, e))
    else:
        content = sys.stdin.buffer.read()
    b, port = connect(args); _ready_or_die(b, port)
    b.run("mkdir -p %s" % PROFILES_PATH)
    dest = "%s/%s.sh" % (PROFILES_PATH, args.name)
    size = b.put_file(dest, content, make_exec=True)
    b.run("sync")
    b.close()
    print("pushed '%s' -> %s  (%s bytes on device)" % (args.name, dest, size))


def cmd_push(args):
    """Push an arbitrary local file to an absolute path on the device (chunked)."""
    try:
        with open(args.file, "rb") as f:
            content = f.read()
    except OSError as e:
        sys.exit("cannot read %s: %r" % (args.file, e))
    b, port = connect(args); _ready_or_die(b, port)
    parent = args.dest.rsplit("/", 1)[0]
    if parent and parent != args.dest:
        b.run("mkdir -p %s" % parent)
    size = b.put_file(args.dest, content, make_exec=args.chmod)
    b.run("sync")
    b.close()
    print("pushed %s -> %s  (%s bytes on device%s)" %
          (args.file, args.dest, size, ", +x" if args.chmod else ""))


# ---- identifier editing (v0.7) -----------------------------------------------
# VID/PID/SN live on the ATTACKMODE line (stock firmware honors them there).
# MAN/PROD must go through env vars (BUNNY_IMANUFACTURER / BUNNY_IPRODUCT):
# stock ATTACKMODE has NO PROD_ handling and uppercases MAN_, and the env path
# (via the patch_attackmode.sh patch) preserves exact case and embedded spaces.

ATK_KEYS = ("VID", "PID", "SN")          # honored as ATTACKMODE args
ENV_KEYS = {"MAN": "BUNNY_IMANUFACTURER", "PROD": "BUNNY_IPRODUCT"}


def _parse_attackmode(line):
    """(indent, modes[list], overrides{dict}) from an ATTACKMODE line.
    Collects VID/PID/SN plus any legacy MAN/PROD args still present."""
    import shlex
    indent = line[:len(line) - len(line.lstrip())]
    toks = shlex.split(line.strip())          # strips KEY_"a b" quoting for us
    modes, ov = [], {}
    for t in toks[1:]:                         # toks[0] == "ATTACKMODE"
        for k in ("VID", "PID", "SN", "MAN", "PROD"):
            if t.startswith(k + "_"):
                ov[k] = t[len(k) + 1:]
                break
        else:
            modes.append(t)
    return indent, modes, ov


def _build_attackmode(indent, modes, ov):
    """Rebuild an ATTACKMODE line. Only VID/PID/SN go here; MAN/PROD are env."""
    parts = ["ATTACKMODE"] + modes
    for k in ATK_KEYS:
        v = ov.get(k)
        if v:
            parts.append('%s_"%s"' % (k, v) if k == "SN" else "%s_%s" % (k, v))
    return indent + " ".join(parts)


def _parse_env(lines):
    """Current BUNNY_IPRODUCT / BUNNY_IMANUFACTURER values from export lines."""
    import re
    env = {}
    for l in lines:
        m = re.match(r'\s*export\s+(BUNNY_IPRODUCT|BUNNY_IMANUFACTURER)=(.*)$', l)
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"')
    return env


def cmd_ids(args):
    import re
    b, port = connect(args); _ready_or_die(b, port)
    path = "%s/%s.sh" % (PROFILES_PATH, args.name)
    text = b.run("cat %s 2>/dev/null || echo __NOT_FOUND__" % path)
    if "__NOT_FOUND__" in text:
        b.close(); sys.exit("profile not found: %s   (run: bunnyctl profiles)" % args.name)
    lines = text.splitlines()
    idx = next((i for i, l in enumerate(lines) if l.strip().startswith("ATTACKMODE")), None)
    if idx is None:
        b.close(); sys.exit("no ATTACKMODE line in profile '%s'" % args.name)
    indent, modes, ov = _parse_attackmode(lines[idx])
    env = _parse_env(lines)
    cur_man  = env.get("BUNNY_IMANUFACTURER", ov.get("MAN", ""))
    cur_prod = env.get("BUNNY_IPRODUCT",      ov.get("PROD", ""))

    atk_over = {"VID": args.vid, "PID": args.pid, "SN": args.sn}
    changing = (args.mode is not None or args.man is not None or args.prod is not None
                or any(v is not None for v in atk_over.values()))
    if not changing:
        b.close()
        print("profile : %s" % args.name)
        print("class   : %s" % (" ".join(modes) if modes else "(none)"))
        print("VID     : %s" % ov.get("VID", "(default)"))
        print("PID     : %s" % ov.get("PID", "(default)"))
        print("SN      : %s" % ov.get("SN", "(default)"))
        print("MAN     : %s  (env)" % (cur_man  or "(default)"))
        print("PROD    : %s  (env)" % (cur_prod or "(default)"))
        return

    # ATTACKMODE-line edits (class + VID/PID/SN)
    if args.mode is not None:
        modes = args.mode.split()
    for k, v in atk_over.items():
        if v is not None:
            ov[k] = v
    old_atk = lines[idx]
    new_atk = _build_attackmode(indent, modes, ov)
    lines[idx] = new_atk

    # env edits (MAN/PROD) - upsert export line before ATTACKMODE
    env_targets = {}
    if args.man is not None:
        env_targets["BUNNY_IMANUFACTURER"] = args.man
    if args.prod is not None:
        env_targets["BUNNY_IPRODUCT"] = args.prod
    for var, val in env_targets.items():
        newline = 'export %s="%s"' % (var, val)
        for i, l in enumerate(lines):
            if re.match(r'\s*export\s+%s=' % var, l):
                lines[i] = newline
                break
        else:
            aidx = next((i for i, l in enumerate(lines) if l.strip().startswith("ATTACKMODE")), len(lines))
            lines.insert(aidx, newline)

    new_text = "\n".join(lines) + "\n"
    b.put_file(path, new_text.encode(), make_exec=True)
    b.run("sync")
    b.close()
    print("profile : %s" % args.name)
    if new_atk != old_atk:
        print("- %s" % old_atk.strip())
        print("+ %s" % new_atk.strip())
    for var, val in env_targets.items():
        print('+ export %s="%s"' % (var, val))
    print("(flip to the matching switch and replug to use it)")


# ---- QUACK char-delay patch (v0.9) ------------------------------------------
# Embedded shell script pushed to the device by 'patch-quack'. Idempotent:
# backs up /usr/local/bunny/bin/QUACK to QUACK.orig, then patches the STRING
# command loop to honour the BUNNY_CHAR_DELAY env var (ms per keystroke).

QUACK_PATCH = r"""#!/bin/sh
# patch_quack.sh - inject BUNNY_CHAR_DELAY per-character HID delay into QUACK
# Idempotent: backs up QUACK to QUACK.orig; always rebuilds patched copy from it.
QUACK=/usr/local/bunny/bin/QUACK
ORIG="${QUACK}.orig"
[ -f "$QUACK" ] || { echo "ERROR: $QUACK not found"; exit 1; }
[ -f "$ORIG" ] || { cp "$QUACK" "$ORIG" && echo "saved original -> $ORIG"; }
if grep -q BUNNY_CHAR_DELAY "$QUACK"; then
    echo "already patched"
    exit 0
fi
python2 - "$ORIG" "$QUACK" <<'__PY__'
import sys
src, dst = sys.argv[1], sys.argv[2]
with open(src) as f:
    text = f.read()
if 'import os' not in text:
    text = 'import os\n' + text
lines = text.split('\n')
out, done, in_s = [], False, False
for line in lines:
    out.append(line)
    if "cmd == 'STRING'" in line:
        in_s = True
    if in_s and 'hidg_write(elements)' in line and not done:
        p = ' ' * (len(line) - len(line.lstrip()))
        out.append(p + "_d = int(os.environ.get('BUNNY_CHAR_DELAY', '0') or '0')")
        out.append(p + "if _d > 0:")
        out.append(p + "    time.sleep(_d * 0.001)")
        done = True
if not done:
    sys.stderr.write('patch_quack ERROR: STRING/hidg_write block not found in QUACK\n')
    sys.exit(1)
with open(dst, 'w') as f:
    f.write('\n'.join(out))
print('patch applied ok')
__PY__
[ $? -eq 0 ] || { echo "python2 patch step failed"; exit 1; }
chmod +x "$QUACK"
echo "QUACK char-delay patch applied successfully"
"""


def cmd_patch_quack(args):
    """Push and apply the QUACK char-delay patch on the device.
    Backs up /usr/local/bunny/bin/QUACK to QUACK.orig (idempotent) and injects
    a BUNNY_CHAR_DELAY env read + time.sleep() after each hidg_write() in the
    STRING command loop.  Set export BUNNY_CHAR_DELAY=<ms> in any profile to
    activate per-character sleep; 0 or unset = no delay (original behaviour)."""
    b, port = connect(args); _ready_or_die(b, port)
    tmp = "/tmp/patch_quack.sh"
    b.put_file(tmp, QUACK_PATCH.encode(), make_exec=True)
    out = b.run("sh %s; rm -f %s" % (tmp, tmp), timeout=30)
    b.close()
    print(out)
    if "applied" in out or "already patched" in out:
        print()
        print("Use 'bunnyctl speed <profile> <ms>' to set char delay in a profile (e.g. 5 ms).")
        print("Use 'bunnyctl speed <profile>'      to read the current setting.")


def cmd_speed(args):
    """Get/set BUNNY_CHAR_DELAY (per-keystroke sleep, ms) in a profile.
    Reads or upserts the 'export BUNNY_CHAR_DELAY=<ms>' line in the profile
    script.  Requires the QUACK patch on-device (run 'bunnyctl patch-quack')."""
    import re
    b, port = connect(args); _ready_or_die(b, port)
    path = "%s/%s.sh" % (PROFILES_PATH, args.name)
    text = b.run("cat %s 2>/dev/null || echo __NOT_FOUND__" % path)
    if "__NOT_FOUND__" in text:
        b.close(); sys.exit("profile not found: %s   (run: bunnyctl profiles)" % args.name)
    lines = text.splitlines()
    ms = args.ms

    if ms is None:
        b.close()
        cur = None
        for l in lines:
            m = re.match(r'\s*export\s+BUNNY_CHAR_DELAY=(\S+)', l)
            if m:
                cur = m.group(1); break
        if cur:
            print("profile '%s'  BUNNY_CHAR_DELAY = %s ms" % (args.name, cur))
        else:
            print("profile '%s'  BUNNY_CHAR_DELAY = not set (0 ms / disabled)" % args.name)
        return

    newline = "export BUNNY_CHAR_DELAY=%s" % ms
    found = False
    for i, l in enumerate(lines):
        if re.match(r'\s*export\s+BUNNY_CHAR_DELAY=', l):
            lines[i] = newline; found = True; break
    if not found:
        aidx = next((i for i, l in enumerate(lines) if l.strip().startswith("ATTACKMODE")), 0)
        lines.insert(aidx, newline)

    new_text = "\n".join(lines) + "\n"
    b.put_file(path, new_text.encode(), make_exec=True)
    b.run("sync")
    b.close()
    print("profile '%s'  BUNNY_CHAR_DELAY -> %s ms" % (args.name, ms))
    if ms == "0":
        print("(0 ms = no per-character delay; patch still present but inactive)")


def cmd_type_file(args):
    """Generate and push a keyboard file-delivery profile from a local file.
    Creates on the device:
      profiles/<name>.ducky  - Ducky Script: heredoc, base64 chunks+ENTER, decode, sha1sum
      profiles/<name>.sh     - shell profile (BUNNY_CHAR_DELAY, ATTACKMODE HID, QUACK)
    When loaded and flipped, the Bunny types the file to whatever terminal is
    open on the target.  Run 'bunnyctl patch-quack' first if not done."""
    import base64, hashlib
    import os as _os

    b64_width  = args.width   # b64 chars per typed line
    char_delay = args.speed   # ms written to BUNNY_CHAR_DELAY in profile
    cmd_delay  = args.delay   # Ducky DEFAULT_DELAY between commands (ms)

    try:
        with open(args.file, "rb") as f:
            data = f.read()
    except OSError as e:
        sys.exit("cannot read %s: %r" % (args.file, e))

    b64    = base64.b64encode(data).decode()
    sha1   = hashlib.sha1(data).hexdigest()
    fname  = _os.path.basename(args.file)
    remote_out = args.out if args.out else "/tmp/%s" % fname
    b64_tmp    = remote_out + ".b64"
    chunks     = [b64[i:i+b64_width] for i in range(0, len(b64), b64_width)]

    # ---- Ducky Script --------------------------------------------------------
    dk = []
    dk.append("REM bunnyctl type-file: %s -> %s" % (fname, remote_out))
    dk.append("REM size=%d  sha1=%s" % (len(data), sha1))
    dk.append("REM chunks=%d  width=%d  char_delay=%d ms" % (len(chunks), b64_width, char_delay))
    dk.append("")
    dk.append("DEFAULT_DELAY %d" % cmd_delay)
    dk.append("DELAY 1000")
    dk.append("")
    dk.append("REM open heredoc on target shell")
    dk.append("STRING cat << 'BCTL_EOF' > %s" % b64_tmp)
    dk.append("ENTER")
    dk.append("")
    dk.append("REM base64 payload (%d chunks x %d chars)" % (len(chunks), b64_width))
    for chunk in chunks:
        dk.append("STRING %s" % chunk)
        dk.append("ENTER")
    dk.append("")
    dk.append("REM close heredoc + decode")
    dk.append("STRING BCTL_EOF")
    dk.append("ENTER")
    dk.append("DELAY 500")
    dk.append("STRING base64 -d %s > %s" % (b64_tmp, remote_out))
    dk.append("ENTER")
    dk.append("DELAY 500")
    dk.append("REM verify integrity")
    dk.append("STRING echo '%s  %s' | sha1sum -c -" % (sha1, remote_out))
    dk.append("ENTER")
    dk.append("DELAY 300")
    dk.append("STRING rm -f %s" % b64_tmp)
    dk.append("ENTER")
    ducky_text = "\n".join(dk) + "\n"

    # ---- Profile shell script ------------------------------------------------
    ducky_path = "%s/%s.ducky" % (PROFILES_PATH, args.name)
    sh_lines = [
        "#!/bin/sh",
        "# bunnyctl type-file profile: %s" % args.name,
        "# delivers: %s (%d bytes, sha1=%s)" % (fname, len(data), sha1),
        "export BUNNY_CHAR_DELAY=%d" % char_delay,
        "ATTACKMODE HID",
        "LED ATTACK",
        "QUACK %s" % ducky_path,
        "LED FINISH",
    ]
    sh_text = "\n".join(sh_lines) + "\n"

    # ---- Push to device ------------------------------------------------------
    b, port = connect(args); _ready_or_die(b, port)
    b.run("mkdir -p %s" % PROFILES_PATH)

    print("pushing ducky script (%d chunks) ..." % len(chunks))
    sz_dk = b.put_file(ducky_path, ducky_text.encode(), make_exec=False)

    sh_path = "%s/%s.sh" % (PROFILES_PATH, args.name)
    print("pushing profile ...")
    sz_sh = b.put_file(sh_path, sh_text.encode(), make_exec=True)
    b.run("sync")
    b.close()

    print()
    print("profile  : %s" % args.name)
    print("ducky    : %s  (%s bytes on device)" % (ducky_path, sz_dk))
    print("script   : %s  (%s bytes on device)" % (sh_path, sz_sh))
    print("delivers : %s  (%d bytes)" % (fname, len(data)))
    print("sha1     : %s" % sha1)
    print("chunks   : %d x %d chars  char_delay=%d ms" % (len(chunks), b64_width, char_delay))
    print()
    print("Load:   bunnyctl mode <name>   OR   bunnyctl mode2 <name>" )
    print("        then flip switch and replug into target (target needs open terminal)")
    print("Verify: echo '%s  %s' | sha1sum -c -" % (sha1, remote_out))


# ---- arg parser + dispatch ---------------------------------------------------

def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port",     help="serial device (default: autodetect)")
    common.add_argument("--baud",     type=int, default=115200)
    common.add_argument("--user",     default="root")
    common.add_argument("--password", default="hak5bunny")
    common.add_argument("--timeout",  type=int, default=8, help="per-command read timeout (s)")
    common.add_argument("--no-wake",  action="store_true", help="skip the DTR hangup toggle")

    p = argparse.ArgumentParser(prog="bunnyctl", description="Bash Bunny serial control & audit (v%s)" % VERSION)
    p.add_argument("--version", action="version", version="bunnyctl " + VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe",    parents=[common], help="detect port and dump console output")
    sub.add_parser("gpio",     parents=[common], help="read switch-position GPIOs")
    sub.add_parser("audit",    parents=[common], help="read-only inventory/security audit")

    pe = sub.add_parser("exec", parents=[common], help="run one shell command on the Bunny")
    pe.add_argument("command")

    ps = sub.add_parser("setup-ssh", parents=[common], help="install pubkeys + eth0 up/persist")
    ps.add_argument("--pubkey", action="append", help="public key file (repeatable)")
    ps.add_argument("--ip", default="172.16.64.1/24", help="eth0 CIDR on the Bunny")

    sub.add_parser("modes",   help="list ATTACKMODE tokens + built-in profiles (no device needed)")

    pm = sub.add_parser("mode", parents=[common], help="get/set SW1 (far) profile in mode.txt")
    pm.add_argument("value", nargs="*", help="profile name (e.g. kbd1); omit to read current")

    pm2 = sub.add_parser("mode2", parents=[common], help="get/set SW2 (middle) profile in mode2.txt")
    pm2.add_argument("value", nargs="*", help="profile name (e.g. kbd2); omit to read current")

    sub.add_parser("status",  parents=[common], help="switch position + both bank profiles")
    sub.add_parser("hostid",  help="how the attached Bunny enumerates on THIS host (no serial)")

    sub.add_parser("profiles", parents=[common], help="list profiles on device")

    pp = sub.add_parser("profile", parents=[common], help="show a profile script from device")
    pp.add_argument("name", help="profile name (without .sh extension)")

    ppush = sub.add_parser("profile-push", parents=[common],
                           help="push a local script as a named profile on device")
    ppush.add_argument("name", help="profile name (without .sh)")
    ppush.add_argument("file", nargs="?", help="local .sh file to push (default: stdin)")

    ppush2 = sub.add_parser("push", parents=[common],
                            help="push any local file to an absolute device path (chunked)")
    ppush2.add_argument("file", help="local file to push")
    ppush2.add_argument("dest", help="absolute destination path on device")
    ppush2.add_argument("--chmod", action="store_true", help="chmod +x the pushed file")

    pids = sub.add_parser("ids", parents=[common],
                          help="view/edit ATTACKMODE identifiers of a profile")
    pids.add_argument("name", help="profile name (without .sh)")
    pids.add_argument("--vid",  help="USB vendor id, e.g. 0x0781")
    pids.add_argument("--pid",  help="USB product id, e.g. 0x5567")
    pids.add_argument("--man",  help="iManufacturer string")
    pids.add_argument("--prod", help="iProduct string")
    pids.add_argument("--sn",   help="iSerial string")
    pids.add_argument("--mode", help="class token(s), e.g. 'HID' or 'HID STORAGE'")

    sub.add_parser("patch-quack", parents=[common],
                   help="patch QUACK on-device to honour BUNNY_CHAR_DELAY (idempotent)")

    pspd = sub.add_parser("speed", parents=[common],
                          help="get/set BUNNY_CHAR_DELAY (ms/char) for a profile")
    pspd.add_argument("name", help="profile name (without .sh)")
    pspd.add_argument("ms",   nargs="?",
                      help="delay in ms (0 = disabled); omit to read current value")

    ptf = sub.add_parser("type-file", parents=[common],
                         help="generate + push a keyboard file-delivery profile")
    ptf.add_argument("file", help="local file to deliver via keyboard typing")
    ptf.add_argument("name", help="profile name (without .sh)")
    ptf.add_argument("--width", type=int, default=76,
                     help="base64 chars per typed line (default 76; smaller = more visual checkpoints)")
    ptf.add_argument("--speed", type=int, default=5,
                     help="per-char delay ms written to BUNNY_CHAR_DELAY (default 5; 0 = off)")
    ptf.add_argument("--delay", type=int, default=200,
                     help="Ducky DEFAULT_DELAY between commands in ms (default 200)")
    ptf.add_argument("--out",   help="destination path on target (default /tmp/<filename>)")

    a = p.parse_args()
    dispatch = {
        "probe":        cmd_probe,
        "gpio":         cmd_gpio,
        "audit":        cmd_audit,
        "exec":         cmd_exec,
        "setup-ssh":    cmd_setup_ssh,
        "modes":        cmd_modes,
        "mode":         cmd_mode,
        "mode2":        cmd_mode2,
        "status":       cmd_status,
        "hostid":       cmd_hostid,
        "profiles":     cmd_profiles,
        "profile":      cmd_profile,
        "profile-push": cmd_profile_push,
        "push":         cmd_push,
        "ids":          cmd_ids,
        "patch-quack":  cmd_patch_quack,
        "speed":        cmd_speed,
        "type-file":    cmd_type_file,
    }
    dispatch[a.cmd](a)


if __name__ == "__main__":
    main()
