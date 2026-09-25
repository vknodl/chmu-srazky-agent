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
        
        # 1. Projdeme celou strukturu ČHMÚ a získáme data
        for klic, obsah in data.items():
            if isinstance(obsah, dict) and "nazev" in obsah:
                nazev_stanice = obsah.get("nazev")
                srazky_historie = obsah.get("srazky_6dni", [])
                
                for zaznam in srazky_historie:
                    if zaznam.get("datum") and zaznam.get("hodnota") is not None:
                        docasny_seznam.append({
                            "Datum": zaznam.get("datum"),
                            "Stanice": nazev_stanice,
                            "Srazky": float(zaznam.get("hodnota"))
                        })
        
        if not docasny_seznam:
            print("Zdroje ČHMÚ nevrátily žádná platná data o srážkách.")
            return

        df_surova = pd.DataFrame(docasny_seznam)
        
        # 2. Přeskládáme tabulku: Řádky = Dny, Sloupce = Názvy stanic
        df_nove = df_surova.pivot(index="Datum", columns="Stanice", values="Srazky").reset_index()
        
        # 3. Kontrola stávající historie v repozitáři
        if os.path.exists(OUTPUT_FILE):
            try:
                df_stare = pd.read_csv(OUTPUT_FILE, sep=';', encoding="utf-8-sig")
                # Vyčistíme české čárky zpět na tečky pro spojení dat v Pandas
                for col in df_stare.columns:
                    if col != "Datum":
                        df_stare[col] = df_stare[col].astype(str).str.replace(',', '.', regex=False)
                        df_stare[col] = pd.to_numeric(df_stare[col], errors='coerce')
                
                df_vysledne = pd.concat([df_stare, df_nove]).drop_duplicates(subset=["Datum"], keep="last")
            except Exception as e:
                print(f"Staré CSV nebylo možné načíst, vytvářím čisté nové: {e}")
                df_vysledne = df_nove
        else:
            df_vysledne = df_nove
            
        # Seřadíme tabulku chronologicky podle dnů
        df_vysledne = df_vysledne.sort_values(by="Datum")
        
        # Abecedně seřadíme sloupce se stanicemi (Datum zůstane první)
        stanice_sloupce = sorted([c for c in df_vysledne.columns if c != "Datum"])
        df_vysledne = df_vysledne[["Datum"] + stanice_sloupce]
        
        # 4. ÚPRAVA PRO ČESKÝ EXCEL: Nahrazení teček čárkami a ošetření chybějících hodnot
        for col in df_vysledne.columns:
            if col != "Datum":
                df_vysledne[col] = pd.to_numeric(df_vysledne[col], errors='coerce').round(1)
                df_vysledne[col] = df_vysledne[col].astype(str).str.replace('.', ',', regex=False)
                df_vysledne[col] = df_vysledne[col].str.replace('nan', '0,0', regex=False)
                
        # Uložení s oddělovačem středníků
        df_vysledne.to_csv(OUTPUT_FILE, sep=';', index=False, encoding="utf-8-sig")
        print(f"Soubor {OUTPUT_FILE} byl úspěšně vygenerován.")
        
    except Exception as e:
        print(f"Chyba při parsování dat: {e}")

if __name__ == "__main__":
    df = stahni_vsechny_stanice_excel()
