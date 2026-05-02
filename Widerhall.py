import speech_recognition as sr
import pyttsx3
import webbrowser
import requests
import urllib.parse
import re
import time
import threading
import datetime
import subprocess
import os
import ast
import operator
import random
import platform

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import psutil
except ImportError:
    psutil = None


NEWS_API_KEY = os.getenv("NEWS_API_KEY", "6b444347cf7546afa955f8ab678495d8")
USER_NAME = os.getenv("USER_NAME", "Boss")
DEFAULT_CITY = os.getenv("DEFAULT_CITY", "")
NOTES_FILE = os.getenv("NOTES_FILE", "notes.txt")

# Wikipedia (and some other APIs) reject requests without a descriptive User-Agent.
HTTP_HEADERS = {
    "User-Agent": "Widerhall/1.0 (https://github.com/AradhyaStuti/Widerhall-Voice-Assistant)"
}


recognizer = sr.Recognizer()
engine = pyttsx3.init()
engine.setProperty("rate", 160)

last_response = ""
stopwatch_start = None
speak_lock = threading.Lock()


def speak(text):
    global last_response
    last_response = text
    print(f"[Widerhall] {text}")
    # Serialize all speak() calls — pyttsx3's runAndWait can't be re-entered
    # ("run loop already started" error) so we use a lock instead of a thread.
    with speak_lock:
        try:
            engine.say(text)
            engine.runAndWait()
        except RuntimeError as e:
            print(f"  [tts] runtime: {e}")
            try:
                engine.stop()
            except Exception:
                pass
        except Exception as e:
            print(f"  [tts] error: {e}")


def listen(timeout=10, phrase_time_limit=7, prompt=None, language="en-US"):
    if prompt:
        speak(prompt)
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.3)
            try:
                audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
            except sr.WaitTimeoutError:
                return ""
    except OSError as e:
        print(f"  [mic] error: {e}")
        return ""
    try:
        text = recognizer.recognize_google(audio, language=language)
        print(f"  [mic heard] {text!r}")
        return text.lower()
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as e:
        print(f"  [mic] recognition error (network?): {e}")
        return ""


# Google STT can't reliably transcribe "Widerhall" (German). So we accept
# the German stems AND English wake words — pick whichever your mic gets right.
WAKE_STEMS = (
    # German variants of "Widerhall"
    "widerhall", "wider hall", "wieder hall", "wieder",
    "wider", "vider", "weeder", "wide a", "vita hall",
    # English fallbacks — much more reliable detection
    "echo", "hey echo", "hey widerhall",
    "computer", "hey computer",
    "jarvis", "hey jarvis",
    "assistant", "hey assistant", "okay assistant",
    "hello", "hey there",
)


def is_wake_word(text):
    return any(stem in text for stem in WAKE_STEMS)


def open_website(name, url):
    webbrowser.open(url)
    speak(f"Opening {name}.")


def open_app(name, exe):
    try:
        subprocess.Popen([exe])
        speak(f"Opening {name}.")
    except FileNotFoundError:
        speak(f"{name} isn't available on this system.")


def send_media_key(code):
    # 173 = mute, 174 = volume down, 175 = volume up
    subprocess.run(
        ["powershell", "-Command",
         f"(New-Object -ComObject WScript.Shell).SendKeys([char]{code})"],
        capture_output=True,
    )


def play_youtube(song_name):
    query = urllib.parse.quote_plus(song_name)
    search_url = f"https://www.youtube.com/results?search_query={query}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        response = requests.get(search_url, headers=headers, timeout=10)
        if response.status_code == 200:
            video_ids = re.findall(r"watch\?v=([\w-]{11})", response.text)
            if video_ids:
                webbrowser.open(f"https://www.youtube.com/watch?v={video_ids[0]}")
                speak(f"Playing {song_name} on YouTube")
                return
    except Exception as e:
        print("YouTube fetch error:", e)
    # Fallback: just open the search page so the user can click the first result
    webbrowser.open(search_url)
    speak(f"Showing YouTube results for {song_name}")


def time_of_day_greeting():
    hour = datetime.datetime.now().hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


WORD_TO_OP = {
    "plus": "+", "added to": "+",
    "minus": "-", "subtract": "-", "less": "-",
    "times": "*", "multiplied by": "*", "into": "*",
    "divided by": "/", "divide by": "/", "over": "/",
    "modulo": "%", "mod": "%",
    "to the power of": "**", "power of": "**",
}

