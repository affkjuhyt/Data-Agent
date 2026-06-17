from __future__ import annotations

import datetime
import os
import time
from typing import Any, Iterator, Literal, Protocol

from jinja2 import Template
from llama_cpp import Llama, LlamaGrammar, LlamaState

from _logging import get_logger

log = get_logger("llama3-model")

_NS = 1_000_000_000  # perf_counter seconds → nanoseconds (Ollama duration fields are ns)


def _render_chat_template(model: Llama,messages: list[dict[str, str]],add_generation_prompt: bool = False,) -> str:
    tmpl_str = (model.metadata or {}).get("tokenizer.chat_template", "")
    if tmpl_str:
        try:
            return Template(tmpl_str).render(
                messages=messages,
                bos_token="<s>",
                eos_token="</s>",
                add_generation_prompt=add_generation_prompt,
            )
        except Exception:
            pass

    out = "".join(
        f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n"
        for m in messages
    )
    if add_generation_prompt:
        out += "<|im_start|>assistant\n"
    return out



class UserInput(Protocol):
    def to_text(self, model: Llama) -> str: ...

class UserPrompt:
    def __init__(self, prompt: str) -> None:
        self.prompt = prompt

    def to_text(self, model: "Llama | None" = None) -> str:
        return self.prompt


class UserMessageList:
    def __init__(self, messages: list[dict[str, str]], offset: int = 0, include_system: bool = False) -> None:
        self.messages = messages
        self._offset = offset  # messages[offset:] are new — everything before is already in KV
        self._include_system = include_system

    def to_text(self, model: Llama) -> str:
        msgs = self.messages[self._offset:]
        # When a pre-cached system KV snapshot is loaded, the system tokens are already
        # in context — skip them here. When starting fresh (no snapshot), include them.
        if not self._include_system:
            msgs = [m for m in msgs if m.get("role") != "system"]
        return _render_chat_template(model, msgs, add_generation_prompt=True)


class Formatter:
    def __init__(self, mode: Literal["chat", "generate"]) -> None:
        self.mode = mode

    def format(self, text: str, *, is_chunk: bool = False, **kwargs: Any) -> dict[str, Any]:
        base: dict[str, Any] = {"model": "llama3", "created_at": _utcnow(), "done": not is_chunk}
        if is_chunk:
            body = {"message": {"role": "assistant", "content": text}} if self.mode == "chat" else {"response": text}
        elif self.mode == "chat":
            body = {
                "message": {"role": "assistant", "content": text},
                "done_reason": "stop",
                "prompt_eval_count": kwargs.get("prompt_token_count", 0),
                "prompt_eval_duration": kwargs.get("eval_duration_ns", 0),
                "eval_count": kwargs.get("output_token_count", 0),
                "eval_duration": kwargs.get("eval_duration_ns", 0),
                "total_duration": kwargs.get("eval_duration_ns", 0),
                "load_duration": 0,
            }
        else:
            body = {
                "response": text,
                "done_reason": "stop",
                "context": kwargs.get("context", []),
                "prompt_eval_count": kwargs.get("prompt_token_count", 0),
                "prompt_eval_duration": kwargs.get("eval_duration_ns", 0),
                "eval_count": kwargs.get("output_token_count", 0),
                "eval_duration": kwargs.get("eval_duration_ns", 0),
                "total_duration": kwargs.get("eval_duration_ns", 0),
                "load_duration": 0,
            }
        return {**base, **body}

# Standard GBNF grammar that constrains output to valid JSON objects.
# Passed to llama_cpp generate() when the caller sets format="json".
_JSON_GRAMMAR_GBNF = r"""
root   ::= object
value  ::= object | array | string | number | ("true" | "false" | "null") ws

object ::=
  "{" ws (
            string ":" ws value
    ("," ws string ":" ws value)*
  )? "}" ws

array  ::=
  "[" ws (
            value
    ("," ws value)*
  )? "]" ws

string ::=
  "\"" (
    [^\\"\x7F\x00-\x1F] |
    "\\" (["\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])
  )* "\"" ws

number ::= ("-"? ([0-9] | [1-9] [0-9]*)) ("." [0-9]+)? ([eE] [-+]? [0-9]+)? ws

ws ::= ([ \t\n] ws)?
"""

_JSON_GRAMMAR: LlamaGrammar | None = None

def _get_json_grammar() -> LlamaGrammar:
    global _JSON_GRAMMAR
    if _JSON_GRAMMAR is None:
        _JSON_GRAMMAR = LlamaGrammar.from_string(_JSON_GRAMMAR_GBNF, verbose=False)
    return _JSON_GRAMMAR


