"""Safety layer between features and language models.

No prompt can guarantee a model will never be manipulated, so this layer assumes
it can be and limits what a manipulated model could achieve:

- Input: companies are validated, documents are cleaned and capped, and pages
  that address an AI are withheld. Internal data needs explicit approval.
- Actions: a model can only return text. Web searches it asks for are checked
  and run by ``research/follow_up.py``, which never shows it internal data.
- Prompt: untrusted text is fenced with a random per-request boundary a web
  page cannot guess, and features supply fixed task instructions, so there is
  no way to send a model a free-form request.
- Output: everything a model returns is treated as untrusted. It must be JSON
  matching the feature's model, cite only documents it was given, use the
  vocabulary of the supplied data rather than an unrelated topic, and contain no
  links, markup, code, credentials, or leaked instructions. Anything else is
  discarded rather than repaired.

The pattern checks are one layer among several and can be evaded on their own;
the limits that matter are structural (no tools, fixed tasks, validated output).
"""

import json
import logging
import os
import re
import secrets
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ValidationError

from sfs_intelligence.llm.base import (
    LLMError,
    LLMPolicyError,
    LLMProvider,
    LLMRequest,
    provider_from_env,
)
from sfs_intelligence.llm.schema import strict_schema

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())  # Stay out of the CLI menus unless logging is set up.

ALLOW_INTERNAL_ENV_VAR = "LLM_ALLOW_INTERNAL_DATA"
PROMPTS_DIR = Path(__file__).with_name("prompts")

MAX_SUBJECT_CHARS = 100
MAX_SUBJECT_WORDS = 10
MAX_DOCUMENT_CHARS = 3_000
MAX_CONTEXT_CHARS = 60_000
MAX_RESPONSE_CHARS = 40_000
MAX_OUTPUT_FIELD_CHARS = 1_500
# Must cover a full report and leave reasoning models room to think; real reports
# have reached about 8,000 tokens, and a cut-off report is lost entirely.
MAX_OUTPUT_TOKENS = 12_000

_SUBJECT_CHARS = re.compile(r"\w[\w &.,'’\"()/+!#@:%|-]*")
_SUBJECT_REQUEST = re.compile(
    r"^(write|generate|create|explain|tell|give|make|help|how|what|why|please|can you)\s"
    r"+(me|us|an?|the|some|to|do|is|are|you)\b",
    re.IGNORECASE,
)

_SUBJECT_QUESTION = re.compile(
    r"^(who|what|when|where|why|how|which)\s+(is|are|was|were|do|does|did|won|many|much|can|to)\b",
    re.IGNORECASE,
)

_INSTRUCTION_NOUNS = r"(instruction|prompt|rule|guideline|direction|polic(y|ie)|safeguard|restriction)s?"
_SOME_WORDS = r"(?:\w+\W+){0,4}?"
_AI_NOUNS = r"(ai|llm|language model|assistant|chatbot|gpt|claude|gemini|copilot|agent)s?"

# Checked against text normalized by ``_normalize``.
_INJECTION_PATTERNS = tuple(
    re.compile(pattern, re.MULTILINE)
    for pattern in (
        rf"\b(ignore|disregard|forget|override|bypass)\W+{_SOME_WORDS}"
        rf"(previous|prior|above|earlier|preceding|foregoing|all|any|your|system|safety)\W+"
        rf"{_SOME_WORDS}{_INSTRUCTION_NOUNS}\b",
        rf"\b(reveal|print|repeat|output|leak|show|tell)\W+{_SOME_WORDS}"
        rf"(system prompt|your (instructions|prompt|rules)|hidden prompt|initial prompt|api key)\b",
        r"\b(system prompt|developer mode|jailbreak mode|jailbroken|do anything now|dan mode)\b",
        r"\byou are (now|no longer)\b",
        r"\b(act|behave|respond) as (an? )?(unrestricted|unfiltered|jailbroken|evil|different)\b",
        r"\bpretend (that )?you\b",
        r"\bnew (instructions?|system message|task)\s*:",
        r"^\s*(system|assistant|developer)\s*:",
        r"</?(system|assistant|instructions?)>|\[/?inst\]|<\|im_(start|end)\|>",
        rf"\b{_AI_NOUNS}\W+{_SOME_WORDS}(reading|processing|summari[sz]ing|analy[sz]ing)\b",
        rf"\b(attention|note|message|instructions?) (to|for) (any |all |the )?{_AI_NOUNS}\b",
        rf"\battention,? (any |all )?{_AI_NOUNS}\b",
        # German, French, and Spanish, for SFS's European markets and sources.
        r"\b(ignoriere|ignorieren sie|vergiss)\W+(?:\w+\W+){0,4}?(anweisungen|befehle|regeln)\b",
        r"\b(ignorez|ignore|oublie[zr]?)\W+(?:\w+\W+){0,4}?(instructions|consignes|regles)\b",
        r"\b(ignora|ignore|olvida)\W+(?:\w+\W+){0,4}?(instrucciones|reglas)\b",
    )
)
# Catches phrases split up to dodge the patterns, e.g. "i g n o r e".
_SQUEEZED_PHRASES = (
    "ignoreallpreviousinstructions",
    "ignorepreviousinstructions",
    "ignoreyourinstructions",
    "disregardpreviousinstructions",
    "revealyoursystemprompt",
    "systemprompt",
)

