import os
import requests
import csv

# Společný JSON zdroj pro všechny stanice ČHMÚ
URL_JSON = "https://chmi.cz"
OUTPUT_FILE = "srazky_vsechny_stanice.csv"

def stahni_vsechny_stanice_rucne():
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(URL_JSON, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # 1. Zjistíme všechny dostupné dny a stanice
        vsechna_data = set()
        vsechny_stanice = set()
        srazky_mapa = {}
        
        for klic, obsah in data.items():
            if isinstance(obsah, dict) and "nazev" in obsah:
                nazev_stanice = obsah.get("nazev").strip().replace(';', ' ')
                srazky_historie = obsah.get("srazky_6dni", [])
                
                for zaznam in srazky_historie:
                    datum = zaznam.get("datum")
                    hodnota = zaznam.get("hodnota")
                    
                    if datum and hodnota is not None:
                        vsechna_data.add(datum)
                        vsechny_stanice.add(nazev_stanice)
                        
                        if datum not in srazky_mapa:
                            srazky_mapa[datum] = {}
                        srazky_mapa[datum][nazev_stanice] = float(hodnota)
                        
        if not srazky_mapa:
            print("Zdroje ČHMÚ nevrátily žádná platná data.")
            return

        seznam_dnu = sorted(list(vsechna_data))
        seznam_stanic = sorted(list(vsechny_stanice))
        
        # 2. Načteme historii, pokud existuje
        stara_data_radky = {}
        if os.path.exists(OUTPUT_FILE):
            try:
                with open(OUTPUT_FILE, mode='r', encoding='utf-8-sig') as f:
                    reader = csv.reader(f, delimiter=';')
                    hlavicka_stara = next(reader, None)
                    if hlavicka_stara:
                        for radek in reader:
                            if radek:
                                den = radek[0]
                                stara_data_radky[den] = radek
            except Exception as e:
                print(f"Nepodařilo se načíst staré CSV: {e}")

        # 3. Vygenerujeme novou tabulku do paměti
        nova_tabulka_radky = []
        hlavicka = ["Datum"] + seznam_stanic
        
        vsechny_dny_ke_zpracovani = sorted(list(set(seznam_dnu) | set(stara_data_radky.keys())))
        
        for den in vsechny_dny_ke_zpracovani:
            novy_radek = [den]
            
            for stanice in seznam_stanic:
                hodnota_srazek = 0.0
                
                if den in srazky_mapa and stanice in srazky_mapa[den]:
                    hodnota_srazek = srazky_mapa[den][stanice]
                elif den in stara_data_radky and 'hlavicka_stara' in locals() and hlavicka_stara:
                    try:
                        index_stanice = hlavicka_stara.index(stanice)
                        stara_hodnota = stara_data_radky[den][index_stanice]
                        hodnota_srazek = float(stara_hodnota.replace(',', '.'))
                    except:
                        hodnota_srazek = 0.0
                        
                text_hodnoty = str(round(hodnota_srazek, 1)).replace('.', ',')
                novy_radek.append(text_hodnoty)
                
            nova_tabulka_radky.append(novy_radek)

        # 4. Zápis do souboru se středníkem
        with open(OUTPUT_FILE, mode='w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(hlavicka)
            writer.writerows(nova_tabulka_radky)
            
        print(f"Úspěch! Soubor {OUTPUT_FILE} byl vytvořen. Počet řádků: {len(nova_tabulka_radky)}")

    except Exception as e:
        print(f"Kritická chyba: {e}")

if __name__ == "__main__":
    stahni_vsechny_stanice_rucne()
