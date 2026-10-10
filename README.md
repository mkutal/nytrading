# NY Trading

Samostatný výzkumný projekt pro XAUUSD, pouze New York. Repozitář: https://github.com/mkutal/nytrading. Software je offline Python backtest; služba pro živé signály zatím není vytvořena. Původní XAUUSD Gold Lab není upraven. London je pro tuto strategii vyřazen: všechny dosud porovnané londýnské varianty měly za celé testované období záporný výsledek. Nejlepší z nich samostatně ztratila přibližně $3 194; v kombinovaném účtu přispěla ztrátou $3 313 kvůli odlišné průběžné velikosti účtu.

## Aktuální nastavení a výsledky

Offline backtest: 1. 4.–2. 10. 2026, počáteční účet $100 000, dynamický risk 1 %, jedna pozice, komise $6/lot za celý obchod. Modelový spread $0,30/oz a skluz $0,05/oz; nejde o ověřené skutečné náklady brokera.

| Varianta | Vstupy v místním čase NY | Cíl TP | Obchody | Výhry po nákladech | Čistý výsledek | PF | Simulovaný max. DD |
|---|---|---|---:|---:|---:|---:|---:|
| Aktuální výchozí nastavení | 10:00–13:30 | 1,7R | 20 | 65,0 % | +$13 386 | 3,02 | 3,97 % |
| Předchozí nastavení (archiv) | 08:30–13:30 | 1,5R | 24 | 58,3 % | +$10 868 | 2,13 | 4,93 % |

Na výslovný pokyn uživatele je nyní výchozí variantou NY 10:00–13:30, TP 1,7R. Vstupy jsou povoleny od 10:00 včetně, do 13:30 bez tohoto času; v 13:30 se uzavře případná zbývající pozice. Předchozí 1,5R varianta zůstává pouze v archivu. Změna výchozího nastavení nepředstavuje nezávislé potvrzení strategie; volba vychází z již prohlížených historických výsledků.

V obou simulacích nebylo zaznamenáno porušení modelovaných denních a celkových ztrátových limitů FTMO 2-Step. To nepotvrzuje splnění všech pravidel FTMO ani průchod oběma fázemi challenge.

## Strategie

Jde o návrat ceny do pásma v málo směrovém trhu (band reversion), nikoli původně navržený EMA trend + pullback. H1 filtr měří směrovost posledních 20 uzavřených hodin. M15 cena překročí pásmo z předchozích 20 uzavřených svíček (průměr ± 2 směrodatné odchylky) a uzavře zpět uvnitř. Potvrzení vyžaduje odpovídající směr těla a uzavření v krajních 35 % rozsahu svíčky. SELL je zrcadlový BUY.

Vstup nastává až po potvrzeném uzavření, na následné M5 svíčce. SL leží za extrémem signální svíčky s rezervou 0,2 ATR; vzdálenost vstup–SL musí být 0,5–2 ATR. K předchozímu průměru musí zbývat prostor alespoň 2R, i když je TP nižší. Pozice se uzavírají nejpozději ve 13:30 New York. Časová zóna America/New_York řeší DST; český čas se proto v týdnech rozdílného přechodu na letní čas mění.

## Spuštění

Vyžaduje Python 3.10+ a standardní knihovnu; nepoužívá externí Python balíčky. Na systémech bez databáze IANA je nutné doplnit podporu časových zón. Historické OHLC soubory zůstávají lokální a nejsou v Git repozitáři; formát a umístění popisuje `data/README.md`. Ze složky projektu:

```sh
python3 backtest.py
```

Aktuální výsledky jsou v `results/working/`: souhrn JSON, jednotlivé obchody a equity CSV. `settings.json` drží výchozí NY 10:00–13:30, TP 1,7R. Volba `--profile historical_candidate` je zachována pro kompatibilitu a odpovídá téže vybrané variantě. `results/previous_working/` archivuje předchozí 1,5R variantu. `design.json` a `selection.json` dokumentují původní výběr před rozhodnutím uživatele; zahrnují i již vyřazený London.

## Ověření kódu

```sh
python3 -m unittest discover -s tests -v
```

Testy kontrolují exekuci, náklady, risk, FTMO ztrátové limity, souběh pozic a práci s budoucími daty. Jeden test kauzality používá skutečná historická data; při jejich absenci se výslovně přeskočí. GitHub Actions spouští sadu při pushi a pull requestu. `reports/backtest-summary.json` obsahuje souhrn vybrané NY varianty; celé obchody a equity se generují lokálně.

## Omezení a provozní stav

Datové soubory jsou samostatné kopie historických Twelve Data OHLC s UTC časem otevření svíčky. V indikátorech jsou vynechány víkendy kvůli problematickým víkendovým barům poskytovatele; nejde o ověřený obchodní kalendář brokera. OHLC neobsahuje skutečný bid/ask ani pořadí ticků. Při společném zásahu TP/SL má přednost SL; skutečný intradenní drawdown nelze z M5 přesně rekonstruovat. Model počítá 100 oz/lot a referenční páku 30. Pozice se nedrží přes noc, proto swap není účtován.

Vzorek 20–24 obchodů je malý a zkoušeli jsme více strategií, časů a cílů. Pozdější časový úsek pochází z již používaného datasetu, takže není nezávislým potvrzením. Před nasazením je potřeba další test na dosud nepoužitých datech a průběžné paper sledování se skutečnými náklady. Výsledky nelze považovat za garantovanou úspěšnost.

Projekt je pouze offline výzkum. London, Discord i obchodní exekuce jsou vypnuté. Žádný webhook ani broker není připojen. Původní tři auditované zdrojové soubory Gold Lab mají při dokončení změny stejné kontrolní součty jako před auditem.
