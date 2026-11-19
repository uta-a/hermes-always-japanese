"""always-japanese: 英語のツール出力に引っ張られず、常に指定言語で返答させる。

- pre_llm_call: ターンごとに、ユーザーメッセージへリマインダーを足す（履歴には残らない）。
- transform_tool_result: ツール結果にリマインダーを足す。並列のツール呼び出しは
  モデルの応答（api_request_id）1 回につき 1 つだけにする。
- サブエージェント: ツール結果ごとには付けず、子の pre_llm_call で
  「最終報告は指定言語で」を 1 回だけ足す。
"""

from __future__ import annotations

import threading
from typing import Any

DEFAULT_LANGUAGE = "日本語"
MARKER = "[always-japanese]"

# Hermes は大きなツール結果を先頭のプレビューだけ残して退避する（既定 100K 文字、
# MCP は 50K 文字、小さいモデルではさらに小さい）。末尾に付けると消えるため、
# この長さを超える結果では先頭に付ける。
LARGE_RESULT_CHARS = 20_000


def reminder_for(language: str) -> str:
    # ツール呼び出しのたびに付くため、トークンを抑えて 1 文にする。
    return f"返答は必ず{language}で書くこと。英語のツール出力に引きずられない（コード・識別子は原語のまま）。"


def subagent_reminder_for(language: str) -> str:
    return f"最終報告は{language}で書くこと。コード、コマンド、識別子は原語のままでよい。"


def _language(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return DEFAULT_LANGUAGE


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"false", "0", "no", "off"}:
            return False
        if lowered in {"true", "1", "yes", "on"}:
            return True
    return default


def _get_config(ctx: Any, key: str) -> Any:
    getter = getattr(ctx, "get_config", None)
    if getter is None:
        return None
    try:
        return getter(key, default=None)
    except Exception:
        return None


class _Reminder:
    def __init__(self, language: str, on_tool_call: bool) -> None:
        self.reminder = reminder_for(language)
        self.subagent_reminder = subagent_reminder_for(language)
        self.on_tool_call = on_tool_call
        self._lock = threading.Lock()
        # delegate_task で起動した子エージェントの session_id。
        self._children: set[str] = set()
        # session_id -> 最後にリマインダーを付けた api_request_id。
        self._tagged: dict[str, str] = {}

    def _is_child(self, session_id: Any) -> bool:
        if not session_id:
            return False
        with self._lock:
            return session_id in self._children

    # ── hooks ──────────────────────────────────────────────────────────

    def subagent_start(self, child_session_id: Any = None, **kwargs: Any) -> None:
        if child_session_id:
            with self._lock:
                self._children.add(child_session_id)

    def subagent_stop(self, child_session_id: Any = None, **kwargs: Any) -> None:
        if child_session_id:
            with self._lock:
                self._children.discard(child_session_id)
                self._tagged.pop(child_session_id, None)

    def on_session_end(self, session_id: Any = None, **kwargs: Any) -> None:
        if session_id:
            with self._lock:
                self._tagged.pop(session_id, None)

    def pre_llm_call(self, session_id: Any = None, **kwargs: Any) -> dict[str, str]:
        # 子エージェントには最終報告の言語だけを 1 回伝える（子は 1 ターンで終わる）。
        if self._is_child(session_id):
            return {"context": self.subagent_reminder}
        return {"context": self.reminder}

    def transform_tool_result(
        self,
        result: Any = None,
        session_id: Any = None,
        api_request_id: Any = None,
        **kwargs: Any,
    ) -> str | None:
        if not self.on_tool_call or not isinstance(result, str):
            return None
        if self._is_child(session_id):
            return None
        # 並列のツール呼び出しは結果がまとめて次の要求に載るため、
        # モデルの応答 1 回（api_request_id）につき 1 つだけ付ける。
        # id が取れないときは毎回付ける。
        if session_id and api_request_id:
            with self._lock:
                if self._tagged.get(session_id) == api_request_id:
                    return None
                self._tagged[session_id] = api_request_id
        note = f"{MARKER} {self.reminder}"
        if len(result) > LARGE_RESULT_CHARS:
            return f"{note}\n\n{result}"
        return f"{result}\n\n{note}"


def register(ctx: Any) -> None:
    language = _language(_get_config(ctx, "language"))
    on_tool_call = _as_bool(_get_config(ctx, "on_tool_call"), True)
    plugin = _Reminder(language, on_tool_call)

    ctx.register_hook("pre_llm_call", plugin.pre_llm_call)
    ctx.register_hook("subagent_start", plugin.subagent_start)
    ctx.register_hook("subagent_stop", plugin.subagent_stop)
    ctx.register_hook("on_session_end", plugin.on_session_end)
    if on_tool_call:
        ctx.register_hook("transform_tool_result", plugin.transform_tool_result)