SAFE_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def safe_eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in SAFE_OPS:
        return SAFE_OPS[type(node.op)](safe_eval(node.left), safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in SAFE_OPS:
        return SAFE_OPS[type(node.op)](safe_eval(node.operand))
    raise ValueError("unsafe expression")


def evaluate_math(text):
    expr = text.lower()
    # longest phrases first so "added to" beats "to"
    for word, sym in sorted(WORD_TO_OP.items(), key=lambda kv: -len(kv[0])):
        expr = expr.replace(word, sym)
    expr = re.sub(r"[^\d\.\+\-\*\/\%\(\)\s]", "", expr).strip()
    if not expr or not re.search(r"\d", expr):
        return None
    try:
        return safe_eval(ast.parse(expr, mode="eval").body)
    except Exception:
        return None


def add_note(text):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(NOTES_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {text}\n")


def read_notes(limit=5):
    if not os.path.exists(NOTES_FILE):
        return []
    with open(NOTES_FILE, "r", encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    return lines[-limit:]


def cmd_news(c):
    try:
        r = requests.get(
            f"https://newsapi.org/v2/top-headlines?country=us&apiKey={NEWS_API_KEY}",
            timeout=10,
        )
        if r.status_code != 200:
            speak("Couldn't fetch the news right now.")
            return
        articles = r.json().get("articles", [])[:5]
        if not articles:
            speak("Nothing in the news feed right now.")
            return
        speak("Here are today's top headlines.")
        for article in articles:
            title = article["title"]
            print(title)
            for part in re.split(r"[.,;:]", title):
                part = part.strip()
                if part:
                    speak(part)
                    time.sleep(0.3)
    except Exception as e:
        print("News error:", e)
        speak("News feed isn't reachable.")


def cmd_weather(c):
    city = c.replace("weather in", "").replace("weather", "").strip() or DEFAULT_CITY
    try:
        r = requests.get(f"https://wttr.in/{urllib.parse.quote(city)}?format=3", timeout=5)
        if r.status_code == 200 and r.text.strip():
            speak(r.text.encode("ascii", "ignore").decode().strip())
        else:
            speak("Couldn't fetch the weather right now.")
    except Exception as e:
        print("Weather error:", e)
        speak("Weather service isn't reachable.")


def cmd_search(c):
    query = c.replace("search for", "").replace("search", "").strip()
    if not query:
        speak("What should I search for?")
        return
    webbrowser.open(f"https://www.google.com/search?q={urllib.parse.quote(query)}")
    speak(f"Searching for {query}")


def cmd_wikipedia(c):
    topic = c.replace("wikipedia", "").replace("tell me about", "").strip()
    if not topic:
        speak("What topic?")
        return
    try:
        # Wikipedia uses underscores for multi-word titles. Capitalize first letter
        # for a better hit rate (e.g. "albert einstein" -> "Albert_einstein").
        wiki_topic = topic.replace(" ", "_")
        if wiki_topic:
            wiki_topic = wiki_topic[0].upper() + wiki_topic[1:]
        r = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(wiki_topic)}",
            headers=HTTP_HEADERS,
            timeout=8,
        )
        if r.status_code == 200:
            extract = r.json().get("extract", "").strip()
            if extract:
                sentences = re.split(r"(?<=[.!?])\s+", extract)
                speak(" ".join(sentences[:2]))
            else:
                speak(f"Nothing on {topic}.")
        else:
            speak(f"Couldn't find anything on {topic}.")
    except Exception as e:
        print("Wikipedia error:", e)
        speak("Wikipedia isn't reachable.")


def cmd_timer(c):
    m = re.search(r"(\d+)\s*(second|minute|hour)", c)
    if not m:
        speak("Tell me a duration in seconds, minutes, or hours.")
        return
    num, unit = int(m.group(1)), m.group(2)
    seconds = num * {"second": 1, "minute": 60, "hour": 3600}[unit]
    plural = "s" if num != 1 else ""
    speak(f"Timer set for {num} {unit}{plural}.")
    threading.Timer(seconds, lambda: speak("Time's up!")).start()


def cmd_time(c):
    speak("It's " + datetime.datetime.now().strftime("%I:%M %p"))


def cmd_date(c):
    speak("Today is " + datetime.datetime.now().strftime("%A, %B %d, %Y"))


def cmd_play(c):
    song = c.replace("play ", "", 1).strip()
    if song:
        play_youtube(song)
    else:
        speak("What should I play?")


def cmd_system_info(c):
    if psutil is None:
        speak("System info needs the psutil package.")
        return
    parts = []
    bat = psutil.sensors_battery()
    if bat:
        status = " and charging" if bat.power_plugged else ""
        parts.append(f"Battery is at {int(bat.percent)} percent{status}")
    parts.append(f"CPU is at {int(psutil.cpu_percent(interval=0.5))} percent")
    parts.append(f"Memory is at {int(psutil.virtual_memory().percent)} percent")
    speak(". ".join(parts) + ".")


def cmd_take_note(c):
    note = (
        c.replace("take a note", "")
        .replace("note that", "")
        .replace("save a note", "")
        .lstrip(":")
        .strip()
    )
    if not note:
        speak("What's the note?")
        return
    add_note(note)
    speak("Got it, saved.")


def cmd_read_notes(c):
    notes = read_notes()
    if not notes:
        speak("You don't have any notes yet.")
        return
    speak(f"Your last {len(notes)} notes.")
    for n in notes:
        speak(n)


def cmd_math(c):
    cleaned = re.sub(r"^(what is|whats|calculate|compute)\b", "", c).strip()
    result = evaluate_math(cleaned)
    if result is None:
        speak("Couldn't work that out.")
        return
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    speak(f"That's {result}")


def cmd_repeat(c):
    if last_response:
        speak(last_response)
    else:
        speak("I haven't said anything yet.")


def cmd_volume_up(c):
    for _ in range(5):
        send_media_key(175)
    speak("Volume up.")


def cmd_volume_down(c):
    for _ in range(5):
        send_media_key(174)
    speak("Volume down.")


def cmd_mute(c):
    send_media_key(173)
    speak("Muted.")


def cmd_lock_screen(c):
    speak("Locking up.")
    subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"])


def cmd_screenshot(c):
    try:
        subprocess.Popen(["snippingtool"])
        speak("Snipping tool is open.")
    except FileNotFoundError:
        try:
            subprocess.Popen(["explorer", "ms-screenclip:"])
            speak("Screen clipper is open.")
        except Exception:
            speak("No screenshot tool available.")


def cmd_disk_usage(c):
    if psutil is None:
        speak("Disk info needs psutil.")
        return
    path = "C:\\" if platform.system() == "Windows" else "/"
    usage = psutil.disk_usage(path)
    used_gb = usage.used / (1024 ** 3)
    total_gb = usage.total / (1024 ** 3)
    speak(
        f"Disk is at {int(usage.percent)} percent. "
        f"{used_gb:.0f} of {total_gb:.0f} gigabytes used."
    )


def cmd_switch_voice(c):
    voices = engine.getProperty("voices")
    if len(voices) < 2:
        speak("Only one voice on this system.")
        return
    current_id = engine.getProperty("voice")
    next_idx = 0
    for i, v in enumerate(voices):
        if v.id == current_id:
            next_idx = (i + 1) % len(voices)
            break
    engine.setProperty("voice", voices[next_idx].id)
    speak(f"Switched to voice {next_idx + 1}.")


def cmd_my_ip(c):
    try:
        r = requests.get("https://api.ipify.org?format=json", timeout=5)
        if r.status_code == 200:
            speak(f"Your public IP is {r.json().get('ip', '')}")
        else:
            speak("Couldn't grab your IP.")
    except Exception as e:
        print("IP error:", e)
        speak("Network's down.")


def cmd_define(c):
    word = (
        c.replace("define", "")
        .replace("what does", "")
        .replace("meaning of", "")
        .replace("mean", "")
        .strip()
    )
    if not word:
        speak("Which word?")
        return
    word = word.split()[0]
    try:
        r = requests.get(
            f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(word)}",
            timeout=5,
        )
        if r.status_code != 200:
            speak(f"No definition found for {word}.")
            return
        data = r.json()
        meanings = data[0].get("meanings", []) if isinstance(data, list) else []
        if not meanings:
            speak(f"Nothing on {word}.")
            return
        first = meanings[0]
        pos = first.get("partOfSpeech", "")
        defs = first.get("definitions", [])
        if defs:
            speak(f"{word}, {pos}. {defs[0]['definition']}")
        else:
            speak(f"No definition listed for {word}.")
    except Exception as e:
        print("Dictionary error:", e)
        speak("Dictionary isn't reachable.")


