import json
from pathlib import Path
from typing import List, Tuple

from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Section, Coin


JSON_SOURCES: List[Tuple[str, str, str]] = [
    ("data/coins_sssr_regular.json", "sssr_regular", "СССР, регулярный чекан"),
    ("data/coins_sssr_yub_dragmetall.json", "sssr_yub_drag", "СССР, юбилейные из драгоценных металлов"),
    ("data/coins_sssr_yub_ne_dragmetall.json", "sssr_yub_ne_drag", "СССР, юбилейные из недрагоценных металлов"),
    ("data/coins_nikolai_ii.json", "nikolai_ii", "Монеты Николая II"),
]


def get_or_create_section(db: Session, slug: str, name: str) -> Section:
    section = db.query(Section).filter(Section.slug == slug).first()
    if section:
        return section

    section = Section(slug=slug, name=name)
    db.add(section)
    db.commit()
    db.refresh(section)
    return section


def import_file(db: Session, path: Path, section: Section) -> int:
    if not path.exists():
        print(f"Файл {path} не найден, пропускаю.")
        return 0

    with path.open("r", encoding="utf-8") as f:
        items = json.load(f)

    imported = 0
    for item in items:
        source_url = item.get("source_url")
        if not source_url:
            continue

        exists = (
            db.query(Coin)
            .filter(Coin.source_url == source_url)
            .first()
        )
        if exists:
            continue

        coin = Coin(
            section_id=section.id,
            source_url=source_url,

            title=item.get("title") or "",
            nominal=item.get("nominal") or "",
            year=item.get("year"),

            letters=item.get("letters") or "",
            edge=item.get("edge") or "",
            quality=item.get("quality") or "",
            ruler=item.get("ruler") or "",
            mintage=item.get("mintage") or "",

            material=item.get("material") or "",
            weight_g=item.get("weight_g"),
            diameter_mm=item.get("diameter_mm"),
            thickness_mm=item.get("thickness_mm"),

            catalogs=item.get("catalogs") or "",

            image_obverse=item.get("image_obverse"),
            image_reverse=item.get("image_reverse"),
        )

        db.add(coin)
        imported += 1

    db.commit()
    return imported


def main():
    db = SessionLocal()
    try:
        total = 0
        for rel_path, slug, name in JSON_SOURCES:
            path = Path(__file__).resolve().parent / rel_path
            section = get_or_create_section(db, slug, name)
            print(f"\nИмпорт {path} в раздел {section.slug} ({section.name})")
            count = import_file(db, path, section)
            print(f"  -> добавлено монет: {count}")
            total += count

        print(f"\nГотово. Всего добавлено монет: {total}")

    finally:
        db.close()


if __name__ == "__main__":
    main()

