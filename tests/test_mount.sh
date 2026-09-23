#!/usr/bin/env bash
# A file written on twin in ~/work appears under ~/twin on this PC.
set -u
f=mount-test-$$
ssh twin "echo hello-mount > ~/work/$f"
sleep 1
got=$(timeout 5 cat "$HOME/twin/$f" 2>/dev/null)
ssh twin "rm -f ~/work/$f"
mounted=no; findmnt -rn -M "$HOME/twin" >/dev/null && mounted=yes
if [[ $mounted == yes && $got == hello-mount ]]; then echo PASS; else echo "FAIL (mounted: $mounted, got '$got')"; exit 1; fi
