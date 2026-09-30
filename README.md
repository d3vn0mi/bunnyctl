# bunnyctl — Bash Bunny chameleon profile system

A profile-driven "chameleon" workflow for the **Hak5 Bash Bunny Mark I**, plus
two control tools:

- **`bunnyctl.py`** — host-side serial controller (macOS/Linux). Finds the port,
  wakes the CDC-ACM console, logs in, and runs commands / audits / profile ops
  non-interactively.
- **`bunnyctl.sh`** — on-device helper. Runs *on* the Bunny and manipulates the
  udisk files directly. No serial, no host required.

The idea: decide **what the Bunny will pretend to be** while it's in arming
mode, then flip the switch and plug into the target. The active USB personality
is selected by a named **profile** rather than by editing payloads each time.

Two operational **banks** — one per switch position — each pick their own
profile from a **shared pool**:

| Bank | Switch          | Mode file   | Default fallback if profile missing |
|------|-----------------|-------------|-------------------------------------|
| SW1  | far end         | `mode.txt`  | `ATTACKMODE HID STORAGE`            |
| SW2  | middle          | `mode2.txt` | `ATTACKMODE STORAGE`                |

---

## How it works

The Bunny's payload directory (`/usr/local/bunny/udisk/`, mounted as
`/Volumes/BashBunny` on the host in arming mode) holds:

```
mode.txt                     # SW1 active profile name
mode2.txt                    # SW2 active profile name
profiles/                    # shared pool, used by both banks
  default.sh                 # HID + STORAGE, no keystrokes
  kbd1.sh                    # HID keyboard -> payload #1 QUACK sequence
  kbd2.sh                    # HID keyboard -> payload #2 QUACK sequence
  usb_sandisk.sh             # STORAGE only, spoofs SanDisk Cruzer Blade VID/PID
payloads/
  switch1/payload.txt        # SW1 dispatcher: self-installs bunnyctl, reads mode.txt
  switch2/payload.txt        # SW2 dispatcher: reads mode2.txt
bunnyctl.sh                  # on-device helper (installer source)
```

Each dispatcher reads its mode file, resolves the matching
`profiles/<name>.sh`, and sources it. A profile script is a full `ATTACKMODE`
(+ `QUACK`) sequence, so swapping personalities is one line in a mode file — no
payload edits. Both dispatchers self-install the on-device `bunnyctl` on first
boot if it's missing (idempotent; the `-x` test short-circuits thereafter).

### Switch geometry (this unit)

| Position | Physical            | GPIO reading `0` | Role                                    |
|----------|---------------------|------------------|-----------------------------------------|
| arming   | closest to USB plug | `PL3`            | setup / mass storage / serial console   |
| SW2      | **middle**          | `PL4`            | bank 2 — runs `mode2.txt` profile       |
| SW1      | **far end**         | `PA8`            | bank 1 — runs `mode.txt` profile        |

> GPIO value `0` = that position is selected. Verify on your unit with
> `bunnyctl gpio` (on-device) or `python3 bunnyctl.py gpio` (host) — Mark I
> silk-screening is easy to misread.

---

## The three red-team scenarios

| # | Goal                                          | Profile        | `ATTACKMODE` line                                                              |
|---|-----------------------------------------------|----------------|-------------------------------------------------------------------------------|
| 1 | Appear as a **keyboard**, run **payload #1**  | `kbd1`         | `ATTACKMODE HID` + QUACK sequence                                             |
| 2 | Appear as **mass storage** with a spoofed ID  | `usb_sandisk`  | `ATTACKMODE STORAGE VID_0x0781 PID_0x5567 MAN_"SanDisk" PROD_"Cruzer Blade"`  |
| 3 | Appear as a **keyboard**, run **payload #2**  | `kbd2`         | `ATTACKMODE HID` + QUACK sequence                                             |

Assign a profile to a bank, then flip to that switch and replug into the target:

