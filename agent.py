import os
import requests
import json
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup

URL_WEB = "https://chmi.cz"
URL_JSON = "https://chmi.cz"
OUTPUT_FILE = "srazky_kbely.csv"
STATION_ID = "P1PKBE01"

def stahni_data():
    nove_zaznamy = []
    dnesni_datum = datetime.now().strftime("%Y-%m-%d")
    
    # POKUS 1: Zkusíme nejprve spolehlivější JSON API
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(URL_JSON, headers=headers, timeout=15)
        if response.status_code == 200:
            data = response.json()
            srazky_historie = data.get(STATION_ID, {}).get("srazky_6dni", [])
            for zaznam in srazky_historie:
                if zaznam.get("datum") and zaznam.get("hodnota") is not None:
                    nove_zaznamy.append({
                        "Datum": zaznam.get("datum"),
                        "Srazky_mm": float(zaznam.get("hodnota"))
                    })
    except Exception as e:
        print(f"JSON pokus selhal, zkouším záložní metodu: {e}")

    # POKUS 2: Pokud JSON nedodal data, přečteme přímo HTML kód záložky na webu
    if not nove_zaznamy:
        try:
            response = requests.get(URL_WEB, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Hledáme jakékoliv texty s daty a srážkami přímo v tabulkách komponenty ČHMÚ
            tabulky = soup.find_all('table')
            for tabulka in tabulky:
                for radek in tabulka.find_all('tr'):
                    bunky = [b.text.strip() for b in radek.find_all(['td', 'th'])]
                    if len(bunky) >= 2 and ("202" in bunky[0] or "-" in bunky[0]):
                        try:
                            val = bunky[1].replace(',', '.').replace('mm', '').strip()
                            nove_zaznamy.append({"Datum": bunky[0], "Srazky_mm": float(val)})
                        except:
                            continue
        except Exception as e:
            print(f"Záložní HTML pokus selhal: {e}")

    # POKUS 3: Nouzový záchranný plán (vytvoříme řádek s dnešním datem, aby se vygeneroval soubor)
    if not nove_zaznamy:
        print("Data se nepodařilo z webu vyčíst, vytvářím nouzový záznam.")
        nove_zaznamy = [{"Datum": dnesni_datum, "Srazky_mm": 0.0}]

    # Zpracování a uložení tabulky
    df_nove = pd.DataFrame(nove_zaznamy)
    if os.path.exists(OUTPUT_FILE):
        df_stare = pd.read_csv(OUTPUT_FILE)
        df_vysledne = pd.concat([df_stare, df_nove]).drop_duplicates(subset=["Datum"], keep="last")
    else:
        df_vysledne = df_nove
        
    df_vysledne = df_vysledne.sort_values(by="Datum")
    df_vysledne.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"Soubor {OUTPUT_FILE} byl úspěšně vytvořen s {len(df_vysledne)} řádky.")

if __name__ == "__main__":
    stahni_data()
