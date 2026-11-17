"""Il punteggio: una formula leggibile, non un modello.

Ogni sotto-punteggio restituisce un valore 0-100, le ragioni in chiaro e la
copertura dei dati. Se una fonte manca (per esempio le stime Bloomberg), il
sotto-punteggio vale None e il suo peso viene ridistribuito sugli altri: lo
score esce lo stesso, ma con confidenza più bassa e con la lista di cosa
mancava.
"""
from datetime import date, timedelta

from .rules import L, is_senior

NAMES = {
    "insider": L("Insider", "Insiders"),
    "guid": L("Guidance e tono", "Guidance and tone"),
    "market": L("Mercato", "Market"),
    "sector": L("Settore", "Sector"),
    "news": L("News", "News"),
    "price": L("Contesto di prezzo", "Price context"),
    "social": L("Social", "Social"),
}


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def insider_score(rows, cluster, tracks, cfg, today=None):
    """Acquisti e vendite discrezionali degli ultimi 90 giorni."""
    today = today or date.today()
    start = (today - timedelta(days=90)).isoformat()
    win = [t for t in rows if t["date"] >= start]
    buys = [t for t in win if t["kind"] == "BUY"]
    sells = [t for t in win if t["kind"] == "SELL_D"]
    titles = cfg["rules"]["senior_titles"]

    weighted_buy = sum(t["value"] * tracks.get(t["who"], {}).get("mult", 1.0) for t in buys)
    sell_val = sum(t["value"] for t in sells)
    net_musd = (weighted_buy - sell_val) / 1e6
    cap = cfg["rules"]["net_flow_cap_musd"]

    v, why = 50.0, []
    people = len({t["who"] for t in buys})
    nd = cfg["rules"]["cluster_days"]
    if cluster:
        n = len({t["who"] for t in cluster})
        v += 18
        why.append(L(f"Cluster di {n} acquirenti distinti entro {nd} giorni.",
                     f"Cluster of {n} separate buyers within {nd} days."))
    elif people == 2:
        v += 8
        why.append(L("Due acquirenti distinti, ma sotto la soglia del cluster.",
                     "Two separate buyers, but below the cluster threshold."))
    elif people == 1:
        why.append(L("Un solo acquirente nel periodo.", "A single buyer in the period."))
    else:
        why.append(L("Nessun acquisto discrezionale negli ultimi 90 giorni.",
                     "No discretionary purchase in the last 90 days."))

    if any(is_senior(t, titles) for t in buys):
        v += 10
        why.append(L("Fra i compratori c'è un dirigente apicale (CEO, CFO o presidente).",
                     "One of the buyers is a top executive (CEO, CFO or president)."))
    if any(is_senior(t, titles) for t in sells):
        v -= 10
        why.append(L("Un dirigente apicale ha venduto in modo discrezionale.",
                     "A top executive sold at their own discretion."))

    v += _clamp(net_musd / cap * 12, -12, 12)
    why.append(L(f"Flusso netto pesato sullo storico: {net_musd:+.2f} M$.",
                 f"Net flow weighted by track record: {net_musd:+.2f} M$."))

    excluded = [t for t in win if t["weight"] == 0]
    if excluded:
        why.append(L(f"{len(excluded)} operazioni escluse perché non informative "
                     "(piani 10b5-1, ritenute fiscali, esercizi e assegnazioni).",
                     f"{len(excluded)} transactions excluded as uninformative "
                     "(10b5-1 plans, tax withholding, exercises and awards)."))
    mults = {w: tr["mult"] for w, tr in tracks.items() if w in {t["who"] for t in buys}}
    if mults:
        lst = ", ".join(f"{w} x{m}" for w, m in sorted(mults.items()))
        why.append(L("Moltiplicatori storici applicati: " + lst + ".",
                     "Track-record multipliers applied: " + lst + "."))
    return round(_clamp(v)), why, ("Alta" if win else "Bassa")


def price_score(prices, today=None):
    """Dove sta il prezzo rispetto agli ultimi mesi."""
    if len(prices) < 60:
        return None, [L("Storico dei prezzi troppo corto.",
                        "Price history too short.")], "Bassa"
    last = prices[-1]["c"]
    six = prices[-126:] if len(prices) >= 126 else prices
    hi = max(p["c"] for p in six)
    lo = min(p["c"] for p in six)
    three = prices[-63:] if len(prices) >= 63 else prices
    mom = (last / three[0]["c"] - 1) * 100

    v, why = 50.0, []
    dd = (last / hi - 1) * 100
    v += _clamp(-dd * 0.5, 0, 15)
    why.append(L(f"Il prezzo è {dd:+.1f}% rispetto al massimo di sei mesi ({hi:.2f}).",
                 f"The price is {dd:+.1f}% against the six-month high ({hi:.2f})."))
    rec = (last / lo - 1) * 100
    why.append(L(f"Ed è {rec:+.1f}% sopra il minimo di sei mesi ({lo:.2f}).",
                 f"And {rec:+.1f}% above the six-month low ({lo:.2f})."))
    v += _clamp(mom * 0.3, -12, 12)
    why.append(L(f"Momentum a tre mesi: {mom:+.1f}%.",
                 f"Three-month momentum: {mom:+.1f}%."))
    return round(_clamp(v)), why, "Alta"


