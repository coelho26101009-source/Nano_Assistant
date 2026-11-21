"""Deep research: several searches, several sites, one report with its sources.

"Investiga a fundo se vale a pena trocar a GTX 1660 Ti por uma RTX 4060." A
single search answers that badly: one page of snippets, one opinion. This
module does what a person would do with twenty minutes and a browser:

1. ASK SEVERAL WAYS. The question itself plus up to three angles the model
   proposes ("rtx 4060 vs gtx 1660 ti benchmark", "rtx 4060 preço portugal").
2. ASK SEVERAL PLACES. Every angle goes to DuckDuckGo (Bing when DuckDuckGo
   gives nothing) and the question also goes to Wikipedia. A page found by two
   different searches counts as better evidence than a page found once.
3. READ THE PAGES, not only the snippets: up to eight sites, at most two per
   domain, so one forum cannot be the whole report.
4. KEEP WHAT IS RELEVANT. Each page is reduced to the paragraphs that mention
   the question's words, never more than ~1 400 characters a site, so the
   whole evidence fits in one model turn even on Groq's free tier.
5. CROSS-CHECK. Figures that appear in more than one site ("8 GB", "2023")
   are listed with the sources that agree on them; the model is told which
   points are confirmed and which rest on one source only.

The model then writes the report from that evidence and cites ``[n]`` after
each fact. The list of sources under the report is NOT the model's: NANO adds
it (core/brain.py, ``_research_sources_note``) from what was really read, so a
link in the report can never be invented.

SAFETY. Everything fetched here is external content. The executor marks the
tool's output untrusted (capability ``browser.read``) and the brain fences it,
so a page that says "ignore your instructions" is data in a report, not an
instruction. Only public http(s) addresses are fetched: every address and
every redirect hop goes through ``validate_public_http_url``, which refuses
loopback, private, link-local and reserved destinations, so a search result
cannot be used to reach the router or a service on this PC. Nothing is sent
anywhere except the search queries themselves; no cookies, no login, nothing
is clicked or submitted. Pages are read up to 1.5 MB, html or plain text only.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote, quote_plus, urljoin, urlparse, urlunparse

import httpx

from core.browser_agent import validate_public_http_url
from core.i18n import tr

logger = logging.getLogger("nano.research")

MAX_QUESTION_CHARS = 300
MAX_QUERY_CHARS = 200
#: The question plus three angles. More searches make the report slower and
#: the search engines less friendly, for little extra evidence.
MAX_QUERIES = 4
MAX_SOURCES = 8
MAX_PER_DOMAIN = 2
RESULTS_PER_SEARCH = 8
MAX_PAGE_BYTES = 1_500_000
MAX_REDIRECTS = 4
EXCERPT_CHARS = 1400
PARAGRAPH_CHARS = 520
SEARCH_TIMEOUT = 10.0
FETCH_TIMEOUT = 10.0
#: The whole research has to finish inside the executor's 60 s plugin limit,
#: with room for the report. Whatever was read by then is used.
TOTAL_BUDGET = 40.0
FETCH_CONCURRENCY = 5

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

#: Written by NANO, never by a page: the brain sends it to the model OUTSIDE
#: the untrusted fence, next to the evidence (core/brain.py).
REPORT_INSTRUCTIONS = (
    "Escreve agora o relatório para o utilizador, na língua dele, usando SÓ as fontes da "
    "investigação. Formato: um título curto (##); '**Resumo**' com 2 a 4 frases que respondem "
    "à pergunta; '**O que as fontes dizem**' em pontos, cada facto seguido da fonte entre "
    "parênteses retos, por exemplo [1] ou [2][4]; '**Onde não concordam**' (ou diz que "
    "concordam); '**Confiança**': alta quando sítios independentes confirmam o mesmo, baixa "
    "quando só uma fonte o diz. Usa os números confirmados por várias fontes. Não inventes "
    "factos, números nem fontes; se as fontes não respondem a alguma parte, diz isso. Não "
    "escrevas a lista de fontes nem ligações no fim: o NANO junta-a sozinho."
)

_STOPWORDS = frozenset("""
a o as os um uma uns umas de do da dos das em no na nos nas por para com sem sobre e ou que
se ao aos qual quais como mais menos muito muita ja nao sim eu tu ele ela nos vos eles elas
me te lhe meu minha teu tua seu sua isto isso aquilo este esta esse essa ha foi ser sao era
vale pena investiga investigar pesquisa pesquisar procura procurar fundo relatorio fontes
diz dizer quero queria sabe saber melhor qual
the an of in on at to for from with without about and or what which how why who is are was
were be been do does did i you he she it we they my your this that these those there here
research investigate report sources deep depth please find out tell me
""".split())

_NUMBER_FACT = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s?"
    r"(%|gb|mb|tb|ghz|mhz|hz|fps|w|kw|kwh|mah|v|mm|cm|km|kg|g|ml|l|€|eur|euros|\$|usd|"
    r"anos|years|horas|hours|minutos|minutes|milhões|milhoes|million|millions|billion|mil)?(?!\w)",
    re.IGNORECASE)


# ---------------------------------------------------------------- data shapes

@dataclass
class Hit:
    url: str
    title: str
    snippet: str = ""
    engine: str = ""
    query: str = ""
    rank: int = 0


@dataclass
class Source:
    url: str
    title: str
    domain: str
    snippet: str = ""
    found_by: set[str] = field(default_factory=set)
    engines: set[str] = field(default_factory=set)
    best_rank: int = 99
    excerpt: str = ""
    read: bool = False
    n: int = 0

    def as_dict(self) -> dict:
        return {"n": self.n, "title": self.title, "url": self.url, "domain": self.domain,
                "read": self.read, "found_by": len(self.found_by),
                "engines": sorted(self.engines)}


# ---------------------------------------------------------------- text helpers

def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", str(text or "").lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit].strip()


def terms_of(*texts: str) -> list[str]:
    """The words that make a paragraph relevant: no stop words, no accents."""
    seen: list[str] = []
    for text in texts:
        for word in re.findall(r"\w+", _fold(text)):
            if (len(word) >= 3 or word.isdigit()) and word not in _STOPWORDS and word not in seen:
                seen.append(word)
    return seen[:24]


def plan_queries(question: str, angles: Any = None) -> list[str]:
    """The question first, then up to three distinct angles from the model."""
    queries = [_clean(question, MAX_QUERY_CHARS)]
    if isinstance(angles, str):
        angles = [angles]
    for angle in angles if isinstance(angles, (list, tuple)) else ():
        text = _clean(angle, MAX_QUERY_CHARS)
        if text and _fold(text) not in {_fold(q) for q in queries}:
            queries.append(text)
        if len(queries) >= MAX_QUERIES:
            break
    return [q for q in queries if q]


_TRACKING = ("utm_", "fbclid", "gclid", "msclkid", "ref=")


def _without_tracking(query: str) -> str:
    return "&".join(part for part in query.split("&")
                    if part and not part.lower().startswith(_TRACKING))


def clean_url(url: str) -> str:
    """The address to read and to show: no fragment, no tracking parameters."""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query=_without_tracking(parsed.query), fragment=""))


def canonical_url(url: str) -> str:
    """One key per page: also no "www." and no trailing slash."""
    parsed = urlparse(url)
    query = _without_tracking(parsed.query)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(("https", host, path, "", query, ""))


def domain_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def safe_link(url: str) -> str:
    """The address as a markdown link target: no spaces, no parentheses."""
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def safe_title(title: str, fallback: str) -> str:
    """A page title as link text: no markdown that could break the list."""
    text = re.sub(r"[\[\]()*_`#>|<]", " ", str(title or ""))
    text = _clean(text, 90)
    return text or fallback


# ---------------------------------------------------------------- html parsing

# Not "form": some sites wrap the whole page in one.
_SKIP_TAGS = frozenset({"script", "style", "noscript", "nav", "footer", "header", "aside",
                        "svg", "template", "iframe", "button", "select", "canvas"})
_BLOCK_TAGS = frozenset({"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "div", "article",
                         "section", "br", "tr", "td", "th", "blockquote", "pre", "dd", "dt",
                         "figcaption", "main", "table", "ul", "ol"})
_VOID_TAGS = frozenset({"br", "img", "hr", "meta", "link", "input", "source", "wbr"})



class _Collector(HTMLParser):
    """Tag-aware text collection shared by the parsers below."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)

    @staticmethod
    def classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        for key, value in attrs:
            if key == "class" and value:
                return set(value.split())
        return set()

    @staticmethod
    def attr(attrs: list[tuple[str, str | None]], name: str) -> str:
        for key, value in attrs:
            if key == name and value:
                return value
        return ""