def cmd_joke(c):
    try:
        r = requests.get("https://official-joke-api.appspot.com/random_joke", timeout=5)
        if r.status_code == 200:
            data = r.json()
            speak(data.get("setup", ""))
            time.sleep(0.5)
            speak(data.get("punchline", ""))
        else:
            speak("Joke service is being grumpy.")
    except Exception as e:
        print("Joke error:", e)
        speak("Couldn't reach the joke service.")


def cmd_fact(c):
    try:
        r = requests.get("https://uselessfacts.jsph.pl/api/v2/facts/random?language=en", timeout=5)
        if r.status_code == 200:
            speak(r.json().get("text", "No fact today."))
        else:
            speak("Couldn't get a fact right now.")
    except Exception as e:
        print("Fact error:", e)
        speak("Fact service isn't reachable.")


def cmd_quote(c):
    try:
        r = requests.get("https://zenquotes.io/api/random", timeout=5)
        if r.status_code == 200:
            data = r.json()[0]
            speak(f"{data['q']} — {data['a']}")
        else:
            speak("Couldn't pull a quote right now.")
    except Exception as e:
        print("Quote error:", e)
        speak("Quote service isn't reachable.")


CRYPTO_ALIASES = {
    "btc": "bitcoin", "eth": "ethereum", "doge": "dogecoin",
    "xrp": "ripple", "ltc": "litecoin", "sol": "solana",
    "ada": "cardano", "bnb": "binancecoin", "matic": "matic-network",
}