def _extract_gen_kwargs(options: dict[str, Any], fmt: str = "", grammar_gbnf: str = "") -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "temp": float(options.get("temperature", 0.8)),
        "top_k": int(options.get("top_k", 40)),
        "top_p": float(options.get("top_p", 0.95)),
        "min_p": float(options.get("min_p", 0.05)),
        "typical_p": float(options.get("typical_p", 1.0)),
        "repeat_penalty": float(options.get("repeat_penalty", 1.0)),
        "frequency_penalty": float(options.get("frequency_penalty", 0.0)),
        "presence_penalty": float(options.get("presence_penalty", 0.0)),
        "tfs_z": float(options.get("tfs_z", 1.0)),
        "mirostat_mode": int(options.get("mirostat", 0)),
        "mirostat_tau": float(options.get("mirostat_tau", 5.0)),
        "mirostat_eta": float(options.get("mirostat_eta", 0.1)),
        "penalize_nl": bool(options.get("penalize_newline", True)),
        "reset": False,
    }
    if grammar_gbnf:
        try:
            kwargs["grammar"] = LlamaGrammar.from_string(grammar_gbnf, verbose=True)
        except Exception as _e:
            import logging as _lg
            _lg.getLogger("llama3-model").error(
                "grammar_gbnf parse failed (%s) — falling back to generic JSON grammar", _e
            )
            kwargs["grammar"] = _get_json_grammar()
    elif fmt == "json":
        kwargs["grammar"] = _get_json_grammar()
    return kwargs

def _resolve_max_tokens(payload: dict[str, Any], options: dict[str, Any]) -> int:
    # LiteLLM proxy sends max_tokens as options.num_predict (Ollama format) OR
    # as a top-level max_tokens field. Check both; fall back to 512.
    v = options.get("num_predict") or payload.get("max_tokens") or payload.get("num_predict")
    return int(v) if v else 512


def parse_chat_payload(payload: dict[str, Any]) -> dict[str, Any]:
    options = payload.get("options") or {}
    fmt = str(payload.get("format", "") or "")
    grammar_gbnf = str(payload.get("grammar_gbnf", "") or "")
    mt = _resolve_max_tokens(payload, options)
    import logging as _l; _l.getLogger("llama3-model").info(
        "parse_chat_payload max_tokens=%d options.num_predict=%r payload.max_tokens=%r format=%r grammar=%s",
        mt, options.get("num_predict"), payload.get("max_tokens"), fmt,
        "custom" if grammar_gbnf else "none",
    )
    return {
        "user_input": UserMessageList(payload["messages"]),
        "formatter": Formatter("chat"),
        "gen_kwargs": _extract_gen_kwargs(options, fmt, grammar_gbnf),
        "max_tokens": mt,
        "stream": bool(payload.get("stream", False)),
    }

def parse_generate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    options = payload.get("options") or {}
    fmt = str(payload.get("format", "") or "")
    return {
        "user_input": UserPrompt(payload["prompt"]),
        "formatter": Formatter("generate"),
        "gen_kwargs": _extract_gen_kwargs(options, fmt),
        "max_tokens": _resolve_max_tokens(payload, options),
        "stream": bool(payload.get("stream", False)),
    }

def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


