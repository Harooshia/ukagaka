"""Tkinter desktop companion UI for Kimiko.

Features:
- Always-on-top, borderless overlay
- Right-click context menu (Ukagaka-style quick actions)
- Draggable within a constrained bottom-right movement area
- Stable dock/undock with a dedicated always-visible dock handle
- Image-based character rendering with open/closed speaking effect
- Idle sleep state after inactivity
"""

from __future__ import annotations

from pathlib import Path
import queue
import threading
import time
import tkinter as tk

from kimiko_core import KimikoCore

try:
    from PIL import Image, ImageTk
except ImportError:  # Optional: app still runs with tkinter-only image loading.
    Image = None
    ImageTk = None


class KimikoDesktopGhost:
    def __init__(self) -> None:
        self.core = KimikoCore()
        self.root = tk.Tk()
        self.root.title("Kimiko")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#00ff00")
        self.root.wm_attributes("-transparentcolor", "#00ff00")

        self.width = 320
        self.height = 240
        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()

        self.drag_zone_w = int(self.screen_w * 0.45)
        self.drag_zone_h = int(self.screen_h * 0.42)
        self.drag_min_x = self.screen_w - self.drag_zone_w
        self.drag_max_x = self.screen_w - self.width - 8
        self.drag_min_y = self.screen_h - self.drag_zone_h
        self.drag_max_y = self.screen_h - self.height - 8

        self.visible_x = self.screen_w - self.width - 24
        self.hidden_x = self.screen_w + 4  # fully off-screen to avoid visual glitches
        self.y = self.screen_h - self.height - 48
        self.current_x = self.visible_x

        self.is_collapsed = False
        self.is_bubble_open = False
        self.is_animating = False
        self.is_dragging = False

        self.drag_start_mouse = (0, 0)
        self.drag_start_pos = (0, 0)

        self.response_queue: queue.Queue[str] = queue.Queue()

        self.last_interaction_ts = time.time()
        self.sleep_timeout_seconds = 45
        self.is_sleeping = False

        self.image_pairs = self._load_image_pairs()
        self.expression_order = ["happy", "nervous", "worried"]
        self.active_expression = self._pick_default_expression()

        self.talk_open = False
        self.is_talking = False

        self.root.geometry(f"{self.width}x{self.height}+{self.current_x}+{self.y}")
        self.canvas = tk.Canvas(
            self.root,
            width=self.width,
            height=self.height,
            bg="#00ff00",
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self._create_dock_handle()
        self._create_context_menu()
        self._setup_bindings()
        self._create_bubble()
        self._draw_character()

        self.root.after(90, self._poll_queue)
        self.root.after(140, self._talk_tick)
        self.root.after(1000, self._idle_tick)

    # ---------------- image loading & rendering ----------------
    def _prepare_image_for_colorkey_window(self, img):
        """Normalize alpha for colorkey transparency windows.

        Tk's transparentcolor behaves as color-key transparency (not full per-pixel alpha),
        so semi-transparent edges can blend with the green key color and look like green halos.
        We harden near-transparent pixels and clamp edge alpha to remove visible green fringes.
        """
        if Image is None:
            return img

        rgba = img.convert("RGBA")
        px = rgba.load()
        width, height = rgba.size

        for y in range(height):
            for x in range(width):
                r, g, b, a = px[x, y]

                # Remove extremely transparent pixels entirely.
                if a <= 24:
                    px[x, y] = (r, g, b, 0)
                    continue

                # Avoid half-transparent edge blending against green transparentcolor key.
                if a < 220:
                    # Edge color de-spill: neutralize green-heavy edge pixels first.
                    if g > r and g > b:
                        g = int((r + b) / 2)
                    px[x, y] = (r, g, b, 0)
                    continue

                # Keep interior opaque, but de-spill any green tint.
                if g > r * 1.1 and g > b * 1.1:
                    g = int((r + b) / 2)
                px[x, y] = (r, g, b, 255)

        return rgba

    def _fit_image(self, img):
        """Scale image to fit character area while keeping full character visible."""
        if Image is None:
            return img

        max_w = int(self.width * 0.88)
        max_h = int(self.height * 0.88)
        src_w, src_h = img.size
        if src_w <= 0 or src_h <= 0:
            return img

        scale = min(max_w / src_w, max_h / src_h)
        new_size = (max(1, int(src_w * scale)), max(1, int(src_h * scale)))
        return img.resize(new_size, Image.Resampling.LANCZOS)

    def _load_image_file(self, path: Path):
        if Image is not None and ImageTk is not None:
            try:
                pil_img = Image.open(path)
                pil_img = self._prepare_image_for_colorkey_window(pil_img)
                pil_img = self._fit_image(pil_img)
                return ImageTk.PhotoImage(pil_img)
            except Exception:
                return None

        try:
            return tk.PhotoImage(file=str(path))
        except tk.TclError:
            return None

    def _load_image_pairs(self) -> dict[str, tuple[tk.PhotoImage | None, tk.PhotoImage | None]]:
        """Load expression pairs from files next to this script.

        Supported stems include exact names from user assets:
        - Happy_Open.png / Happy_Closed.png
        - Nervous_Open.png / Nervous_Closed.png
        - Worried_Open.png / Worried_Closed.png
        """
        folder = Path(__file__).resolve().parent
        files = sorted(folder.glob("*.png"))

        grouped: dict[str, dict[str, tk.PhotoImage]] = {}
        for file in files:
            stem = file.stem.lower()
            if stem.endswith("_open"):
                key, mouth = stem[:-5], "open"
            elif stem.endswith("_closed"):
                key, mouth = stem[:-7], "closed"
            elif stem.endswith("-open"):
                key, mouth = stem[:-5], "open"
            elif stem.endswith("-closed"):
                key, mouth = stem[:-7], "closed"
            else:
                continue

            img = self._load_image_file(file)
            if img is None:
                continue

            grouped.setdefault(key, {})[mouth] = img

        pairs: dict[str, tuple[tk.PhotoImage | None, tk.PhotoImage | None]] = {}
        for key, slot in grouped.items():
            closed = slot.get("closed")
            opened = slot.get("open")
            if closed is None and opened is not None:
                closed = opened
            if opened is None and closed is not None:
                opened = closed
            if closed or opened:
                pairs[key] = (closed, opened)

        return pairs

    def _pick_default_expression(self) -> str:
        for name in self.expression_order:
            if name in self.image_pairs:
                return name
        return next(iter(self.image_pairs.keys()), "fallback")

    def _select_idle_expression(self) -> str:
        if self.is_sleeping and "worried" in self.image_pairs:
            return "worried"
        if "happy" in self.image_pairs:
            return "happy"
        return self._pick_default_expression()

    # ---------------- UI construction ----------------
    def _create_dock_handle(self) -> None:
        self.dock = tk.Toplevel(self.root)
        self.dock.withdraw()
        self.dock.overrideredirect(True)
        self.dock.attributes("-topmost", True)
        self.dock.configure(bg="#00ff00")
        self.dock.wm_attributes("-transparentcolor", "#00ff00")

        self.dock_canvas = tk.Canvas(self.dock, width=26, height=110, bg="#00ff00", highlightthickness=0)
        self.dock_canvas.pack(fill="both", expand=True)
        self.dock_canvas.create_rectangle(3, 5, 24, 105, fill="#d9d4ff", outline="#8e84d2", width=2)
        self.dock_canvas.create_text(14, 54, text="◀", font=("Segoe UI", 11, "bold"), fill="#4a417e")

        self.dock_canvas.bind("<Button-1>", self.on_dock_click)
        self.dock_canvas.bind("<Button-3>", self.on_dock_right_click)
        self.dock_canvas.bind("<Button-2>", self.on_dock_right_click)

    def _place_dock_handle(self) -> None:
        x = self.screen_w - 26
        y = int(self.y + (self.height - 110) / 2)
        y = max(0, min(self.screen_h - 110, y))
        self.dock.geometry(f"26x110+{x}+{y}")

    def _create_context_menu(self) -> None:
        self.menu = tk.Menu(self.root, tearoff=0, bg="#f4f3ff", fg="#29254a", activebackground="#dcd8ff")
        self.menu.add_command(label="Open Chat", command=self.toggle_bubble)
        self.menu.add_command(label="Dock / Undock", command=self.toggle_dock)
        self.menu.add_separator()

        mode_menu = tk.Menu(self.menu, tearoff=0, bg="#f4f3ff", fg="#29254a", activebackground="#dcd8ff")
        for mode in ("companion", "work", "therapy"):
            mode_menu.add_command(label=f"Mode: {mode.title()}", command=lambda m=mode: self._set_mode(m))
        self.menu.add_cascade(label="Mode", menu=mode_menu)

        self.menu.add_separator()
        self.menu.add_command(label="Reset Conversation", command=self._reset_conversation)
        self.menu.add_command(label="Quit", command=self.root.destroy)

    def _setup_bindings(self) -> None:
        self.canvas.bind("<Enter>", self.on_hover_enter)
        self.canvas.bind("<Leave>", self.on_hover_leave)
        self.canvas.bind("<ButtonPress-1>", self.on_left_press)
        self.canvas.bind("<B1-Motion>", self.on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_left_release)
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<Button-2>", self.on_right_click)

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
            text="Hi! Right-click me for options.",
            justify="left",
            anchor="w",
            wraplength=290,
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

    # ---------------- state helpers ----------------
    def _register_activity(self) -> None:
        self.last_interaction_ts = time.time()
        if self.is_sleeping:
            self.is_sleeping = False
            self.active_expression = self._select_idle_expression()
            self._draw_character()

    def _idle_tick(self) -> None:
        idle_for = time.time() - self.last_interaction_ts
        should_sleep = idle_for >= self.sleep_timeout_seconds
        if should_sleep != self.is_sleeping:
            self.is_sleeping = should_sleep
            self.active_expression = self._select_idle_expression()
            self._draw_character()
        self.root.after(1000, self._idle_tick)

    def _set_mode(self, mode: str) -> None:
        self._register_activity()
        self.core.set_mode(mode)
        self.dialog_label.config(text=f"Mode changed to {mode}.")

    def _reset_conversation(self) -> None:
        self._register_activity()
        self.core.reset_conversation()
        self.dialog_label.config(text="Conversation reset.")

    # ---------------- drawing & animation ----------------
    def _draw_character(self) -> None:
        self.canvas.delete("all")

        if self.is_collapsed:
            return

        expr = self.active_expression if self.active_expression in self.image_pairs else self._select_idle_expression()
        if expr in self.image_pairs:
            closed, opened = self.image_pairs[expr]
            frame = opened if self.talk_open else closed
            if frame is None:
                frame = closed or opened
            if frame is not None:
                self.canvas.create_image(self.width // 2, self.height // 2, image=frame)
                return

        # fallback vector if no images are available
        self.canvas.create_oval(95, 40, 225, 210, fill="#f3efff", outline="#9a90dd", width=2)
        self.canvas.create_text(160, 22, text="Kimiko", font=("Segoe UI", 10, "bold"), fill="#5f55a2")
        self.canvas.create_oval(140, 110, 150, 120, fill="#2e2b51", outline="")
        self.canvas.create_oval(170, 110, 180, 120, fill="#2e2b51", outline="")

    def _talk_tick(self) -> None:
        if self.is_talking and not self.is_collapsed:
            self.talk_open = not self.talk_open
            self._draw_character()
        elif self.talk_open:
            self.talk_open = False
            self._draw_character()

        self.root.after(140, self._talk_tick)

    def _start_talking(self) -> None:
        self._register_activity()
        self.is_talking = True

    def _stop_talking(self) -> None:
        self.is_talking = False

    def bubble_position(self) -> str:
        bubble_w = 320
        bubble_h = 180
        x = self.current_x - bubble_w - 12
        y = self.y + 8
        if x < 8:
            x = self.current_x + self.width + 12
        return f"{bubble_w}x{bubble_h}+{x}+{y}"

    def toggle_bubble(self) -> None:
        self._register_activity()

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

    # ---------------- interactions ----------------
    def on_hover_enter(self, _event=None) -> None:
        self._register_activity()
        if "nervous" in self.image_pairs:
            self.active_expression = "nervous"
        self._draw_character()

    def on_hover_leave(self, _event=None) -> None:
        self._register_activity()
        self.active_expression = self._select_idle_expression()
        self._draw_character()

    def on_left_press(self, event) -> None:
        self._register_activity()
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

        if self.is_bubble_open:
            self.bubble.geometry(self.bubble_position())

    def on_left_release(self, event) -> None:
        if self.is_dragging:
            return

        if self.is_collapsed:
            self.swoop_in()
            return

        if abs(event.x_root - self.drag_start_mouse[0]) < 3 and abs(event.y_root - self.drag_start_mouse[1]) < 3:
            self.toggle_bubble()

    def on_right_click(self, event) -> None:
        self._register_activity()
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def on_dock_click(self, _event=None) -> None:
        self._register_activity()
        self.swoop_in()

    def on_dock_right_click(self, event) -> None:
        self._register_activity()
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    # ---------------- chat + async ----------------
    def on_submit(self, _event=None) -> None:
        self._register_activity()
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")

        command_result = self.core.handle_command(text)
        if command_result is not None:
            self.dialog_label.config(text=command_result)
            self._start_talking()
            self.root.after(700, self._stop_talking)
            return

        self.dialog_label.config(text="Kimiko is thinking...")
        self._start_talking()
        threading.Thread(target=self._get_reply, args=(text,), daemon=True).start()

    def _get_reply(self, text: str) -> None:
        reply = self.core.send(text)
        self.response_queue.put(reply)

    def _poll_queue(self) -> None:
        while not self.response_queue.empty():
            self.dialog_label.config(text=self.response_queue.get())
            self.root.after(900, self._stop_talking)

        if self.is_bubble_open:
            self.bubble.geometry(self.bubble_position())

        self.root.after(90, self._poll_queue)

    # ---------------- docking animation ----------------
    def _animate_to(self, target_x: int, speed: int = 20, after=None) -> None:
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

    def toggle_dock(self) -> None:
        self._register_activity()
        if self.is_collapsed:
            self.swoop_in()
        else:
            self.swoop_out()

    def swoop_in(self, after=None) -> None:
        if self.is_animating:
            return

        self.is_collapsed = False
        self.dock.withdraw()

        def done() -> None:
            self._draw_character()
            if after:
                after()

        self._animate_to(self.visible_x, after=done)

    def swoop_out(self) -> None:
        if self.is_animating:
            return

        self.is_collapsed = True
        if self.is_bubble_open:
            self.bubble.withdraw()
            self.is_bubble_open = False

        def done() -> None:
            self._draw_character()
            self._place_dock_handle()
            self.dock.deiconify()
            self.dock.lift()

        self._animate_to(self.hidden_x, after=done)

    def run(self) -> None:
        self.root.bind("<Escape>", lambda _e: self.root.destroy())
        self.root.bind("<Double-Button-1>", lambda _e: self.toggle_dock())
        self.root.mainloop()


if __name__ == "__main__":
    KimikoDesktopGhost().run()
