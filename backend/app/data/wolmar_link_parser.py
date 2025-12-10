"""
Парсер ссылок на завершённые аукционы Wolmar.ru (VIP и Standart).

Скрипт:
- загружает главную страницу wolmar.ru;
- находит правые блоки "VIP аукционы" и "Standart аукционы";
- вытаскивает ссылки вида https://www.wolmar.ru/auction/<id> из этих блоков,
  которые соответствуют завершённым аукционам.

Требуемые внешние зависимости:
    pip install requests beautifulsoup4
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE_URL = "https://www.wolmar.ru/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; CoinAtlasBot/1.0; +https://example.com/bot)"
    )
}


class WolmarParserError(RuntimeError):
    """Общая ошибка при работе парсера Wolmar."""


@dataclass
class AuctionLinks:
    """Результат парсинга ссылок на аукционы."""

    vip: List[str]
    standard: List[str]

    def to_dict(self) -> dict:
        """Преобразовать в словарь, удобный для сериализации в JSON."""
        return {"vip": self.vip, "standard": self.standard}


def fetch_main_page(url: str = BASE_URL) -> str:
    """
    Загрузить HTML главной страницы Wolmar.

    :param url: Базовый URL (по умолчанию https://www.wolmar.ru/).
    :return: HTML как строка.
    :raises WolmarParserError: при сетевых ошибках или неуспешном статусе ответа.
    """
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise WolmarParserError(f"Не удалось загрузить {url!r}: {exc}") from exc

    # У сайта кодировка cp1251, но requests её обычно угадывает сам.
    # На всякий случай явно устанавливаем, если сервер не указал.
    if not response.encoding:
        response.encoding = "cp1251"

    return response.text


def _extract_closed_block(soup: BeautifulSoup, heading_keyword: str) -> List[str]:
    """
    Внутренняя функция: найти блок справа с заголовком, содержащим heading_keyword,
    и вытащить из него все ссылки на аукционы.

    Например, для heading_keyword="VIP" это блок "VIP аукционы:",
    для "Standart" — блок "Standart аукционы:".

    :param soup: разобранный BeautifulSoup документ.
    :param heading_keyword: ключевое слово в заголовке блока ("VIP" или "Standart").
    :return: список абсолютных URL'ов вида https://www.wolmar.ru/auction/<id>.
    """
    results: List[str] = []

    # На сайте нужные блоки имеют классы right_box / right_box_dark.
    for div in soup.find_all("div", class_=["right_box", "right_box_dark"]):
        h2 = div.find("h2")
        if not h2:
            continue

        title = h2.get_text(" ", strip=True).lower()
        if heading_keyword.lower() not in title:
            continue

        # Внутри блока есть <a href=".../auction/NNNN">...</a> для завершённых аукционов.
        for link in div.find_all("a", href=True):
            href = urljoin(BASE_URL, link["href"])
            if "/auction/" in href:
                results.append(href)

    return results


def parse_closed_auctions(html: str) -> AuctionLinks:
    """
    Спарсить HTML главной страницы и извлечь ссылки
    на завершённые VIP и Standart аукционы.

    :param html: HTML главной страницы.
    :return: AuctionLinks с двумя списками ссылок.
    """
    soup = BeautifulSoup(html, "html.parser")

    vip_links = _extract_closed_block(soup, "VIP")
    std_links = _extract_closed_block(soup, "Standart")

    return AuctionLinks(vip=vip_links, standard=std_links)


def main() -> None:
    """
    Точка входа при запуске скрипта как программы.

    - грузит главную страницу;
    - парсит ссылки;
    - печатает краткий отчёт;
    - по желанию сохраняет результат в JSON (закомментированный пример).
    """
    html = fetch_main_page()
    auctions = parse_closed_auctions(html)

    print(f"Найдено завершённых VIP аукционов: {len(auctions.vip)}")
    print(f"Найдено завершённых Standart аукционов: {len(auctions.standard)}")

    # Если нужно сохранить результат в JSON:
    with open("wolmar_closed_auctions.json", "w", encoding="utf-8") as f:
        json.dump(auctions.to_dict(), f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
