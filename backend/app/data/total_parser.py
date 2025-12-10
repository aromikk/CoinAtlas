#!/usr/bin/env python3
"""
Единый файл для парсинга монет с raritetus.ru и склейки результатов.

Возможности:
- Описывает конфигурации всех парсеров монет (СССР, царские и др.).
- Для каждой конфигурации запускает парсинг списка монет и страниц монет.
- Для каждого парсера создаёт отдельный JSON-файл (как самостоятельный вывод).
- Дополнительно создаёт один общий JSON-файл coins_all.json,
  который является конкатенацией всех монет.

Архитектура:
- ParserConfig — dataclass с параметрами парсера.
- CoinRecord — dataclass, описывающий одну монету.
- Parser — Protocol, задающий интерфейс парсера.
- RaritetusParser — реализация Parser для raritetus.ru.
- ParserOrchestrator — оркестратор, который запускает все парсеры
  и собирает результаты.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    runtime_checkable,
)

import requests
from bs4 import BeautifulSoup


# =============================================================================
#  Исключения
# =============================================================================


class ParserError(RuntimeError):
    """Базовая ошибка, связанная с работой парсеров."""


class NetworkError(ParserError):
    """Ошибка сети при запросах к сайту."""


class HtmlParseError(ParserError):
    """Ошибка при разборе HTML страницы."""


class ResultIoError(ParserError):
    """Ошибка при чтении/записи JSON-файлов с результатами."""


# =============================================================================
#  Модели данных
# =============================================================================


@dataclass
class ParserConfig:
    """
    Конфигурация одного парсера raritetus.

    Attributes:
        name:
            Человекочитаемое имя парсера (например, "СССР ходячка").
        group:
            Идентификатор группы монет (например, "sssr_regular").
            Пишется в поле 'group' каждой монеты.
        link_contains:
            Подстрока, по которой мы фильтруем ссылки на монеты
            на страницах списка (например,
            '/stoimost-monet/monety-sssr/hodyachka/sssr/').
        list_url_template:
            Шаблон URL для страниц списка. Может содержать "{page}".
            Например:
                "https://.../sssr/page.{page}/"
            или без "{page}" для одиночной страницы:
                "https://.../tuva/"
        first_page:
            Номер первой страницы пагинации (если в шаблоне есть "{page}").
            Если "{page}" нет, значение игнорируется.
        last_page:
            Номер последней страницы пагинации (если в шаблоне есть "{page}").
        initial_url:
            Дополнительный URL, который нужно спарсить перед пагинацией.
            Например, базовая страница без "page.N".
            Если None — ничего дополнительного не парсим.
        output_json:
            Имя JSON-файла, в который нужно сохранить результат парсинга
            для данного конфигурационного парсера.
    """

    name: str
    group: str
    link_contains: str
    list_url_template: str
    first_page: int
    last_page: int
    initial_url: Optional[str]
    output_json: str

    @property
    def has_pagination(self) -> bool:
        """Возвращает True, если шаблон URL использует параметр {page}."""
        return "{page}" in self.list_url_template

    @property
    def first_list_url_for_log(self) -> str:
        """
        URL, который удобно показать в логах как "первая страница".

        Если есть initial_url — берём его.
        Иначе, если есть пагинация — форматируем first_page.
        Иначе — просто list_url_template.
        """
        if self.initial_url:
            return self.initial_url
        if self.has_pagination:
            return self.list_url_template.format(page=self.first_page)
        return self.list_url_template


@dataclass
class CoinRecord:
    """
    Описание одной монеты, нормализованное для внутреннего использования.

    Поля достаточно общие, чтобы покрыть и СССР, и царские монеты.

    Важно: при записи в JSON мы используем `to_dict()`, чтобы получить
    обычный словарь, удобный для дальнейшего импорта в БД.
    """

    source_url: str
    group: str
    title: str

    nominal: str
    year: Optional[int]
    name: str
    series: str
    variety_desc: str
    letters: str
    edge: str
    quality: str
    mintage: str

    material: str
    weight_g: Optional[float]
    diameter_mm: Optional[float]
    thickness_mm: Optional[float]

    catalogs: List[str]

    image_obverse: Optional[str]
    image_reverse: Optional[str]

    desc_obverse: str
    desc_reverse: str

    def to_dict(self) -> Dict[str, Any]:
        """
        Преобразует запись монеты в словарь для JSON-сериализации.

        Returns:
            dict с ключами, соответствующими атрибутам dataclass.
        """
        return asdict(self)


# =============================================================================
#  Протокол парсера
# =============================================================================


@runtime_checkable
class Parser(Protocol):
    """
    Протокол парсера: то, что должен уметь любой парсер монет.

    Реализации:
    - должны иметь атрибут config: ParserConfig;
    - должны уметь запускать парсинг (run) и загружать результат (load_results).
    """

    config: ParserConfig

    def run(self) -> None:
        """
        Запускает парсинг монет для данной конфигурации.

        Может выбрасывать ParserError наследников при критических ошибках.
        """
        raise NotImplementedError

    def load_results(self) -> List[Dict[str, Any]]:
        """
        Загружает результаты парсинга из файла config.output_json.

        Returns:
            Список словарей с монетами.

        Raises:
            ResultIoError: если файл не найден или JSON некорректен.
        """
        raise NotImplementedError


# =============================================================================
#  Реализация парсера raritetus.ru
# =============================================================================


class RaritetusParser(Parser):
    """
    Парсер монет с raritetus.ru для одной конфигурации ParserConfig.

    Основные обязанности:
    - собрать все ссылки на монеты со страниц списка;
    - по каждой ссылке скачать страницу монеты и распарсить её;
    - сохранить результат (список монет) в JSON-файл config.output_json.
    """

    BASE_URL = "https://www.raritetus.ru"
    USER_AGENT = (
        "Mozilla/5.0 (compatible; CoinAtlasBot/1.0; +https://example.com/bot)"
    )

    def __init__(
        self,
        config: ParserConfig,
        base_dir: Optional[Path] = None,
        sleep_between_requests: float = 0,
    ) -> None:
        """
        Args:
            config:
                Конфигурация парсера.
            base_dir:
                Базовая директория, где будут лежать JSON-файлы.
                По умолчанию — папка текущего файла.
            sleep_between_requests:
                Пауза между запросами к сайту (секунды).
        """
        self.config = config
        self._base_dir = base_dir or Path(__file__).resolve().parent
        self._sleep = sleep_between_requests

        self._session = requests.Session()
        self._session.headers.update({"User-Agent": self.USER_AGENT})

    # ---------- публичный API Parser ----------

    @property
    def output_path(self) -> Path:
        """Полный путь к JSON-файлу с результатом."""
        return self._base_dir / self.config.output_json

    def run(self) -> None:
        """
        Запускает парсинг монет для данной конфигурации.

        Шаги:
        1. Собрать все ссылки на монеты.
        2. Для каждой ссылки спарсить страницу монеты.
        3. Сохранить список монет в JSON-файл.
        """
        print("=" * 80)
        print(f"Парсер: {self.config.name} (group={self.config.group})")
        print(f"Первая страница: {self.config.first_list_url_for_log}")
        print("=" * 80)

        try:
            coin_urls = self._collect_coin_urls()
        except NetworkError as exc:
            print(f"!! Не удалось собрать ссылки на монеты: {exc}")
            raise

        print(f"Всего уникальных ссылок на монеты: {len(coin_urls)}")

        coins: List[CoinRecord] = []
        for index, url in enumerate(coin_urls, start=1):
            print(f"\n[{index}/{len(coin_urls)}] Парсим монету: {url}")
            try:
                coin = self._parse_coin_with_handling(url)
            except ParserError as exc:
                print(f"!! Монета пропущена из-за ошибки: {exc}")
                continue

            coins.append(coin)
            time.sleep(self._sleep)

        self._save_results(coins)

    def load_results(self) -> List[Dict[str, Any]]:
        """
        Читает JSON-файл, созданный данным парсером.

        Returns:
            Список словарей-монет.

        Raises:
            ResultIoError: если файл отсутствует или некорректен.
        """
        path = self.output_path
        if not path.exists():
            raise ResultIoError(
                f"Файл с результатами '{path}' не найден "
                f"(парсер '{self.config.name}')"
            )

        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except Exception as exc:  # noqa: BLE001
            raise ResultIoError(
                f"Не удалось прочитать JSON из файла '{path}'"
            ) from exc

        if not isinstance(data, list):
            raise ResultIoError(
                f"Ожидался список в '{path}', получен объект типа {type(data)!r}"
            )

        print(
            f"Загружено {len(data)} записей из '{path.name}' "
            f"для парсера '{self.config.name}'"
        )
        return data

    # ---------- внутренняя логика парсера ----------

    def _collect_coin_urls(self) -> List[str]:
        """
        Собирает уникальные ссылки на монеты со всех страниц списка.

        Returns:
            Список URL-ов монет.
        """
        urls: List[str] = []
        seen: set[str] = set()

        # 1) Дополнительный initial_url (если указан)
        if self.config.initial_url:
            html = self._fetch_html(self.config.initial_url)
            found = self._parse_list_page(html)
            urls, seen = self._extend_unique(urls, seen, found)
            time.sleep(self._sleep)

        # 2) Пагинация (page.N), если нужно
        if self.config.has_pagination:
            for page in range(self.config.first_page, self.config.last_page + 1):
                page_url = self.config.list_url_template.format(page=page)
                html = self._fetch_html(page_url)
                found = self._parse_list_page(html)
                print(
                    f"На странице {page_url} найдено монет: {len(found)}"
                )
                urls, seen = self._extend_unique(urls, seen, found)
                time.sleep(self._sleep)
        else:
            # Если пагинации нет и не было initial_url —
            # используем list_url_template как единственную страницу.
            if not self.config.initial_url:
                html = self._fetch_html(self.config.list_url_template)
                found = self._parse_list_page(html)
                print(
                    f"На странице {self.config.list_url_template} "
                    f"найдено монет: {len(found)}"
                )
                urls, seen = self._extend_unique(urls, seen, found)
                time.sleep(self._sleep)

        return urls

    def _parse_list_page(self, html: str) -> List[str]:
        """
        Разбирает страницу списка и достаёт ссылки на монеты.

        Ссылки фильтруются по подстроке config.link_contains и
        по наличию числового идентификатора в конце slug-а.
        """
        soup = BeautifulSoup(html, "html.parser")
        urls: List[str] = []

        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            if self.config.link_contains not in href:
                continue
            if not href.endswith("/"):
                continue

            slug = href.rstrip("/").split("/")[-1]
            parts = slug.split("-")
            if not parts or not parts[-1].isdigit():
                continue

            full_url = self._absolute_url(href)
            urls.append(full_url)

        return urls

    def _parse_coin_with_handling(self, url: str) -> CoinRecord:
        """
        Парсит страницу монеты с обработкой сетевых и HTML ошибок.

        При HTTP 500 — явно пишем в лог и выбрасываем HtmlParseError.
        """
        try:
            html = self._fetch_html(url)
        except NetworkError as exc:
            raise HtmlParseError(
                f"Не удалось загрузить страницу монеты '{url}': {exc}"
            ) from exc

        try:
            return self._parse_coin_page(url, html)
        except Exception as exc:  # noqa: BLE001
            raise HtmlParseError(
                f"Ошибка разбора HTML страницы монеты '{url}': {exc}"
            ) from exc

    def _parse_coin_page(self, url: str, html: str) -> CoinRecord:
        """
        Парсит страницу конкретной монеты raritetus.

        Общая логика едина для большинства разделов:
        - заголовок <h1> с удалением текста вида "Стоимость монеты ...";
        - таблицы "Общая информация", "Характеристики", "Каталожные номера";
        - описания аверса/реверса;
        - картинки аверса и реверса.
        """
        soup = BeautifulSoup(html, "html.parser")

        # Заголовок
        title_raw = self._extract_title(soup)
        title_clean = self._clean_title(title_raw)

        # Таблица "Общая информация"
        info_table = self._find_table_after_header(soup, "Общая информация")
        info_data = self._parse_two_column_table(info_table)

        # Таблица "Характеристики"
        char_table = self._find_table_after_header(soup, "Характеристики")
        char_data = self._parse_two_column_table(char_table)

        # Таблица "Каталожные номера"
        catalogs_table = self._find_table_after_header(
            soup,
            "Каталожные номера",
        )

        catalogs = self._parse_catalogs(catalogs_table)

        # Из "Общая информация"
        nominal = info_data.get("Номинал", "")
        year_str = info_data.get("Год")
        variety_desc = info_data.get("Описание разновидности", "")
        name = info_data.get("Название", "")
        series_name = info_data.get("Название серии", "")
        edge = info_data.get("Гурт", "")
        letters = info_data.get("Буквы", "")
        quality = info_data.get("Качество выпуска", "")
        mintage = info_data.get("Тираж", "")

        year = self._to_int(year_str)

        # Из "Характеристики"
        material = char_data.get("Материал", "")
        weight = self._to_float(char_data.get("Вес"))
        diameter = self._to_float(char_data.get("Диаметр"))
        thickness = self._to_float(char_data.get("Толщина"))

        # Описание аверса/реверса
        desc_obverse = self._extract_text_after_header(soup, "Описание аверса")
        desc_reverse = self._extract_text_after_header(soup, "Описание реверса")

        # Картинки
        image_obverse, image_reverse = self._extract_coin_images(soup)

        return CoinRecord(
            source_url=url,
            group=self.config.group,
            title=title_clean,
            nominal=nominal,
            year=year,
            name=name,
            series=series_name,
            variety_desc=variety_desc,
            letters=letters,
            edge=edge,
            quality=quality,
            mintage=mintage,
            material=material,
            weight_g=weight,
            diameter_mm=diameter,
            thickness_mm=thickness,
            catalogs=catalogs,
            image_obverse=image_obverse,
            image_reverse=image_reverse,
            desc_obverse=desc_obverse,
            desc_reverse=desc_reverse,
        )

    def _save_results(self, coins: Sequence[CoinRecord]) -> None:
        """
        Сохраняет список монет в JSON-файл config.output_json.

        Каждая монета сначала преобразуется в словарь через to_dict().
        """
        path = self.output_path
        data = [coin.to_dict() for coin in coins]

        try:
            with path.open("w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            raise ResultIoError(
                f"Не удалось записать результат в файл '{path}'"
            ) from exc

        print(
            f"\nСохранено {len(coins)} монет в файл '{path.name}' "
            f"для парсера '{self.config.name}'"
        )

    # ---------- низкоуровневые вспомогательные методы ----------

    def _fetch_html(self, url: str) -> str:
        """
        Выполняет HTTP GET и возвращает текст страницы.

        Raises:
            NetworkError: при сетевой ошибке или статусе != 200.
        """
        print(f"[GET] {url}")
        try:
            response = self._session.get(url, timeout=20)
        except Exception as exc:  # noqa: BLE001
            raise NetworkError(f"Ошибка сети при запросе к '{url}'") from exc

        if response.status_code >= 500:
            raise NetworkError(
                f"Сервер вернул статус {response.status_code} для '{url}'"
            )

        try:
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise NetworkError(
                f"Ошибка HTTP {response.status_code} для '{url}'"
            ) from exc

        return response.text

    @staticmethod
    def _extend_unique(
        current: List[str],
        seen: set[str],
        new_items: Iterable[str],
    ) -> Tuple[List[str], set[str]]:
        """
        Добавляет в список новые URL-ы, устраняя дубли по множеству seen.

        Returns:
            Обновлённые current и seen.
        """
        for item in new_items:
            if item not in seen:
                seen.add(item)
                current.append(item)
        return current, seen

    @classmethod
    def _absolute_url(cls, href: str) -> str:
        """Преобразует относительный ссылку raritetus в абсолютный URL."""
        if href.startswith("//"):
            return "https:" + href
        return requests.compat.urljoin(cls.BASE_URL, href)

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> str:
        """Возвращает сырой текст заголовка <h1>."""
        tag = soup.find("h1")
        return tag.get_text(" ", strip=True) if tag else ""

    @staticmethod
    def _clean_title(raw_title: str) -> str:
        """
        Удаляет из заголовка служебные префиксы вида
        "Стоимость монеты" / "Цена монеты" и т.п.
        """
        if not raw_title:
            return ""

        title = raw_title.strip()
        lowered = title.lower()

        prefixes = [
            "стоимость монеты",
            "стоимость  монеты",
            "стоимость монет",
            "стоимость",
            "цена монеты",
            "цена",
        ]
        for prefix in prefixes:
            if lowered.startswith(prefix):
                title = title[len(prefix) :].strip()
                break

        if title.lower().startswith("монеты "):
            title = title[7:].strip()

        title = title.lstrip(":-— ").strip()
        return title or raw_title

    @staticmethod
    def _find_header(
        soup: BeautifulSoup,
        header_text: str,
    ) -> Optional[Any]:
        """
        Ищет заголовок h2/h3/h4, содержащий указанный текст (регистр игнорируется).
        """
        return soup.find(
            lambda tag: tag.name in ("h2", "h3", "h4")
            and header_text.lower() in tag.get_text(strip=True).lower()
        )

    @classmethod
    def _find_table_after_header(
        cls,
        soup: BeautifulSoup,
        header_text: str,
    ) -> Optional[Any]:
        """
        Ищет таблицу, следующую за заголовком "Общая информация" /
        "Характеристики" / "Каталожные номера".
        """
        header = cls._find_header(soup, header_text)
        if not header:
            return None

        node = header
        while True:
            node = node.find_next_sibling()
            if node is None:
                break

            if node.name == "table":
                return node

            if hasattr(node, "find"):
                table = node.find("table")
                if table is not None:
                    return table

            if node.name in ("h2", "h3", "h4"):
                break

        return None

    @staticmethod
    def _parse_two_column_table(table: Any) -> Dict[str, str]:
        """
        Разбирает таблицу вида:

        | Номинал | 20 копеек |
        | Год     | 1977      |

        и возвращает словарь:
        {"Номинал": "20 копеек", "Год": "1977"}.
        """
        result: Dict[str, str] = {}
        if table is None:
            return result

        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            label = cells[0].get_text(" ", strip=True)
            value = cells[1].get_text(" ", strip=True)
            if label:
                result[label] = value

        return result

    @staticmethod
    def _parse_catalogs(table: Any) -> List[str]:
        """
        Разбирает таблицу "Каталожные номера" в единый список строк вида:
        "Федорин 64-71", "Биткин 1234", "Конрос Ц-45" и т.п.
        """
        catalogs: List[str] = []
        if table is None:
            return catalogs

        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            label = cells[0].get_text(" ", strip=True)
            value = cells[1].get_text(" ", strip=True)
            if not label or not value:
                continue
            catalogs.append(f"{label} {value}".strip())

        return catalogs

    @classmethod
    def _extract_text_after_header(
        cls,
        soup: BeautifulSoup,
        header_text: str,
    ) -> str:
        """
        Собирает текст после заголовка (например, "Описание аверса")
        до следующего заголовка.
        """
        header = cls._find_header(soup, header_text)
        if not header:
            return ""

        chunks: List[str] = []
        node = header

        while True:
            node = node.find_next_sibling()
            if node is None:
                break
            if node.name in ("h2", "h3", "h4"):
                break

            text = node.get_text(" ", strip=True)
            if text:
                chunks.append(text)

        return "\n".join(chunks)

    @classmethod
    def _extract_coin_images(
        cls,
        soup: BeautifulSoup,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Пытается найти URL-ы картинок аверса и реверса.

        Логика:
        - сначала смотрим <img src="...storage/coins...avers..."> и revers;
        - если не нашли, смотрим <a href="...storage/coins...">.
        """
        image_obverse: Optional[str] = None
        image_reverse: Optional[str] = None

        for img in soup.find_all("img", src=True):
            src = img["src"]
            if "storage/coins" not in src:
                continue
            if "avers" in src and image_obverse is None:
                image_obverse = cls._absolute_url(src)
            elif "revers" in src and image_reverse is None:
                image_reverse = cls._absolute_url(src)

        if image_obverse is None or image_reverse is None:
            for tag in soup.find_all("a", href=True):
                href = tag["href"]
                if "storage/coins" not in href:
                    continue
                if "avers" in href and image_obverse is None:
                    image_obverse = cls._absolute_url(href)
                elif "revers" in href and image_reverse is None:
                    image_reverse = cls._absolute_url(href)

        return image_obverse, image_reverse

    @staticmethod
    def _to_float(value: Optional[str]) -> Optional[float]:
        """
        Извлекает число с плавающей точкой из строки
        (например, '3,4 г.' -> 3.4).
        """
        if not value:
            return None
        match = re.search(r"([\d.,]+)", value)
        if not match:
            return None
        text = (
            match.group(1)
            .replace(" ", "")
            .replace("\u00a0", "")
            .replace(",", ".")
        )
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _to_int(value: Optional[str]) -> Optional[int]:
        """Преобразует строку года в int, если это простое число."""
        if value is None:
            return None
        value_stripped = value.strip()
        return int(value_stripped) if value_stripped.isdigit() else None


