import datetime
import subprocess
import re
import os
import sys

# 1. Vygenerování verze podle aktuálního času (např. 26.04.30.1420)
new_version = datetime.datetime.now().strftime("%y%m%d.%H%M")
print(f"--- Generuji verzi: {new_version} ---")

exe_name = f"KickChatCounter_{new_version.replace('.', '_')}"

# 2. Aktualizace BUILD_VERSION v kick_counter_gui.py
main_script = "kick_counter_gui.py"

if os.path.exists(main_script):
    with open(main_script, "r", encoding="utf-8") as f:
        content = f.read()

    content = re.sub(r'BUILD_VERSION\s*=\s*".*?"', f'BUILD_VERSION = "{new_version}"', content)

    with open(main_script, "w", encoding="utf-8") as f:
        f.write(content)
    print("--- Verze v kick_counter_gui.py aktualizována ---")
else:
    print("CHYBA: kick_counter_gui.py nenalezen!")
    exit()

# 3. Spuštění PyInstaller
print("--- Spouštím PyInstaller... ---")
cmd = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--noconsole",
    "--name", exe_name,
    "--collect-all", "customtkinter",
    "--collect-all", "darkdetect",
    "--hidden-import", "curl_cffi",
    "--hidden-import", "websocket",
    "--hidden-import", "requests",
]

if os.path.exists("icon.ico"):
    cmd += ["--icon", "icon.ico", "--add-data", "icon.ico;."]

cmd.append(main_script)

try:
    subprocess.run(cmd, check=True)
    print(f"\n--- HOTOVO! Soubor {exe_name}.exe najdeš ve složce dist ---")
except subprocess.CalledProcessError:
    print("\n--- CHYBA: PyInstaller selhal! ---")
