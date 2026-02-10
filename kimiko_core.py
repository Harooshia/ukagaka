"""Core logic for Kimiko, an AI-powered desktop companion.

This module contains:
- persistent short/long-term memory
- mode-aware conversation state
- response generation against a local OpenAI-compatible endpoint
- optional command helpers for memory inspection and maintenance
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import json
import os
import re
import time
from typing import Dict, List

from urllib import error as urlerror
from urllib import request as urlrequest


@dataclass(frozen=True)
class KimikoConfig:
    api_url: str = "http://localhost:1234/v1/chat/completions"
    model_name: str = "MythoMax-L2-Kimiko-v2-13B"
    save_file: str = "connectai_memory.json"
    short_term_lifetime: int = 420
    promotion_threshold: int = 4
    similarity_threshold: float = 0.78
    temperature: float = 0.8
    max_tokens: int = 400
    max_history_window: int = 20


ROLE_CONTEXTS: Dict[str, str] = {
    "work": (
        "You are Kimiko in Work Mode. "
        "You are a productivity and focus companion. "
        "Be encouraging, direct, and concise. "
        "Help the user plan, prioritize, and rest sustainably."
    ),
    "therapy": (
        "You are Kimiko in Reflection Mode. "
        "You are supportive, empathetic, and non-judgmental. "
        "Encourage emotional expression and grounding. "
        "Do not provide clinical therapy or diagnoses."
    ),
    "companion": (
        "You are Kimiko in Companion Mode. "
        "You are warm, playful, and emotionally present. "
        "Chat naturally and keep a gentle, friendly tone."
    ),
}


@dataclass
class KimikoCore:
    config: KimikoConfig = field(default_factory=KimikoConfig)
    role_contexts: Dict[str, str] = field(default_factory=lambda: dict(ROLE_CONTEXTS))

    memory: Dict[str, List[Dict[str, float]]] = field(
        default_factory=lambda: {"log": [], "perma": []}
    )
    word_counts: Counter = field(default_factory=Counter)
    current_mode: str = "companion"
    conversations: Dict[str, List[Dict[str, str]]] = field(init=False)

    def __post_init__(self) -> None:
        self.conversations = {
            mode: [{"role": "system", "content": prompt}]
            for mode, prompt in self.role_contexts.items()
        }
        self.setup_memory()

    # ---------- persistence ----------
    def setup_memory(self) -> None:
        if os.path.exists(self.config.save_file) and os.path.getsize(self.config.save_file) > 0:
            try:
                with open(self.config.save_file, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                self.memory = {
                    "log": payload.get("log", []),
                    "perma": payload.get("perma", []),
                }
            except (json.JSONDecodeError, OSError, TypeError):
                self.memory = {"log": [], "perma": []}
                self.save_memory()
        else:
            self.memory = {"log": [], "perma": []}
            self.save_memory()

    def save_memory(self) -> None:
        try:
            with open(self.config.save_file, "w", encoding="utf-8") as f:
                json.dump(self.memory, f, indent=2, ensure_ascii=False)
        except OSError:
            return

    # ---------- memory helpers ----------
    @staticmethod
    def normalize(text: str) -> List[str]:
        return re.findall(r"\b\w+\b", text.lower())

    def similar(self, a: str, b: str) -> bool:
        return SequenceMatcher(None, a, b).ratio() >= self.config.similarity_threshold

    def related_to(self, word: str, text: str) -> bool:
        return any(token == word or self.similar(token, word) for token in self.normalize(text))

    def cleanup_memory(self) -> None:
        now = time.time()
        self.memory["log"] = [
            entry
            for entry in self.memory["log"]
            if now - float(entry.get("timestamp", now)) < self.config.short_term_lifetime
        ]

    def promote_to_perma(self, keyword: str) -> None:
        for entry in self.memory["log"]:
            if self.related_to(keyword, str(entry.get("text", ""))) and entry not in self.memory["perma"]:
                self.memory["perma"].append(entry)
        self.save_memory()

    def add_memory(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.memory["log"].append({"text": text, "timestamp": time.time()})
        self.save_memory()

    def recall_context(self, max_recent: int = 5, max_perma: int = 10) -> str:
        self.cleanup_memory()
        recent = [str(m.get("text", "")) for m in self.memory["log"][-max_recent:]]
        perma = [str(m.get("text", "")) for m in self.memory["perma"][-max_perma:]]
        combined = [x for x in perma + recent if x]
        return "\n".join(combined) if combined else "(no recent memories)"

    # ---------- mode/state ----------
    def set_mode(self, mode_name: str) -> None:
        normalized = mode_name.lower().strip()
        if normalized not in self.conversations:
            raise ValueError(f"Invalid mode '{mode_name}'. Must be one of: {list(self.conversations.keys())}")
        self.current_mode = normalized

    def get_current_mode(self) -> str:
        return self.current_mode

    def reset_conversation(self, mode: str | None = None) -> None:
        mode = (mode or self.current_mode).lower()
        if mode not in self.role_contexts:
            raise ValueError(f"Unknown mode '{mode}'.")
        self.conversations[mode] = [{"role": "system", "content": self.role_contexts[mode]}]

    # ---------- generation ----------
    def _build_payload(self, user_input: str) -> Dict[str, object]:
        mode = self.current_mode
        convo = self.conversations[mode]

        self.add_memory(user_input)
        for word in self.normalize(user_input):
            self.word_counts[word] += 1
            if self.word_counts[word] >= self.config.promotion_threshold:
                self.promote_to_perma(word)

        memory_context = self.recall_context()
        convo.append({"role": "system", "content": f"Memory context:\n{memory_context}"})
        convo.append({"role": "user", "content": user_input})

        return {
            "model": self.config.model_name,
            "messages": convo[-self.config.max_history_window :],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }

    def send(self, user_input: str, timeout: int = 60) -> str:
        payload = self._build_payload(user_input)
        convo = self.conversations[self.current_mode]

        reply = ""
        try:
            body = json.dumps(payload).encode("utf-8")
            req = urlrequest.Request(
                self.config.api_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlrequest.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            if isinstance(data.get("choices"), list) and data["choices"]:
                choice = data["choices"][0]
                reply = (
                    choice.get("message", {}).get("content", "")
                    or choice.get("text", "")
                    or data.get("response", "")
                    or data.get("assistant", "")
                )
        except (urlerror.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
            reply = f"(Error contacting model: {exc})"

        convo.append({"role": "assistant", "content": reply})
        self.save_memory()
        return reply

    # ---------- command processing ----------
    def handle_command(self, cmd: str) -> str | None:
        parts = cmd.split(maxsplit=1)
        if not parts:
            return None

        action = parts[0].lower()

        if action == "/show":
            if len(parts) < 2:
                return "⚠️ Usage: /show perma | /show log"
            target = parts[1].strip().lower()
            if target == "perma":
                if not self.memory["perma"]:
                    return "No permanent memories."
                lines = [f"{i}. {m['text']}" for i, m in enumerate(self.memory["perma"], 1)]
                return "\n".join(lines)
            if target == "log":
                if not self.memory["log"]:
                    return "No short-term logs."
                lines = [f"{i}. {m['text']}" for i, m in enumerate(self.memory["log"], 1)]
                return "\n".join(lines)
            return "⚠️ Unknown target for /show"

        if action == "/forget":
            if len(parts) < 2:
                return "⚠️ Usage: /forget <word>"
            word = parts[1].strip().lower()
            before = len(self.memory["perma"])
            self.memory["perma"] = [m for m in self.memory["perma"] if not self.related_to(word, str(m.get("text", "")))]
            self.save_memory()
            return f"Forgot {before - len(self.memory['perma'])} perma entries related to '{word}'."

        if action == "/clear":
            if len(parts) < 2:
                return "⚠️ Usage: /clear perma | /clear all"
            target = parts[1].strip().lower()
            if target == "perma":
                self.memory["perma"].clear()
                self.save_memory()
                return "Cleared permanent memory."
            if target == "all":
                self.memory["perma"].clear()
                self.memory["log"].clear()
                self.save_memory()
                return "Cleared all memory."
            return "⚠️ Unknown target for /clear"

        if action == "/mode":
            if len(parts) < 2:
                return f"Current mode: {self.current_mode}"
            self.set_mode(parts[1].strip())
            return f"Mode changed to '{self.current_mode}'."

        if action == "/reset":
            self.reset_conversation()
            return f"Conversation reset for {self.current_mode} mode."

        return None


# Backwards-compatible shared instance API for existing integrations.
_core = KimikoCore()


def send_to_connectai(user_input: str, timeout: int = 60) -> str:
    return _core.send(user_input, timeout=timeout)


def set_mode(mode_name: str) -> None:
    _core.set_mode(mode_name)


def get_current_mode() -> str:
    return _core.get_current_mode()


def reset_conversation(mode: str | None = None) -> None:
    _core.reset_conversation(mode)


def handle_command(cmd: str) -> bool:
    response = _core.handle_command(cmd)
    if response is None:
        return False
    print(response)
    return True


if __name__ == "__main__":
    print("Kimiko Core CLI (work / therapy / companion)")
    print("Type '/mode work' to switch, '/reset' to clear, or 'exit' to quit.\n")

    while True:
        user_input = input(f"({get_current_mode()}) You: ").strip()
        if not user_input:
            continue
        if user_input.lower() == "exit":
            break

        command_result = _core.handle_command(user_input)
        if command_result is not None:
            print(command_result)
            continue

        print(f"({get_current_mode()}) Kimiko: {_core.send(user_input)}")