```sh
# SW1 bank (far end)
python3 bunnyctl.py mode  kbd1          # scenario 1
python3 bunnyctl.py mode  usb_sandisk   # scenario 2
python3 bunnyctl.py mode  kbd2          # scenario 3

# SW2 bank (middle) - independent, same profile pool
python3 bunnyctl.py mode2 usb_sandisk

python3 bunnyctl.py status              # both banks + switch position

# on-device equivalents
bunnyctl mode  kbd1
bunnyctl mode2 usb_sandisk
bunnyctl status
```

> **Note on the mouse:** stock Bash Bunny firmware exposes HID keyboard only —
> there is no `MOUSE` ATTACKMODE token. All keystroke profiles are keyboard HID.

---

## Adding / changing keystrokes

Keystrokes are `QUACK` (Ducky Script) lines inside a profile. Edit the profile
directly on the udisk in arming mode, or author locally and push it.

Common `QUACK` verbs:

| Verb                         | Effect                                           |
|------------------------------|--------------------------------------------------|
| `QUACK STRING <text>`        | type literal text                                |
| `QUACK ENTER` / `TAB` / `ESC`| single named key                                 |
| `QUACK GUI r`                | Win/Cmd + r (Run dialog)                          |
| `QUACK CTRL-ALT DELETE`      | key combo                                        |
| `QUACK DELAY <ms>`           | wait (let the target settle before typing)       |
| `QUACK ALT F4`               | combo with function key                          |

Example — a profile that opens Run and launches PowerShell:

```sh
# profiles/ps_pop.sh
ATTACKMODE HID
LED ATTACK
QUACK DELAY 2000        # give the OS time to enumerate the keyboard
QUACK GUI r
QUACK DELAY 500
QUACK STRING powershell
QUACK ENTER
LED FINISH
```

Push it and assign it to a bank:

```sh
python3 bunnyctl.py profile-push ps_pop profiles/ps_pop.sh
python3 bunnyctl.py mode ps_pop
```

To append keystrokes to an existing profile, edit `profiles/<name>.sh` on the
udisk (arming mode) and add `QUACK` lines between `LED ATTACK` and `LED FINISH`,
or pull it, edit, and push it back:

```sh
python3 bunnyctl.py profile kbd1 > kbd1.sh   # pull
$EDITOR kbd1.sh                              # add QUACK lines
python3 bunnyctl.py profile-push kbd1 kbd1.sh # push
```

> **Timing matters.** A fresh HID enumeration on the target isn't instant. Lead
> with `QUACK DELAY 1500`–`3000` or the first keystrokes are dropped. Locked-down
> or slow VDI targets may need more.

---

## Changing USB identifiers (VID / PID / SN / strings)

> **Read this first — stock firmware reality (Mark I).** The Bash Bunny's
> `ATTACKMODE` does **not** treat all descriptor fields equally. It only wires
> `idVendor` / `idProduct` / `iManufacturer` / `iSerialNumber` from its args, it
> has **no `PROD_` handling at all**, and it **uppercases every argument**. So a
> naive `MAN_"SanDisk" PROD_"Cruzer Blade"` yields manufacturer `SANDISK` and
> the *default* product string (`HP Skylab USB Keyboard`) — a dead giveaway.
> This tool ships an ATTACKMODE patch (`patch_attackmode.sh`) that adds
> case/space-preserving `iProduct` + `iManufacturer` via env vars. Apply it once
> per device (see below); the SW1/SW2 dispatchers don't need it, only the string
> spoofs do.

Split by how each field is actually applied:

| Field | Descriptor | How it's set | Notes |
|-------|-----------|--------------|-------|
| VID | idVendor | `VID_0x####` on `ATTACKMODE` line | works stock |
| PID | idProduct | `PID_0x####` on `ATTACKMODE` line | works stock |
| SN | iSerialNumber | `SN_"..."` on `ATTACKMODE` line | works stock |
| MAN | iManufacturer | `export BUNNY_IMANUFACTURER="..."` before `ATTACKMODE` | env — preserves case/spaces (needs patch) |
| PROD | iProduct | `export BUNNY_IPRODUCT="..."` before `ATTACKMODE` | env — stock has no `PROD_` at all (needs patch) |