def cmd_crypto(c):
    m = re.search(r"price of (\w+)|(\w+)\s+price", c)
    coin = (m.group(1) or m.group(2)).lower() if m else None
    if not coin:
        words = [w for w in c.split() if w not in ("how", "much", "is", "the", "what", "current")]
        coin = words[-1].lower() if words else None
    if not coin:
        speak("Which coin?")
        return
    coin = CRYPTO_ALIASES.get(coin, coin)
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd",
            timeout=5,
        )
        if r.status_code == 200 and coin in r.json():
            price = r.json()[coin]["usd"]
            speak(f"{coin.capitalize()} is at {price} dollars.")
        else:
            speak(f"No price found for {coin}.")
    except Exception as e:
        print("Crypto error:", e)
        speak("Crypto service isn't reachable.")


CURRENCY_NAMES = {
    "dollar": "USD", "dollars": "USD", "usd": "USD",
    "euro": "EUR", "euros": "EUR", "eur": "EUR",
    "pound": "GBP", "pounds": "GBP", "gbp": "GBP",
    "rupee": "INR", "rupees": "INR", "inr": "INR",
    "yen": "JPY", "jpy": "JPY",
    "yuan": "CNY", "cny": "CNY",
    "ruble": "RUB", "rubles": "RUB", "rub": "RUB",
    "real": "BRL", "brl": "BRL",
    "franc": "CHF", "francs": "CHF", "chf": "CHF",
    "won": "KRW", "krw": "KRW",
    "dirham": "AED", "aed": "AED",
}


def cmd_convert_currency(c):
    m = re.search(r"convert\s+(\d+(?:\.\d+)?)\s*([a-z]+)\s+(?:to|into)\s+([a-z]+)", c)
    if not m:
        speak("Try something like, convert 100 dollars to euros.")
        return
    amount = float(m.group(1))
    src = CURRENCY_NAMES.get(m.group(2), m.group(2).upper())
    dst = CURRENCY_NAMES.get(m.group(3), m.group(3).upper())
    try:
        r = requests.get(f"https://open.er-api.com/v6/latest/{src}", timeout=5)
        data = r.json()
        if data.get("result") != "success" or dst not in data.get("rates", {}):
            speak(f"Couldn't convert {src} to {dst}.")
            return
        converted = round(amount * data["rates"][dst], 2)
        speak(f"{amount} {src} is {converted} {dst}.")
    except Exception as e:
        print("Currency error:", e)
        speak("Currency service isn't reachable.")


def cmd_coin_flip(c):
    speak(random.choice(["Heads.", "Tails."]))


def cmd_roll_dice(c):
    m = re.search(r"(\d+)\s*sided", c)
    sides = int(m.group(1)) if m else 6
    if sides < 2:
        sides = 6
    speak(f"Rolled a {random.randint(1, sides)}.")


def cmd_stopwatch_start(c):
    global stopwatch_start
    if stopwatch_start is not None:
        speak("Stopwatch is already running.")
        return
    stopwatch_start = time.time()
    speak("Stopwatch started.")


def cmd_stopwatch_stop(c):
    global stopwatch_start
    if stopwatch_start is None:
        speak("Stopwatch isn't running.")
        return
    elapsed = int(time.time() - stopwatch_start)
    stopwatch_start = None
    mins, secs = divmod(elapsed, 60)
    if mins:
        speak(f"That was {mins} minutes and {secs} seconds.")
    else:
        speak(f"That was {secs} seconds.")


def cmd_help(c):
    speak(
        "I can open websites and apps, play songs, get the news, weather, time, date, "
        "search the web, look things up on Wikipedia, define words, set timers, take notes, "
        "do math, check crypto and currency, tell jokes, facts, or quotes, control volume, "
        "lock the screen, take screenshots, flip a coin, roll dice, or run a stopwatch. "
        "Say exit to quit."
    )


def cmd_exit(c):
    speak("Bye.")
    raise SystemExit


WEBSITES = [
    ("google",    "https://www.google.com",    "Google"),
    ("facebook",  "https://www.facebook.com",  "Facebook"),
    ("instagram", "https://www.instagram.com", "Instagram"),
    ("linkedin",  "https://www.linkedin.com",  "LinkedIn"),
    ("youtube",   "https://www.youtube.com",   "YouTube"),
    ("github",    "https://www.github.com",    "GitHub"),
    ("gmail",     "https://mail.google.com",   "Gmail"),
    ("twitter",   "https://www.twitter.com",   "Twitter"),
    ("reddit",    "https://www.reddit.com",    "Reddit"),
]

APPS = [
    ("notepad",        "notepad.exe",  "Notepad"),
    ("calculator",     "calc.exe",     "Calculator"),
    ("file explorer",  "explorer.exe", "File Explorer"),
    ("explorer",       "explorer.exe", "File Explorer"),
    ("paint",          "mspaint.exe",  "Paint"),
    ("command prompt", "cmd.exe",      "Command Prompt"),
]


