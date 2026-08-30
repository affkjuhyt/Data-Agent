"""Copy one ollama model (manifest + blobs) into a bundle directory.

    python3 ollama_export.py qwen2.5:3b /path/to/bundle/ollama

Restores by copying the same tree into ~/.ollama/models on the target — ollama
reads manifests/registry.ollama.ai/library/<name>/<tag> and blobs/sha256-<digest>.
No network, no `ollama pull` on the air-gapped host.
"""
import json
import os
import shutil
import sys

model, out = sys.argv[1], sys.argv[2]
name, _, tag = model.partition(":")
tag = tag or "latest"
root = os.environ.get("OLLAMA_MODELS", os.path.expanduser("~/.ollama/models"))
manifest_rel = os.path.join("manifests", "registry.ollama.ai", "library", name, tag)
manifest_path = os.path.join(root, manifest_rel)
if not os.path.exists(manifest_path):
    sys.exit(f"model {model} not found at {manifest_path} — `ollama pull {model}` first")

with open(manifest_path, encoding="utf-8") as fh:
    manifest = json.load(fh)
digests = [manifest["config"]["digest"], *[layer["digest"] for layer in manifest["layers"]]]

os.makedirs(os.path.join(out, os.path.dirname(manifest_rel)), exist_ok=True)
shutil.copy2(manifest_path, os.path.join(out, manifest_rel))
os.makedirs(os.path.join(out, "blobs"), exist_ok=True)
total = 0
for digest in digests:
    blob = "sha256-" + digest.split(":", 1)[1]
    src = os.path.join(root, "blobs", blob)
    dst = os.path.join(out, "blobs", blob)
    if not os.path.exists(dst):
        shutil.copy2(src, dst)
    total += os.path.getsize(src)
print(f"{model}: manifest + {len(digests)} blob(s), {total / 1e9:.2f} GB")
