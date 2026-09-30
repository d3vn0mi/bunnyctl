# SanDisk Cruzer Blade storage spoof.
# Product/Manufacturer set via env (BUNNY_*) so exact case + spaces survive:
# stock ATTACKMODE uppercases + word-splits MAN_ and has no PROD_ handling at all.
# Requires the ATTACKMODE env patch (patch_attackmode.sh); without it VID/PID/SN
# still spoof, but iProduct falls back to the firmware default.
export BUNNY_IMANUFACTURER="SanDisk"
export BUNNY_IPRODUCT="Cruzer Blade"
ATTACKMODE STORAGE VID_0x0781 PID_0x5567 SN_"4C530112050213104539"
LED FINISH