# ============================================================
# Natural language layer — say it however you want, it figures it out.
# ============================================================
# Words to drop when extracting an entity (song name, city, topic, etc.).
# Note: "you" and "of" are deliberately NOT here — many song titles need them
# (e.g. "Shape of You", "End of an Era").
FILLER_WORDS = [
    "i", "want", "the", "a", "an", "please",
    "for", "me", "my",
    "tell", "give", "show", "is", "are", "was", "be",
    "what", "what's", "whats", "how", "how's",
    "right now", "currently", "right", "now", "this", "that",
    "and", "or", "but", "with",
    "make", "do", "did", "does", "got",
    "hey", "okay", "ok", "alright",
    # Multi-word phrases stripped as one unit (longest-first sort handles this).
    # "to" is NOT a generic filler because song/topic titles often need it
    # ("Stairway to Heaven", "Highway to Hell"). Strip it via these phrases.
    "can you", "could you", "would you", "will you", "please can you",
    "i would like to", "i'd like to", "i would like", "i'd like",
    "i want to", "i wanted to", "i wanna",
]


def strip_phrases(text, phrases):
    """Remove the given phrases from text using word boundaries (longest first)."""
    out = " " + text + " "
    for p in sorted(phrases, key=lambda x: -len(x)):
        if not p:
            continue
        out = re.sub(rf"\b{re.escape(p.lower())}\b", " ", out)
    return re.sub(r"\s+", " ", out).strip()


def extract_entity(text, trigger_phrases, also_strip=None):
    """Strip trigger phrases + filler words to leave just the entity."""
    out = strip_phrases(text.lower(), trigger_phrases)
    out = strip_phrases(out, FILLER_WORDS)
    if also_strip:
        out = strip_phrases(out, also_strip)
    return out


def handle_open(text):
    """Open a website or app — pick which one from the rest of the sentence."""
    aliases = {
        "email": "gmail", "mail": "gmail",
        "videos": "youtube", "video": "youtube",
        "code editor": "notepad", "text editor": "notepad",
        "calc": "calculator",
        "files": "file explorer", "file manager": "file explorer",
        "terminal": "command prompt", "cmd": "command prompt", "shell": "command prompt",
    }
    expanded = text.lower()
    for short, long in aliases.items():
        expanded = re.sub(rf"\b{re.escape(short)}\b", long, expanded)
    for key, url, name in WEBSITES:
        if re.search(rf"\b{re.escape(key)}\b", expanded):
            open_website(name, url)
            return
    for key, exe, name in APPS:
        if re.search(rf"\b{re.escape(key)}\b", expanded):
            open_app(name, exe)
            return
    target = extract_entity(text, ["open", "launch", "go to", "take me to", "fire up", "bring up", "start", "run"])
    if target:
        speak(f"I don't know how to open {target}.")
    else:
        speak("What should I open?")


def handle_play(text):
    song = extract_entity(
        text,
        ["play", "put on", "listen to", "i wanna hear", "i want to hear", "queue up", "queue", "stream", "hear"],
        also_strip=["song", "music", "track", "tune", "called", "named", "youtube", "on"],
    )
    if song:
        play_youtube(song)
    else:
        speak("What should I play?")


def handle_weather(text):
    city = extract_entity(
        text,
        ["weather", "forecast", "temperature", "raining", "sunny", "humid", "hot", "cold", "outside",
         "is it raining", "is it hot", "is it cold", "is it sunny",
         "what is the weather", "what's the weather", "how's the weather", "how is the weather"],
        also_strip=["in", "for", "at", "like", "out there", "out side"],
    )
    city = city or DEFAULT_CITY
    try:
        r = requests.get(f"https://wttr.in/{urllib.parse.quote(city)}?format=3", timeout=5)
        if r.status_code == 200 and r.text.strip():
            speak(r.text.encode("ascii", "ignore").decode().strip())
        else:
            speak("Couldn't fetch the weather right now.")
    except Exception as e:
        print("Weather error:", e)
        speak("Weather service isn't reachable.")


def handle_search(text):
    query = extract_entity(
        text,
        ["search for", "search the web for", "search the web", "search google for",
         "google", "search", "look up", "look online for", "look online", "find online", "find me"],
    )
    if not query:
        speak("What should I search for?")
        return
    webbrowser.open(f"https://www.google.com/search?q={urllib.parse.quote(query)}")
    speak(f"Searching for {query}")


