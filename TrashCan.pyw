"""Double-click me to start the trash can.

The .pyw extension means Windows runs this with pythonw.exe, so no console
window appears behind the floating can.

On the very first run the display components (PySide6) are downloaded and
installed automatically; a small window shows the progress. After that startup
is instant.
"""

import queue
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trashcan.bootstrap import dependencies_present, ensure_dependencies, is_frozen

TITLE = "Floating Trash Can"


def _install_with_progress() -> tuple[bool, str]:
    """Install the dependencies, showing a small progress window while we wait.

    Tkinter ships with Python itself, so this works before anything has been
    installed.  If it is somehow unavailable we just install quietly.
    """
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        return ensure_dependencies()

    updates: queue.Queue = queue.Queue()
    outcome: dict[str, tuple[bool, str]] = {}

    def work() -> None:
        try:
            outcome["value"] = ensure_dependencies(updates.put)
        except Exception as exc:  # never leave the window hanging
            outcome["value"] = (False, str(exc))
        updates.put(None)

    root = tk.Tk()
    root.title(TITLE)
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Setting up for first use", font=("Segoe UI", 11, "bold")).pack(anchor="w")
    status = ttk.Label(frame, text="This takes a minute or two, just this once.", wraplength=400)
    status.pack(anchor="w", pady=(6, 12))
    bar = ttk.Progressbar(frame, mode="indeterminate", length=400)
    bar.pack()
    bar.start(12)

    threading.Thread(target=work, daemon=True).start()

    def poll() -> None:
        try:
            while True:
                message = updates.get_nowait()
                if message is None:
                    root.destroy()
                    return
                status.config(text=str(message))
        except queue.Empty:
            pass
        root.after(120, poll)

    root.after(120, poll)
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = (root.winfo_screenheight() - root.winfo_height()) // 3
    root.geometry(f"+{x}+{y}")
    root.mainloop()

    return outcome.get("value", (False, "Setup was closed before it finished."))


def _report_failure(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            TITLE,
            f"{message}\n\nYou can also install it yourself by running:\n"
            f'"{sys.executable}" -m pip install PySide6-Essentials',
        )
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)


def main() -> int:
    if not is_frozen() and not dependencies_present():
        ready, message = _install_with_progress()
        if not ready:
            _report_failure(message)
            return 1

    from trashcan.app import main as run_app

    return run_app([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
