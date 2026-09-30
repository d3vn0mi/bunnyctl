#!/bin/sh
export BUNNY_IPRODUCT="Cruzer Blade"
cat > /tmp/fi <<'EOF'
#!/bin/sh
n=0
for a in "$@"; do n=$((n+1)); printf 'ARG%s=[%s]\n' "$n" "$a"; done
EOF
chmod +x /tmp/fi
insmod_cmd="/tmp/fi bunny_gadget.ko is_storage=1"
[ "x$BUNNY_IPRODUCT" != "x" ] && insmod_cmd="$insmod_cmd iProduct=\"$BUNNY_IPRODUCT\""
echo "=== OLD ==="
/bin/sh -c "$insmod_cmd"
insmod_cmd="/tmp/fi bunny_gadget.ko is_storage=1"
[ "x$BUNNY_IPRODUCT" != "x" ] && insmod_cmd="$insmod_cmd iProduct='\"$BUNNY_IPRODUCT\"'"
echo "=== NEW ==="
/bin/sh -c "$insmod_cmd"
