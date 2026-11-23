"""Chi supera il 5% per contare: lo Schedule 13D.

Chi arriva a possedere più del 5% di una società quotata deve dirlo alla SEC.
Se è un investitore passivo (un fondo indice, una banca) deposita lo Schedule
13G, breve. Se invece vuole influire sulla società, o non può dirsi passivo,
deposita lo **Schedule 13D**, entro dieci giorni (cinque dal febbraio 2024), e
nel punto 4 scrive che cosa intende fare: chiedere posti nel consiglio, una
vendita, un taglio dei costi, un riacquisto di azioni.

Sono gli investitori "attivisti" (Icahn, Elliott, Trian, Starboard…), ma non
solo: un 13D lo depositano anche un fondatore che sale sopra il 5%, una società
che prepara un'offerta, un azionista di controllo. Per questo il sito dice
"13D", non "attivista", e scrive chi l'ha depositato.

Nello studio classico (Brav, Jiang, Partnoy e Thomas, Journal of Finance,
2008) il titolo fa circa il 7% oltre il mercato nei giorni intorno al deposito.
La pagina Backtest rifà la prova sulle duecento aziende; nel punteggio il 13D
non sposta punti, compare fra i fatti dell'azienda.

Da dove si legge, senza richieste in più dove si può:

- il JSON dei depositi di ogni azienda (quello dei Form 4 e degli 8-K) elenca
  anche i 13D che altri hanno depositato su di lei: tipo, data, accessione;
- per sapere **chi** l'ha depositato si legge l'intestazione del deposito
  (`<accessione>.hdr.sgml`, pochi kilobyte), una volta sola: resta in cache;
- la **quota** (la percentuale della classe) si cerca nel documento
  principale; se non si trova, la scheda non la dice.

Dal 18 dicembre 2024 i moduli si chiamano "SCHEDULE 13D" e sono in XML; prima
"SC 13D", in testo o HTML. Si leggono tutti e due.
"""
import json
import os
import re
from datetime import date

FIRST = ("SC 13D", "SCHEDULE 13D")
AMEND = ("SC 13D/A", "SCHEDULE 13D/A")
NEW_PER_RUN = 40          # intestazioni nuove da leggere per giro, in tutto
DOC_LIMIT = 600_000       # del documento principale si guarda al più l'inizio: la quota sta in copertina


def from_table(cols, since):
    """Le righe 13D della tabella dei depositi (sources.filings_table), dal giorno `since`."""
    out = []
    forms, dates = cols.get("form") or [], cols.get("filingDate") or []
    accs, docs = cols.get("accessionNumber") or [], cols.get("primaryDocument") or []
    ciks = cols.get("_cik") or [""] * len(forms)
    for i, f in enumerate(forms):
        f = str(f).strip().upper()
        if f not in FIRST + AMEND:
            continue
        d = dates[i] if i < len(dates) else ""
        if not d or d < since:
            continue
        out.append({"date": d, "form": f, "first": f in FIRST,
                    "acc": accs[i] if i < len(accs) else "",
                    "doc": docs[i] if i < len(docs) else "", "cik": ciks[i] if i < len(ciks) else ""})
    return out


def parse_header(text):
    """I nomi di chi deposita, dall'intestazione SGML o da quella in testo."""
    names = []
    for block in re.findall(r"<FILED-BY>(.*?)(?:</FILED-BY>|<SUBJECT-COMPANY>|$)", text, re.S | re.I):
        names += re.findall(r"<CONFORMED-NAME>\s*([^\r\n<]+)", block, re.I)
    if not names:
        for block in re.split(r"(?=SUBJECT COMPANY:|FILED BY:)", text):
            if block.strip().upper().startswith("FILED BY:"):
                names += re.findall(r"COMPANY CONFORMED NAME:\s*([^\r\n]+)", block)
    seen, out = set(), []
    for n in names:
        n = n.strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def parse_percent(text):
    """La percentuale della classe dichiarata in copertina, o None.

    Nell'XML è un elemento (percentOfClass); nei vecchi moduli è la riga 13,
    "Percent of class represented by amount in row (11)". Se in copertina ci
    sono più dichiaranti, si prende il valore più alto (di solito il gruppo).
    """
    vals = [float(x) for x in re.findall(r"<(?:\w+:)?percentOfClass>\s*([\d.]+)\s*<", text, re.I)]
    if not vals:
        flat = re.sub(r"<[^>]+>", " ", text)
        flat = re.sub(r"&nbsp;|&#160;|\s+", " ", flat)
        for m in re.finditer(r"percent\s+of\s+class\s+represented\s+by\s+amount\s+in\s+row\s*\(?\s*11\s*\)?"
                             r"[^0-9]{0,80}?(\d{1,3}(?:\.\d+)?)\s*%", flat, re.I):
            vals.append(float(m.group(1)))
    vals = [v for v in vals if 0 < v <= 100]
    return max(vals) if vals else None


