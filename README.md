# Widerhall

A small desktop voice assistant I built in Python. Say "Widerhall" and it does things — opens stuff, plays music, reads news, does math, answers questions, runs timers. The name is German for "echo".

It only uses free public APIs. Nothing is sent to an LLM or paid AI service.

## What it can do

After the wake word, you can say things like:

- "Open Google", "Open YouTube", "Open Notepad", "Open Calculator"
- "Play Believer"
- "Tell me the news"
- "Weather in Berlin"  (or just "weather" if `DEFAULT_CITY` is set)
- "Wikipedia Mount Everest"  /  "Tell me about Albert Einstein"
- "Define ephemeral"  /  "What does serendipity mean"
- "Search for Python tutorials"
- "What is the time"  /  "What is today's date"
- "What is 17 times 4"  /  "Calculate 100 divided by 5"
- "Convert 100 dollars to euros"
- "Price of bitcoin"
- "Take a note buy milk tomorrow"  /  "Read my notes"
- "Set a timer for 5 minutes"  /  "Remind me in 30 seconds"
- "Start stopwatch" / "Stop stopwatch"
- "Volume up", "Volume down", "Mute"
- "Lock screen"
- "Take a screenshot"
- "Disk usage", "Battery", "CPU usage", "Memory usage"
- "Switch voice"
- "What is my IP"
- "Tell me a joke", "Tell me a fact", "Inspire me"
- "Flip a coin", "Roll a dice", "Roll a 20 sided dice"
- "Repeat"
- "Help"
- "Exit"

Inside the assistant, just say "help" and it'll list the categories aloud.

## Setup

```bash
git clone https://github.com/AradhyaStuti/Widerhall-Voice-Assistant.git
cd widerhall-voice-assistant
pip install -r requirements.txt
```

If `pyaudio` won't install on Windows, grab a prebuilt wheel from
<https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio>.

Then copy the env template and fill in your NewsAPI key (free at <https://newsapi.org>):

```bash
cp .env.example .env
```

Everything else in `.env` is optional. If you skip this whole step it still runs — there's a built-in fallback key, but it's shared and rate-limited, so use your own.

## Run

```bash
python Widerhall.py
```

You'll get a banner, then a `>>>` prompt. **You can type any command directly** — no wake word needed. Try:

```
>>> play perfect
>>> what is 17 times 4
>>> tell me a joke
>>> weather in berlin
>>> help
>>> exit
```

Voice runs in the background at the same time. Say "Widerhall", "Echo", "Computer", "Jarvis", "Assistant", or "Hello", wait for "Yes?", then give your command.

If voice is misbehaving, run with voice disabled:

```bash
set WIDERHALL_TEXT_ONLY=1     # Windows cmd
$env:WIDERHALL_TEXT_ONLY=1    # PowerShell
export WIDERHALL_TEXT_ONLY=1  # bash
python Widerhall.py
```

## How the wake word works

Google's STT mangles "Widerhall" pretty badly, so the listener accepts a bunch of stems — both German variants (`widerhall`, `wider hall`, `wieder`) and English fallbacks (`echo`, `computer`, `jarvis`, `assistant`, `hello`, …). Whichever one your mic and the recognizer agree on, it triggers.

The console prints exactly what was heard at every step:

```
  [mic] listening (en-US, up to 8s)…
  [mic] heard: 'play perfect'
```

If you see a heard line but no command runs, it means the wake word didn't match — add whatever you heard to `WAKE_STEMS` near the top of the file.

If voice is being uncooperative, you can also just **type the command** at the `[type] >` prompt that runs alongside the voice loop. Same registry, same handlers. Useful for testing, or as a permanent backup.

To skip the wake word entirely (every utterance becomes a command), set:

```bash
set WIDERHALL_ALWAYS_LISTEN=1     # Windows cmd
$env:WIDERHALL_ALWAYS_LISTEN=1    # PowerShell
export WIDERHALL_ALWAYS_LISTEN=1  # bash
```

## How commands work

There's one list called `COMMANDS`. Each entry looks like:

```python
(["trigger phrase", "another trigger"], handler_function),
```

The dispatcher walks the list in order and runs the first handler whose trigger appears in the heard text. Specific patterns are listed first; the math handler (which catches things like "what is …") sits at the bottom so it doesn't steal "what is the time".

To add a new command:

```python
def cmd_say_hi(c):
    speak("Hello there.")

COMMANDS.append((["say hi", "say hello"], cmd_say_hi))
```

## How math works

Spoken phrases like "what is 17 plus 5 times 2" are converted to symbols via the `WORD_TO_OP` table, then parsed with Python's `ast` module. Only a whitelisted set of operators is allowed (`+ - * / % **`). No `eval()`.

## Project files

```
Widerhall.py        the whole thing
requirements.txt    Python deps
.env.example        copy to .env and fill in
.gitignore
README.md
notes.txt           created when you first take a note
```

## Requirements

- Python 3.9+
- Working microphone
- Internet
- Windows for the system commands (Notepad, volume keys, lock screen, snipping tool). Cross-platform commands work on Mac/Linux too.

## Stuff I might add later

- Tkinter or PyQt GUI
- A way to change the wake word at runtime
- Brightness control
- Spotify control
- Email and WhatsApp dictation
- Persistent reminders / calendar

## License

MIT.

## Author

Aradhya Stuti — <https://github.com/AradhyaStuti>
