"""Insider abituali e opportunisti.

L'idea è di Cohen, Malloy e Pomorski ("Decoding Inside Information", Journal of
Finance, 2012). Molti insider comprano o vendono sempre nello stesso periodo
dell'anno: dopo il bonus, prima delle tasse, quando si apre la finestra dopo
una certa trimestrale. Quelle operazioni seguono il calendario, non quello che
l'insider sa, e nello studio originale non anticipano niente. Le altre, quelle
che rompono l'abitudine, sì.

La definizione, la stessa dello studio:

- un'operazione si può classificare solo se il suo autore ha fatto almeno
  un'operazione dello stesso verso (acquisto o vendita) in ciascuno dei tre
  anni solari precedenti;
- è **abituale** se in ciascuno di quei tre anni ce n'è stata una nello stesso
  mese dell'anno;
- altrimenti è **opportunistica**.

Per le vendite la storia conta anche quelle dei piani 10b5-1: un piano è
un'abitudine messa per iscritto. Ma si classificano e si misurano solo le
vendite discrezionali, perché sono le sole che il punteggio usa.

Con cinque anni di Form 4 (dall'ottobre 2021) il primo anno solare completo è
il 2022, quindi si possono classificare le operazioni dal 2025 in poi.

Qui si fanno due cose. `classify` mette l'etichetta alle operazioni di
un'azienda (finisce nella scheda e fra le ragioni della voce insider, senza
punti). `run` misura il rendimento oltre il settore nelle 60 sedute dopo, per
abituali, opportunisti e non classificabili: è la prova che decide se
l'etichetta meriterà mai dei punti.
"""
from datetime import date

from . import eventstudy, rules

YEARS = 3                       # gli anni solari precedenti da guardare
SIDE = {"BUY": "buy", "SELL_D": "sell", "SELL_P": "sell"}   # il verso di un'operazione
LABELLED = ("BUY", "SELL_D")    # le operazioni che si classificano e si misurano
MIN_N = 5                       # sotto, un gruppo non ha media


def first_full_year(start):
    """Il primo anno solare interamente coperto da una storia che comincia il giorno `start`."""
    d = date.fromisoformat(start[:10])
    return d.year if (d.month, d.day) == (1, 1) else d.year + 1


def classify(tx, start):
    """{posizione in tx: "routine" | "opp"} per gli acquisti e le vendite discrezionali.

    `start` è il giorno da cui la storia dei Form 4 è completa. Un'operazione
    che non ha tre anni solari interi alle spalle dentro la storia non si
    classifica (manca dal risultato).
    """
    if not start:
        return {}
    full = first_full_year(start)
    seen = {}                      # (chi, verso) -> {(anno, mese)}
    for t in tx:
        s = SIDE.get(t["kind"])
        if s:
            y, m = int(t["date"][:4]), int(t["date"][5:7])
            seen.setdefault((t["who"], s), set()).add((y, m))
    out = {}
    for i, t in enumerate(tx):
        if t["kind"] not in LABELLED:
            continue
        y, m = int(t["date"][:4]), int(t["date"][5:7])
        if y - YEARS < full:
            continue
        ym = seen.get((t["who"], SIDE[t["kind"]]), set())
        years = range(y - YEARS, y)
        if not all(any(yy == Y for yy, _ in ym) for Y in years):
            continue               # non ha operato in tutti e tre gli anni: non si sa
        out[i] = "routine" if all((Y, m) in ym for Y in years) else "opp"
    return out


def _stats(obs):
    """[(giorno, rendimento)] -> {n, mean, se, t, pos, blocks} con l'errore a grappoli dello studio di evento."""
    n = len(obs)
    if n < MIN_N:
        return {"n": n}
    r = eventstudy.mean_se(obs)
    pos = round(sum(1 for _, v in obs if v > 0) / n * 100)
    if r is None:
        m = sum(v for _, v in obs) / n
        return {"n": n, "mean": round(m, 2), "pos": pos}
    m, se, n, G = r
    return {"n": n, "mean": round(m, 2), "se": round(se, 2), "t": round(m / se, 2) if se else None,
            "pos": pos, "blocks": G}


def _diff(a, b):
    """Opportunisti meno abituali, con l'errore standard della differenza."""
    if a.get("se") is None or b.get("se") is None:
        return None
    d = a["mean"] - b["mean"]
    se = (a["se"] ** 2 + b["se"] ** 2) ** 0.5
    return {"mean": round(d, 2), "se": round(se, 2), "t": round(d / se, 2) if se else None}


def run(results, cfg):
    """-> il blocco "habits" dell'indice.

    L'unità è la persona-mese: tutte le operazioni dello stesso insider, dello
    stesso verso, nello stesso mese, sono una prova sola (si misura la prima).
    Il rendimento è quello oltre il settore nelle 60 sedute dopo, limitato a
    ±50 punti come nel backtest.
    """
    H = cfg["rules"]["track_record_horizon_days"]
    groups = {s: {"routine": [], "opp": [], "none": []} for s in ("buy", "sell")}
    who = {s: {"routine": set(), "opp": set(), "none": set()} for s in ("buy", "sell")}
    since = None
    for d in results:
        start = d.get("_tx_start")
        if not start or not (d.get("history") or {}).get("tx_complete", True):
            continue
        tx = d.get("tx") or []
        lab = classify(tx, start)
        full = first_full_year(start)
        px = [{"d": x[0], "c": x[1]} for x in (d.get("_px_long") or []) if x[1]]
        bench = d.get("_bench_long") or []
        if not px or not bench:
            continue
        done = set()
        for i, t in enumerate(tx):
            if t["kind"] not in LABELLED or int(t["date"][:4]) - YEARS < full:
                continue
            side = SIDE[t["kind"]]
            key = (t["who"], side, t["date"][:7])
            if key in done:
                continue
            done.add(key)
            car = rules.abnormal_return(px, bench, t["date"], H)
            if car is None:
                continue                  # 60 sedute non ancora passate
            g = lab.get(i, "none")
            groups[side][g].append((t["date"], max(-eventstudy.CAP, min(eventstudy.CAP, car))))
            who[side][g].add((d["company"]["ticker"], t["who"]))
            since = t["date"] if since is None or t["date"] < since else since
    if since is None:
        return None
    out = {"years": YEARS, "horizon": H, "from": since, "min": MIN_N}
    for s in ("buy", "sell"):
        st = {g: dict(_stats(v), people=len(who[s][g])) for g, v in groups[s].items()}
        st["diff"] = _diff(st["opp"], st["routine"])
        out[s] = st
    return out
