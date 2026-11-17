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
from pipeline.build import build_one, finish, load_config, write_all
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
check("la riga con la nota 10b5-1 è programmata", mix[0]["plan"] is True)
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


def fake(ticker, buyers=(), sellers=()):
    tx = []
    for i, who in enumerate(buyers):
        tx.append({"date": series[-20 + i * 3]["d"], "who": who,
                   "role": "Chief Executive Officer" if i == 0 else "Director",
                   "code": "P", "plan": False, "shares": 10000, "price": 55.0,
                   "value": 550000.0, "ad": "A", "is_senior": i == 0, "is_director": True})
    for i, who in enumerate(sellers):
        tx.append({"date": series[-30 + i * 2]["d"], "who": who, "role": "Director",
                   "code": "S", "plan": False, "shares": 8000, "price": 55.0,
                   "value": 440000.0, "ad": "D", "is_senior": False, "is_director": True})
    return {"ticker": ticker, "name": ticker + " Inc", "cik": "0001000000",
            "prices": series, "bench": bench, "tx": tx, "errors": [], "failed": [],
            "price_source": "test"}


raws = [fake("AAAA", buyers=["Okafor Maya", "Varga Stefan", "Brandt Lena"]),
        fake("BBBB", sellers=["Chen Renata"]),
        fake("CCCC"),
        fake("DDDD", sellers=["Rossi Luca", "Neri Ada"])]
built = [build_one(cfg, r) for r in raws]
universe = [d["insider_90d"] for d in built]
built = [finish(cfg, d, universe) for d in built]
a, b = built[0], built[1]

check("quattro aziende calcolate", len(built) == 4)
old_cluster = fake("EEEE", buyers=[])
for i, who in enumerate(["Uno", "Due", "Tre"]):
    old_cluster["tx"].append({"date": series[40 + i * 4]["d"], "who": who, "role": "Director",
                              "code": "P", "plan": False, "shares": 1000, "price": 50.0,
                              "value": 50000.0, "ad": "A", "is_senior": False, "is_director": True})
e = finish(cfg, build_one(cfg, old_cluster), universe)
check("un cluster di un anno fa non viene segnalato", not e["cluster"], e["cluster"])
check("il cluster è solo in AAAA", bool(a["cluster"]) and not b["cluster"])
check("config con due universi", len(cfg["universi"]) == 2 and
      sum(len(g["tickers"]) for g in cfg["universi"]) == 40,
      [len(g["tickers"]) for g in cfg["universi"]])
check("nessun ticker ripetuto fra gli universi",
      len({t for g in cfg["universi"] for t in g["tickers"]}) == 40)
check("il settore ora ha un valore", a["score"]["subs"][3]["v"] is not None,
      a["score"]["subs"][3])
check("chi compra da solo prende più settore di chi non compra",
      a["score"]["subs"][3]["v"] > b["score"]["subs"][3]["v"],
      (a["score"]["subs"][3]["v"], b["score"]["subs"][3]["v"]))
check("il punteggio di AAAA è più alto di quello di DDDD",
      a["score"]["value"] > built[3]["score"]["value"],
      (a["score"]["value"], built[3]["score"]["value"]))
check("la copertura sale con il settore", a["score"]["covered_pct"] == 50,
      a["score"]["covered_pct"])
check("serializzabile in JSON", bool(json.dumps(built)))


# ------------------------------------------------------------------ due lingue
def bilingual(v):
    return isinstance(v, dict) and set(v) == {"it", "en"} and all(isinstance(x, str) for x in v.values())


def all_texts(d):
    """Tutti i testi generati dalla pipeline che il sito mostra all'utente."""
    out = [d["score"]["verdict"]]
    for sub in d["score"]["subs"]:
        out.append(sub["name"])
        out += sub["why"]
    for src in d["sources"]:
        out += [src["t"], src["m"], src["x"]]
    out += [t["read"] for t in d["tx"]]
    out.append(d["group"])
    return out


for d in built:
    d["group"] = {"it": "Prova", "en": "Test"}
bad = [t for d in built for t in all_texts(d) if not bilingual(t)]
check("ogni testo generato esiste in italiano e in inglese", not bad, bad[:3])
check("l'inglese è diverso dall'italiano",
      a["score"]["verdict"]["en"] == "Moderate bullish conviction", a["score"]["verdict"])
check("le ragioni del punteggio insider sono tradotte",
      a["score"]["subs"][0]["why"][0]["en"].startswith("Cluster of 3 separate buyers"),
      a["score"]["subs"][0]["why"][0]["en"])

