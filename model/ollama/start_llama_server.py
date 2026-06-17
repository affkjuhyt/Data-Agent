"""
Launch the Llama3 pool server (api.py) with configurable settings.
Run from model/ollama/:  python start_llama_server.py
Server listens on http://localhost:11434
"""

import os
import sys
import uvicorn
from pathlib import Path

HERE = Path(__file__).parent
MODEL_PATH = HERE / "weights" / "model.gguf"

print("=" * 62)
print("  Llama Server (Pool Mode)")
print("=" * 62, "\n")

if not MODEL_PATH.exists():
    print(f"Model not found: {MODEL_PATH}")
    print(f"Place your model at: {MODEL_PATH.absolute()}")
    sys.exit(1)

# Configuration — override any of these via env vars before running.
HOST           = os.getenv("HOST",           "127.0.0.1")
PORT           = int(os.getenv("PORT",       "11434"))
N_GPU_LAYERS   = os.getenv("N_GPU_LAYERS",   "50")   # RTX 4070: 40-50; -1 = all on GPU
N_CTX          = os.getenv("N_CTX",          "4096")
N_THREADS      = os.getenv("N_THREADS",      "8")
KV_CACHE_TYPE  = os.getenv("KV_CACHE_TYPE",  "f16")
USE_MMAP       = os.getenv("USE_MMAP",       "true")
USE_MLOCK      = os.getenv("USE_MLOCK",      "true")
VERBOSE        = os.getenv("VERBOSE",        "false")
POOL_SIZE      = os.getenv("POOL_SIZE",      "1")

print(f"Model  : {MODEL_PATH}")
print(f"Size   : {MODEL_PATH.stat().st_size / (1024 ** 3):.1f} GB")
print(f"Server : http://{HOST}:{PORT}\n")
print(f"Settings:")
print(f"   Pool instances : {POOL_SIZE}")
print(f"   GPU layers     : {N_GPU_LAYERS}")
print(f"   Context (n_ctx): {N_CTX}")
print(f"   Threads        : {N_THREADS}")
print(f"   KV cache type  : {KV_CACHE_TYPE}\n")
print("Loading model(s)...\n")

# Propagate settings to env so api.py + llama.py pick them up.
os.environ.setdefault("MODEL_PATH",    str(MODEL_PATH))
os.environ.setdefault("N_GPU_LAYERS",  N_GPU_LAYERS)
os.environ.setdefault("N_CTX",         N_CTX)
os.environ.setdefault("N_THREADS",     N_THREADS)
os.environ.setdefault("KV_CACHE_TYPE", KV_CACHE_TYPE)
os.environ.setdefault("USE_MMAP",      USE_MMAP)
os.environ.setdefault("USE_MLOCK",     USE_MLOCK)
os.environ.setdefault("VERBOSE",       VERBOSE)
os.environ.setdefault("POOL_SIZE",     POOL_SIZE)

# Add model/ollama to sys.path so api.py can import its siblings.
sys.path.insert(0, str(HERE))

try:
    uvicorn.run("api:app", host=HOST, port=PORT, log_level="info", reload=False)
except KeyboardInterrupt:
    print("\n\nServer stopped")
except Exception as exc:
    print(f"\nError: {exc}")
    print("\nTroubleshooting:")
    print("1. Install dependencies:  pip install -r requirements.txt")
    print("2. For CUDA GPU support install the matching llama-cpp-python wheel")
    print("   (see docker/Dockerfile for the exact wheel URL)")
    sys.exit(1)
