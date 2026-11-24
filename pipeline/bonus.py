"""I bonus della voce insider, stimati dai dati invece che scritti a mano.

Fino a questa versione la voce insider dava +18 a un cluster di compratori,
+10 a un acquisto di un dirigente apicale e +8 a due compratori distinti.
Erano numeri scelti a mano. Qui si chiede ai dati quanto valgono, con tre
cautele che cambiano il risultato più di qualunque formula.

1. **Le prove si contano per campagna, non per acquisto.** Un insider che
   compra dieci volte in tre settimane non dà dieci prove: le sue dieci
   misure a 60 sedute coprono quasi lo stesso periodo di borsa. Un episodio è
   una coppia (titolo, giorno); una campagna raccoglie gli episodi dello stesso
   titolo entro 90 giorni dal primo, cioè quelli i cui orizzonti si
   sovrappongono. Ogni campagna conta una volta sola.

2. **I rendimenti estremi vengono limitati a ±50 punti.** Un +207% in 60 sedute
   è successo davvero, ma da solo deciderebbe la stima.

3. **Ogni stima si restringe verso zero quanto più è incerta.** La regola è
   quella bayesiana più semplice: se l'effetto misurato vale b con incertezza
   s, il bonus usato vale b * tau^2 / (tau^2 + s^2). Con tanti dati s è piccolo
   e il bonus resta quasi b; con una campagna sola s è enorme e il bonus va a
   zero. `tau` dice quanto ci aspettiamo che possa valere, al massimo plausibile,
   un effetto vero: 5 punti percentuali in 60 sedute. È l'unica scelta fatta a
   mano, ed è dichiarata; la pagina mostra anche che cosa darebbe con 3 e con 10.

La conversione è un punto di punteggio per ogni punto percentuale di
rendimento in più a 60 sedute, al netto del settore.

Dal 23 novembre 2026 c'è una quarta caratteristica, `beat`: la campagna è
cominciata dopo una trimestrale sopra le attese degli analisti (l'ultima
annunciata prima del primo acquisto, entro 100 giorni; dati Alpha Vantage, vedi
pipeline/alphavantage.py). Risponde alla domanda: un insider che compra dopo una
sorpresa positiva conta di più? A mano non valeva niente, quindi parte da zero.
Il dato non c'è per tutte le campagne (gli utili arrivano a rate, dodici
aziende al giorno): per questo il suo effetto si stima a parte, sulle sole
campagne in cui si sa com'era andata l'ultima trimestrale, con le altre tre
caratteristiche come controllo. Così le tre stime di prima non cambiano per il
fatto che il dato nuovo c'è o non c'è.

Che cosa non si stima qui: la penalità per la vendita di un dirigente apicale
(il backtest guarda solo gli acquisti; la prova è la curva dopo una vendita in
eventstudy.run_sell) e il flusso netto, che è una misura
continua e non un bonus.
"""
from datetime import date

CAMPAIGN_DAYS = 90       # due acquisti entro 90 giorni misurano quasi lo stesso periodo
CAP = 50.0               # rendimenti limitati a ±50 punti percentuali
TAU = 5.0                # effetto vero massimo plausibile, in punti percentuali
TAUS = (3.0, 5.0, 10.0)  # la sensibilità che la pagina mostra
POINTS_PER_PP = 1.0      # un punto di punteggio per punto percentuale
MAX_POINTS = 20          # nessun bonus stimato può superare ±20

# i bonus scritti a mano fino al 22 novembre 2026, per il confronto
HAND = {"cluster": 18, "two": 8, "senior": 10, "beat": 0}
FEATURES = ("cluster", "two", "senior", "beat")
OPTIONAL = ("beat",)     # caratteristiche che non tutte le campagne hanno: si stimano sul sottoinsieme noto
MIN_KNOWN = 0.5          # una di queste dà punti solo se il dato c'è in almeno metà delle campagne


def _d(s):
    return date.fromisoformat(s)


def episodes(rows, min_people=3):
    """Acquisti -> episodi. `rows`: {car, t, d, people, senior, beat}.

    Dentro un episodio il rendimento è lo stesso per tutti (stesso titolo, stesso
    giorno di partenza); le caratteristiche valgono se valgono per almeno uno.
    """
    by = {}
    for r in rows:
        e = by.setdefault((r["t"], r["d"]), {"t": r["t"], "d": r["d"], "car": r["car"],
                                             "people": 0, "senior": False, "buys": 0,
                                             "beat": r.get("beat"), "size": r.get("size")})
        e["people"] = max(e["people"], r.get("people") or 1)
        e["senior"] = e["senior"] or bool(r.get("senior"))
        e["buys"] += 1
    out = sorted(by.values(), key=lambda e: (e["t"], e["d"]))
    for e in out:
        e["cluster"] = e["people"] >= min_people
        e["two"] = e["people"] == 2
    return out


def campaigns(rows, min_people=3, days=CAMPAIGN_DAYS):
    """Acquisti -> campagne indipendenti."""
    out = []
    for e in episodes(rows, min_people):
        last = out[-1] if out else None
        if last and last["t"] == e["t"] and (_d(e["d"]) - _d(last["d"])).days <= days:
            last["eps"].append(e)
        else:
            out.append({"t": e["t"], "d": e["d"], "eps": [e]})
    for c in out:
        eps = c.pop("eps")
        c["car"] = round(sum(e["car"] for e in eps) / len(eps), 2)
        c["episodes"] = len(eps)
        c["buys"] = sum(e["buys"] for e in eps)
        c["cluster"] = any(e["cluster"] for e in eps)
        c["two"] = any(e["two"] for e in eps) and not c["cluster"]
        c["senior"] = any(e["senior"] for e in eps)
        c["beat"] = eps[0].get("beat")    # com'era andata l'ultima trimestrale prima del primo acquisto
        c["size"] = eps[0].get("size")    # grande, media o piccola capitalizzazione
    return out


