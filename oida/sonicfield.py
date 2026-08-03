"""Bridge into a configured Sonic Field knowledge base.

Read-only. Lets a listening result reach outward: after oida produces an
analysis, `explore()` searches the wiki (lexicon), topics, journal, paths,
research, notes, labs, and the large library/archive for related concepts,
theories, and resources.

Index strategy: the core surfaces (~1k documents) are indexed with frontmatter
plus a body excerpt; the archive/library (~90k files) is matched by its
concept-slug filenames first, reading frontmatter only for the top candidates.
Query terms are normalized through config/taxonomy/topic-aliases.json.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)

CORE_SURFACES = {
    "topics": ("content/topics", "/topics"),
    "journal": ("content/journal", "/journal"),
    "paths": ("content/paths", "/paths"),
    "research": ("content/research", "/research"),
    "notes": ("content/notes", "/notes"),
    "labs": ("content/labs", "/labs"),
}
WIKI_JSON = "data/runtime/wiki-pages.json"
ARCHIVE_DIR = "content/archive"
TAXONOMY_ALIASES = "config/taxonomy/topic-aliases.json"

BODY_EXCERPT_CHARS = 2400
MAX_ARCHIVE_CANDIDATES = 48

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "has", "have", "in",
    "into", "is", "it", "its", "like", "most", "near", "no", "not", "of", "on", "or", "over",
    "read", "s", "signal", "sound", "sounds", "that", "the", "this", "to", "was", "with",
    "approx", "audio", "event", "events", "features", "listening", "measured", "only",
    "across", "one", "possible", "resembles", "also", "fits", "reading", "steady",
}


@dataclass
class SonicFieldEntry:
    surface: str
    slug: str
    title: str
    path: str
    route: str
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    summary: str = ""
    body: str = ""
    kind: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "slug": self.slug,
            "title": self.title,
            "path": self.path,
            "route": self.route,
            "tags": self.tags[:8],
            "summary": self.summary[:280],
            "kind": self.kind,
        }


class SonicFieldBridge:
    def __init__(self, root: Path | None) -> None:
        self.root = Path(root).expanduser().resolve() if root else None
        self._lock = threading.Lock()
        self._entries: list[SonicFieldEntry] | None = None
        self._archive_slugs: list[tuple[str, Path]] | None = None
        self._aliases: dict[str, str] = {}
        self._build_ms: int | None = None
        self._error: str | None = None

    # ---------------------------------------------------------------- status

    @property
    def available(self) -> bool:
        return bool(self.root and self.root.exists())

    def status(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        if self._entries is not None:
            for entry in self._entries:
                counts[entry.surface] = counts.get(entry.surface, 0) + 1
        if self._archive_slugs is not None:
            counts["library"] = len(self._archive_slugs)
        return {
            "available": self.available,
            "root": str(self.root) if self.root else None,
            "indexed": self._entries is not None,
            "counts": counts,
            "build_ms": self._build_ms,
            "error": self._error,
        }

    # ----------------------------------------------------------------- index

    def ensure_index(self) -> None:
        if not self.available:
            raise ValueError("Sonic Field root is not available; set OIDA_SONICFIELD_ROOT")
        with self._lock:
            if self._entries is not None:
                return
            started = time.perf_counter()
            try:
                self._aliases = self._load_aliases()
                entries: list[SonicFieldEntry] = []
                entries.extend(self._index_wiki())
                for surface, (relative, route) in CORE_SURFACES.items():
                    entries.extend(self._index_mdx_dir(surface, relative, route))
                self._entries = entries
                self._archive_slugs = self._index_archive_slugs()
                self._error = None
            except Exception:
                LOGGER.exception("Sonic Field index build failed")
                self._entries = []
                self._archive_slugs = []
                self._error = "Sonic Field index unavailable"
            self._build_ms = round((time.perf_counter() - started) * 1000)

    def _load_aliases(self) -> dict[str, str]:
        aliases: dict[str, str] = {}
        path = self.root / TAXONOMY_ALIASES if self.root else None
        if not path or not path.exists():
            return aliases
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return aliases
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, str):
                    aliases[_norm_term(key)] = _norm_term(value)
                elif isinstance(value, list):
                    canonical = _norm_term(key)
                    for item in value:
                        if isinstance(item, str):
                            aliases[_norm_term(item)] = canonical
        return aliases

    def _index_wiki(self) -> list[SonicFieldEntry]:
        entries: list[SonicFieldEntry] = []
        path = self.root / WIKI_JSON
        if not path.exists():
            return entries
        try:
            pages = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return entries
        if not isinstance(pages, list):
            return entries
        for page in pages:
            if not isinstance(page, dict):
                continue
            meta = page.get("meta") if isinstance(page.get("meta"), dict) else {}
            slug = str(meta.get("slug") or "").strip()
            title = str(meta.get("title") or slug or "Untitled").strip()
            if not slug and not title:
                continue
            content = str(page.get("content") or "")
            entries.append(
                SonicFieldEntry(
                    surface="wiki",
                    slug=slug,
                    title=title,
                    path=str(path),
                    route=f"/wiki/{slug}" if slug else "/wiki",
                    tags=[str(tag) for tag in meta.get("tags", []) if isinstance(tag, str)],
                    summary=content[:280].replace("\n", " ").strip(),
                    body=content[:BODY_EXCERPT_CHARS].lower(),
                    kind=str(meta.get("type") or "concept"),
                )
            )
        return entries

    def _index_mdx_dir(self, surface: str, relative: str, route_prefix: str) -> list[SonicFieldEntry]:
        entries: list[SonicFieldEntry] = []
        directory = self.root / relative
        if not directory.exists():
            return entries
        for file_path in sorted(directory.glob("*.mdx")) + sorted(directory.glob("*.md")):
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            front, body = _split_frontmatter(text)
            slug = str(front.get("slug") or file_path.stem)
            entries.append(
                SonicFieldEntry(
                    surface=surface,
                    slug=slug,
                    title=str(front.get("title") or _title_from_slug(slug)),
                    path=str(file_path),
                    route=f"{route_prefix}/{slug}",
                    tags=_string_list(front.get("tags")) + _string_list(front.get("topics")) + _string_list(front.get("categories")),
                    aliases=_string_list(front.get("aliases")) + _string_list(front.get("relatedTopics")),
                    summary=str(front.get("summary") or "").strip(),
                    body=body[:BODY_EXCERPT_CHARS].lower(),
                    kind=str(front.get("type") or surface),
                )
            )
        return entries

    def _index_archive_slugs(self) -> list[tuple[str, Path]]:
        directory = self.root / ARCHIVE_DIR
        if not directory.exists():
            return []
        slugs: list[tuple[str, Path]] = []
        for file_path in directory.iterdir():
            if file_path.suffix in {".mdx", ".md"}:
                slugs.append((file_path.stem.lower(), file_path))
        return slugs

    # ---------------------------------------------------------------- search

    def explore(self, terms: list[str], *, limit_per_surface: int = 5) -> dict[str, Any]:
        self.ensure_index()
        normalized = self._normalize_terms(terms)
        if not normalized:
            return {"query_terms": [], "groups": {}, "total": 0}
        scored: dict[str, list[tuple[float, SonicFieldEntry, list[str]]]] = {}
        for entry in self._entries or []:
            score, matched = self._score_entry(entry, normalized)
            if score <= 0:
                continue
            scored.setdefault(entry.surface, []).append((score, entry, matched))
        for score, entry, matched in self._score_archive(normalized):
            scored.setdefault("library", []).append((score, entry, matched))

        groups: dict[str, list[dict[str, Any]]] = {}
        total = 0
        for surface, matches in scored.items():
            matches.sort(key=lambda item: item[0], reverse=True)
            rows = []
            for score, entry, matched in matches[:limit_per_surface]:
                row = entry.public()
                row["score"] = round(score, 2)
                row["matched_terms"] = matched[:6]
                row["excerpt"] = _excerpt(entry, matched)
                rows.append(row)
            if rows:
                groups[surface] = rows
                total += len(rows)
        ordered = {key: groups[key] for key in ("wiki", "topics", "journal", "library", "paths", "research", "notes", "labs") if key in groups}
        return {"query_terms": normalized, "groups": ordered, "total": total}

    def _normalize_terms(self, terms: list[str]) -> list[str]:
        seen: list[str] = []
        for term in terms:
            for token in _tokenize(str(term)):
                canonical = self._aliases.get(token, token)
                if canonical and canonical not in seen and canonical not in STOPWORDS and len(canonical) > 2:
                    seen.append(canonical)
        return seen[:24]

    def _score_entry(self, entry: SonicFieldEntry, terms: list[str]) -> tuple[float, list[str]]:
        score = 0.0
        matched: list[str] = []
        title = entry.title.lower()
        slug = entry.slug.lower().replace("-", " ")
        tags = [self._aliases.get(_norm_term(tag), _norm_term(tag)) for tag in entry.tags]
        aliases = [_norm_term(alias) for alias in entry.aliases]
        summary = entry.summary.lower()
        for term in terms:
            spaced = term.replace("-", " ")
            hit = 0.0
            if spaced and spaced in title:
                hit = max(hit, 3.0)
            if spaced and spaced in slug:
                hit = max(hit, 2.4)
            if term in tags:
                hit = max(hit, 2.2)
            if term in aliases or spaced in " ".join(aliases):
                hit = max(hit, 2.0)
            if spaced and spaced in summary:
                hit = max(hit, 1.2)
            if spaced and spaced in entry.body:
                hit = max(hit, 0.6)
            if hit:
                score += hit
                matched.append(term)
        if len(matched) > 1:
            score *= 1.0 + 0.15 * (len(matched) - 1)
        return score, matched

    def _score_archive(self, terms: list[str]) -> list[tuple[float, SonicFieldEntry, list[str]]]:
        if not self._archive_slugs:
            return []
        candidates: list[tuple[float, str, Path, list[str]]] = []
        for slug, file_path in self._archive_slugs:
            spaced = slug.replace("-", " ")
            matched = [term for term in terms if term.replace("-", " ") in spaced]
            if not matched:
                continue
            score = 2.0 * len(matched) + (1.0 if spaced in {term.replace("-", " ") for term in terms} else 0.0)
            candidates.append((score, slug, file_path, matched))
        candidates.sort(key=lambda item: item[0], reverse=True)
        results: list[tuple[float, SonicFieldEntry, list[str]]] = []
        for score, slug, file_path, matched in candidates[:MAX_ARCHIVE_CANDIDATES]:
            entry = self._read_archive_entry(slug, file_path)
            extra, matched_full = self._score_entry(entry, terms)
            results.append((score + extra * 0.5, entry, matched_full or matched))
        return results

    def _read_archive_entry(self, slug: str, file_path: Path) -> SonicFieldEntry:
        front: dict[str, Any] = {}
        try:
            head = file_path.read_text(encoding="utf-8", errors="ignore")[:4000]
            front, _body = _split_frontmatter(head)
        except Exception:
            pass
        return SonicFieldEntry(
            surface="library",
            slug=slug,
            title=str(front.get("title") or _title_from_slug(slug)),
            path=str(file_path),
            route=f"/library/{slug}",
            tags=_string_list(front.get("tags")),
            summary=str(front.get("summary") or "").strip(),
            body="",
            kind=str(front.get("type") or "archive"),
        )


# ------------------------------------------------------------------ helpers


def terms_from_event(event: dict[str, Any] | None, extra_query: str | None = None) -> list[str]:
    """Pull searchable concept terms out of a listening event."""
    terms: list[str] = []
    if extra_query:
        terms.append(extra_query)
    if isinstance(event, dict):
        aggregate = event.get("aggregate") if isinstance(event.get("aggregate"), dict) else {}
        for key in ("primary_tags",):
            for tag in aggregate.get(key, []) if isinstance(aggregate.get(key), list) else []:
                terms.append(str(tag))
        for tag in event.get("tags", []) if isinstance(event.get("tags"), list) else []:
            terms.append(str(tag))
        title = aggregate.get("title")
        if isinstance(title, str):
            terms.append(title)
        summary = aggregate.get("short_summary")
        if isinstance(summary, str):
            terms.append(summary)
        hypotheses = aggregate.get("hypotheses") if isinstance(aggregate.get("hypotheses"), list) else []
        for hypothesis in hypotheses[:4]:
            if isinstance(hypothesis, dict) and hypothesis.get("statement"):
                terms.append(str(hypothesis["statement"]))
    return terms


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9-]{1,}", text.lower())
    return [token.strip("-") for token in tokens if token.strip("-")]


def _norm_term(value: str) -> str:
    return re.sub(r"\s+", "-", str(value).strip().lower())


def _title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").strip().title()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if isinstance(item, (str, int, float)) and str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _excerpt(entry: SonicFieldEntry, matched: list[str]) -> str:
    if entry.summary:
        return entry.summary[:220]
    body = entry.body
    for term in matched:
        spaced = term.replace("-", " ")
        index = body.find(spaced)
        if index >= 0:
            start = max(0, index - 90)
            end = min(len(body), index + 130)
            return ("…" if start > 0 else "") + body[start:end].replace("\n", " ").strip() + ("…" if end < len(body) else "")
    return body[:200].replace("\n", " ").strip()


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    body = text[match.end():]
    raw = match.group(1)
    try:
        import yaml  # transformers dependency chain ships pyyaml

        parsed = yaml.safe_load(raw)
        if isinstance(parsed, dict):
            return parsed, body
    except Exception:
        pass
    return _mini_yaml(raw), body


def _mini_yaml(raw: str) -> dict[str, Any]:
    """Tolerant fallback for simple `key: value` / inline and dash lists."""
    data: dict[str, Any] = {}
    current_key: str | None = None
    for line in raw.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        dash = re.match(r"\s+-\s*(.+)$", line)
        if dash and current_key:
            data.setdefault(current_key, [])
            if isinstance(data[current_key], list):
                data[current_key].append(dash.group(1).strip().strip("'\""))
            continue
        pair = re.match(r"([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not pair:
            continue
        key, value = pair.group(1), pair.group(2).strip()
        current_key = key
        if not value:
            data[key] = []
        elif value.startswith("[") and value.endswith("]"):
            data[key] = [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
        else:
            data[key] = value.strip("'\"")
    return data
