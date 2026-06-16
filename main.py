#!/usr/bin/env python3
"""DXF Editor - entry point."""

import sys
import tkinter as tk

from app.editor import DXFEditor


def main() -> None:
    root = tk.Tk()
    filepath = sys.argv[1] if len(sys.argv) > 1 else None
    DXFEditor(root, filepath)
    root.mainloop()


if __name__ == "__main__":
    main()
