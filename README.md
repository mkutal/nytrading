# NY Trading

Samostatný výzkumný projekt pro XAUUSD, pouze New York. Repozitář: https://github.com/mkutal/nytrading. Software obsahuje offline backtest a worker `scanner.py` pro živá Twelve Data a paper signály do Discordu. Scanner neodesílá příkazy brokerovi. Původní XAUUSD Gold Lab není upraven. London je pro tuto strategii vyřazen: všechny dosud porovnané londýnské varianty měly za celé testované období záporný výsledek. Nejlepší z nich samostatně ztratila přibližně $3 194; v kombinovaném účtu přispěla ztrátou $3 313 kvůli odlišné průběžné velikosti účtu.

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

## Proměnné prostředí

Šablona je v `.env.example`. Exportované proměnné prostředí mají přednost před `settings.json`; samotný soubor `.env` se automaticky nenačítá. Na hostingu je nastav v jeho Variables.

Aktivní backtest načítá `NEW_YORK_START`, `NEW_YORK_END`, `NEW_YORK_TIMEZONE`, `TP_RR`, `SIZING_RISK_PERCENT`, `INITIAL_EQUITY_USD` a `DATA_DIR`. Hodnota risku `1` znamená 1 %, tedy interně 0,01. `NEW_YORK_END` je současně konec vstupů i povinný čas uzavření pozice. Časy se interpretují v `America/New_York`, včetně DST. `DATA_DIR="/data"` vyžaduje, aby v této složce byly historické JSON soubory; bez nastavení se používá projektová složka `data`.

`scanner.py` používá `TWELVE_DATA_API_KEY` a `DISCORD_WEBHOOK_URL`. Offline `backtest.py` tyto hodnoty stále nečte a nic neodesílá. `SCANNER_DRY_RUN=true` zapne sledování a logování bez Discord zpráv, s odděleným virtuálním účtem. Skutečná tajemství patří do Railway Variables nebo lokálního ignorovaného prostředí, nikoli do Git repozitáře.

## Živý paper scanner a Railway

Scanner načítá UTC OHLC pro XAU/USD. M5 aktualizuje při nové pětiminutové svíčce, M15/H1 při jejich uzavření. Indikátory mají warmup 1000 M15 a 500 H1 barů; po odfiltrování víkendů používá stejnou funkci band reversion jako backtest. Do signálu vstupují pouze uzavřené M15/H1. Entry používá open právě začínající M5 plus modelovaný spread a skluz, takže jde o indikativní cenu, nikoli potvrzené plnění. Po startu se neposílají staré příležitosti: maximální stáří signálu je 90 sekund a vstup musí ležet v NY okně.

Discord zprávy mají stejné členění jako Gold Lab: BUY/SELL, Entry, Stop loss, Take profit · 100% · 1.7R, Risk budget, **Volume (total) v lotech**, SL distance a Price risk at SL. Přidaný Planned risk incl. costs zahrnuje modelovanou komisi a skluz při SL. Risk budget vychází z dynamické virtuální equity, nikoli z napojení na skutečný účet. H1 popisek uvádí nízkou směrovost pro band reversion; nepředstírá potvrzení býčího/medvědího EMA trendu. [Ukázka zpráv](reports/discord-preview.md).

**Všechny časy v Discordu jsou pouze Europe/Prague**, včetně data a času výsledku. Běžné okno je 16:00–19:30. Ve dnech 25.–31. 10. 2026 je 15:00–18:30 kvůli rozdílnému týdnu změny letního času v USA a Evropě; víkendy scanner vynechává. Přesný čas povinného uzavření je vždy přímo v signálu. Interní časování zůstává navázané na NY session z backtestu a převádí se automaticky. `NEW_YORK_TIMEZONE` je volitelná; pokud je nastavená, musí být `America/New_York`. Pro české zobrazování není potřeba měnit Variables ani přidávat časovou zónu.

Pozice běží do TP/SL nebo povinného konce session. Zpráva **Ending the session** současně říká No more entries a připomíná ruční uzavření zbývající skutečné pozice. Při současném konci vstupů a držení se neposílá zvláštní dřívější cutoff. Oznámení se posílá jednou za sledovanou pracovní session, i když nevznikl žádný obchod; nezávisí na dostupnosti Twelve Data v okamžiku konce. Při startu mimo okno bez předchozího záznamu se historické session nedohánějí.

Po modelovém uzavření se odešle TAKE PROFIT HIT, STOP LOSS HIT nebo SESSION CLOSED s Entry, Reference exit, loty a čistým výsledkem v R i USD po nákladech. Výsledek M5 se potvrdí až po dokončení svíčky a dostupnosti čerstvých dat; zpráva o konci session není potvrzením brokerova uzavření. Uvnitř modelu je povolena pouze jedna otevřená pozice; její TP/SL/deadline se sleduje z dokončených M5 svíček, konzervativně SL před TP. Svíčka vstupu může obsahovat pohyb před doručením alertu; pro paper účet se celý rozsah zpracuje konzervativně. Startup ani testovací zprávy do Discordu neodesíláme.

