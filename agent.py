import os
import requests
import csv
from datetime import datetime

# Oficiální textový zdroj ČHMÚ pro srážky za posledních 24 hodin
URL_CHMI = "https://chmi.cz"
OUTPUT_FILE = "srazky_vsechny_stanice.csv"
CILOVA_STANICE = "Praha-Kbely"

def stahni_srazky_kbely():
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(URL_CHMI, headers=headers, timeout=20)
        response.encoding = 'cp1250' # České kódování pro ČHMÚ data
        
        dnesni_datum = datetime.now().strftime("%Y-%m-%d")
        srazky_hodnota = "0,0" # Výchozí hodnota, pokud stanici nenajdeme nebo bude výpadek
        
        if response.status_code == 200:
            radky_textu = response.text.split('\n')
            for radek in radky_textu:
                if radek.strip() and not radek.startswith('#'):
                    casti = [c.strip() for c in radek.split('\t') if c.strip()]
                    if len(casti) >= 2:
                        nazev_stanice = casti[0].replace(';', ' ')
                        
                        # Pokud jsme našli Kbely, vytáhneme hodnotu srážek
                        if CILOVA_STANICE.lower() in nazev_stanice.lower():
                            try:
                                # Očistíme text od mezer a převedeme na české zobrazení
                                cislo_text = casti[1].replace(',', '.').strip()
                                hodnota_float = float(cislo_text)
                                srazky_hodnota = str(round(hodnota_float, 1)).replace('.', ',')
                            except:
                                srazky_hodnota = "0,0"
                            break

        # Načteme dosavadní historii, pokud už soubor existuje
        stara_historie = []
        if os.path.exists(OUTPUT_FILE):
            try:
                with open(OUTPUT_FILE, mode='r', encoding='utf-8-sig') as f:
                    reader = csv.reader(f, delimiter=';')
                    hlavicka = next(reader, None)
                    for radek in reader:
                        # Ochrana, abychom neduplikovali dnešní den, pokud skript pustíš víckrát
                        if radek and radek[0] != dnesni_datum:
                            stara_historie.append(radek)
            except:
                pass

        # Definujeme pevnou a přehlednou hlavičku pro dva sloupce
        hlavicka = ["Datum", "Praha-Kbely"]
        dnesni_radek = [dnesni_datum, srazky_hodnota]

        # Zápis do souboru se středníkem pro český Excel
        with open(OUTPUT_FILE, mode='w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(hlavicka)
            writer.writerows(stara_historie)
            writer.writerow(dnesni_radek)
            
        print(f"Úspěch! Soubor {OUTPUT_FILE} byl kompletně vytvořen.")

    except Exception as e:
        print(f"Kritická chyba: {e}")

if __name__ == "__main__":
    stahni_srazky_kbely()