def handle_wikipedia(text):
    topic = extract_entity(
        text,
        ["wikipedia", "tell me about", "who is", "who was", "what is", "what are",
         "what was", "history of", "info on", "info about"],
    )
    if not topic:
        speak("What topic?")
        return
    try:
        # Wikipedia uses underscores for multi-word titles. Capitalize first letter
        # for a better hit rate (e.g. "albert einstein" -> "Albert_einstein").
        wiki_topic = topic.replace(" ", "_")
        if wiki_topic:
            wiki_topic = wiki_topic[0].upper() + wiki_topic[1:]
        r = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(wiki_topic)}",
            headers=HTTP_HEADERS,
            timeout=8,
        )
        if r.status_code == 200:
            extract = r.json().get("extract", "").strip()
            if extract:
                sentences = re.split(r"(?<=[.!?])\s+", extract)
                speak(" ".join(sentences[:2]))
            else:
                speak(f"Nothing on {topic}.")
        else:
            speak(f"Couldn't find anything on {topic}.")
    except Exception as e:
        print("Wikipedia error:", e)
        speak("Wikipedia isn't reachable.")


def handle_define(text):
    word = extract_entity(
        text,
        ["define", "definition of", "meaning of", "what does", "what is the meaning of"],
        also_strip=["mean", "means", "word"],
    )
    if not word:
        speak("Which word?")
        return
    word = word.split()[0]
    try:
        r = requests.get(
            f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(word)}",
            timeout=5,
        )
        if r.status_code != 200:
            speak(f"No definition found for {word}.")
            return
        data = r.json()
        meanings = data[0].get("meanings", []) if isinstance(data, list) else []
        if not meanings:
            speak(f"Nothing on {word}.")
            return
        first = meanings[0]
        pos = first.get("partOfSpeech", "")
        defs = first.get("definitions", [])
        if defs:
            speak(f"{word}, {pos}. {defs[0]['definition']}")
        else:
            speak(f"No definition listed for {word}.")
    except Exception as e:
        print("Dictionary error:", e)
        speak("Dictionary isn't reachable.")


def handle_take_note(text):
    note = extract_entity(
        text,
        ["take a note", "save a note", "note that", "remember that", "remind me to",
         "write down", "remember to", "remember", "note", "save"],
    )
    if not note:
        speak("What's the note?")
        return
    add_note(note)
    speak("Got it, saved.")


def handle_greeting(text):
    speak(f"{time_of_day_greeting()}, {USER_NAME}.")


def handle_how_are_you(text):
    speak(random.choice([
        "Doing great, thanks for asking.",
        "All good. How can I help?",
        "Running smoothly. What do you need?",
    ]))


def handle_thank_you(text):
    speak(random.choice([
        "You're welcome.", "Anytime.", "Glad to help.", "Sure thing.",
    ]))