_OUTPUT_RULES = tuple(
    (reason, re.compile(pattern, re.IGNORECASE))
    for reason, pattern in (
        ("a link", r"[a-z][a-z0-9+.-]*://|\bwww\.|\]\(|!\[|\b[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}/\S"),
        ("markup", r"<\s*/?[a-z!][^>]*>"),
        ("a code block", r"```|~~~"),
        (
            "code or commands",
            r"\b(curl|wget|powershell|sudo|chmod|rm -rf|eval\(|exec\(|os\.system|subprocess|"
            r"base64 -d|import (os|sys|socket|requests))\b|\bdef \w+\(|\bfunction \w+\(",
        ),
        (
            "a credential",
            r"\b(sk-[a-z0-9_-]{16,}|gh[pousr]_[a-z0-9]{20,}|github_pat_\w{20,}|AKIA[0-9A-Z]{16}|"
            r"AIza[0-9a-z_-]{30,}|xox[baprs]-[a-z0-9-]{10,})|-----BEGIN [A-Z ]*PRIVATE KEY",
        ),
    )
)


# A field must use at least this share of words from the supplied data. Words are
# compared by their first letters, so "expanding" matches "expansion".
MIN_GROUNDED_SHARE = 0.5
MIN_WORDS_TO_CHECK = 5
_WORD_PREFIX_CHARS = 5
_WORD = re.compile(r"[^\W\d_]{4,}")
_COMMON_WORDS = frozenset(
    """about also after been before being both could does each even every from have
    here into just like made make many more most much must only other over same
    should some such than that their them then there these they this those through
    under very were what when where which while will with would your""".split()
)
# Fields a model writes about the evidence rather than from it; capped in length instead.
_UNGROUNDED_FIELDS = ("limitations",)


class UnsafeInputError(ValueError):
    """Raised when a company name is not acceptable as model or search input."""


@dataclass(frozen=True)
class UntrustedDocument:
    """Text from outside the system, e.g. a web page or an internal record."""

    id: str
    title: str
    text: str
    url: str | None = None
    category: str | None = None
    internal: bool = False
    published: date | None = None


@dataclass(frozen=True)
class AnalysisResult[T: BaseModel]:
    output: T
    sent_ids: tuple[str, ...]
    withheld_ids: tuple[str, ...]
    omitted_ids: tuple[str, ...]


def validate_subject(subject: str) -> str:
    """Return a cleaned company name, or raise ``UnsafeInputError``."""
    subject = " ".join(subject.split())
    if not subject:
        raise UnsafeInputError("A company name is required.")
    if len(subject) > MAX_SUBJECT_CHARS or len(subject.split()) > MAX_SUBJECT_WORDS:
        raise UnsafeInputError("That is too long for a company name.")
    if not _SUBJECT_CHARS.fullmatch(subject):
        raise UnsafeInputError("A company name cannot contain those characters.")
    if _SUBJECT_REQUEST.match(subject) or _SUBJECT_QUESTION.match(subject) or find_injection(subject):
        raise UnsafeInputError("Enter only a company name, not a request or instructions.")
    return subject


def clean_text(text: str) -> str:
    """Remove invisible and control characters that can hide text from a reader."""
    return "".join(
        character
        for character in text
        if character in "\n\t" or unicodedata.category(character) not in ("Cc", "Cf", "Co", "Cn")
    ).strip()


def find_injection(text: str) -> str | None:
    """Return text that appears to address an AI rather than inform a reader."""
    normalized = _normalize(text)
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(normalized)
        if match:
            return match.group(0)
    squeezed = re.sub(r"\W+", "", normalized)
    return next((phrase for phrase in _SQUEEZED_PHRASES if phrase in squeezed), None)


