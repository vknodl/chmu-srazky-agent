import os
import requests
import pandas as pd
from datetime import datetime

# Společný JSON zdroj pro všechny stanice ČHMÚ
URL_JSON = "https://chmi.cz"
OUTPUT_FILE = "srazky_vsechny_stanice.csv"

def stahni_vsechny_stanice_excel():
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(URL_JSON, headers=headers, timeout=15)
        response.raise_for_status()
        
        data = response.json()
        docasny_seznam = []
        
        # 1. Projdeme všechny stanice a vytáhneme data
        for station_id, station_content in data.items():
            if isinstance(station_content, dict) and "nazev" in station_content:
                nazev_stanice = station_content.get("nazev")
                srazky_historie = station_content.get("srazky_6dni", [])
                
                for zaznam in srazky_historie:
                    if zaznam.get("datum") and zaznam.get("hodnota") is not None:
                        docasny_seznam.append({
                            "Datum": zaznam.get("datum"),
                            "Stanice": nazev_stanice,
                            "Srazky": float(zaznam.get("hodnota"))
                        })
        
        if not docasny_seznam:
            print("Nepodařilo se načíst žádná data ze sítě ČHMÚ.")
            return

        df_surova = pd.DataFrame(docasny_seznam)
        
        # 2. Přeskládáme tabulku: Řádky = Dny, Sloupce = Stanice
        df_nove = df_surova.pivot(index="Datum", columns="Stanice", values="Srazky").reset_index()
        
        # 3. Kontrola, zda už máme starší historii uloženou
        if os.path.exists(OUTPUT_FILE):
            try:
                # Načteme starý soubor a vyčistíme české čárky zpět na tečky pro zpracování v Pythonu
                df_stare = pd.read_csv(OUTPUT_FILE, sep=';', encoding="utf-8-sig")
                for col in df_stare.columns:
                    if col != "Datum":
                        df_stare[col] = df_stare[col].astype(str).str.replace(',', '.', regex=False)
                        df_stare[col] = pd.to_numeric(df_stare[col], errors='coerce')
                
                # Spojíme stará data s novými a aktualizujeme řádky podle data
                df_vysledne = pd.concat([df_stare, df_nove]).drop_duplicates(subset=["Datum"], keep="last")
            except Exception as e:
                print(f"Nepodařilo se načíst staré CSV, vytvářím nové: {e}")
                df_vysledne = df_nove
        else:
            df_vysledne = df_nove
            
        # Seřadíme tabulku chronologicky podle data
        df_vysledne = df_vysledne.sort_values(by="Datum")
        
        # 4. ÚPRAVA PRO ČESKÝ EXCEL: Nahrazení desetinných teček za čárky
        for col in df_vysledne.columns:
            if col != "Datum":
                # Převedeme na text, vyměníme tečku za čárku a prázdná místa ošetříme
                df_vysledne[col] = df_vysledne[col].round(1).astype(str).str.replace('.', ',', regex=False)
                df_vysledne[col] = df_vysledne[col].str.replace('nan', '0,0', regex=False)
                
        # Uložení se středníkem jako oddělovačem sloupců
        df_vysledne.to_csv(OUTPUT_FILE, sep=';', index=False, encoding="utf-8-sig")
        print(f"Hotovo! Tabulka byla uložena do {OUTPUT_FILE}")
        
    except Exception as e:
        print(f"Chyba při běhu skriptu: {e}")

if __name__ == "__main__":
    stahni_vsechny_stanice_excel()
