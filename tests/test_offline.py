"""Test senza rete: parsing, regole e punteggio.

    python -m tests.test_offline

Le funzioni che chiamano la rete non sono coperte: si provano con
`python -m pipeline.build` quando la connessione c'è.
"""
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import rules, score
from pipeline.build import build, load_config
from pipeline.sources import parse_form4, parse_prices_csv

F = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
ok, bad = 0, []


def check(name, cond, detail=""):
    global ok
    if cond:
        ok += 1
        print(f"  ok  {name}")
    else:
        bad.append(name)
        print(f"  NO  {name} {detail}")


def read(n):
    with open(os.path.join(F, n), encoding="utf-8") as f:
        return f.read()


print("Parsing del Form 4")
buy = parse_form4(read("form4_buy.xml"))
check("un'operazione estratta", len(buy) == 1, buy)
check("nome e ruolo", buy[0]["who"] == "Okafor Maya" and "Chief Executive" in buy[0]["role"])
check("codice P", buy[0]["code"] == "P")
check("valore 40000 x 40,10", round(buy[0]["value"]) == 1604000, buy[0]["value"])
check("nessun piano 10b5-1", buy[0]["plan"] is False)

sale = parse_form4(read("form4_plan_sale.xml"))
check("vendita con piano riconosciuta", sale[0]["plan"] is True)

mix = parse_form4(read("form4_mixed.xml"))
check("due operazioni nello stesso documento", len(mix) == 2, len(mix))
check("la riga con la nota 10b5-1 e' programmata", mix[0]["plan"] is True)
check("l'altra riga resta discrezionale", mix[1]["plan"] is False)

print("\nClassificazione e pesi")
rows = rules.enrich(buy + sale)
check("acquisto = BUY", rows[0]["kind"] == "BUY" and rows[0]["weight"] == 1.0)
check("vendita con piano = SELL_P e pesa 0", rows[1]["kind"] == "SELL_P" and rows[1]["weight"] == 0.0)

print("\nPrezzi")
px = parse_prices_csv(read("prices.csv"))
check("sei sedute", len(px) == 6, len(px))
check("ordinate e con la chiusura giusta", px[-1]["d"] == "2026-09-21" and px[-1]["c"] == 40.85)

print("\nCluster")
base = date(2026, 9, 1)
many = []
for i, who in enumerate(["A", "B", "C"]):
    many.append({"date": (base + timedelta(days=i * 5)).isoformat(), "who": who,
                 "role": "Director", "code": "P", "plan": False, "shares": 1000,
                 "price": 40.0, "value": 40000.0})
many = rules.enrich(many)
check("tre compratori in 30 giorni formano un cluster", len(rules.find_cluster(many)) == 3)
far = rules.enrich([dict(t, date=(base + timedelta(days=i * 60)).isoformat())
                    for i, t in enumerate(many)])
check("gli stessi tre a mesi di distanza no", rules.find_cluster(far) == [])

print("\nMoltiplicatore storico")
check("nessun acquisto passato -> 1,00", rules.track_multiplier([]) == 1.0)
check("acquisti andati bene -> sopra 1", rules.track_multiplier([10, 8, 6]) > 1.0)
check("acquisti andati male -> sotto 1", rules.track_multiplier([-9, -7]) < 1.0)
check("limite superiore 1,5", rules.track_multiplier([200, 200, 200]) == 1.5)

print("\nRidistribuzione dei pesi")
subs = {"insider": {"v": 80, "coverage": "Alta"}, "price": {"v": 60, "coverage": "Alta"},
        "guid": {"v": None, "coverage": "Assente"}, "market": {"v": None, "coverage": "Assente"},
        "sector": {"v": None, "coverage": "Assente"}, "news": {"v": None, "coverage": "Assente"},
        "social": {"v": None, "coverage": "Assente"}}
w = {"insider": 30, "guid": 20, "market": 15, "sector": 10, "news": 10, "price": 10, "social": 5}
tot, conf, missing, covered = score.combine(subs, w)
check("media pesata sui soli disponibili", tot == 75, tot)
check("confidenza bassa con il 40% dei pesi", conf == "Bassa" and covered == 40, (conf, covered))
check("elenca cosa manca", set(missing) == {"guid", "market", "sector", "news", "social"})

print("\nBuild completo, senza rete")
cfg = load_config(os.path.join(os.path.dirname(F), "..", "config.json"))
n = 400
series = [{"d": (date(2025, 3, 3) + timedelta(days=i)).isoformat(),
           "c": 50 + 10 * (i / n) - (8 if 250 < i < 300 else 0), "v": 1000}
          for i in range(n)]
bench = [{"d": p["d"], "c": 100 + 5 * (i / n), "v": 1000} for i, p in enumerate(series)]
tx = []
for i, who in enumerate(["Okafor Maya", "Varga Stefan", "Brandt Lena"]):
    tx.append({"date": series[-20 + i * 3]["d"], "who": who,
               "role": "Chief Executive Officer" if i == 0 else "Director",
               "code": "P", "plan": False, "shares": 10000, "price": 55.0,
               "value": 550000.0, "ad": "A", "is_senior": i == 0, "is_director": True})
tx.append({"date": series[-250]["d"], "who": "Okafor Maya", "role": "Chief Executive Officer",
           "code": "P", "plan": False, "shares": 5000, "price": 52.0, "value": 260000.0,
           "ad": "A", "is_senior": True, "is_director": True})
raw = {"ticker": "EXSM", "name": "Example Semiconductors", "cik": "0001045810",
       "prices": series, "bench": bench, "tx": tx}
data = build(cfg, raw)
check("data.json ha lo score", isinstance(data["score"]["value"], int), data["score"]["value"])
check("il cluster è stato trovato", len(data["cluster"]) == 3, data["cluster"])
check("lo storico del CEO è stato calcolato", "Okafor Maya" in data["tracks"])
check("segnala le fonti mancanti", "market" in data["score"]["missing"])
check("serializzabile in JSON", bool(json.dumps(data)))
print(f"\n  score {data['score']['value']} - {data['score']['verdict']} "
      f"(confidenza {data['score']['confidence'].lower()}, copertura {data['score']['covered_pct']}%)")

print(f"\n{ok} controlli passati, {len(bad)} falliti")
if bad:
    print("falliti:", ", ".join(bad))
sys.exit(1 if bad else 0)
