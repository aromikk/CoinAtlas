import pandas as pd

from .database import SessionLocal
from .models import Section, Coin

EXCEL_PATH = "data/coins.xlsx"  # путь относительно корня backend


def main():
    df = pd.read_excel(EXCEL_PATH)

    db = SessionLocal()
    try:
        # Пока сделаем один раздел "all" для всех монет.
        section = db.query(Section).filter_by(code="all").first()
        if section is None:
            section = Section(
                code="all",
                title="Все монеты",
                subtitle=None,
            )
            db.add(section)
            db.flush()  # чтобы у section появился id

        for _, row in df.iterrows():
            coin = Coin(
                section_id=section.id,

                DT=str(row.get("DT") or ""),           # ПОДГОНЯЙ имена столбцов под свой xlsx
                cname=str(row.get("cname") or ""),

                sname=str(row.get("sname") or "") if "sname" in row and not pd.isna(row["sname"]) else None,
                nominal=str(row.get("nominal") or ""),
                metal=str(row.get("metal") or ""),
                #weight_g=float(row["weight_g"]) if "weight_g" in row and not pd.isna(row["weight_g"]) else None,
                #diameter_mm=float(row["diameter_mm"]) if "diameter_mm" in row and not pd.isna(row["diameter_mm"]) else None,
                #edge=str(row.get("edge") or ""),
                #mint=str(row.get("mint") or ""),
                #mintage=int(row["mintage"]) if "mintage" in row and not pd.isna(row["mintage"]) else None,
                #catalog_code=str(row.get("catalog") or ""),
            )
            db.add(coin)

        db.commit()
        print("Импорт монет из Excel завершён.")
    finally:
        db.close()


if __name__ == "__main__":
    main()