"""実際の PluginManager の discovery を通して、hook の結果を確かめる。

実行（Hermes 本体の Python 環境で動かす）:
    hermes --run-module unittest discover -s <repo>/tests -t <repo>
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import json

from hermes_cli import plugins as plugins_mod
from hermes_cli.plugins import PluginManager

PLUGIN_SRC = Path(__file__).resolve().parent.parent
PLUGIN_NAME = "always-japanese"


def _reminder(language: str = "日本語") -> str:
    return f"返答は必ず{language}で書くこと。英語のツール出力に引きずられない（コード・識別子は原語のまま）。"


def _subagent_reminder(language: str = "日本語") -> str:
    return f"最終報告は{language}で書くこと。コード、コマンド、識別子は原語のままでよい。"


def _note(language: str = "日本語") -> str:
    return f"[always-japanese] {_reminder(language)}"


class AlwaysJapaneseTest(unittest.TestCase):
    settings: dict | None = None

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        home = tmp / ".hermes"
        dest = home / "plugins" / PLUGIN_NAME
        shutil.copytree(
            PLUGIN_SRC, dest,
            ignore=shutil.ignore_patterns(".git", "tests", "__pycache__", "*.md", "LICENSE"),
        )
        plugins_cfg: dict = {"enabled": [PLUGIN_NAME]}
        if self.settings is not None:
            plugins_cfg["entries"] = {PLUGIN_NAME: {"settings": self.settings}}
        (home / "config.yaml").write_text(json.dumps({"plugins": plugins_cfg}, ensure_ascii=False))
        bundled = tmp / "bundled"
        bundled.mkdir()

        self._patches = [
            mock.patch.dict(os.environ, {"HERMES_HOME": str(home), "HOME": str(tmp / "os-home")}),
            mock.patch.object(plugins_mod, "get_bundled_plugins_dir", lambda: bundled),
        ]
        for p in self._patches:
            p.start()
        self.manager = PluginManager()
        self.manager.discover_and_load()
        loaded = self.manager._plugins.get(PLUGIN_NAME)
        self.assertIsNotNone(loaded, "プラグインが discovery されていない")
        self.assertIsNotNone(loaded.module, f"プラグインの読み込みに失敗: {getattr(loaded, 'error', None)}")

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    # ── helpers ──

    def hook(self, name: str, **kwargs):
        return self.manager.invoke_hook(name, **kwargs)

    def transform(self, result="ok", session_id="s1", api_request_id="t1:api:1", **kwargs):
        out = self.hook(
            "transform_tool_result", tool_name="terminal", args={}, result=result,
            task_id="task", session_id=session_id, tool_call_id="c1", turn_id="t1",
            api_request_id=api_request_id, duration_ms=1, status="success",
            error_type=None, error_message=None, **kwargs,
        )
        strings = [r for r in out if isinstance(r, str)]
        return strings[0] if strings else None

    def pre_llm(self, session_id="s1"):
        return self.hook(
            "pre_llm_call", session_id=session_id, task_id="task", turn_id="t1",
            user_message="hello", conversation_history=[], is_first_turn=True,
            model="m", platform="cli", parent_session_id="", sender_id="",
        )


class DefaultTest(AlwaysJapaneseTest):
    def test_pre_llm_call_でリマインダーを足す(self):
        self.assertEqual(self.pre_llm(), [{"context": _reminder()}])

    def test_ツール結果の末尾にリマインダーを足す(self):
        self.assertEqual(self.transform("output"), f"output\n\n{_note()}")

    def test_エラー結果にも足す(self):
        out = self.transform('{"error": "boom"}')
        self.assertTrue(out.endswith(_note()))

    def test_並列のツール結果には応答1回につき1つだけ足す(self):
        a = self.transform("a", api_request_id="t1:api:1")
        b = self.transform("b", api_request_id="t1:api:1")
        c = self.transform("c", api_request_id="t1:api:2")
        self.assertEqual(a, f"a\n\n{_note()}")
        self.assertIsNone(b)
        self.assertEqual(c, f"c\n\n{_note()}")

    def test_セッションごとに独立して数える(self):
        self.assertIsNotNone(self.transform("a", session_id="s1"))
        self.assertIsNotNone(self.transform("b", session_id="s2"))

    def test_api_request_id_が無ければ毎回足す(self):
        self.assertIsNotNone(self.transform("a", api_request_id=""))
        self.assertIsNotNone(self.transform("b", api_request_id=""))

    def test_セッション終了後は同じ_id_でも再び足す(self):
        self.assertIsNotNone(self.transform("a"))
        self.hook("on_session_end", session_id="s1", completed=True, interrupted=False, model="m", platform="cli")
        self.assertIsNotNone(self.transform("b"))

    def test_大きな結果では先頭に足す(self):
        big = "x" * 30_000
        out = self.transform(big)
        self.assertTrue(out.startswith(_note()))
        self.assertTrue(out.endswith(big))

    def test_文字列以外の結果は変えない(self):
        self.assertIsNone(self.transform([{"type": "text", "text": "hi"}]))

    def test_サブエージェントには最終報告の指定だけを足す(self):
        self.hook(
            "subagent_start", parent_session_id="s1", parent_turn_id="t1", parent_subagent_id=None,
            child_session_id="child", child_subagent_id="sa1", child_role="leaf", child_goal="探して",
        )
        self.assertEqual(self.pre_llm(session_id="child"), [{"context": _subagent_reminder()}])
        self.assertIsNone(self.transform("a", session_id="child"))
        # 親には従来どおり付く
        self.assertIsNotNone(self.transform("a", session_id="s1"))

        self.hook(
            "subagent_stop", parent_session_id="s1", parent_turn_id="t1", child_session_id="child",
            child_role="leaf", child_summary="", child_status="completed", tool_call_history=[], duration_ms=1,
        )
        self.assertEqual(self.pre_llm(session_id="child"), [{"context": _reminder()}])


class LanguageSettingTest(AlwaysJapaneseTest):
    settings = {"language": "English"}

    def test_言語設定を反映する(self):
        self.assertEqual(self.pre_llm(), [{"context": _reminder("English")}])
        self.assertEqual(self.transform("o"), f"o\n\n{_note('English')}")


class BlankLanguageTest(AlwaysJapaneseTest):
    settings = {"language": "  "}

    def test_空の言語設定は日本語に戻す(self):
        self.assertEqual(self.pre_llm(), [{"context": _reminder()}])


class OnToolCallOffTest(AlwaysJapaneseTest):
    settings = {"on_tool_call": False}

    def test_ツール結果には足さず入力時には足す(self):
        self.assertIsNone(self.transform("o"))
        self.assertEqual(self.pre_llm(), [{"context": _reminder()}])


if __name__ == "__main__":
    unittest.main()
