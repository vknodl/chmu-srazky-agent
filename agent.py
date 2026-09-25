import os
import requests
import pandas as pd
from datetime import datetime

URL = "https://chmi.cz"
OUTPUT_FILE = "srazky_kbely.csv"
STATION_ID = "P1PKBE01"

def stahni_data():
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(URL, headers=headers, timeout=15)
        response.raise_for_status()

        data = response.json()
        stanice_data = data.get(STATION_ID, {})
        srazky_historie = stanice_data.get("srazky_6dni", [])

        if not srazky_historie:
            print("Data pro stanici Praha-Kbely nebyla nalezena.")
            return

        nove_zaznamy = []
        for zaznam in srazky_historie:
            nove_zaznamy.append({
                "Datum": zaznam.get("datum"),
                "Srazky_mm": zaznam.get("hodnota")
            })

        df_nove = pd.DataFrame(nove_zaznamy)

        if os.path.exists(OUTPUT_FILE):
            df_stare = pd.read_csv(OUTPUT_FILE)
            df_vysledne = pd.concat([df_stare, df_nove]).drop_duplicates(subset=["Datum"], keep="last")
        else:
            df_vysledne = df_nove

        df_vysledne = df_vysledne.sort_values(by="Datum")
        df_vysledne.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
        print(f"Data úspěšně uložena.")

    except Exception as e:
        print(f"Chyba: {e}")

if __name__ == "__main__":
    stahni_data()
