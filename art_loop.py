#!/usr/bin/env python3
import base64
import glob
import json
import os
import random
import select
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time

from PIL import Image
import requests

KEYFILE = os.path.expanduser("~/.agent/api_keys.json")
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CARD = "/dev/dri/card1"
DISPLAY_BIN = os.path.join(SCRIPT_DIR, "display_image")
OUTPUT_IMG = "/tmp/display_art.png"
IMAGES_DIR = os.path.join(SCRIPT_DIR, "images")
INTERVAL = 7200
PAUSE_TIMEOUT = 300

IMG_MODEL = "google/gemini-3.1-flash-image-preview"
TEXT_MODEL = "google/gemini-3.1-flash-lite-preview"

STYLES = [
    "cyberpunk", "watercolor painting", "oil painting", "pixel art",
    "impressionist", "art nouveau", "synthwave", "minimalist",
    "surrealist", "Japanese ukiyo-e", "Art Deco", "geometric abstract",
    "fantasy illustration", "noir photography", "pop art",
    "stained glass", "steampunk", "cubist", "baroque", "vaporwave",
]

SUBJECTS = [
    "a lone astronaut", "a futuristic city", "an enchanted forest",
    "a cosmic nebula", "underwater ruins", "a mechanical dragon",
    "floating islands", "a rainy street at night", "ancient temple",
    "a quantum computer", "garden of bioluminescent plants",
    "a cathedral made of light", "robot tea ceremony",
    "library in space", "crystal cavern", "desert at twilight",
    "neon-lit alleyway", "aurora over mountains", "clockwork universe",
    "zen garden with holographic elements",
]

paused = False
paused_lock = threading.Lock()
last_key_time = 0.0
keytime_lock = threading.Lock()
display_proc = None


def load_key():
    if os.path.exists(KEYFILE):
        with open(KEYFILE) as f:
            return json.load(f).get("OPENROUTER_API_KEY", "")
    return os.environ.get("OPENROUTER_API_KEY", "")


OPENROUTER_KEY = load_key()


def or_headers():
    return {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
    }


def generate_prompt():
    style = random.choice(STYLES)
    subject = random.choice(SUBJECTS)
    phrasing = random.choice([
        f"Create a vivid image generation prompt. Style: {style}. "
        f"Topic: {subject}. Include lighting, composition, and mood details. "
        f"Output ONLY the prompt text, nothing else.",
        f"Write a beautiful art prompt for an image generator. "
        f"Theme: {subject} in the style of {style}. "
        f"Keep it under 200 characters. Output ONLY the prompt.",
        f"Generate a detailed digital art prompt. "
        f"Style: {style}. Subject: {subject}. "
        f"Be creative and descriptive. Output ONLY the prompt.",
    ])

    try:
        resp = requests.post(
            OR_URL, headers=or_headers(),
            json={"model": TEXT_MODEL, "messages": [{"role": "user", "content": phrasing}],
                  "max_tokens": 200},
            timeout=30,
        )
        if resp.status_code == 200:
            prompt = resp.json()["choices"][0]["message"]["content"].strip().strip('"')
            print(f"  Prompt: {prompt}")
            return prompt
    except Exception as e:
        print(f"  Text model error: {e}")

    fallback = f"{subject}, {style} style, high detail, beautiful composition"
    print(f"  Fallback: {fallback}")
    return fallback


def generate_image(prompt):
    try:
        resp = requests.post(
            OR_URL, headers=or_headers(),
            json={"model": IMG_MODEL,
                  "messages": [{"role": "user", "content": f"Generate an image: {prompt}"}],
                  "max_tokens": 4096},
            timeout=120,
        )

        if resp.status_code != 200:
            print(f"  Image API error: {resp.status_code} {resp.text[:300]}")
            return False

        data = resp.json()
        msg = data["choices"][0]["message"]
        images = msg.get("images", [])
        if not images:
            print(f"  No image. Content: {str(msg.get('content', ''))[:200]}")
            return False

        img_data = images[0].get("image_url", {}).get("url", "")
        if not img_data:
            print("  Empty image URL")
            return False

        if img_data.startswith("data:image/png;base64,"):
            img_data = img_data[len("data:image/png;base64,"):]

        raw = base64.b64decode(img_data)

        if raw.startswith(b"\x89PNG"):
            ext = "png"
        elif raw.startswith(b"\xff\xd8\xff"):
            ext = "jpg"
        else:
            jpeg_start = raw.find(b"\xff\xd8\xff")
            if jpeg_start >= 0:
                raw = raw[jpeg_start:]
                ext = "jpg"
            else:
                png_start = raw.find(b"\x89PNG")
                if png_start >= 0:
                    raw = raw[png_start:]
                    ext = "png"
                else:
                    print(f"  Unknown image format, first bytes: {raw[:16].hex()}")
                    return False

        with open(OUTPUT_IMG, "wb") as f:
            f.write(raw)
        fit_to_screen(OUTPUT_IMG)
        save_to_gallery(OUTPUT_IMG)
        print("  Image saved and fitted to 1920x1080")
        return True

    except Exception as e:
        print(f"  Image generation error: {e}")
        return False


