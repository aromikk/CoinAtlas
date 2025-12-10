"""
Высокопроизводительный асинхронный парсер закрытых аукционов wolmar.ru.

Особенности:
- асинхронная загрузка страниц (aiohttp);
- параллельная обработка множества аукционов и лотов;
- стриминговая запись результата в NDJSON-файл (по одной строке на лот);
- аккуратная обработка ошибок;
- цена лота берётся из итоговой "Ставки" на странице лота;
- для каждого лота сохраняется дата/время закрытия аукциона;
- все лоты, которые не удалось спарсить после всех попыток, пишутся
  в отдельный NDJSON и затем повторно обрабатываются несколькими раундами.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    AsyncIterator,
    Dict,
    Iterable,
    List,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
)

import aiofiles
import aiohttp
from aiohttp import ClientResponse, ClientSession, ClientTimeout
from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag
from urllib.parse import urljoin

# orjson — опционально, если установлен, используем его для сериализации
try:
    import orjson  # type: ignore[import]
except Exception:  # noqa: BLE001
    orjson = None


# ==========================
#  Исключения
# ==========================


class FetchError(Exception):
    """Ошибка при загрузке HTML-страницы."""


class LotParsingError(Exception):
    """Ошибка при разборе HTML-страницы лота."""


# ==========================
#  Конфигурация и индекс
# ==========================


@dataclass(frozen=True)
class CrawlerConfig:
    """
    Конфигурация параметров краулера.
    """

    base_url: str = "https://www.wolmar.ru"
    retries: int = 3
    timeout_sec: int = 5
    max_concurrent_requests: int = 20
    lot_delay_sec: float = 0.0
    page_delay_sec: float = 0.0

    @property
    def normalized_base_url(self) -> str:
        """Возвращает базовый URL без завершающего слэша."""
        return self.base_url.rstrip("/")

    @classmethod
    def default(cls) -> "CrawlerConfig":
        """Создаёт конфигурацию по умолчанию."""
        return cls()


@dataclass
class AuctionIndex:
    """
    Модель индекса аукционов.

    Ожидаемый JSON:
    {
        "vip": [...],
        "standard": [...]
    }
    """

    vip: List[str]
    standard: List[str]

    @classmethod
    def from_file(cls, path: Path) -> "AuctionIndex":
        """Загружает индекс аукционов из JSON-файла."""
        if not path.exists():
            raise FileNotFoundError(f"Не найден файл индекса аукционов: {path}")

        with path.open("r", encoding="utf-8") as file:
            raw: Dict[str, Sequence[str]] = json.load(file)

        vip_urls = list(raw.get("vip", []))
        standard_urls = list(raw.get("standard", []))

        if not vip_urls and not standard_urls:
            raise ValueError(
                "Индекс аукционов пустой: не найдено ни 'vip', ни 'standard'."
            )

        return cls(vip=vip_urls, standard=standard_urls)

    def iter_auctions(self) -> Iterable[Tuple[str, str]]:
        """Итератор по всем аукционам (тип, URL)."""
        for url in self.vip:
            yield "vip", url
        for url in self.standard:
            yield "standard", url


# ==========================
#  HTTP-слой
# ==========================


class AsyncHtmlFetcher(Protocol):
    """Протокол асинхронного загрузчика HTML-страниц."""

    async def fetch(self, url: str) -> str:
        raise NotImplementedError


@dataclass
class AsyncRequestsHtmlFetcher:
    """
    Реализация AsyncHtmlFetcher на основе aiohttp.ClientSession.

    Один общий экземпляр используется всеми краулерами; параллелизм
    ограничивается общим семафором.
    """

    config: CrawlerConfig
    _session: Optional[ClientSession] = field(init=False, default=None)
    _semaphore: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_requests)

    @property
    def session(self) -> ClientSession:
        """Лениво создаёт и возвращает HTTP-сессию."""
        if self._session is None:
            timeout = ClientTimeout(total=self.config.timeout_sec)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 "
                        "(compatible; WolmarResearchBot/1.0; "
                        "+https://example.com/)"
                    )
                },
            )
        return self._session

    async def aclose(self) -> None:
        """Закрывает HTTP-сессию (если она была создана)."""
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "AsyncRequestsHtmlFetcher":
        _ = self.session  # инициируем сессию
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        await self.aclose()

    async def fetch(self, url: str) -> str:
        """
        Загружает HTML по указанному URL с несколькими попытками.
        """
        last_error: Optional[BaseException] = None

        async with self._semaphore:
            for attempt in range(1, self.config.retries + 1):
                try:
                    async with self.session.get(url) as response:
                        response = response  # type: ClientResponse
                        response.raise_for_status()
                        return await response.text()
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    last_error = exc
                    delay = 0.1 * attempt
                    print(
                        f"[WARN] Ошибка при загрузке {url} "
                        f"(попытка {attempt}/{self.config.retries}): {exc}"
                    )
                    if attempt < self.config.retries:
                        print(f"[INFO] Повтор через {delay} сек...")
                        await asyncio.sleep(delay)

        raise FetchError(f"Не удалось загрузить {url}") from last_error


# ==========================
#  Модель лота и парсер
# ==========================


@dataclass
class Lot:
    """
    Модель лота Wolmar.

    Важные моменты:
    - final_price — конечная ставка (цена) в рублях в виде int;
    - datetime — дата и время закрытия соответствующего аукциона.
    """

    auction_type: str
    auction_url: str
    lot_url: str

    # дата/время закрытия аукциона, например "27.11.2025 10:10"
    datetime: Optional[str] = None

    lot_number: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None

    year: Optional[str] = None
    letters: Optional[str] = None
    metal: Optional[str] = None
    grade: Optional[str] = None

    final_price: Optional[int] = None
    leader: Optional[str] = None
    bids: Optional[int] = None
    status: Optional[str] = None


# Регулярки на модульном уровне — компилируются один раз
LOT_HEADER_INLINE_RE = re.compile(r"Лот\s*№\s*(\d+)\.?\s*(.*)")
LOT_HEADER_TEXT_RE = re.compile(r"Лот\s*№\s*\d+")
LOT_HEADER_LINE_RE = re.compile(r"^Лот\s*№\s*\d+")


class LotParser:
    """
    Парсер HTML-страниц лотов Wolmar.

    Description извлекается из DOM как текст, расположенный блоком
    сразу под большим красным заголовком «Лот №…».
    """

    FIELD_MAP: Dict[str, str] = {
        "Год": "year",
        "Буквы": "letters",
        "Металл": "metal",
        "Сохранность": "grade",
        "Ставка": "final_price",
        "Лидер": "leader",
        "Количество ставок": "bids",
    }

    # ---------- Вспомогательные методы ----------

    @staticmethod
    def _normalize_lines_from_soup(soup: BeautifulSoup) -> List[str]:
        text = soup.get_text("\n", strip=True)
        return [line.strip() for line in text.splitlines() if line.strip()]

    @classmethod
    def _lines_from_html(cls, html: str) -> Tuple[BeautifulSoup, List[str]]:
        # Используем быстрый парсер lxml
        soup = BeautifulSoup(html, "lxml")
        lines = cls._normalize_lines_from_soup(soup)
        return soup, lines

    @staticmethod
    def _parse_lot_header(line: str) -> Tuple[Optional[int], Optional[str]]:
        match = LOT_HEADER_INLINE_RE.match(line)
        if not match:
            return None, None
        number = int(match.group(1))
        title = match.group(2).strip() or None
        return number, title

    @staticmethod
    def _parse_int_from_text(text: str) -> Optional[int]:
        cleaned = text.replace("\u00a0", " ")
        match = re.search(r"\d+", cleaned.replace(" ", ""))
        if not match:
            return None
        try:
            return int(match.group(0))
        except ValueError:
            return None

    # ---------- Поиск правильного заголовка через DOM ----------

    def _distance_to_nearest_field(self, node: NavigableString) -> int:
        max_distance = 10**9
        distance = 0

        for element in node.next_elements:
            distance += 1

            if isinstance(element, NavigableString):
                text = str(element).strip()
            else:
                continue

            if not text:
                continue

            if any(
                text.startswith(f"{name}:") or text == f"{name}:"
                for name in self.FIELD_MAP.keys()
            ):
                return distance

        return max_distance

    def _find_header_node(self, soup: BeautifulSoup) -> NavigableString:
        candidates = list(soup.find_all(string=LOT_HEADER_TEXT_RE))
        if not candidates:
            raise LotParsingError("Не найден текст 'Лот №...' на странице.")

        best_node = candidates[0]
        best_distance = self._distance_to_nearest_field(best_node)

        for node in candidates[1:]:
            distance = self._distance_to_nearest_field(node)
            if distance < best_distance:
                best_node = node
                best_distance = distance

        return best_node

    # ---------- DOM-извлечение description ----------

    def _extract_dom_description(
        self,
        header_node: NavigableString,
    ) -> Optional[str]:
        parent = header_node.parent
        if parent is None:
            return None

        for sibling in parent.next_siblings:
            if isinstance(sibling, NavigableString):
                text = str(sibling).strip()
            elif isinstance(sibling, Tag):
                text = sibling.get_text(" ", strip=True)
            else:
                continue

            if not text:
                continue

            if any(
                text.startswith(f"{name}:") or text == f"{name}:"
                for name in self.FIELD_MAP.keys()
            ):
                break

            if text.startswith("Лот "):
                break

            return text

        return None

    # ---------- Парсинг характеристик/цен/статуса ----------

    def _parse_fields(self, lines: Sequence[str], lot: Lot) -> None:
        parsed: Set[str] = set()

        for idx, line in enumerate(lines):
            for ru_name, attr_name in self.FIELD_MAP.items():
                key_colon = f"{ru_name}:"

                if not (line.startswith(key_colon) or line == key_colon):
                    continue
                if attr_name in parsed:
                    continue

                inline_value = (
                    line[len(key_colon) :].strip() if line != key_colon else ""
                )
                if inline_value:
                    value_text = inline_value
                else:
                    value_text = (
                        lines[idx + 1].strip()
                        if idx + 1 < len(lines)
                        else ""
                    )

                if not value_text:
                    continue

                if attr_name == "bids":
                    bids_int = self._parse_int_from_text(value_text)
                    if bids_int is not None:
                        lot.bids = bids_int
                elif attr_name == "final_price":
                    price_int = self._parse_int_from_text(value_text)
                    if price_int is not None:
                        lot.final_price = price_int
                else:
                    setattr(lot, attr_name, value_text)

                parsed.add(attr_name)

        for line in lines:
            if line.startswith("Лот закрыт"):
                lot.status = "closed"
                break
            if line.startswith("Лот открыт"):
                lot.status = "open"
                break

    # ---------- Основной метод ----------

    def parse(
        self,
        html: str,
        lot_url: str,
        auction_type: str,
        auction_url: str,
        auction_datetime: Optional[str] = None,
    ) -> Lot:
        soup, lines = self._lines_from_html(html)

        header_node = self._find_header_node(soup)
        header_text = str(header_node).strip()

        header_index: Optional[int] = None
        for idx, line in enumerate(lines):
            if line == header_text:
                header_index = idx
                break

        if header_index is None:
            for idx, line in enumerate(lines):
                if LOT_HEADER_LINE_RE.search(line):
                    header_index = idx
                    break

        if header_index is None:
            raise LotParsingError("Не удалось сопоставить заголовок лота в тексте.")

        header_line = lines[header_index]

        lot = Lot(
            auction_type=auction_type,
            auction_url=auction_url,
            lot_url=lot_url,
            datetime=auction_datetime,
        )

        lot_number, title = self._parse_lot_header(header_line)
        lot.lot_number = lot_number
        lot.title = title

        lot.description = self._extract_dom_description(header_node)

        field_lines = lines[header_index:]
        self._parse_fields(field_lines, lot)

        return lot


# ==========================
#  Синки для записи результата
# ==========================


class LotSink(Protocol):
    async def write_lot(self, lot: Lot) -> None:
        raise NotImplementedError

    async def aclose(self) -> None:
        raise NotImplementedError


@dataclass
class JsonLinesLotSink:
    """
    Потоковая запись лотов в NDJSON-файл (по одной строке на лот).

    ВНИМАНИЕ: буферизации нет, каждая запись сразу уходит в файл.
    Это максимально стриминговый режим — удобно, если важна
    надёжность/простота и не критична лишняя нагрузка на диск.
    """

    path: Path
    encoding: str = "utf-8"

    _file: Optional[object] = field(init=False, default=None)
    _binary: bool = field(init=False, default=False)

    async def __aenter__(self) -> "JsonLinesLotSink":
        # Если есть orjson — используем бинарный режим (он работает с bytes)
        self._binary = orjson is not None
        if self._binary:
            self._file = await aiofiles.open(self.path, mode="wb")
        else:
            self._file = await aiofiles.open(
                self.path,
                mode="w",
                encoding=self.encoding,
            )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        await self.aclose()

    async def write_lot(self, lot: Lot) -> None:
        """
        Пишет один лот в файл немедленно (без буферизации).
        """
        if self._file is None:
            raise RuntimeError("JsonLinesLotSink не инициализирован (__aenter__).")

        payload = lot.__dict__

        if self._binary and orjson is not None:
            data = orjson.dumps(payload, option=orjson.OPT_APPEND_NEWLINE)
            await self._file.write(data)  # type: ignore[union-attr]
        else:
            line = json.dumps(payload, ensure_ascii=False) + "\n"
            await self._file.write(line)  # type: ignore[union-attr]

    async def aclose(self) -> None:
        """Закрывает файл, если он открыт."""
        if self._file is not None:
            await self._file.close()  # type: ignore[union-attr]
            self._file = None



# ----- Слой для проблемных ссылок -----


@dataclass
class FailedUrlRecord:
    """
    Описание проблемного лота, который не удалось спарсить.

    Содержит всю контекстную информацию, чтобы можно было повторно
    запросить страницу и корректно распарсить лот.
    """

    url: str
    auction_type: str
    auction_url: str
    auction_datetime: Optional[str]


@dataclass
class FailedUrlSink:
    """
    Потоковая запись проблемных лотов в NDJSON-файл.

    Одновременно хранит в памяти уникальный набор ссылок для повторного
    обхода после завершения основного цикла краулера.
    """

    path: Path
    encoding: str = "utf-8"
    buffer_size: int = 1000

    _file: Optional[object] = field(init=False, default=None)
    _binary: bool = field(init=False, default=False)
    _buffer: List[object] = field(init=False, default_factory=list)
    _for_retry: Dict[str, FailedUrlRecord] = field(init=False, default_factory=dict)

    async def __aenter__(self) -> "FailedUrlSink":
        self._binary = orjson is not None
        if self._binary:
            self._file = await aiofiles.open(self.path, mode="wb")
        else:
            self._file = await aiofiles.open(
                self.path,
                mode="w",
                encoding=self.encoding,
            )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        await self.aclose()

    async def _flush(self) -> None:
        if not self._file or not self._buffer:
            return

        if self._binary:
            data = b"".join(self._buffer)  # type: ignore[arg-type]
            await self._file.write(data)  # type: ignore[union-attr]
        else:
            text = "".join(self._buffer)  # type: ignore[list-item]
            await self._file.write(text)  # type: ignore[union-attr]

        self._buffer.clear()

    async def write_failed_url(self, record: FailedUrlRecord) -> None:
        """
        Сохраняет информацию о проблемной ссылке.

        - пишет одну строку в NDJSON;
        - запоминает ссылку в in-memory наборе для последующей повторной обработки.
        """
        if self._file is None:
            raise RuntimeError("FailedUrlSink не инициализирован (__aenter__).")

        # In-memory набор для последующей обработки (дедуп по url)
        if record.url not in self._for_retry:
            self._for_retry[record.url] = record

        payload = {
            "url": record.url,
            "auction_type": record.auction_type,
            "auction_url": record.auction_url,
            "auction_datetime": record.auction_datetime,
        }

        if self._binary and orjson is not None:
            self._buffer.append(
                orjson.dumps(payload, option=orjson.OPT_APPEND_NEWLINE)
            )
        else:
            self._buffer.append(json.dumps(payload, ensure_ascii=False) + "\n")

        if len(self._buffer) >= self.buffer_size:
            await self._flush()

    async def aclose(self) -> None:
        if self._file is not None:
            await self._flush()
            await self._file.close()  # type: ignore[union-attr]
            self._file = None

    def get_records_for_retry(self) -> List[FailedUrlRecord]:
        """Возвращает список уникальных проблемных ссылок для повторной обработки."""
        return list(self._for_retry.values())


# ==========================
#  Краулер аукциона
# ==========================


@dataclass
class AsyncAuctionCrawler:
    """
    Асинхронный краулер страниц одного аукциона.
    """

    config: CrawlerConfig
    fetcher: AsyncHtmlFetcher
    lot_parser: LotParser
    failed_sink: FailedUrlSink

    lot_url_pattern: re.Pattern[str] = field(
        init=False,
        default=re.compile(r"^/auction/\d+/\d+/?$"),
    )

    @staticmethod
    def _extract_auction_datetime(soup: BeautifulSoup) -> Optional[str]:
        """
        Извлекает дату/время закрытия аукциона с главной страницы.

        Ищет текст вида 'Закрыт 27.11.2025 10:10' и возвращает
        '27.11.2025 10:10'.
        """
        node = soup.find(
            string=re.compile(
                r"Закрыт\s+\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}"
            )
        )
        if not node:
            return None

        match = re.search(
            r"(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})",
            str(node),
        )
        if not match:
            return None
        return match.group(1)

    def _parse_auction_page(
        self,
        html: str,
        current_url: str,
    ) -> Tuple[List[str], Optional[str], Optional[str]]:
        """
        Разбирает страницу аукциона один раз.

        Returns:
            lot_urls: список URL лотов;
            next_page_url: URL следующей страницы или None;
            auction_datetime: дата/время закрытия аукциона.
        """
        soup = BeautifulSoup(html, "lxml")

        urls: Set[str] = set()
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            if self.lot_url_pattern.match(href):
                urls.add(urljoin(self.config.normalized_base_url, href))

        lot_urls = sorted(urls)

        link = soup.find(
            "a",
            string=lambda txt: isinstance(txt, str)
            and "Следующая страница" in txt,
        )
        if link and link.get("href"):
            next_url = urljoin(current_url, link["href"])
        else:
            next_url = None

        if next_url == current_url:
            next_url = None

        auction_datetime = self._extract_auction_datetime(soup)

        return lot_urls, next_url, auction_datetime

    async def _fetch_and_parse_lot(
        self,
        lot_url: str,
        auction_type: str,
        auction_url: str,
        auction_datetime: Optional[str],
    ) -> Optional[Lot]:
        """
        Загружает и парсит один лот (с обработкой ошибок).

        При неуспехе записывает информацию о проблемной ссылке в FailedUrlSink.
        """
        try:
            html = await self.fetcher.fetch(lot_url)
            lot = self.lot_parser.parse(
                html=html,
                lot_url=lot_url,
                auction_type=auction_type,
                auction_url=auction_url,
                auction_datetime=auction_datetime,
            )
        except (FetchError, LotParsingError) as exc:
            print(f"[ERROR] Пропуск лота {lot_url}: {exc}")
            await self.failed_sink.write_failed_url(
                FailedUrlRecord(
                    url=lot_url,
                    auction_type=auction_type,
                    auction_url=auction_url,
                    auction_datetime=auction_datetime,
                )
            )
            return None

        if self.config.lot_delay_sec > 0:
            await asyncio.sleep(self.config.lot_delay_sec)

        return lot

    async def crawl_auction(
        self,
        auction_url: str,
        auction_type: str,
        start_page_url: Optional[str] = None,
    ) -> AsyncIterator[Lot]:
        """
        Асинхронно обходит один аукцион и отдаёт его лоты.

        Args:
            auction_url: базовый URL аукциона (без ?page=...),
                         попадает в Lot.auction_url.
            auction_type: тип ('vip' или 'standard').
            start_page_url: URL страницы, с которой начинать обход.
                По умолчанию совпадает с auction_url. Используется для
                повторной обработки проблемных страниц (если нужно).
        """
        page_url: Optional[str] = start_page_url or auction_url
        auction_datetime: Optional[str] = None

        while page_url:
            print(f"[INFO] Загружаю страницу аукциона: {page_url}")
            try:
                html = await self.fetcher.fetch(page_url)
            except FetchError as exc:
                print(f"[ERROR] Невозможно загрузить аукцион {page_url}: {exc}")
                # Здесь фиксируем только факт проблемы на уровне аукциона
                # (без дальнейших попыток повторного обхода страниц аукциона).
                break

            lot_urls, next_page, page_datetime = self._parse_auction_page(
                html,
                page_url,
            )
            if auction_datetime is None:
                auction_datetime = page_datetime

            lot_urls = list(dict.fromkeys(lot_urls))

            if not lot_urls:
                print("[WARN] На странице аукциона нет ссылок на лоты.")
                break

            print(f"[INFO] Найдено {len(lot_urls)} лотов на странице.")

            tasks: List[asyncio.Task[Optional[Lot]]] = [
                asyncio.create_task(
                    self._fetch_and_parse_lot(
                        lot_url=url,
                        auction_type=auction_type,
                        auction_url=auction_url,
                        auction_datetime=auction_datetime,
                    )
                )
                for url in lot_urls
            ]

            for task in asyncio.as_completed(tasks):
                lot = await task
                if lot is not None:
                    yield lot

            if not next_page:
                break

            if self.config.page_delay_sec > 0:
                await asyncio.sleep(self.config.page_delay_sec)

            page_url = next_page


# ==========================
#  Высокоуровневый краулер
# ==========================


@dataclass
class AsyncWolmarCrawler:
    """
    Высокоуровневый краулер, который запускает обработку всех аукционов.
    """

    index: AuctionIndex
    auction_crawler: AsyncAuctionCrawler
    lot_sink: LotSink

    total_lots: int = 0

    async def _crawl_one_auction(self, auction_type: str, auction_url: str) -> None:
        print(
            f"=== Старт обхода аукциона типа '{auction_type}': "
            f"{auction_url} ==="
        )
        try:
            async for lot in self.auction_crawler.crawl_auction(
                auction_url=auction_url,
                auction_type=auction_type,
            ):
                await self.lot_sink.write_lot(lot)
                self.total_lots += 1
        except Exception as exc:  # noqa: BLE001
            print(
                f"[ERROR] Некорректное завершение обхода аукциона "
                f"{auction_url}: {exc}"
            )

    async def run(self) -> None:
        tasks: List[asyncio.Task[None]] = []

        for auction_type, auction_url in self.index.iter_auctions():
            tasks.append(
                asyncio.create_task(
                    self._crawl_one_auction(
                        auction_type=auction_type,
                        auction_url=auction_url,
                    )
                )
            )

        if tasks:
            await asyncio.gather(*tasks)

        print(f"[INFO] Всего спарсено лотов: {self.total_lots}")


# ==========================
#  Повторная обработка проблемных лотов
# ==========================


async def retry_failed_lots(
    failed_records: List[FailedUrlRecord],
    auction_crawler: AsyncAuctionCrawler,
    lot_sink: LotSink,
    max_rounds: int = 5,
) -> None:
    """
    Повторно обрабатывает все проблемные лоты до успешного парсинга
    или до достижения максимального числа раундов.

    На каждом раунде:
    - для каждой ссылки пытаемся заново скачать и распарсить лот;
    - при успехе сразу пишем в основной NDJSON;
    - при неуспехе ссылка переносится в следующий раунд.
    """
    current = failed_records
    round_no = 1

    while current and round_no <= max_rounds:
        print(
            f"[INFO] Дополнительный обход проблемных лотов: "
            f"раунд {round_no}, всего {len(current)} ссылок."
        )
        next_round: List[FailedUrlRecord] = []

        for record in current:
            lot = await auction_crawler._fetch_and_parse_lot(
                lot_url=record.url,
                auction_type=record.auction_type,
                auction_url=record.auction_url,
                auction_datetime=record.auction_datetime,
            )
            if lot is None:
                next_round.append(record)
            else:
                await lot_sink.write_lot(lot)

        current = next_round
        round_no += 1

    if current:
        print(
            f"[WARN] После {max_rounds} раундов осталось "
            f"{len(current)} неуспешных ссылок."
        )
    else:
        print("[INFO] Все проблемные лоты успешно обработаны.")


# ==========================
#  Точка входа
# ==========================


async def async_main() -> None:
    input_path = Path("wolmar_closed_auctions_1.json")
    output_path = Path("wolmar_lots_async_1.ndjson")
    failed_path = Path("wolmar_failed_lots.ndjson")

    config = CrawlerConfig.default()
    index = AuctionIndex.from_file(input_path)

    async with AsyncRequestsHtmlFetcher(config=config) as fetcher, \
            JsonLinesLotSink(path=output_path) as sink, \
            FailedUrlSink(path=failed_path) as failed_sink:

        lot_parser = LotParser()
        auction_crawler = AsyncAuctionCrawler(
            config=config,
            fetcher=fetcher,
            lot_parser=lot_parser,
            failed_sink=failed_sink,
        )

        wolmar_crawler = AsyncWolmarCrawler(
            index=index,
            auction_crawler=auction_crawler,
            lot_sink=sink,
        )

        # Основной проход по всем аукционам
        await wolmar_crawler.run()

        # Дополнительные раунды для проблемных ссылок
        failed_records = failed_sink.get_records_for_retry()
        if failed_records:
            await retry_failed_lots(
                failed_records=failed_records,
                auction_crawler=auction_crawler,
                lot_sink=sink,
            )

    print(f"[INFO] Результат сохранён в {output_path}")
    print(f"[INFO] Проблемные лоты записаны в {failed_path}")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
