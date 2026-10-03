"""Lets the engine tests run headless (CI has no display and may lack customtkinter/tkinter)."""
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

try:
    import customtkinter  # noqa: F401
except ImportError:
    stub = types.ModuleType("customtkinter")
    stub.CTk = object
    stub.CTkToplevel = object
    sys.modules["customtkinter"] = stub

try:
    import tkinter  # noqa: F401
except ImportError:
    tk = types.ModuleType("tkinter")
    tk.filedialog = None
    tk.messagebox = None
    sys.modules["tkinter"] = tk
