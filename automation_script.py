"""Open ChatGPT in the default browser and type a prompt. Experiment only."""

import time
import webbrowser

import pyautogui

PROMPT = "Write me the code for finding the even numbers in Python"
PAGE_LOAD_WAIT = 12  # seconds to let the browser and chat UI finish loading


def main() -> None:
    webbrowser.open("https://chatgpt.com/")
    time.sleep(PAGE_LOAD_WAIT)

    # The composer is focused on load; press "/" or click it if your setup differs.
    pyautogui.write(PROMPT, interval=0.03)
    time.sleep(0.5)
    pyautogui.press("enter")


if __name__ == "__main__":
    main()