def _ddg_target(href: str) -> str:
    """DuckDuckGo wraps results in //duckduckgo.com/l/?uddg=<the real address>."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if (parsed.hostname or "").endswith("duckduckgo.com"):
        if parsed.path.startswith("/y.js"):          # an advert
            return ""
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return target
    return href


class _DuckDuckGoParser(_Collector):
    def __init__(self) -> None:
        super().__init__()
        self.hits: list[Hit] = []
        self._field: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag, attrs):
        classes = self.classes(attrs)
        # Adverts link through duckduckgo.com/y.js, which _ddg_target drops.
        if tag == "a" and "result__a" in classes:
            url = _ddg_target(unescape(self.attr(attrs, "href")))
            if url:
                self.hits.append(Hit(url=url, title=""))
                self._field, self._buffer = "title", []
        elif "result__snippet" in classes and self.hits:
            self._field, self._buffer = "snippet", []

    def handle_endtag(self, tag):
        if self._field and tag in {"a", "td", "div"}:
            text = _clean("".join(self._buffer), 400)
            if self._field == "title":
                self.hits[-1].title = text
            else:
                self.hits[-1].snippet = text
            self._field = None

    def handle_data(self, data):
        if self._field:
            self._buffer.append(data)


def _bing_target(href: str) -> str:
    """Bing may wrap results in bing.com/ck/a?...&u=a1<base64 of the address>."""
    parsed = urlparse(href)
    if (parsed.hostname or "").endswith("bing.com") and parsed.path.startswith("/ck/"):
        encoded = parse_qs(parsed.query).get("u", [""])[0]
        if encoded.startswith("a1"):
            encoded = encoded[2:]
            try:
                return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
            except Exception:
                return ""
        return ""
    return href


class _BingParser(_Collector):
    def __init__(self) -> None:
        super().__init__()
        self.hits: list[Hit] = []
        self._in_result = 0
        self._depth = 0
        self._field: str | None = None
        self._buffer: list[str] = []
        self._in_h2 = False

    def handle_starttag(self, tag, attrs):
        if tag in _VOID_TAGS:                       # no end tag: would skew the depth
            return
        self._depth += 1
        if tag == "li" and "b_algo" in self.classes(attrs):
            self._in_result = self._depth
            self.hits.append(Hit(url="", title=""))
            return
        if not self._in_result:
            return
        if tag == "h2":
            self._in_h2 = True
        elif tag == "a" and self._in_h2 and not self.hits[-1].url:
            self.hits[-1].url = _bing_target(unescape(self.attr(attrs, "href")))
            self._field, self._buffer = "title", []
        elif tag == "p" and not self.hits[-1].snippet:
            self._field, self._buffer = "snippet", []

    def handle_endtag(self, tag):
        if self._field and tag in {"a", "p"}:
            text = _clean("".join(self._buffer), 400)
            if self._field == "title":
                self.hits[-1].title = text
            else:
                self.hits[-1].snippet = text
            self._field = None
        if tag == "h2":
            self._in_h2 = False
        if tag in _VOID_TAGS:
            return
        if self._in_result and tag == "li" and self._depth <= self._in_result:
            self._in_result = 0
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data):
        if self._field:
            self._buffer.append(data)


def parse_duckduckgo(html: str) -> list[Hit]:
    parser = _DuckDuckGoParser()
    parser.feed(html or "")
    return [h for h in parser.hits if h.url.startswith(("http://", "https://")) and h.title]


def parse_bing(html: str) -> list[Hit]:
    parser = _BingParser()
    parser.feed(html or "")
    return [h for h in parser.hits if h.url.startswith(("http://", "https://")) and h.title]


class _PageText(_Collector):
    """The readable text of a page, one paragraph per line, plus its title."""

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.description = ""
        self._skip = 0
        self._in_title = False
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            name = (self.attr(attrs, "name") or self.attr(attrs, "property")).lower()
            if name in {"description", "og:description"} and not self.description:
                self.description = _clean(self.attr(attrs, "content"), 400)
            return
        if tag in _VOID_TAGS:
            if tag == "br":
                self._parts.append("\n")
            return
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title = _clean(self.title + " " + data, 200)
        elif not self._skip:
            self._parts.append(data)

    def paragraphs(self) -> list[str]:
        lines = [" ".join(line.split()) for line in "".join(self._parts).split("\n")]
        return [line for line in lines if len(line) >= 40]


def page_text(html: str) -> tuple[str, list[str], str]:
    """(title, paragraphs, description) of an html page."""
    parser = _PageText()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:                                   # malformed html: keep what was read
        pass
    return parser.title, parser.paragraphs(), parser.description


def relevant_excerpt(paragraphs: list[str], terms: list[str], limit: int = EXCERPT_CHARS) -> str:
    """The paragraphs that best answer the question, in page order."""
    if not paragraphs:
        return ""
    scored = []
    for index, paragraph in enumerate(paragraphs[:400]):
        folded = _fold(paragraph)
        hits = sum(1 for term in terms if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", folded))
        if hits:
            facts = 1 if re.search(r"\d", paragraph) else 0
            scored.append((hits * 2 + facts, index))
    if not scored:
        chosen = list(range(min(3, len(paragraphs))))
    else:
        scored.sort(key=lambda item: (-item[0], item[1]))
        chosen = sorted(index for _score, index in scored[:6])
    out: list[str] = []
    used = 0
    for index in chosen:
        piece = paragraphs[index][:PARAGRAPH_CHARS]
        if used + len(piece) > limit:
            if not out:
                out.append(piece[:limit])
            break
        out.append(piece)
        used += len(piece) + 1
    return "\n".join(out)


def shared_figures(sources: list[Source], *, minimum: int = 2, limit: int = 8,
                   ignore: set[str] | frozenset[str] = frozenset()) -> list[tuple[str, list[int]]]:
    """Figures ("8 GB", "2023", "15%") that appear in more than one source.

    ``ignore`` holds the numbers of the question itself: "RTX 4060" in every
    source agrees on nothing but the name of the product.
    """
    seen: dict[str, set[int]] = {}
    shown: dict[str, str] = {}
    for source in sources:
        text = f"{source.excerpt} {source.snippet}"
        for match in _NUMBER_FACT.finditer(text):
            number, unit = match.group(1), (match.group(2) or "")
            if not unit and len(re.sub(r"\D", "", number)) < 3:
                continue                                 # "2" or "15" alone says nothing
            if not unit and number in ignore:
                continue
            key = f"{number.replace(',', '.')} {unit.lower()}".strip()
            seen.setdefault(key, set()).add(source.n)
            shown.setdefault(key, f"{number} {unit}".strip())
    agreed = [(shown[key], sorted(ns)) for key, ns in seen.items() if len(ns) >= minimum]
    agreed.sort(key=lambda item: (-len(item[1]), item[1]))
    return agreed[:limit]


# ---------------------------------------------------------------- network

def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(FETCH_TIMEOUT, connect=6.0),
        headers={"User-Agent": USER_AGENT,
                 "Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8",
                 "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8"},
        follow_redirects=False)


async def _public(url: str) -> bool:
    ok, _error = await asyncio.to_thread(validate_public_http_url, url)
    return ok


async def fetch_page(client: httpx.AsyncClient, url: str) -> tuple[str, str] | None:
    """(final address, html) of a public page, following redirects one by one."""
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        if not await _public(current):
            return None
        async with client.stream("GET", current) as response:
            if response.is_redirect:
                location = response.headers.get("location") or ""
                if not location:
                    return None
                current = urljoin(current, location)
                continue
            if response.status_code != 200:
                return None
            kind = response.headers.get("content-type", "").lower()
            if kind and "html" not in kind and "text/plain" not in kind:
                return None
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) >= MAX_PAGE_BYTES:
                    break
            encoding = response.encoding or "utf-8"
            try:
                return current, bytes(body).decode(encoding, errors="replace")
            except LookupError:
                return current, bytes(body).decode("utf-8", errors="replace")
    return None


async def _get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    return await client.get(url, timeout=SEARCH_TIMEOUT, follow_redirects=True, **kwargs)


async def search_duckduckgo(client: httpx.AsyncClient, query: str) -> list[Hit]:
    response = await _get(client, f"https://html.duckduckgo.com/html/?q={quote_plus(query)}")
    if response.status_code != 200:
        return []
    return parse_duckduckgo(response.text)[:RESULTS_PER_SEARCH]


async def search_bing(client: httpx.AsyncClient, query: str) -> list[Hit]:
    response = await _get(client, f"https://www.bing.com/search?q={quote_plus(query)}")
    if response.status_code != 200:
        return []
    return parse_bing(response.text)[:RESULTS_PER_SEARCH]


async def search_wikipedia(client: httpx.AsyncClient, query: str, language: str) -> list[Hit]:
    lang = "pt" if str(language).lower().startswith("pt") else "en"
    response = await _get(
        client, f"https://{lang}.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": query, "srlimit": "2",
                "format": "json", "utf8": "1"})
    if response.status_code != 200:
        return []
    rows = ((response.json() or {}).get("query") or {}).get("search") or []
    hits = []
    for row in rows[:2]:
        title = _clean(row.get("title"), 160)
        if not title:
            continue
        snippet = _clean(re.sub(r"<[^>]+>", "", unescape(str(row.get("snippet") or ""))), 400)
        hits.append(Hit(url=f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                        title=title, snippet=snippet))
    return hits


async def _one_search(client, engine: str, query: str, language: str) -> tuple[str, str, list[Hit], str]:
    """(engine, query, hits, error). Never raises: a failed engine is reported."""
    try:
        if engine == "duckduckgo":
            hits = await search_duckduckgo(client, query)
            if not hits:
                engine = "bing"
                hits = await search_bing(client, query)
        elif engine == "wikipedia":
            hits = await search_wikipedia(client, query, language)
        else:
            hits = await search_bing(client, query)
    except Exception as exc:                             # network, parsing, JSON
        logger.info("research search %s failed: %s", engine, type(exc).__name__)
        return engine, query, [], type(exc).__name__
    for rank, hit in enumerate(hits):
        hit.engine, hit.query, hit.rank = engine, query, rank
    return engine, query, hits, ""


def merge_hits(hits: list[Hit], *, max_sources: int = MAX_SOURCES,
               per_domain: int = MAX_PER_DOMAIN) -> list[Source]:
    """One source per page, best evidence first, no domain more than twice."""
    by_key: dict[str, Source] = {}
    for hit in hits:
        if not hit.url.startswith(("http://", "https://")):
            continue
        key = canonical_url(hit.url)
        source = by_key.get(key)
        if source is None:
            source = by_key[key] = Source(url=clean_url(hit.url), title=hit.title, domain=domain_of(hit.url),
                                          snippet=hit.snippet)
        source.found_by.add(_fold(hit.query))
        source.engines.add(hit.engine)
        source.best_rank = min(source.best_rank, hit.rank)
        if not source.snippet and hit.snippet:
            source.snippet = hit.snippet
    ranked = sorted(by_key.values(),
                    key=lambda s: (-len(s.found_by), -len(s.engines), s.best_rank, s.domain))
    chosen: list[Source] = []
    per: dict[str, int] = {}
    for source in ranked:
        if per.get(source.domain, 0) >= per_domain:
            continue
        per[source.domain] = per.get(source.domain, 0) + 1
        chosen.append(source)
        if len(chosen) >= max_sources:
            break
    return chosen


async def _read_source(client, source: Source, terms: list[str], gate: asyncio.Semaphore) -> None:
    async with gate:
        try:
            page = await asyncio.wait_for(fetch_page(client, source.url), timeout=FETCH_TIMEOUT + 2)
        except Exception as exc:
            logger.info("research could not read %s: %s", source.domain, type(exc).__name__)
            return
    if page is None:
        return
    _final, html = page
    title, paragraphs, description = page_text(html)
    excerpt = relevant_excerpt(paragraphs, terms)
    if not excerpt and description:
        excerpt = description
    if excerpt:
        source.excerpt = excerpt
        source.read = True
    if title and (not source.title or len(source.title) < 8):
        source.title = _clean(title, 160)


# ---------------------------------------------------------------- the research

async def run(question: str, angles: Any = None, *, language: str | None = None,
              client: httpx.AsyncClient | None = None,
              budget: float = TOTAL_BUDGET) -> dict:
    """Search, read and cross-check. Returns the evidence for the report."""
    question = _clean(question, MAX_QUESTION_CHARS)
    if len(question) < 3:
        return {"ok": False, "status": "question_required",
                "message": tr("Diz o que queres que eu investigue.")}
    if language is None:
        from core import i18n  # noqa: PLC0415 - settings are read at call time

        language = i18n.current()
    started = time.monotonic()
    queries = plan_queries(question, angles)
    terms = terms_of(*queries)

    own_client = client is None
    client = client or _client()
    try:
        jobs = [_one_search(client, "duckduckgo", q, language) for q in queries]
        jobs.append(_one_search(client, "wikipedia", question, language))
        try:
            searches = await asyncio.wait_for(asyncio.gather(*jobs), timeout=SEARCH_TIMEOUT * 2 + 2)
        except asyncio.TimeoutError:
            searches = []
        hits = [hit for _engine, _query, found, _error in searches for hit in found]
        failed = sorted({engine for engine, _q, found, error in searches if error and not found})
        engines_used = sorted({engine for engine, _q, found, _e in searches if found})
        sources = merge_hits(hits)
        if not sources:
            return {"ok": False, "status": "no_results", "queries": queries, "failed_engines": failed,
                    "message": tr("Não encontrei resultados na internet para isso. Verifica a "
                                  "ligação ou reformula a pergunta.")}

        remaining = max(5.0, budget - (time.monotonic() - started))
        gate = asyncio.Semaphore(FETCH_CONCURRENCY)
        reads = [asyncio.create_task(_read_source(client, s, terms, gate)) for s in sources]
        done, pending = await asyncio.wait(reads, timeout=remaining)
        for task in pending:
            task.cancel()
    finally:
        if own_client:
            await client.aclose()

    # A source nobody could read still counts by its search summary, but says so.
    usable = [s for s in sources if s.excerpt or s.snippet]
    for index, source in enumerate(usable, start=1):
        source.n = index
    figures = shared_figures(usable, ignore={term for term in terms if term.isdigit()})
    return {
        "ok": True,
        "kind": "nano_research",
        "question": question,
        "queries": queries,
        "engines": engines_used,
        "failed_engines": failed,
        "sources": [s.as_dict() for s in usable],
        "pages_read": sum(1 for s in usable if s.read),
        "agreement": [{"figure": figure, "sources": ns} for figure, ns in figures],
        "seconds": round(time.monotonic() - started, 1),
        "content": evidence_text(question, queries, usable, figures),
    }


def evidence_text(question: str, queries: list[str], sources: list[Source],
                  figures: list[tuple[str, list[int]]]) -> str:
    """The evidence as the model reads it (inside the untrusted fence)."""
    read = sum(1 for s in sources if s.read)
    lines = [f"Pergunta: {question}",
             "Pesquisas feitas: " + " | ".join(queries),
             f"Fontes: {len(sources)} ({read} lidas na página, {len(sources) - read} só pelo resumo "
             "da pesquisa)", ""]
    for source in sources:
        seen = len(source.found_by)
        where = f"encontrada em {seen} pesquisas" if seen > 1 else "encontrada numa pesquisa"
        lines.append(f"[{source.n}] {safe_title(source.title, source.domain)} — {source.domain} ({where})")
        if source.read:
            lines.append(source.excerpt)
        else:
            lines.append(f"(só o resumo da pesquisa) {source.snippet}")
        lines.append("")
    if figures:
        lines.append("Números que aparecem em mais de uma fonte: " + "; ".join(
            f"{figure} em " + ", ".join(f"[{n}]" for n in ns) for figure, ns in figures))
    else:
        lines.append("Nenhum número aparece em mais de uma fonte; compara as afirmações com cuidado.")
    return "\n".join(lines).strip()


def sources_markdown(sources: list[dict], heading: str) -> str:
    """The numbered list of sources NANO appends under the report."""
    rows = []
    for source in sources:
        url = str(source.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        domain = str(source.get("domain") or domain_of(url))
        title = safe_title(str(source.get("title") or ""), domain)
        rows.append(f"{int(source.get('n') or len(rows) + 1)}. [{title}]({safe_link(url)}) — {domain}")
    if not rows:
        return ""
    return f"**{heading}**\n" + "\n".join(rows)


__all__ = [
    "MAX_QUERIES",
    "MAX_SOURCES",
    "REPORT_INSTRUCTIONS",
    "canonical_url",
    "evidence_text",
    "fetch_page",
    "merge_hits",
    "page_text",
    "parse_bing",
    "parse_duckduckgo",
    "plan_queries",
    "relevant_excerpt",
    "run",
    "shared_figures",
    "sources_markdown",
    "terms_of",
]
