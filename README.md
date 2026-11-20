# Srážkový agent ČHMÚ – Praha-Kbely

Jednoduchý skript stahuje denní úhrny srážek (prvek `SRA`) pouze pro meteorologickou stanici **Praha-Kbely**.

## Zdroj dat

- ČHMÚ Open Data: https://opendata.chmi.cz/meteorology/climate/recent/data/daily/
- WSI stanice: `0-20000-0-11518` (Praha-Kbely)

Skript stahuje jeden měsíční JSON soubor stanice. Nepoužívá seznam metadat ani nestahuje ostatní stanice.

## Výstup

Soubor `srazky_kbely_praha.csv` obsahuje dva sloupce:

- `Datum`
- `SRA_mm` – denní úhrn srážek v mm

Sloupce jsou oddělené tabulátorem (TAB), desetinným oddělovačem je tečka. Chybějící měření zůstává prázdné. Historie se zachovává mezi jednotlivými spuštěními.

## Spuštění

Workflow lze spustit ručně v GitHub Actions (**Srážky – Praha-Kbely**) nebo automaticky každý den v 09:15 pražského času.

Lokálně:

```bash
python -m pip install -r requirements.txt
python agent.py
```
