#!/bin/sh
# usage: eval/run_mode.sh MODE [--no-llm] sets...
m=$1; shift; flag=$1; shift
for f in "$@"; do .venv/bin/python -c "
import sys,runpy; sys.path.insert(0,'.'); import agent.core as C; C.MODE='$m'
sys.argv=['x','eval/$f.json','$flag']; runpy.run_path('eval/run_agent.py',run_name='__main__')" 2>&1 | grep -E 'TOTAL|Error|Trace|semantic error' | sed "s/^/$f: /"; done