Modelový účet začíná na `INITIAL_EQUITY_USD`, aktualizuje se podle virtuálních výsledků a přetrvává přes restart. Není synchronizován se skutečnou equity FTMO/MT5. Limity 5 % denní ztráty s resetem Europe/Prague a statických 10 % celkové ztráty kontroluje na modelu spolu s interní 4% denní rezervou. [FTMO 2-Step pravidla](https://ftmo.com/en/trading-objectives/). Chybějící M5 při otevřené modelové pozici nebo porušení limitu účet zastaví; neodhadne výsledek ani nezačne automaticky znovu. Zprávy nejsou důkazem souladu skutečného účtu se všemi FTMO pravidly a neobsahují makro news filtr.

Rezervace signálu i modelové pozice se uloží před Discord POST. Stejná ochrana před opakováním přes restart platí pro výsledky a session oznámení. Potvrzení používá `wait=true`. Při nejasném výsledku HTTP požadavku se zpráva slepě neopakuje; může chybět doručení a je potřeba zkontrolovat Discord i stav. Staré čekající session pokyny z předchozího dne se neodesílají; opožděné paper záznamy jsou označené. Stav je v `/data/ny-trading.json`, u dry-run odděleně; soubor nemaž při běžném redeployi. Starší stav se doplní o oznámení při zachování equity a pozice. Souborový zámek brání dvěma workerům používat stejný stav.

Railway nasazení:

1. Použij samostatnou službu NY Trading, zdroj `mkutal/nytrading`, větev `main`.
2. V projektu vytvoř **Volume**, přiřaď jej této službě a nastav mount path **`/data`**. Samotná `DATA_DIR` úložiště nevytváří. [Railway Volumes](https://docs.railway.com/volumes).
3. Ponech nastavené Variables podle `.env.example`; webhook/API klíč vyplň jen v Railway. Pro scanner není nutné nahrávat historické JSON soubory do Volume.
4. Repo obsahuje Dockerfile a `railway.json`: start **`python -u scanner.py`**, jedna replika, Serverless vypnutý a požadovaný mount `/data`. Worker běží trvale, nemá veřejný HTTP server a nepotřebuje doménu/HTTP healthcheck. [Railway konfigurace](https://docs.railway.com/config-as-code/reference).
5. Použij auto-deploy nového commitu nebo v Command Palette **Deploy Latest Commit**. Běžný Redeploy může znovu použít předchozí obraz. [Deployment Actions](https://docs.railway.com/deployments/deployment-actions).
6. V logu očekávej `NY_TRADING_READY`, následně `STATUS waiting_for_NY` mimo okno nebo `STATUS active` v okně. Tyto zprávy potvrzují start workeru; skutečná oprávnění API a Discord doručení se potvrdí až požadavky. `CONFIGURATION_ERROR` říká, co chybí. `FEED_UNAVAILABLE` znamená chybu/opoždění Twelve Data, `SIGNAL_SENT` potvrzené odeslání vstupu a `NOTICE_SENT` potvrzené odeslání výsledku/session. INFO jde na stdout, skutečná varování/chyby na stderr.

Na sdíleném Twelve Data klíči obě služby čerpají stejný kreditní limit. Worker mimo NY nevolá API, pokud neřeší neuzavřenou modelovou pozici; H1/M15 používá cache. [Twelve Data kredity](https://support.twelvedata.com/en/articles/5615854-credits). Delay, seed ATR z jiného začátku historie, provider revize a skutečné bid/ask/fill ceny mohou vytvořit rozdíl mezi živým paper výsledkem a starým backtestem.

Lokálně, s exportovanými variables:

```sh
python3 scanner.py --check-config
python3 scanner.py --dry-run --once
python3 scanner.py
```

`--check-config` nevolá API ani Discord. `--dry-run` volá Twelve Data, ale neodesílá zprávy. Dosavadní integrační testy používají HTTP fixtures; skutečná Railway služba a její credentials nejsou z místního prostředí ověřeny.

## Ověření kódu

```sh
python3 -m unittest discover -s tests -v
```

Testy kontrolují exekuci, náklady, risk, FTMO ztrátové limity, souběh pozic, práci s budoucími daty, scanner freshness, restart/deduplikaci a selhání doručení. Testy na skutečných historických datech se při jejich absenci výslovně přeskočí. Replay modelového účtu reprodukuje vybraný backtest. GitHub Actions spouští sadu při pushi/pull requestu a sestaví Docker obraz s kontrolou konfigurace bez skutečných credentials. `reports/backtest-summary.json` obsahuje souhrn vybrané NY varianty; celé obchody a equity se generují lokálně.

## Omezení a provozní stav

Datové soubory jsou samostatné kopie historických Twelve Data OHLC s UTC časem otevření svíčky. V indikátorech jsou vynechány víkendy kvůli problematickým víkendovým barům poskytovatele; nejde o ověřený obchodní kalendář brokera. OHLC neobsahuje skutečný bid/ask ani pořadí ticků. Při společném zásahu TP/SL má přednost SL; skutečný intradenní drawdown nelze z M5 přesně rekonstruovat. Model počítá 100 oz/lot a referenční páku 30. Pozice se nedrží přes noc, proto swap není účtován.

Vzorek 20–24 obchodů je malý a zkoušeli jsme více strategií, časů a cílů. Pozdější časový úsek pochází z již používaného datasetu, takže není nezávislým potvrzením. Před nasazením je potřeba další test na dosud nepoužitých datech a průběžné paper sledování se skutečnými náklady. Výsledky nelze považovat za garantovanou úspěšnost.

Projekt slouží pro historický výzkum a paper signály. London a obchodní exekuce jsou vypnuté. Scanner odešle Discord signál, pokud je nasazen s platným webhookem a vznikne čerstvý vstup. Původní tři auditované zdrojové soubory Gold Lab mají stejné kontrolní součty jako před auditem.
