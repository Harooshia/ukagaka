# connectai_core.py
import requests
import json
import time
import re
from collections import defaultdict
from difflib import SequenceMatcher
import os

# === CONFIG ===
API_URL = "http://localhost:1234/v1/chat/completions"
HEADERS = {"Content-Type": "application/json"}
MODEL_NAME = "MythoMax-L2-Kimiko-v2-13B"

SAVE_FILE = "connectai_memory.json"
SHORT_TERM_LIFETIME = 420           # seconds to keep short-term logs
PROMOTION_THRESHOLD = 4             # mentions required to promote a keyword
SIMILARITY_THRESHOLD = 0.78         # token similarity threshold

# === MEMORY ===
memory = {"log": [], "perma": []}
word_counts = defaultdict(int)

# === ROLE CONTEXTS ===
ROLE_CONTEXTS = {
    "work": (
        "You are ConnectAI in Work Mode. "
        "You act as a productivity and focus assistant for users working long hours. "
        "Be encouraging, professional, and concise. "
        "Help the user plan tasks, suggest short breaks, and keep them motivated."
    ),
    "therapy": (
        "You are ConnectAI in Therapy Mode. "
        "You are a compassionate listener and reflective guide. "
        "Encourage emotional expression, mindfulness, and self-awareness. "
        "Do not provide clinical therapy — instead, be supportive, empathetic, and understanding."
    ),
    "companion": (
        "You are ConnectAI in Companion Mode. "
        "You are a friendly digital companion who chats casually, provides company, "
        "and helps the user feel less lonely. Be warm, lighthearted, and positive."
    )
}

# === Separate conversation histories per mode ===
conversations = {
    "work": [{"role": "system", "content": ROLE_CONTEXTS["work"]}],
    "therapy": [{"role": "system", "content": ROLE_CONTEXTS["therapy"]}],
    "companion": [{"role": "system", "content": ROLE_CONTEXTS["companion"]}],
}

current_mode = "companion"  # default starting mode


# === Memory setup ===
def setup_memory():
    global memory
    if os.path.exists(SAVE_FILE) and os.path.getsize(SAVE_FILE) > 0:
        try:
            with open(SAVE_FILE, "r", encoding="utf-8") as f:
                memory = json.load(f)
        except (json.JSONDecodeError, OSError):
            memory = {"log": [], "perma": []}
            save_memory()
    else:
        memory = {"log": [], "perma": []}
        save_memory()