import tempfile
with tempfile.TemporaryDirectory() as tmp:
    idx = write_all(built, cfg, tmp)
    check("index.json elenca tutte le aziende", len(idx["companies"]) == 4)
    check("ordinate dal punteggio più alto",
          idx["companies"][0]["score"] >= idx["companies"][-1]["score"])
    check("un file per azienda", all(os.path.exists(os.path.join(tmp, "data", t + ".json"))
                                     for t in ["AAAA", "BBBB", "CCCC", "DDDD"]))
print(f"\n  {a['company']['ticker']}: score {a['score']['value']} "
      f"(settore {a['score']['subs'][3]['v']}), "
      f"{b['company']['ticker']}: score {b['score']['value']} "
      f"(settore {b['score']['subs'][3]['v']})")

# ------------------------------------------- short interest (export manuale)
from pipeline.score import market_score


def si_series(values, dtc=1.2):
    return [{"date": f"2026-{(i % 12) + 1:02d}-15", "si": v, "close": 50.0,
             "vol": 1e6, "dtc": dtc} for i, v in enumerate(values)]


falling = si_series([100e6] * 20 + [95e6, 90e6, 85e6, 70e6])
rising = si_series([100e6] * 20 + [110e6, 125e6, 140e6, 160e6])
vf, wf, cf = market_score(falling)
vr, wr, cr = market_score(rising)
check("short interest in calo vale più di short interest in crescita", vf > vr, (vf, vr))
check("lo short interest in crescita penalizza", vr < 50, vr)
check("storico lungo = copertura alta", cf == "Alta", cf)
check("storico corto non produce un punteggio",
      market_score(si_series([100e6] * 4))[0] is None)
check("le ragioni dello short interest sono in due lingue",
      all(set(w) == {"it", "en"} for w in wf), wf[0])

# due aziende di dimensioni diverse devono restare confrontabili
small = si_series([10e6] * 20 + [9.5e6, 9e6, 8.5e6, 7e6])
check("la dimensione dell'azienda non cambia il punteggio",
      market_score(small)[0] == vf, (market_score(small)[0], vf))


# --------------------------- confronto dello short interest con i simili
from pipeline.score import market_peers

base = {"v": 50, "why": [], "coverage": "Alta"}
piu_shortata = market_peers(dict(base, why=[]), 6.0, [2.0, 2.0, 2.0, 2.0])
meno_shortata = market_peers(dict(base, why=[]), 1.0, [2.0, 2.0, 2.0, 2.0])
check("essere shortati più dei simili abbassa il punteggio", piu_shortata["v"] < 50, piu_shortata["v"])
check("esserlo meno lo alza", meno_shortata["v"] > 50, meno_shortata["v"])
check("l'effetto è limitato a otto punti", abs(piu_shortata["v"] - 50) <= 8, piu_shortata["v"])
check("con meno di tre simili non si confronta",
      market_peers(dict(base, why=[]), 6.0, [2.0, 2.0])["v"] == 50)
check("senza la percentuale propria non si confronta",
      market_peers(dict(base, why=[]), None, [2.0, 2.0, 2.0, 2.0])["v"] == 50)
check("il confronto è scritto in due lingue",
      set(piu_shortata["why"][-1]) == {"it", "en"}, piu_shortata["why"])


# ------------------------- divergenza: comprano mentre tagliano la guidance
def call(tone, up=0, dn=0, q="T3 '26"):
    g = [{"metric": "x", "value": "y", "direction": "alzata"} for _ in range(up)]
    g += [{"metric": "x", "value": "y", "direction": "ridotta"} for _ in range(dn)]
    return {"ticker": "AAAA", "quarter": q, "tone": tone, "guidance": g}


taglia = build_one(cfg, fake("AAAA", buyers=["Tizio"]), [call(60, up=0, dn=4)])
alza = build_one(cfg, fake("AAAA", buyers=["Tizio"]), [call(60, up=4, dn=0)])
check("tagliare la guidance abbassa il punteggio del tono",
      taglia["_subs"]["guid"]["v"] < alza["_subs"]["guid"]["v"],
      (taglia["_subs"]["guid"]["v"], alza["_subs"]["guid"]["v"]))
check("l'effetto è limitato a dieci punti",
      abs(taglia["_subs"]["guid"]["v"] - 60) <= 10, taglia["_subs"]["guid"]["v"])
