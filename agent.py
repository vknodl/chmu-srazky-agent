# Tento skript každý den stáhne denní srážková data z ČHMÚ,
# spojí je s historickými daty a uloží je do CSV.
#
# Důležitá vlastnost tohoto skriptu:
# CSV záměrně končí o jeden den dříve než nejnovější dostupná data.
# Tím získáme jednodenní rezervu na doplnění hodnot ze všech stanic.

import csv  # Vestavěný modul pro čtení a zápis CSV souborů.
import glob  # Umožňuje vyhledat soubory podle vzoru, např. všechny naše CSV.
import os  # Práce se soubory – např. jejich mazání a kontrola existence.
import re  # Regulární výrazy – zde použité pro hledání názvů souborů na serveru ČHMÚ.
import sys  # Umožňuje ukončit program s chybovým kódem.
from concurrent.futures import ThreadPoolExecutor, as_completed  # Paralelní stahování dat.
from datetime import datetime, timedelta, timezone  # Práce s datem a časem.
from zoneinfo import ZoneInfo  # Převod času do konkrétní časové zóny.

import requests  # Externí knihovna pro stahování dat přes HTTP/HTTPS.


# ---------------------------------------------------------------------------
# NASTAVENÍ
# ---------------------------------------------------------------------------

# URL denních metadat ČHMÚ. {date} se při spuštění nahradí např. 20260926.
META_URL = "https://opendata.chmi.cz/meteorology/climate/now/metadata/meta1-{date}.json"

# Adresa adresáře, kde ČHMÚ zveřejňuje denní soubory jednotlivých stanic.
DAILY_INDEX_URL = "https://opendata.chmi.cz/meteorology/climate/recent/data/daily/"

# Šablona URL konkrétní stanice. {wsi} = identifikátor stanice,
# {yyyymm} = rok a měsíc, např. 202609.
DAILY_URL = DAILY_INDEX_URL + "dly-{wsi}-{yyyymm}.json"

# Začátek názvu našeho výstupního CSV.
OUTPUT_PREFIX = "srazky_vsechny_stanice"

# Výchozí název. Ve funkci main() se později přepíše na
# např. srazky_vsechny_stanice_2026-09-24.csv.
OUTPUT_FILE = "srazky_vsechny_stanice.csv"

# Hlavička požadavku na server ČHMÚ. Identifikuje náš program.
HEADERS = {"User-Agent": "chmu-srazky-agent/3.0"}

# Maximální doba čekání na odpověď serveru v sekundách.
TIMEOUT = 30

# Kolik stanic může skript stahovat současně.
# Vyšší číslo může být rychlejší, ale zbytečně zatěžuje server.
MAX_WORKERS = 12

# Časová zóna Prahy. Používáme ji pro určení aktuálního dne a měsíce.
PRAGUE_TZ = ZoneInfo("Europe/Prague")


# ---------------------------------------------------------------------------
# POMOCNÉ FUNKCE PRO STAHOVÁNÍ A ČTENÍ JSON
# ---------------------------------------------------------------------------

def get_json(url):
    """Stáhne JSON z dané URL a vrátí ho jako Python objekt."""

    # Pošleme požadavek na server ČHMÚ.
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)

    # Pokud server vrátí chybu (např. 404), vyvoláme výjimku.
    response.raise_for_status()

    # Text odpovědi převedeme z JSON do Pythonu (slovníky, seznamy...).
    return response.json()


def get_table(data):
    """Z JSONu ČHMÚ vytáhne názvy sloupců a samotné řádky tabulky."""

    # ČHMÚ ukládá tabulku uvnitř data -> data -> data.
    block = data.get("data", {}).get("data", {})

    # 'header' obsahuje názvy sloupců oddělené čárkami.
    header = block.get("header", "")

    # 'values' obsahuje jednotlivé řádky tabulky.
    rows = block.get("values", [])

    # Když struktura neodpovídá očekávání, raději program zastavíme.
    if not header or not isinstance(rows, list):
        raise ValueError("Neznámá struktura JSON z ČHMÚ.")

    # Z názvů sloupců odstraníme mezery.
    return [h.strip() for h in header.split(",")], rows


