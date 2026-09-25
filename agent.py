import os
import csv
import time
import requests
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# Oficiální otevřená data ČHMÚ (nahrazují starý neplatný zdroj "https://chmi.cz")
# Popis formátu: https://opendata.chmi.cz/meteorology/climate/Klimatologicka_data_popis.pdf
META_URL = "https://opendata.chmi.cz/meteorology/climate/now/metadata/meta1-{datum}.json"
DATA_URL = "https://opendata.chmi.cz/meteorology/climate/now/data/1h-{wsi}-{datum}.json"

OUTPUT_FILE = "srazky_vsechny_stanice.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (chmu-srazky-agent)"}
TIMEOUT = 20
POCET_VLAKEN = 20  # kolik stanic stahovat souběžně


def stahni_json(url):
    """Stáhne a vrátí JSON z dané URL, nebo None, pokud se to nepovede (např. 404)."""
    try:
        odpoved = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if odpoved.status_code != 200:
            return None
        return odpoved.json()
    except Exception:
        return None


def ziskej_seznam_stanic(datum_utc):
    """Stáhne seznam stanic (WSI + název) z metadatového souboru meta1 pro dané datum (YYYYMMDD, UTC).

    Pokud pro dnešní den ještě metadata nejsou k dispozici, zkusí to se včerejším datem.
    """
    for datum in (datum_utc, (datetime.strptime(datum_utc, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")):
        data = stahni_json(META_URL.format(datum=datum))
        if isinstance(data, list) and data:
            stanice = []
            for zaznam in data:
                if not isinstance(zaznam, dict):
                    continue
                wsi = zaznam.get("WSI")
                nazev = zaznam.get("FULL_NAME") or zaznam.get("GH_ID") or wsi
                if wsi and nazev:
                    stanice.append((wsi, str(nazev).replace(";", " ").strip()))
            if stanice:
                return stanice, datum
    return [], datum_utc


def extrahuj_sra1h(data):
    """Z JSON dat hodinového souboru vytáhne a sečte hodnoty prvku SRA1H (hodinový úhrn srážek).

    Formát JSON souborů opendata.chmi.cz není nikde zveřejněn jako přesné schéma na úrovni
    jednotlivých záznamů, proto je parsování odolné vůči drobným odlišnostem ve struktuře.
    """
    celkem = 0.0
    nalezeno = False

    def zpracuj_zaznam(zaznam):
        nonlocal celkem, nalezeno
        if not isinstance(zaznam, dict):
            return
        je_sra1h = any(
            isinstance(hodnota, str) and hodnota.strip().upper() == "SRA1H"
            for hodnota in zaznam.values()
        )
        if not je_sra1h:
            return
        for klic, hodnota in zaznam.items():
            if isinstance(hodnota, (int, float)) and "flag" not in klic.lower():
                celkem += float(hodnota)
                nalezeno = True
                break

    if isinstance(data, list):
        for zaznam in data:
            zpracuj_zaznam(zaznam)
    elif isinstance(data, dict):
        for klic, hodnota in data.items():
            if isinstance(klic, str) and klic.strip().upper() == "SRA1H" and isinstance(hodnota, list):
                for polozka in hodnota:
                    if isinstance(polozka, dict):
                        for k2, v2 in polozka.items():
                            if isinstance(v2, (int, float)) and "flag" not in k2.lower():
                                celkem += float(v2)
                                nalezeno = True
                                break
                    elif isinstance(polozka, (int, float)):
                        celkem += float(polozka)
                        nalezeno = True
            elif isinstance(hodnota, list):
                for polozka in hodnota:
                    zpracuj_zaznam(polozka)

    return celkem if nalezeno else None


def stahni_srazky_stanice(wsi, datum_utc):
    data = stahni_json(DATA_URL.format(wsi=wsi, datum=datum_utc))
    if data is None:
        return None
    return extrahuj_sra1h(data)


def stahni_vsechny_stanice_final():
    try:
        dnesni_datum = datetime.now().strftime("%Y-%m-%d")
        datum_utc = datetime.now(timezone.utc).strftime("%Y%m%d")

        seznam_stanic_wsi, pouzity_datum_utc = ziskej_seznam_stanic(datum_utc)
        if not seznam_stanic_wsi:
            print("Nepodařilo se načíst seznam stanic z otevřených dat ČHMÚ.")
            return

        nove_srazky_mapa = {}
        vsechny_stanice_set = set()

        with ThreadPoolExecutor(max_workers=POCET_VLAKEN) as executor:
            budoucnosti = {
                executor.submit(stahni_srazky_stanice, wsi, pouzity_datum_utc): nazev
                for wsi, nazev in seznam_stanic_wsi
            }
            for budoucnost in as_completed(budoucnosti):
                nazev_stanice = budoucnosti[budoucnost]
                try:
                    hodnota = budoucnost.result()
                except Exception:
                    hodnota = None
                if hodnota is None:
                    continue
                # pokud stejný název stanice dorazí vícekrát, sečteme (mělo by být vzácné)
                nove_srazky_mapa[nazev_stanice] = nove_srazky_mapa.get(nazev_stanice, 0.0) + hodnota
                vsechny_stanice_set.add(nazev_stanice)

        if not nove_srazky_mapa:
            print("Nepodařilo se načíst žádná data o srážkách z otevřených dat ČHMÚ.")
            return

        print(f"Načteno {len(nove_srazky_mapa)} stanic se srážkovými daty.")

        # Seřadíme stanice abecedně, aby sloupce v Excelu měly řád
        seznam_stanic = sorted(list(vsechny_stanice_set))
        hlavicka = ["Datum"] + seznam_stanic

        # Načteme dosavadní historii, pokud už soubor existuje
        stara_historie_radky = {}
        hlavicka_stara = []
        if os.path.exists(OUTPUT_FILE):
            try:
                with open(OUTPUT_FILE, mode='r', encoding='utf-8-sig') as f:
                    reader = csv.reader(f, delimiter=';')
                    hlavicka_stara = next(reader, None)
                    for radek in reader:
                        if radek:
                            den = radek[0]
                            if den != dnesni_datum:
                                stara_historie_radky[den] = radek
            except Exception:
                pass

        # Spojíme staré dny a dnešní den dohromady
        vsechny_dny = sorted(list(set(stara_historie_radky.keys()) | {dnesni_datum}))
        nova_tabulka_zapis = []

        for den in vsechny_dny:
            novy_radek = [den]
            for stanice in seznam_stanic:
                hodnota = 0.0

                # 1. Zkusíme vzít čerstvou hodnotu ze stažených dat
                if den == dnesni_datum and stanice in nove_srazky_mapa:
                    hodnota = nove_srazky_mapa[stanice]
                # 2. Pokud jde o starší historii, zkusíme ji vytáhnout ze starého souboru
                elif den in stara_historie_radky and hlavicka_stara and stanice in hlavicka_stara:
                    try:
                        idx = hlavicka_stara.index(stanice)
                        hodnota = float(stara_historie_radky[den][idx].replace(',', '.'))
                    except Exception:
                        hodnota = 0.0

                # ÚPRAVA PRO ČESKÝ EXCEL: zaokrouhlit na 1 desetinné místo a změnit tečku na čárku
                text_hodnoty = str(round(hodnota, 1)).replace('.', ',')
                novy_radek.append(text_hodnoty)

            nova_tabulka_zapis.append(novy_radek)

        # ZÁPIS DO SOUBORU SE STŘEDNÍKEM
        with open(OUTPUT_FILE, mode='w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(hlavicka)
            writer.writerows(nova_tabulka_zapis)

        print("Úspěch! Všechny stanice byly zapsány.")

    except Exception as e:
        print(f"Kritická chyba: {e}")


if __name__ == "__main__":
    stahni_vsechny_stanice_final()
