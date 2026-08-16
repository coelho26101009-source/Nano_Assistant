"""
H.E.L.I.O.S. Plugin: Web Vision & Automation
Playwright headless — navega, extrai, pesquisa, interage.

O browser é tratado como uma ferramenta de internet pública: URLs locais,
private/link-local, file:// e outros esquemas não são aceites.
"""

import asyncio
import base64
import ipaddress
import json
import logging
import socket
from urllib.parse import parse_qs, quote_plus, urlparse

logger = logging.getLogger("helios.plugins.web_vision")

_browser = None
_context = None
_page = None
_playwright = None


async def _get_page(headless: bool = True):
    global _browser, _context, _page, _playwright
    if _browser is None or not _browser.is_connected():
        from playwright.async_api import async_playwright
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--disable-infobars", "--window-size=1440,900"],
        )
        _context = await _browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pt-PT",
        )
        await _context.route(
            "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,otf}",
            lambda r: r.abort() if _is_tracker(r.request.url) else r.continue_(),
        )
        _page = await _context.new_page()
    return _page


def _is_tracker(url: str) -> bool:
    return any(b in url for b in ["google-analytics", "doubleclick", "facebook.net", "hotjar", "clarity.ms", "googletagmanager"])


def _is_public_host(host: str) -> bool:
    if not host:
        return False
    if host.lower() in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return False
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except socket.gaierror:
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


