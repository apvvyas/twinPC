#!/usr/bin/env bash
# `twin camera off` tells the truth: an unreachable twin keeps its mic input, and the camera comes back at login.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
stubs=$(mktemp -d); trap 'rm -rf "$stubs"' EXIT
printf '#!/bin/sh\nexit 0\n' > "$stubs/systemctl"
printf '#!/bin/sh\nexit %s\n' '${SSH_STUB_RC:-0}' > "$stubs/ssh"
chmod +x "$stubs"/*
out=$(PATH="$stubs:$PATH" SSH_STUB_RC=255 "$ROOT/twin" camera off 2>&1); rc=$?
[[ $rc != 0 && $out == *unreachable* ]] || { echo "FAIL off with the twin unreachable said: $out (rc $rc)"; exit 1; }
out=$(PATH="$stubs:$PATH" SSH_STUB_RC=0 "$ROOT/twin" camera off 2>&1); rc=$?
[[ $rc == 0 && $out == *"next login"* ]] || { echo "FAIL off said: $out (rc $rc)"; exit 1; }
out=$(PATH="$stubs:$PATH" SSH_STUB_RC=255 "$ROOT/twin" camera on 2>&1); rc=$?
[[ $rc != 0 && $out == *unreachable* ]] || { echo "FAIL on with the twin unreachable said: $out (rc $rc)"; exit 1; }
echo "PASS twin camera on/off report what they did"