# =============================================================================
#  Оркестратор
# =============================================================================


class ParserOrchestrator:
    """
    Класс-оркестратор, который управляет запуском нескольких парсеров.

    Responsibilities:
    - хранит список парсеров;
    - по очереди запускает их run();
    - загружает их результаты (load_results()) и объединяет;
    - сохраняет общий JSON-файл.
    """

    def __init__(
        self,
        parsers: Sequence[Parser],
        base_dir: Optional[Path] = None,
    ) -> None:
        """
        Args:
            parsers:
                Набор парсеров, реализующих протокол Parser.
            base_dir:
                Базовая директория для итогового файла (по умолчанию —
                папка текущего файла).
        """
        self._parsers: List[Parser] = list(parsers)
        self._base_dir: Path = base_dir or Path(__file__).resolve().parent

    @property
    def combined_output_path(self) -> Path:
        """Путь к итоговому JSON-файлу со всеми монетами."""
        return self._base_dir / "coins_all.json"

    def run_all(self, stop_on_error: bool = False) -> List[Dict[str, Any]]:
        """
        Запускает все парсеры по очереди и собирает результаты.

        Args:
            stop_on_error:
                Если True — при первой ошибке выбрасывает исключение.
                Если False — пишет ошибку в лог и продолжает с остальными.

        Returns:
            Объединённый список всех монет.
        """
        combined: List[Dict[str, Any]] = []

        for parser in self._parsers:
            print("\n" + "#" * 80)
            print(f"Запуск парсера: {parser.config.name}")
            print("#" * 80)

            try:
                parser.run()
                coins = parser.load_results()
                combined.extend(coins)
            except ParserError as exc:
                print(f"!! Ошибка в парсере '{parser.config.name}': {exc}")
                if stop_on_error:
                    raise

        return combined

    def save_combined(self, coins: Sequence[Dict[str, Any]]) -> Path:
        """
        Сохраняет общий список монет в coins_all.json.

        Returns:
            Путь к созданному файлу.
        """
        output_path = self.combined_output_path
        try:
            with output_path.open("w", encoding="utf-8") as file:
                json.dump(list(coins), file, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            raise ResultIoError(
                f"Не удалось записать общий JSON в '{output_path}'"
            ) from exc

        print(
            f"\nИтоговый файл: '{output_path}' "
            f"(общее количество монет: {len(coins)})"
        )
        return output_path


# =============================================================================
#  Конфигурация всех парсеров
# =============================================================================


def build_all_parser_configs() -> List[ParserConfig]:
    """
    Создаёт список конфигураций для всех нужных разделов raritetus.

    ВНИМАНИЕ: при необходимости сюда можно добавлять новые конфиги.
    """
    configs: List[ParserConfig] = []

    # ---- Россия: современные монеты ----

    configs.append(
        ParserConfig(
            name="Наборы ЦБ РФ",
            group="parser_nabory_cb",
            link_contains="/stoimost-monet/monety-rossii/nabory-cbrf/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-rossii/nabory-cbrf/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_nabory_cb.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Пробные монеты России",
            group="parser_trial",
            link_contains="/stoimost-monet/monety-rossii/probnye/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-rossii/probnye/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_trial.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Россия — Шпицберген",
            group="parser_shpicbergen",
            link_contains="/stoimost-monet/monety-rossii/shpicbergen/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-rossii/shpicbergen/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_shpicbergen.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Юбилейные РФ (недрагметаллы)",
            group="parser_yubilej_ne_dragmetall",
            link_contains=(
                "/stoimost-monet/monety-rossii/yubilejnye/ne-dragmetall/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-rossii/yubilejnye/ne-dragmetall/"
                "page.{page}/"
            ),
            first_page=1,
            last_page=11,
            initial_url=None,
            output_json="coins_yubilej_ne_dragmetall.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Юбилейные РФ (драгметаллы) часть 1",
            group="parser_yubilej_dragmetall_1",
            link_contains=(
                "/stoimost-monet/monety-rossii/yubilejnye/dragmetall/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-rossii/yubilejnye/dragmetall/"
                "page.{page}/"
            ),
            first_page=1,
            last_page=11,
            initial_url=None,
            output_json="coins_yubilej_dragmetall_1.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Юбилейные РФ (драгметаллы) часть 2",
            group="parser_yubilej_dragmetall_2",
            link_contains=(
                "/stoimost-monet/monety-rossii/yubilejnye/dragmetall/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-rossii/yubilejnye/dragmetall/"
                "page.{page}/"
            ),
            first_page=12,
            last_page=21,
            initial_url=None,
            output_json="coins_yubilej_dragmetall_2.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Юбилейные РФ (драгметаллы) часть 3",
            group="parser_yubilej_dragmetall_3",
            link_contains=(
                "/stoimost-monet/monety-rossii/yubilejnye/dragmetall/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-rossii/yubilejnye/dragmetall/"
                "page.{page}/"
            ),
            first_page=22,
            last_page=32,
            initial_url=None,
            output_json="coins_yubilej_dragmetall_3.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Ходячка РФ после деноминации",
            group="parser_regular_postdev.py",
            link_contains=(
                "/stoimost-monet/monety-rossii/hodyachka/posle-devalvacii/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-rossii/hodyachka/posle-devalvacii/"
                "page.{page}/"
            ),
            first_page=1,
            last_page=8,
            initial_url=None,
            output_json="coins_regular_postdev.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Ходячка РФ до деноминации",
            group="parser_regular_predev.py",
            link_contains=(
                "/stoimost-monet/monety-rossii/hodyachka/do-devalvacii/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-rossii/hodyachka/do-devalvacii/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_regular_predev.json",
        )
    )

    # ---- Царские монеты ----

    configs.append(
        ParserConfig(
            name="Александр I",
            group="parser_aleksandr_i",
            link_contains="/stoimost-monet/carskie-monety/aleksandr-i/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/carskie-monety/aleksandr-i/page.{page}/"
            ),
            first_page=1,
            last_page=16,
            initial_url=None,
            output_json="coins_imp_aleksandr_i.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Александр II",
            group="parser_aleksandr_ii",
            link_contains="/stoimost-monet/carskie-monety/aleksandr-ii/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/carskie-monety/aleksandr-ii/page.{page}/"
            ),
            first_page=1,
            last_page=14,
            initial_url=None,
            output_json="coins_imp_aleksandr_ii.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Александр III",
            group="parser_aleksandr_iii",
            link_contains="/stoimost-monet/carskie-monety/aleksandr-iii/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/carskie-monety/aleksandr-iii/page.{page}/"
            ),
            first_page=1,
            last_page=10,
            initial_url=None,
            output_json="coins_imp_aleksandr_iii.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Анна Иоанновна",
            group="parser_anna_ioannovna",
            link_contains="/stoimost-monet/carskie-monety/anna-ioannovna/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/carskie-monety/anna-ioannovna/page.{page}/"
            ),
            first_page=1,
            last_page=5,
            initial_url=None,
            output_json="coins_imp_anna_ioannovna.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Екатерина I",
            group="parser_ekaterina_i",
            link_contains="/stoimost-monet/carskie-monety/ekaterina-i/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/carskie-monety/ekaterina-i/page.{page}/"
            ),
            first_page=1,
            last_page=3,
            initial_url=None,
            output_json="coins_imp_ekaterina_i.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Екатерина II (продолжение)",
            group="parser_ekaterina_ii_resume",
            link_contains="/stoimost-monet/carskie-monety/ekaterina-ii/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/carskie-monety/ekaterina-ii/page.{page}/"
            ),
            first_page=11,
            last_page=21,
            initial_url=None,
            output_json="coins_imp_ekaterina_ii_resume.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Екатерина II",
            group="parser_ekaterina_ii",
            link_contains="/stoimost-monet/carskie-monety/ekaterina-ii/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/carskie-monety/ekaterina-ii/page.{page}/"
            ),
            first_page=1,
            last_page=10,
            initial_url=None,
            output_json="coins_imp_ekaterina_ii.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Елизавета",
            group="parser_elizaveta",
            link_contains="/stoimost-monet/carskie-monety/elizaveta/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/carskie-monety/elizaveta/page.{page}/"
            ),
            first_page=1,
            last_page=8,
            initial_url=None,
            output_json="coins_imp_elizaveta.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Хрущёвские новоделы СССР",
            group="parser_hrushevskie_novodely",
            link_contains="/stoimost-monet/monety-sssr/hrushevskie-novodely/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-sssr/hrushevskie-novodely/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_sssr_hrushevskie_novodely.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Иоанн Антонович",
            group="parser_ioann_antonovich",
            link_contains=(
                "/stoimost-monet/carskie-monety/ioann-antonovich/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/carskie-monety/ioann-antonovich/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_imp_ioann_antonovich.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Монетовидные жетоны",
            group="parser_monetovidnye_jetony",
            link_contains=(
                "/stoimost-monet/carskie-monety/monetovidnye-jetony/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/carskie-monety/monetovidnye-jetony/"
                "page.{page}/"
            ),
            first_page=1,
            last_page=2,
            initial_url=None,
            output_json="coins_imp_monetovidnye_jetony.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Николай I",
            group="parser_nikolaj_i",
            link_contains="/stoimost-monet/carskie-monety/nikolaj-i/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/carskie-monety/nikolaj-i/page.{page}/"
            ),
            first_page=1,
            last_page=11,
            initial_url=None,
            output_json="coins_imp_nikolaj_i.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Николай I (продолжение)",
            group="parser_nikolaj_i_resume",
            link_contains="/stoimost-monet/carskie-monety/nikolaj-i/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/carskie-monety/nikolaj-i/page.{page}/"
            ),
            first_page=12,
            last_page=23,
            initial_url=None,
            output_json="coins_imp_nikolaj_i_resume.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Николай II",
            group="parser_nikolaj_ii",
            link_contains="/stoimost-monet/carskie-monety/nikolaj-ii/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/carskie-monety/nikolaj-ii/page.{page}/"
            ),
            first_page=1,
            last_page=10,
            initial_url=None,
            output_json="coins_imp_nikolaj_ii.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Павел I",
            group="parser_pavel_i",
            link_contains="/stoimost-monet/carskie-monety/pavel-i/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/carskie-monety/pavel-i/page.{page}/"
            ),
            first_page=1,
            last_page=5,
            initial_url=None,
            output_json="coins_imp_pavel_i.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Пётр I",
            group="parser_petr_i",
            link_contains="/stoimost-monet/carskie-monety/petr-i/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/carskie-monety/petr-i/page.{page}/"
            ),
            first_page=1,
            last_page=11,
            initial_url=None,
            output_json="coins_imp_petr_i.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Пётр II",
            group="parser_petr_ii",
            link_contains="/stoimost-monet/carskie-monety/petr-ii/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/carskie-monety/petr-ii/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_imp_petr_ii.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Пётр III",
            group="parser_petr_iii",
            link_contains="/stoimost-monet/carskie-monety/petr-iii/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/carskie-monety/petr-iii/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_imp_petr_iii.json",
        )
    )

    # ---- СССР ----

    configs.append(
        ParserConfig(
            name="СССР наборы Госбанка",
            group="parser_sssr_nabory_gosbanka",
            link_contains="/stoimost-monet/monety-sssr/nabory-gosbanka/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-sssr/nabory-gosbanka/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_sssr_nabory_gosbanka.json",
        )
    )

    configs.append(
        ParserConfig(
            name="СССР Шпицберген",
            group="parser_sssr_shpicbergen",
            link_contains="/stoimost-monet/monety-sssr/shpicbergen/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-sssr/shpicbergen/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_sssr_shpicbergen.json",
        )
    )

    configs.append(
        ParserConfig(
            name="СССР ходячка (часть 1)",
            group="parser_sssr_regular",
            link_contains="/stoimost-monet/monety-sssr/hodyachka/sssr/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-sssr/hodyachka/sssr/page.{page}/"
            ),
            first_page=1,
            last_page=9,
            initial_url=None,
            output_json="coins_sssr_regular.json",
        )
    )

    configs.append(
        ParserConfig(
            name="СССР ходячка (часть 2)",
            group="parser_sssr_regular_resume",
            link_contains="/stoimost-monet/monety-sssr/hodyachka/sssr/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-sssr/hodyachka/sssr/page.{page}/"
            ),
            first_page=10,
            last_page=17,
            initial_url=None,
            output_json="coins_sssr_regular_resume.json",
        )
    )

    configs.append(
        ParserConfig(
            name="СССР юбилейные (драгметаллы, Россия-раздел)",
            group="parser_sssr_yubilej_dragmetall",
            link_contains=(
                "/stoimost-monet/monety-rossii/yubilejnye/dragmetall/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-rossii/yubilejnye/dragmetall/"
                "page.{page}/"
            ),
            first_page=1,
            last_page=4,
            initial_url=None,
            output_json="coins_sssr_yubilej_dragmetall.json",
        )
    )

    configs.append(
        ParserConfig(
            name="СССР юбилейные (недрагметаллы, Россия-раздел)",
            group="parser_sssr_yubilej_ne_dragmetall",
            link_contains=(
                "/stoimost-monet/monety-rossii/yubilejnye/ne-dragmetall/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-rossii/yubilejnye/ne-dragmetall/"
                "page.{page}/"
            ),
            first_page=1,
            last_page=11,
            initial_url=None,
            output_json="coins_sssr_yubilej_ne_dragmetall.json",
        )
    )

    configs.append(
        ParserConfig(
            name="СССР пробные монеты",
            group="parser_sssr_trial",
            link_contains="/stoimost-monet/monety-sssr/probnye/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-sssr/probnye/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_sssr_trial.json",
        )
    )

    configs.append(
        ParserConfig(
            name="СССР Тува",
            group="parser_sssr_tuva",
            link_contains="/stoimost-monet/monety-sssr/tuva/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/monety-sssr/tuva/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coint_sssr_tuva.json",
        )
    )

    # ---- Допетровские ----

    configs.append(
        ParserConfig(
            name="Допетровские — чешуя Петра Алексеевича",
            group="parser_doptr_cheshuya",
            link_contains=(
                "/stoimost-monet/dopetrovskie/cheshuya-petra-alekseevicha/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/dopetrovskie/cheshuya-petra-alekseevicha/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_doptr_cheshuya.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Допетровские — совместное правление",
            group="parser_doptr_mutual",
            link_contains=(
                "/stoimost-monet/dopetrovskie/sovmestnoe-pravlenie/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/dopetrovskie/sovmestnoe-pravlenie/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_doptr_mutual.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Допетровские — Фёдор Алексеевич",
            group="parser_doptr_fedor_a",
            link_contains="/stoimost-monet/dopetrovskie/fedor-alekseevich/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/dopetrovskie/fedor-alekseevich/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_doptr_fedor_a.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Допетровские — Алексей Михайлович",
            group="parser_doptr_aleksej_m",
            link_contains=(
                "/stoimost-monet/dopetrovskie/aleksej-mihajlovich/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/dopetrovskie/aleksej-mihajlovich/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_doptr_aleksej_m.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Допетровские — Михаил Фёдорович",
            group="parser_doptr_mihail_f",
            link_contains=(
                "/stoimost-monet/dopetrovskie/mihail-fedorovich/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/dopetrovskie/mihail-fedorovich/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_doptr_mihail_f.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Допетровские — второе земское ополчение",
            group="parser_doptr_zemskoe",
            link_contains=(
                "/stoimost-monet/dopetrovskie/vtoroe-zemskoe-opolchenie/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/dopetrovskie/vtoroe-zemskoe-opolchenie/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_doptr_zemskoe.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Допетровские — шведская оккупация Новгорода",
            group="parser_doptr_occupy",
            link_contains=(
                "/stoimost-monet/dopetrovskie/"
                "shvedskaya-okkupaciya-novgoroda/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/dopetrovskie/"
                "shvedskaya-okkupaciya-novgoroda/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_doptr_occupy.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Допетровские — Владислав Жигимонтович",
            group="parser_doptr_vlad_z",
            link_contains=(
                "/stoimost-monet/dopetrovskie/vladislav-zhigimontovich/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/dopetrovskie/vladislav-zhigimontovich/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_doptr_vlad_z.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Допетровские — Василий Иванович Шуйский",
            group="parser_doptr_vasilij_i",
            link_contains=(
                "/stoimost-monet/dopetrovskie/vasilij-ivanovich-shuiskij/"
            ),
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/dopetrovskie/"
                "vasilij-ivanovich-shuiskij/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_doptr_vasilij_i.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Допетровские — Лжедмитрий I",
            group="parser_doptr_lzhe_i",
            link_contains="/stoimost-monet/dopetrovskie/lzhedmitrij-i/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/dopetrovskie/lzhedmitrij-i/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_doptr_lzhe_i.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Допетровские — Борис Годунов",
            group="parser_doptr_boris_g",
            link_contains="/stoimost-monet/dopetrovskie/boris-godunov/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/dopetrovskie/boris-godunov/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_doptr_boris_g.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Допетровские — Фёдор Иванович",
            group="parser_doptr_fedor_i",
            link_contains="/stoimost-monet/dopetrovskie/fedor-ivanovich/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/dopetrovskie/fedor-ivanovich/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_doptr_fedor_i.json",
        )
    )

    configs.append(
        ParserConfig(
            name="Допетровские — Иван Грозный",
            group="parser_doptr_ivan_iv",
            link_contains="/stoimost-monet/dopetrovskie/ivan-groznyj/",
            list_url_template=(
                "https://www.raritetus.ru/"
                "stoimost-monet/dopetrovskie/ivan-groznyj/"
            ),
            first_page=1,
            last_page=1,
            initial_url=None,
            output_json="coins_doptr_ivan_iv.json",
        )
    )

    return configs


def build_all_parsers() -> List[Parser]:
    """
    Создаёт список объектов RaritetusParser для всех конфигураций.
    """
    configs = build_all_parser_configs()
    return [RaritetusParser(config) for config in configs]


# =============================================================================
#  Точка входа
# =============================================================================


def main() -> None:
    """
    Основная точка входа скрипта.

    Выполняет:
    1. Создание всех парсеров для raritetus.ru.
    2. Последовательный запуск парсеров и сбор их результатов.
    3. Сохранение объединённого JSON-файла.
    """
    parsers = build_all_parsers()
    orchestrator = ParserOrchestrator(parsers)

    combined_coins = orchestrator.run_all(stop_on_error=False)
    orchestrator.save_combined(combined_coins)


if __name__ == "__main__":
    main()
