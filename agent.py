"""Denní stahování denních srážek ČHMÚ – pouze stanice Praha-Kbely."""

import csv
import glob
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Praha-Kbely, WMO 11518 – pevně zvolená stanice, bez načítání metadat.
STATION_WSI = "0-20000-0-11518"
STATION_NAME = "Praha-Kbely"
DAILY_URL = (
    "https://opendata.chmi.cz/meteorology/climate/recent/data/daily/"
    f"dly-{STATION_WSI}-{{yyyymm}}.json"
)
OUTPUT_FILE = "srazky_kbely_praha.csv"
OLD_FILE_PATTERN = "srazky_vsechny_stanice_????-??-??.csv"
PRAGUE_TZ = ZoneInfo("Europe/Prague")
TIMEOUT = (10, 45)


def create_session():
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "chmu-srazky-kbely/1.0"})
    return session


def parse_number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    value = str(value).strip().replace(",", ".")
    if value in ("", "-", "--", "NA", "N/A", "null", "None"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def download_month(session, yyyymm):
    url = DAILY_URL.format(yyyymm=yyyymm)
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()

    try:
        table = payload["data"]["data"]
        headers = [name.strip() for name in table["header"].split(",")]
        rows = table["values"]
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"Neznámá struktura JSON ČHMÚ ({url}): {exc}") from exc

    indexes = {name: i for i, name in enumerate(headers)}
    required = {"ELEMENT", "DT", "VAL"}
    if not required.issubset(indexes):
        raise ValueError(f"V JSON chybí sloupce: {required - indexes.keys()}")

    result = {}
    last_index = max(indexes[name] for name in required)
    for row in rows:
        if len(row) <= last_index:
            continue
        if str(row[indexes["ELEMENT"]]).strip().upper() != "SRA":
            continue
        day = str(row[indexes["DT"]]).strip()[:10]
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        result[day] = parse_number(row[indexes["VAL"]])

    if not result:
        raise ValueError(f"Soubor ČHMÚ neobsahuje žádné řádky SRA: {url}")
    return result


def read_history():
    """Načte novou historii, případně převezme sloupec Kbel ze starého CSV."""
    candidates = []
    if os.path.exists(OUTPUT_FILE):
        candidates.append(OUTPUT_FILE)
    candidates.extend(sorted(glob.glob(OLD_FILE_PATTERN), reverse=True))

    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as file:
                first_line = file.readline()
                delimiter = "\t" if "\t" in first_line else ";"
                file.seek(0)
                rows = list(csv.reader(file, delimiter=delimiter))
            if not rows:
                continue

            header = rows[0]
            date_index = header.index("Datum") if "Datum" in header else 0
            if STATION_WSI in header:
                value_index = header.index(STATION_WSI)
            elif "SRA_mm" in header:
                value_index = header.index("SRA_mm")
            else:
                continue

            history = {}
            for row in rows[1:]:
                if len(row) <= max(date_index, value_index):
                    continue
                day = row[date_index].strip()
                try:
                    date.fromisoformat(day)
                except ValueError:
                    continue
                history[day] = parse_number(row[value_index])

            if history:
                print(f"Historie načtena z {path}: {len(history)} dnů.")
                return history
        except (OSError, csv.Error, ValueError) as exc:
            print(f"Upozornění: soubor {path} nelze načíst: {exc}")
    return {}


def main():
    today = datetime.now(PRAGUE_TZ).date()
    yesterday = today - timedelta(days=1)
    target_day = yesterday.isoformat()
    # Stahuje se měsíc cílového měření, nikoli měsíc aktuálního spuštění.
    # Díky tomu funguje správně i první den v měsíci.
    yyyymm = yesterday.strftime("%Y%m")

    print(f"Stanice: {STATION_NAME} ({STATION_WSI})")
    print(f"Stahuji denní SRA za {target_day} z ČHMÚ (soubor {yyyymm}).")

    session = create_session()
    try:
        downloaded = download_month(session, yyyymm)
    finally:
        session.close()

    # Bez skutečné hodnoty za včerejší den se nesmí vytvořit ani uložit
    # neúplný výstup. Chybějící údaj se nesmí tvářit jako úspěšná aktualizace.
    if target_day not in downloaded:
        raise RuntimeError(
            f"ČHMÚ zatím neposkytuje denní záznam SRA za {target_day}. "
            "CSV nebylo změněno."
        )
    if downloaded[target_day] is None:
        raise RuntimeError(
            f"ČHMÚ má pro {target_day} záznam SRA, ale bez číselné hodnoty. "
            "CSV nebylo změněno."
        )

    history = read_history()

    # Platné nové hodnoty aktualizují historii; prázdná hodnota
    # nepřepíše dříve známé měření.
    for day, value in downloaded.items():
        if value is not None:
            history[day] = value
        else:
            history.setdefault(day, None)

    history = {day: value for day, value in history.items() if day <= yesterday.isoformat()}
    if not history:
        raise RuntimeError("Do včerejšího dne nejsou k dispozici žádná data. CSV nebylo změněno.")

    first_day = min(date.fromisoformat(day) for day in history)
    last_day = max(date.fromisoformat(day) for day in history)

    with open(OUTPUT_FILE, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file, delimiter="\t", lineterminator="\n")
        writer.writerow(["Datum", "SRA_mm"])
        day = first_day
        while day <= last_day:
            key = day.isoformat()
            value = history.get(key)
            writer.writerow([key, "" if value is None else f"{value:.1f}"])
            day += timedelta(days=1)

    print(f"Hotovo: {OUTPUT_FILE}; {last_day.isoformat()}, {len(history)} známých dnů.")


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as exc:
        print(f"CHYBA PŘI STAHOVÁNÍ ČHMÚ: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"KRITICKÁ CHYBA: {exc}")
        sys.exit(1)
