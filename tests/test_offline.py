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
_BAD0 = bad          # vedi l'ultimo controllo del file


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
        "sector": {"v": None, "coverage": "Assente"}, "events": {"v": None, "coverage": "Assente"}}
# pesi di prova che sommano a 100: quando è stata tolta la voce social questi
# sommavano a 95, e "il 40%" era diventato il 42% senza che nessuno lo vedesse
w = {"insider": 30, "guid": 20, "market": 20, "sector": 10, "events": 10, "price": 10}
tot, conf, missing, covered = score.combine(subs, w)
check("media pesata sui soli disponibili", tot == 75, tot)
check("confidenza bassa con il 40% dei pesi", conf == "Bassa" and covered == 40, (conf, covered))
check("elenca cosa manca", set(missing) == {"guid", "market", "sector", "events"})

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
# un Form 4 depositato in ritardo può dichiarare un'operazione di anni prima:
# fuori dalla finestra della storia non entra (e non sposta "da quando parte")
late = fake("LATE", buyers=["Tizio"])
late["tx"].insert(0, dict(late["tx"][0], date="2014-09-26", who="Caio"))
lb = build_one(cfg, late)
check("un'operazione più vecchia della finestra resta fuori",
      all(t["date"] >= "2020" for t in lb["tx"]) and lb["history"]["tx_from"] != "2014-09-26",
      lb["history"]["tx_from"])
_all = [t for g in cfg["universi"] for t in g["tickers"]]
check("config con dieci universi e duecento aziende", len(cfg["universi"]) == 10 and len(_all) == 200,
      [len(g["tickers"]) for g in cfg["universi"]])
check("nessun ticker ripetuto fra gli universi", len(set(_all)) == len(_all),
      sorted({t for t in _all if _all.count(t) > 1}))
check("ogni universo ha nome nelle due lingue, riferimento e almeno 5 aziende",
      all(g.get("nome") and g.get("nome_en") and g.get("benchmark") and len(g["tickers"]) >= 5
          for g in cfg["universi"]))
check("ogni riferimento è diverso", len({g["benchmark"] for g in cfg["universi"]}) == len(cfg["universi"]))
# Il registro delle previsioni confronta ogni azienda con il riferimento del
# suo universo: le quaranta di partenza non devono cambiare universo, o le righe
# già registrate non si potrebbero più misurare.
_first = {g["benchmark"]: set(g["tickers"]) for g in cfg["universi"][:2]}
check("le quaranta di partenza restano con SMH e SPY",
      len(_first.get("SMH", ())) == 20 and len(_first.get("SPY", ())) == 20
      and {"NVDA", "AEHR", "VECO"} <= _first["SMH"] and {"AAPL", "XOM", "KO"} <= _first["SPY"], _first.keys())
check("i CIK precedenti sono di aziende che ci sono",
      set(cfg.get("cik_predecessors") or {}) <= set(_all), cfg.get("cik_predecessors"))
check("il settore ora ha un valore", a["score"]["subs"][3]["v"] is not None,
      a["score"]["subs"][3])
check("chi compra da solo prende più settore di chi non compra",
      a["score"]["subs"][3]["v"] > b["score"]["subs"][3]["v"],
      (a["score"]["subs"][3]["v"], b["score"]["subs"][3]["v"]))
check("il punteggio di AAAA è più alto di quello di DDDD",
      a["score"]["value"] > built[3]["score"]["value"],
      (a["score"]["value"], built[3]["score"]["value"]))
