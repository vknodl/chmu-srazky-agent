import csv
import glob
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

META_URL = "https://opendata.chmi.cz/meteorology/climate/now/metadata/meta1-{date}.json"
DAILY_INDEX_URL = "https://opendata.chmi.cz/meteorology/climate/recent/data/daily/"
DAILY_URL = DAILY_INDEX_URL + "dly-{wsi}-{yyyymm}.json"

OUTPUT_PREFIX = "srazky_vsechny_stanice"
OUTPUT_FILE = "srazky_vsechny_stanice.csv"
HEADERS = {"User-Agent": "chmu-srazky-agent/3.0"}
TIMEOUT = 30
MAX_WORKERS = 12
PRAGUE_TZ = ZoneInfo("Europe/Prague")


def get_json(url):
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def get_table(data):
    block = data.get("data", {}).get("data", {})
    header = block.get("header", "")
    rows = block.get("values", [])
    if not header or not isinstance(rows, list):
        raise ValueError("Neznámá struktura JSON z ČHMÚ.")
    return [h.strip() for h in header.split(",")], rows


def get_station_list():
    today = datetime.now(timezone.utc).date()

    for delta in range(0, 3):
        date = (today - timedelta(days=delta)).strftime("%Y%m%d")
        try:
            data = get_json(META_URL.format(date=date))
            headers, rows = get_table(data)
            idx = {name: i for i, name in enumerate(headers)}

            if not {"WSI", "FULL_NAME"}.issubset(idx):
                raise ValueError("meta1 neobsahuje WSI a FULL_NAME.")

            stations = {}
            for row in rows:
                if len(row) <= max(idx["WSI"], idx["FULL_NAME"]):
                    continue
                wsi = str(row[idx["WSI"]]).strip()
                name = str(row[idx["FULL_NAME"]]).replace(";", " ").strip()
                if wsi and name:
                    stations[wsi] = name

            if stations:
                print(f"Načteno {len(stations)} stanic z meta1-{date}.json")
                return sorted(stations.items(), key=lambda item: item[1].lower())

            raise ValueError("meta1 neobsahuje žádné stanice.")
        except Exception as exc:
            print(f"Metadata {date}: {exc}")

    raise RuntimeError("ČHMÚ meta1: nepodařilo se načíst seznam stanic.")


def parse_number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        if value in ("", "-", "--", "NA", "N/A", "null", "None"):
            return None
        try:
            return float(value)
        except ValueError:
            return None
    return None


def extract_sra(data):
    headers, rows = get_table(data)
    idx = {name: i for i, name in enumerate(headers)}

    if not {"ELEMENT", "DT", "VAL"}.issubset(idx):
        raise ValueError("Denní soubor neobsahuje ELEMENT, DT a VAL.")

    result = {}
    for row in rows:
        if len(row) <= max(idx["ELEMENT"], idx["DT"], idx["VAL"]):
            continue
        if str(row[idx["ELEMENT"]]).strip().upper() != "SRA":
            continue

        date = str(row[idx["DT"]]).strip()[:10]
        if len(date) == 10 and date[4] == "-" and date[7] == "-":
            # Datum evidujeme i při prázdné/nevalidní hodnotě SRA.
            # ČHMÚ může mít nejnovější den již publikovaný, ale u
            # konkrétní stanice zatím bez číselné hodnoty.
            result[date] = parse_number(row[idx["VAL"]])

    return result


def get_available_wsi(yyyymm):
    response = requests.get(DAILY_INDEX_URL, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    pattern = re.compile(r'href="dly-(.+)-' + re.escape(yyyymm) + r'\.json"')
    return set(pattern.findall(response.text))


def fetch_station(wsi, yyyymm):
    url = DAILY_URL.format(wsi=wsi, yyyymm=yyyymm)
    try:
        values = extract_sra(get_json(url))
        return wsi, values, None
    except Exception as exc:
        return wsi, {}, str(exc)


def read_history():
    candidates = sorted(glob.glob(OUTPUT_PREFIX + "_????-??-??.csv"), reverse=True)
    source_file = candidates[0] if candidates else OUTPUT_FILE
    if not os.path.exists(source_file):
        return [], {}

    with open(source_file, "r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file, delimiter=";"))

    if not rows:
        return [], {}

    header = rows[0]
    table = {row[0]: row for row in rows[1:] if row}
    return header, table


def fmt(value):
    if value is None:
        return ""
    return f"{value:.1f}".replace(".", ",")


def main():
    stations = get_station_list()
    now_prague = datetime.now(PRAGUE_TZ)
    current_date = now_prague.date().isoformat()
    yyyymm = now_prague.strftime("%Y%m")

    available = get_available_wsi(yyyymm)
    stations = [(wsi, name) for wsi, name in stations if wsi in available]
    print(f"Našel jsem {len(stations)} stanic s denním souborem za {yyyymm}.")
    print(f"Stahuji denní SRA za {yyyymm} pro {len(stations)} stanic...")

    values_by_name = {}
    failures = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(fetch_station, wsi, yyyymm): (wsi, name)
            for wsi, name in stations
        }

        for future in as_completed(futures):
            wsi, name = futures[future]
            _, values, error = future.result()

            if error:
                failures += 1
                print(f"  CHYBA {name} [{wsi}]: {error}")
                continue

            if values:
                values_by_name[name] = values

    print(f"Úspěšně načteno {len(values_by_name)} stanic; chyby/bez dat: {failures}.")

    if not values_by_name:
        raise RuntimeError("Z ČHMÚ se nepodařilo načíst žádná denní data SRA.")

    old_header, old_table = read_history()
    old_stations = old_header[1:] if old_header else []
    station_names = sorted(set(old_stations) | set(values_by_name), key=str.lower)

    dates = set(old_table)
    for station_values in values_by_name.values():
        dates.update(station_values)

    # Aktuální den není uzavřený a nesmí se použít jako poslední den srážek.
    dates.discard(current_date)

    # Název výstupu je vždy odvozen od nejnovějšího skutečně dostupného
    # uzavřeného dne, nikoli od data spuštění GitHub Actions.
    latest_date = max(dates) if dates else None
    if not latest_date:
        raise RuntimeError("Nepodařilo se určit žádný uzavřený den srážkových dat.")

    print(f"Nejnovější uzavřený den srážkových dat: {latest_date}")

    global OUTPUT_FILE
    OUTPUT_FILE = OUTPUT_PREFIX + "_" + latest_date + ".csv"

    old_index = {name: i for i, name in enumerate(old_header)}
    new_rows = []

    for date in sorted(dates):
        old_row = old_table.get(date, [])
        row = [date]

        for station in station_names:
            value = values_by_name.get(station, {}).get(date)

            if value is None and station in old_index and old_row:
                index = old_index[station]
                if index < len(old_row):
                    value = parse_number(old_row[index])

            row.append(fmt(value))

        new_rows.append(row)

    with open(OUTPUT_FILE, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(["Datum"] + station_names)
        writer.writerows(new_rows)

    for old_file in glob.glob(OUTPUT_PREFIX + "*.csv"):
        if old_file != OUTPUT_FILE:
            os.remove(old_file)

    print(f"Hotovo: {OUTPUT_FILE}, {len(new_rows)} dnů × {len(station_names)} stanic.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"KRITICKÁ CHYBA: {exc}")
        sys.exit(1)
