from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
import ipaddress
import re
import socket
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import time

from market_checker_app.collectors.source_diagnostics import is_retryable_transport_error

from pypdf import PdfReader

from market_checker_app.config import ShortReportSourceConfig


class ShortReportFetchError(RuntimeError):
    """Raised when a configured report cannot be fetched or safely parsed."""

    def __init__(self, message: str, *, attempts: int = 1) -> None:
        super().__init__(message)
        self.attempts = max(1, int(attempts))


@dataclass(frozen=True, slots=True)
class FetchedShortReport:
    source: ShortReportSourceConfig
    final_url: str
    mime_type: str
    title: str
    text: str
    content_hash: str
    size_bytes: int
    extractor: str
    fetch_attempts: int = 1


ShortReportTransport = Callable[
    [str, dict[str, str], float, int],
    tuple[bytes, str, str],
]


def _validate_public_https_url(url: str) -> str:
    normalized = str(url or "").strip()
    parsed = urlparse(normalized)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ShortReportFetchError("Short report URL musí být veřejná HTTPS adresa.")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
        raise ShortReportFetchError("Lokální adresy nejsou pro short reporty povolené.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ShortReportFetchError("Privátní nebo lokální IP adresy nejsou povolené.")
    if parsed.username or parsed.password:
        raise ShortReportFetchError("URL s přihlašovacími údaji není povolená.")
    return normalized


def _validate_resolved_public_host(url: str) -> str:
    normalized = _validate_public_https_url(url)
    parsed = urlparse(normalized)
    hostname = str(parsed.hostname)
    try:
        port = parsed.port or 443
        addresses = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except (OSError, ValueError) as exc:
        raise ShortReportFetchError(
            f"Doménu short reportu nelze bezpečně přeložit: {exc}"
        ) from exc
    resolved = {
        ipaddress.ip_address(str(sockaddr[0]))
        for _, _, _, _, sockaddr in addresses
        if sockaddr
    }
    if not resolved or any(not address.is_global for address in resolved):
        raise ShortReportFetchError(
            "Doména short reportu směřuje na neveřejnou IP adresu."
        )
    return normalized


class _PublicHTTPSRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_resolved_public_host(str(newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_transport(
    url: str,
    headers: dict[str, str],
    timeout_seconds: float,
    max_download_bytes: int,
) -> tuple[bytes, str, str]:
    _validate_resolved_public_host(url)
    request = Request(url, headers=headers, method="GET")
    opener = build_opener(_PublicHTTPSRedirectHandler())
    with opener.open(request, timeout=timeout_seconds) as response:
        final_url = str(response.geturl())
        mime_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0]
        payload = response.read(max_download_bytes + 1)
    return payload, mime_type, final_url


class _HTMLTextExtractor(HTMLParser):
    BLOCKED_TAGS = {"script", "style", "noscript", "svg"}
    STRUCTURAL_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dt",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "p",
        "section",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._blocked_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        if normalized in self.BLOCKED_TAGS:
            self._blocked_depth += 1
        elif normalized in self.STRUCTURAL_TAGS:
            self.text_parts.append("\n")
        elif normalized == "title" and self._blocked_depth == 0:
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in self.BLOCKED_TAGS and self._blocked_depth:
            self._blocked_depth -= 1
        elif normalized == "title":
            self._in_title = False
        elif (
            normalized in self.STRUCTURAL_TAGS
            and normalized != "br"
            and self._blocked_depth == 0
        ):
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._blocked_depth:
            return
        value = re.sub(r"\s+", " ", str(data or ""))
        if not value.strip():
            return
        self.text_parts.append(value)
        if self._in_title:
            self.title_parts.append(value.strip())


def _normalize_text(value: str, max_characters: int) -> str:
    normalized = str(value or "").replace("\r", "\n").replace("\xa0", " ")
    normalized = re.sub(r"[ \t\f\v]+", " ", normalized)
    normalized = re.sub(r" *\n+ *", "\n", normalized)
    normalized = re.sub(r"\n{2,}", "\n", normalized)
    return normalized.strip()[:max_characters]


def _extract_html(payload: bytes, max_characters: int) -> tuple[str, str]:
    decoded = payload.decode("utf-8", errors="replace")
    parser = _HTMLTextExtractor()
    parser.feed(decoded)
    title = _normalize_text(" ".join(parser.title_parts), 500)
    text = _normalize_text(" ".join(parser.text_parts), max_characters)
    return title, text


def _extract_pdf(payload: bytes, max_characters: int) -> tuple[str, str]:
    try:
        reader = PdfReader(BytesIO(payload))
        if reader.is_encrypted:
            reader.decrypt("")
        page_text: list[str] = []
        extracted_characters = 0
        for page_number, page in enumerate(reader.pages):
            if page_number >= 1_000 or extracted_characters >= max_characters:
                break
            value = str(page.extract_text() or "")
            remaining = max(0, max_characters - extracted_characters)
            page_text.append(value[:remaining])
            extracted_characters += min(len(value), remaining)
    except Exception as exc:
        raise ShortReportFetchError(f"PDF short report nelze přečíst: {exc}") from exc
    metadata_title = ""
    if reader.metadata is not None:
        metadata_title = str(reader.metadata.title or "")
    return (
        _normalize_text(metadata_title, 500),
        _normalize_text(" ".join(page_text), max_characters),
    )


class ShortReportClient:
    """Fetch one explicitly configured report without source discovery."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 20.0,
        max_download_bytes: int = 8_000_000,
        max_text_characters: int = 500_000,
        max_attempts: int = 3,
        transport: ShortReportTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.user_agent = str(user_agent or "").strip() or "JohnySkore/2.1"
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.max_download_bytes = max(1_024, int(max_download_bytes))
        self.max_text_characters = max(1_000, int(max_text_characters))
        self.max_attempts = max(1, int(max_attempts))
        self._transport = transport or _default_transport
        self._sleep = sleep

    def fetch(self, source: ShortReportSourceConfig) -> FetchedShortReport:
        url = _validate_public_https_url(source.url)
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain",
        }
        last_error: Exception | None = None
        attempts = 0
        for attempts in range(1, self.max_attempts + 1):
            try:
                payload, raw_mime_type, final_url = self._transport(
                    url,
                    headers,
                    self.timeout_seconds,
                    self.max_download_bytes,
                )
                break
            except (HTTPError, URLError, TimeoutError) as exc:
                last_error = exc
                if not is_retryable_transport_error(exc) or attempts == self.max_attempts:
                    raise ShortReportFetchError(
                        f"Short report nelze stáhnout: {type(exc).__name__}: {exc}",
                        attempts=attempts,
                    ) from exc
                self._sleep(float(2 ** (attempts - 1)))
        else:
            raise ShortReportFetchError(
                f"Short report nelze stáhnout: {last_error}",
                attempts=attempts,
            ) from last_error
        _validate_public_https_url(final_url)
        if len(payload) > self.max_download_bytes:
            raise ShortReportFetchError(
                f"Short report překročil limit {self.max_download_bytes} bajtů."
            )
        if not payload:
            raise ShortReportFetchError("Short report je prázdný.")

        mime_type = str(raw_mime_type or "").strip().lower()
        if mime_type == "application/pdf" or payload.startswith(b"%PDF"):
            title, text = _extract_pdf(payload, self.max_text_characters)
            extractor = "pypdf"
            mime_type = "application/pdf"
        elif mime_type in {"text/html", "application/xhtml+xml", ""}:
            title, text = _extract_html(payload, self.max_text_characters)
            extractor = "html.parser"
            mime_type = mime_type or "text/html"
        elif mime_type.startswith("text/plain"):
            title = ""
            text = _normalize_text(
                payload.decode("utf-8", errors="replace"),
                self.max_text_characters,
            )
            extractor = "plain_text"
            mime_type = "text/plain"
        else:
            raise ShortReportFetchError(
                f"Nepodporovaný Content-Type short reportu: {mime_type or 'unknown'}."
            )
        if not text:
            raise ShortReportFetchError(
                "Short report neobsahuje strojově čitelný text; může jít o skenované PDF."
            )
        return FetchedShortReport(
            source=source,
            final_url=final_url,
            mime_type=mime_type,
            title=title,
            text=text,
            content_hash=sha256(payload).hexdigest(),
            size_bytes=len(payload),
            extractor=extractor,
            fetch_attempts=attempts,
        )
