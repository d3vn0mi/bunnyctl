# bunnyctl roadmap

Open items and ideas, roughly in priority order.

## Planned / open

- **Separate clean storage image for the SanDisk decoy.**
  Right now `ATTACKMODE STORAGE` exposes the Bunny's udisk, so the spoofed
  "Cruzer Blade" volume mounts with the label `BashBunny` and shows
  `payloads/`, `tools/`, `profiles/`, `docs/` to anyone who opens it — a far
  bigger tell than any USB descriptor. Build a dedicated, clean backing image
  (FAT, innocuous label, a few plausible filler files) and point the storage
  function at it via the module's `file=` param instead of the udisk. This is
  the last real gap for a believable drive / SW2 decoy.
  Process documented in `separate-clean-storage-image.md`.
  _Decision needed from Iason: build this?_

- **Relabel quick-win.** Independent of the clean-image work: set the udisk FAT
  volume label to something neutral so `diskutil` / Explorer don't show
  `BashBunny`.

## Possible / later

- `hostid --match <term>` — cross-check the active profile's claimed identifiers
  against what the host actually enumerated, with a per-field pass/fail.
- `bcdDevice` override support in `ids` (module already exposes the param).
- Chunked `put_file` readback fix — `wc -c` occasionally returns `-1` due to a
  mangled serial readback; cosmetic, the write itself is fine.
- Optional: fold `patch_attackmode.sh` into an on-device install step so a
  firmware upgrade auto-repairs the ATTACKMODE env patch.

## Done

- v0.2 chameleon flow (mode / status / gpio)
- v0.3 profile system (profiles / profile / profile-push)
- v0.4 second payload bank (SW2 / mode2.txt)
- v0.5 `ids` identifier editing
- v0.6 chunked base64 push (fixes tty MAX_CANON mangling); `push` command
- v0.7 `ids` MAN/PROD via `BUNNY_*` env; ATTACKMODE env patch for iProduct/
  iManufacturer with correct kernel-level quoting (case + spaces preserved)
