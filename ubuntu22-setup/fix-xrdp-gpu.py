#!/usr/bin/env python3
"""Apply the xorgxrdp upstream PR #241 settings to its dedicated Xorg config."""

import datetime
import difflib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


CONFIG = Path("/etc/X11/xrdp/xorg.conf")


def patch_section(text, section, setting_pattern, desired):
    pattern = re.compile(
        r'(^[ \t]*Section[ \t]+"' + re.escape(section)
        + r'"[ \t]*\n)(.*?)(^[ \t]*EndSection[^\n]*$)',
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise RuntimeError("Expected exactly one " + section + " section; no changes made.")
    match = matches[0]
    body = match.group(2)
    setting = re.compile(setting_pattern, re.MULTILINE | re.IGNORECASE)
    if setting.search(body):
        body = setting.sub("    " + desired, body)
    else:
        body = "    " + desired + "\n" + body
    return text[:match.start()] + match.group(1) + body + match.group(3) + text[match.end():]


def patched_config(original):
    modified = patch_section(
        original, "ServerFlags", r'^[ \t]*Option[ \t]+"AutoAddGPU"[^\n]*$',
        'Option "AutoAddGPU" "off"',
    )
    return patch_section(
        modified, "Screen", r'^[ \t]*GPUDevice[ \t]+[^\n]*$', 'GPUDevice ""',
    )


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run this script with sudo python3.")
    original = CONFIG.read_text(encoding="utf-8")
    modified = patched_config(original)
    if modified != original:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = CONFIG.with_name(CONFIG.name + ".before-gpu-fix-" + stamp)
        shutil.copy2(CONFIG, backup)
        print("Backup:", backup, flush=True)
        print("".join(difflib.unified_diff(
            original.splitlines(True), modified.splitlines(True),
            fromfile=str(backup), tofile=str(CONFIG),
        )), flush=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=CONFIG.parent,
            prefix=".xorg-gpu-fix-", delete=False,
        ) as temp:
            temporary = Path(temp.name)
            temp.write(modified)
        try:
            shutil.copystat(CONFIG, temporary)
            os.replace(temporary, CONFIG)
        finally:
            temporary.unlink(missing_ok=True)
    else:
        print("Both GPU settings are already correct; config unchanged.", flush=True)
    subprocess.run(["systemctl", "restart", "xrdp"], check=True)
    subprocess.run(["systemctl", "is-active", "xrdp"], check=True)
    print("DONE. Reconnect using the Xorg session and user rock.", flush=True)


if __name__ == "__main__":
    main()
