"""
Minimal launcher — starts api.py directly with default settings.
Run from model/ollama/:  python start_llama_simple.py
Server listens on http://localhost:11434
"""

import os
import sys
import uvicorn
from pathlib import Path

HERE = Path(__file__).parent
MODEL_PATH = HERE / "weights" / "model.gguf"

if not MODEL_PATH.exists():
    print(f"❌ Model not found: {MODEL_PATH}")
    sys.exit(1)

print(f"Model : {MODEL_PATH}")
print(f"Server: http://localhost:11434")
print(f"Loading model (first time: 30-60 s)...\n")

os.environ.setdefault("MODEL_PATH", str(MODEL_PATH))
sys.path.insert(0, str(HERE))

try:
    uvicorn.run("api:app", host="127.0.0.1", port=11434, log_level="info", reload=False)
except KeyboardInterrupt:
    print("\n\n✅ Server stopped")
except Exception as exc:
    print(f"\n❌ Error: {exc}")
    print("Install dependencies:  pip install -r requirements.txt")
    sys.exit(1)
