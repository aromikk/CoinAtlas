import json
import re
import time
from typing import Dict, Any, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.raritetus.ru"
START_URL = "https://www.raritetus.ru/stoimost-monet/monety-sssr/yubilejnye/dragmetall/"
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
        if "/stoimost-monet/monety-sssr/yubilejnye/dragmetall/" not in href:
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


def _to_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    m = re.search(r"([\d \u00a0]+)", value)
    if not m:
        return None
    s = m.group(1).replace(" ", "").replace("\u00a0", "")
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _absolute_url(src: str) -> str:
    if src.startswith("//"):
        return "https:" + src
    return urljoin(BASE_URL, src)


def _clean_title(raw_title: str) -> str:
    title = raw_title or ""
    if not title:
        return title

    low = title.lower()
    prefixes = [
        "стоимость монеты",
        "стоимость  монеты",
        "стоимость монет",
        "стоимость  монет",
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

    return title.lstrip(":-— ").strip()


def _find_text_block_after_header(
    soup: BeautifulSoup,
    header_text: str,
) -> Optional[str]:
    header = soup.find(
        lambda tag: tag.name in ("h2", "h3", "h4")
        and header_text.lower() in tag.get_text(strip=True).lower()
    )
    if not header:
        return None

    parts: List[str] = []
    node = header
    while True:
        node = node.find_next_sibling()
        if node is None:
            break
        if node.name in ("h2", "h3", "h4"):
            break
        text = node.get_text(" ", strip=True)
        if text:
            parts.append(text)

    return "\n\n".join(parts) if parts else None


def parse_coin_page_once(url: str) -> Dict[str, Any]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    raw_title = h1.get_text(" ", strip=True) if h1 else ""
    title = _clean_title(raw_title)
    info_table = _parse_two_column_table(
        _find_table_after_header(soup, "Общая информация")
    )
    char_table = _parse_two_column_table(
        _find_table_after_header(soup, "Характеристики")
    )
    catalog_table_node = _find_table_after_header(soup, "Каталожные номера")
    nominal = info_table.get("Номинал")
    year_str = info_table.get("Год")
    name = info_table.get("Название")
    series_name = info_table.get("Название серии")
    edge = info_table.get("Гурт")
    issue_date = info_table.get("Дата выпуска")
    quality = info_table.get("Качество выпуска")
    type_ = info_table.get("Тип")
    artist = info_table.get("Художник")
    sculptor = info_table.get("Скульптор")
    mintage_str = info_table.get("Тираж")

    year = int(year_str) if year_str and year_str.isdigit() else None
    mintage = _to_int(mintage_str)
    material = char_table.get("Материал")
    weight = _to_float(char_table.get("Вес"))
    diameter = _to_float(char_table.get("Диаметр"))
    thickness = _to_float(char_table.get("Толщина"))
    catalog_items: List[str] = []
    if catalog_table_node is not None:
        for tr in catalog_table_node.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            label = cells[0].get_text(" ", strip=True)
            value = cells[1].get_text(" ", strip=True)
            if label and value:
                catalog_items.append(f"{label} {value}".strip())
    catalog = "; ".join(catalog_items) if catalog_items else None
    obverse_desc = _find_text_block_after_header(soup, "Описание аверса")
    reverse_desc = _find_text_block_after_header(soup, "Описание реверса")
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
        "name": name or "",
        "series_name": series_name or "",
        "edge": edge or "",
        "issue_date": issue_date or "",
        "quality": quality or "",
        "type": type_ or "",
        "artist": artist or "",
        "sculptor": sculptor or "",
        "mintage": mintage,
        "material": material or "",
        "weight_g": weight,
        "diameter_mm": diameter,
        "thickness_mm": thickness,
        "catalog": catalog,
        "obverse_description": obverse_desc,
        "reverse_description": reverse_desc,
        "image_obverse": image_obverse,
        "image_reverse": image_reverse,
    }

    return coin


def parse_coin_page(
    url: str,
    max_attempts: int = 5,
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
    for page in range(1, 4):
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
        coin = parse_coin_page(url, max_attempts=5)
        if coin is None:
            print(f"!! Монета пропущена: {url}")
        else:
            coins.append(coin)
        time.sleep(0.2)

    out_path = "coins_sssr_yubilej_dragmetall.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(coins, f, ensure_ascii=False, indent=2)

    print(f"Готово. Успешно спарсили монет: {len(coins)}")
    print(f"Результат сохранён в {out_path}")


if __name__ == "__main__":
    main()