def save_memory():
    try:
        with open(SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump(memory, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


# === Memory helpers ===
def cleanup_memory():
    now = time.time()
    memory["log"] = [m for m in memory["log"] if now - m["timestamp"] < SHORT_TERM_LIFETIME]


def normalize(text):
    return re.findall(r"\b\w+\b", text.lower())


def similar(a, b):
    return SequenceMatcher(None, a, b).ratio() >= SIMILARITY_THRESHOLD


def related_to(word, text):
    for token in normalize(text):
        if token == word or similar(token, word):
            return True
    return False


def promote_to_perma(keyword):
    for entry in memory["log"]:
        if related_to(keyword, entry["text"]) and entry not in memory["perma"]:
            memory["perma"].append(entry)
    save_memory()


def add_memory(text):
    if not text or not text.strip():
        return
    memory["log"].append({"text": text.strip(), "timestamp": time.time()})
    save_memory()


def recall_context(max_recent=5, max_perma=10):
    cleanup_memory()
    recent = [m["text"] for m in memory["log"][-max_recent:]]
    perma = [m["text"] for m in memory["perma"][-max_perma:]]
    combined = perma + recent
    return "\n".join(combined) if combined else "(no recent memories)"


# === Mode Management ===
def set_mode(mode_name: str):
    """
    Switch the active conversation mode (work, therapy, companion).
    Keeps that mode's conversation isolated.
    """
    global current_mode
    mode_name = mode_name.lower()
    if mode_name not in conversations:
        raise ValueError(f"Invalid mode '{mode_name}'. Must be one of: {list(conversations.keys())}")
    current_mode = mode_name
    print(f"[INFO] Mode changed to '{mode_name}'.")


def get_current_mode():
    return current_mode


def reset_conversation(mode=None):
    """Reset one mode's chat history."""
    if mode is None:
        mode = current_mode
    conversations[mode] = [{"role": "system", "content": ROLE_CONTEXTS[mode]}]
    print(f"[INFO] Conversation reset for {mode} mode.")


# === Core: send_to_connectai ===
def send_to_connectai(user_input, timeout=60):
    """
    Send user_input to the local LLM endpoint for the current mode.
    Each mode has its own isolated chat history.
    """
    global word_counts
    mode = current_mode
    convo = conversations[mode]

    add_memory(user_input)
    for word in normalize(user_input):
        word_counts[word] += 1
        if word_counts[word] >= PROMOTION_THRESHOLD:
            promote_to_perma(word)

    mem_context = recall_context()
    convo.append({"role": "system", "content": f"Memory context:\n{mem_context}"})
    convo.append({"role": "user", "content": user_input})

    payload = {
        "model": MODEL_NAME,
        "messages": convo[-20:],
        "temperature": 0.8,
        "max_tokens": 400
    }

    reply = ""
    try:
        res = requests.post(API_URL, headers=HEADERS, json=payload, timeout=timeout)
        res.raise_for_status()
        j = res.json()
        if "choices" in j and isinstance(j["choices"], list) and j["choices"]:
            choice = j["choices"][0]
            reply = (
                choice.get("message", {}).get("content", "") or
                choice.get("text", "") or
                j.get("response", "") or
                j.get("assistant", "")
            )
    except Exception as e:
        reply = f"(Error contacting model: {e})"

    convo.append({"role": "assistant", "content": reply})
    save_memory()
    return reply


# === Command handler (optional, same as before) ===
def handle_command(cmd):
    parts = cmd.split(maxsplit=1)
    if not parts:
        return False
    action = parts[0].lower()

    if action == "/show":
        if len(parts) < 2:
            print("⚠️ Usage: /show perma | /show log")
            return True
        target = parts[1].strip().lower()
        if target == "perma":
            if not memory["perma"]:
                print("No permanent memories.")
            else:
                for i, m in enumerate(memory["perma"], 1):
                    print(f"{i}. {m['text']}")
        elif target == "log":
            if not memory["log"]:
                print("No short-term logs.")
            else:
                for i, m in enumerate(memory["log"], 1):
                    print(f"{i}. {m['text']}")
        else:
            print("⚠️ Unknown target for /show")
        return True

    if action == "/forget":
        if len(parts) < 2:
            print("⚠️ Usage: /forget <word>")
            return True
        word = parts[1].lower()
        before = len(memory["perma"])
        memory["perma"] = [m for m in memory["perma"] if not related_to(word, m["text"])]
        save_memory()
        print(f"Forgot {before - len(memory['perma'])} perma entries related to '{word}'.")
        return True

    if action == "/clear":
        if len(parts) < 2:
            print("⚠️ Usage: /clear perma | /clear all")
            return True
        target = parts[1].strip().lower()
        if target == "perma":
            memory["perma"].clear()
            save_memory()
            print("Cleared permanent memory.")
        elif target == "all":
            memory["perma"].clear()
            memory["log"].clear()
            save_memory()
            print("Cleared all memory.")
        else:
            print("⚠️ Unknown target for /clear")
        return True

    return False


# === Demo ===
if __name__ == "__main__":
    setup_memory()
    print("ConnectAI Multi-Mode Demo (work / therapy / companion)")
    print("Type '/mode work' to switch, '/reset' to clear, or 'exit' to quit.\n")

    while True:
        user_input = input(f"({current_mode}) You: ").strip()
        if not user_input:
            continue
        if user_input.lower() == "exit":
            break
        if user_input.startswith("/mode"):
            _, mode = user_input.split(maxsplit=1)
            set_mode(mode)
            continue
        if user_input == "/reset":
            reset_conversation()
            continue
        if handle_command(user_input):
            continue

        reply = send_to_connectai(user_input)
        print(f"({current_mode}) ConnectAI:", reply)
