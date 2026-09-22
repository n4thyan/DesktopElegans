from __future__ import annotations

import tkinter as tk

root = tk.Tk()
root.title("Worm Input Host | clicked=0 typed=0 scrolled=0")
root.geometry("640x420+220+180")
clicked = 0
typed = 0
scrolled = 0

text = tk.Text(root, wrap="word", font=("Segoe UI", 12))
text.pack(fill="both", expand=True)
text.insert("1.0", "Click here, type, and scroll.\n" + "Underlying input line\n" * 80)
text.mark_set("insert", "1.0")


def refresh_title() -> None:
    root.title(f"Worm Input Host | clicked={clicked} typed={typed} scrolled={scrolled}")


def on_click(_event) -> None:
    global clicked
    clicked += 1
    refresh_title()


def on_key(event) -> None:
    global typed
    if event.char:
        typed += 1
        refresh_title()


def on_scroll(_event) -> None:
    global scrolled
    scrolled += 1
    refresh_title()


root.bind_all("<Button-1>", on_click)
root.bind_all("<KeyPress>", on_key)
root.bind_all("<MouseWheel>", on_scroll)
root.mainloop()