def summary(camps):
    """Il riassunto di un gruppo di campagne, nella forma che il sito legge.

    `camp` è il numero di prove indipendenti; `ep` e `n` (episodi e acquisti)
    restano per mostrare quanto il conteggio per acquisto gonfiava le cose.
    """
    if not camps:
        return None
    xs = sorted(c["car"] for c in camps)
    k = len(xs)
    med = xs[k // 2] if k % 2 else (xs[k // 2 - 1] + xs[k // 2]) / 2
    return {"camp": k, "ep": sum(c["episodes"] for c in camps),
            "n": sum(c["buys"] for c in camps),
            "pos": sum(1 for x in xs if x > 0),
            "avg": round(sum(xs) / k, 2), "med": round(med, 2)}


# ---------------------------------------------------------------- algebra minima
def _inverse(m):
    """Inversa di una matrice piccola, Gauss-Jordan con pivot. None se singolare."""
    n = len(m)
    a = [list(map(float, row)) + [1.0 if i == j else 0.0 for j in range(n)]
         for i, row in enumerate(m)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-12:
            return None
        a[col], a[piv] = a[piv], a[col]
        p = a[col][col]
        a[col] = [v / p for v in a[col]]
        for r in range(n):
            if r != col and a[r][col]:
                f = a[r][col]
                a[r] = [v - f * w for v, w in zip(a[r], a[col])]
    return [row[n:] for row in a]


def _ols(X, y):
    """Minimi quadrati ordinari: coefficienti ed errori standard classici."""
    k = len(X[0])
    xtx = [[sum(r[i] * r[j] for r in X) for j in range(k)] for i in range(k)]
    inv = _inverse(xtx)
    if inv is None:
        return None
    xty = [sum(r[i] * v for r, v in zip(X, y)) for i in range(k)]
    beta = [sum(inv[i][j] * xty[j] for j in range(k)) for i in range(k)]
    resid = [v - sum(b * x for b, x in zip(beta, r)) for r, v in zip(X, y)]
    dof = len(y) - k
    s2 = sum(e * e for e in resid) / dof
    se = [(s2 * inv[i][i]) ** 0.5 for i in range(k)]
    return beta, se


def _points(post):
    p = round(post * POINTS_PER_PP)
    return int(max(-MAX_POINTS, min(MAX_POINTS, p)))


def _fit(camps, feats):
    """Regressione del rendimento sulle caratteristiche -> (costante, {f: (b, s)}), o None.

    Una caratteristica che nessuna campagna ha (o che hanno tutte) resta fuori.
    """
    usable = [f for f in feats if 0 < sum(1 for c in camps if c[f]) < len(camps)]
    X = [[1.0] + [1.0 if c[f] else 0.0 for f in usable] for c in camps]
    y = [max(-CAP, min(CAP, c["car"])) for c in camps]
    if not X or len(y) - len(X[0]) < 3:
        return None
    fit = _ols(X, y)
    if fit is None:
        return None
    beta, se = fit
    return beta[0], {f: (beta[i + 1], se[i + 1]) for i, f in enumerate(usable)}


def estimate(camps, tau=TAU):
    """La stima dei bonus. None se le campagne sono troppo poche.

    Una caratteristica che nessuna campagna ha (o che hanno tutte) non si può
    stimare: il suo bonus è zero, perché senza prove non si dà un premio. Le
    caratteristiche di OPTIONAL si stimano sulle sole campagne in cui il dato
    c'è, con le altre come controllo.
    """
    if not camps:
        return None
    base = [f for f in FEATURES if f not in OPTIONAL]
    main = _fit(camps, base)
    if main is None:
        return None
    const, coef = main
    for f in OPTIONAL:
        sub = [c for c in camps if c.get(f) is not None]
        extra = _fit(sub, base + [f]) if sub else None
        if extra and f in extra[1]:
            coef[f] = extra[1][f]
    feats = []
    for f in FEATURES:
        known = [c for c in camps if c.get(f) is not None]
        row = {"id": f, "n": sum(1 for c in known if c[f]), "hand": HAND[f]}
        frozen = False
        if f in OPTIONAL:
            row["known"] = len(known)
            # Con il dato su poche campagne la stima può dipendere da quali aziende
            # lo hanno (Alpha Vantage ne copre una dozzina al giorno): si mostra,
            # ma non dà punti. Il 24/11/2026 diceva -3 su 187 campagne su 1.163.
            frozen = len(known) < MIN_KNOWN * len(camps)
            row["frozen"] = frozen
        if f in coef:
            b, s = coef[f]
            shrink = tau * tau / (tau * tau + s * s)
            row.update({"raw": round(b, 2), "se": round(s, 2),
                        "shrink": round(shrink, 3), "post": round(b * shrink, 2),
                        "points": 0 if frozen else _points(b * shrink),
                        "by_tau": {str(int(t)): _points(b * t * t / (t * t + s * s))
                                   for t in TAUS}})
        else:
            row.update({"raw": None, "se": None, "shrink": 0.0, "post": 0.0,
                        "points": 0, "by_tau": {str(int(t)): 0 for t in TAUS}})
        feats.append(row)
    return {"tau": tau, "camp": len(camps), "cap": CAP, "days": CAMPAIGN_DAYS,
            "base": round(const, 2), "features": feats}


def points(est):
    """{cluster, two, senior, beat} -> punti. Senza una stima, nessun bonus."""
    if not est:
        return {f: 0 for f in FEATURES}
    return {r["id"]: r["points"] for r in est["features"]}
