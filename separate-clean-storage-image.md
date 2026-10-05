# Separate clean storage image for the SanDisk decoy

Status: **planned** (see `ROADMAP.md`). This document explains the process; the
on-device specifics marked _(confirm at build)_ need checking against the unit
before implementing.

## Why

Today the `usb_sandisk` profile runs `ATTACKMODE STORAGE`, which exposes the
**udisk** (`/usr/local/bunny/udisk`) as the mass-storage LUN. After the env
patch, the USB descriptors are a perfect SanDisk Cruzer Blade — but the mounted
volume still:

- is labelled `BashBunny` (the udisk's FAT label), and
- contains `payloads/`, `tools/`, `profiles/`, `docs/`, `loot/`.

Anyone who opens the drive sees it's a Bash Bunny. Relabeling the udisk only
fixes the name, is global (changes arming too), and does nothing about the
contents. The clean fix is to back the decoy's storage with a **separate disk
image** that has its own label and innocuous contents, leaving the real udisk
untouched and still labelled `BashBunny` for your own use.

## How Bash Bunny storage works

`ATTACKMODE STORAGE` ends up at `insmod /usr/local/bunny/lib/bunny_gadget.ko
… is_storage=1 <backing>`. The gadget module's mass-storage function is
file-backed — `modinfo` exposes exactly the LUN parameters we need:

| Param | Meaning |
|-------|---------|
| `file` | backing file(s) or device(s) for the LUN(s) — **this is the lever** |
| `removable` | `1` = simulate removable media (a flash drive) |
| `ro` | `1` = force read-only |
| `cdrom` | `1` = present as CD-ROM (not what we want for a flash stick) |
| `luns` | number of LUNs |

By default `ATTACKMODE` points `file=` at the udisk (the exact token is built
into the `$str_mass_storage` variable — _(confirm at build: read the default in
`/usr/local/bunny/bin/ATTACKMODE`)_). Point `file=` at an image instead and the
host sees that image as the drive — its size, its label, its contents — with the
udisk never exposed.

## Hard constraint: space

The decoy image is a file **on the Bunny's own flash**, and a Mark I has very
little (~2 GB, mostly taken by the udisk). Consequences:

- A "16 GB Cruzer" decoy is impossible — there isn't room. Size the image to
  what fits: **256 MB–1 GB** is realistic for an older/small USB stick.
- The image is **too large for the chat bridge** (`device_commit_files` caps at
  ~20 MB/file), so it cannot be pushed from here. It must be **created on the
  device**, or built on the Mac and copied over in arming mode.
- Decide where it lives. If free space on the rootfs is tight, the image may have
  to sit on the udisk itself (consuming udisk space) — _(confirm at build: `df -h`
  on the device)_.

## Build process

### 1. Decide the drive's identity

| Choice | Example |
|--------|---------|
| Size | `512M` (plausible small stick, fits the Bunny) |
| FAT label | `CRUZER` (≤11 chars, uppercase) |
| Contents | a few boring files: a PDF or two, some JPEGs, a `resume.docx`, maybe an empty `DCIM/` |
| Read-only? | optional `ro=1` — a write-protected stick is unremarkable and stops the target writing to it |

### 2. Create and format the image

Easiest on a Linux box (the cloud container, or the Bunny if it has the tools):

```sh
dd if=/dev/zero of=decoy.img bs=1M count=512
mkfs.vfat -F 32 -n CRUZER decoy.img        # dosfstools; -n sets the label
```

_(confirm at build: `which mkfs.vfat dd` on the device. If dosfstools is absent,
format on the Mac or cloud container and copy the finished image over.)_

### 3. Seed innocuous files

Without mounting, using mtools (no root/loop needed) if available:

```sh
mcopy -i decoy.img ./filler/*  ::/         # mtools
mdir  -i decoy.img ::/                      # verify
```

Or by loop-mounting (Linux, root):

```sh
mkdir -p /mnt/decoy
mount -o loop decoy.img /mnt/decoy
cp ./filler/* /mnt/decoy/
sync && umount /mnt/decoy
```

Keep filler plausible and benign — nothing that references RavenSec, the
engagement, or tooling. Match the backstory of whatever drive you're imitating.

### 4. Place the image on the device

- **Built on device:** already in place; just note the path, e.g.
  `/usr/local/bunny/decoy.img`.
- **Built on Mac/cloud:** in **arming mode**, copy it onto the udisk
  (`/Volumes/BashBunny/decoy.img`), then over serial move it to its final path.
  Mind the space constraint — it may need to stay on the udisk.

## Wire it to the decoy profile

Extend the existing ATTACKMODE env patch (`patch_attackmode.sh`) with one more
override so a profile can swap the storage backing via env — same pattern as
`BUNNY_IPRODUCT`/`BUNNY_IMANUFACTURER`:

```sh
# in attack_mode_params(), when building the storage params:
#   default:         file=<udisk>            (unchanged)
#   if BUNNY_STORAGE_FILE set: file=<that image> INSTEAD of the udisk
```

Important: `file=` is an **array** param — passing a second `file=` adds a LUN
rather than replacing one. The patch must **substitute** the default backing, not
append, so the decoy exposes only the image. _(confirm at build: how
`$str_mass_storage` is assembled, then replace it when `BUNNY_STORAGE_FILE` is
set.)_

Decoy profile (`profiles/usb_sandisk.sh`, or a dedicated `profiles/decoy.sh`):

```sh
export BUNNY_IMANUFACTURER="SanDisk"
export BUNNY_IPRODUCT="Cruzer Blade"
export BUNNY_STORAGE_FILE="/usr/local/bunny/decoy.img"
ATTACKMODE STORAGE VID_0x0781 PID_0x5567 SN_"4C530112050213104539"
LED FINISH
```

Keep the SW2 bank pointed at this for a one-flip, inspect-clean decoy; leave
`usb_sandisk` as the udisk-backed attack-storage variant if you still want one.

## Verify (host side)

Plug into the target and confirm **all** of:

| Check | macOS |
|-------|-------|
| Descriptors | `ioreg -p IOUSB -l -w0 \| grep -iA5 -e SanDisk -e Cruzer` |
| Label + size | `diskutil list external physical` → shows `CRUZER`, the image's size |
| Contents | open the volume — only the filler files, **no** `payloads/`/`tools/` |

The win condition: label `CRUZER`, a plausible size, only innocuous files, and
the udisk in arming mode still shows `BashBunny`.

## Caveats

- **Firmware upgrade** wipes device-side changes (the image, the ATTACKMODE
  patch). Keep `decoy.img` build steps scripted so it's reproducible.
- **Space**: re-check `df -h` before creating; a too-large image fills the flash
  and can brick payload operation.
- **Write persistence**: if `ro` is not set, target writes land in the image and
  persist — fine for a decoy, but don't carry sensitive residue between targets;
  rebuild or keep a pristine copy.
- **Performance**: file-backed storage on slow NAND is sluggish; a large image
  makes enumeration and browsing visibly slow — another reason to keep it small.

## Open items to resolve at build

1. `df -h` — how much free space, and where the image can live.
2. `which mkfs.vfat mcopy dd losetup` — what formatting/seeding tools exist on
   the unit.
3. The exact `$str_mass_storage` construction in `ATTACKMODE`, to replace the
   backing cleanly when `BUNNY_STORAGE_FILE` is set.
4. Whether `removable=1` is already implied by the storage path or must be added.
