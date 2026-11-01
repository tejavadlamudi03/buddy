# Buddy

A JARVIS-style assistant: control this laptop by voice, and see it live from your phone.
Ubuntu with an X11 session.

## What it needs

| | Without the local AI | With the local AI |
| --- | --- | --- |
| System | Ubuntu 24.04 GNOME, "Ubuntu on Xorg" session | Same |
| Processor and memory | 4 cores, 8 GB RAM | 8 cores, 16 GB RAM |
| Graphics | Any | NVIDIA with 8 GB of video memory, driver installed |
| Disk | 5 GB | 15 GB |

Also needed: Google Chrome, a microphone, `uv`, and internet for installing. The WhatsApp features
need a second WhatsApp account for the laptop. Windows, macOS and Wayland sessions won't work.

To set up a new laptop, copy this folder without `.venv`, then:

    cd ~/buddy && uv sync
    .venv/bin/buddy install-launcher
    .venv/bin/buddy install-autostart
    scripts/install-local-ai.sh
    scripts/install-telugu.sh
    .venv/bin/buddy whatsapp-login

## Start

    cd ~/buddy && uv sync
    .venv/bin/buddy install-launcher    # adds "Buddy" to the app menu
    .venv/bin/buddy                     # or start it directly
    .venv/bin/buddy --url               # print the private link

`buddy install-autostart` makes Buddy start at every login and restart after a crash. It runs
inside your desktop session, so it can't work until someone has logged in after a reboot.

Hold the button to talk, or tick Hands-free and begin with "Buddy". Right after a command,
the next known command works without saying "Buddy". Replies are spoken in a local neural voice.

## WhatsApp setup (once)

1. Enter the number you message from, with country code, under Settings.
2. Run `.venv/bin/buddy whatsapp-login` and scan the QR code using the WhatsApp account
   that will stay linked on this laptop. The WhatsApp Business app works the same way:
   Settings > Linked devices. Meta's Business API isn't used, since it would take the number
   off the app and need a public address pointing at this laptop.
3. To control the laptop by message, tick "Take commands from this number" under Settings.

"Unlock" also works from that number, after a "yes". It's refused from everywhere else, including
voice and the phone link, because anyone in the room could say it at the lock screen.

From that number, send anything you'd say aloud. Replies come back in the chat as text and
aren't read aloud. "Screenshot"
sends the screen, "Share my screen with control" sends a live link, and "Stop" cancels. Risky
actions wait for "yes". Buddy reads only the chat it opened by that number, and only messages under 3 minutes old. Other
chats are never read. Voice notes work too, in English or Telugu: Buddy transcribes them locally,
tells you what it heard, then acts on it. If it isn't sure of the English, it shows what it heard
and waits for "yes".

Send a picture, PDF, document, sheet, slides or text file and the local AI reads it, using your
caption as the question, and you can ask more about it for 30 minutes. A copy is saved in Downloads.
It's only ever discussed: neither the caption nor anything written in the file can run as a command.
It reads about 12000 characters of a document, or the first 3 pages of a scan, and says when it stopped.

You can also just talk to the local AI there: ask questions, paste text to review, ask for code.
It remembers the last few messages, so "make it shorter" works, and replies in your language. It
has no actions in this chat, and anything pasted (several lines, or over 300 characters) is only
ever discussed, so text you paste can't run as a command. It can't look things up online. Messages
are read up to 12000 characters. The last note is kept at
`~/.local/share/buddy/last-voice-note.ogg`, overwritten each time, for checking by ear.
Buddy must be running, the laptop awake, and the laptop signed in to your desktop session. Say
"Stay awake" before you leave. The screen still locks, and it survives a restart of Buddy, but
keep the laptop plugged in and out of a bag. A laptop that's already asleep can't be woken by message.
WhatsApp runs hidden with no window; a window only appears for `whatsapp-login`. Opening Buddy
from the app menu brings its window to the front, and closing that window leaves Buddy running.

## What to say

| Say | What happens |
| --- | --- |
| "Go to YouTube" / "Open Claude" | Opens the site in Chrome |
| "Open VS Code" | Focuses it if open, launches it if not |
| "Search for shape of you on YouTube", then "Play it" | Shows results, then plays the first video |
| "Play tum hi ho" | Plays the first YouTube result |
| "Open VS Code and send me a screenshot on WhatsApp" | Chains the steps |
| "Type hello world" / "Press control shift t" | Types or presses keys in the current window |
| "New tab", "Go back", "Copy", "Paste", "Save", "Scroll down" | Common shortcuts |
| "Change the song", "Next", "Skip this", "Previous song" | Skips through the player, locally |
| "Change the song to believer", "Put on believer" | Replaces the song in the same YouTube tab |
| "What song is this", "Pause", "Resume", "Volume 30", "Mute" | Media and volume |
| "What time is it", "How much battery is left" | Answered locally |
| "Share my screen", "Share my screen with control", "Stop sharing" | Live screen on your phone |
| "Minimize", "Maximize", "Close Chrome", "Switch to Files" | Window control |
| "Lock", "Shut down", "Restart" | Shutdown, restart, suspend and logout ask you to say "yes" |
| "Stay awake", "Stay awake for 2 hours", "Allow sleep" | Stops the laptop sleeping, even with the lid closed, for 8 hours unless you say otherwise (24 at most) |
| "Stop" | Cancels whatever is running |
| "Click Save", "Click Sound", "Click 7", "Double click report", "Read the screen" | Finds that text in the front window with local OCR and clicks it, or presses the control by name through Linux accessibility when OCR can't read it |
| "Turn off Bluetooth", "Dark mode on", "Brightness 40", "Night light", "Do not disturb", "Battery saver" | System settings |
| "Find report", "List my downloads", "Open report.pdf", "Open file budget" | Your files |
| "Send me report.pdf", "Send me the latest download" | Sends a file to your WhatsApp |
| "Download https://example.com/a.pdf", "Download https://example.com/a.docx as pdf and send it to me" | Saves it in Downloads and converts it if you asked. From WhatsApp the file comes back to you |
| "Convert report.docx to pdf", "Send me the latest download as mp3" | Converts audio, video, pictures, documents, sheets and slides, and zips anything. The original stays |
| "Report", "How is my laptop" | Battery, memory, disk, Wi-Fi, windows and what's playing |
| "Run df -h" | Reads the command back, then runs it after "yes" and returns the output |
| "Open Bluetooth settings", "Open my downloads", "Zoom in", "Snap left", "Next workspace" | Settings pages, folders, windows |
| Anything else, or a question | The local AI reads the screen and works through it, step by step |

