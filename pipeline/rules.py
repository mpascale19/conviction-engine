"""Le regole: classificare le operazioni, trovare i cluster, pesare lo storico.

Qui non c'è nessun modello linguistico. Sono le regole scritte a mano che
rendono il punteggio verificabile e ripetibile.
"""
from datetime import date, timedelta

# Codici SEC che contano e quanto pesano.
#   P = acquisto sul mercato        S = vendita
#   M = esercizio di opzioni        F = azioni trattenute per le imposte
#   A = assegnazione
KIND_OF = {
    "P": "BUY",
    "S_plan": "SELL_P",
    "S": "SELL_D",
    "M": "EXER",
    "F": "TAX",
    "A": "AWARD",
}
WEIGHT = {"BUY": 1.0, "SELL_D": 1.0, "SELL_P": 0.0, "EXER": 0.0, "TAX": 0.0,
          "AWARD": 0.0, "OTHER": 0.0}
READ = {
    "BUY": "Acquisto sul mercato: pesa nello score.",
    "SELL_D": "Vendita discrezionale, senza piano: pesa nello score.",
    "SELL_P": "Vendita programmata da un piano 10b5-1: non informativa.",
    "EXER": "Esercizio di opzioni senza vendita contestuale: non informativa.",
    "TAX": "Azioni trattenute per le imposte su un'assegnazione: non informativa.",
    "AWARD": "Assegnazione di azioni: non è una scelta di acquisto.",
    "OTHER": "Operazione non classificata: esclusa per prudenza.",
}


def classify(tx):
    code = (tx.get("code") or "").upper()
    if code == "P":
        return "BUY"
    if code == "S":
        return "SELL_P" if tx.get("plan") else "SELL_D"
    return KIND_OF.get(code, "OTHER") if code in KIND_OF else "OTHER"


def enrich(rows):
    """Aggiunge tipo, peso e lettura a ogni operazione."""
    for t in rows:
        t["kind"] = classify(t)
        t["weight"] = WEIGHT[t["kind"]]
        t["read"] = READ[t["kind"]]
    return rows


def is_senior(tx, titles):
    t = (tx.get("role") or "").lower()
    return any(k in t for k in titles)


def _d(s):
    return date.fromisoformat(s[:10])


def find_cluster(rows, days=30, min_people=3):
    """Il primo gruppo di acquisti di almeno `min_people` persone diverse
    entro `days` giorni. Restituisce la lista delle operazioni o []."""
    buys = sorted([t for t in rows if t["kind"] == "BUY"], key=lambda t: t["date"])
    for i, start in enumerate(buys):
        window = [b for b in buys[i:]
                  if 0 <= (_d(b["date"]) - _d(start["date"])).days <= days]
        if len({b["who"] for b in window}) >= min_people:
            return window
    return []


def abnormal_return(prices, bench, day, horizon=60):
    """Rendimento del titolo meno quello del settore, `horizon` giorni dopo
    `day`. None se non ci sono abbastanza prezzi."""
    a = _ret(prices, day, horizon)
    b = _ret(bench, day, horizon)
    if a is None or b is None:
        return None
    return round((a - b) * 100, 2)


def _ret(series, day, horizon):
    after = [p for p in series if p["d"] >= day]
    if len(after) < 2:
        return None
    start = after[0]["c"]
    end = after[min(horizon, len(after) - 1)]["c"]
    return (end / start - 1) if start else None


def track_multiplier(past_returns, prior=3):
    """1 + (somma dei rendimenti / (numero + prior)) / 10, limitato a 0,5-1,5.
    Con pochi acquisti passati resta vicino a 1: è la prudenza del campione."""
    n = len(past_returns)
    if not n:
        return 1.0
    m = 1 + (sum(past_returns) / (n + prior)) / 10
    return round(max(0.5, min(1.5, m)), 3)


def track_records(rows, prices, bench, horizon=60, prior=3, window_start=None):
    """Per ogni insider: i suoi acquisti passati e come sono andati."""
    out = {}
    for t in rows:
        if t["kind"] != "BUY":
            continue
        if window_start and t["date"] >= window_start:
            continue                       # gli acquisti di oggi non si giudicano da soli
        r = abnormal_return(prices, bench, t["date"], horizon)
        if r is None:
            continue
        out.setdefault(t["who"], []).append({"date": t["date"], "car": r})
    return {who: {"buys": b,
                  "n": len(b),
                  "pos": sum(1 for x in b if x["car"] > 0),
                  "avg": round(sum(x["car"] for x in b) / len(b), 2),
                  "mult": track_multiplier([x["car"] for x in b], prior)}
            for who, b in out.items()}