def _normalize(text: str) -> str:
    """Undo look-alike characters, accents, and spacing tricks before matching."""
    decomposed = unicodedata.normalize("NFKD", clean_text(text))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[ \t]+", " ", stripped).casefold()


def find_output_violation(value: Any) -> str | None:
    """Return why a model's output is unacceptable, checking every string in it."""
    if isinstance(value, str):
        return _string_violation(value)
    if isinstance(value, dict):
        value = value.values()
    elif not isinstance(value, (list, tuple)):
        return None
    return next((found for item in value if (found := find_output_violation(item))), None)


def find_ungrounded_text(value: Any, known_words: set[str]) -> bool:
    """Return True if any field is mostly words absent from the supplied data.

    This is what keeps answers to the data: a model that writes about anything
    else, whatever it was told or claims, fails here. It depends only on the
    words returned, not on the model's cooperation.
    """
    if isinstance(value, str):
        words = _content_words(value)
        if len(words) < MIN_WORDS_TO_CHECK:
            return False
        return sum(word in known_words for word in words) / len(words) < MIN_GROUNDED_SHARE
    if isinstance(value, dict):
        return any(
            find_ungrounded_text(item, known_words)
            for key, item in value.items()
            if key not in _UNGROUNDED_FIELDS
        )
    if isinstance(value, (list, tuple)):
        return any(find_ungrounded_text(item, known_words) for item in value)
    return False


def _content_words(text: str) -> list[str]:
    words = (word.casefold() for word in _WORD.findall(text))
    return [word[:_WORD_PREFIX_CHARS] for word in words if word not in _COMMON_WORDS]


def _string_violation(text: str) -> str | None:
    if len(text) > MAX_OUTPUT_FIELD_CHARS:
        return "an overly long field"
    if clean_text(text) != text.strip():
        return "control or invisible characters"
    for reason, pattern in _OUTPUT_RULES:
        if pattern.search(text):
            return reason
    return "language aimed at an AI" if find_injection(text) else None