**Apply the ATTACKMODE patch once per device:**

```sh
python3 bunnyctl.py push patch_attackmode.sh /usr/local/bunny/udisk/patch_attackmode.sh
python3 bunnyctl.py exec 'sh /usr/local/bunny/udisk/patch_attackmode.sh'
```

It backs up the original to `ATTACKMODE.orig`, is idempotent, and rebuilds from
that backup each run (so it also repairs an earlier attempt). A **firmware
upgrade wipes it** — just re-run.

**Manual profile form** (what `usb_sandisk.sh` looks like):

```sh
export BUNNY_IMANUFACTURER="SanDisk"
export BUNNY_IPRODUCT="Cruzer Blade"
ATTACKMODE STORAGE VID_0x0781 PID_0x5567 SN_"4C530112050213104539"
LED FINISH
```

Keyboard impersonation is identical — set the class to `HID`:

```sh
export BUNNY_IMANUFACTURER="Dell"
export BUNNY_IPRODUCT="KB216 Wired Keyboard"
ATTACKMODE HID VID_0x413c PID_0x2113
```

(`0x413c` = Dell, `0x2113` = KB216. Match a keyboard the target org actually
deploys to blend into endpoint inventories.)

**Where to source real IDs:** USB-IF database, `lsusb` on a reference device, or
the Windows registry (`HKLM\SYSTEM\CurrentControlSet\Enum\USB`) on a
target-representative host. `SN_` is free-form — for storage, cloning a real
unit's serial defeats naive allowlists keyed on serial.

**With the tool** — no hand-editing (VID/PID/SN edit the `ATTACKMODE` line;
MAN/PROD upsert the `BUNNY_*` env exports):

```sh
python3 bunnyctl.py ids usb_sandisk --sn "4C530001071112116351"
python3 bunnyctl.py ids kbd1 --vid 0x413c --pid 0x2113 --man "Dell" --prod "KB216 Wired Keyboard"
python3 bunnyctl.py ids usb_sandisk          # show current: VID/PID/SN + MAN/PROD (env)
```

> **Verify from the host, not serial.** In a STORAGE-only spoof there's no serial
> console, so confirm on the target:
> `ioreg -p IOUSB -l -w0 | grep -iA5 -e SanDisk -e Cruzer` (macOS) or check
> Windows "Devices and Printers". macOS caches string descriptors per
> `(VID,PID)`/port — if a value looks stale, replug into a **different port**.

---

## `bunnyctl.py` (host) command reference

Options go **after** the subcommand. Port autodetects `/dev/cu.usbmodem*`
(macOS) / `/dev/ttyACM*` (Linux) when `--port` is omitted.

| Command                              | What it does                                         |
|--------------------------------------|------------------------------------------------------|
| `probe`                              | detect port, dump raw console output                 |
| `gpio`                               | read switch GPIOs (PA8/PL4/PL3)                       |
| `exec "<cmd>"`                       | run one shell command, print output                  |
| `audit`                              | read-only inventory / security audit                 |
| `setup-ssh --pubkey <file>`          | install pubkey(s), bring `eth0` up, persist          |
| `modes`                              | list ATTACKMODE tokens + built-in profiles (offline) |
| `mode [name]`                        | get / set **SW1** profile (`mode.txt`)               |
| `mode2 [name]`                       | get / set **SW2** profile (`mode2.txt`)              |
| `status`                             | switch position + **both** bank profiles             |
| `hostid`                             | how the Bunny enumerates on **this** host (offline)  |
| `profiles`                           | list profiles on device                              |
| `profile <name>`                     | print a profile script from device                   |
| `profile-push <name> <file>`         | push a local `.sh` as a named profile                |
| `ids <name> [--vid --pid --sn --mode --man --prod]` | view / edit a profile's identifiers |

