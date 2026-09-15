from __future__ import annotations
import subprocess
import sys
import time
from pathlib import Path
import pyautogui

PROMPT = "Write me the code for finding the even numbers in Python"
URL = "https://chatgpt.com/"

CHROME_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe",
)

PAGE_LOAD_WAIT = 12.0
TYPE_INTERVAL = 0.03


def find_chrome() -> Path:
    for path in CHROME_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "Google Chrome not found. Add its chrome.exe path to CHROME_CANDIDATES."
    )


def open_chrome(chrome: Path) -> None:
    subprocess.Popen([str(chrome), "--new-window", URL])


def send_prompt() -> None:
    # The composer is focused on load. If yours is not, click it first:
    #   pyautogui.click(x=..., y=...)
    pyautogui.write(PROMPT, interval=TYPE_INTERVAL)
    time.sleep(0.5)
    pyautogui.press("enter")


def main() -> int:
    try:
        chrome = find_chrome()
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Launching {chrome.name} at {URL}")
    open_chrome(chrome)

    print(f"Waiting {PAGE_LOAD_WAIT:.0f}s for the page to load...")
    time.sleep(PAGE_LOAD_WAIT)

    print(f"Typing prompt: {PROMPT!r}")
    send_prompt()
    print("Prompt submitted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