# ============================================================
# Intent registry — order matters when scores tie.
# ============================================================
INTENTS = [
    # Stopwatch (very specific phrases first)
    {"triggers": ["start stopwatch", "begin stopwatch", "start timing", "begin timing"],
     "handle": cmd_stopwatch_start},
    {"triggers": ["stop stopwatch", "end stopwatch", "stop timing", "end timing"],
     "handle": cmd_stopwatch_stop},

    # Open something
    {"triggers": ["open", "launch", "go to", "take me to", "fire up", "bring up", "run"],
     "handle": handle_open},

    # Play
    {"triggers": ["play", "put on", "listen to", "i wanna hear", "i want to hear",
                  "queue up", "queue", "stream", "hear"],
     "boost": ["song", "music", "track", "tune", "youtube"],
     "handle": handle_play},

    # News
    {"triggers": ["news", "headlines", "what's happening", "current events",
                  "world news", "latest news"],
     "handle": cmd_news},

    # Weather
    {"triggers": ["weather", "forecast", "temperature", "raining", "is it hot",
                  "is it cold", "is it sunny", "is it raining", "humid",
                  "what is the weather", "what's the weather", "how's the weather",
                  "how is the weather"],
     "boost": ["outside", "today", "right now"],
     "handle": handle_weather},

    # Search
    {"triggers": ["search for", "search the web", "search google", "google",
                  "search", "look up", "look online", "find online", "find me"],
     "handle": handle_search},

    # Time (BEFORE wikipedia so ties on "what is" go to time/date when relevant)
    {"triggers": ["what time", "the time", "current time", "tell me the time",
                  "time please", "time is it", "what's the time"],
     "handle": cmd_time},

    # Date. Include apostrophe-free variants because STT often drops apostrophes.
    {"triggers": ["what date", "what day", "today's date", "todays date",
                  "the date", "what is today", "what's today", "whats today",
                  "what is the date", "date today"],
     "handle": cmd_date},

    # Define
    {"triggers": ["define", "definition of", "meaning of", "what does"],
     "boost": ["mean", "means", "word"],
     "handle": handle_define},

    # Wikipedia. Note: "what is" / "what's" trigger here too, so it covers
    # generic "what is python" queries. Math wins over wikipedia when there
    # are digits + a math op word.
    {"triggers": ["wikipedia", "tell me about", "who is", "who was", "history of",
                  "info on", "info about", "what are", "what was",
                  "what is", "what's"],
     "boost": ["wiki"],
     "handle": handle_wikipedia},

    # Timer
    {"triggers": ["timer", "remind me in", "wake me in", "alarm", "alert me in",
                  "set a timer", "start a timer"],
     "boost": ["minutes", "seconds", "hours"],
     "handle": cmd_timer},

    # Take a note
    {"triggers": ["take a note", "save a note", "note that", "remember that",
                  "write down", "remember to"],
     "handle": handle_take_note},

    # Read notes
    {"triggers": ["read my notes", "read notes", "show notes", "what are my notes",
                  "my notes", "list notes"],
     "handle": cmd_read_notes},

    # System info
    {"triggers": ["system status", "system info", "battery", "cpu usage",
                  "memory usage", "ram usage", "how am i doing"],
     "boost": ["status", "stats"],
     "handle": cmd_system_info},

    # Disk
    {"triggers": ["disk usage", "disk space", "storage", "how much space",
                  "free space"],
     "handle": cmd_disk_usage},

    # Volume
    {"triggers": ["volume up", "louder", "turn it up", "increase volume",
                  "raise volume", "speak up"],
     "handle": cmd_volume_up},
    {"triggers": ["volume down", "quieter", "turn it down", "decrease volume",
                  "lower volume", "softer"],
     "handle": cmd_volume_down},
    {"triggers": ["mute", "unmute", "silent", "quiet down", "shut up", "stop sound"],
     "handle": cmd_mute},

    # Lock
    {"triggers": ["lock screen", "lock my computer", "lock the screen",
                  "lock the computer", "lock"],
     "handle": cmd_lock_screen},

    # Screenshot
    {"triggers": ["screenshot", "screen shot", "take a screenshot",
                  "snip", "capture screen"],
     "handle": cmd_screenshot},

    # Voice
    {"triggers": ["switch voice", "change voice", "different voice", "another voice"],
     "handle": cmd_switch_voice},

    # IP
    {"triggers": ["my ip", "ip address", "public ip", "what's my ip"],
     "handle": cmd_my_ip},

    # Crypto
    {"triggers": ["price of", "crypto price", "bitcoin", "ethereum", "dogecoin",
                  "btc price", "eth price"],
     "boost": ["price", "cost", "value", "worth"],
     "handle": cmd_crypto},

    # Currency
    {"triggers": ["convert", "exchange rate", "in dollars", "in euros",
                  "in rupees", "in pounds", "in yen"],
     "boost": ["how much"],
     "handle": cmd_convert_currency},

    # Joke / fact / quote
    {"triggers": ["joke", "make me laugh", "say something funny", "humor me",
                  "tell me a joke"],
     "handle": cmd_joke},
    {"triggers": ["fact", "trivia", "tell me something interesting",
                  "did you know", "random fact"],
     "handle": cmd_fact},
    {"triggers": ["quote", "inspire me", "motivate me", "wisdom",
                  "say something inspiring"],
     "handle": cmd_quote},

    # Coin / dice
    {"triggers": ["flip a coin", "coin flip", "heads or tails", "toss a coin"],
     "handle": cmd_coin_flip},
    {"triggers": ["roll a dice", "roll the dice", "roll a die", "roll dice",
                  "throw a dice", "throw a die", "random number",
                  "sided dice", "sided die"],
     "handle": cmd_roll_dice},

    # Repeat
    {"triggers": ["repeat", "say again", "say that again", "what did you say",
                  "come again", "pardon"],
     "handle": cmd_repeat},

    # Greetings
    {"triggers": ["hello", "hi there", "hey there", "good morning",
                  "good afternoon", "good evening"],
     "handle": handle_greeting},
    {"triggers": ["how are you", "how's it going", "what's up", "how do you feel"],
     "handle": handle_how_are_you},
    {"triggers": ["thank you", "thanks", "thank u"],
     "handle": handle_thank_you},

    # Help / exit
    {"triggers": ["help", "what can you do", "list commands", "your commands",
                  "what do you do"],
     "handle": cmd_help},
    {"triggers": ["exit", "quit", "goodbye", "bye", "shut down", "shutdown",
                  "go to sleep", "stop listening"],
     "handle": cmd_exit},

    # Math — only considered when there's a digit in the input.
    {"triggers": ["calculate", "compute", "what is", "what's", "math"],
     "boost": ["plus", "minus", "times", "multiplied", "divided", "power",
               "modulo", "added"],
     "requires_digit": True,
     "handle": cmd_math},
]


def find_intent(text):
    """Return (intent, score) of the highest-scoring matching intent, or (None, 0)."""
    best = None
    best_score = 0
    for intent in INTENTS:
        if intent.get("requires_digit") and not re.search(r"\d", text):
            continue
        score = 0
        for trig in intent.get("triggers", []):
            if trig in text:
                # Reward longer, more specific phrases
                score += 10 + len(trig.split())
        for boost in intent.get("boost", []):
            if boost in text:
                score += 3
        if score > best_score:
            best = intent
            best_score = score
    return best, best_score


