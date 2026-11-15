"""Il punteggio: una formula leggibile, non un modello.

Ogni sotto-punteggio restituisce un valore 0-100, le ragioni in chiaro e la
copertura dei dati. Se una fonte manca (per esempio le stime Bloomberg), il
sotto-punteggio vale None e il suo peso viene ridistribuito sugli altri: lo
score esce lo stesso, ma con confidenza più bassa e con la lista di cosa
mancava.
"""
from datetime import date, timedelta

from .rules import is_senior

NAMES = {
    "insider": "Insider", "guid": "Guidance e tono", "market": "Mercato",
    "sector": "Settore", "news": "News", "price": "Contesto di prezzo",
    "social": "Social",
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
    if cluster:
        v += 18
        why.append(f"Cluster di {len({t['who'] for t in cluster})} acquirenti distinti "
                   f"entro {cfg['rules']['cluster_days']} giorni.")
    elif people == 2:
        v += 8
        why.append("Due acquirenti distinti, ma sotto la soglia del cluster.")
    elif people == 1:
        why.append("Un solo acquirente nel periodo.")
    else:
        why.append("Nessun acquisto discrezionale negli ultimi 90 giorni.")

    if any(is_senior(t, titles) for t in buys):
        v += 10
        why.append("Fra i compratori c'è un dirigente apicale (CEO, CFO o presidente).")
    if any(is_senior(t, titles) for t in sells):
        v -= 10
        why.append("Un dirigente apicale ha venduto in modo discrezionale.")

    v += _clamp(net_musd / cap * 12, -12, 12)
    why.append(f"Flusso netto pesato sullo storico: {net_musd:+.2f} M$.")

    excluded = [t for t in win if t["weight"] == 0]
    if excluded:
        why.append(f"{len(excluded)} operazioni escluse perché non informative "
                   "(piani 10b5-1, ritenute fiscali, esercizi e assegnazioni).")
    mults = {w: tr["mult"] for w, tr in tracks.items() if w in {t["who"] for t in buys}}
    if mults:
        why.append("Moltiplicatori storici applicati: " +
                   ", ".join(f"{w} x{m}" for w, m in sorted(mults.items())) + ".")
    return round(_clamp(v)), why, ("Alta" if win else "Bassa")


def price_score(prices, today=None):
    """Dove sta il prezzo rispetto agli ultimi mesi."""
    if len(prices) < 60:
        return None, ["Storico dei prezzi troppo corto."], "Bassa"
    last = prices[-1]["c"]
    six = prices[-126:] if len(prices) >= 126 else prices
    hi = max(p["c"] for p in six)
    lo = min(p["c"] for p in six)
    three = prices[-63:] if len(prices) >= 63 else prices
    mom = (last / three[0]["c"] - 1) * 100

    v, why = 50.0, []
    dd = (last / hi - 1) * 100
    v += _clamp(-dd * 0.5, 0, 15)
    why.append(f"Il prezzo è {dd:+.1f}% rispetto al massimo di sei mesi ({hi:.2f}).")
    rec = (last / lo - 1) * 100
    why.append(f"Ed è {rec:+.1f}% sopra il minimo di sei mesi ({lo:.2f}).")
    v += _clamp(mom * 0.3, -12, 12)
    why.append(f"Momentum a tre mesi: {mom:+.1f}%.")
    return round(_clamp(v)), why, "Alta"


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
    if s > 80: return "Convinzione rialzista forte"
    if s >= 65: return "Convinzione rialzista moderata"
    if s >= 55: return "Leggermente rialzista"
    if s > 45: return "Nessuna convinzione chiara"
    if s >= 35: return "Leggermente ribassista"
    if s >= 20: return "Convinzione ribassista moderata"
    return "Convinzione ribassista forte"
