"""De-identification: replace students' identifying details with pseudonyms.

Runs locally before ANY text is sent to an external AI provider. Every student in
the assignment is in the roster (not only the one being graded), so classmates
named in a poster or note are replaced too.

What is replaced:
  * full names and other names, in any letter case, with or without commas;
  * runs of two or more of a student's name words in either order
    ("Mei Ling", "Mei Ling Tan", "Wei Jie Lim");
  * single name words written as names (Title Case or UPPER CASE), unless the word
    is also an ordinary English word or place name ("Low", "Tan", "Hong", "Kong"):
    those are replaced only as part of a multi-word run;
  * matriculation numbers, e-mail addresses and Singapore phone numbers.

Replacements look like "[S07]"; re-identification maps them back locally.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

# Name words that are also ordinary English words or place names, plus very common
# Malay/Arabic name parts and connectors. On their own they are not replaced
# (doing so would break unrelated text, e.g. "low motivation", "Hong Kong");
# they are still replaced inside multi-word name runs.
AMBIGUOUS = set("""
low long may mark will hope joy rose grace faith crystal jade tan sin sun see son say ray sky
win art june summer star hong kong china india
bin binte binti bte bt d-o s-o a-l a-p muhammad mohd mohammad nur siti abdul
""".split())

MATRIC_RE = re.compile(r"(?<![A-Za-z0-9])U\d{7}[A-Z](?![A-Za-z0-9])", re.IGNORECASE)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?<!\d)(?:\+65[ -]?)?[689]\d{3}[ -]?\d{4}(?!\d)")


@dataclass
class RosterEntry:
    pseudonym: str
    display_name: str
    full_name: str
    other_name: str | None
    matric: str | None
    name_tokens: list[str]


@dataclass
class DeidResult:
    text: str
    replacements: list[tuple[str, str]] = field(default_factory=list)  # (original, placeholder)


def _words(name: str) -> list[str]:
    return [w for w in re.split(r"[\s,()]+", name) if w]


def _phrases(e: RosterEntry) -> list[str]:
    """Multi-word sequences that identify this student, longest first."""
    out = {e.full_name, e.full_name.replace(",", ""), e.display_name}
    if e.other_name:
        out.add(e.other_name)
    for name in filter(None, [e.full_name, e.other_name]):
        w = _words(name)
        for n in range(2, len(w) + 1):
            for i in range(len(w) - n + 1):
                out.add(" ".join(w[i:i + n]))
        for i in range(len(w) - 1):  # run together: "MeiLing", "weijie"
            joined = w[i] + w[i + 1]
            if len(joined) >= 5 and w[i].lower() not in AMBIGUOUS and w[i + 1].lower() not in AMBIGUOUS:
                out.add(joined)
        if len(w) >= 2:  # surname moved to the end: "Mei Ling Tan"
            out.add(" ".join(w[1:] + w[:1]))
            out.add(" ".join(w[-1:] + w[:-1]))
    return sorted((p for p in out if len(p) > 2), key=len, reverse=True)


def _single_words(e: RosterEntry) -> list[str]:
    ws = set()
    for name in filter(None, [e.full_name, e.other_name]):
        for w in _words(name):
            if len(w) >= 2 and w.lower() not in AMBIGUOUS:
                ws.add(w)
    return sorted(ws, key=len, reverse=True)


class Deidentifier:
    def __init__(self, roster: list[RosterEntry]):
        self.roster = roster
        self.by_pseudonym = {e.pseudonym: e for e in roster}
        rules: list[tuple[re.Pattern, str]] = []
        phrase_rules, word_rules = [], []
        for e in roster:
            ph = f"[{e.pseudonym}]"
            for p in _phrases(e):
                words = [re.escape(w) for w in _words(p)]
                pat = r"[\s,]+".join(words)
                phrase_rules.append((len(p), re.compile(rf"(?<![\w-]){pat}(?![\w-])", re.IGNORECASE), ph))
            for w in _single_words(e):
                # Title Case or UPPER CASE only, as a standalone word
                variants = {w.capitalize(), w.upper()}
                alt = "|".join(re.escape(v) for v in sorted(variants))
                word_rules.append((len(w), re.compile(rf"(?<![\w-])(?:{alt})(?![\w-])"), ph))
        # longest phrases first so "Tan Mei Ling" wins over "Mei Ling"
        rules += [(r, ph) for _, r, ph in sorted(phrase_rules, key=lambda x: -x[0])]
        rules += [(r, ph) for _, r, ph in sorted(word_rules, key=lambda x: -x[0])]
        self.rules = rules

    def deidentify(self, text: str) -> DeidResult:
        if not text:
            return DeidResult(text or "")
        reps: list[tuple[str, str]] = []

        def sub(pattern: re.Pattern, placeholder: str, s: str) -> str:
            def f(m: re.Match) -> str:
                reps.append((m.group(0), placeholder))
                return placeholder
            return pattern.sub(f, s)

        text = sub(EMAIL_RE, "[EMAIL]", text)   # before names: addresses often contain them
        text = sub(MATRIC_RE, "[ID]", text)
        text = sub(PHONE_RE, "[PHONE]", text)
        for pattern, ph in self.rules:
            text = sub(pattern, ph, text)
        text = re.sub(r"(\[S\d+\])(?:[\s,]+\1)+", r"\1", text)  # "[S05] [S05]" -> "[S05]"
        return DeidResult(text, reps)

    def residuals(self, text: str) -> list[str]:
        """Name words still present after de-identification, in ANY letter case
        (e.g. "meiling" in a URL). Shown to the instructor before sending."""
        found = set()
        for e in self.roster:
            joined = [p for p in _phrases(e) if " " not in p and p not in _single_words(e)]
            for w in _single_words(e) + joined:
                if len(w) >= 3 and re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", text, re.IGNORECASE):
                    found.add(w.capitalize())
        return sorted(found)

    def reidentify(self, text: str) -> str:
        def f(m: re.Match) -> str:
            e = self.by_pseudonym.get(m.group(1))
            return e.display_name if e else m.group(0)
        return re.sub(r"\[(S\d+)\]", f, text or "")


def roster_from_students(students) -> list[RosterEntry]:
    return [RosterEntry(s.pseudonym, s.display_name, s.full_name, s.other_name, s.matric,
                        list(s.name_tokens or [])) for s in students]
