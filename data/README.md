# Historická data (lokální)

Backtest vyžaduje `5min.json`, `15min.json` a `1h.json` v této složce. Data nejsou součástí Git repozitáře. Na tomto počítači už jsou samostatné kopie připravené; další instalace potřebuje zkopírovat tytéž soubory nebo dodat odpovídající exporty Twelve Data.

Formát je JSON pole OHLC objektů s klíči `datetime`, `open`, `high`, `low`, `close`. Hodnoty cen mohou být čísla nebo číselné řetězce. Čas je začátek svíčky v UTC. U záznamů bez časové zóny loader předpokládá UTC pouze na základě ověřených download manifestů. Export z jiného časového pásma musí být před použitím převeden.

Uložené manifesty obsahují kontrolní součty a informace o původních downloadech. Současný backtest má pevně nastavené období 1. 4.–2. 10. 2026 a potřebuje i předchozí data pro výpočet indikátorů. Nové období vyžaduje změnu časových hranic v `backtest.py` a nový audit dat.