# ---------------------------------------------------------------------------
# SEZNAM STANIC
# ---------------------------------------------------------------------------

def get_station_list():
    """Načte seznam stanic ČHMÚ jako dvojice WSI kód -> název."""

    # Pro metadata nejprve použijeme dnešní datum v UTC.
    today = datetime.now(timezone.utc).date()

    # Zkusíme dnešní metadata a při problému ještě dva předchozí dny.
    # To je pojistka pro případ, že dnešní meta1 ještě není zveřejněna.
    for delta in range(0, 3):
        date = (today - timedelta(days=delta)).strftime("%Y%m%d")

        try:
            # Stáhneme metadata pro daný den.
            data = get_json(META_URL.format(date=date))
            headers, rows = get_table(data)

            # Vytvoříme mapu názvu sloupce -> jeho pořadí.
            # Např. {"WSI": 0, "FULL_NAME": 3, ...}
            idx = {name: i for i, name in enumerate(headers)}

            # Pro nás jsou nutné identifikátor stanice WSI a její název.
            if not {"WSI", "FULL_NAME"}.issubset(idx):
                raise ValueError("meta1 neobsahuje WSI a FULL_NAME.")

            stations = {}

            # Projdeme všechny stanice v metadatech.
            for row in rows:
                # Přeskočíme neúplné řádky.
                if len(row) <= max(idx["WSI"], idx["FULL_NAME"]):
                    continue

                # WSI je jedinečný identifikátor stanice.
                wsi = str(row[idx["WSI"]]).strip()

                # FULL_NAME je název stanice. Středník odstraníme,
                # protože ho používáme jako oddělovač v našem CSV.
                name = str(row[idx["FULL_NAME"]]).replace(";", " ").strip()

                if wsi and name:
                    stations[wsi] = name

            if stations:
                print(f"Načteno {len(stations)} stanic z meta1-{date}.json")

                # Stanice seřadíme podle názvu, aby měly v CSV stabilní pořadí.
                return sorted(stations.items(), key=lambda item: item[0])

            raise ValueError("meta1 neobsahuje žádné stanice.")

        except Exception as exc:
            # Pokud metadata pro jeden den nejdou načíst, zkusíme další den.
            print(f"Metadata {date}: {exc}")

    # Pokud selhaly všechny tři pokusy, nemá smysl pokračovat.
    raise RuntimeError("ČHMÚ meta1: nepodařilo se načíst seznam stanic.")


# ---------------------------------------------------------------------------
# VÝSTUPNÍ TABULKA STANIC
# ---------------------------------------------------------------------------