## Live screen on your phone

Press **Share screen to phone** or say "Share my screen", then scan the QR code. No app to install.

- The link is view-only unless you tick "Let the phone control this laptop" or say "with control".
- With control: tap to click, press and hold to right-click, type, use keys, or talk to Buddy.
- It expires after 60 minutes (`share_minutes`). End it from either device, or say "Stop sharing".
- Buddy announces aloud when a device connects. If you didn't expect it, say "Stop sharing".
- Sharing must be started at the laptop, the laptop has to stay awake, each share gets a new
  link, and laptop audio isn't sent.

## Downloads and conversions

Everything arrives in `~/Downloads`. Buddy sends files up to 50 MB to WhatsApp and tells you when one
is bigger. Only send links you trust: a downloaded file is never run, but converting one does open it in
ffmpeg or LibreOffice. YouTube and similar sites aren't handled, so a link to one saves just the page.
"Stop" cancels a download or conversion. Private files can't be converted, because the copy wouldn't be private.

## Private files

Buddy won't find, read or send anything in `.ssh`, `.gnupg`, `.aws`, browser and mail profiles,
Buddy's own data, or files like `.env` and `*.pem`. To add your own folders, list them under
`private_folders` in the config, relative to your home folder, and restart Buddy. A "run" command you confirm yourself isn't limited by this list.

## How it works

    mic -> web page -> local Whisper -> rules (parser.py) -> actions.py -> the desktop
                                           \-> not recognised: the local AI reads it (language.py)
                                                  a command: rules again, then AI + OCR + screenshot (agent.py)
                                                  a question or writing: a reply, with no actions

Everything runs on this laptop: Whisper hears, Piper speaks, Linux accessibility and RapidOCR
read apps, and an open source model in Ollama thinks. No AI service is involved. Clicks only ever
target the front window. Chrome, VS Code and other Electron apps may expose few controls by name,
so OCR handles those.

## English and Telugu

Speak or type either one, including Telugu in English letters ("chrome open cheyyi"). No training
is involved. Known English commands run straight away. Anything else also goes through a Whisper
model tuned for Telugu, and the local AI decides which language was spoken from the two
transcripts. Telugu is then translated into the commands in the table above, so shutdown and other
risky actions still ask first, and అవును, వద్దు and ఆపు work as yes, no and stop. With hands-free
on, say the wake word, or Telugu isn't tried at all, which keeps room noise away from it.

Spoken requests that aren't built-in commands take a few seconds longer, about 6 to 7 for a voice note. Replies to commands are in
English, while the AI's own answers follow your language and use a Telugu voice when read aloud.

    ~/buddy/scripts/install-telugu.sh

## Local AI setup (once)

    ~/buddy/scripts/install-local-ai.sh

No sudo needed. It downloads Ollama and the model (about 8 GB in total) into
`~/.local/share/buddy/ollama`. Use another model with `install-local-ai.sh <model>` and by
setting `ai_model` in the config. Until this is done, built-in commands and "click X" still
work, and other requests get a spoken explanation.

## Privacy and safety

- Voice, screen contents and AI processing all stay on this machine. The exceptions are the
  websites you ask for, WhatsApp, and screen sharing when you turn it on.
- The server listens on 127.0.0.1 only, and each API call needs the token stored in
  `~/.config/buddy/config.json`. Treat the link with the token as a password.
- The local AI can only pick actions from `AGENT_ACTIONS`.
  It can't power off the laptop or start screen sharing. It can type into a terminal window
  only if your request mentions a terminal, command, install or similar; the VS Code terminal
  isn't covered by this check. It's told to ask before sending, buying or deleting, but that
  part is an instruction to the model and not a hard limit, so watch it on sensitive pages.
  The Stop button halts it before its next action.
- Screen sharing goes through a temporary Cloudflare tunnel. The link is encrypted in
  transit, but Cloudflare's servers handle the traffic, and anyone with the link can see your
  screen until it ends. The tunnel reaches only a small separate server, never Buddy's main
  token or settings.

## Tests

    env -u PYTHONPATH uv run --group dev pytest   # a sourced ROS environment breaks pytest otherwise

Settings are in `~/.config/buddy/config.json`: `whisper_model`, `ai_model`, `agent_max_steps`, `share_minutes`, `port`.
