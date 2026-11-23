"""Le regole: classificare le operazioni, trovare i cluster, pesare lo storico.

Qui non c'è nessun modello linguistico. Sono le regole scritte a mano che
rendono il punteggio verificabile e ripetibile.
"""
from datetime import date, timedelta


def L(it, en):
    """Un testo nelle due lingue. Il sito sceglie quale mostrare."""
    return {"it": it, "en": en}

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
    "BUY": L("Acquisto sul mercato: pesa nello score.",
             "Open-market purchase: it counts in the score."),
    "SELL_D": L("Vendita discrezionale, senza piano: pesa nello score.",
                "Discretionary sale, no plan: it counts in the score."),
    "SELL_P": L("Vendita programmata da un piano 10b5-1: non informativa.",
                "Sale scheduled under a 10b5-1 plan: not informative."),
    "EXER": L("Esercizio di opzioni senza vendita contestuale: non informativa.",
              "Option exercise with no sale alongside: not informative."),
    "TAX": L("Azioni trattenute per le imposte su un'assegnazione: non informativa.",
             "Shares withheld for the taxes on a vesting: not informative."),
    "AWARD": L("Assegnazione di azioni: non è una scelta di acquisto.",
               "Share award: not a decision to buy."),
    "OTHER": L("Operazione non classificata: esclusa per prudenza.",
               "Transaction not classified: excluded to be safe."),
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


JUMP_FLAG = -25.0     # un giorno sotto il settore di più di 25 punti: da controllare a mano
NEUTRAL_MIN = -5.0    # uno scorporo noto si corregge solo se quel giorno il salto c'è davvero


def _rel_day(px, i, bmap):
    """Rendimento del titolo meno quello del settore nella seduta i, in frazione."""
    r = px[i]["c"] / px[i - 1]["c"] - 1
    b0, b1 = bmap.get(px[i - 1]["d"]), bmap.get(px[i]["d"])
    rb = (b1 / b0 - 1) if b0 and b1 else 0.0
    return r - rb, rb


def neutralize(px, bench, dates, days=7):
    """Corregge i salti degli scorpori che la chiusura di Yahoo non corregge.

    La chiusura di Yahoo è corretta per i frazionamenti, non per gli scorpori
    né per i dividendi straordinari: il giorno in cui gli azionisti di Pfizer
    ricevono le azioni di Viatris, la serie di Pfizer mostra un crollo che
    nessuno ha subìto. Con cinque anni di storia le aziende con uno scorporo
    erano state lasciate fuori dall'universo; con dieci non si può, e le date
    note stanno in config.json ("corporate_actions").

    Per ogni data si cerca, entro `days` giorni, la seduta peggiore rispetto al
    settore; se è davvero un salto (sotto NEUTRAL_MIN punti), tutti i prezzi
    prima di quella seduta si moltiplicano per un fattore che rende il suo
    rendimento uguale a quello del settore. È come se quel giorno il titolo
    avesse fatto quanto il settore: si perde un giorno di informazione vera,
    ma non si inventa un crollo. -> (prezzi corretti, [{d, rel}] dei giorni corretti)
    """
    if not dates or len(px) < 2:
        return px, []
    out = [dict(p) for p in px]
    bmap = {b["d"]: b["c"] for b in bench or []}
    fixed = []
    for dt in dates:
        d0 = date.fromisoformat(dt[:10])
        best = None
        for i in range(1, len(out)):
            if abs((date.fromisoformat(out[i]["d"][:10]) - d0).days) > days:
                continue
            if not (out[i]["c"] and out[i - 1]["c"]):
                continue
            rel, rb = _rel_day(out, i, bmap)
            if best is None or rel < best[1]:
                best = (i, rel, rb)
        if best is None or best[1] * 100 > NEUTRAL_MIN:
            continue
        i, rel, rb = best
        f = out[i]["c"] / (out[i - 1]["c"] * (1 + rb))
        for j in range(i):
            out[j]["c"] = round(out[j]["c"] * f, 4)
        fixed.append({"d": out[i]["d"], "rel": round(rel * 100, 1)})
    return out, fixed


def jumps(px, bench, limit=JUMP_FLAG):
    """Le sedute in cui il titolo ha fatto peggio del settore di più di `limit` punti.

    Non si correggono: un crollo vero (una trimestrale disastrosa) è
    informazione. Si elencano perché qualcuno guardi se sono scorpori non
    ancora in config.json.
    """
    bmap = {b["d"]: b["c"] for b in bench or []}
    out = []
    for i in range(1, len(px)):
        if px[i]["c"] and px[i - 1]["c"]:
            rel, _ = _rel_day(px, i, bmap)
            if rel * 100 <= limit:
                out.append({"d": px[i]["d"], "rel": round(rel * 100, 1)})
    return out


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


START_GAP_DAYS = 7   # quanto può distare la prima seduta utile dal giorno dell'operazione


def _ret(series, day, horizon):
    """Il rendimento sulle `horizon` sedute dopo `day`, o None.

    Niente scorciatoie sull'orizzonte: se le sedute disponibili non bastano,
    questa osservazione non e' ancora matura e non deve entrare in nessun
    conto. Prima qui si prendeva l'ultimo prezzo disponibile, quindi un
    acquisto di due settimane fa entrava nelle statistiche a 60 giorni
    misurato su 14 sedute, come se fossero 60.
    """
    after = [p for p in series if p["d"] >= day]
    if len(after) <= horizon:
        return None
    # La prima seduta dopo `day` deve essere davvero quella del giorno (o del
    # primo giorno di Borsa dopo un fine settimana o una festa). Se `day` sta
    # prima dell'inizio della serie, la "prima seduta" è di mesi o anni dopo:
    # così un acquisto del 2018 veniva misurato sui prezzi del 2021.
    if (date.fromisoformat(after[0]["d"]) - date.fromisoformat(day[:10])).days > START_GAP_DAYS:
        return None
    start = after[0]["c"]
    return (after[horizon]["c"] / start - 1) if start else None


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
                  "camp": len(person_campaigns(b)),
                  "pos": sum(1 for x in b if x["car"] > 0),
                  "avg": round(sum(x["car"] for x in b) / len(b), 2),
                  "mult": track_multiplier(person_campaigns(b), prior)}
            for who, b in out.items()}


def person_campaigns(buys, days=90):
    """Gli acquisti passati di una persona, raggruppati in campagne.

    Dieci acquisti in tre settimane non sono dieci prove della bravura di
    qualcuno: le loro misure a 60 sedute coprono quasi lo stesso periodo di
    borsa. Ogni campagna (acquisti entro `days` giorni dal primo) diventa un
    rendimento solo, la loro media, e il moltiplicatore conta le campagne.
    Prima contava gli acquisti, e il prior di prudenza diventava irrilevante
    per chi compra spesso.
    """
    out, first = [], None
    for x in sorted(buys, key=lambda x: x["date"]):
        if first and (_d(x["date"]) - _d(first)).days <= days:
            out[-1].append(x["car"])
        else:
            out.append([x["car"]])
            first = x["date"]
    return [round(sum(g) / len(g), 2) for g in out]
