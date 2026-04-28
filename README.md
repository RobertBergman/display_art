# Display Art

Display Art turns an idle Ubuntu display into an AI art frame. It is intended to
start on boot, take over the display while the system is sitting idle, and get
out of the way as soon as someone presses a key to log in.

The art is shown directly through DRM/KMS, so it can run without a desktop
session or browser.

The project has three main pieces:

- `art_loop.py` generates prompts and images through OpenRouter, fits each image
  to 1920x1080, saves it under `images/`, displays it, and watches keyboard
  input so user activity blanks the art display.
- `display_image.c` loads an image with `stb_image.h` and presents it on a KMS
  framebuffer.
- `hello_kms.c` is a standalone DRM/KMS smoke test that draws a simple
  "Hello World" screen.

## Requirements

- Linux with DRM/KMS access, usually under `/dev/dri/card*`
- A connected display on the selected DRM card
- C compiler
- `libdrm` development headers
- Python 3
- Python packages:
  - `Pillow`
  - `requests`
- An OpenRouter API key

On Debian or Ubuntu, the system dependencies are typically:

```sh
sudo apt install build-essential libdrm-dev python3-pil python3-requests
```

## Build

Build the image display helper:

```sh
gcc display_image.c -o display_image $(pkg-config --cflags --libs libdrm)
```

Build the KMS smoke test:

```sh
gcc hello_kms.c -o hello_kms $(pkg-config --cflags --libs libdrm)
```

## Configure

Set an OpenRouter API key either in the environment:

```sh
export OPENROUTER_API_KEY="..."
```

Or in `~/.agent/api_keys.json`:

```json
{
  "OPENROUTER_API_KEY": "..."
}
```

The default DRM card is `/dev/dri/card1`. Update `CARD` in `art_loop.py` if the
target display is attached to a different card.

## Install Service

Run the installer with `sudo`:

```sh
sudo ./install.sh
```

The installer:

- creates a `display_art` system account with home directory
  `/var/lib/display_art`
- prompts for the OpenRouter API key and writes it to
  `/var/lib/display_art/.agent/api_keys.json`
- grants the service account access to the `video`, `render`, and `input`
  groups when those groups exist
- installs and enables `display_art.service`

You can also pass the API key through the environment:

```sh
sudo OPENROUTER_API_KEY="..." ./install.sh
```

Start the service:

```sh
sudo systemctl start display_art.service
```

Check logs:

```sh
sudo journalctl -u display_art.service -f
```

## Run

Test KMS output:

```sh
./hello_kms /dev/dri/card1
```

Display an existing image:

```sh
./display_image images/art_20260427_204506.png /dev/dri/card1
```

Start the art loop:

```sh
python3 art_loop.py
```

The loop generates a new image every two hours. Any keyboard press pauses and
blanks the art display so the user can log in. After five minutes without further
keyboard input, the art display resumes.

For boot-time use, run `art_loop.py` from a system service after the DRM device is
available. The service account needs access to the selected `/dev/dri/card*`
device and `/dev/input/event*` keyboard devices.

## Notes

- DRM/KMS access may require running from a local console, adding the user to the
  `video` group, or running with elevated privileges depending on the system.
- Generated images are saved in `images/`.
- The current fitted display image is written to `/tmp/display_art.png`.
