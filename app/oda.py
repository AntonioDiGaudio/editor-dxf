import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from app.constants import CONFIG_FILE


class ODAConverter:
    """Handles DWG to DXF conversion via ODA File Converter."""

    def __init__(self) -> None:
        self._path: str | None = self._load_config()

    # ── Config persistence ───────────────────────────────────────────

    def _load_config(self) -> str | None:
        try:
            if CONFIG_FILE.exists():
                data = json.loads(CONFIG_FILE.read_text())
                return data.get("oda_path")
        except Exception:
            pass
        return None

    def _save_config(self, path: str) -> None:
        try:
            CONFIG_FILE.write_text(json.dumps({"oda_path": path}, indent=2))
        except Exception:
            pass

    # ── Executable discovery ─────────────────────────────────────────

    @property
    def path(self) -> str | None:
        return self._path

    @path.setter
    def path(self, value: str) -> None:
        self._path = value
        self._save_config(value)

    def find(self) -> str | None:
        """Auto-discover ODA File Converter. Returns full path or None."""
        if self._path and os.path.isfile(self._path):
            return self._path

        # 1. Search PATH
        exe_name = "ODAFileConverter" + (".exe" if platform.system() == "Windows" else "")
        found = shutil.which(exe_name)
        if found:
            self._path = found
            self._save_config(found)
            return found

        # On macOS, also try the .app bundle via PATH or mdfind
        found = shutil.which("ODAFileConverter.app")
        if found:
            inner = os.path.join(found, "Contents", "MacOS", "ODAFileConverter")
            if os.path.isfile(inner):
                self._path = inner
                self._save_config(inner)
                return inner

        # 2. System-specific locations
        system = platform.system()
        candidates: list[str] = []

        if system == "Windows":
            candidates = [
                r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
                r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe",
                os.path.expanduser(
                    r"~\AppData\Local\ODA\ODAFileConverter\ODAFileConverter.exe"
                ),
                os.path.expanduser(
                    r"~\AppData\Roaming\ODA\ODAFileConverter\ODAFileConverter.exe"
                ),
            ]
            # Registry
            try:
                from winreg import HKEY_LOCAL_MACHINE, OpenKey, QueryValueEx
                for key_path in (
                    r"SOFTWARE\ODA\ODAFileConverter",
                    r"SOFTWARE\WOW6432Node\ODA\ODAFileConverter",
                ):
                    try:
                        with OpenKey(HKEY_LOCAL_MACHINE, key_path) as key:
                            val = QueryValueEx(key, "InstallPath")[0]
                            if val:
                                candidates.append(
                                    os.path.join(val, "ODAFileConverter.exe")
                                )
                    except Exception:
                        pass
            except Exception:
                pass
        elif system == "Darwin":
            candidates = [
                "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
                os.path.expanduser(
                    "~/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"
                ),
                "/Applications/ODA/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
            ]
            # Spotlight search as last resort
            try:
                result = subprocess.run(
                    ["mdfind", "kMDItemFSName==ODAFileConverter.app"],
                    capture_output=True, text=True, timeout=10
                )
                for line in result.stdout.strip().splitlines():
                    app_path = line.strip()
                    if app_path and os.path.isdir(app_path):
                        inner = os.path.join(app_path, "Contents", "MacOS", "ODAFileConverter")
                        if os.path.isfile(inner):
                            candidates.append(inner)
            except Exception:
                pass

        for cand in candidates:
            if os.path.isfile(cand):
                self._path = cand
                self._save_config(cand)
                return cand

        return None

    def prompt_path(self) -> str | None:
        """Ask the user to locate the ODA converter (last resort fallback)."""
        from tkinter import filedialog
        system = platform.system()
        filetypes = (
            [("ODAFileConverter.exe", "ODAFileConverter.exe"), ("Tutti i file", "*.*")]
            if system == "Windows"
            else [("ODAFileConverter", "ODAFileConverter"), ("Tutti i file", "*.*")]
        )
        path = filedialog.askopenfilename(
            title="Seleziona ODAFileConverter", filetypes=filetypes
        )
        if path and os.path.isfile(path):
            self._path = path
            self._save_config(path)
            return path
        return None

    # ── Conversion ───────────────────────────────────────────────────

    def convert(
        self,
        dwg_path: str,
        out_dxf: str,
        status_callback: Callable[[str], None] | None = None,
    ) -> bool:
        """Convert DWG to DXF. Saves to out_dxf. Returns True on success."""
        if status_callback:
            status_callback("Conversione DWG → DXF in corso...")

        oda = self.find()
        if not oda:
            from tkinter import messagebox
            messagebox.showerror(
                "ODA Converter non trovato",
                "Scaricalo da: https://www.opendesign.com/guestfiles/oda_file_converter"
            )
            return False

        try:
            tmp_input = tempfile.mkdtemp(prefix="dwg_input_")
            tmp_output = tempfile.mkdtemp(prefix="dwg_output_")

            shutil.copy2(dwg_path, tmp_input)

            result = subprocess.run(
                [oda, tmp_input, tmp_output, "AC1032", "AC1032", "0", "0"],
                capture_output=True, text=True, timeout=120
            )

            dxf_files = [f for f in os.listdir(tmp_output)
                         if f.lower().endswith(".dxf")]
            if not dxf_files:
                err = result.stderr or "Nessun file DXF generato"
                from tkinter import messagebox
                messagebox.showerror("Conversione fallita", err)
                return False

            src_dxf = os.path.join(tmp_output, dxf_files[0])
            shutil.copy2(src_dxf, out_dxf)
            return True

        except subprocess.TimeoutExpired:
            from tkinter import messagebox
            messagebox.showerror("Errore", "Conversione timeout (120s)")
            return False
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("Errore conversione", str(e))
            return False
        finally:
            shutil.rmtree(tmp_input, ignore_errors=True)
            shutil.rmtree(tmp_output, ignore_errors=True)