`ids` with no flags prints class + VID/PID/SN (from the `ATTACKMODE` line) and
MAN/PROD (from `BUNNY_*` env exports). With flags: `--vid/--pid/--sn/--mode` edit
the `ATTACKMODE` line; `--man/--prod` upsert the env exports (needs
`patch_attackmode.sh` applied). QUACK/LED lines are never touched; a diff is
shown. `--mode` replaces the class token(s), e.g. `--mode "HID STORAGE"`.

Common options: `--port --baud --user --password --timeout --no-wake`.

## `bunnyctl` (on-device) command reference

| Command                | What it does                                   |
|------------------------|------------------------------------------------|
| `mode [name]`          | show / set SW1 profile (`mode.txt`)            |
| `mode2 [name]`         | show / set SW2 profile (`mode2.txt`)           |
| `gpio`                 | read switch GPIOs                              |
| `status`               | both banks + GPIO + profile list, one shot     |
| `profiles`             | list profile names                            |
| `profile <name>`       | print a profile script                        |

---

## Install

### 1. Profiles + dispatchers (host, arming mode)

With the Bunny in arming mode the udisk mounts as `/Volumes/BashBunny` (macOS)
or under `/media/...` (Linux):

```sh
cp -r profiles                    /Volumes/BashBunny/
cp payloads/switch1/payload.txt   /Volumes/BashBunny/payloads/switch1/
cp payloads/switch2/payload.txt   /Volumes/BashBunny/payloads/switch2/
cp bunnyctl.sh                    /Volumes/BashBunny/
printf 'default\n'              > /Volumes/BashBunny/mode.txt
printf 'default\n'              > /Volumes/BashBunny/mode2.txt
sync
```

### 2. On-device `bunnyctl`

Both dispatchers self-install it on first boot. To force it manually (serial/SSH):

```sh
mkdir -p /usr/local/bunny/bin
cp /usr/local/bunny/udisk/bunnyctl.sh /usr/local/bunny/bin/bunnyctl
chmod +x /usr/local/bunny/bin/bunnyctl
```

### 3. Host tool

```sh
python3 -m pip install --user pyserial
cp bunnyctl.py ~/bin/          # or anywhere on PATH
python3 ~/bin/bunnyctl.py --version
```

---

## Adding a profile

```sh
cat > payload3.sh <<'SH'
ATTACKMODE HID
LED ATTACK
QUACK DELAY 2000
QUACK GUI r
QUACK DELAY 500
QUACK STRING calc
QUACK ENTER
LED FINISH
SH
python3 bunnyctl.py profile-push demo payload3.sh
python3 bunnyctl.py mode demo          # assign to SW1
# or: python3 bunnyctl.py mode2 demo   # assign to SW2
```

Or drop `profiles/demo.sh` on the udisk directly in arming mode.

---

## Troubleshooting

### `Resource busy` / `could not open port ... [Errno 16]` (macOS)
The CDC-ACM device is wedged at the kernel level — `lsof` and `fuser` show
nothing holding it, but `stty -f /dev/cu.usbmodem*` still returns
`Resource busy`. Known macOS CDC-ACM quirk after an aborted serial session.
**Fix: physically unplug and replug the Bunny.** No software release works
reliably. If you can't replug, the dispatchers self-install `bunnyctl`, so you
can drive everything on-device with no serial at all.

### `shell not responding on <port> (try replugging / --no-wake off)`
The console didn't answer after the DTR wake. In order:
1. Confirm the Bunny is in **arming mode** (switch nearest USB) — serial only
   comes up there and in a bank whose profile enables `SERIAL`.
2. Give it ~15–20 s after replug to finish booting.
3. Retry — the first wake after enumeration sometimes misses.
4. `python3 bunnyctl.py probe` to see raw bytes; empty rx = wrong mode or getty
   not up yet.

