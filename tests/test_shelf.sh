#!/usr/bin/env bash
# A file dropped through each PC's shelf socket arrives in the other PC's shelf folder.
# (It pops the shelf up on both PCs.)
set -u
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
tok=shelf-$RANDOM$RANDOM
echo "$tok" > "$tmp/$tok.txt"
"$HOME/.local/bin/twin-shelf" --drop "$tmp/$tok.txt" || { echo "FAIL could not reach twin-clipd here"; exit 1; }
got=
for _ in $(seq 30); do
  got=$(ssh twin "cat ~/.cache/twinpc/shelf/*/$tok.txt 2>/dev/null")
  [[ $got == "$tok" ]] && break; sleep 0.5
done
[[ $got == "$tok" ]] || { echo "FAIL this PC → twin shelf"; exit 1; }

tok=shelf-$RANDOM$RANDOM
src=/tmp/twinpc-shelf-test-$tok
ssh twin "mkdir -p $src && echo $tok > $src/$tok.txt && .local/bin/twin-shelf --socket shelf.sock --drop $src/$tok.txt" \
  || { echo "FAIL could not reach the twin's shelf socket"; exit 1; }
for _ in $(seq 30); do
  got=$(cat ~/.cache/twinpc/shelf/*/"$tok.txt" 2>/dev/null)
  [[ $got == "$tok" ]] && break; sleep 0.5
done
ssh twin "rm -rf $src"
[[ $got == "$tok" ]] || { echo "FAIL twin → this PC shelf"; exit 1; }
echo "PASS shelf both ways"