def write_station_mapping(stations):
    """Zapíše převodní tabulku WSI kód -> název stanice."""

    # Tento soubor slouží jako jednoduchá pomůcka pro přiřazení WSI kódů
    # k názvům stanic. Samotný soubor se srážkami používá pouze WSI kódy.
    with open("stanice.csv", "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(["WSI", "Název stanice"])

        for wsi, name in stations:
            writer.writerow([wsi, name])


# ---------------------------------------------------------------------------
# PŘEVOD HODNOT NA ČÍSLA
# ---------------------------------------------------------------------------

def parse_number(value):
    """Převede hodnotu ze souboru ČHMÚ na číslo, nebo vrátí None."""

    # True/False nechceme považovat za číslo.
    if isinstance(value, bool) or value is None:
        return None

    # Pokud už je hodnota číslo, pouze ji převedeme na float.
    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        # Odstraníme mezery a českou desetinnou čárku převedeme na tečku.
        value = value.strip().replace(",", ".")

        # Tyto hodnoty znamenají, že číslo není k dispozici.
        if value in ("", "-", "--", "NA", "N/A", "null", "None"):
            return None

        try:
            return float(value)
        except ValueError:
            # Pokud obsah není číslo, vrátíme None místo pádu programu.
            return None

    return None


# ---------------------------------------------------------------------------
# VYTAŽENÍ DENNÍCH SRÁŽEK SRA
# ---------------------------------------------------------------------------

def extract_sra(data):
    """Z denního JSONu vybere pouze denní srážku SRA podle data."""

    headers, rows = get_table(data)
    idx = {name: i for i, name in enumerate(headers)}

    # Denní soubor musí obsahovat tyto tři sloupce.
    if not {"ELEMENT", "DT", "VAL"}.issubset(idx):
        raise ValueError("Denní soubor neobsahuje ELEMENT, DT a VAL.")

    result = {}

    # Projdeme všechny řádky denního souboru.
    for row in rows:
        if len(row) <= max(idx["ELEMENT"], idx["DT"], idx["VAL"]):
            continue

        # Zajímají nás pouze řádky s prvkem SRA = denní úhrn srážek.
        if str(row[idx["ELEMENT"]]).strip().upper() != "SRA":
            continue

        # Z hodnoty DT vezmeme pouze datum YYYY-MM-DD.
        date = str(row[idx["DT"]]).strip()[:10]

        if len(date) == 10 and date[4] == "-" and date[7] == "-":
            # Datum evidujeme i tehdy, když je hodnota SRA prázdná/neplatná.
            # Je to důležité, protože ČHMÚ může nejnovější den již publikovat,
            # ale u konkrétní stanice ještě nemusí být hodnota doplněná.
            result[date] = parse_number(row[idx["VAL"]])

    return result


# ---------------------------------------------------------------------------
# ZJIŠTĚNÍ, KTERÉ STANICE MAJÍ DENNÍ SOUBOR
# ---------------------------------------------------------------------------

def get_available_wsi(yyyymm):
    """Zjistí ze seznamu ČHMÚ, pro které stanice existuje soubor za daný měsíc."""

    # Načteme HTML index adresáře daily/.
    response = requests.get(DAILY_INDEX_URL, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()

    # Hledáme odkazy typu dly-IDENTIFIKATOR-202609.json.
    pattern = re.compile(r'href="dly-(.+)-' + re.escape(yyyymm) + r'\.json"')

    # Vrátíme množinu WSI identifikátorů.
    return set(pattern.findall(response.text))


# ---------------------------------------------------------------------------
# STAŽENÍ DAT JEDNÉ STANICE
# ---------------------------------------------------------------------------

def fetch_station(wsi, yyyymm):
    """Stáhne a zpracuje denní data jedné stanice."""

    url = DAILY_URL.format(wsi=wsi, yyyymm=yyyymm)

    try:
        # Stáhneme JSON a vybereme z něj pouze SRA.
        values = extract_sra(get_json(url))
        return wsi, values, None
    except Exception as exc:
        # Chybu vrátíme jako text, aby jedna nefunkční stanice
        # nezastavila stahování všech ostatních.
        return wsi, {}, str(exc)


# ---------------------------------------------------------------------------
# NAČTENÍ PŘEDCHOZÍHO CSV
# ---------------------------------------------------------------------------

def read_history():
    """Načte nejnovější již existující CSV, aby se zachovala historie."""

    # Najdeme všechny naše CSV se skutečným datem v názvu.
    candidates = sorted(glob.glob(OUTPUT_PREFIX + "_????-??-??.csv"), reverse=True)

    # Použijeme nejnovější soubor. Pokud žádný není, zkusíme výchozí název.
    source_file = candidates[0] if candidates else OUTPUT_FILE

    if not os.path.exists(source_file):
        # První spuštění – zatím nemáme žádnou historii.
        return [], {}

    # Načteme celý CSV soubor.
    with open(source_file, "r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file, delimiter=";"))

    if not rows:
        return [], {}

    # První řádek je hlavička, ostatní řádky jsou data.
    header = rows[0]

    # Vytvoříme slovník datum -> celý řádek.
    table = {row[0]: row for row in rows[1:] if row}

    return header, table


# ---------------------------------------------------------------------------
# FORMÁTOVÁNÍ ČÍSEL PRO CSV
# ---------------------------------------------------------------------------

def fmt(value):
    """Převede číslo na text s jedním desetinným místem a čárkou."""

    if value is None:
        # Prázdná buňka = hodnota zatím není k dispozici.
        return ""

    # Např. 1.2 se zapíše jako 1,2.
    return f"{value:.1f}".replace(".", ",")


# ---------------------------------------------------------------------------
# HLAVNÍ PROGRAM
# ---------------------------------------------------------------------------

def main():
    """Provede celý proces od načtení stanic až po vytvoření CSV."""

    # 1) Načteme seznam stanic a jejich názvy.
    stations = get_station_list()

    # 2) Vytvoříme aktuální převodní tabulku WSI kódů na názvy stanic.\n    write_station_mapping(stations)\n\n    # 2) Zjistíme aktuální datum a měsíc podle pražského času.
    now_prague = datetime.now(PRAGUE_TZ)
    current_date = now_prague.date().isoformat()
    yyyymm = now_prague.strftime("%Y%m")

    # 3) Zjistíme, které stanice mají na serveru ČHMÚ soubor za aktuální měsíc.
    available = get_available_wsi(yyyymm)

    # Necháme pouze stanice, které skutečně mají svůj měsíční soubor.
    stations = [(wsi, name) for wsi, name in stations if wsi in available]

    print(f"Našel jsem {len(stations)} stanic s denním souborem za {yyyymm}.")
    print(f"Stahuji denní SRA za {yyyymm} pro {len(stations)} stanic...")

    # Data ukládáme podle jednoznačného WSI kódu stanice.
    # Název stanice zde vůbec nepoužíváme jako identifikátor.
    values_by_wsi = {}

    # Počet stanic, u kterých se stahování nepovedlo.
    failures = 0

    # 4) Stahujeme více stanic současně, aby celý proces netrval zbytečně dlouho.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        # Pro každou stanici vytvoříme úkol ke stažení.
        futures = {
            pool.submit(fetch_station, wsi, yyyymm): (wsi, name)
            for wsi, name in stations
        }

        # Postupně zpracujeme dokončené úkoly.
        for future in as_completed(futures):
            wsi, name = futures[future]
            _, values, error = future.result()

            if error:
                failures += 1
                print(f"  CHYBA [{wsi}] {name}: {error}")
                continue

            # Stanici uložíme pouze tehdy, pokud z ní máme nějaká data.
            if values:
                values_by_wsi[wsi] = values

    print(f"Úspěšně načteno {len(values_by_wsi)} stanic; chyby/bez dat: {failures}.")

    # Pokud nemáme ani jednu stanici, nemá smysl vytvářet CSV.
    if not values_by_wsi:
        raise RuntimeError("Z ČHMÚ se nepodařilo načíst žádná denní data SRA.")

    # 5) Načteme předchozí CSV, pokud existuje.
    # Díky tomu zachováme starší dny a hodnoty, které nejsou v novém stažení.
    old_header, old_table = read_history()

    # Výstupní sloupce tvoří pouze WSI kódy. Název stanice se do CSV nepíše.
    station_codes = sorted({wsi for wsi, _ in stations} | set(values_by_wsi))

    # Starší CSV obsahovalo názvy stanic. Při přechodu na WSI proto vytvoříme
    # převod názvu -> WSI podle aktuálních metadat ČHMÚ.
    name_to_wsi = {}
    for wsi, name in stations:
        name_to_wsi.setdefault(name, []).append(wsi)

    # Starou historii převedeme z názvů sloupců na WSI kódy.
    old_by_wsi = {}
    if old_header:
        for old_name_index, old_name in enumerate(old_header[1:], start=1):
            matching_wsi = name_to_wsi.get(old_name, [])
            if len(matching_wsi) == 1:
                old_by_wsi[matching_wsi[0]] = old_name_index

    # Při další práci už používáme pouze WSI kódy.
    station_codes = sorted(set(station_codes))

    # 6) Zjistíme všechna data, která známe ze starého CSV i z nového stažení.
    dates = set(old_table)
    for station_values in values_by_wsi.values():
        dates.update(station_values)

    # Aktuální den ještě není uzavřený, takže ho nikdy nepoužijeme.
    dates.discard(current_date)

    # 7) Určíme pevné cílové datum podle dnešního data.
    # Pokud je dnes 28. 9., CSV má končit 27. 9.
    # Neodvozujeme tedy název souboru od toho, jaké datum se náhodou
    # podařilo stáhnout jako nejnovější z jednotlivých stanic.
    latest_available_date = max(dates) if dates else None

    if not latest_available_date:
        raise RuntimeError("Nepodařilo se načíst žádná data srážek.")

    # CSV vždy obsahuje data pouze do včerejška.
    # Tím máme přesně jednodenní rezervu na doplnění dnešních dat.
    target_date = (now_prague.date() - timedelta(days=1)).isoformat()
    latest_date = target_date

    # Požadovaný den musí být mezi načtenými daty nebo ve starší historii.
    if target_date not in dates:
        raise RuntimeError(
            f"Data pro požadovaný den {target_date} zatím nejsou k dispozici."
        )

    print(
        f"Nejnovější dostupný den: {latest_available_date}; "
        f"poslední den v CSV s jednodenní rezervou: {latest_date}"
    )

    # Název CSV se odvozuje od posledního dne, který do něj patří.
    # Např. data do 24. 9. -> srazky_vsechny_stanice_2026-09-24.csv
    global OUTPUT_FILE
    OUTPUT_FILE = OUTPUT_PREFIX + "_" + latest_date + ".csv"

    # 9) Připravíme mapu WSI -> pořadí sloupce ve starém CSV.
    old_index = old_by_wsi

    new_rows = []

    # Do CSV zařadíme pouze dny do zvoleného cílového data.
    # Tím zabráníme tomu, aby se do souboru s názvem například
    # 2026-09-26 omylem dostal i novější den 2026-09-27.
    dates = {date for date in dates if date <= latest_date}

    # Projdeme všechna dostupná data chronologicky.
    for date in sorted(dates):
        old_row = old_table.get(date, [])
        row = [date]

        for station in station_codes:
            # Nejprve použijeme aktuálně staženou hodnotu podle WSI.
            value = values_by_wsi.get(station, {}).get(date)

            # Pokud ji aktuální stažení nemá, zkusíme staré CSV.
            # To je důležité pro zachování dříve známých hodnot.
            if value is None and station in old_index and old_row:
                index = old_index[station]
                if index < len(old_row):
                    value = parse_number(old_row[index])

            row.append(fmt(value))

        new_rows.append(row)

    # 10) Zapíšeme nové CSV.
    # utf-8-sig pomáhá Excelu správně poznat UTF-8 a české znaky.
    with open(OUTPUT_FILE, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(["Datum"] + station_codes)
        writer.writerows(new_rows)

    # 11) Smažeme staré verze CSV.
    # V repozitáři tak zůstane pouze aktuální soubor s posledním datem.
    for old_file in glob.glob(OUTPUT_PREFIX + "*.csv"):
        if old_file != OUTPUT_FILE:
            os.remove(old_file)

    print(f"Hotovo: {OUTPUT_FILE}, {len(new_rows)} dnů × {len(station_codes)} stanic.")


# Tato část se spustí pouze tehdy, když soubor agent.py spustíme přímo.
# V GitHub Actions se tedy spustí právě main().
if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Jakákoli neošetřená chyba se vypíše do logu GitHub Actions
        # a sys.exit(1) označí běh jako neúspěšný.
        print(f"KRITICKÁ CHYBA: {exc}")
        sys.exit(1)