def save_to_gallery(src_path):
    os.makedirs(IMAGES_DIR, exist_ok=True)
    dst = os.path.join(IMAGES_DIR, f"art_{time.strftime('%Y%m%d_%H%M%S')}.png")
    Image.open(src_path).save(dst, "PNG")
    print(f"  Saved: {dst}")


def fit_to_screen(path, target_w=1920, target_h=1080):
    img = Image.open(path)
    w, h = img.size
    target_aspect = target_w / target_h
    img_aspect = w / h

    if img_aspect > target_aspect:
        new_w = int(h * target_aspect)
        img = img.crop(((w - new_w) // 2, 0, (w + new_w) // 2, h))
    elif img_aspect < target_aspect:
        new_h = int(w / target_aspect)
        img = img.crop((0, (h - new_h) // 2, w, (h + new_h) // 2))

    if img.size != (target_w, target_h):
        img = img.resize((target_w, target_h), Image.LANCZOS)

    img.save(path, "PNG")


def kill_display():
    global display_proc
    if display_proc and display_proc.poll() is None:
        display_proc.send_signal(signal.SIGTERM)
        try:
            display_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            display_proc.kill()
            display_proc.wait()
        display_proc = None


def show_image():
    global display_proc
    kill_display()
    display_proc = subprocess.Popen(
        [DISPLAY_BIN, OUTPUT_IMG, CARD],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        line = display_proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if "READY" in line:
            print("  Display showing")
            return
        if "Error" in line or "error" in line or "Failed" in line:
            print(f"  Display error: {line}")
            return
    print("  Display started (no READY confirmation)")


def set_paused(state):
    global paused
    with paused_lock:
        changed = paused != state
        paused = state
        return changed


def is_paused():
    with paused_lock:
        return paused


def touch_key():
    global last_key_time
    with keytime_lock:
        last_key_time = time.time()


def seconds_since_last_key():
    with keytime_lock:
        return time.time() - last_key_time


def input_monitor():
    """Thread: monitor /dev/input/event* for key presses."""
    devices = {}
    try:
        for path in sorted(glob.glob("/dev/input/event*")):
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                devices[fd] = path
            except (PermissionError, OSError):
                pass
    except Exception:
        pass

    if not devices:
        print("  No input devices available for monitoring")
        return

    print(f"  Monitoring {len(devices)} input devices for key presses")

    while True:
        try:
            r, _, _ = select.select(list(devices.keys()), [], [], 1.0)
            for fd in r:
                try:
                    data = os.read(fd, 24)
                    if len(data) == 24:
                        _, _, ev_type, ev_code, ev_value = struct.unpack('llHHi', data)
                        if ev_type == 1 and ev_value == 1:
                            touch_key()
                            if set_paused(True):
                                print("  [Paused by keyboard input]")
                except (OSError, BlockingIOError):
                    pass
        except Exception:
            time.sleep(1)


def main():
    if not OPENROUTER_KEY:
        print("Set OPENROUTER_API_KEY in ~/.agent/api_keys.json")
        sys.exit(1)

    print("Art loop starting. New image every 2 hours.")
    print(f"Image model: {IMG_MODEL}")
    print(f"Text model:  {TEXT_MODEL}")
    print(f"Pause on keypress, resume after {PAUSE_TIMEOUT // 60} min idle")
    print()

    monitor = threading.Thread(target=input_monitor, daemon=True)
    monitor.start()

    while True:
        try:
            if is_paused():
                if seconds_since_last_key() >= PAUSE_TIMEOUT:
                    if set_paused(False):
                        print("  [Resuming after idle timeout]")
                        kill_display()
                else:
                    kill_display()
                    time.sleep(1)
                    continue

            print(f"[{time.strftime('%H:%M:%S')}] Generating new art...")
            prompt = generate_prompt()
            if generate_image(prompt):
                if not is_paused():
                    show_image()
            else:
                print("  Failed, will retry next cycle.")

            remaining = INTERVAL
            while remaining > 0:
                if is_paused():
                    print("  [Paused, blanking display]")
                    kill_display()
                    while is_paused() and seconds_since_last_key() < PAUSE_TIMEOUT:
                        time.sleep(1)
                    if is_paused():
                        set_paused(False)
                        print("  [Resuming after idle timeout]")
                    break
                time.sleep(1)
                remaining -= 1

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"  Error: {e}")
            time.sleep(INTERVAL)

    kill_display()
    print("Done.")


if __name__ == "__main__":
    main()