async def _validate_url(url: str) -> tuple[bool, str]:
    try:
        parsed = urlparse(str(url).strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return False, "Apenas URLs HTTP/HTTPS públicas são permitidas."
        if parsed.username or parsed.password:
            return False, "URLs com credenciais embutidas não são permitidas."
        if not _is_public_host(parsed.hostname):
            return False, "O destino não é um endereço público permitido."
        return True, ""
    except Exception:
        return False, "URL inválida."


async def _safe_goto(page, url: str, timeout: int = 20_000):
    ok, error = await _validate_url(url)
    if not ok:
        return {"error": error}
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    return None


async def close_browser():
    global _browser, _context, _page, _playwright
    for obj in [_page, _context, _browser]:
        if obj:
            try:
                await obj.close()
            except Exception:
                pass
    if _playwright:
        try:
            await _playwright.stop()
        except Exception:
            pass
    _browser = _context = _page = _playwright = None


async def navigate_and_extract(url: str, extract_mode: str = "text") -> dict:
    try:
        page = await _get_page()
        error = await _safe_goto(page, url)
        if error:
            return error
        await page.wait_for_timeout(1500)
        result = {"url": page.url, "title": await page.title()}
        if extract_mode == "text":
            result["content"] = await page.evaluate("""() => {
                ['script','style','nav','footer','header','aside','[class*="cookie"]','[class*="popup"]','[class*="banner"]']
                .forEach(s => document.querySelectorAll(s).forEach(e => e.remove()));
                return document.body.innerText.replace(/\\n{3,}/g,'\\n\\n').trim().slice(0,12000);
            }""")
        elif extract_mode == "markdown":
            result["content"] = await page.evaluate("""() => {
                function toMd(n) {
                    if (n.nodeType===3) return n.textContent;
                    const t = n.tagName?.toLowerCase();
                    const c = () => Array.from(n.childNodes).map(toMd).join('');
                    if (!t) return c();
                    if (['script','style','nav','footer','aside'].includes(t)) return '';
                    if (/^h[1-6]$/.test(t)) return '\\n'+'#'.repeat(+t[1])+' '+n.innerText?.trim()+'\\n';
                    if (t==='p') return '\\n'+n.innerText?.trim()+'\\n';
                    if (t==='a') return `[${n.innerText?.trim()}](${n.href})`;
                    if (t==='li') return '\\n- '+n.innerText?.trim();
                    if (t==='strong'||t==='b') return `**${c()}**`;
                    if (t==='code') return '`'+c()+'`';
                    return c();
                }
                return toMd(document.body).replace(/\\n{3,}/g,'\\n\\n').trim().slice(0,10000);
            }""")
        elif extract_mode == "screenshot":
            img = await page.screenshot(full_page=False, type="jpeg", quality=80)
            result["screenshot_b64"] = base64.b64encode(img).decode()
        return result
    except Exception as exc:
        return {"error": f"Erro ao navegar para {url}: {exc}"}


async def extract_prices(url: str) -> dict:
    try:
        page = await _get_page()
        error = await _safe_goto(page, url)
        if error:
            return error
        await page.wait_for_timeout(2000)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        await page.wait_for_timeout(800)
        products = await page.evaluate("""() => {
            const re = /[€$£]?\\s*\\d+[.,]\\d{2}\\s*[€$£]?/g;
            const out = [];
            document.querySelectorAll('[itemtype*="Product"],[itemtype*="Offer"]').forEach(el => {
                const name = el.querySelector('[itemprop="name"]')?.textContent?.trim();
                const price = el.querySelector('[itemprop="price"]')?.content || el.querySelector('[itemprop="price"]')?.textContent?.trim();
                if (name && price) out.push({name, price, source:'schema'});
            });
            if (!out.length) document.querySelectorAll('article,.product,[class*="product"],[class*="item"],li').forEach(b => {
                const txt = b.innerText;
                const prices = txt.match(re);
                if (prices) {
                    const name = txt.split('\\n').find(l=>l.trim().length>3&&!l.match(re))?.trim();
                    if (name && name.length<200) out.push({name, price:prices[0], source:'heuristic'});
                }
            });
            return out.slice(0,30);
        }""")
        return {"url": page.url, "title": await page.title(), "products": products, "count": len(products)}
    except Exception as exc:
        return {"error": f"Não consegui extrair preços: {exc}"}


async def search_web(query: str, engine: str = "duckduckgo") -> dict:
    encoded = quote_plus(str(query).strip())
    urls = {
        "duckduckgo": f"https://html.duckduckgo.com/html/?q={encoded}",
        "google": f"https://www.google.com/search?q={encoded}",
        "bing": f"https://www.bing.com/search?q={encoded}",
    }
    try:
        page = await _get_page()
        error = await _safe_goto(page, urls.get(engine, urls["duckduckgo"]), timeout=15_000)
        if error:
            return error
        results = await page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('.result__body').forEach(r => {
                const title = r.querySelector('.result__title')?.innerText?.trim();
                const url = r.querySelector('.result__url')?.innerText?.trim();
                const snip = r.querySelector('.result__snippet')?.innerText?.trim();
                if (title) out.push({title, url, snippet: snip});
            });
            if (!out.length) document.querySelectorAll('#search .g').forEach(r => {
                const title = r.querySelector('h3')?.innerText?.trim();
                const url = r.querySelector('a')?.href;
                const snip = r.querySelector('.VwiC3b')?.innerText?.trim();
                if (title) out.push({title, url, snippet: snip});
            });
            return out.slice(0,8);
        }""")
        return {"query": query, "results": results}
    except Exception as exc:
        return {"error": f"Pesquisa falhou: {exc}"}


async def click_and_interact(action: str, selector: str | None = None, text: str | None = None, value: str | None = None) -> dict:
    try:
        page = await _get_page()
        if action == "click":
            if selector:
                await page.click(selector, timeout=8_000)
            elif text:
                await page.get_by_text(text, exact=False).first.click(timeout=8_000)
            else:
                return {"error": "click requer selector ou text"}
        elif action == "type" and selector is not None and value is not None:
            await page.fill(selector, value)
        elif action == "scroll":
            amount = max(-5000, min(5000, int(value or 500)))
            await page.evaluate("amount => window.scrollBy(0, amount)", amount)
        elif action == "wait":
            await page.wait_for_timeout(min(max(int(value or 2000), 0), 10_000))
        elif action == "screenshot":
            img = await page.screenshot(type="jpeg", quality=75)
            return {"screenshot_b64": base64.b64encode(img).decode(), "url": page.url}
        else:
            return {"error": "Ação inválida ou argumentos em falta."}
        return {"success": True, "url": page.url, "title": await page.title()}
    except Exception as exc:
        return {"error": f"Interação falhou: {exc}"}


async def take_screenshot(full_page: bool = False) -> dict:
    try:
        page = await _get_page()
        img = await page.screenshot(full_page=bool(full_page), type="jpeg", quality=80)
        return {"screenshot_b64": base64.b64encode(img).decode(), "url": page.url, "title": await page.title()}
    except Exception as exc:
        return {"error": str(exc)}


def get_tools() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": "web_navigate_extract",
            "description": "Navega para uma URL pública e extrai o conteúdo (texto, markdown ou screenshot).",
            "parameters": {"type": "object", "required": ["url"], "properties": {
                "url": {"type": "string", "description": "URL HTTP/HTTPS pública"},
                "extract_mode": {"type": "string", "enum": ["text", "markdown", "screenshot"]},
            }},
        }},
        {"type": "function", "function": {
            "name": "web_extract_prices",
            "description": "Extrai preços e produtos de páginas públicas.",
            "parameters": {"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}},
        }},
        {"type": "function", "function": {
            "name": "web_search",
            "description": "Pesquisa na web e devolve resultados com títulos, URLs e snippets.",
            "parameters": {"type": "object", "required": ["query"], "properties": {
                "query": {"type": "string"},
                "engine": {"type": "string", "enum": ["duckduckgo", "google", "bing"]},
            }},
        }},
        {"type": "function", "function": {
            "name": "web_interact",
            "description": "Interage com a página actual: clica, preenche campos, faz scroll.",
            "parameters": {"type": "object", "required": ["action"], "properties": {
                "action": {"type": "string", "enum": ["click", "type", "scroll", "wait", "screenshot"]},
                "selector": {"type": "string"},
                "text": {"type": "string"},
                "value": {"type": "string"},
            }},
        }},
        {"type": "function", "function": {
            "name": "web_screenshot",
            "description": "Tira screenshot do browser actual.",
            "parameters": {"type": "object", "properties": {"full_page": {"type": "boolean"}}},
        }},
    ]


TOOL_HANDLERS: dict = {
    "web_navigate_extract": lambda a: navigate_and_extract(**a),
    "web_extract_prices": lambda a: extract_prices(**a),
    "web_search": lambda a: search_web(**a),
    "web_interact": lambda a: click_and_interact(**a),
    "web_screenshot": lambda a: take_screenshot(**a),
}
