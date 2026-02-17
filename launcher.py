import os
import signal
import subprocess
import sys
import time

import requests
from dotenv import load_dotenv


def wait_for_api(base_url: str, timeout_sec: int = 30) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            resp = requests.get(f"{base_url}/list-apps", timeout=2)
            if resp.ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(1)
    return False


def main() -> int:
    load_dotenv()

    api_url = os.getenv("ADK_API_BASE", "http://127.0.0.1:8000")

    print("[1/2] Starting ADK API server...")
    api_proc = subprocess.Popen(["adk", "api_server"])

    try:
        if not wait_for_api(api_url):
            print("ADK API server did not become ready in time.")
            return 1

        print("[2/2] Launching Streamlit UI...")
        ui_cmd = [sys.executable, "-m", "streamlit", "run", "streamlit_app.py"]
        ui_proc = subprocess.Popen(ui_cmd)
        ui_exit = ui_proc.wait()
        return ui_exit
    finally:
        if api_proc.poll() is None:
            if os.name == "nt":
                api_proc.send_signal(signal.CTRL_BREAK_EVENT)
                time.sleep(1)
            api_proc.terminate()
            try:
                api_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                api_proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
