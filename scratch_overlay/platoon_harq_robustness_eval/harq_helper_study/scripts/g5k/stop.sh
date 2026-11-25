#!/bin/bash
# Stop the genie campaign on Grid'5000: delete every OAR job of this user whose name starts with "genie-"
# (waiting or running). Finished runs are kept; submitting again resumes.
ids=$(oarstat -u -J 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(' '.join(str(k) for k, j in d.items() if str(j.get('name') or '').startswith('genie-')))")
[ -n "$ids" ] && oardel $ids || echo "no genie job"
