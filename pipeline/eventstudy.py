"""Che cosa succede dopo un acquisto, seduta per seduta.

Il backtest guarda un numero solo per campagna: il rendimento oltre il settore
dopo 60 sedute. Qui si guarda tutto il percorso, dai 20 giorni di borsa prima
del primo acquisto ai 120 dopo. È lo "studio di evento" classico della finanza:
si allineano tutte le campagne sul loro giorno zero e si fa la media.

Come si calcola:

- **L'unità è la campagna**, come nel backtest (pipeline/bonus.py): gli
  acquisti di un'azienda entro 90 giorni dal primo sono una prova sola. Il
  giorno zero è la prima seduta dal giorno del primo acquisto (al più 7 giorni
  dopo, altrimenti la campagna non entra).
- **Rendimento oltre il settore, cumulato**: ogni seduta, il rendimento del
  titolo meno quello del suo ETF di settore; la curva è la loro somma a partire
  da 20 sedute prima, dove vale zero. Se il settore non ha il prezzo di quella
  seduta, la campagna si ferma lì.
- **Ogni campagna conta finché ha dati**: una campagna di due mesi fa entra
  nella curva fino a +40 e poi esce. Per questo la pagina dice, per ogni
  seduta, su quante campagne è fatta la media. I valori di una campagna sono
  limitati a ±50 punti, come nel backtest, perché un solo caso estremo non
  sposti tutta la curva.
- **L'incertezza tiene conto delle campagne vicine nel tempo.** Campagne di
  aziende diverse negli stessi mesi vivono la stessa borsa e non sono prove
  indipendenti. L'errore standard è quello "a grappoli" (cluster-robust),
  con i grappoli fatti di blocchi di 87 giorni di calendario come nella prova
  all'indietro (pipeline/predict.py). La banda è ±1,96 errori standard.

Il giorno zero è il giorno dell'operazione, non quello in cui la si viene a
sapere: il Form 4 arriva entro due giorni lavorativi. Chi legge i Form 4 può
comprare al più presto dal giorno +2, e la tabella dei tratti lo separa.
"""
from datetime import date

from . import alphavantage, bonus, rules

PRE = 20                 # sedute prima del giorno zero
POST = 120               # sedute dopo
BLOCK_DAYS = 87          # grappoli dell'errore standard: come la prova all'indietro
CAP = bonus.CAP          # ±50 punti per campagna
MIN_EVENTS = 20          # sotto questo numero di campagne un punto della curva non si disegna
PUBLIC_DAY = 2           # il Form 4 è pubblico al più tardi due giorni lavorativi dopo

# I gruppi che la pagina confronta: lo stesso significato del backtest.
GROUPS = {
    "all": lambda c: True,
    "cluster": lambda c: c["cluster"],
    "solo": lambda c: not c["cluster"],
    "senior": lambda c: c["senior"],
    "other": lambda c: not c["senior"],
    "beat": lambda c: c.get("beat") is True,
    "nobeat": lambda c: c.get("beat") is False,
}

# I tratti della tabella: (id, da, a). "da" è la seduta il cui valore si toglie.
SEGMENTS = (("before", -PRE, -1),          # i 20 giorni prima dell'acquisto
            ("first", -1, PUBLIC_DAY - 1),  # giorno zero e +1: il Form 4 non è ancora pubblico
            ("to60", PUBLIC_DAY - 1, 60),   # da quando il Form 4 è pubblico a +60
            ("to120", PUBLIC_DAY - 1, 120))


def campaigns(results, cfg):
    """Le campagne di acquisto di tutte le aziende, con le caratteristiche del backtest."""
    nd = cfg["rules"]["cluster_days"]
    titles = cfg["rules"]["senior_titles"]
    rows = []
    for d in results:
        tx = [t for t in (d.get("tx") or []) if t["kind"] == "BUY"]
        earn = d.get("_earn_long") or []
        for t in tx:
            day = date.fromisoformat(t["date"])
            near = {x["who"] for x in tx if abs((date.fromisoformat(x["date"]) - day).days) <= nd}
            ls = alphavantage.last_surprise(earn, t["date"]) if earn else None
            rows.append({"car": 0.0, "t": d["company"]["ticker"], "d": t["date"],
                         "people": len(near), "senior": rules.is_senior(t, titles),
                         "beat": (ls["sp"] > 0) if ls else None})
    return bonus.campaigns(rows, cfg["rules"]["cluster_min_people"])


