import json
import re
import time
from typing import Dict, Any, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.raritetus.ru"
START_URL = "https://www.raritetus.ru/stoimost-monet/monety-sssr/hodyachka/sssr/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CoinAtlasBot/1.0; +https://example.com/bot)"
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
        if "/stoimost-monet/monety-sssr/hodyachka/sssr/" not in href:
            continue
        if not href.endswith("/"):
            continue

        tail = href.rstrip("/").split("/")[-1]
        parts = tail.split("-")
        if not parts:
            continue
        if not parts[-1].isdigit():
            continue

        full = urljoin(START_URL, href)
        if full not in seen:
            seen.add(full)
            urls.append(full)

    return urls


def _find_table_after_header(
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


def _parse_two_column_table(table: Optional[BeautifulSoup]) -> Dict[str, str]:
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


def _to_float(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    m = re.search(r"([\d.,]+)", value)
    if not m:
        return None
    s = m.group(1).replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _absolute_url(src: str) -> str:
    if src.startswith("//"):
        return "https:" + src
    return urljoin(BASE_URL, src)


def _collect_section_text(soup: BeautifulSoup, header_text: str) -> str:
    header = soup.find(
        lambda tag: tag.name in ("h2", "h3", "h4")
        and header_text.lower() in tag.get_text(strip=True).lower()
    )
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
        text = node.get_text(" ", strip=True) if hasattr(node, "get_text") else ""
        if text:
            chunks.append(text)

    return " ".join(chunks)


def parse_coin_page_once(url: str) -> Dict[str, Any]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    raw_title = h1.get_text(" ", strip=True) if h1 else ""
    title = raw_title

    if title:
        low = title.lower()
        prefixes = [
            "стоимость монеты",
            "стоимость  монеты",
            "стоимость  монет",
            "стоимость монет",
            "стоимость",
            "цена монеты",
            "цена",
        ]
        for pref in prefixes:
            if low.startswith(pref):
                title = title[len(pref):].strip()
                break

        if title.lower().startswith("монеты "):
            title = title[7:].strip()

        title = title.lstrip(":-— ").strip()
    info_table = _parse_two_column_table(
        _find_table_after_header(soup, "Общая информация")
    )
    char_table = _parse_two_column_table(
        _find_table_after_header(soup, "Характеристики")
    )
    catalog_table_node = _find_table_after_header(soup, "Каталожные номера")

    nominal = info_table.get("Номинал")
    year_str = info_table.get("Год")
    edge = info_table.get("Гурт")
    type_ = info_table.get("Тип")

    material = char_table.get("Материал")
    weight_str = char_table.get("Вес")
    diameter_str = char_table.get("Диаметр")
    thickness_str = char_table.get("Толщина")

    year = int(year_str) if year_str and year_str.isdigit() else None
    weight = _to_float(weight_str)
    diameter = _to_float(diameter_str)
    thickness = _to_float(thickness_str)

    catalog_entries: List[str] = []
    if catalog_table_node is not None:
        for tr in catalog_table_node.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            label = cells[0].get_text(" ", strip=True)
            value = cells[1].get_text(" ", strip=True)
            if not value:
                continue
            entry = f"{label} {value}".strip()
            catalog_entries.append(entry)

    catalog = "; ".join(catalog_entries) if catalog_entries else None

    obverse_description = _collect_section_text(soup, "Описание аверса")
    reverse_description = _collect_section_text(soup, "Описание реверса")

    image_obverse = None
    image_reverse = None

    for img in soup.find_all("img", src=True):
        src = img["src"]
        if "storage/coins" not in src:
            continue
        if "avers" in src and image_obverse is None:
            image_obverse = _absolute_url(src)
        elif "revers" in src and image_reverse is None:
            image_reverse = _absolute_url(src)

    if image_reverse is None or image_obverse is None:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "storage/coins" not in href:
                continue
            if "avers" in href and image_obverse is None:
                image_obverse = _absolute_url(href)
            elif "revers" in href and image_reverse is None:
                image_reverse = _absolute_url(href)

    coin: Dict[str, Any] = {
        "source_url": url,
        "title": title or raw_title,
        "nominal": nominal or "",
        "year": year,
        "type": type_ or "",
        "edge": edge or "",
        "material": material or "",
        "weight_g": weight,
        "diameter_mm": diameter,
        "thickness_mm": thickness,
        "image_obverse": image_obverse,
        "image_reverse": image_reverse,
        "catalog": catalog,
        "obverse_description": obverse_description or None,
        "reverse_description": reverse_description or None,
    }

    return coin


def parse_coin_page(
    url: str,
    max_attempts: int = 15,
) -> Optional[Dict[str, Any]]:
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"  -> попытка #{attempt} для {url}")
            return parse_coin_page_once(url)

        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            print(f"  !! HTTPError {status} на попытке {attempt}: {e}")
            if status is not None and 500 <= status < 600:
                print("SKIP")
                return None

            time.sleep(0.2)

        except Exception as e:
            print(f"  !! ошибка парсинга {url} на попытке {attempt}: {e}")
            time.sleep(0.2)

    print("SKIP.LIMIT")
    return None


def main():
    all_urls: List[str] = []
    for page in range(1, 17):
        if page == 1:
            page_url = f"{START_URL}page.1/"
        else:
            page_url = f"{START_URL}page.{page}/"

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
        coin = parse_coin_page(url, max_attempts=15)
        if coin is None:
            print(f"!! Монета пропущена: {url}")
        else:
            coins.append(coin)
        time.sleep(0.2)

    with open("coins_sssr_regular.json", "w", encoding="utf-8") as f:
        json.dump(coins, f, ensure_ascii=False, indent=2)

    print(f"Cпарсили: {len(coins)}")


if __name__ == "__main__":
    main()

