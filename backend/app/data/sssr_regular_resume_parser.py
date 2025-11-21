import json
import time
from typing import List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from parser import (
    START_URL,
    fetch_html,
    parse_list_page,
    parse_coin_page,
)

JSON_PATH = "coins_sssr_regular.json"


def load_existing_coins():
    try:
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            coins = json.load(f)
    except FileNotFoundError:
        coins = []
    return coins


def collect_all_urls() -> List[str]:
    all_urls = []

    for page in range(1, 17):
        page_url = f"{START_URL}page.{page}/"
        html = fetch_html(page_url)
        page_urls = parse_list_page(html)
        print(f"{page_url}: {len(page_urls)}")
        all_urls.extend(page_urls)
        time.sleep(1.0)

    all_urls = sorted(set(all_urls))
    print(f"Всего уникальных ссылок на монеты: {len(all_urls)}")
    return all_urls


def main():
    coins = load_existing_coins()
    parsed_urls = {c["source_url"] for c in coins if "source_url" in c}

    all_urls = collect_all_urls()
    to_parse = [u for u in all_urls if u not in parsed_urls]

    print(f"{len(to_parse)}")

    for idx, url in enumerate(to_parse, start=1):
        print(f"\n[{idx}/{len(to_parse)}] Парсим {url}")
        coin = parse_coin_page(url, max_attempts=5)
        if coin is None:
            print("SKIP")
        else:
            coins.append(coin)
        time.sleep(1.0)

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(coins, f, ensure_ascii=False, indent=2)

    print(f"Tеперь монет: {len(coins)}")


if __name__ == "__main__":
    main()

