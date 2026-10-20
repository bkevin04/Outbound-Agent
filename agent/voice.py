"""Lint text for the things that give it away as AI, from Kevin's voice rules.

Errors block the stage; warnings are for the agent to read the sentence again as Kevin on a call."""
import re

# Words and phrases Kevin has said never to write (kevin-voice "Never write", "Not his English").
BANNED = [
    "leverage", "seamless", "seamlessly", "robust", "unlock", "delve", "landscape", "journey", "north star",
    "move the needle", "unpack", "stands out", "speaks to", "surprised me", "which i did not expect", "notably",
    "compelling", "crucial", "genuinely", "pattern", "patterns", "passionate about", "proven track record",
    "fast-paced", "the right doors", "the message lands", "sell themselves", "hits the biggest worry",
    "a story bigger than", "game-changer", "game changer", "cutting-edge", "hope this email finds you",
    "circle back", "touch base", "synergy", "empower", "revolutionize", "revolutionise", "supercharge",
    "in today's", "wedge", "motion", "signals", "double down", "re-ups",
]
JOINERS = ["which", "while", "since", "even though", "whereas"]


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def lint(text, where=""):
    """Return (errors, warnings) for one piece of text."""
    errors, warnings = [], []
    tag = f"{where}: " if where else ""
    low = text.lower()
    for b in BANNED:
        if re.search(r"(?<![a-z])%s(?![a-z])" % re.escape(b), low):
            errors.append(f"{tag}'{b}' is on Kevin's never-write list")
    if "—" in text or re.search(r"\w – \w", text):
        errors.append(f"{tag}em/en dash inside a sentence: use a comma or a colon")
    if ";" in text:
        errors.append(f"{tag}semicolon: split it or join with 'and' / 'so' / 'but'")
    if re.search(r"~\s?[$€£\d]", text):
        errors.append(f"{tag}'~' before a number: write numbers plain ('$5B')")
    if len(re.findall(r"\bexactly\b", low)) > 1:
        errors.append(f"{tag}'exactly' more than once")
    for j in JOINERS:
        if re.search(r"\b%s\b" % j, low):
            warnings.append(f"{tag}'{j}' hangs a second point on: join with 'and' / 'so' / 'but' or cut it")
    if re.search(r"\bnot (just )?[^.,]{1,50}, (it is|it's|but)\b", low):
        warnings.append(f"{tag}looks like a 'not X, it is Y' turn")
    for s in _sentences(text):
        n = len(s.split())
        if n > 32:
            warnings.append(f"{tag}{n}-word sentence: '{s[:60]}...'")
    return errors, warnings