righe = " ".join(w["it"] for w in taglia["_subs"]["guid"]["why"])
check("la divergenza viene scritta a parole", "divergenza" in righe, righe)
senza_acquisti = build_one(cfg, fake("AAAA"), [call(60, up=0, dn=4)])
check("senza acquisti non si parla di divergenza",
      "divergenza" not in " ".join(w["it"] for w in senza_acquisti["_subs"]["guid"]["why"]))
check("alzare la guidance senza acquisti viene segnalato",
      "nessun insider compra" in " ".join(
          w["it"] for w in build_one(cfg, fake("AAAA"), [call(60, up=3)])["_subs"]["guid"]["why"]))


# -------------------------- azioni in circolazione dalla SEC (senza rete)
from pipeline.sources import parse_shares

payload = {"units": {"shares": [
    {"end": "2026-04-24", "val": 4_380_000_000, "fy": 2026},
    {"end": "2026-10-24", "val": 5_440_000_000, "fy": 2026},
    {"end": "2025-10-24", "val": 4_330_000_000, "fy": 2025}]}}
n, when = parse_shares(payload)
check("prende la rilevazione più recente", (n, when) == (5.44e9, "2026-10-24"), (n, when))
check("un payload vuoto non rompe niente", parse_shares({}) == (None, None))
check("scarta i valori non numerici",
      parse_shares({"units": {"shares": [{"end": "2026-01-01", "val": None}]}}) == (None, None))

vs, ws, _ = market_score(falling, shares=1e9)
pct = [w for w in ws if "circolazione" in w["it"]]
check("la percentuale sul circolante compare fra le ragioni", len(pct) == 1, ws)
check("ed è calcolata giusta", "7.00%" in pct[0]["it"], pct[0]["it"] if pct else None)
check("senza il dato, il punteggio non cambia", market_score(falling)[0] == vs)


# ---------------------------------- ogni trascrizione va alla sua azienda
from pipeline.extract import ticker_of

check("il ticker si legge dal nome del file",
      [ticker_of(x) for x in ["INTC_2026Q3.txt", "orcl-2026q2.txt", "BAC 2026Q1.txt"]]
      == ["INTC", "ORCL", "BAC"])
ex = [{"ticker": "INTC", "tone": 70}, {"ticker": "ORCL", "tone": 20}]
only_intc = [e for e in ex if e["ticker"] == "INTC"]
g = build_one(cfg, fake("INTC", buyers=["Tizio"]), only_intc)
check("il tono di una call vale solo per la sua azienda",
      g["_subs"]["guid"]["v"] == 70, g["_subs"]["guid"])


# ------------------------------------------------- il CIK viene verificato
# La mappa della SEC ha dato ADI -> Eli Lilly (16/11/2026): un CIK si accetta
# solo se il JSON dei depositi elenca davvero quel ticker.
from pipeline import sources


class FakeResp:
    def __init__(self, payload): self.payload = payload
    def json(self): return self.payload
    @property
    def text(self): return self.payload if isinstance(self.payload, str) else ""


def fake_sec(mapping_cik, subs):
    def _get(url, headers=None, tries=3):
        if "company_tickers.json" in url:
            return FakeResp({"0": {"cik_str": int(mapping_cik), "ticker": "ADI", "title": "Sbagliata"}})
        if "submissions/CIK" in url:
            return FakeResp(subs[url.split("CIK")[1].split(".json")[0]])
        if "browse-edgar" in url:
            return FakeResp("<CIK>0000006281</CIK>")
        raise AssertionError(url)
    return _get


SUBS = {"0000059478": {"name": "ELI LILLY & Co", "tickers": ["LLY"]},
        "0000006281": {"name": "ANALOG DEVICES INC", "tickers": ["ADI"]}}
real_get, sources._SUBS = sources._get, {}
try:
    sources._get = fake_sec("59478", SUBS)
    cik, name, how = sources.cik_for("ADI")
    check("un CIK con il ticker sbagliato viene scartato", cik == "0000006281", (cik, name, how))
    check("e si ripiega sulla ricerca di EDGAR", how == "browse-edgar", how)
    sources._SUBS = {}
    sources._get = fake_sec("6281", SUBS)
    cik, name, how = sources.cik_for("ADI")
    check("un CIK giusto viene accettato", (cik, how) == ("0000006281", "company_tickers.json"),
          (cik, how))
    check("e porta con se' il nome dell'azienda", name == "ANALOG DEVICES INC", name)
finally:
    sources._get, sources._SUBS = real_get, {}

print(f"\n{ok} controlli passati, {len(bad)} falliti")
if bad:
    print("falliti:", ", ".join(bad))
sys.exit(1 if bad else 0)
