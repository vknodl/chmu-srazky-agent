import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# Oficiální otevřená data ČHMÚ.
# Dokumentace: https://opendata.chmi.cz/meteorology/climate/Klimatologicka_data_popis.pdf
META_URL = "https://opendata.chmi.cz/meteorology/climate/now/metadata/meta1-{date}.json"
DAILY_URL = "https://opendata.chmi.cz/meteorology/climate/recent/data/daily/dly-{wsi}-{yyyymm}.json"

OUTPUT_FILE = "srazky_vsechny_stanice.csv"
HEADERS = {"User-Agent": "chmu-srazky-agent/2.0"}
TIMEOUT = 30
MAX_WORKERS = 12


def get_json(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def get_station_list():
    """Return [(WSI, station_name), ...] from CHMI meta1."""
    today = datetime.now(timezone.utc).date()

    for delta in range(0, 3):
        date = (today - timedelta(days=delta)).strftime("%Y%m%d")
        try:
            data = get_json(META_URL.format(date=date))
        except Exception as exc:
            print(f"Metadata {date}: {exc}")
            continue

        stations = {}
        for obj in walk(data):
            wsi = obj.get("WSI")
            name = obj.get("FULL_NAME") or obj.get("GH_ID")
            if wsi and name:
                stations[str(wsi)] = str(name).replace(";", " ").strip()

        if stations:
            print(f"Načteno {len(stations)} stanic z meta1-{date}.json")
            return sorted(stations.items(), key=lambda x: x[1].lower())

    raise RuntimeError("ČHMÚ meta1: nepodařilo se načíst seznam stanic.")


def parse_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", ".")
        if s in ("", "-", "--", "NA", "N/A", "null", "None"):
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def normalize_date(value):
    if value is None:
        return None

    if isinstance(value, (int, float)):
        # nepovažujeme libovolné číslo za datum
        return None

    s = str(value).strip()
    for pattern in (
        r"^(\d{4})-(\d{2})-(\d{2})",
        r"^(\d{4})(\d{2})(\d{2})",
        r"^(\d{2})\.(\d{2})\.(\d{4})",
    ):
        m = re.match(pattern, s)
        if m:
            if pattern.startswith(r"^(\\d{2})"):
                return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def extract_sra(data):
    """Extract {YYYY-MM-DD: mm} from the many possible CHMI JSON layouts.

    The official documentation guarantees the SRA element and file naming,
    while this parser deliberately tolerates wrapper/layout changes.
    """
    result = {}

    date_keys = {
        "date", "datum", "date_meas", "date_measurement", "datetime",
        "dt", "time", "timestamp", "date_time", "measurement_date"
    }
    value_keys = {"value", "hodnota", "val", "data", "result"}

    def visit(obj, context_date=None):
        if isinstance(obj, dict):
            local_date = context_date
            for k, v in obj.items():
                if str(k).lower() in date_keys:
                    d = normalize_date(v)
                    if d:
                        local_date = d

            # Layout A: {"SRA": number} or {"SRA": {"value": number}}
            for k, v in obj.items():
                if str(k).upper() != "SRA":
                    continue

                n = parse_number(v)
                if n is not None and local_date:
                    result[local_date] = n
                    continue

                if isinstance(v, dict):
                    n = None
                    d = local_date
                    for kk, vv in v.items():
                        if str(kk).lower() in value_keys:
                            n = parse_number(vv)
                        if str(kk).lower() in date_keys:
                            d = normalize_date(vv) or d
                    if n is not None and d:
                        result[d] = n
                    visit(v, d)
                elif isinstance(v, list):
                    visit(v, local_date)

            # Layout B: {"element": "SRA", "date": "...", "value": ...}
            element = str(obj.get("element", obj.get("prvek", obj.get("EL_ABBREVIATION", "")))).upper()
            if element == "SRA":
                n = None
                for k, v in obj.items():
                    if str(k).lower() in value_keys:
                        n = parse_number(v)
                        if n is not None:
                            break
                if n is not None and local_date:
                    result[local_date] = n

            for v in obj.values():
                if isinstance(v, (dict, list)):
                    visit(v, local_date)

        elif isinstance(obj, list):
            for item in obj:
                visit(item, context_date)

    visit(data)
    return result


def fetch_station(wsi, yyyymm):
    url = DAILY_URL.format(wsi=wsi, yyyymm=yyyymm)
    try:
        data = get_json(url)
        values = extract_sra(data)
        return wsi, values, None
    except Exception as exc:
        return wsi, {}, str(exc)


def read_history():
    if not os.path.exists(OUTPUT_FILE):
        return [], {}

    with open(OUTPUT_FILE, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))

    if not rows:
        return [], {}

    header = rows[0]
    table = {}
    for row in rows[1:]:
        if row:
            table[row[0]] = row

    return header, table


def fmt(value):
    if value is None:
        return ""
    return f"{value:.1f}".replace(".", ",")


def main():
    stations = get_station_list()

    # We use the current month. The daily files contain all days from the
    # beginning of the month through the previous completed day.
    now = datetime.now()
    yyyymm = now.strftime("%Y%m")
    today = now.strftime("%Y-%m-%d")

    print(f"Stahuji denní SRA za {yyyymm} pro {len(stations)} stanic...")

    values_by_name = {}
    failures = 0
    successful = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(fetch_station, wsi, yyyymm): (wsi, name)
            for wsi, name in stations
        }

        for future in as_completed(futures):
            wsi, name = futures[future]
            got_wsi, values, error = future.result()
            if error:
                failures += 1
                print(f"  CHYBA {name} [{wsi}]: {error}")
                continue

            if values:
                successful += 1
                values_by_name[name] = values

    print(f"Úspěšně načteno {successful} stanic; chyby/bez dat: {failures}.")

    if not values_by_name:
        raise RuntimeError("Z ČHMÚ se nepodařilo načíst žádné denní SRA.")

    old_header, old_table = read_history()

    # Keep all old columns and add new stations alphabetically.
    old_stations = old_header[1:] if old_header else []
    station_names = sorted(set(old_stations) | set(values_by_name), key=str.lower)

    # Use all dates already present plus all dates returned by CHMI.
    dates = set(old_table)
    for station_values in values_by_name.values():
        dates.update(station_values)

    # Do not invent a value for today: CHMI's daily file is normally complete
    # only through the previous day.
    dates.discard(today)

    if not dates:
        raise RuntimeError("ČHMÚ vrátilo data, ale nepodařilo se z nich určit žádný den.")

    new_rows = []
    old_index = {name: i for i, name in enumerate(old_header)}

    for date in sorted(dates):
        row = [date]
        old_row = old_table.get(date, [])

        for station in station_names:
            value = None

            if station in values_by_name and date in values_by_name[station]:
                value = values_by_name[station][date]
            elif station in old_index and old_row:
                idx = old_index[station]
                if idx < len(old_row):
                    value = parse_number(old_row[idx])

            row.append(fmt(value))

        new_rows.append(row)

    with open(OUTPUT_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Datum"] + station_names)
        writer.writerows(new_rows)

    print(
        f"Hotovo: {OUTPUT_FILE}, {len(new_rows)} dnů × "
        f"{len(station_names)} stanic."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"KRITICKÁ CHYBA: {exc}")
        sys.exit(1)
