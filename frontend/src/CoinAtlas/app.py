import toga
from toga.style import Pack
from toga.style.pack import COLUMN, ROW
import httpx

API_URL = "http://77.222.47.102:8000"


class HomeView(toga.Box):
    def __init__(self, app):
        super().__init__(direction=COLUMN, style=Pack(margin=8))
        self.app = app

        title = toga.Label(
            "CoinAtlas — Монеты России, СССР и Российской Империи",
            style=Pack(margin=(8, 8, 12, 8), font_size=18),
        )
        self.add(title)

        sections = [
            {
                "id": 0,
                "title": "Все монеты",
                "subtitle": "все записи из базы",
            },
            {
                "id": 1,
                "title": "Раздел 1 (section_id = 1)",
                "subtitle": "монеты с section_id = 1",
            },
        ]

        data = []
        for s in sections:
            data.append(
                {
                    "title": s["title"],
                    "subtitle": s["subtitle"],
                    "section_id": s["id"],
                }
            )

        self.section_list = toga.DetailedList(
            data=data,
            on_select=self.on_section_select,
            style=Pack(flex=1),
        )
        self.add(self.section_list)

    def on_section_select(self, widget):
        row = widget.selection
        if row is None:
            return

        section_id = row.section_id
        section_title = row.title
        self.app.open_section(section_id, section_title)


class SectionView(toga.Box):
    def __init__(self, app, section_id, section_title):
        super().__init__(direction=COLUMN)
        self.app = app
        self.section_id = section_id
        self.section_title = section_title
        self.coins = []

        header = toga.Box(direction=ROW, style=Pack(margin=8))
        back_btn = toga.Button(
            "← Назад",
            on_press=self.on_back,
            style=Pack(margin_right=8),
        )
        title_label = toga.Label(
            section_title,
            style=Pack(font_size=16),
        )
        header.add(back_btn)
        header.add(title_label)
        self.add(header)

        self.status_label = toga.Label(
            "Загружаю монеты...",
            style=Pack(margin=8),
        )
        self.add(self.status_label)

        self.by_year_list = toga.DetailedList(
            data=[],
            on_select=self.on_coin_select,
        )
        self.by_nominal_list = toga.DetailedList(
            data=[],
            on_select=self.on_coin_select,
        )
        self.by_series_list = toga.DetailedList(
            data=[],
            on_select=self.on_coin_select,
        )

        self.tabs = toga.OptionContainer(
            content=[
                ("ГОД", self.by_year_list),
                ("НОМИНАЛ", self.by_nominal_list),
                ("СЕРИЯ", self.by_series_list),
            ],
            style=Pack(flex=1),
        )
        self.add(self.tabs)

        self.app.add_background_task(self._load_coins_task)

    async def _load_coins_task(self, app):
        try:
            params = {}
            if self.section_id != 0:
                params["section_id"] = self.section_id

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{API_URL}/coins", params=params)
                resp.raise_for_status()
                self.coins = resp.json()
        except Exception as e:
            print("Ошибка загрузки монет:", e)
            self.coins = []
            self.status_label.text = "Не удалось загрузить монеты"
            return

        self.by_year_list.data = self._build_list_data(sort_key="year")
        self.by_nominal_list.data = self._build_list_data(sort_key="nominal")
        self.by_series_list.data = self._build_list_data(sort_key="sname")
        self.status_label.text = f"Загружено монет: {len(self.coins)}"

    def _get_year(self, coin):
        dt = coin.get("DT")
        if not dt:
            return 0
        try:
            return int(str(dt)[:4])
        except ValueError:
            return 0

    def _nominal_sort_key(self, coin):
        text = coin.get("nominal") or ""
        first = text.split()[0].replace(",", ".")
        try:
            return float(first)
        except ValueError:
            return 9999.0

    def _build_list_data(self, sort_key):
        if sort_key == "year":
            key_fn = lambda c: (self._get_year(c), c.get("nominal") or "")
        elif sort_key == "nominal":
            key_fn = lambda c: (self._nominal_sort_key(c), self._get_year(c))
        else:  # "sname"
            key_fn = lambda c: (c.get("sname") or "", self._get_year(c))

        coins_sorted = sorted(self.coins, key=key_fn)

        data = []
        for coin in coins_sorted:
            year = self._get_year(coin)
            title = coin.get("cname") or coin.get("nominal") or "Монета"
            series = coin.get("sname") or ""
            nominal = coin.get("nominal") or ""
            metal = coin.get("metal") or ""

            subtitle_parts = []
            if year:
                subtitle_parts.append(str(year))
            if nominal:
                subtitle_parts.append(nominal)
            if series:
                subtitle_parts.append(series)
            if metal:
                subtitle_parts.append(metal)

            subtitle = " • ".join(subtitle_parts)

            data.append(
                {
                    "title": title,
                    "subtitle": subtitle,
                    "coin_id": coin["id"],
                }
            )

        return data

    def on_back(self, widget):
        self.app.show_home()

    def on_coin_select(self, widget):
        row = widget.selection
        if row is None:
            return

        coin_id = row.coin_id
        coin_obj = next((c for c in self.coins if c["id"] == coin_id), None)
        if coin_obj is not None:
            self.app.open_coin(self.section_id, self.section_title, coin_obj)


