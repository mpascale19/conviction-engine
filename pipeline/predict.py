"""La prova all'indietro delle voci che si possono ricostruire senza inventare.

Il punteggio intero non si può provare nel passato, perché guidance e short
interest percentuale sui simili non esistono giorno per giorno. Quattro voci
invece sì, con i soli dati che la pipeline ha già:

- **prezzo**: dalle chiusure;
- **settore**: dai Form 4 di tutte le aziende dell'universo;
- **mercato**, nella parte che guarda la storia del titolo: dallo storico dello
  short interest (il confronto con i simili no: servirebbero le azioni in
  circolazione di ogni giorno passato);
- **fatti rilevanti**: dagli 8-K, ma solo per le date coperte dal file dei
  depositi della SEC (vedi `covered`).

Per ogni data e per ogni universo si calcola la correlazione di rango
(Spearman) fra il valore della voce in quel giorno e il rendimento delle 60
sedute successive, fra le aziende dello stesso universo. Il confronto è nella
stessa data e nello stesso universo, quindi l'andamento del mercato si annulla
e non serve un riferimento.

Le date vicine si sovrappongono (i loro 60 giorni coincidono quasi del tutto):
l'incertezza si misura su blocchi di 60 sedute, che sono le prove davvero
indipendenti. Con due anni e quaranta aziende i blocchi sono una decina, e
l'incertezza resta intorno a ±0,06: si vedrebbe solo un segnale forte. Il sito
lo scrive.

Il calcolo è deterministico: stessi dati, stesso risultato.
"""
import statistics as st
from datetime import date, timedelta

from . import rules, score

H = 60          # sedute dopo cui si misura
START = 126     # sei mesi di storia prima della prima data: il prezzo ne ha bisogno
STEP = 5        # una data ogni cinque sedute basta, le vicine dicono la stessa cosa
EVENT_DAYS = 180
MIN_CO = 5      # sotto le cinque aziende una correlazione non dice niente

SIGNALS = ("price", "sector", "market", "events")


def _ranks(xs):
    """Ranghi con i pari merito alla media: molte voci hanno valori ripetuti."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(a, b):
    """Correlazione di rango. None se non c'è variazione o le aziende sono poche."""
    if len(a) < MIN_CO or len(set(a)) < 2 or len(set(b)) < 2:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = st.mean(ra), st.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** .5
    return num / den if den else None


def summarize(points, horizon=H):
    """[(indice della seduta, correlazione)] di più universi -> il riassunto.

    `points` è un dizionario universo -> lista: i blocchi si formano dentro ogni
    universo, perché due universi diversi nella stessa data sono prove distinte.
    """
    every, blocks = [], []
    for xs in points.values():
        every += [x for _, x in xs]
        by = {}
        for i, x in xs:
            by.setdefault(i // horizon, []).append(x)
        blocks += [st.mean(v) for v in by.values() if len(v) >= 3]
    if len(blocks) < 3:
        return None
    m = st.mean(blocks)
    se = st.stdev(blocks) / len(blocks) ** .5
    return {"ic": round(st.mean(every), 3), "se": round(se, 3),
            "t": round(m / se, 2) if se else None,
            "blocks": len(blocks), "dates": len(every)}


def _summary_at(tx, day, cfg):
    start = (date.fromisoformat(day) - timedelta(days=90)).isoformat()
    win = [t for t in tx if start <= t["date"] <= day]
    buys = [t for t in win if t["kind"] == "BUY"]
    sells = [t for t in win if t["kind"] == "SELL_D"]
    return {"buyers": len({t["who"] for t in buys}),
            "cluster": bool(rules.find_cluster(win, cfg["rules"]["cluster_days"],
                                               cfg["rules"]["cluster_min_people"])),
            "net_musd": (sum(t["value"] for t in buys) - sum(t["value"] for t in sells)) / 1e6}


def _signals_on(group, pos, day, cfg):
    """I quattro valori di ogni azienda del gruppo, com'erano in quel giorno.

    `pos[ticker]` è la posizione di quel giorno nella serie di prezzi di
    ciascuna azienda: le serie non hanno tutte la stessa lunghezza.
    """
    out = {k: {} for k in SIGNALS}
    summ = {}
    for d in group:
        t = d["company"]["ticker"]
        px = d["_px"][:pos[t] + 1]
        out["price"][t] = score.price_score(px)[0]
        summ[t] = dict(_summary_at(d.get("tx") or [], day, cfg), ticker=t)
        si = [r for r in d["_si"] if r["date"] <= day]
        if len(si) >= 8:
            out["market"][t] = score.market_score(si)[0]
        hist, covered = d.get("events_hist"), d.get("events_from")
        lo = (date.fromisoformat(day) - timedelta(days=EVENT_DAYS)).isoformat()
        if hist is not None and covered and covered <= lo:
            out["events"][t] = score.events_score(
                [{"date": e[0], "items": e[1]} for e in hist if lo < e[0] <= day], EVENT_DAYS)[0]
    if len(summ) >= 4:
        for t, me in summ.items():
            out["sector"][t] = score.sector_score(me, list(summ.values()), cfg)[0]
    return out


def run(results, cfg, step=STEP):
    groups = {}
    for d in results:
        g = d.get("group")
        key = g.get("it") if isinstance(g, dict) else g
        # la serie lunga se c'è (in memoria durante il giro), altrimenti quella della scheda
        ser = d.get("_px_long") or (d.get("price") or {}).get("series") or []
        if not key or len(ser) < START + H + 1:
            continue
        d = dict(d, _px=[{"d": x[0], "c": x[1]} for x in ser if x[1]],
                 _si=[{"date": r[0], "si": r[1], "dtc": r[2]} for r in (d.get("si_series") or [])])
        groups.setdefault(key, []).append(d)

    points = {k: {} for k in SIGNALS}
    first = last = None
    for key, group in groups.items():
        if len(group) < MIN_CO:
            continue
        # Ogni data usa le aziende che in quel giorno hanno sei mesi di storia
        # alle spalle e 60 sedute davanti. Un titolo quotato da meno tempo
        # entra quando può, senza accorciare la storia di tutti gli altri.
        where = {d["company"]["ticker"]: {p["d"]: k for k, p in enumerate(d["_px"])} for d in group}
        days = sorted(set().union(*[set(w) for w in where.values()]))
        for j in range(0, len(days), step):
            day = days[j]
            pos = {t: w[day] for t, w in where.items() if day in w}
            members = [d for d in group if d["company"]["ticker"] in pos
                       and pos[d["company"]["ticker"]] >= START
                       and pos[d["company"]["ticker"]] + H < len(d["_px"])]
            if len(members) < MIN_CO:
                continue
            fwd = {}
            for d in members:
                t = d["company"]["ticker"]
                fwd[t] = d["_px"][pos[t] + H]["c"] / d["_px"][pos[t]]["c"] - 1
            sig = _signals_on(members, pos, day, cfg)
            for k in SIGNALS:
                ts = [t for t in sig[k] if sig[k][t] is not None and t in fwd]
                x = spearman([sig[k][t] for t in ts], [fwd[t] for t in ts])
                if x is not None:
                    points[k].setdefault(key, []).append((j, x))
            first = day if first is None or day < first else first
            last = day if last is None or day > last else last

    rows = []
    for k in SIGNALS:
        s = summarize(points[k])
        rows.append(dict({"id": k}, **(s or {"ic": None})))
    if all(r["ic"] is None for r in rows):
        return None
    return {"horizon": H, "from": first, "to": last, "step": step, "rows": rows}
