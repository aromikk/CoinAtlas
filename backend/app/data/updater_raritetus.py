"""
Updater for raritetus parsers.

Сценарий:
- обновляет только три раздела, в которых появляются новые монеты:
  * parser_yubilej_dragmetall_1
  * parser_yubilej_ne_dragmetall
  * parser_regular_postdev.py
- для каждого:
  * берёт первую страницу списка монет;
  * собирает ссылки монет;
  * смотрит, какие из них уже есть в локальном JSON (по source_url);
  * парсит только новые монеты (до первого совпадения);
  * дописывает новые монеты в начало JSON.

Дополнительно:
- после обновления отдельных файлов заново собирает
  слитный JSON из всех parser-JSON'ов, известных основному парсеру.

Запуск:
    python updater.py

Перед запуском:
    1) Убедись, что total_parser.py лежит в той же директории.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from total_parser import (
    ParserConfig,
    RaritetusParser,
    build_all_parser_configs,
)

BASE_URL = "https://www.raritetus.ru"
USER_AGENT = (
    "Mozilla/5.0 (compatible; CoinAtlasUpdater/1.0; +https://example.com/bot)"
)
HTTP_TIMEOUT = 20.0
REQUEST_SLEEP = 0.2

# Имя общего слитного JSON
COMBINED_OUTPUT_JSON = "coins_all.json"


# ---------------------------------------------------------------------------
# Вспомогательные утилиты
# ---------------------------------------------------------------------------


class LinkCollector(HTMLParser):
    """Простейший HTML-парсер, собирающий все href из тегов <a>."""

    def __init__(self) -> None:
        super().__init__()
        self.links: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str]]) -> None:
        if tag != "a":
            return
        href: Optional[str] = None
        for name, value in attrs:
            if name == "href":
                href = value
                break
        if href:
            self.links.append(href)


def fetch_html(url: str) -> str:
    """
    Получить HTML по URL с минимальными заголовками.

    Raises:
        HTTPError, URLError при сетевых проблемах.
    """
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        data = resp.read()
    return data.decode("utf-8", errors="replace")


def parse_list_page(html: str, link_contains: str) -> List[str]:
    """
    Разбор страницы со списком монет.

    Логика:
    - берём все <a href="...">;
    - фильтруем по подстроке link_contains;
    - оставляем только ссылки, заканчивающиеся на ...-<id>/, где <id> — цифры;
    - возвращаем абсолютные URL, в порядке появления.
    """
    parser = LinkCollector()
    parser.feed(html)

    urls: List[str] = []
    seen: Set[str] = set()

    for href in parser.links:
        if link_contains not in href:
            continue
        if not href.endswith("/"):
            continue

        tail = href.rstrip("/").split("/")[-1]
        parts = tail.split("-")
        if not parts or not parts[-1].isdigit():
            continue

        full = urljoin(BASE_URL, href)
        if full not in seen:
            seen.add(full)
            urls.append(full)

    return urls


def load_existing_coins(path: str) -> Tuple[List[Dict[str, Any]], Set[str]]:
    """
    Загрузить существующий JSON с монетами.

    Возвращает:
        (coins, known_urls), где known_urls — множество всех source_url.
    """
    coins: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        print(f"[INFO] Файл {path!r} не найден, считаем, что он пустой.")
        return [], set()

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, list):
            coins = [c for c in raw if isinstance(c, dict)]
        else:
            print(f"[WARN] Файл {path!r} не содержит список, игнорирую.")
            coins = []
    except json.JSONDecodeError as exc:
        print(f"[WARN] Не удалось распарсить JSON {path!r}: {exc}")
        coins = []

    known: Set[str] = set()
    for coin in coins:
        url = coin.get("source_url")
        if isinstance(url, str):
            known.add(url)

    return coins, known


def atomic_write_json(path: str, data: Iterable[Dict[str, Any]]) -> None:
    """
    Надёжная запись JSON: сначала во временный файл, затем os.replace.

    Это уменьшает риск испортить основной файл при сбое записи.
    """
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(list(data), f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


@dataclass
class UpdateResult:
    """Результат обновления одного конфига."""

    config: ParserConfig
    added_coins: List[Dict[str, Any]]

    @property
    def added_count(self) -> int:
        """Количество добавленных монет."""
        return len(self.added_coins)


# ---------------------------------------------------------------------------
# Логика обновления
# ---------------------------------------------------------------------------

TARGET_GROUPS: Set[str] = {
    "parser_yubilej_dragmetall_1",
    "parser_yubilej_ne_dragmetall",
    "parser_regular_postdev.py",
}


def first_page_url(config: ParserConfig) -> str:
    """
    Построить URL первой страницы списка для данного конфига.

    Если в шаблоне есть "{page}", подставляем page=1,
    иначе считаем, что шаблон уже указывает на первую страницу.
    """
    template = config.list_url_template
    if "{page}" in template:
        return template.format(page=1)
    return template


def update_config(config: ParserConfig) -> UpdateResult:
    """
    Обновить один конфиг:
    - загрузить существующий JSON;
    - спарсить первую страницу;
    - добавить новые монеты до первой уже известной.

    Возвращает:
        UpdateResult с добавленными монетами.
    """
    print("\n" + "=" * 72)
    print(f"[UPDATER] Обновление конфига: {config.name} ({config.group})")
    print(f"  JSON-файл: {config.output_json}")

    coins_old, known_urls = load_existing_coins(config.output_json)
    print(f"  Уже есть монет: {len(coins_old)}")

    url = first_page_url(config)
    print(f"  Первая страница списка: {url}")

    try:
        html = fetch_html(url)
    except (HTTPError, URLError) as exc:
        print(f"[ERROR] Не удалось скачать список {url!r}: {exc}")
        return UpdateResult(config=config, added_coins=[])

    list_urls = parse_list_page(html, config.link_contains)
    print(f"  Найдено ссылок на монеты на странице: {len(list_urls)}")

    new_coins: List[Dict[str, Any]] = []

    # создаём парсер, чтобы использовать его метод _parse_coin_page
    parser = RaritetusParser(config)

    for coin_url in list_urls:
        if coin_url in known_urls:
            print(f"  Встретили уже известную монету: {coin_url}")
            print("  → считаем, что новые монеты закончились.")
            break

        print(f"  [NEW] Парсим новую монету: {coin_url}")
        try:
            coin_html = fetch_html(coin_url)
            coin_obj = parser._parse_coin_page(coin_url, coin_html)
            # --- КРИТИЧЕСКОЕ МЕСТО: приводим CoinRecord к dict ---
            if isinstance(coin_obj, dict):
                coin_dict: Dict[str, Any] = coin_obj
            elif hasattr(coin_obj, "to_dict"):
                coin_dict = coin_obj.to_dict()  # type: ignore[assignment]
            else:
                coin_dict = dict(coin_obj.__dict__)
        except Exception as exc:  # noqa: BLE001
            print(f"    [WARN] Ошибка парсинга {coin_url!r}: {exc}")
            continue

        new_coins.append(coin_dict)
        time.sleep(REQUEST_SLEEP)

    if not new_coins:
        print("  Новых монет не найдено.")
        return UpdateResult(config=config, added_coins=[])

    print(f"  Добавляем новых монет: {len(new_coins)}")

    # новые монеты кладём в начало списка
    updated_coins = list(new_coins) + coins_old
    atomic_write_json(config.output_json, updated_coins)

    print(f"  JSON {config.output_json!r} успешно обновлён.")
    return UpdateResult(config=config, added_coins=new_coins)


def select_target_configs(all_configs: List[ParserConfig]) -> List[ParserConfig]:
    """
    Оставить только те конфиги, которые могут обновляться на сайте.

    Сейчас это:
    - parser_yubilej_dragmetall_1
    - parser_yubilej_ne_dragmetall
    - parser_regular_postdev.py
    """
    targets = [cfg for cfg in all_configs if cfg.group in TARGET_GROUPS]
    print("[INFO] Конфиги, подлежащие обновлению:")
    for cfg in targets:
        print(f"  - {cfg.name} ({cfg.group}) → {cfg.output_json}")
    return targets


# ---------------------------------------------------------------------------
# Сборка общего слитного JSON
# ---------------------------------------------------------------------------


def rebuild_combined_json(
    all_configs: List[ParserConfig],
    combined_path: str = COMBINED_OUTPUT_JSON,
) -> None:
    """
    Собрать новый слитный JSON из всех parser-JSON'ов.

    Берём ВСЕ конфиги основного парсера, читаем их output_json (если есть),
    склеиваем в один список и убираем дубликаты по source_url.

    Затем перезаписываем combined_path.
    """
    print("\n" + "=" * 72)
    print("[COMBINER] Пересборка общего слитного JSON")

    all_coins: List[Dict[str, Any]] = []
    seen_urls: Set[str] = set()
    total_files = 0

    for cfg in all_configs:
        path = cfg.output_json
        if not os.path.exists(path):
            print(f"[COMBINER] Файл {path!r} отсутствует, пропускаем.")
            continue

        total_files += 1
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except json.JSONDecodeError as exc:
            print(f"[COMBINER] Ошибка JSON в {path!r}: {exc}, пропускаем.")
            continue

        if not isinstance(raw, list):
            print(f"[COMBINER] {path!r} не содержит список, пропускаем.")
            continue

        added_from_file = 0
        for item in raw:
            if not isinstance(item, dict):
                continue
            url = item.get("source_url")
            if isinstance(url, str):
                if url in seen_urls:
                    continue
                seen_urls.add(url)
            all_coins.append(item)
            added_from_file += 1

        print(
            f"[COMBINER] Файл {path!r}: монет {added_from_file}, "
            f"всего теперь {len(all_coins)}"
        )

    print(
        f"[COMBINER] Обработано файлов: {total_files}, итоговых монет: "
        f"{len(all_coins)}"
    )

    atomic_write_json(combined_path, all_coins)
    print(f"[COMBINER] Слитный JSON перезаписан: {combined_path!r}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    """Точка входа для запуска updater-а."""
    print("[UPDATER] Старт обновления монет raritetus")

    all_configs = build_all_parser_configs()
    target_configs = select_target_configs(all_configs)

    all_new: List[Dict[str, Any]] = []
    for cfg in target_configs:
        result = update_config(cfg)
        all_new.extend(result.added_coins)

    print("\n" + "=" * 72)
    print("[UPDATER] Обновление отдельных JSON завершено.")
    print(f"Всего добавлено новых монет: {len(all_new)}")

    # Сразу после обновления пересобираем общий слитный JSON
    rebuild_combined_json(all_configs, COMBINED_OUTPUT_JSON)

    # Дополнительно файл только с новыми монетами за этот запуск
    if all_new:
        combined_updates_path = "coins_all.json"
        atomic_write_json(combined_updates_path, all_new)
        print(
            f"[UPDATER] Сводный JSON только с новыми монетами: "
            f"{combined_updates_path!r}"
        )

    print("\n[UPDATER] Готово.")


if __name__ == "__main__":
    main()
