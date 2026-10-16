## Vytvořeno pro: [https://ministrmystery.cz](https://ministrmystery.cz)

Počítadlo aktivity v Kick chatu — stejný styl/architektura jako
[KickBot](https://github.com/brezipe/KickBot) (soutěžní bot), ale místo
soutěže o číslo jen počítá, kolik zpráv kdo napsal od připojení, a na
požádání (tlačítkem nebo chatovým příkazem) vyhlásí žebříček.

- Živý žebříček se aktualizuje v okně appky při každé zprávě.
- `!top` (moderátor) — vyhlásí průběžné pořadí do chatu, nic neresetuje.
- `!stop` (moderátor) — vyhlásí finální pořadí do chatu a vynuluje počítadlo
  pro další sérii (appka běží dál, jen začíná počítat od nuly).
- Texty a příkazy jdou upravit v `bot_config.json` (vytvoří se při prvním
  spuštění), stejně jako u KickBota.

python3 -m venv venv

Win:

PS C:\xx\bot> . .\venv\Scripts\Activate.ps1

Linux:

$ source ./venv/bin/activate

pip install -r requirements.txt

python kick_counter_gui.py

python build_exe.py

dist/ Tady je exe
