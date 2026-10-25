"""Does the request actually authorise deletion?  (multilingual, deterministic, conservative)

A request is a deletion request when it asks for permanent removal/closure/erasure of the account and
its personal data.  Access, export, correction, e-mail change, login help, invoice copy and marketing
opt-out requests - often phrased with words such as "remove" or "close" but explicitly preserving the
account - are NOT_APPLICABLE: no destructive action is authorised.
"""
from __future__ import annotations

import os
import re
from typing import Tuple

# verbs/nouns that, on their own, express deletion in the languages seen in the data
_DELETE = re.compile(
    r"permanent|delet|eras(?:e|ing|ure)|clos(?:e|ed|ure)\b|remov(?:e|ed|al)\b|\bgone\b|clean (?:my|out)|wipe|"
    r"eliminaci[oó]n|eliminar|elimine|borrar|supresi[oó]n|"                  # es
    r"exclus[aã]o|excluir|apagar|eliminar|"                                    # pt
    r"suppression|supprimer|supprim|"                                          # fr
    r"löschen|löschung|"                                                       # de
    r"削除|消去|退会|"                                                         # ja
    r"삭제|영구|탈퇴|"                                                         # ko
    r"删除|注销|清除|抹除|永久", re.I)                                         # zh
# explicit preservation / narrower-scope cues
_KEEP = re.compile(
    r"leave (?:the|my) (?:account|profile)|keep (?:the|my) account|do not close|don't close|do not delete|"
    r"still need|continue using|i want to continue|account active|marketing lists only|only (?:the )?marketing|"
    r"stop the promotional|(?:send|need|give|want|get|provide)\b.{0,25}\ba copy of|download my|portable format|what personal information you hold|"
    r"reset(?:ting)? the login|misspell|out of date|change the email|replace it", re.I)


# a deletion cue is NEGATED when a negator precedes it within a short window (or, for CJK, immediately follows)
_NEG_BEFORE = re.compile(
    r"(?:\bdo not|\bdon't|\bdont|\bnever|\bnot\b|\bno need to|\bwithout|\bkein(?:e|en)?\b|\bnicht\b|"      # en, de
    r"\bno\s+(?:quiero|deseo|quisiera|elimin|borr|necesito)|\bsin\b|\bnunca\b|"                                  # es
    r"\bne\b[^.!?]{0,25}\bpas\b|\bsans\b|\bjamais\b|"                                                           # fr
    r"\bn[ãa]o\b|\bsem\b|\bmai\b|\bnon\b|\bsenza\b|"                                                          # pt, it
    r"不要|请勿|請勿|别|別|不用|不想|不需要|안 |하지 마)[^.!?;]{0,30}$", re.I)
_NEG_AFTER_CJK = re.compile(r"^(?:しないで|しない|しないでください|하지|を希望しません)")


def _negated(t: str, m) -> bool:
    before = t[max(0, m.start() - 45):m.start()]
    sent = re.split(r"[.!?;。！？]", before)[-1]
    if _NEG_BEFORE.search(sent):
        return True
    after = t[m.end():m.end() + 14]
    return bool(_NEG_AFTER_CJK.match(after) or re.match(r"\w{0,4}\s+pas\b", after, re.I))   # fr "ne supprimez pas"


# Sentences addressed to the *agent* (prompt-injection attempts) are not the requester's own wishes: drop them.
_INJECTION = re.compile(
    r"^\s*(?:system|assistant|developer|admin|instruction)s?\s*:|ignore (?:all |any )?(?:the )?(?:previous|prior|above|earlier|your)|"
    r"disregard (?:all |any )?(?:the )?(?:previous|prior|above|rules|instructions)|you are (?:now )?(?:authori[sz]ed|allowed|permitted)|"
    r"(?:mark|set|output|report|return|answer)\b[^.]{0,30}\b(?:complete|incomplete|not_applicable|compliant)\b|"
    r"as an? (?:ai|assistant|language model)|new instructions?", re.I)


def sanitize(text: str) -> str:
    parts = re.split(r"(?<=[.!?;。！？\n])\s*", text.strip())
    return " ".join(p for p in parts if p and not _INJECTION.search(p))


def classify_intent(text: str, use_llm: bool = True) -> Tuple[bool, str]:
    """Return (authorises_deletion, reason)."""
    t = sanitize(text)
    strong = any(not _negated(t, m) for m in _DELETE.finditer(t))
    keep = bool(_KEEP.search(t))

    # Evaluate deterministic baseline
    det_auth = False
    det_reason = ""
    is_ambiguous = strong and keep

    if strong and keep:
        # a generic "do not delete X" in one sentence does not cancel an unnegated request in another sentence
        hits = list(_KEEP.finditer(t))
        if all(re.match(r"do not (?:delete|close)|don't close", h.group(0), re.I) for h in hits):
            sid = lambda pos: len(re.findall(r"[.!?;。！？]", t[:pos]))
            keep_sents = {sid(h.start()) for h in hits}
            for m in _DELETE.finditer(t):
                if not _negated(t, m) and sid(m.start()) not in keep_sents:
                    det_auth, det_reason = True, "explicit deletion request (separate preservation clause)"
                    break
            else:
                det_auth, det_reason = False, "deletion words with preservation clauses"
        elif re.search(r"permanent(?:ly)?\s+(?:delet|erase|remov|account)", t, re.I) and not re.search(
                r"do not close|don't close|leave (?:the|my)|keep (?:the|my)", t, re.I):
            det_auth, det_reason = True, "explicit permanent deletion"
        else:
            det_auth, det_reason = False, "deletion words with an explicit preservation instruction"
    elif strong and not keep:
        det_auth, det_reason = True, "explicit deletion request"
    else:
        det_auth, det_reason = False, "no deletion authorised (access / correction / export / opt-out)"

    # When ambiguous (mixed signals) and LLM is enabled, verify with OpenAI
    if is_ambiguous and use_llm and os.getenv("GHOSTGUARD_NO_LLM", "0") != "1":
        try:
            from deletion_audit.openai_client import AI_CLIENT
            if AI_CLIENT.is_available():
                auth, reason, used = AI_CLIENT.analyze_ambiguous_intent(t, (det_auth, det_reason))
                if used:
                    return auth, reason
        except Exception:
            pass

    return det_auth, det_reason