class GuardedLLM:
    """The only way features reach a model. See the module docstring."""

    def __init__(self, provider: LLMProvider, allow_internal_data: bool = False) -> None:
        self._provider = provider
        self._allow_internal_data = allow_internal_data

    @property
    def label(self) -> str:
        return f"{self._provider.name}, {self._provider.model}"

    def analyze[T: BaseModel](
        self,
        task: str,
        subject: str,
        documents: Sequence[UntrustedDocument],
        output_model: type[T],
    ) -> AnalysisResult[T]:
        """Run the fixed prompt ``task`` (a file in ``prompts/``) over ``documents``.

        ``output_model`` must define the only fields the model may return.
        """
        subject = validate_subject(subject)
        self._check_data_policy(documents)
        sent, withheld, omitted = _prepare_documents(documents)
        if not sent:
            raise LLMPolicyError("No documents were safe to send to the language model.")

        boundary = secrets.token_hex(16)
        request = LLMRequest(
            system=_load_prompt("security_policy").replace("{boundary}", boundary),
            instructions=_load_prompt(task),
            context=_build_context(boundary, subject, sent),
            schema=strict_schema(output_model),
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        sent_ids = tuple(document.id for document in sent)
        known_words = set(_content_words(f"{request.instructions}\n{request.context}"))
        output = self._call_with_retry(request, output_model, set(sent_ids), boundary, known_words)
        return AnalysisResult(output, sent_ids, withheld, omitted)

    def _check_data_policy(self, documents: Sequence[UntrustedDocument]) -> None:
        if self._allow_internal_data:
            return
        if any(document.internal for document in documents):
            raise LLMPolicyError(
                f"Internal data cannot be sent to {self._provider.name} "
                f"unless {ALLOW_INTERNAL_ENV_VAR} is approved and set to true."
            )

    def _call_with_retry[T: BaseModel](
        self,
        request: LLMRequest,
        output_model: type[T],
        allowed_ids: set[str],
        boundary: str,
        known_words: set[str],
    ) -> T:
        """Allow one retry for an invalid response. Refusals and call errors are final."""
        violation = ""
        for _ in range(2):
            response = self._provider.complete(_with_correction(request, violation))
            logger.info(
                "LLM call: provider=%s model=%s input_tokens=%s output_tokens=%s",
                self._provider.name, response.model, response.input_tokens, response.output_tokens,
            )
            try:
                return _validate_output(
                    response.text, output_model, allowed_ids, boundary, known_words
                )
            except _RejectedOutput as rejection:
                violation = str(rejection)
                logger.warning("LLM output rejected: provider=%s reason=%s", self._provider.name, violation)
        raise LLMPolicyError(f"The model's response was rejected: it contained {violation}.")


class _RejectedOutput(Exception):
    pass


def _with_correction(request: LLMRequest, violation: str) -> LLMRequest:
    """Add why the last answer failed. This is this module's wording, never model text."""
    if not violation:
        return request
    note = f"Your previous answer was rejected because it contained {violation}. Correct this."
    return replace(request, instructions=f"{request.instructions}\n\n{note}")


def _validate_output[T: BaseModel](
    text: str, output_model: type[T], allowed_ids: set[str], boundary: str, known_words: set[str]
) -> T:
    if len(text) > MAX_RESPONSE_CHARS:
        raise _RejectedOutput("too much text")
    if boundary in text:
        raise _RejectedOutput("leaked instructions")
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        raise _RejectedOutput("invalid JSON") from None
    try:
        output = output_model.model_validate(data)
    except (ValidationError, RecursionError) as error:
        fields = _invalid_fields(error, output_model)
        logger.warning("LLM output invalid: %s", fields)
        raise _RejectedOutput(f"fields that do not match the required format: {fields}") from None

    dumped = output.model_dump(mode="json")
    violation = find_output_violation(dumped)
    if violation:
        raise _RejectedOutput(violation)
    unknown = _cited_ids(dumped) - allowed_ids
    if unknown:
        raise _RejectedOutput("citations of sources it was not given")
    if find_ungrounded_text(dumped, known_words):
        raise _RejectedOutput("content unrelated to the supplied data")
    return output


def _invalid_fields(error: Exception, output_model: type[BaseModel]) -> str:
    """Name failed fields, using only names from the model so no output text is repeated."""
    if not isinstance(error, ValidationError):
        return "too deeply nested"
    found = {str(item["loc"][0]) for item in error.errors() if item["loc"]}
    return ", ".join(sorted(found & set(output_model.model_fields))) or "unexpected fields"


def _cited_ids(value: Any) -> set[str]:
    if isinstance(value, dict):
        cited = set(value.get("source_ids") or ())
        return cited.union(*(_cited_ids(item) for item in value.values()))
    if isinstance(value, (list, tuple)):
        return set().union(*(_cited_ids(item) for item in value))
    return set()


def _prepare_documents(
    documents: Sequence[UntrustedDocument],
) -> tuple[list[UntrustedDocument], tuple[str, ...], tuple[str, ...]]:
    """Return documents to send, ids withheld as unsafe, and ids over the size budget."""
    sent: list[UntrustedDocument] = []
    withheld: list[str] = []
    omitted: list[str] = []
    budget = MAX_CONTEXT_CHARS
    for document in documents:
        text = clean_text(document.text)[:MAX_DOCUMENT_CHARS]
        title = clean_text(document.title)[:200]
        if find_injection(f"{title}\n{text}"):
            logger.warning("Document withheld from LLM: id=%s", document.id)
            withheld.append(document.id)
        elif len(text) > budget:
            omitted.append(document.id)
        else:
            budget -= len(text)
            sent.append(
                UntrustedDocument(
                    document.id,
                    title,
                    text,
                    url=_site(document.url),
                    category=document.category,
                    internal=document.internal,
                    published=document.published,
                )
            )
    return sent, tuple(withheld), tuple(omitted)


def _site(url: str | None) -> str | None:
    """Keep only the host: the rest of a URL is attacker-controlled free text."""
    return urlsplit(url).hostname if url else None


def _build_context(boundary: str, subject: str, documents: Sequence[UntrustedDocument]) -> str:
    sections = [f"<<<{boundary} SUBJECT>>>\n{subject}\n<<<{boundary} END>>>"]
    for document in documents:
        lines = [f"<<<{boundary} DOCUMENT id={document.id}>>>", f"Title: {document.title}"]
        if document.url:
            lines.append(f"Site: {document.url}")
        if document.category:
            lines.append(f"Category: {document.category}")
        if not document.internal:
            lines.append(f"Published: {document.published or 'unknown'}")
        lines += [document.text, f"<<<{boundary} END>>>"]
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def llm_from_env() -> GuardedLLM:
    """Build the configured model client with the safety layer applied."""
    allowed = os.environ.get(ALLOW_INTERNAL_ENV_VAR, "").strip().lower() == "true"
    return GuardedLLM(provider_from_env(), allow_internal_data=allowed)


__all__ = [
    "AnalysisResult",
    "GuardedLLM",
    "LLMError",
    "UnsafeInputError",
    "UntrustedDocument",
    "clean_text",
    "find_injection",
    "find_output_violation",
    "find_ungrounded_text",
    "llm_from_env",
    "validate_subject",
]