def path(px, bench, day, pre=PRE, post=POST):
    """Il rendimento cumulato oltre il settore, in punti percentuali, dalla seduta -pre in poi.

    `px` e `bench`: [{d, c}] in ordine di data. -> lista di valori per le sedute
    -pre, -pre+1, ... finché ci sono dati (al massimo fino a +post), o None se la
    campagna non si può misurare (prezzi che non arrivano a -pre, giorno zero
    troppo lontano dal giorno dell'acquisto).
    """
    i0 = next((i for i, p in enumerate(px) if p["d"] >= day), None)
    if i0 is None or i0 - pre < 0:
        return None
    if (date.fromisoformat(px[i0]["d"]) - date.fromisoformat(day[:10])).days > rules.START_GAP_DAYS:
        return None
    b = {p["d"]: p["c"] for p in bench}
    out, cum = [0.0], 0.0
    for i in range(i0 - pre + 1, min(i0 + post, len(px) - 1) + 1):
        p0, p1 = px[i - 1], px[i]
        b0, b1 = b.get(p0["d"]), b.get(p1["d"])
        if not (p0["c"] and p1["c"] and b0 and b1):
            break                         # un buco nel settore: la campagna si ferma qui
        cum += (p1["c"] / p0["c"] - 1) - (b1 / b0 - 1)
        out.append(max(-CAP, min(CAP, cum * 100)))
    return out


def _block(day):
    return date.fromisoformat(day).toordinal() // BLOCK_DAYS


def mean_se(vals):
    """[(giorno della campagna, valore)] -> (media, errore standard a grappoli, n, grappoli)."""
    n = len(vals)
    if n < 2:
        return None
    m = sum(v for _, v in vals) / n
    by = {}
    for day, v in vals:
        g = _block(day)
        by[g] = by.get(g, 0.0) + (v - m)
    G = len(by)
    if G < 2:
        return None
    # varianza a grappoli della media, con la correzione per pochi grappoli G/(G-1)
    var = sum(s * s for s in by.values()) / (n * n) * G / (G - 1)
    return m, var ** 0.5, n, G


def _curve(evs):
    """Media e banda per ogni seduta da -PRE a +POST."""
    mean, lo, hi, cnt = [], [], [], []
    for k in range(PRE + POST + 1):
        vals = [(e["d"], e["path"][k]) for e in evs if len(e["path"]) > k]
        r = mean_se(vals) if len(vals) >= MIN_EVENTS else None
        if r is None:
            mean.append(None), lo.append(None), hi.append(None), cnt.append(len(vals))
            continue
        m, se, n, _ = r
        mean.append(round(m, 2)), lo.append(round(m - 1.96 * se, 2)), hi.append(round(m + 1.96 * se, 2))
        cnt.append(n)
    return {"mean": mean, "lo": lo, "hi": hi, "n": cnt}


def _segments(evs):
    """I tratti della tabella, con la loro incertezza."""
    out = {}
    for sid, a, b in SEGMENTS:
        ia, ib = a + PRE, b + PRE
        vals = [(e["d"], e["path"][ib] - e["path"][ia]) for e in evs if len(e["path"]) > ib]
        r = mean_se(vals)
        if r is None:
            out[sid] = {"n": len(vals)}
            continue
        m, se, n, G = r
        out[sid] = {"mean": round(m, 2), "se": round(se, 2), "t": round(m / se, 2) if se else None,
                    "n": n, "blocks": G, "pos": round(sum(1 for _, v in vals if v > 0) / n * 100)}
    return out


def run(results, cfg):
    """-> il blocco "event" dell'indice, o None se le campagne misurabili sono troppo poche."""
    by_t = {d["company"]["ticker"]: d for d in results}
    evs = []
    for c in campaigns(results, cfg):
        d = by_t.get(c["t"])
        px = [{"d": x[0], "c": x[1]} for x in (d.get("_px_long") or []) if x[1]]
        bench = d.get("_bench_long") or []
        p = path(px, bench, c["d"]) if px and bench else None
        if p is not None:
            evs.append(dict(c, path=p))
    if len(evs) < MIN_EVENTS:
        return None
    groups = {}
    for gid, f in GROUPS.items():
        sub = [e for e in evs if f(e)]
        groups[gid] = {"camp": len(sub), "curve": _curve(sub), "seg": _segments(sub)}
    return {"pre": PRE, "post": POST, "public_day": PUBLIC_DAY, "min": MIN_EVENTS,
            "block_days": BLOCK_DAYS, "camp": len(evs),
            "from": min(e["d"] for e in evs), "to": max(e["d"] for e in evs),
            "groups": groups}
