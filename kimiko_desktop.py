"""Tkinter desktop ghost UI for Kimiko.

- Always-on-top, borderless character overlay
- Hover/click interactions with playful animation
- Dock/collapse behavior on right screen edge
- Lightweight speech bubble and text input for chatting
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk

from kimiko_core import KimikoCore


class KimikoDesktopGhost:
    def __init__(self) -> None:
        self.core = KimikoCore()
        self.root = tk.Tk()
        self.root.title("Kimiko Ghost")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#00ff00")
        self.root.wm_attributes("-transparentcolor", "#00ff00")

        self.width = 150
        self.height = 180
        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()

        self.visible_x = self.screen_w - self.width - 30
        self.hidden_x = self.screen_w - 30
        self.y = self.screen_h - self.height - 80
        self.current_x = self.hidden_x
        self.is_collapsed = True
        self.is_bubble_open = False
        self.is_animating = False

        self.response_queue: queue.Queue[str] = queue.Queue()

        self.root.geometry(f"{self.width}x{self.height}+{self.current_x}+{self.y}")

        self.canvas = tk.Canvas(
            self.root,
            width=self.width,
            height=self.height,
            bg="#00ff00",
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self._draw_character(idle=True)
        self._setup_bindings()
        self._create_bubble()

        self.swoop_in()
        self.root.after(80, self._poll_queue)

    def _setup_bindings(self) -> None:
        self.canvas.bind("<Enter>", self.on_hover_enter)
        self.canvas.bind("<Leave>", self.on_hover_leave)
        self.canvas.bind("<Button-1>", self.on_click)

    def _draw_character(self, idle: bool = True, excited: bool = False) -> None:
        self.canvas.delete("ghost")

        body_color = "#f5f5ff" if idle else "#ececff"
        self.canvas.create_oval(35, 35, 120, 145, fill=body_color, outline="#9999dd", width=2, tags="ghost")
        self.canvas.create_oval(47, 50, 108, 120, fill="white", outline="", tags="ghost")

        eye_y = 72 if not excited else 68
        self.canvas.create_oval(64, eye_y, 72, eye_y + 8, fill="#23233c", outline="", tags="ghost")
        self.canvas.create_oval(85, eye_y, 93, eye_y + 8, fill="#23233c", outline="", tags="ghost")

        self.canvas.create_arc(67, 90, 90, 108, start=200, extent=140, style="arc", width=2, outline="#555577", tags="ghost")

        self.canvas.create_text(
            76,
            24,
            text="Kimiko",
            font=("Segoe UI", 10, "bold"),
            fill="#5f5fa3",
            tags="ghost",
        )
        self.canvas.create_text(
            76,
            160,
            text="click to chat",
            font=("Segoe UI", 8),
            fill="#666688",
            tags="ghost",
        )

        if excited:
            self.canvas.create_text(110, 55, text="✨", font=("Segoe UI Emoji", 12), tags="ghost")

    def _create_bubble(self) -> None:
        self.bubble = tk.Toplevel(self.root)
        self.bubble.withdraw()
        self.bubble.overrideredirect(True)
        self.bubble.attributes("-topmost", True)
        self.bubble.configure(bg="#efeefe")

        container = tk.Frame(self.bubble, bg="#efeefe", bd=2, relief="solid")
        container.pack(fill="both", expand=True)

        self.dialog_label = tk.Label(
            container,
            text="Hi! I'm Kimiko. How are you feeling today?",
            justify="left",
            anchor="w",
            wraplength=260,
            bg="#efeefe",
            fg="#2f2f4f",
            font=("Segoe UI", 10),
            padx=10,
            pady=8,
        )
        self.dialog_label.pack(fill="x")

        input_row = tk.Frame(container, bg="#efeefe")
        input_row.pack(fill="x", padx=8, pady=(0, 8))

        self.entry = tk.Entry(input_row, font=("Segoe UI", 10), relief="solid", bd=1)
        self.entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.entry.bind("<Return>", self.on_submit)

        self.send_btn = tk.Button(
            input_row,
            text="Send",
            command=self.on_submit,
            bg="#d8d8ff",
            fg="#2f2f4f",
            relief="flat",
            padx=10,
        )
        self.send_btn.pack(side="left", padx=(6, 0))

    def bubble_position(self) -> str:
        bubble_w = 300
        bubble_h = 170
        gap = 12
        x = self.current_x - bubble_w - gap
        y = self.y + 10
        if x < 10:
            x = self.current_x + self.width + gap
        return f"{bubble_w}x{bubble_h}+{x}+{y}"

    def toggle_bubble(self) -> None:
        if self.is_bubble_open:
            self.bubble.withdraw()
            self.is_bubble_open = False
            return

        self.bubble.geometry(self.bubble_position())
        self.bubble.deiconify()
        self.bubble.lift()
        self.entry.focus_set()
        self.is_bubble_open = True

    def on_hover_enter(self, _event=None) -> None:
        self._draw_character(idle=False, excited=True)
        if self.is_collapsed and not self.is_animating:
            self.swoop_in()

    def on_hover_leave(self, _event=None) -> None:
        self._draw_character(idle=True)

    def on_click(self, _event=None) -> None:
        if self.is_collapsed and not self.is_animating:
            self.swoop_in(after=self.toggle_bubble)
            return
        self.toggle_bubble()

    def on_submit(self, _event=None) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")

        command_result = self.core.handle_command(text)
        if command_result is not None:
            self.dialog_label.config(text=command_result)
            return

        self.dialog_label.config(text="Kimiko is thinking...")
        threading.Thread(target=self._get_reply, args=(text,), daemon=True).start()

    def _get_reply(self, text: str) -> None:
        reply = self.core.send(text)
        self.response_queue.put(reply)

    def _poll_queue(self) -> None:
        while not self.response_queue.empty():
            self.dialog_label.config(text=self.response_queue.get())

        if self.is_bubble_open:
            self.bubble.geometry(self.bubble_position())

        self.root.after(80, self._poll_queue)

    def _animate_to(self, target_x: int, speed: int = 18, after=None) -> None:
        self.is_animating = True

        def step() -> None:
            delta = target_x - self.current_x
            if abs(delta) <= speed:
                self.current_x = target_x
                self.root.geometry(f"{self.width}x{self.height}+{self.current_x}+{self.y}")
                self.is_animating = False
                if after:
                    after()
                return

            self.current_x += speed if delta > 0 else -speed
            self.root.geometry(f"{self.width}x{self.height}+{self.current_x}+{self.y}")
            self.root.after(16, step)

        step()

    def swoop_in(self, after=None) -> None:
        self.is_collapsed = False
        self._animate_to(self.visible_x, after=after)

    def swoop_out(self) -> None:
        self.is_collapsed = True
        self._animate_to(self.hidden_x)
        if self.is_bubble_open:
            self.bubble.withdraw()
            self.is_bubble_open = False

    def run(self) -> None:
        self.root.bind("<Escape>", lambda _e: self.root.destroy())
        self.root.bind("<Double-Button-1>", lambda _e: self.swoop_out())
        self.root.mainloop()


if __name__ == "__main__":
    KimikoDesktopGhost().run()
