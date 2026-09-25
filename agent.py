import os
import requests
import csv
from datetime import datetime

# Oficiální textový zdroj ČHMÚ pro srážky za posledních 24 hodin
URL_CHMI = "https://chmi.cz"
OUTPUT_FILE = "srazky_vsechny_stanice.csv"

def stahni_vsechny_stanice_final():
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(URL_CHMI, headers=headers, timeout=20)
        response.encoding = 'cp1250' # České kódování pro ČHMÚ data
        
        dnesni_datum = datetime.now().strftime("%Y-%m-%d")
        nove_srazky_mapa = {}
        vsechny_stanice_set = set()
        
        if response.status_code == 200:
            radky_textu = response.text.split('\n')
            for radek in radky_textu:
                if radek.strip() and not radek.startswith('#'):
                    casti = [c.strip() for c in radek.split('\t') if c.strip()]
                    if len(casti) >= 2:
                        nazev_stanice = casti[0].replace(';', ' ')
                        try:
                            cislo_text = casti[1].replace(',', '.').strip()
                            hodnota_float = float(cislo_text)
                            nove_srazky_mapa[nazev_stanice] = hodnota_float
                            vsechny_stanice_set.add(nazev_stanice)
                        except:
                            nove_srazky_mapa[nazev_stanice] = 0.0
                            vsechny_stanice_set.add(nazev_stanice)

        if not nove_srazky_mapa:
            print("Nepodařilo se načíst žádná data z webu ČHMÚ.")
            return

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
                            if den != dnesni_datum: # TADY BYL OPRAVEN PŘEKLEP V NÁZVU
                                stara_historie_radky[den] = radek
            except:
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
                    except:
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
            
        print(f"Úspěch! Všechny stanice byly zapsány.")

    except Exception as e:
        print(f"Kritická chyba: {e}")

if __name__ == "__main__":
    stahni_vsechny_stanice_final()