# insider 35 + settore 10 + prezzo 10: l'azienda di prova non ha guidance,
# short interest né 8-K. Il valore atteso era rimasto a prima di prezzo ed eventi.
check("la copertura sale con il settore", a["score"]["covered_pct"] == 55,
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
# Attenzione al nome: `bad` è l'elenco dei controlli falliti. Fino al 22
# novembre 2026 questa riga lo riassegnava, cancellando ogni fallimento
# registrato prima: due controlli fallivano da settimane e il riepilogo diceva
# "0 falliti". Il controllo in fondo al file verifica che non succeda più.
nonbil = [t for d in built for t in all_texts(d) if not bilingual(t)]
check("ogni testo generato esiste in italiano e in inglese", not nonbil, nonbil[:3])
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
    sources._TICKERS = None            # la mappa si tiene per tutto il giro: qui cambia a ogni prova
    cik, name, how = sources.cik_for("ADI")
    check("un CIK con il ticker sbagliato viene scartato", cik == "0000006281", (cik, name, how))
    check("e si ripiega sulla ricerca di EDGAR", how == "browse-edgar", how)
    sources._SUBS = {}
    sources._get = fake_sec("6281", SUBS)
    sources._TICKERS = None            # la mappa si tiene per tutto il giro: qui cambia a ogni prova
    cik, name, how = sources.cik_for("ADI")
    check("un CIK giusto viene accettato", (cik, how) == ("0000006281", "company_tickers.json"),
          (cik, how))
    check("e porta con sé il nome dell'azienda", name == "ANALOG DEVICES INC", name)
finally:
    sources._get, sources._SUBS, sources._TICKERS = real_get, {}, None

# --------------------------------------------------------------- il backtest
from pipeline import build
CFG = cfg

def fake_co(ticker, buys):
    """buys: lista di (chi, data, car, apicale)."""
    tracks = {}
    for who, d, car, _ in buys:
        tracks.setdefault(who, {"buys": []})["buys"].append({"date": d, "car": car})
    return {"company": {"ticker": ticker},
            "tx": [{"who": w, "date": d, "kind": "BUY",
                    "role": "Chief Executive Officer" if sr else "Director", "is_senior": sr}
                   for w, d, _, sr in buys],
            "tracks": tracks}

# cinque dirigenti che comprano lo stesso giorno nella stessa azienda
stesso_giorno = fake_co("UCTT", [(f"P{i}", "2025-03-07", -29.21, False) for i in range(5)])
# e sei acquisti, uno al mese da gennaio a giugno, ciascuno in guadagno.
# Il 15 aprile è esattamente al 90° giorno dal 15 gennaio: da gennaio ad aprile
# è una campagna (media di 4, 5, 6, 7 = 5,5), maggio e giugno un'altra (8,5).
isolati = fake_co("INTC", [(f"Q{i}", f"2025-0{i+1}-15", 4.0 + i, i == 0) for i in range(6)])

bt = build.backtest([stesso_giorno, isolati], CFG)
check("il backtest gira su un campione minimo", bt is not None)
check("dichiara che conta per campagne", bt["unit"] == "campaign" and bt["days"] == 90, bt.get("unit"))
check("le campagne sono tre", bt["all"]["camp"] == 3, bt["all"])
check("e porta ancora episodi e acquisti, per mostrare la differenza",
      (bt["all"]["ep"], bt["all"]["n"]) == (7, 11), bt["all"])
check("il conteggio per acquisto resta disponibile", bt["by_purchase"]["n"] == 11, bt["by_purchase"])
check("cinque firme nello stesso giorno sono un episodio e una campagna",
      (bt["cluster"]["camp"], bt["cluster"]["ep"], bt["cluster"]["n"]) == (1, 1, 5), bt["cluster"])
check("acquisti a meno di 90 giorni dal primo sono una campagna sola",
      (bt["solo"]["camp"], bt["solo"]["ep"], bt["solo"]["n"]) == (2, 6, 6), bt["solo"])
check("la mediana è quella delle campagne", (bt["all"]["med"], bt["solo"]["med"]) == (5.5, 7.0),
      (bt["all"]["med"], bt["solo"]["med"]))
check("conta le campagne in guadagno", (bt["all"]["pos"], bt["cluster"]["pos"]) == (2, 0), bt["all"])
check("separa le campagne dei dirigenti apicali", (bt["senior"]["camp"], bt["other"]["camp"]) == (1, 2),
      (bt["senior"], bt["other"]))
check("dichiara l'orizzonte su cui misura",
      bt["horizon"] == CFG["rules"]["track_record_horizon_days"], bt["horizon"])
check("e porta con sé la stima dei bonus", "bonus" in bt)

# "apicale" nel backtest vuol dire quello che vuol dire nella formula: il flag
# "officer" del Form 4 vale anche per un responsabile legale, e non basta
legale = {"who": "X", "date": "2025-01-15", "kind": "BUY", "role": "Chief Legal Officer", "is_senior": True}
cfo = {"who": "Y", "date": "2025-01-15", "kind": "BUY", "role": "EVP, CFO", "is_senior": True}
r = build.backtest_rows([{"company": {"ticker": "Z"}, "tx": [legale, cfo],
                          "tracks": {"X": {"buys": [{"date": "2025-01-15", "car": 1.0}]},
                                     "Y": {"buys": [{"date": "2025-01-15", "car": 1.0}]}}}], CFG)
check("un responsabile legale non è un dirigente apicale", [x["senior"] for x in r] == [False, True], r)

# sotto le dieci osservazioni non dice nulla, invece di dire poco
check("con pochi dati il backtest non esiste",
      build.backtest([fake_co("INTC", [("A", "2025-01-15", 3.0, False)])], CFG) is None)
# un acquisto senza prezzo a 60 giorni non entra nel conto
senza = fake_co("ORCL", [(f"R{i}", f"2025-0{i+1}-20", 1.0, False) for i in range(6)])
senza["tracks"] = {}
check("un acquisto troppo recente per essere misurato viene escluso",
      build.backtest([stesso_giorno, isolati, senza], CFG)["by_purchase"]["n"] == 11)

from pipeline import sources
# ------------------------------------------------- i fatti rilevanti (8-K)
def f8k(d, *items): return {"date": d, "items": list(items), "acc": ""}

v, why, cov = score.events_score([])
check("nessun 8-K non è un dato mancante, è un fatto", (v, cov) == (50, "Alta"), (v, cov))
v, why, cov = score.events_score(None)
check("ma se la SEC non risponde la voce resta vuota", v is None and cov == "Assente")

# routine: risultati, allegati, assemblea. Non devono muovere niente.
v, why, cov = score.events_score([f8k("2026-10-20", "2.02", "9.01"), f8k("2026-09-01", "5.07")])
check("i depositi di routine lasciano il punteggio a metà", v == 50, v)
check("e lo dice invece di tacere", any("routine" in str(w) or "risultati" in str(w) for w in why))

# una rettifica dei conti passati è il fatto peggiore che un'azienda dichiari
v4, _, _ = score.events_score([f8k("2026-10-01", "4.02")])
check("i conti da rifare abbassano il punteggio", v4 == 28, v4)
vb, _, _ = score.events_score([f8k("2026-10-01", "1.03")])
check("il fallimento pesa più di tutto", vb == 20 and vb < v4, (vb, v4))
va, _, _ = score.events_score([f8k("2026-10-01", "1.01")])
check("un nuovo accordo rilevante alza, ma di poco", va == 54, va)

# un solo tipo di fatto non può dominare per accumulo
vm, _, _ = score.events_score([f8k("2026-0%d-01" % i, "1.01") for i in range(1, 7)])
check("il lato positivo è limitato", vm == 62, vm)
vn, _, _ = score.events_score([f8k("2026-0%d-01" % i, "2.06", "2.04") for i in range(1, 7)])
check("anche quello negativo, e il punteggio resta nella scala", vn == 15 and 0 <= vn <= 100, vn)

# il perché deve essere controllabile: codice, nome e date
_, why, _ = score.events_score([f8k("2026-10-01", "4.02"), f8k("2026-08-02", "5.02")])
txt = " ".join(str(w) for w in why)
check("cita il codice item e la data", "4.02" in txt and "2026-10-01" in txt)
check("i fatti peggiori vengono per primi", "4.02" in str(why[1]))
check("singolare e plurale in italiano", "1 volta" in str(why[1]) and "volte" not in str(why[1]))
_, w1, _ = score.events_score([f8k("2026-10-01", "2.02")])
check("vale anche per il conteggio dei moduli", "1 modulo 8-K depositato" in str(w1[0]), w1[0])
check("e in inglese", "1 Form 8-K filing in" in str(w1[0]), w1[0])
check("il testo dei fatti è nelle due lingue",
      all(isinstance(w, dict) and set(w) == {"it", "en"} for w in why))

# il lettore degli 8-K parte dal JSON dei depositi, senza chiamate nuove
fake = {"filings": {"recent": {
    "form": ["8-K", "10-Q", "8-K", "8-K/A"],
    "filingDate": ["2026-10-01", "2026-09-30", "2025-01-02", "2026-10-05"],
    "items": ["2.02,9.01", "", "4.02", "5.02"],
    "accessionNumber": ["a", "b", "c", "d"]}}}
sources._SUBS["0000000001"] = fake
got = sources.recent_8k("1", "2026-01-01")
check("prende solo gli 8-K, solo dentro la finestra", [g["date"] for g in got] == ["2026-10-01", "2026-10-05"], got)
check("separa i codici item", got[0]["items"] == ["2.02", "9.01"], got[0])
check("tiene anche gli 8-K/A", got[1]["items"] == ["5.02"])

# ------------------------------------------ la distribuzione del backtest
d = build._dist([{"car": c} for c in (-80, -31, -30, -0.1, 0, 4.9, 5, 29.9, 30, 207)])
check("ogni acquisto finisce in un bucket e in uno solo",
      sum(b["n"] for b in d) == 10, [b["n"] for b in d])
check("la coda sinistra raccoglie i casi oltre il limite", d[0]["n"] == 2, d[0])
check("e quella destra pure", d[-1]["n"] == 2, d[-1])
check("i bucket agli estremi non hanno un lato", d[0]["lo"] is None and d[-1]["hi"] is None)
check("lo zero apre un bucket, non ci cade dentro a metà",
      any(b["lo"] == 0 for b in d))
zero = [b for b in d if b["lo"] == 0][0]
check("0 e 4.9 stanno nel primo bucket positivo", zero["n"] == 2, zero)
neg = [b for b in d if b["hi"] == 0][0]
check("-0.1 sta nell'ultimo negativo", neg["n"] == 1, neg)
check("i bucket coprono la scala senza buchi",
      all(d[i]["hi"] == d[i + 1]["lo"] for i in range(1, len(d) - 2)))
check("senza acquisti la distribuzione è tutta a zero",
      sum(b["n"] for b in build._dist([])) == 0)

# ------------------------------------------ chi ha spinto il punteggio
def sb(i, v, w): return {"id": i, "name": rules.L(i, i), "v": v, "w": w}

dv = build.driver([sb("insider", 55, 35), sb("market", 20, 15), sb("price", 52, 10)])
check("vince lo scarto per il peso, non il valore più alto",
      dv["name"]["it"] == "market", dv)
check("e dice da che parte spinge", dv["up"] is False, dv)
dv = build.driver([sb("insider", 70, 35), sb("market", 44, 15)])
check("una voce sopra 50 spinge in su", dv["name"]["it"] == "insider" and dv["up"], dv)
check("le voci senza valore non possono vincere",
      build.driver([sb("insider", None, 35), sb("price", 40, 10)])["name"]["it"] == "price")
check("se nessuna voce si muove dal centro non c'è un motore",
      build.driver([sb("insider", 50, 35), sb("price", 50, 10)]) is None)
check("senza voci non si inventa nulla", build.driver([]) is None)

# --------------------------------- un giro a vuoto non deve riscrivere niente
import tempfile, time as _t
_tmp = tempfile.mkdtemp()
os.makedirs(os.path.join(_tmp, "data"), exist_ok=True)
_res = [{"company": {"ticker": "AAA", "name": "A"}, "group": rules.L("g", "g"),
         "score": {"value": 55, "verdict": rules.L("v", "v"), "confidence": "Alta",
                   "covered_pct": 100, "subs": [], "missing": []},
         "insider_90d": {"buyers": 1, "sellers": 0, "net_musd": 1.0, "cluster": False},
         "price": {"last": {"d": "2026-11-19", "c": 10.0}}, "errors": [],
         "counts": {"tx": 1, "prices": 300, "si": 0, "events": 0},
         "tx": [], "tracks": {}, "sources": []}]
i1 = build.write_all(_res, cfg, _tmp)
mt1 = os.path.getmtime(os.path.join(_tmp, "data", "AAA.json"))
_t.sleep(1.1)
i2 = build.write_all(_res, cfg, _tmp)
mt2 = os.path.getmtime(os.path.join(_tmp, "data", "AAA.json"))
check("un giro a vuoto non tocca le schede", mt1 == mt2)
check("ma registra di aver controllato", i1["generated_at"] != i2["generated_at"])
check("e tiene ferma la data del cambiamento vero",
      i1["changed_at"] == i2["changed_at"], (i1["changed_at"], i2["changed_at"]))
_res[0]["score"]["value"] = 99
_t.sleep(1.1)
i3 = build.write_all(_res, cfg, _tmp)
check("quando qualcosa cambia, la scheda viene riscritta",
      os.path.getmtime(os.path.join(_tmp, "data", "AAA.json")) != mt2)
check("e la data del cambiamento si sposta", i3["changed_at"] != i2["changed_at"])
check("il confronto ignora solo gli orologi, non i dati",
      build.same_but_for_the_clock(os.path.join(_tmp, "data", "AAA.json"),
                                   dict(_res[0], generated_at="1999-01-01T00:00:00+00:00")))

# --------------------------------- la regola di maturità sulle osservazioni
# Un'osservazione vale solo se l'orizzonte è passato davvero. Prima la funzione
# accorciava l'orizzonte quando le sedute non bastavano, e così un acquisto
# misurato su 14 sedute finiva nel conto dei 60 giorni.
_ser = [{"d": f"2026-01-{i:02d}", "c": 100.0 + i} for i in range(1, 11)]
check("con meno sedute dell'orizzonte l'osservazione non è matura",
      rules._ret(_ser, "2026-01-05", 60) is None)
check("e nemmeno se ne manca una sola",
      rules._ret(_ser, "2026-01-01", 10) is None)
check("con l'orizzonte compiuto il rendimento c'è",
      rules._ret(_ser, "2026-01-01", 9) is not None)
check("e vale esattamente il rapporto fra le due chiusure",
      abs(rules._ret(_ser, "2026-01-01", 9) - (110.0 / 101.0 - 1)) < 1e-12)
# un'operazione di prima che la serie cominci non si misura sui primi prezzi disponibili
check("un giorno prima dell'inizio della serie non ha rendimento",
      rules._ret(_ser, "2018-11-16", 5) is None)
check("ma un sabato prima di un lunedì sì",
      rules._ret([{"d": "2026-01-05", "c": 1.0}, {"d": "2026-01-06", "c": 2.0}], "2026-01-03", 1) == 1.0)

# --------------------------------- il registro delle previsioni
from pipeline import history

check("la fascia segue i confini del verdetto scritto",
      [history.band_of(s) for s in (70, 65, 64, 55, 54, 46, 45, 35, 34, 0)] ==
      ["convinzione rialzista", "convinzione rialzista", "leggermente rialzista",
       "leggermente rialzista", "nessuna convinzione", "nessuna convinzione",
       "leggermente ribassista", "leggermente ribassista",
       "convinzione ribassista", "convinzione ribassista"])

_hdir = tempfile.mkdtemp()
_hp = os.path.join(_hdir, "history.csv")
check("un file che non c'è è un registro vuoto, non un errore", history.load(_hp) == [])

_r1 = [{"date": "2026-01-02", "ticker": t, "score": 70, "close": 100.0,
        "bench": "SPY", "bench_close": 50.0} for t in ("AAA", "BBB", "CCC")]
check("la prima scrittura aggiunge tutte le righe", history.add(_hp, _r1) == 3)
check("la seconda non aggiunge niente", history.add(_hp, _r1) == 0)
check("e il registro non è cresciuto", len(history.load(_hp)) == 3)
check("le righe rilette sono quelle scritte",
      history.load(_hp)[0]["ticker"] == "AAA" and history.load(_hp)[0]["score"] == 70)

with open(_hp, "a", encoding="utf-8") as _f:
    _f.write("non,una,riga,valida\n")
check("una riga rotta viene saltata senza far cadere il giro", len(history.load(_hp)) == 3)

# il confine dei 90 giorni: 89 non basta, 90 sì
_b = [{"date": "2026-01-01", "ticker": "AAA", "score": 70, "close": 100.0,
       "bench": "SPY", "bench_close": 50.0},
      {"date": "2026-03-31", "ticker": "AAA", "score": 70, "close": 110.0,
       "bench": "SPY", "bench_close": 50.0}]            # 89 giorni
check("a 89 giorni la previsione è ancora aperta", history.stats(_b)["done"] == 0)
_b[1]["date"] = "2026-04-01"                            # 90 giorni esatti
check("a 90 giorni esatti diventa un risultato", history.stats(_b)["done"] == 1)
# i tre numeri mostrati dal sito devono sempre tornare: l'ultima riga di ogni
# azienda è per forza ancora aperta, ed è giusto che risulti in attesa
_s = history.stats(_b)
check("registrate = misurate + in attesa",
      _s["observations"] == _s["done"] + _s["pending"], _s)
check("l'ultima riga di ogni azienda resta in attesa", _s["pending"] == 1)

# il rendimento è al netto del riferimento, come nel resto del progetto
_c = [{"date": "2026-01-01", "ticker": "AAA", "score": 70, "close": 100.0,
       "bench": "SPY", "bench_close": 100.0},
      {"date": "2026-04-01", "ticker": "AAA", "score": 70, "close": 110.0,
       "bench": "SPY", "bench_close": 105.0}]
_done, _pend = history.evaluate(_c)
check("il rendimento è relativo al riferimento", _done[0]["ret"] == 5.0, _done[0]["ret"])
check("e porta con sé la fascia di partenza", _done[0]["band"] == "convinzione rialzista")

_st = history.stats(_c)
check("il riassunto conta la fascia giusta",
      [b["n"] for b in _st["bands"]] == [1, 0, 0, 0, 0])
check("e tiene il totale", _st["all"]["n"] == 1 and _st["all"]["med"] == 5.0)
check("un registro senza risultati non inventa un totale",
      "all" not in history.stats(_r1) and history.stats(_r1)["pending"] == 3)
check("conta le sedute, non le righe", history.stats(_r1)["days"] == 1)

# la fotografia del giorno salta chi non ha un riferimento da cui misurare
_snap = history.snapshot(
    [{"company": {"ticker": "AAA"}, "score": {"value": 60}, "benchmark": "SPY",
      "price": {"last": {"d": "2026-11-20", "c": 10.0}}},
     {"company": {"ticker": "BBB"}, "score": {"value": 60}, "benchmark": "XXX",
      "price": {"last": {"d": "2026-11-20", "c": 10.0}}}],
    {("SPY", "2026-11-20"): 500.0})
check("senza la chiusura del riferimento la riga non si scrive",
      [r["ticker"] for r in _snap] == ["AAA"])
check("la riga è datata al giorno della chiusura, non del giro",
      _snap[0]["date"] == "2026-11-20")

# --------------------------------- la prova dei pesi
from pipeline import stress

check("due classifiche identiche hanno rango 1",
      stress.spearman({"A": 10, "B": 5, "C": 1}, {"A": 90, "B": 50, "C": 10}) == 1.0)
check("una classifica rovesciata ha rango -1",
      stress.spearman({"A": 10, "B": 5, "C": 1}, {"A": 1, "B": 5, "C": 10}) == -1.0)
check("con meno di tre non si calcola un rango",
      stress.spearman({"A": 1, "B": 2}, {"A": 2, "B": 1}) is None)

_W = {"insider": 35, "guid": 20, "market": 15, "sector": 10, "events": 10, "price": 10}
# dieci aziende: insider e mercato si muovono, il settore è identico per tutti
_subs = {}
for _i in range(10):
    _subs[f"T{_i}"] = {
        "insider": {"v": 20 + _i * 6, "coverage": "Alta"},
        "guid": {"v": None, "coverage": "Assente"},
        "market": {"v": 80 - _i * 5, "coverage": "Alta"},
        "sector": {"v": 50, "coverage": "Alta"},        # uguale per tutti
        "events": {"v": 50, "coverage": "Alta"},
        "price": {"v": 50, "coverage": "Alta"},
    }
_st = stress.run(_subs, _W, score.combine, score.verdict, draws=200)

check("una voce uguale per tutti non cambia la classifica se la togli",
      next(d for d in _st["drop"] if d["id"] == "sector")["rho"] == 1.0)
check("una voce che nessuno ha non viene valutata",
      next(d for d in _st["drop"] if d["id"] == "guid")["rho"] is None)
check("e il sito sa su quante aziende è alimentata",
      next(d for d in _st["drop"] if d["id"] == "guid")["n"] == 0)

_base = {t: score.combine(s, _W)[0] for t, s in _subs.items()}
check("la fascia contiene sempre il punteggio pubblicato",
      all(_st["per"][t]["lo"] <= _base[t] <= _st["per"][t]["hi"] for t in _base),
      [(t, _st["per"][t], _base[t]) for t in _base
       if not _st["per"][t]["lo"] <= _base[t] <= _st["per"][t]["hi"]])
check("il verdetto tenuto è una percentuale sensata",
      all(0 <= _st["per"][t]["kept"] <= 100 for t in _base))
check("la correlazione peggiore non supera la mediana",
      _st["rho"]["min"] <= _st["rho"]["med"] <= 1.0)

# il seme è fisso: due giri sugli stessi dati devono dare lo stesso risultato,
# altrimenti ogni ora il sito riscriverebbe quaranta schede per puro rumore
check("due giri sugli stessi dati danno lo stesso identico risultato",
      json.dumps(_st, sort_keys=True) ==
      json.dumps(stress.run(_subs, _W, score.combine, score.verdict, draws=200), sort_keys=True))
check("con troppe poche aziende non si prova niente",
      stress.run({k: _subs[k] for k in list(_subs)[:3]}, _W,
                 score.combine, score.verdict, draws=50) is None)

# --------------------------------- i bonus insider, stimati dai dati
import random as _rnd
from pipeline import bonus as BO

_rows = [{"t": "A", "d": "2025-01-01", "car": 10.0, "people": 1, "senior": False},
         {"t": "A", "d": "2025-01-01", "car": 10.0, "people": 1, "senior": True},
         {"t": "A", "d": "2025-04-01", "car": 20.0, "people": 1, "senior": False},   # 90° giorno
         {"t": "A", "d": "2025-04-02", "car": 30.0, "people": 2, "senior": False},   # 91°: nuova
         {"t": "B", "d": "2025-01-01", "car": -5.0, "people": 4, "senior": False}]
_c = BO.campaigns(_rows)
check("due firme nello stesso giorno sono un episodio", len(BO.episodes(_rows)) == 4)
check("il 90° giorno sta nella stessa campagna, il 91° no",
      [(c["t"], c["episodes"], c["buys"]) for c in _c] == [("A", 2, 3), ("A", 1, 1), ("B", 1, 1)], _c)
check("il rendimento di una campagna è la media dei suoi episodi", _c[0]["car"] == 15.0, _c[0])
check("una caratteristica vale per la campagna se vale per un episodio", _c[0]["senior"] is True)
check("un cluster non è anche 'due compratori'", _c[2]["cluster"] and not _c[2]["two"], _c[2])
check("due compratori distinti si riconoscono", _c[1]["two"] and not _c[1]["cluster"], _c[1])
check("troppe poche campagne: nessuna stima", BO.estimate(_c) is None)
check("senza stima, nessun bonus", BO.points(None) == {"cluster": 0, "two": 0, "senior": 0, "beat": 0})
check("una matrice singolare non si inverte", BO._inverse([[1, 2], [2, 4]]) is None)


def _fake_camps(n, eff, noise=20.0, seed=1):
    r = _rnd.Random(seed); out = []
    for i in range(n):
        c = {"t": f"T{i}", "d": "2025-01-01", "episodes": 1, "buys": 1,
             "cluster": r.random() < .2, "senior": r.random() < .35}
        c["two"] = (not c["cluster"]) and r.random() < .2
        c["car"] = sum(eff[f] for f in BO.FEATURES if c.get(f)) + r.gauss(0, noise)
        out.append(c)
    return out


_vero = {"cluster": 10.0, "two": 4.0, "senior": 6.0}
_e = BO.estimate(_fake_camps(5000, _vero, seed=3))
_got = {r["id"]: r["points"] for r in _e["features"]}
check("con molti dati ritrova gli effetti veri",
      all(abs(_got[f] - _vero[f]) <= 1 for f in _vero), _got)
check("e quasi non li restringe", all(r["shrink"] > .95 for r in _e["features"] if r["id"] in _vero),
      _e["features"])
_e = BO.estimate(_fake_camps(26, _vero, seed=26))
check("con 26 campagne anche effetti veri vengono ristretti molto",
      all(abs(r["points"]) < abs(_vero[r["id"]]) for r in _e["features"] if r["id"] in _vero),
      _e["features"])
_nulls = [BO.points(BO.estimate(_fake_camps(26, {f: 0 for f in BO.FEATURES}, seed=100 + s)))
          for s in range(20)]
check("senza effetti veri i bonus restano piccoli",
      all(abs(p[f]) <= 6 for p in _nulls for f in p), _nulls)
_one = [dict(c, cluster=False, two=False) for c in _fake_camps(30, {f: 0 for f in BO.FEATURES}, seed=7)]
_one[0].update(cluster=True, car=-30.0)
_e = BO.estimate(_one)
_cl = [r for r in _e["features"] if r["id"] == "cluster"][0]
check("una campagna sola non basta a dare un bonus", abs(_cl["points"]) <= 1, _cl)
_tw = [r for r in _e["features"] if r["id"] == "two"][0]
check("una caratteristica che nessuno ha vale zero", _tw["raw"] is None and _tw["points"] == 0, _tw)
check("la sensibilità a tau è dichiarata", set(_cl["by_tau"]) == {"3", "5", "10"}, _cl["by_tau"])
_big = BO.estimate(_fake_camps(5000, {"cluster": 60.0, "two": 0, "senior": 0}, noise=5, seed=4))
check("nessun bonus supera il tetto",
      all(abs(r["points"]) <= BO.MAX_POINTS for r in _big["features"]), _big["features"])

# la voce insider usa i bonus che le si passano, e lo scrive
_tx = rules.enrich([{"date": "2026-04-01", "who": w, "role": "Director", "code": "P", "plan": False,
                     "shares": 1, "price": 1.0, "value": 1.0, "ad": "A"} for w in ("U", "V", "W")])
_cl3 = rules.find_cluster(_tx, 30, 3)
_today = date(2026, 4, 10)
v_hand, w_hand, _ = score.insider_score(_tx, _cl3, {}, cfg, _today)
v_est, w_est, _ = score.insider_score(_tx, _cl3, {}, cfg, _today, bonus={"cluster": 0, "two": 0, "senior": 0})
check("senza bonus passati valgono quelli scritti a mano", v_hand - v_est == 18, (v_hand, v_est))
check("con i bonus stimati il perché lo dice",
      "stimato dai dati" in w_est[0]["it"] and "estimated from the data" in w_est[0]["en"], w_est[0])
check("con quelli a mano non aggiunge niente", "stimato" not in w_hand[0]["it"], w_hand[0])

# --------------------------------- il moltiplicatore conta le campagne
_buys = [{"date": f"2025-01-{d:02d}", "car": 8.0} for d in range(1, 11)]
check("dieci acquisti in dieci giorni sono una campagna", rules.person_campaigns(_buys) == [8.0])
check("e il moltiplicatore li conta una volta: 1 + (8 / 4) / 10",
      rules.track_multiplier(rules.person_campaigns(_buys), 3) == 1.2)
check("due campagne distanti restano due",
      len(rules.person_campaigns(_buys + [{"date": "2025-06-01", "car": -2.0}])) == 2)

# --------------------------------- i Form 4 di un altro emittente
_X = """<ownershipDocument><issuer><issuerCik>{ic}</issuerCik></issuer>
<reportingOwner><reportingOwnerId><rptOwnerName>Rossi Ada</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>2026-01-05</value></transactionDate>
<transactionCoding><transactionCode>P</transactionCode></transactionCoding><transactionAmounts>
<transactionShares><value>100</value></transactionShares><transactionPricePerShare><value>10</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""
check("un Form 4 dell'azienda stessa viene letto",
      len(parse_form4(_X.format(ic="0000070858"), issuer_cik="70858")) == 1)
check("uno di un altro emittente no", parse_form4(_X.format(ic="0001234567"), issuer_cik="0000070858") is None)
check("senza CIK da confrontare si legge come prima", len(parse_form4(_X.format(ic="0001234567"))) == 1)

_docs = {"f1": _X.format(ic="0009999999"), "f2": _X.format(ic="0000000042"),
         "f3": _X.format(ic="0000000042"), "f4": _X.format(ic="0000000042")}
_calls = []
real_get, real_f4 = sources._get, sources.form4_filings
try:
    class _R:
        def __init__(self, t): self.text = t
    sources._get = lambda url, **k: (_calls.append(url), _R(_docs[url]))[1]
    sources.form4_filings = lambda cik, months: [{"accession": k, "date": "2026-01-01", "url": k}
                                                for k in ("f1", "f2", "f3", "f4")]
    _cd = tempfile.mkdtemp()
    _r = sources.insider_transactions("42", limit=2, cache_dir=_cd)
    check("il limite conta solo i documenti dell'azienda", len(_r) == 2 and _calls == ["f1", "f2", "f3"], _calls)
    _calls.clear()
    _r2 = sources.insider_transactions("42", limit=2, cache_dir=_cd)
    check("e dalla cache si ottiene lo stesso, senza riscaricare", len(_r2) == 2 and _calls == [], _calls)
    check("il documento estraneo resta segnato come estraneo nella cache",
          json.load(open(os.path.join(_cd, "f1.json"))) is None)
finally:
    sources._get, sources.form4_filings = real_get, real_f4


# --------------------------------- la prova all'indietro delle voci
from pipeline import predict as PR
check("ranghi con pari merito alla media", PR._ranks([1, 2, 2, 3]) == [0, 1.5, 1.5, 3])
check("correlazione di rango perfetta", abs(PR.spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) - 1) < 1e-12)
check("e rovesciata", abs(PR.spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) + 1) < 1e-12)
check("una voce uguale per tutti non correla con niente", PR.spearman([7] * 6, [1, 2, 3, 4, 5, 6]) is None)
check("sotto le cinque aziende non si calcola", PR.spearman([1, 2, 3, 4], [1, 2, 3, 4]) is None)
def _dd(i):                            # una data a `i` giorni dall'inizio di un blocco
    return date.fromordinal((date(2024, 1, 1).toordinal() // 87) * 87 + i).isoformat()


check("con meno di tre blocchi nessun riassunto",
      PR.summarize({"g": [(_dd(i), .1) for i in range(0, 174, 5)]}) is None)
_s = PR.summarize({"g": [(_dd(i), .1) for i in range(0, 435, 5)]})
check("il riassunto conta i blocchi di 87 giorni di calendario", _s["blocks"] == 5 and _s["dates"] == 87, _s)
# due universi nello stesso periodo stanno nello stesso blocco: non sono prove indipendenti
_s2 = PR.summarize({"a": [(_dd(i), .1) for i in range(0, 435, 5)],
                    "b": [(_dd(i), .3) for i in range(0, 435, 5)]})
check("due universi nello stesso periodo fanno un blocco solo", _s2["blocks"] == 5 and _s2["dates"] == 174, _s2)
_s3 = PR.summarize({"a": [(_dd(i), .1 if (i // 87) % 2 else .3) for i in range(0, 435, 5)],
                    "b": [(_dd(i), .1 if (i // 87) % 2 else .3) for i in range(0, 435, 5)]})
_s4 = PR.summarize({"a": [(_dd(i), .1 if (i // 87) % 2 else .3) for i in range(0, 435, 5)]})
check("e un universo copiato due volte non riduce l'incertezza", _s3["se"] == _s4["se"], (_s3, _s4))
# correlazione, errore e t parlano della stessa media
_s5 = PR.summarize({"a": [(_dd(i), .9) for i in range(0, 87, 5)] +
                         [(_dd(i), .1 if i % 2 else .3) for i in range(87, 435, 29)]})
check("il t è la correlazione mostrata divisa per il suo errore",
      _s5 and abs(_s5["t"] - _s5["ic"] / _s5["se"]) < 0.06, _s5)


def _co(t, g, n=420, start=date(2025, 1, 1), ev=None, ev_from=None):
    ser = [[(start + timedelta(days=i)).isoformat(), round(100 * (1 + g) ** i, 6)] for i in range(n)]
    return {"company": {"ticker": t}, "group": {"it": "Prova", "en": "Test"},
            "price": {"series": ser}, "tx": [], "si_series": [],
            "events_hist": ev, "events_from": ev_from}


# controllo positivo attraverso tutta la pipeline: ogni azienda cresce a un
# ritmo costante e diverso, quindi chi è salito di più continua a salire di più.
# I ritmi sono distanziati perché il punteggio di prezzo è arrotondato all'intero:
# troppo vicini darebbero pari merito, e la correlazione scenderebbe sotto 1.
_cos = [_co(f"C{k}", 0.0006 * (k + 1)) for k in range(8)]
_r = PR.run(_cos, cfg)
_pr = [x for x in _r["rows"] if x["id"] == "price"][0]
check("una voce che anticipa davvero il futuro vale 1", _pr["ic"] == 1.0, _pr)
check("dichiara l'orizzonte e le date", _r["horizon"] == 60 and _r["from"] < _r["to"], _r)
check("senza short interest la voce mercato non si calcola",
      [x for x in _r["rows"] if x["id"] == "market"][0]["ic"] is None)
check("la stessa prova dà lo stesso risultato", json.dumps(_r) == json.dumps(PR.run(_cos, cfg)))
check("il calcolo non modifica le schede", "_px" not in _cos[0] and "_si" not in _cos[0])

# gli 8-K valgono solo dove il file della SEC copre davvero il passato
_ev = [["2025-05-01", ["4.02"]]]
_cov = [_co(f"E{k}", 0.0001 * (k + 1), ev=_ev if k % 2 else [], ev_from="2024-01-01") for k in range(8)]
_unc = [_co(f"U{k}", 0.0001 * (k + 1), ev=_ev if k % 2 else [], ev_from="2026-01-01") for k in range(8)]
_e1 = [x for x in PR.run(_cov, cfg)["rows"] if x["id"] == "events"][0]
_e2 = [x for x in PR.run(_unc, cfg)["rows"] if x["id"] == "events"][0]
check("con gli 8-K coperti la voce si calcola", _e1["ic"] is not None, _e1)
check("con gli 8-K non coperti no, invece di leggere 'nessun fatto'", _e2["ic"] is None, _e2)


# --------------------------------- cinque anni di storia
# gli archivi dei depositi: si scaricano solo se servono, e restano allineati
_main = {"filings": {"recent": {"form": ["4", "8-K"], "accessionNumber": ["n1", "n2"],
                                "filingDate": ["2026-10-01", "2026-06-01"], "primaryDocument": ["a.xml", "b.htm"],
                                "items": ["", "5.02"]},
                     "files": [{"name": "A1.json", "filingFrom": "2024-01-01", "filingTo": "2026-05-31"},
                               {"name": "A0.json", "filingFrom": "2019-01-01", "filingTo": "2023-12-31"}]}}
_arch = {"A1.json": {"form": ["4", "8-K"], "accessionNumber": ["o1", "o2"],
                     "filingDate": ["2025-01-10", "2024-03-02"], "primaryDocument": ["c.xml", "d.htm"]},
         "A0.json": {"form": ["4"], "accessionNumber": ["x"], "filingDate": ["2020-01-01"], "primaryDocument": ["z"]}}
_asked = []
real_get = sources._get
try:
    class _J:
        def __init__(self, j): self._j = j
        def json(self): return self._j
    sources._SUBS, sources._TAB = {"0000000077": _main}, {}
    sources._get = lambda url, **k: (_asked.append(url.split("/")[-1]), _J(_arch[url.split("/")[-1]]))[1]
    _cols, _cov = sources.filings_table("77", "2024-01-01")
    check("un archivio che serve viene letto, uno tutto più vecchio no", _asked == ["A1.json"], _asked)
    check("l'elenco unito è dal più recente al più vecchio",
          _cols["accessionNumber"] == ["n1", "n2", "o1", "o2"], _cols["accessionNumber"])
    check("una colonna che l'archivio non ha resta allineata e vuota", _cols["items"] == ["", "5.02", "", ""], _cols["items"])
    check("e dice da quando l'elenco è completo", _cov == "2024-03-02", _cov)
    _asked.clear(); sources._TAB = {}
    _c2, _cov2 = sources.filings_table("77", "2026-09-01")
    check("se il file principale basta, nessun archivio", _asked == [] and _cov2 == "2026-06-01", (_asked, _cov2))
    sources._TAB = {}
    check("gli 8-K arrivano anche dagli archivi",
          [e["date"] for e in sources.recent_8k("77", "2024-01-01")] == ["2024-03-02", "2026-06-01"])
finally:
    sources._get, sources._SUBS, sources._TAB = real_get, {}, {}

# il tetto di documenti nuovi per giro, condiviso fra le aziende
_docs2 = {f"g{i}": _X.format(ic="0000000042") for i in range(5)}
_calls2 = []
real_get, real_f4 = sources._get, sources.form4_filings
try:
    sources._get = lambda url, **k: (_calls2.append(url), _R(_docs2[url]))[1]
    sources.form4_filings = lambda cik, months: [{"accession": k, "date": "2026-01-01", "url": k} for k in _docs2]
    _cd2 = tempfile.mkdtemp(); _b = {"left": 3}; _st = {}
    _r = sources.insider_transactions("42", cache_dir=_cd2, budget=_b, status=_st)
    check("con il tetto a 3 se ne scaricano 3", len(_calls2) == 3 and _b["left"] == 0, (_calls2, _b))
    check("e lo storico risulta ancora incompleto", _st == {"complete": False, "missing": 2}, _st)
    _calls2.clear(); _b = {"left": 3}; _st = {}
    _r = sources.insider_transactions("42", cache_dir=_cd2, budget=_b, status=_st)
    check("il giro dopo legge dalla cache e scarica solo i mancanti",
          len(_calls2) == 2 and len(_r) == 5 and _st["complete"], (_calls2, _st))

    # i documenti recenti non aspettano: un'azienda nuova ha subito la voce di oggi
    _new = {f"r{i}": _X.format(ic="0000000042") for i in range(3)}
    _new.update({f"o{i}": _X.format(ic="0000000042") for i in range(3)})
    _docs2.update(_new)
    _recent = (date.today() - timedelta(days=10)).isoformat()
    sources.form4_filings = lambda cik, months: (
        [{"accession": f"r{i}", "date": _recent, "url": f"r{i}"} for i in range(3)] +
        [{"accession": f"o{i}", "date": "2023-01-01", "url": f"o{i}"} for i in range(3)])
    _calls2.clear(); _b = {"left": 0}; _st = {}
    _r = sources.insider_transactions("42", cache_dir=tempfile.mkdtemp(), budget=_b, status=_st)
    check("con il tetto esaurito i documenti recenti si scaricano lo stesso",
          _calls2 == ["r0", "r1", "r2"] and _st == {"complete": False, "missing": 3}, (_calls2, _st))
    check("e non consumano il tetto", _b["left"] == 0, _b)
    _calls2.clear(); _b = {"left": 99, "until": 0}; _st = {}
    _r = sources.insider_transactions("42", cache_dir=tempfile.mkdtemp(), budget=_b, status=_st)
    check("passato l'orario la storia vecchia aspetta, anche con documenti nel tetto",
          _calls2 == ["r0", "r1", "r2"] and _st["missing"] == 3 and _b["left"] == 99, (_calls2, _st, _b))
finally:
    sources._get, sources.form4_filings = real_get, real_f4

# gli archivi dei depositi si tengono su disco; se la SEC rifà l'archivio, si riscarica
real_get, real_dir = sources._get, sources.ARCHIVE_DIR
try:
    _ag = []
    sources._get = lambda url, **k: (_ag.append(url), _J({"accessionNumber": ["a"]}))[1]
    sources.ARCHIVE_DIR = tempfile.mkdtemp()
    _f = {"name": "CIK0000000077-submissions-001.json", "filingFrom": "2020-01-01",
          "filingTo": "2022-12-31", "filingCount": 1}
    _a1, _a2 = sources._archive(_f), sources._archive(_f)
    check("un archivio letto una volta non si riscarica", len(_ag) == 1 and _a1 == _a2, _ag)
    sources._archive(dict(_f, filingCount=2))
    check("se la SEC dichiara un altro contenuto, si riscarica", len(_ag) == 2, _ag)
    sources.ARCHIVE_DIR = None
    sources._archive(_f)
    check("senza cartella di cache si scarica e basta", len(_ag) == 3, _ag)
finally:
    sources._get, sources.ARCHIVE_DIR = real_get, real_dir

# la mappa dei ticker della SEC si scarica una volta per giro
real_get, real_conf = sources._get, sources.confirms
try:
    _tg = []
    sources._get = lambda url, **k: (_tg.append(url), _J({"0": {"ticker": "AAA", "cik_str": 1},
                                                           "1": {"ticker": "BBB", "cik_str": 2}}))[1]
    sources.confirms = lambda cik, t: (True, t, [t])
    sources._TICKERS = None
    _c1, _c2 = sources.cik_for("AAA"), sources.cik_for("BBB")
    check("due aziende, una sola richiesta della mappa dei ticker",
          len(_tg) == 1 and _c1[0] == "0000000001" and _c2[0] == "0000000002", (_tg, _c1, _c2))
finally:
    sources._get, sources.confirms, sources._TICKERS = real_get, real_conf, None

# i prezzi si tagliano alla stessa finestra per ogni fonte
real_y, real_s = sources._prices_yahoo, sources._prices_stooq
try:
    sources._prices_yahoo = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("giù"))
    _old = [{"d": (date.today() - timedelta(days=d)).isoformat(), "c": 1.0, "v": 1} for d in range(4000, -1, -1)]
    sources._prices_stooq = lambda *a, **k: _old
    _px, _src = sources.prices("ZZZ")
    check("stooq dà dieci anni, se ne tengono cinque",
          _src == "stooq" and _px[0]["d"] >= (date.today() - timedelta(days=int(5 * 365.25) + 8)).isoformat(), _px[0])
finally:
    sources._prices_yahoo, sources._prices_stooq = real_y, real_s

# la scheda mostra due anni di operazioni, i calcoli ne usano cinque
_full = {"price": {"last": {"d": "2026-11-20", "c": 1}}, "_px_long": [[1, 2]], "_ins": {},
         "tx": [{"date": "2022-01-01"}, {"date": "2024-12-01"}, {"date": "2026-11-01"}]}
_card = build.card_view(_full)
check("nella scheda non escono le chiavi di lavoro", "_px_long" not in _card and "_ins" not in _card)
check("e le operazioni sono quelle degli ultimi due anni",
      [t["date"] for t in _card["tx"]] == ["2024-12-01", "2026-11-01"], _card["tx"])
check("il dizionario in memoria resta intero", len(_full["tx"]) == 3 and "_px_long" in _full)
check("e la scheda dice quante sono in due anni", _card["tx_n"] == 2, _card.get("tx_n"))
_end = date(2026, 11, 20)
_many = [{"date": (_end - timedelta(days=700 - i)).isoformat()} for i in range(60)] + \
        [{"date": (_end - timedelta(days=100 - i)).isoformat()} for i in range(30)]
_cm = build.card_view({"price": {"last": {"d": _end.isoformat()}}, "tx": _many})
check("con molte operazioni recenti la scheda le porta tutte, e solo quelle",
      len(_cm["tx"]) == 30 and _cm["tx"][0]["date"] == (_end - timedelta(days=100)).isoformat()
      and _cm["tx_n"] == 90, (len(_cm["tx"]), _cm["tx"][0]))
_few = _many[:60] + _many[-5:]
_cf = build.card_view({"price": {"last": {"d": _end.isoformat()}}, "tx": _few})
check("con poche operazioni recenti porta le ultime 25", len(_cf["tx"]) == 25 and _cf["tx"][-1] == _few[-1],
      len(_cf["tx"]))

# i prezzi: se Yahoo non risponde vale la copia dell'ultimo giro riuscito, ma solo se è fresca
real_p, real_cache = sources.prices, build.CACHE
try:
    build.CACHE = tempfile.mkdtemp()
    _fresh = [{"d": (date.today() - timedelta(days=1)).isoformat(), "c": 10.0, "v": 1}]
    sources.prices = lambda sym: (_fresh, "yahoo")
    check("un giro riuscito salva i prezzi", build.prices_or_copy("ZZ") == (_fresh, "yahoo"))
    def _down(sym):
        raise RuntimeError("429 da Yahoo")
    sources.prices = _down
    _px, _src = build.prices_or_copy("ZZ")
    check("il giro dopo, con Yahoo giù, usa la copia e lo dice", _px == _fresh and _src == "yahoo (cache)", _src)
    try:
        build.prices_or_copy("ZZ", max_age=0)
        check("una copia troppo vecchia non vale", False)
    except RuntimeError:
        check("una copia troppo vecchia non vale", True)
    try:
        build.prices_or_copy("MAI")
        check("senza copia l'errore resta", False)
    except RuntimeError:
        check("senza copia l'errore resta", True)
finally:
    sources.prices, build.CACHE = real_p, real_cache

# la prova all'indietro usa la serie lunga e non si accorcia per un titolo giovane
_long = [_co(f"L{k}", 0.0006 * (k + 1), n=900) for k in range(7)] + [_co("YOUNG", 0.003, n=250)]
for _d in _long:
    _d["_px_long"], _d["price"]["series"] = _d["price"]["series"], _d["price"]["series"][-200:]
_rl = PR.run(_long, cfg)
check("con la serie lunga le date arrivano indietro",
      _rl["from"] <= (date(2025, 1, 1) + timedelta(days=130)).isoformat(), _rl["from"])
check("un titolo con meno storia non accorcia gli altri",
      [x for x in _rl["rows"] if x["id"] == "price"][0]["dates"] >= 100, _rl["rows"][0])


# --------------------------------- i CIK precedenti (ExxonMobil in Texas)
_new = {"filings": {"recent": {"form": ["8-K", "4"], "accessionNumber": ["N-1", "N-2"],
                               "filingDate": ["2026-09-01", "2026-08-01"],
                               "primaryDocument": ["n.htm", "n.xml"], "items": ["7.01", ""]}}}
_old = {"filings": {"recent": {"form": ["4", "8-K"], "accessionNumber": ["O-1", "O-2"],
                               "filingDate": ["2025-03-01", "2024-02-01"],
                               "primaryDocument": ["o.xml", "o.htm"], "items": ["", "5.02"]}}}
try:
    sources._SUBS, sources._TAB = {"0002115436": _new, "0000034088": _old}, {}
    sources.PREDECESSORS = {"0002115436": ["0000034088"]}
    _cols, _cov = sources.filings_table("2115436", "2024-01-01")
    check("i depositi del CIK precedente si uniscono a quelli nuovi",
          _cols["accessionNumber"] == ["N-1", "N-2", "O-1", "O-2"], _cols["accessionNumber"])
    check("e l'elenco è completo da quando lo è quello vecchio", _cov == "2024-02-01", _cov)
    _again, _ = sources.filings_table("2115436", "2024-01-01")
    check("una seconda chiamata non li unisce due volte", len(_again["accessionNumber"]) == 4, _again["accessionNumber"])
    _f4 = sources.form4_filings("2115436", 60)
    check("un Form 4 vecchio si prende dalla cartella del CIK vecchio",
          [f["url"].split("/data/")[1].split("/")[0] for f in _f4] == ["2115436", "34088"], [f["url"] for f in _f4])
    check("gli 8-K arrivano da entrambi",
          [e["date"] for e in sources.recent_8k("2115436", "2024-01-01")] == ["2024-02-01", "2026-09-01"])
    check("un Form 4 con l'emittente vecchio viene accettato",
          len(parse_form4(_X.format(ic="0000034088"), issuer_cik=["0002115436", "0000034088"])) == 1)
    check("uno di un altro emittente no",
          parse_form4(_X.format(ic="0000000099"), issuer_cik=["0002115436", "0000034088"]) is None)
finally:
    sources._SUBS, sources._TAB, sources.PREDECESSORS = {}, {}, {}

# --------------------------------- lo short interest dai file pubblici di FINRA
print("\nShort interest da FINRA")
from pipeline import finra

_P = finra.periods(date(2026, 11, 22))
check("la prima rilevazione da cercare è quella del 15 novembre (domenica: si prova da venerdì 13)",
      _P[0] == ("2026-11-A", [date(2026, 11, 13), date(2026, 11, 12), date(2026, 11, 11), date(2026, 11, 10)]),
      _P[0])
check("poi fine ottobre (sabato 31: da venerdì 30)", _P[1][0] == "2026-10-B" and _P[1][1][0] == date(2026, 10, 30),
      _P[1])
check("cinque anni sono circa 120 rilevazioni", 118 <= len(_P) <= 122, len(_P))
check("mai un sabato o una domenica fra i giorni da provare",
      all(d.weekday() < 5 for _, ds in _P for d in ds))

_HEAD = ("accountingYearMonthNumber|symbolCode|issueName|issuerServicesGroupExchangeCode|marketClassCode|"
         "currentShortPositionQuantity|previousShortPositionQuantity|stockSplitFlag|averageDailyVolumeQuantity|"
         "daysToCoverQuantity|revisionFlag|changePercent|changePreviousNumber|settlementDate")


def _file(day, rows):
    return _HEAD + "\n" + "\n".join(
        f"202610|{t}|{t} INC|A|NNM|{si}|1|N|{vol}|{dtc}||0|0|{day}" for t, si, vol, dtc in rows) + "\n"


_d, _rows = finra.parse(_file("2026-10-30", [("AAA", 1000, 50, 20.0), ("ZZZ", 5, 5, 1.0)]), {"AAA"})
check("del file restano solo le aziende che servono", _rows == {"AAA": [1000.0, 50.0, 20.0]}, _rows)
_d2, _rows2 = finra.parse(_HEAD.replace("|", ",") + "\n202610,AAA,A,A,NNM,7,1,N,2,3.5,,0,0,2026-10-30\n", {"AAA"})
check("se il separatore diventa la virgola si legge lo stesso", _rows2 == {"AAA": [7.0, 2.0, 3.5]}, _rows2)
try:
    finra.parse("a|b|c\n1|2|3\n", {"AAA"})
    check("un file con colonne sconosciute è un errore, non zero righe", False)
except ValueError:
    check("un file con colonne sconosciute è un errore, non zero righe", True)

# Un FINRA finto: un file per ogni 15 e ogni fine mese in giorno feriale, da
# luglio 2026. Prima di luglio non c'è niente (come se i file non esistessero).
_fin = {}
for _k, _ds in finra.periods(date(2026, 11, 22)):
    if _ds[0] >= date(2026, 7, 1) and _ds[0] <= date(2026, 10, 31):
        _fin[_ds[0]] = _file(_ds[0].isoformat(), [("AAA", 1000 + _ds[0].toordinal() % 97, 50, 2.0),
                                                   ("BBB", 300, 10, 30.0)])
_asked = []


def _fget(d):
    _asked.append(d)
    return _fin.get(d)


_cd = tempfile.mkdtemp()
_ser, _st = finra.update(_cd, ["AAA", "BBB"], today=date(2026, 11, 22), files=5, get=_fget)
check("con un tetto di 5 richieste arrivano le prime rilevazioni e le altre restano in coda",
      len(_asked) == 5 and _st["pending"] > 100 and _st["readings"] >= 1, (_asked, _st))
check("la rilevazione di novembre non è ancora uscita e non viene segnata come inesistente",
      not os.path.exists(os.path.join(_cd, "2026-11-A.json")))
_asked.clear()
_ser, _st = finra.update(_cd, ["AAA", "BBB"], today=date(2026, 11, 22), files=1000, get=_fget)
check("con il tetto largo arrivano tutte, otto rilevazioni da luglio a ottobre",
      _st["readings"] == 8 and _st["pending"] == 0 and len(_ser["AAA"]) == 8, _st)
check("in ordine di data e con i campi degli export manuali",
      [r["date"] for r in _ser["AAA"]] == sorted(r["date"] for r in _ser["AAA"])
      and set(_ser["AAA"][0]) == {"date", "si", "vol", "dtc"} and _ser["BBB"][0]["dtc"] == 30.0, _ser["AAA"][0])
check("le rilevazioni più vecchie, che non esistono, sono segnate in cache",
      os.path.exists(os.path.join(_cd, "2025-03-A.json")))
_asked.clear()
_ser, _st = finra.update(_cd, ["AAA", "BBB"], today=date(2026, 11, 22), files=1000, get=_fget)
check("il giro dopo riprova solo novembre, che non è ancora uscito",
      sorted(set(_asked)) == [date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13)],
      _asked)
_asked.clear()
_ser, _st = finra.update(_cd, ["AAA", "BBB", "CCC"], today=date(2026, 11, 22), files=3, get=_fget)
check("un'azienda nuova fa riscaricare le rilevazioni, a rate",
      len(_asked) == 3 and _st["pending"] > 0 and len(_ser["AAA"]) == 8, (_asked, _st))
_ser, _st = finra.update(_cd, ["AAA", "BBB", "CCC"], today=date(2026, 11, 22), offline=True, get=_fget)
check("senza rete si usa quello che c'è in cache", len(_ser["AAA"]) == 8)

# se FINRA non risponde a niente, nessuna rilevazione va segnata come inesistente
_cd2 = tempfile.mkdtemp()
_ser, _st = finra.update(_cd2, ["AAA"], today=date(2026, 11, 22), files=1000, get=lambda d: None)
check("FINRA giù non diventa \"nessun dato per cinque anni\"",
      os.listdir(_cd2) == [] and _ser == {} and _st["pending"] > 100, (os.listdir(_cd2)[:3], _st))

# nella scheda: FINRA quando ha almeno otto rilevazioni, altrimenti l'export manuale
_si8 = [{"date": f"2026-0{1 + i // 2}-{'15' if i % 2 == 0 else '28'}", "si": 1e6 + i, "vol": 1e5, "dtc": 3.0}
        for i in range(8)]
_fs = build_one(cfg, dict(raws[2], ticker="FINR", si=_si8))
check("con otto rilevazioni FINRA la voce mercato c'è e la fonte è FINRA",
      _fs["si_source"] == "finra" and _fs["_subs"]["market"]["v"] is not None and len(_fs["_si_long"]) == 8,
      _fs.get("si_source"))
_fs2 = build_one(cfg, dict(raws[2], ticker="FINR", si=_si8[:3]))
check("con tre sole rilevazioni e nessun export la voce non ha ancora un valore",
      _fs2["si_source"] == "finra" and _fs2["_subs"]["market"]["v"] is None, _fs2["_subs"].get("market"))
_fs3 = build_one(cfg, dict(raws[2], ticker="FINR"))
check("senza FINRA e senza export non c'è fonte", _fs3["si_source"] is None and _fs3["_subs"]["market"]["v"] is None)
check("la serie lunga resta fuori dalla scheda", "_si_long" not in build.card_view(_fs))

# --------------------------------- le trascrizioni da Alpha Vantage
print("\nTrascrizioni da Alpha Vantage")
from pipeline import trascrizioni as TR

check("Nvidia (anno fiscale a gennaio): la call di novembre 2026 è il 2027Q3",
      TR.fiscal_label("2026-11-19", 1) == "2027Q3")
check("Oracle (a maggio): la call di settembre 2026 è il 2027Q1, quella di dicembre il 2027Q2",
      TR.fiscal_label("2026-09-09", 5) == "2027Q1" and TR.fiscal_label("2026-12-10", 5) == "2027Q2")
check("anno solare: la call di gennaio commenta il quarto trimestre dell'anno prima",
      TR.calendar_label("2026-01-27") == "2025Q4" and TR.calendar_label("2026-10-27") == "2026Q3")

_ev = lambda *ds: [[d, ["2.02", "9.01"]] for d in ds] + [["2026-09-01", ["5.02"]]]
_cards = {"AAA": {"company": {"ticker": "AAA", "cik": "1"}, "events_hist": _ev("2026-04-20", "2026-07-21", "2026-10-20")},
          "BBB": {"company": {"ticker": "BBB", "cik": "2"}, "events_hist": _ev("2026-07-28", "2026-10-28")},
          "CCC": {"company": {"ticker": "CCC", "cik": "3"}, "events_hist": _ev("2026-10-30")}}
_idx = {"companies": [{"ticker": "AAA", "score": 70, "buyers": 0},
                      {"ticker": "BBB", "score": 40, "buyers": 2},
                      {"ticker": "CCC", "score": 50, "buyers": 0, "cluster": {"n": 3}}]}
_w = TR.wanted(_idx, _cards, set(), per_company=2, today=date(2026, 11, 22))
check("prima il cluster, poi i compratori, poi il punteggio; prima l'ultima call di tutti",
      _w == [("CCC", "2026-10-30"), ("BBB", "2026-10-28"), ("AAA", "2026-10-20"),
             ("BBB", "2026-07-28"), ("AAA", "2026-07-21")], _w)
_w2 = TR.wanted(_idx, _cards, {("BBB", "2026-10-28")}, per_company=2, today=date(2026, 11, 22))
check("una call già presa si salta", ("BBB", "2026-10-28") not in _w2 and len(_w2) == 4, _w2)
check("un 8-K che non è un risultato (5.02) non è una call",
      all(d != "2026-09-01" for _, d in _w))

_xd = tempfile.mkdtemp()
open(os.path.join(_xd, "AAA_2026Q3.json"), "w").write("{}")
check("le call già estratte in input/extracted si riconoscono dal nome del file",
      TR.extracted_pairs(_xd, _cards, {"AAA": 12}) == {("AAA", "2026-10-20")})

check("una trascrizione di Alpha Vantage diventa una lista di interventi",
      TR.parse_av({"symbol": "AAA", "quarter": "2026Q3", "transcript": [
          {"speaker": "Jane Doe", "title": "CEO", "content": "We raised guidance.", "sentiment": "0.7"},
          {"speaker": "Op", "title": "", "content": "  "}]}) ==
      [{"speaker": "Jane Doe", "title": "CEO", "content": "We raised guidance."}])
check("una risposta senza trascrizione è vuota, non un errore", TR.parse_av({"symbol": "AAA"}) == [])
for _msg in ["We have detected your API key as ... our standard API rate limit is 25 requests per day.",
             "Thank you for using Alpha Vantage! This is a premium endpoint."]:
    try:
        TR.parse_av({"Information": _msg})
        check("quota finita o piano a pagamento fermano il giro: " + _msg[:30], False)
    except TR.Limit:
        check("quota finita o piano a pagamento fermano il giro: " + _msg[:30], True)
# la call di un anno prima si scarta (TJX, 22 novembre 2026: chiesta 2026Q3, arrivata la fiscal 2026)
_r26 = [{"speaker": "Op", "title": "", "content": "Welcome to the Third Quarter Fiscal 2026 call, 11/19/2025."}]
_r27 = [{"speaker": "Op", "title": "", "content": "Welcome to the Third Quarter Fiscal 2027 call."}]
check("una trascrizione dell'anno sbagliato non passa il controllo",
      not TR.call_matches(_r26, "2027Q3") and TR.call_matches(_r27, "2027Q3"))
check("vale anche la forma corta FY27",
      TR.call_matches([{"speaker": "", "title": "", "content": "our FY27 third quarter"}], "2027Q3"))
_st = TR.clean_state({"done": [["TJX", "2026-11-18"], ["BA", "2026-10-27"]]}, {"TJX": 1, "BA": 12})
check("le call prese con la vecchia riserva si richiedono, quelle a calendario no",
      _st["done"] == [["BA", "2026-10-27"]] and _st["v"] == 2, _st)
check("e la pulizia si fa una volta sola",
      TR.clean_state({"v": 2, "done": [["TJX", "x"]]}, {"TJX": 1})["done"] == [["TJX", "x"]])
_calls = []
check("la richiesta chiede il trimestre giusto",
      TR.fetch("AAA", "2026Q3", "k", get=lambda p: (_calls.append(p), {"transcript": []})[1]) == []
      and _calls[0]["quarter"] == "2026Q3" and _calls[0]["function"] == "EARNINGS_CALL_TRANSCRIPT")
try:
    import docx  # noqa: F401  (c'è solo nel workflow delle trascrizioni)
    _dp = os.path.join(tempfile.mkdtemp(), "AAA_2026Q3.docx")
    TR.to_docx(_dp, "AAA", "AAA INC", "2026Q3", "2026-10-20",
               [{"speaker": "Jane Doe", "title": "CEO", "content": "We raised guidance."}])
    _txt = "\n".join(p.text for p in docx.Document(_dp).paragraphs)
    check("il file Word contiene chi parla e che cosa dice", "Jane Doe" in _txt and "We raised guidance." in _txt)
except ImportError:
    print("  (python-docx non installato: la prova del file Word si salta)")

# --------------------------------- Alpha Vantage: calendario, titoli, utili
print("\nAlpha Vantage oltre alle trascrizioni")
from pipeline import alphavantage as AVM

_lst = ("symbol,name,exchange,assetType,ipoDate,delistingDate,status\n"
        "BNY,The Bank of New York Mellon,NYSE,Stock,2007-07-02,null,Active\n"
        "AAPL,Apple Inc,NASDAQ,Stock,1980-12-12,null,Active\n")
_gone = ("symbol,name,exchange,assetType,ipoDate,delistingDate,status\n"
         "BK,The Bank of New York Mellon Corp,NYSE,Stock,2007-07-02,2026-05-21,Delisted\n")
check("l'elenco tiene solo i ticker dell'universo",
      set(AVM.parse_listing(_lst, ["BNY", "BK"])) == {"BNY"})
check("e dei titoli tolti legge la data",
      AVM.parse_listing(_gone, ["BK"])["BK"]["delisted"] == "2026-05-21")
_cal = ("symbol,name,reportDate,fiscalDateEnding,estimate,currency\n"
        "NVDA,NVIDIA Corp,2027-02-25,2027-01-31,1.52,USD\n"
        "ZZZZ,Altro,2027-01-10,2026-12-31,,USD\n"
        "NVDA,NVIDIA Corp,2026-12-01,2026-10-31,,USD\n")
_pc = AVM.parse_calendar(_cal, ["NVDA"])
check("il calendario: solo l'universo, in ordine di data",
      list(_pc) == ["NVDA"] and [r["date"] for r in _pc["NVDA"]] == ["2026-12-01", "2027-02-25"], _pc)
check("una stima mancante resta vuota, non zero", _pc["NVDA"][0]["estimate"] is None)
_earn_js = json.dumps({"symbol": "AAA", "quarterlyEarnings": [
    {"fiscalDateEnding": "2026-09-30", "reportedDate": "2026-10-28", "reportedEPS": "1.10",
     "estimatedEPS": "1.00", "surprise": "0.1", "surprisePercentage": "10", "reportTime": "post-market"},
    {"fiscalDateEnding": "2026-06-30", "reportedDate": "2026-07-29", "reportedEPS": "0.90",
     "estimatedEPS": "1.00", "surprise": "None", "surprisePercentage": "None", "reportTime": "pre-market"},
    {"fiscalDateEnding": "2026-03-31", "reportedDate": "2026-04-28", "reportedEPS": "0.80",
     "estimatedEPS": "None", "surprise": "None", "surprisePercentage": "None"}]})
_pe = AVM.parse_earnings(_earn_js)
check("gli utili arrivano dal più vecchio", [r["date"] for r in _pe] == ["2026-04-28", "2026-07-29", "2026-10-28"])
check("la sorpresa di Alpha Vantage si usa così com'è", _pe[-1]["sp"] == 10.0, _pe[-1])
check("se manca la si calcola da utile e attese", _pe[1]["sp"] == -10.0, _pe[1])
check("senza attese non c'è sorpresa", _pe[0]["sp"] is None and _pe[0]["est"] is None, _pe[0])
_lim = json.dumps({"Information": "We have detected your API key as X and our standard API rate limit is 25 requests per day."})
try:
    AVM.parse_csv(_lim)
    check("un CSV che è un messaggio di limite ferma il giro", False)
except AVM.Limit:
    check("un CSV che è un messaggio di limite ferma il giro", True)
check("la classe Limit è la stessa per trascrizioni e utili", TR.Limit is AVM.Limit)

_ls = [{"date": "2026-07-29", "sp": -3.0}, {"date": "2026-10-28", "sp": 4.2}]
check("l'ultima trimestrale prima dell'acquisto",
      AVM.last_surprise(_ls, "2026-11-05")["sp"] == 4.2)
check("il giorno stesso dell'annuncio non conta ancora",
      AVM.last_surprise(_ls, "2026-10-28")["sp"] == -3.0)
check("dopo cento giorni non conta più", AVM.last_surprise(_ls, "2027-03-01") is None)
check("prima della prima trimestrale non c'è niente", AVM.last_surprise(_ls, "2026-01-01") is None)

# il giro giornaliero: prima titoli e calendario, poi gli utili, entro il budget
_avd = tempfile.mkdtemp()
_asked = []


def _avget(params):
    _asked.append(params["function"] + ("/" + params["symbol"] if "symbol" in params else "")
                  + ("/" + params["state"] if "state" in params else ""))
    f = params["function"]
    if f == "LISTING_STATUS":
        return _gone if params.get("state") == "delisted" else _lst
    if f == "EARNINGS_CALENDAR":
        return ("symbol,name,reportDate,fiscalDateEnding,estimate,currency\n"
                "AAPL,Apple,2027-01-29,2026-12-31,2.40,USD\n")
    return _earn_js


_uni = ["BNY", "AAPL", "BK", "MSFT"]
_u, _lg, _stop = AVM.update(_avd, _uni, "k", budget=25, today=date(2026, 11, 23), per_day=2, get=_avget)
check("primo giorno: elenco (1), calendario (1) e due aziende di utili",
      _u == 4 and _asked == ["LISTING_STATUS", "EARNINGS_CALENDAR",
                             "EARNINGS/BNY", "EARNINGS/AAPL"] and not _stop, (_u, _asked))
check("l'elenco dei titoli tolti non si chiede più (Alpha Vantage risponde {})",
      "LISTING_STATUS/delisted" not in _asked)
check("il riepilogo dice quali ticker non sono quotati", any("BK" in x and "MSFT" in x for x in _lg), _lg)
check("e quante aziende restano per i giorni dopo", any("restano 2" in x for x in _lg), _lg)
_asked.clear()
_u, _lg, _ = AVM.update(_avd, _uni, "k", budget=25, today=date(2026, 11, 23), per_day=2, get=_avget)
check("lo stesso giorno non si richiedono né elenco né calendario",
      _asked == ["EARNINGS/BK", "EARNINGS/MSFT"], _asked)
_asked.clear()
AVM.update(_avd, _uni, "k", budget=25, today=date(2026, 11, 24), per_day=2, get=_avget)
check("il giorno dopo solo il calendario: l'elenco aspetta una settimana",
      _asked == ["EARNINGS_CALENDAR"], _asked)
_asked.clear()
AVM.update(_avd, _uni, "k", budget=25, today=date(2026, 12, 1), per_day=5, get=_avget)
check("dopo sette giorni l'elenco si rilegge", _asked[:1] == ["LISTING_STATUS"], _asked)
_asked.clear()
_u, _, _ = AVM.update(tempfile.mkdtemp(), _uni, "k", budget=1, today=date(2026, 11, 23), get=_avget)
check("con una richiesta sola si usa quella e basta",
      _u == 1 and _asked == ["LISTING_STATUS"], _asked)


def _limget(params):
    if params["function"] == "EARNINGS":
        return _lim
    return _avget(params)


_u, _lg, _stop = AVM.update(tempfile.mkdtemp(), _uni, "k", budget=25, today=date(2026, 11, 23), get=_limget)
check("il limite raggiunto ferma il giro e lo dice", _stop and _u == 3 and _lg[-1].startswith("STOP"), (_u, _lg))


# 23/11/2026: il primo giro vero è caduto su una risposta "{}" di Alpha Vantage.
# Ora un pezzo che non va si scrive nel riepilogo e il giro continua.
def _emptyget(which):
    def g(params):
        if params["function"] in which:
            _asked.append(params["function"])
            return "{}"
        return _avget(params)
    return g


_asked.clear()
_d1 = tempfile.mkdtemp()
_u, _lg, _stop = AVM.update(_d1, _uni, "k", budget=25, today=date(2026, 11, 23), per_day=2,
                            get=_emptyget({"LISTING_STATUS"}))
check("un elenco vuoto non ferma il giro: calendario e utili arrivano lo stesso",
      not _stop and _asked == ["LISTING_STATUS", "EARNINGS_CALENDAR", "EARNINGS/BNY", "EARNINGS/AAPL"], _asked)
check("e il riepilogo lo dice, con quello che ha risposto Alpha Vantage",
      any(x.startswith("ERR   elenco") and "{}" in x for x in _lg), _lg)
check("l'elenco non salvato si riprova al giro dopo",
      AVM.load(_d1)["listing"] is None and "listing_on" not in json.load(open(os.path.join(_d1, "stato.json"))))
_asked.clear()
_u, _lg, _stop = AVM.update(tempfile.mkdtemp(), _uni, "k", budget=25, today=date(2026, 11, 23), per_day=2,
                            get=_emptyget({"EARNINGS_CALENDAR"}))
check("un calendario vuoto non ferma gli utili",
      not _stop and _asked[-2:] == ["EARNINGS/BNY", "EARNINGS/AAPL"] and any(x.startswith("ERR   calendario") for x in _lg), _lg)
_d2 = tempfile.mkdtemp()
AVM.update(_d2, _uni, "k", budget=25, today=date(2026, 11, 23), per_day=0,
           get=lambda p: _lst.splitlines()[0] + "\nBNY,x,NYSE,Stock,2000-01-01,null,Active\n"
           if p["function"] == "LISTING_STATUS" else _avget(p))
check("un elenco con meno di metà dell'universo non si usa: niente 150 falsi avvisi",
      AVM.load(_d2)["listing"] is None)


def _boom(params):
    if params["function"] == "EARNINGS_CALENDAR":
        raise RuntimeError("rete giù")
    return _avget(params)


_u, _lg, _stop = AVM.update(tempfile.mkdtemp(), _uni, "k", budget=25, today=date(2026, 11, 23), get=_boom)
check("anche un errore inatteso si scrive e non fa cadere il programma",
      not _stop and any("RuntimeError" in x for x in _lg), _lg)

# 23/11/2026, secondo giro: richieste troppo ravvicinate. Alpha Vantage ne
# regge una al secondo; il suo messaggio cita anche le 25 al giorno e veniva
# preso per il limite del giorno. E un calendario vuoto era stato salvato.
_burst = json.dumps({"Information": "Thank you for using Alpha Vantage! Please consider spreading out your free "
                                    "API requests more sparingly (1 request per second). You may subscribe to any "
                                    "of the premium plans at https://www.alphavantage.co/premium/ to lift the free "
                                    "key rate limit (25 requests per day), raise the per-second burst limit"})
try:
    AVM.parse_earnings(_burst)
    check("il messaggio «rallenta» si riconosce come tale", False)
except AVM.Burst as e:
    check("il messaggio «rallenta» si riconosce come tale, non come limite del giorno", isinstance(e, AVM.Limit))
try:
    AVM.parse_earnings(_lim)
    check("il limite del giorno resta il limite del giorno", False)
except AVM.Burst:
    check("il limite del giorno resta il limite del giorno", False)
except AVM.Limit:
    check("il limite del giorno resta il limite del giorno", True)


def _burstget(times):
    left = {"n": times}

    def g(params):
        _asked.append(params["function"])
        if params["function"] == "EARNINGS_CALENDAR" and left["n"] > 0:
            left["n"] -= 1
            return _burst
        return _avget(params)
    return g


_asked.clear()
_d3 = tempfile.mkdtemp()
_u, _lg, _stop = AVM.update(_d3, _uni, "k", budget=25, today=date(2026, 11, 23), per_day=1, get=_burstget(1))
check("dopo un «rallenta» si aspetta, si riprova e il calendario arriva",
      not _stop and AVM.load(_d3)["next"].get("AAPL") and any(x.startswith("PAUSA") for x in _lg), _lg)
check("e la richiesta ripetuta si conta", _u == 4, _u)
_u, _lg, _stop = AVM.update(tempfile.mkdtemp(), _uni, "k", budget=25, today=date(2026, 11, 23), get=_burstget(2))
check("due «rallenta» di fila fermano il giro", _stop and _lg[-1].startswith("STOP"), _lg)
check("un CSV con il BOM all'inizio si legge lo stesso",
      list(AVM.parse_calendar("﻿" + _cal, ["NVDA"])) == ["NVDA"])
for _empty in ("", "symbol,name,reportDate,fiscalDateEnding,estimate,currency\n"):
    try:
        AVM.parse_calendar(_empty, ["NVDA"])
        check("un calendario vuoto è un errore, non un calendario", False)
    except ValueError:
        check("un calendario vuoto è un errore, non un calendario", True)
try:
    AVM.parse_calendar(_cal, ["XXXX"])
    check("un calendario senza nessuna azienda dell'universo è un errore", False)
except ValueError:
    check("un calendario senza nessuna azienda dell'universo è un errore", True)
_d4 = tempfile.mkdtemp()
os.makedirs(_d4, exist_ok=True)
json.dump({"calendar_on": "2026-11-23"}, open(os.path.join(_d4, "stato.json"), "w"))
json.dump({"on": "2026-11-23", "next": {}, "seen": {}}, open(os.path.join(_d4, "calendario.json"), "w"))
_asked.clear()
AVM.update(_d4, _uni, "k", budget=25, today=date(2026, 11, 23), per_day=0, get=_avget)
check("un calendario vuoto salvato oggi si richiede lo stesso giorno", "EARNINGS_CALENDAR" in _asked, _asked)

# con la rete vera, fra due richieste passa almeno PAUSE secondi, anche per le trascrizioni
import requests as _rq
_slept, _orig = [], (_rq.get, AVM.time.sleep)


class _Resp:
    content = json.dumps({"symbol": "X", "transcript": []}).encode()

    def raise_for_status(self):
        pass


_rq.get = lambda *a, **k: _Resp()
AVM.time.sleep = lambda x: _slept.append(x)
try:
    AVM._last[0] = 0.0
    AVM.request({"function": "EARNINGS", "symbol": "X"}, "k")
    TR.fetch("X", "2026Q3", "k")
finally:
    _rq.get, AVM.time.sleep = _orig
check("fra una richiesta e la successiva si aspetta (anche fra utili e trascrizioni)",
      len(_slept) == 1 and _slept[0] > AVM.PAUSE - .2, _slept)

# quando rileggere gli utili di un'azienda che li ha già
_st = {"earnings_on": {"AAA": "2026-10-01"}}
_rep = {"AAA": ["2026-11-01"]}
_due = lambda on, today: AVM.earnings_due(["AAA"], {"earnings_on": {"AAA": on}}, {}, _rep,
                                          today, {"AAA": "2026-08-01"})
check("dopo una trimestrale nuova si rileggono", _due("2026-10-01", date(2026, 11, 5)) == ["AAA"])
check("e si riprova per qualche giorno se il dato non c'è ancora", _due("2026-11-02", date(2026, 11, 5)) == ["AAA"])
check("ma non due volte lo stesso giorno", _due("2026-11-05", date(2026, 11, 5)) == [])
check("e dopo tre giorni si aspetta la prossima", _due("2026-11-05", date(2026, 11, 10)) == [])
check("una trimestrale di oggi non c'è ancora", _due("2026-10-01", date(2026, 11, 1)) == [])
check("se l'ultima trimestrale l'abbiamo già, niente",
      AVM.earnings_due(["AAA"], _st, {}, _rep, date(2026, 11, 5), {"AAA": "2026-11-01"}) == [])
check("chi non ha mai avuto gli utili viene dopo chi ha appena annunciato",
      AVM.earnings_due(["NEW", "AAA"], _st, {}, _rep, date(2026, 11, 5), {"AAA": "2026-08-01"}) == ["AAA", "NEW"])

_av = AVM.load(_avd)
check("il giro del sito legge utili, calendario ed elenco",
      set(_av["earnings"]) == set(_uni) and _av["next"]["AAPL"][0]["date"] == "2027-01-29"
      and _av["listing"]["on"] == "2026-12-01", _av.keys())
check("un ticker che non è più nell'elenco diventa un avviso",
      "tolto dalla borsa" in (AVM.listing_problem(_av, "BK") or "")
      and "config.json" in (AVM.listing_problem(_av, "BK") or ""))
check("uno che non compare dice di controllare il ticker", "cambiato ticker" in (AVM.listing_problem(_av, "MSFT") or ""))
check("un ticker quotato non dà avvisi", AVM.listing_problem(_av, "BNY") is None)
check("senza la cartella tutto è vuoto e niente si rompe",
      AVM.load(os.path.join(_avd, "non-esiste")) == {"earnings": {}, "next": {}, "calendar_on": None, "listing": None})

# il bonus "dopo una sorpresa positiva": si stima sulle sole campagne con il dato
_vb = {"cluster": 10.0, "two": 4.0, "senior": 6.0, "beat": 5.0}
_cb = _fake_camps(6000, _vb, seed=11)
for _i, _c in enumerate(_cb):
    if _i % 3 == 0:                       # un terzo delle campagne senza il dato
        _c["car"] -= 5.0 if _c.get("beat") else 0.0
        _c["beat"] = None
_rnd2 = _rnd.Random(5)
for _c in _cb:
    if "beat" not in _c:
        _c["beat"] = _rnd2.random() < .5
        _c["car"] += 5.0 if _c["beat"] else 0.0
_eb = {r["id"]: r for r in BO.estimate(_cb)["features"]}
check("ritrova l'effetto della sorpresa positiva", abs(_eb["beat"]["points"] - 5) <= 1, _eb["beat"])
check("e dice su quante campagne il dato c'è", _eb["beat"]["known"] == 4000, _eb["beat"].get("known"))
_eb0 = {r["id"]: r for r in BO.estimate([dict(c, beat=None) for c in _cb])["features"]}
check("le altre tre stime non cambiano se il dato c'è o no",
      all(_eb[f]["raw"] == _eb0[f]["raw"] for f in ("cluster", "two", "senior")))
check("senza il dato il bonus non si stima e vale zero",
      _eb0["beat"]["raw"] is None and _eb0["beat"]["points"] == 0 and _eb0["beat"]["known"] == 0)
check("a mano la sorpresa non valeva niente", BO.HAND["beat"] == 0 and score.HAND_BONUS["beat"] == 0)
_camps_b = BO.campaigns([{"car": 1.0, "t": "A", "d": "2025-01-10", "people": 1, "beat": True},
                         {"car": 2.0, "t": "A", "d": "2025-02-10", "people": 1, "beat": False}])
check("la campagna prende la sorpresa del primo acquisto", _camps_b[0]["beat"] is True and len(_camps_b) == 1)

# la voce insider: il bonus si applica e la ragione lo dice
_pts = {"cluster": 0, "two": 0, "senior": 0, "beat": 7}
_v1, _w1, _ = score.insider_score(_tx, [], {}, cfg, date(2026, 4, 20), bonus=_pts,
                                  beat={"date": "2026-03-10", "sp": 4.2})
_v0, _w0, _ = score.insider_score(_tx, [], {}, cfg, date(2026, 4, 20), bonus=_pts)
check("dopo una sorpresa positiva vale il bonus stimato", _v1 - _v0 == 7, (_v1, _v0))
check("e la ragione dice di quanto e quando",
      any("+4,2%" in w["it"] and "2026-03-10" in w["it"] and "+7" in w["it"] for w in _w1), _w1)
_vn, _wn, _ = score.insider_score(_tx, [], {}, cfg, date(2026, 4, 20), bonus=_pts,
                                  beat={"date": "2026-03-10", "sp": -2.0})
check("dopo una trimestrale sotto le attese niente bonus, ma lo dice",
      _vn == _v0 and any("sotto le attese" in w["it"] for w in _wn), _wn)

# nella scheda: gli utili, la prossima trimestrale, e la sorpresa prima degli acquisti
_last = series[-1]["d"]
_eraw = dict(raws[0], ticker="EEEE",
             earnings=[{"fiscal_end": "2025-12-31", "date": series[-60]["d"], "eps": 1.1, "est": 1.0, "sp": 10.0,
                        "time": None}],
             calendar=[{"date": "2025-01-01", "fiscal_end": None, "estimate": None},
                       {"date": "2099-02-01", "fiscal_end": "2098-12-31", "estimate": 1.3}])
_eb1 = build_one(cfg, _eraw)
check("la scheda porta gli utili contro attese", _eb1["earnings"][0]["sp"] == 10.0, _eb1.get("earnings"))
check("e la prossima trimestrale, non una passata", _eb1["next_report"]["date"] == "2099-02-01", _eb1.get("next_report"))
check("la voce insider vede la sorpresa prima del primo acquisto",
      _eb1["_ins"]["beat"] == {"date": series[-60]["d"], "sp": 10.0}
      and any("sopra le attese" in w["it"] for w in _eb1["_subs"]["insider"]["why"]), _eb1["_ins"])
check("senza dati di Alpha Vantage la scheda resta com'era",
      build_one(cfg, dict(raws[0], ticker="FFFF"))["earnings"] is None)
_fin = finish(cfg, _eb1, [_eb1["insider_90d"]] * 4)
check("e fra le fonti compare Alpha Vantage", any(x["n"] == 6 for x in _fin["sources"]), _fin["sources"])
_ix = write_all([_fin], cfg, tempfile.mkdtemp(), av=_av)
check("l'indice dice quante aziende hanno gli utili e quali ticker non risultano quotati",
      _ix["alphavantage"]["earnings"] == 1 and _ix["alphavantage"]["listing_on"] == "2026-12-01"
      and _ix["alphavantage"]["not_listed"] == ["EEEE"], _ix["alphavantage"])
check("e per ogni azienda la data della prossima trimestrale", _ix["companies"][0]["next_report"] == "2099-02-01")
_br = build.backtest_rows([{"company": {"ticker": "Z"}, "tx": [cfo],
                            "tracks": {"Y": {"buys": [{"date": "2025-01-15", "car": 1.0}]}},
                            "_earn_long": [{"date": "2024-12-01", "sp": 3.0}]},
                           {"company": {"ticker": "W"}, "tx": [cfo],
                            "tracks": {"Y": {"buys": [{"date": "2025-01-15", "car": 1.0}]}}}], CFG)
check("nel backtest ogni acquisto sa com'era andata l'ultima trimestrale, o che non si sa",
      [x["beat"] for x in _br] == [True, None], _br)

# la prova all'indietro: chi sorprende di più sale di più -> la voce vale 1
_ecos = []
for _k in range(8):
    _c = _co(f"S{_k}", 0.0006 * (_k + 1))
    _c["_earn_long"] = [{"date": (date(2024, 12, 15) + timedelta(days=90 * j)).isoformat(), "sp": float(_k + 1)}
                        for j in range(6)]
    _ecos.append(_c)
_su = [x for x in PR.run(_ecos, cfg)["rows"] if x["id"] == "surprise"][0]
check("la sorpresa sugli utili entra nella prova all'indietro", _su["ic"] == 1.0, _su)
_su0 = [x for x in PR.run(_cos, cfg)["rows"] if x["id"] == "surprise"][0]
check("senza utili la voce non si calcola, invece di valere zero", _su0["ic"] is None, _su0)

# il riepilogo deve contare ogni fallimento: l'elenco non va mai sostituito
check("l'elenco dei fallimenti è ancora quello di partenza", bad is _BAD0)
print(f"\n{ok} controlli passati, {len(bad)} falliti")
if bad:
    print("falliti:", ", ".join(bad))
sys.exit(1 if bad else 0)