def sector_score(me, universe, cfg):
    """Il titolo è isolato o si muove con il settore?

    `me` e `universe` sono i riassunti {ticker, net_musd, buyers, cluster}
    già calcolati per ogni azienda dell'universo. Vale di più quando solo
    questo titolo ha acquisti, di meno quando comprano tutti.
    """
    others = [u for u in universe if u["ticker"] != me["ticker"]]
    if len(others) < 3:
        return None, [L("Servono almeno quattro aziende nell'universo.",
                        "At least four companies are needed in the universe.")], "Assente"
    with_buy = [u for u in others if u["buyers"] > 0]
    clusters = [u for u in others if u["cluster"]]
    share = len(with_buy) / len(others)
    nets = sorted(u["net_musd"] for u in others)
    mid = nets[len(nets) // 2] if len(nets) % 2 else (nets[len(nets) // 2 - 1] + nets[len(nets) // 2]) / 2

    v, why = 50.0, []
    nb, no, nc = len(with_buy), len(others), len(clusters)
    why.append(L(f"Nel settore {nb} aziende su {no} hanno acquisti degli insider, "
                 f"e {nc} hanno un cluster.",
                 f"In the sector {nb} companies out of {no} have insider purchases, "
                 f"and {nc} have a cluster."))
    why.append(L(f"Flusso netto mediano dei concorrenti: {mid:+.2f} M$; "
                 f"{me['ticker']}: {me['net_musd']:+.2f} M$.",
                 f"Median net flow among peers: {mid:+.2f} M$; "
                 f"{me['ticker']}: {me['net_musd']:+.2f} M$."))
    if me["buyers"] > 0:
        if share <= 0.25:
            v += 20
            why.append(L("Pochi altri comprano: il segnale è specifico di questa azienda.",
                         "Few others are buying: the signal is specific to this company."))
        elif share >= 0.5:
            v += 5
            why.append(L("Comprano anche molti concorrenti: è un movimento di settore.",
                         "Many peers are buying too: this is a sector-wide move."))
        else:
            v += 12
            why.append(L("Qualche concorrente compra, ma la maggioranza no.",
                         "A few peers are buying, but most are not."))
        if me["cluster"] and not clusters:
            v += 8
            why.append(L("È l'unica con un cluster di acquirenti.",
                         "It is the only one with a cluster of buyers."))
    else:
        if share >= 0.4:
            v -= 12
            why.append(L("I concorrenti comprano e questa no.",
                         "Peers are buying and this one is not."))
        else:
            why.append(L("Nessuno compra, qui come altrove: il settore non dice niente.",
                         "Nobody is buying, here or elsewhere: the sector says nothing."))
    if me["net_musd"] > mid:
        v += 6
        why.append(L("Il flusso netto è comunque migliore della mediana del settore.",
                     "The net flow is still better than the sector median."))
    else:
        v -= 6
        why.append(L("Il flusso netto è peggiore della mediana del settore.",
                     "The net flow is worse than the sector median."))
    return round(_clamp(v)), why, "Media"


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def market_score(rows, today=None, shares=None):
    """Short interest: quanto il mercato scommette contro il titolo.

    L'export non da' la percentuale sul flottante, quindi ogni misura è presa
    contro la storia del titolo stesso. Così due aziende di dimensioni diverse
    restano confrontabili, e un numero grande non conta per il fatto di essere
    grande. Short interest che scende = ribassisti che escono = punto a favore.
    """
    if len(rows) < 8:
        return None, [L("Storico dello short interest troppo corto.",
                        "Short-interest history too short.")], "Assente"
    last, why, v = rows[-1], [], 50.0
    year = rows[-24:] if len(rows) >= 24 else rows
    med = _median([r["si"] for r in year])
    dev = (last["si"] / med - 1) * 100 if med else 0.0
    v -= _clamp(dev * 0.5, -18, 18)
    why.append(L(f"Short interest a {last['si']/1e6:.1f} milioni di azioni, "
                 f"{dev:+.1f}% rispetto alla mediana dell'ultimo anno "
                 f"({med/1e6:.1f} milioni).",
                 f"Short interest at {last['si']/1e6:.1f} million shares, "
                 f"{dev:+.1f}% against the median of the last year "
                 f"({med/1e6:.1f} million)."))

    if shares:
        pct = last["si"] / shares * 100
        why.append(L(f"Sono il {pct:.2f}% delle azioni in circolazione "
                     f"({shares/1e6:.0f} milioni, fonte SEC).",
                     f"That is {pct:.2f}% of shares outstanding "
                     f"({shares/1e6:.0f} million, source SEC)."))

    back = rows[-7] if len(rows) >= 7 else rows[0]
    mom = (last["si"] / back["si"] - 1) * 100 if back["si"] else 0.0
    v -= _clamp(mom * 0.35, -10, 10)
    why.append(L(f"Negli ultimi tre mesi è {'salito' if mom > 0 else 'sceso'} "
                 f"del {abs(mom):.1f}% (dal {back['date']}).",
                 f"Over the last three months it has "
                 f"{'risen' if mom > 0 else 'fallen'} by {abs(mom):.1f}% "
                 f"(since {back['date']})."))

    dtcs = [r["dtc"] for r in year if r["dtc"]]
    if last["dtc"] and dtcs:
        dmed = _median(dtcs)
        high = last["dtc"] > dmed * 1.25
        if high:
            v += 4 if mom < 0 else -4
        why.append(L(f"Giorni per ricoprire: {last['dtc']:.2f} "
                     f"(mediana dell'anno {dmed:.2f})."
                     + (" Chi è short fatica a uscire." if high else ""),
                     f"Days to cover: {last['dtc']:.2f} "
                     f"(median of the year {dmed:.2f})."
                     + (" Shorts would struggle to get out." if high else "")))

    peak = max(year, key=lambda r: r["si"])
    if peak["si"] > last["si"] * 1.05:
        why.append(L(f"Massimo dell'anno {peak['si']/1e6:.1f} milioni il {peak['date']}: "
                     f"da allora {((last['si']/peak['si'])-1)*100:+.1f}%.",
                     f"Peak of the year {peak['si']/1e6:.1f} million on {peak['date']}: "
                     f"{((last['si']/peak['si'])-1)*100:+.1f}% since then."))

    cov = "Alta" if len(rows) >= 24 else "Media"
    why.append(L(f"{len(rows)} rilevazioni, l'ultima del {last['date']}.",
                 f"{len(rows)} readings, the last one from {last['date']}."))
    return round(_clamp(v)), why, cov


def market_peers(sub, me_pct, peer_pcts):
    """Aggiunge al sotto-punteggio Mercato il confronto con i simili.

    Lo short interest in percentuale delle azioni in circolazione è l'unica
    misura che si può mettere a fianco fra aziende di dimensioni diverse.
    Essere shortati molto più della mediana del settore è un segnale contro;
    molto meno, un segnale a favore.
    """
    peers = [p for p in peer_pcts if isinstance(p, (int, float))]
    if sub.get("v") is None or me_pct is None or len(peers) < 3:
        return sub
    med = _median(peers)
    if not med:
        return sub
    ratio = me_pct / med
    sub["v"] = round(_clamp(sub["v"] - _clamp((ratio - 1) * 12, -8, 8)))
    sub["why"] = list(sub["why"]) + [L(
        f"Nel settore la mediana è {med:.2f}% delle azioni: "
        f"{me_pct:.2f}% vale {ratio:.1f} volte "
        f"({len(peers)} aziende confrontate).",
        f"The sector median is {med:.2f}% of shares: "
        f"{me_pct:.2f}% is {ratio:.1f} times that "
        f"({len(peers)} companies compared).")]
    return sub


def combine(subs, weights):
    """Media pesata sui soli sotto-punteggi disponibili."""
    avail = {k: s for k, s in subs.items() if s["v"] is not None}
    if not avail:                       # nessuna fonte: niente punteggio inventato
        return None, "Assente", list(weights), 0
    tot_w = sum(weights[k] for k in avail)
    score = sum(subs[k]["v"] * weights[k] for k in avail) / tot_w
    missing = [k for k in weights if k not in avail]
    covered = tot_w / sum(weights.values())
    if covered >= 0.85 and all(subs[k]["coverage"] != "Bassa" for k in avail):
        conf = "Alta"
    elif covered >= 0.6:
        conf = "Media"
    else:
        conf = "Bassa"
    return round(score), conf, missing, round(covered * 100)


def verdict(s):
    if s > 80: return L("Convinzione rialzista forte", "Strong bullish conviction")
    if s >= 65: return L("Convinzione rialzista moderata", "Moderate bullish conviction")
    if s >= 55: return L("Leggermente rialzista", "Mildly bullish")
    if s > 45: return L("Nessuna convinzione chiara", "No clear conviction")
    if s >= 35: return L("Leggermente ribassista", "Mildly bearish")
    if s >= 20: return L("Convinzione ribassista moderata", "Moderate bearish conviction")
    return L("Convinzione ribassista forte", "Strong bearish conviction")