### `No serial port found`
`--port` not given and nothing matched. Check the cable is data-capable (not
charge-only), the device enumerated (`bunnyctl.py hostid`), and pass
`--port /dev/cu.usbmodemXXXX` explicitly.

### Profile set but the target sees the old personality
A dispatcher reads its mode file **at boot on its switch**. You must **replug on
that switch position** after changing the mode — a live flip does not re-run the
payload. Confirm with `bunnyctl.py status` before unplugging.

### First keystrokes are dropped
The target hadn't finished enumerating the HID before the profile started
typing. Increase the leading `QUACK DELAY` (2000–3000 ms; more for VDI/locked
endpoints).

### Storage/keyboard spoof: device doesn't enumerate at all (nothing appears)
The gadget `insmod` failed, so no USB device comes up. On this firmware the
usual cause is a **space in a string value reaching the kernel unquoted**: e.g.
`iProduct="Cruzer Blade"` — the shell strips the quotes before `insmod`, the
kernel module parser then splits on the space, treats `Blade` as an unknown
parameter, and the load fails. The `patch_attackmode.sh` patch fixes this by
single-quote-wrapping the value so the quotes survive to the kernel
(`iProduct='"Cruzer Blade"'` → argv keeps `iProduct="Cruzer Blade"`). If you
hand-roll module params, do the same. Confirm the patch is applied:
`python3 bunnyctl.py exec 'grep -c BUNNY_ /usr/local/bunny/bin/ATTACKMODE'`
(expect `2`). Revert with `ATTACKMODE.orig` if needed.

### Manufacturer shows UPPERCASE, product shows "HP Skylab USB Keyboard"
Stock `ATTACKMODE` uppercases args (`MAN_"SanDisk"` → `SANDISK`) and has no
`PROD_` handling at all, so the product stays the firmware default. Set both via
the `BUNNY_*` env exports (what `ids --man/--prod` writes) with the patch applied
— see **Changing USB identifiers**. Re-check with `bunnyctl.py ids <name>`.

### Spoofed VID/PID/SN not taking effect
`ids` edits only the first `ATTACKMODE` line. Confirm the profile has exactly one
`ATTACKMODE` line and re-check with `python3 bunnyctl.py ids <name>`. macOS caches
USB descriptors per `(VID,PID)`/port — try a different port or clear the target's
USB enumeration cache.

### Spoofed storage volume is a tell (label / contents)
`ATTACKMODE STORAGE` exposes the **udisk** itself, so the volume mounts labelled
`BashBunny` and shows `payloads/`, `tools/`, `profiles/`. Descriptors can be
perfect and the mounted drive still gives it away. A clean separate backing image
is on the roadmap (`ROADMAP.md`); until then, relabel the udisk and be aware
anyone browsing the volume sees Bunny files.

### `profile '<name>' not found on device`
The mode file names a profile with no matching `profiles/<name>.sh`. Run
`bunnyctl.py profiles`; the dispatcher falls back to its bank default
(`HID STORAGE` for SW1, `STORAGE` for SW2) when the script is missing.

### SSH over the gadget ethernet won't connect from macOS
macOS won't bind the Bunny's **ECM** ethernet (interface comes up `NO-CARRIER`).
SSH must originate from a **Linux** host on the gadget network, or use
`RNDIS_ETHERNET` for Windows. `setup-ssh` handles the device side; the host-side
binding is the blocker on macOS.

### `bunnyctl: command not found` on the device
`/usr/local/bunny/bin` isn't on `PATH`. Call it by full path, add the dir to
`PATH`, or symlink into `/usr/local/bin`.

---

## Default credentials

Stock Bash Bunny serial/SSH login is `root` / `hak5bunny`. **Change it** on any
unit that leaves the bench. `bunnyctl.py` defaults to these; override with
`--user` / `--password`.

## Safety / scope

Authorized testing only. This drives a USB HID-injection and mass-storage attack
platform. Use on hardware you own or have **written** permission to test. See
`LICENSE`.