MATH_OP_WORDS = re.compile(
    r"\b(plus|minus|times|multiplied|divided|over|power|squared|cubed|modulo|added|subtract)\b"
)


def processCommand(c):
    text = re.sub(r"\s+", " ", c.lower().strip())
    intent, score = find_intent(text)
    if intent:
        print(f"  [intent] {intent['handle'].__name__} (score {score})")
        intent["handle"](text)
        return
    # Last resort: a bare math expression like "5 plus 3" with no command word.
    # Only fire if there's both a digit AND a math operator word.
    if re.search(r"\d", text) and MATH_OP_WORDS.search(text):
        cmd_math(text)
        return
    speak("Didn't catch that. Say help for what I can do.")


def keyboard_input_loop():
    """Type commands directly. This is always available — voice or no voice."""
    while True:
        try:
            cmd = input("\n>>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            os._exit(0)
        if not cmd:
            continue
        try:
            processCommand(cmd.lower())
        except SystemExit:
            os._exit(0)
        except Exception as e:
            print("Command error:", e)


def check_microphone():
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
        return True
    except Exception as e:
        print(f"  microphone error: {e}")
        return False


def strip_wake_words(text):
    """Remove any wake-word stems from the text and return what's left."""
    out = " " + text + " "
    for stem in sorted(WAKE_STEMS, key=lambda x: -len(x)):
        out = re.sub(rf"\b{re.escape(stem)}\b", " ", out)
    return re.sub(r"\s+", " ", out).strip()


def voice_loop(always_listen):
    while True:
        try:
            if always_listen:
                command = listen(timeout=15, phrase_time_limit=12, language="en-US")
                if command:
                    processCommand(command)
                continue

            word = listen(timeout=8, phrase_time_limit=8, language="en-US")
            if not word:
                continue
            if is_wake_word(word):
                # Did they say wake word + command in one breath?
                # e.g., "widerhall play hamdard" → just run "play hamdard".
                rest = strip_wake_words(word)
                if rest:
                    print(f"  (wake + command in one breath)")
                    processCommand(rest)
                else:
                    speak("Yes?")
                    command = listen(timeout=12, phrase_time_limit=12, language="en-US")
                    if command:
                        processCommand(command)
                continue
            # No wake word — but if the heard text already matches an intent,
            # just run it directly.
            intent, score = find_intent(word)
            if intent:
                print(f"  (treating '{word}' as a direct command)")
                processCommand(word)
        except SystemExit:
            os._exit(0)
        except KeyboardInterrupt:
            os._exit(0)
        except Exception as e:
            print(f"  voice loop error: {e}")
            time.sleep(1)


def print_banner(voice_ok, always_listen):
    bar = "=" * 64
    print(bar)
    print("  WIDERHALL  -  Voice Assistant")
    print(bar)
    print(f"  User       : {USER_NAME}")
    print(f"  News API   : {'configured' if NEWS_API_KEY else 'missing'}")
    print(f"  psutil     : {'available' if psutil else 'not installed'}")
    print(f"  Microphone : {'OK' if voice_ok else 'NOT WORKING (typed mode only)'}")
    if voice_ok:
        if always_listen:
            print(f"  Voice mode : ALWAYS LISTENING (no wake word)")
        else:
            print(f"  Voice mode : wake-word (say one of below, then your command)")
            print(f"               Widerhall / Echo / Computer / Jarvis / Assistant / Hello")
    print(bar)
    print("  TYPE A COMMAND BELOW AND PRESS ENTER:")
    print()
    print("    play perfect           - opens that song on YouTube")
    print("    tell me a joke         - random joke")
    print("    weather in berlin      - current weather")
    print("    what is 17 times 4     - math")
    print("    open notepad           - launches Notepad")
    print("    set a timer for 1 minute")
    print("    convert 100 dollars to euros")
    print("    price of bitcoin")
    print("    define ephemeral")
    print("    help                   - hear the full list")
    print("    exit                   - quit")
    print(bar)


if __name__ == "__main__":
    always_listen = os.getenv("WIDERHALL_ALWAYS_LISTEN", "").lower() in ("1", "true", "yes")
    text_only = os.getenv("WIDERHALL_TEXT_ONLY", "").lower() in ("1", "true", "yes")

    voice_ok = False if text_only else check_microphone()

    print_banner(voice_ok, always_listen)

    if not voice_ok and not text_only:
        print("  Available microphones:")
        for i, name in enumerate(sr.Microphone.list_microphone_names()):
            print(f"    [{i}] {name}")
        print()

    try:
        speak(f"{time_of_day_greeting()}, {USER_NAME}. Widerhall is ready. Type or speak a command.")
    except Exception as e:
        print(f"  [tts] could not speak greeting: {e}")
        print(f"  Continuing in print-only mode.")

    if voice_ok:
        voice_thread = threading.Thread(target=voice_loop, args=(always_listen,), daemon=True)
        voice_thread.start()

    keyboard_input_loop()