def _folder(cik, acc):
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/"


def details(row, cache_dir, budget):
    """{who, pct} di un 13D: dalla cache, o due richieste se c'è ancora posto nel giro."""
    path = os.path.join(cache_dir, row["acc"] + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        pass
    if budget["left"] <= 0:
        return None
    from .sources import _get
    budget["left"] -= 1
    base = _folder(row["cik"], row["acc"])
    got = {"who": [], "pct": None}
    try:
        got["who"] = parse_header(_get(base + row["acc"] + ".hdr.sgml").text)
    except Exception as e:
        got["err"] = str(e)[:200]
    if row.get("doc"):
        try:
            doc = re.sub(r"^xsl[^/]+/", "", row["doc"])     # l'XML grezzo, non la versione impaginata
            got["pct"] = parse_percent(_get(base + doc).text[:DOC_LIMIT])
        except Exception as e:
            got.setdefault("err", str(e)[:200])
    if "err" in got and not got["who"] and "404" not in got["err"]:
        return None                        # la SEC non ha risposto: si riprova al prossimo giro
    os.makedirs(cache_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(got, f)
    return got


def collect(cols, cik, since, cache_dir, budget):
    """I 13D di un'azienda, ciascuno con chi e quanto: [{date, form, first, who, pct, acc}].

    Le correzioni (13D/A) si elencano ma non si leggono: basta sapere che ci
    sono, e chi le firma è quasi sempre chi ha depositato il primo.
    """
    out = []
    for r in from_table(cols, since):
        r = dict(r, cik=r["cik"] or cik)
        det = details(r, cache_dir, budget) if r["first"] else None
        out.append({"date": r["date"], "form": r["form"], "first": r["first"], "acc": r["acc"],
                    "who": (det or {}).get("who") or [], "pct": (det or {}).get("pct")})
    return out


def summary(events, today, days=365):
    """Per la scheda: i 13D dell'ultimo anno, il più recente primo."""
    lo = date.fromordinal(date.fromisoformat(today).toordinal() - days).isoformat()
    first = [e for e in events if e["first"] and e["date"] >= lo]
    amend = [e for e in events if not e["first"] and e["date"] >= lo]
    return {"first": sorted(first, key=lambda e: e["date"], reverse=True),
            "amend": len(amend), "last_amend": max((e["date"] for e in amend), default=None)}


def run(results):
    """Lo studio di evento dei 13D iniziali: il blocco "activists" dell'indice, o None.

    Il giorno zero è il giorno del deposito, che è anche il giorno in cui la
    notizia è pubblica. Gli stessi conti della curva "dopo un acquisto"
    (pipeline/eventstudy.py): rendimento cumulato oltre il settore, errore a
    grappoli di 87 giorni, valori limitati a ±50 punti.
    """
    from . import eventstudy as es
    evs, recent = [], []
    for d in results:
        px = [{"d": x[0], "c": x[1]} for x in (d.get("_px_long") or []) if x[1]]
        bench = d.get("_bench_long") or []
        seen = set()
        for e in d.get("_13d") or []:
            if not e["first"]:
                continue
            recent.append({"t": d["company"]["ticker"], "d": e["date"], "who": e["who"][:2], "pct": e["pct"]})
            if e["date"] in seen or not px or not bench:
                continue                   # due dichiaranti lo stesso giorno: un evento solo
            seen.add(e["date"])
            p = es.path(px, bench, e["date"])
            if p is not None:
                evs.append({"d": e["date"], "path": p})
    if not recent:
        return None
    segs = (("before", -es.PRE, -1), ("around", -1, 1), ("to60", 1, 60), ("to120", 1, 120))
    seg = {}
    for sid, a, b in segs:
        ia, ib = a + es.PRE, b + es.PRE
        vals = [(e["d"], e["path"][ib] - e["path"][ia]) for e in evs if len(e["path"]) > ib]
        r = es.mean_se(vals) if len(vals) >= 2 else None
        if r is None:
            seg[sid] = {"n": len(vals)}
            continue
        m, se, n, G = r
        seg[sid] = {"mean": round(m, 2), "se": round(se, 2), "t": round(m / se, 2) if se else None,
                    "n": n, "blocks": G, "pos": round(sum(1 for _, v in vals if v > 0) / n * 100)}
    recent.sort(key=lambda r: r["d"], reverse=True)
    return {"events": len(evs), "filings": len(recent), "min": es.MIN_EVENTS,
            "from": min(r["d"] for r in recent), "seg": seg, "recent": recent[:15]}
