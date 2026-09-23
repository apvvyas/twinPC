#!/usr/bin/env bash
# A viewer window for a twin tmux session appears on workspace 9 and the active workspace is unchanged.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd); T="$ROOT/twin"
s=viewtest-$$
wsid() { "$T" gui hypr activeworkspace -j | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])'; }
before=$(wsid)
ssh twin "tmux new-session -d -s $s 'sleep 20'"
"$T" viewer "$s"; sleep 2
ws=$("$T" gui hypr clients -j | python3 -c "
import sys,json
print(next((str(c['workspace']['id']) for c in json.load(sys.stdin) if c['class']=='twin-task' and c.get('initialTitle')=='$s'), 'none'))")
after=$(wsid)
ssh twin "tmux kill-session -t $s; pkill -f \"foot --app-id=twin-task --title=$s \"" 2>/dev/null
if [[ $ws == 9 && $before == "$after" ]]; then echo "PASS viewer on ws 9, focus stayed on $after"; else echo "FAIL ws=$ws before=$before after=$after"; exit 1; fi
