# AI Assistant

A desktop AI assistant inspired by "Hey Google" for Linux. Voice-activated, lightweight, and full system control.

## Features

### Voice Mode
- Wake word: "Hey Assistant"
- Text-to-speech responses
- Google Speech Recognition / Whisper STT
- Interrupt TTS with "shut up" or "stop talking"

### System Control
- Launch any application (including Flatpak)
- Install/remove packages (with sudo in full mode)
- Run shell commands
- Full system access mode (`-s` flag)

### File Operations
- Read files
- Write/create files
- List directories
- Delete files

### Music Control
- Play, pause, stop, next, previous
- Works with YouTube, Spotify, and any MPRIS player
- Get current song info

## Usage

```bash
# Interactive text mode
python assistant.py -i

# Voice mode
python assistant.py -v

# Full system access mode
python assistant.py -i -s
python assistant.py -v -s

# Single question
python assistant.py "what time is it"
```

## Setup

1. Get an API key from [NVIDIA NIM](https://build.nvidia.com/)
2. Add your key to `.env`:
   ```
   NVIDIA_NIM_API_KEY=nvapi-your-key-here
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Voice Mode Dependencies

**Arch Linux:**
```bash
sudo pacman -S espeak-ng portaudio playerctl
pip install SpeechRecognition pyaudio
```

**Debian/Ubuntu:**
```bash
sudo apt install espeak portaudio19-dev playerctl
pip install SpeechRecognition pyaudio
```

**Fedora:**
```bash
sudo dnf install espeak-ng portaudio-devel playerctl
pip install SpeechRecognition pyaudio
```

**openSUSE:**
```bash
sudo zypper install espeak-ng portaudio-devel playerctl
pip install SpeechRecognition pyaudio
```

## Models

Default: `nvidia/nemotron-3-super-120b-a12b`

Other options:
- `nvidia/nemotron-3-nano-30b-a3b`
- `nvidia/nemotron-3.5-lightning-30b-a3b`
- `meta/llama-3.1-8b-instruct`
- `meta/llama-3.3-70b-instruct`

More models may be added in the future.

Change with: type `model` in interactive mode or set `NVIDIA_NIM_MODEL` env var.

## Flags

| Flag | Description |
|------|-------------|
| `-i` | Interactive text mode |
| `-v` | Voice mode |
| `-s` | Full system access (requires sudo) |
| `--gtts` | Use Google TTS (better quality) |
| `--whisper` | Use Whisper for speech recognition |
| `--energy 500` | Custom mic sensitivity |