class Llama3Model:
    __slots__ = ("_model_path", "_n_ctx", "_n_gpu_layers", "_n_threads","_kv_cache_type",
                 "_kv_type_int", "_use_mmap", "_use_mlock", "_verbose", "_model")
    def __init__(self) -> None:
        self._model_path: str = os.getenv("MODEL_PATH", "/models/model.gguf")
        self._n_ctx: int = int(os.getenv("N_CTX", "4096"))
        self._n_gpu_layers: int = int(os.getenv("N_GPU_LAYERS", "-1"))  # -1 = all layers on GPU
        self._n_threads: int = int(os.getenv("N_THREADS", "8"))
        self._kv_cache_type: str = os.getenv("KV_CACHE_TYPE", "f16")
        self._kv_type_int: int = {"f16": 1, "q8_0": 8, "q4_0": 2}.get(self._kv_cache_type, 1)
        self._use_mmap: bool = os.getenv("USE_MMAP", "true").lower() == "true"
        self._use_mlock: bool = os.getenv("USE_MLOCK", "true").lower() == "true"
        self._verbose: bool = os.getenv("VERBOSE", "false").lower() == "true"
        self._model: Llama | None = None

    def load(self) -> None:
        log.info("Loading model from %s with n_gpu_layers=%d …", self._model_path, self._n_gpu_layers)
        t0 = time.perf_counter()
        self._model = Llama(
            model_path=self._model_path,
            n_ctx=self._n_ctx,
            n_gpu_layers=self._n_gpu_layers,
            n_threads=self._n_threads,
            type_k=self._kv_type_int,
            type_v=self._kv_type_int,
            use_mmap=self._use_mmap,
            use_mlock=self._use_mlock,
            verbose=self._verbose,
            n_batch=512,
        )
        elapsed = time.perf_counter() - t0
        gpu_layers = getattr(self._model, "n_gpu_layers", self._n_gpu_layers)
        n_layers = getattr(self._model, "n_layers", "?")
        log.info("Weights loaded in %.2fs | GPU layers: %s/%s", elapsed, gpu_layers, n_layers)
        if self._n_gpu_layers != 0:
            log.info("GPU acceleration ENABLED")
        else:
            log.warning("GPU acceleration DISABLED — model running on CPU only")

    def precaching_system_prompt(self, system_prompt: str) -> LlamaState:
        assert self._model is not None
        t0 = time.perf_counter()
        tokens = self._model.tokenize(system_prompt.encode(), add_bos=True, special=True)
        self._model.eval(tokens)
        state = self._model.save_state()
        log.info("KV snapshot ready in %.2fs — %d tokens frozen", time.perf_counter() - t0, len(tokens))
        return state

    def reload_state(self, state: LlamaState) -> None:
        assert self._model is not None
        self._model.load_state(state)

    def persist_state(self) -> LlamaState:
        assert self._model is not None
        return self._model.save_state()

    def decode_tokens(self, tokens: list[int]) -> str:
        assert self._model is not None
        return self._model.detokenize(tokens).decode("utf-8", errors="replace")

    def eval_assistant_reply(self, reply: str) -> None:
        assert self._model is not None
        if not reply:
            return
        rendered = _render_chat_template(
            self._model,
            [{"role": "assistant", "content": reply}],
            add_generation_prompt=False,
        )
        idx = rendered.find(reply)
        suffix = rendered[idx + len(reply):] if idx != -1 else ""
        if not suffix:
            return
        tokens = self._model.tokenize(suffix.encode(), add_bos=False, special=True)
        if tokens:
            self._model.eval(tokens)

    def prefill(self, user_input: UserInput) -> list[int]:
        assert self._model is not None, "call load() first"
        tokens = self._model.tokenize(user_input.to_text(self._model).encode(), add_bos=False, special=True)
        self._model.eval(tokens)
        return tokens

    def generate_tokens(self, gen_kwargs: dict[str, Any], max_tokens: int) -> tuple[list[int], int]:
        assert self._model is not None
        t0 = time.perf_counter()
        output: list[int] = []
        for token in self._model.generate([], **gen_kwargs):
            if token == self._model.token_eos() or len(output) >= max_tokens:
                break
            output.append(token)
        return output, int((time.perf_counter() - t0) * _NS)

    def stream_tokens(self, gen_kwargs: dict[str, Any], max_tokens: int) -> Iterator[int]:
        assert self._model is not None
        count = 0
        for token in self._model.generate([], **gen_kwargs):
            if token == self._model.token_eos() or count >= max_tokens:
                break
            count += 1
            yield token

    def decode_reply(
            self,
            input_tokens: list[int],
            formatter: Formatter,
            gen_kwargs: dict[str, Any],
            max_tokens: int = 512,
    ) -> dict[str, Any]:
        output_tokens, duration_ns = self.generate_tokens(gen_kwargs, max_tokens)
        return formatter.format(
            self.decode_tokens(output_tokens),
            context=input_tokens + output_tokens,
            prompt_token_count=len(input_tokens),
            output_token_count=len(output_tokens),
            eval_duration_ns=duration_ns,
        )

    def stream_decode_reply(
            self,
            formatter: Formatter,
            gen_kwargs: dict[str, Any],
            max_tokens: int = 512,
    ) -> Iterator[dict[str, Any]]:
        for token_id in self.stream_tokens(gen_kwargs, max_tokens):
            yield formatter.format(self.decode_tokens([token_id]), is_chunk=True)

    def response(
            self,
            user_input: UserInput,
            formatter: Formatter,
            gen_kwargs: dict[str, Any],
            max_tokens: int = 512,
            **_: Any,
    ) -> dict[str, Any]:
        tokens = self.prefill(user_input)
        return self.decode_reply(tokens, formatter, gen_kwargs, max_tokens)

    def stream_response(
            self,
            user_input: UserInput,
            formatter: Formatter,
            gen_kwargs: dict[str, Any],
            max_tokens: int = 512,
            **_: Any,
    ) -> Iterator[dict[str, Any]]:
        self.prefill(user_input)
        yield from self.stream_decode_reply(formatter, gen_kwargs, max_tokens)

    def embed(self, text: str) -> list[float]:
        """Return a float embedding for the given text using the model's token representations."""
        assert self._model is not None
        result = self._model.create_embedding(text)
        return result["data"][0]["embedding"]

    def health(self) -> dict[str, Any]:
        return {
            "status": "ready" if self._model else "not_loaded",
            "model_path": self._model_path,
            "n_ctx": self._n_ctx,
            "n_gpu_layers": self._n_gpu_layers,
            "n_threads": self._n_threads,
            "kv_cache_type": self._kv_cache_type,
        }


