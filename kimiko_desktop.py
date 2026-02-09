"""Tkinter desktop ghost UI for Kimiko.

- Always-on-top, borderless character overlay
- Hover/click interactions with playful animation
- Right-click context menu (Ukagaka-style quick actions)
- Draggable within a constrained bottom-right movement area
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

        self.width = 160
        self.height = 190
        self.peek_width = 18
        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()

        # Keep movement in a dedicated lower-right zone so the companion stays unobtrusive.
        self.drag_zone_w = int(self.screen_w * 0.45)
        self.drag_zone_h = int(self.screen_h * 0.42)
        self.drag_min_x = self.screen_w - self.drag_zone_w
        self.drag_max_x = self.screen_w - self.width - 8
        self.drag_min_y = self.screen_h - self.drag_zone_h
        self.drag_max_y = self.screen_h - self.height - 8

        self.visible_x = self.screen_w - self.width - 30
        self.hidden_x = self.screen_w - self.peek_width
        self.y = self.screen_h - self.height - 80
        self.current_x = self.hidden_x
        self.is_collapsed = True
        self.is_bubble_open = False
        self.is_animating = False

        self.is_dragging = False
        self.drag_start_mouse = (0, 0)
        self.drag_start_pos = (0, 0)

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

        self._create_context_menu()
        self._draw_character(idle=True)
        self._setup_bindings()
        self._create_bubble()

        self.swoop_in()
        self.root.after(80, self._poll_queue)

    def _create_context_menu(self) -> None:
        self.menu = tk.Menu(self.root, tearoff=0, bg="#f4f3ff", fg="#29254a", activebackground="#dcd8ff")
        self.menu.add_command(label="Open Chat", command=self.toggle_bubble)
        self.menu.add_command(label="Dock / Undock", command=self.toggle_dock)
        self.menu.add_separator()

        mode_menu = tk.Menu(self.menu, tearoff=0, bg="#f4f3ff", fg="#29254a", activebackground="#dcd8ff")
        for mode in ("companion", "work", "therapy"):
            mode_menu.add_command(label=f"Mode: {mode.title()}", command=lambda m=mode: self._set_mode(m))
        self.menu.add_cascade(label="Mode", menu=mode_menu)

        self.menu.add_command(label="Reset Conversation", command=self._reset_conversation)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self.root.destroy)

    def _setup_bindings(self) -> None:
        self.canvas.bind("<Enter>", self.on_hover_enter)
        self.canvas.bind("<Leave>", self.on_hover_leave)

        self.canvas.bind("<ButtonPress-1>", self.on_left_press)
        self.canvas.bind("<B1-Motion>", self.on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_left_release)

        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<Button-2>", self.on_right_click)

    def _draw_character(self, idle: bool = True, excited: bool = False) -> None:
        self.canvas.delete("all")

        if self.is_collapsed:
            # Docked tab stays visible so the user can always undock.
            self.canvas.create_oval(136, 56, 178, 136, fill="#d9d4ff", outline="#9188d6", width=2)
            self.canvas.create_text(148, 95, text="◀", font=("Segoe UI", 11, "bold"), fill="#4a417e")
            self.canvas.create_text(146, 122, text="✦", font=("Segoe UI", 10), fill="#5f55a2")
            return

        glow = "#eeebff" if idle else "#dfd9ff"
        body = "#fcfbff" if idle else "#f3efff"

        self.canvas.create_oval(28, 24, 132, 156, fill=glow, outline="", width=0)
        self.canvas.create_oval(34, 32, 126, 150, fill=body, outline="#9a90dd", width=2)
        self.canvas.create_oval(47, 48, 112, 126, fill="#ffffff", outline="", width=0)

        eye_y = 74 if not excited else 69
        self.canvas.create_oval(63, eye_y, 72, eye_y + 9, fill="#2e2b51", outline="")
        self.canvas.create_oval(87, eye_y, 96, eye_y + 9, fill="#2e2b51", outline="")
        self.canvas.create_arc(68, 92, 92, 112, start=198, extent=145, style="arc", width=2, outline="#635a9f")

        self.canvas.create_text(
            80,
            18,
            text="Kimiko",
            font=("Segoe UI", 10, "bold"),
            fill="#5f55a2",
        )

        if excited:
            self.canvas.create_text(114, 54, text="✨", font=("Segoe UI Emoji", 12), fill="#6b5fc1")

    def _create_bubble(self) -> None:
        self.bubble = tk.Toplevel(self.root)
        self.bubble.withdraw()
        self.bubble.overrideredirect(True)
        self.bubble.attributes("-topmost", True)
        self.bubble.configure(bg="#ebe7ff")

        container = tk.Frame(self.bubble, bg="#ebe7ff", bd=2, relief="solid", highlightbackground="#9f96e4")
        container.pack(fill="both", expand=True)

        self.dialog_label = tk.Label(
            container,
            text="Hi! I'm Kimiko. Right-click me for options.",
            justify="left",
            anchor="w",
            wraplength=270,
            bg="#ebe7ff",
            fg="#2f2a56",
            font=("Segoe UI", 10),
            padx=10,
            pady=8,
        )
        self.dialog_label.pack(fill="x")

        input_row = tk.Frame(container, bg="#ebe7ff")
        input_row.pack(fill="x", padx=8, pady=(0, 8))

        self.entry = tk.Entry(input_row, font=("Segoe UI", 10), relief="solid", bd=1)
        self.entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.entry.bind("<Return>", self.on_submit)

        self.send_btn = tk.Button(
            input_row,
            text="Send",
            command=self.on_submit,
            bg="#d9d4ff",
            fg="#322c61",
            relief="flat",
            padx=10,
        )
        self.send_btn.pack(side="left", padx=(6, 0))

    def _set_mode(self, mode: str) -> None:
        self.core.set_mode(mode)
        self.dialog_label.config(text=f"Mode changed to {mode}.")

    def _reset_conversation(self) -> None:
        self.core.reset_conversation()
        self.dialog_label.config(text="Conversation reset.")

    def bubble_position(self) -> str:
        bubble_w = 312
        bubble_h = 176
        gap = 12
        x = self.current_x - bubble_w - gap
        y = self.y + 8
        if x < 8:
            x = self.current_x + self.width + gap
        return f"{bubble_w}x{bubble_h}+{x}+{y}"

    def toggle_bubble(self) -> None:
        if self.is_bubble_open:
            self.bubble.withdraw()
            self.is_bubble_open = False
            return

        if self.is_collapsed:
            self.swoop_in(after=self._open_bubble)
            return
        self._open_bubble()

    def _open_bubble(self) -> None:
        self.bubble.geometry(self.bubble_position())
        self.bubble.deiconify()
        self.bubble.lift()
        self.entry.focus_set()
        self.is_bubble_open = True

    def on_hover_enter(self, _event=None) -> None:
        self._draw_character(idle=False, excited=True)

    def on_hover_leave(self, _event=None) -> None:
        self._draw_character(idle=True)

    def on_left_press(self, event) -> None:
        self.is_dragging = False
        self.drag_start_mouse = (event.x_root, event.y_root)
        self.drag_start_pos = (self.current_x, self.y)

    def on_left_drag(self, event) -> None:
        if self.is_collapsed or self.is_animating:
            return

        dx = event.x_root - self.drag_start_mouse[0]
        dy = event.y_root - self.drag_start_mouse[1]
        if abs(dx) > 2 or abs(dy) > 2:
            self.is_dragging = True

        new_x = max(self.drag_min_x, min(self.drag_max_x, self.drag_start_pos[0] + dx))
        new_y = max(self.drag_min_y, min(self.drag_max_y, self.drag_start_pos[1] + dy))

        self.current_x = int(new_x)
        self.y = int(new_y)
        self.root.geometry(f"{self.width}x{self.height}+{self.current_x}+{self.y}")

    def on_left_release(self, event) -> None:
        if self.is_dragging:
            return

        if self.is_collapsed:
            self.swoop_in()
            return

        # plain click toggles chat
        if abs(event.x_root - self.drag_start_mouse[0]) < 3 and abs(event.y_root - self.drag_start_mouse[1]) < 3:
            self.toggle_bubble()

    def on_right_click(self, event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

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

    def _animate_to(self, target_x: int, speed: int = 20, after=None) -> None:
        self.is_animating = True

        def step() -> None:
            delta = target_x - self.current_x
            if abs(delta) <= speed:
                self.current_x = target_x
                self.root.geometry(f"{self.width}x{self.height}+{self.current_x}+{self.y}")
                self.is_animating = False
                self._draw_character(idle=True)
                if after:
                    after()
                return

            self.current_x += speed if delta > 0 else -speed
            self.root.geometry(f"{self.width}x{self.height}+{self.current_x}+{self.y}")
            self.root.after(16, step)

        step()

    def toggle_dock(self) -> None:
        if self.is_collapsed:
            self.swoop_in()
        else:
            self.swoop_out()

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
        self.root.bind("<Double-Button-1>", lambda _e: self.toggle_dock())
        self.root.mainloop()


if __name__ == "__main__":
    KimikoDesktopGhost().run()
