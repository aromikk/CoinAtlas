import json
import re
import time
from typing import Dict, Any, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.raritetus.ru"
LIST_BASE = (
    "https://www.raritetus.ru/stoimost-monet/carskie-monety/nikolaj-ii/"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; CoinAtlasBot/1.0; +https://example.com/bot)"
    )
}

session = requests.Session()
session.headers.update(HEADERS)


def fetch_html(url: str) -> str:
    print(f"[GET] {url}")
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    return resp.text


def parse_list_page(html: str) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    urls: List[str] = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/stoimost-monet/carskie-monety/nikolaj-ii/" not in href:
            continue
        if not href.endswith("/"):
            continue

        tail = href.rstrip("/").split("/")[-1]
        parts = tail.split("-")
        if not parts:
            continue
        if not parts[-1].isdigit():
            continue

        full = urljoin(LIST_BASE, href)
        if full not in seen:
            seen.add(full)
            urls.append(full)

    return urls


def find_table_after_header(
    soup: BeautifulSoup,
    header_text: str,
) -> Optional[BeautifulSoup]:
    header = soup.find(
        lambda tag: tag.name in ("h2", "h3", "h4")
        and header_text.lower() in tag.get_text(strip=True).lower()
    )
    if not header:
        return None

    node = header
    while True:
        node = node.find_next_sibling()
        if node is None:
            break
        if node.name == "table":
            return node
        tbl = node.find("table") if hasattr(node, "find") else None
        if tbl:
            return tbl
        if node.name in ("h2", "h3", "h4"):
            break

    return None


def parse_two_column_table(table: BeautifulSoup) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if table is None:
        return result

    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        label = cells[0].get_text(" ", strip=True)
        value = cells[1].get_text(" ", strip=True)
        if label:
            result[label] = value
    return result


def to_float(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    m = re.search(r"([\d.,]+)", value)
    if not m:
        return None
    s = (
        m.group(1)
        .replace(" ", "")
        .replace("\u00a0", "")
        .replace(",", ".")
    )
    try:
        return float(s)
    except ValueError:
        return None


def absolute_url(src: str) -> str:
    if src.startswith("//"):
        return "https:" + src
    return urljoin(BASE_URL, src)


def parse_coin_page(url: str) -> Optional[Dict[str, Any]]:
    try:
        html = fetch_html(url)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        print(f"  !! HTTPError {status} для {url}, пропускаю монету")
        return None
    except Exception as e:
        print(f"  !! ошибка сети для {url}: {e}")
        return None

    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    info_table = parse_two_column_table(
        find_table_after_header(soup, "Общая информация")
    )
    char_table = parse_two_column_table(
        find_table_after_header(soup, "Характеристики")
    )
    catalog_table = find_table_after_header(soup, "Каталожные номера")

    nominal = info_table.get("Номинал")
    year_str = info_table.get("Год")
    letters = info_table.get("Буквы")
    edge = info_table.get("Гурт")
    quality = info_table.get("Качество выпуска")
    ruler = info_table.get("Правитель")
    mintage = info_table.get("Тираж")

    material = char_table.get("Материал")
    weight_str = char_table.get("Вес")
    diameter_str = char_table.get("Диаметр")
    thickness_str = char_table.get("Толщина")

    year = int(year_str) if year_str and year_str.isdigit() else None
    weight = to_float(weight_str)
    diameter = to_float(diameter_str)
    thickness = to_float(thickness_str)
    catalogs_parts: List[str] = []
    if catalog_table is not None:
        for tr in catalog_table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            label = cells[0].get_text(" ", strip=True)
            value = cells[1].get_text(" ", strip=True)
            if label and value:
                catalogs_parts.append(f"{label} {value}")
    catalogs = "; ".join(catalogs_parts) if catalogs_parts else None
    image_obverse = None
    image_reverse = None

    for img in soup.find_all("img", src=True):
        src = img["src"]
        if "storage/coins" not in src:
            continue
        if "avers" in src and image_obverse is None:
            image_obverse = absolute_url(src)
        elif "revers" in src and image_reverse is None:
            image_reverse = absolute_url(src)

    if image_obverse is None or image_reverse is None:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "storage/coins" not in href:
                continue
            if "avers" in href and image_obverse is None:
                image_obverse = absolute_url(href)
            elif "revers" in href and image_reverse is None:
                image_reverse = absolute_url(href)

    coin: Dict[str, Any] = {
        "source_url": url,
        "title": title,
        "nominal": nominal or "",
        "year": year,
        "letters": letters or "",
        "edge": edge or "",
        "quality": quality or "",
        "ruler": ruler or "",
        "mintage": mintage or "",
        "material": material or "",
        "weight_g": weight,
        "diameter_mm": diameter,
        "thickness_mm": thickness,
        "catalogs": catalogs,
        "image_obverse": image_obverse,
        "image_reverse": image_reverse,
    }

    return coin


def main():
    all_urls: List[str] = []
    for page in range(1, 10):
        page_url = f"{LIST_BASE}page.{page}/"
        html = fetch_html(page_url)
        page_urls = parse_list_page(html)
        print(f"На странице {page_url} найдено монет: {len(page_urls)}")
        all_urls.extend(page_urls)
        time.sleep(0.2)

    all_urls = sorted(set(all_urls))
    print(f"Всего уникальных ссылок на монеты: {len(all_urls)}")

    coins: List[Dict[str, Any]] = []
    for idx, url in enumerate(all_urls, start=1):
        print(f"\n[{idx}/{len(all_urls)}] Парсим {url}")
        coin = parse_coin_page(url)
        if coin is None:
            print(f"!! Монета пропущена: {url}")
        else:
            coins.append(coin)
        time.sleep(0.2)

    with open("coins_nikolai_ii.json", "w", encoding="utf-8") as f:
        json.dump(coins, f, ensure_ascii=False, indent=2)

    print(f"Успешно спарсили: {len(coins)}")


if __name__ == "__main__":
    main()