class CoinDetailView(toga.Box):
    def __init__(self, app, section_id, section_title, coin):
        super().__init__(direction=COLUMN)
        self.app = app
        self.section_id = section_id
        self.section_title = section_title
        self.coin = coin

        header = toga.Box(direction=ROW, style=Pack(margin=8))
        back_btn = toga.Button(
            "← Назад",
            on_press=self.on_back,
            style=Pack(margin_right=8),
        )
        title_label = toga.Label(
            coin.get("cname") or coin.get("nominal") or "Монета",
            style=Pack(font_size=16),
        )
        header.add(back_btn)
        header.add(title_label)
        self.add(header)

        images_box = toga.Box(
            direction=ROW,
            style=Pack(margin=(0, 8, 8, 8)),
        )
        obv = self._image_placeholder("Аверс")
        rev = self._image_placeholder("Реверс")
        images_box.add(obv)
        images_box.add(rev)
        self.add(images_box)

        specs_box = self._build_specs_tab()
        catalogs_box = self._build_catalogs_tab()
        prices_box = self._build_prices_tab()
        sales_box = self._build_sales_tab()
        mycoins_box = self._build_mycoins_tab()

        tabs = toga.OptionContainer(
            content=[
                ("Характеристики", specs_box),
                ("Каталоги", catalogs_box),
                ("Цены", prices_box),
                ("Аукционы", sales_box),
                ("Мои монеты", mycoins_box),
            ],
            style=Pack(flex=1),
        )
        self.add(tabs)

    def _image_placeholder(self, label_text):
        box = toga.Box(
            style=Pack(
                flex=1,
                height=120,
                margin=10,
                background_color="#EEEEEE",
                align_items="center",
            )
        )
        box.add(
            toga.Label(
                label_text,
                style=Pack(font_size=12),
            )
        )
        return box

    def _build_specs_tab(self):
        coin = self.coin

        dt_text = coin.get("DT") or ""
        if dt_text:
            dt_display = dt_text[:10]
            year = dt_text[:4]
        else:
            dt_display = "—"
            year = ""

        rows = [
            ("Раздел", self.section_title),
            ("ID монеты (БД)", str(coin.get("id"))),
            ("section_id", str(coin.get("section_id"))),
            ("Дата выпуска", dt_display),
            ("Год", year),
            ("Номинал", coin.get("nominal") or ""),
            ("Серия", coin.get("sname") or "—"),
            ("Металл", coin.get("metal") or "—"),
        ]

        box = toga.Box(direction=COLUMN, style=Pack(margin=10))
        for name, value in rows:
            row = toga.Box(direction=ROW, style=Pack(margin_bottom=4))
            row.add(toga.Label(name + ":", style=Pack(width=130)))
            row.add(toga.Label(value))
            box.add(row)

        return box

    def _build_catalogs_tab(self):
        fake_catalogs = [
            {"catalog": "ЦБ РФ", "code": "№ (заглушка)"},
            {"catalog": "Конрос", "code": "—"},
            {"catalog": "Федорин", "code": "—"},
        ]
        data = [{"title": c["catalog"], "subtitle": c["code"]} for c in fake_catalogs]
        return toga.DetailedList(data=data)

    def _build_prices_tab(self):
        box = toga.Box(direction=COLUMN, style=Pack(margin=10))
        box.add(
            toga.Label(
                "Средняя цена за последний год: — ₽ (заглушка)",
                style=Pack(margin_bottom=8),
            )
        )
        box.add(
            toga.Label(
                "Здесь будет график динамики цен по годам.",
                style=Pack(margin_bottom=8),
            )
        )
        box.add(
            toga.Label(
                "Данные будут браться из парсинга wolmar/meshok.",
                style=Pack(color="#666666", font_size=11),
            )
        )
        return box

    def _build_sales_tab(self):
        fake_sales = [
            {
                "source": "wolmar",
                "title": "XF, без дефектов",
                "price": 220,
                "currency": "RUB",
                "sale_date": "15.03.2024",
            },
            {
                "source": "meshok",
                "title": "VF, есть следы обращения",
                "price": 180,
                "currency": "RUB",
                "sale_date": "01.02.2024",
            },
        ]

        data = []
        for s in fake_sales:
            title = f"{s['source']} • {s['price']} {s['currency']}"
            subtitle = f"{s['sale_date']} • {s['title']}"
            data.append({"title": title, "subtitle": subtitle})

        return toga.DetailedList(data=data)

    def _build_mycoins_tab(self):
        box = toga.Box(direction=COLUMN, style=Pack(margin=10))

        box.add(
            toga.Label(
                "Учёт моих экземпляров (пока без API):",
                style=Pack(margin_bottom=8),
            )
        )

        qty_label = toga.Label("Количество:", style=Pack(margin_bottom=4))
        qty_input = toga.NumberInput(
            min=0,
            max=999,
            step=1,
            value=0,
            style=Pack(width=100, margin_bottom=8),
        )

        note_label = toga.Label("Заметка:", style=Pack(margin_bottom=4))
        note_input = toga.MultilineTextInput(
            style=Pack(flex=1, height=80, margin_bottom=8),
        )

        status_label = toga.Label("", style=Pack(margin_top=4))

        def on_save(widget):
            qty = qty_input.value if qty_input.value is not None else 0
            note = note_input.value or ""
            status_label.text = (
                f"Сохранено (локальная заглушка): {qty} шт., заметка: {note[:30]}"
            )

        save_btn = toga.Button(
            "Сохранить (локально)",
            on_press=on_save,
            style=Pack(margin=(4, 0, 0, 0)),
        )

        box.add(qty_label)
        box.add(qty_input)
        box.add(note_label)
        box.add(note_input)
        box.add(save_btn)
        box.add(status_label)

        return box

    def on_back(self, widget):
        self.app.open_section(self.section_id, self.section_title)


class CoinAtlas(toga.App):
    def startup(self):
        self.main_window = toga.MainWindow(title=self.formal_name)
        self.show_home()
        self.main_window.show()

    def show_home(self):
        self.current_view = HomeView(app=self)
        self.main_window.content = self.current_view

    def open_section(self, section_id, section_title):
        self.current_view = SectionView(
            app=self,
            section_id=section_id,
            section_title=section_title,
        )
        self.main_window.content = self.current_view

    def open_coin(self, section_id, section_title, coin):
        self.current_view = CoinDetailView(
            app=self,
            section_id=section_id,
            section_title=section_title,
            coin=coin,
        )
        self.main_window.content = self.current_view


def main():
    return CoinAtlas("CoinAtlas", "com.example.coinatlas")

