"""Fill ${NAME}-style placeholders in k8s/*.yaml from the environment. Fails loudly if one is missing.

    python jenkins/scripts/render_k8s.py k8s/configmap.yaml k8s/api-deployment.yaml ... | kubectl apply -f -
(macOS has no envsubst by default; this needs only Python.)
"""
import os
import re
import sys

missing, out = set(), []
for path in sys.argv[1:]:
    text = open(path, encoding="utf-8").read()
    out.append(re.sub(r"\$\{([A-Z0-9_]+)\}", lambda m: os.environ.get(m.group(1)) or missing.add(m.group(1)) or "", text))
if missing:
    sys.exit(f"render_k8s: set these variables first: {', '.join(sorted(missing))}")
print("\n---\n".join(t.strip() for t in out))
