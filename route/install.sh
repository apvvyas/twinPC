#!/usr/bin/env bash
# route/install.sh — kept for compatibility: installs only the task-routing feature via twinpc.
exec "$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)/tool/twinpc" install routing "$@"
