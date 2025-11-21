import json
import re
import time
from typing import Dict, Any, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.raritetus.ru"
START_URL = (
    "https://www.raritetus.ru/stoimost-monet/monety-sssr/"
    "yubilejnye/ne-dragmetall/"
)
LIST_URL_FILTER = "/stoimost-monet/monety-sssr/yubilejnye/ne-dragmetall/"

OUTPUT_JSON = "coins_sssr_jubilee_nonprecious.json"

PAGES = range(1, 5)
SLEEP_BETWEEN_REQUESTS = 0.2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CoinAtlasBot/1.0)"
}

session = requests.Session()
session.headers.update(HEADERS)


def fetch_html(url: str) -> str:
    print(f"[GET] {url}")
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    time.sleep(SLEEP_BETWEEN_REQUESTS)
    return resp.text


def parse_list_page(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: List[str] = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if LIST_URL_FILTER not in href:
            continue
        if not href.endswith("/"):
            continue

        tail = href.rstrip("/").split("/")[-1]
        parts = tail.split("-")
        if not parts or not parts[-1].isdigit():
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
    if not table:
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


def _to_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def _absolute_url(src: str) -> str:
    if src.startswith("//"):
        return "https:" + src
    return urljoin(BASE_URL, src)


def _extract_description(
    soup: BeautifulSoup,
    header_text: str,
) -> str:
    h = soup.find(
        lambda tag: tag.name in ("h2", "h3", "h4")
        and header_text.lower() in tag.get_text(strip=True).lower()
    )
    if not h:
        return ""

    parts: List[str] = []
    node = h
    while True:
        node = node.find_next_sibling()
        if node is None:
            break
        if node.name in ("h2", "h3", "h4"):
            break
        text = node.get_text(" ", strip=True)
        if text:
            parts.append(text)
    return " ".join(parts)


def _parse_catalog_table(table: Optional[BeautifulSoup]) -> str:
    if not table:
        return ""
    parts: List[str] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        name = cells[0].get_text(" ", strip=True)
        val = cells[1].get_text(" ", strip=True)
        if name and val:
            parts.append(f"{name} {val}")
    return "; ".join(parts)


def parse_coin_page(url: str) -> Optional[Dict[str, Any]]:
    try:
        html = fetch_html(url)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status and 500 <= status < 600:
            print(f"  !! 5xx ошибка ({status}) для {url}, пропускаю монету")
            return None
        raise

    except Exception as e:
        print(f"  !! ошибка запроса {url}: {e}")
        return None

    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    raw_title = h1.get_text(" ", strip=True) if h1 else ""
    title = raw_title

    if title:
        low = title.lower()
        prefixes = [
            "стоимость монеты",
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
    info = _parse_two_column_table(
        _find_table_after_header(soup, "Общая информация")
    )
    chars = _parse_two_column_table(
        _find_table_after_header(soup, "Характеристики")
    )
    catalog_table = _find_table_after_header(soup, "Каталожные номера")
    nominal = info.get("Номинал") or ""
    year_str = info.get("Год")
    name = info.get("Название") or ""
    series_name = info.get("Название серии") or ""
    variety_desc = info.get("Описание разновидности") or ""
    edge = info.get("Гурт") or ""
    issue_date = info.get("Дата выпуска") or ""
    quality = info.get("Качество выпуска") or ""
    type_ = info.get("Тип") or ""
    artist = info.get("Художник") or ""
    sculptor = info.get("Скульптор") or ""
    mintage_str = info.get("Тираж")

    year = int(year_str) if year_str and year_str.isdigit() else None
    mintage = _to_int(mintage_str)
    material = chars.get("Материал") or ""
    weight = _to_float(chars.get("Вес"))
    diameter = _to_float(chars.get("Диаметр"))
    thickness = _to_float(chars.get("Толщина"))
    catalog = _parse_catalog_table(catalog_table)
    desc_obverse = _extract_description(soup, "Описание аверса")
    desc_reverse = _extract_description(soup, "Описание реверса")
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

    if image_obverse is None or image_reverse is None:
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
        "nominal": nominal,
        "year": year,
        "name": name,
        "series_name": series_name,
        "variety_description": variety_desc,
        "edge": edge,
        "type": type_,
        "material": material,
        "weight_g": weight,
        "diameter_mm": diameter,
        "thickness_mm": thickness,
        "issue_date": issue_date,
        "quality": quality,
        "artist": artist,
        "sculptor": sculptor,
        "mintage": mintage,
        "description_obverse": desc_obverse,
        "description_reverse": desc_reverse,
        "catalog": catalog,
        "image_obverse": image_obverse,
        "image_reverse": image_reverse,
    }

    return coin


def main():
    all_urls: List[str] = []

    for page in PAGES:
        page_url = f"{START_URL}page.{page}/"
        html = fetch_html(page_url)
        page_urls = parse_list_page(html)
        print(f"На странице {page_url} найдено монет: {len(page_urls)}")
        all_urls.extend(page_urls)

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

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(coins, f, ensure_ascii=False, indent=2)

    print(f"Спарсили: {len(coins)}")
    print(f"JSON: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()

