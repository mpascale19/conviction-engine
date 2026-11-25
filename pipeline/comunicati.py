"""Che cosa dicono i dirigenti: il tono delle loro frasi nei comunicati.

Il terzo segnale, accanto a quello che gli insider **fanno** (Form 4) e a
quello che il management dice agli analisti nelle **call**: quello che i
dirigenti dicono in pubblico, nei comunicati stampa.

**Da dove.** Ogni comunicato importante di un'azienda americana (risultati,
acquisizioni, guidance) arriva anche alla SEC, allegato a un 8-K come
documento EX-99 (di solito EX-99.1). È la stessa frase che esce sui giornali
(«"Abbiamo chiuso un trimestre record", ha detto la CEO…»), ma presa alla
fonte: gratis, legale, con dieci anni di storia, e con la data in cui è
diventata pubblica. I siti di notizie no: i loro termini vietano di
raschiarli, molti sono a pagamento, e dai server di GitHub bloccano spesso
le richieste.

Si leggono gli 8-K con item 2.02 (risultati), 7.01 (comunicazioni al
mercato) e 8.01 (altri fatti), quelli che portano i comunicati. Per ognuno:
l'indice del deposito (quale file è l'EX-99), poi l'allegato. Due richieste,
una volta sola: poi resta in cache.

**Che cosa si tiene.** Le frasi fra virgolette attribuite a un dirigente: il
paragrafo che contiene la citazione deve nominare un ruolo (chief executive,
CEO, president, chair, chief financial, CFO, chief operating, founder…) e un
verbo di attribuzione (said, stated, commented, added…). Un paragrafo che
continua la citazione di quello prima conta anche lui. Si contano anche le
parole di tutto il comunicato, per confronto.

**Come si legge, senza modelli.** Con il dizionario di Loughran e McDonald
(2011), lo standard della finanza (`input/lm/`): parole positive, negative e
di incertezza. Una parola positiva preceduta entro tre parole da no, not,
none, neither, never o nobody non conta (la loro regola sulla negazione).
Tono netto = (positive − negative) per 100 parole.

**Solo i comunicati sui risultati fanno il segnale.** Lo scarto dal solito si
calcola soltanto sugli 8-K con item 2.02, i risultati trimestrali, confrontati
con i risultati precedenti: un annuncio di nomine o di un'acquisizione è per
natura più entusiasta, e mescolato ai risultati falserebbe il confronto (dal
25/11/2026; prima Home Depot, con risultati e cambio di vertice lo stesso
giorno, risultava "più ottimista del solito" per l'annuncio sulle nomine). Le
frasi degli altri comunicati restano nella scheda, con il loro tono ma senza
scarto.

**Il segnale è la sorpresa, non il tono.** Alcuni CEO sono sempre
entusiasti: conta quanto un comunicato è più ottimista o più cauto **del
solito per quell'azienda**. Per ogni comunicato: tono meno la media dei
comunicati precedenti (fino a otto, almeno quattro), diviso per la loro
deviazione standard. È lo scarto «z». Huang, Teoh e Zhang (The Accounting
Review, 2014) trovano che il tono anormalmente positivo dei comunicati sugli
utili è seguito da rendimenti più bassi: le parole possono servire a
gestire le attese.

Il segnale **non** entra nel punteggio: è una voce candidata nella prova
all'indietro (pipeline/predict.py), come la sorpresa sugli utili e i fondi.
Entrerebbe solo se la prova mostrasse che anticipa i rendimenti. La scheda di
ogni azienda mostra le frasi, il tono e il confronto con quello che fanno gli
insider.

Lo scarica il workflow comunicati.yml nella cache "comunicati-"; il giro
orario la legge e basta.
"""
import argparse
import html as htmlmod
import json
import os
import re
import statistics as st
import time
from datetime import date, datetime, timedelta, timezone

DIR = "cache-comunicati"
LM_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "input", "lm")
ITEMS = ("2.02", "7.01", "8.01")   # gli 8-K che portano comunicati
YEARS = 10
QUOTE_MAX = 1500                   # di una citazione si tiene al più tanto testo
QUOTES_PER_DOC = 6
MIN_WORDS = 25                     # sotto, il tono di un comunicato non si misura
BASE_MIN, BASE_MAX = 4, 8          # quanti comunicati prima servono per il "solito"
WINDOW = 120                       # un comunicato vale come segnale per 120 giorni
CARD_N = 6                         # quanti comunicati mostra la scheda
MAX_FAILS = 3                      # dopo tre tentativi falliti un deposito si lascia stare
PAUSE = 0.1                        # secondi in più fra una richiesta e l'altra, oltre a quelli di sources._get
NEGATIONS = {"no", "not", "none", "neither", "never", "nobody"}

ROLE = re.compile(r"\b(chief\s+executive|ceo|president|chair(?:man|woman|person)?|chief\s+financial|cfo|"
                  r"chief\s+operating|coo|founder|managing\s+director)\b", re.I)
VERB = re.compile(r"\b(said|says|stated|states|commented|comments|added|adds|noted|notes|continued|"
                  r"explained|remarked|concluded|according\s+to)\b", re.I)
CONT = re.compile(r"(?:(?:he|she|they|(?:mr|ms|mrs|dr)\.?\s*[A-Z][\w'-]+|[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?)\s+"
                  r"(?:said|added|continued|concluded|noted)|(?:said|added|continued|concluded|noted)\s+"
                  r"(?:(?:mr|ms|mrs|dr)\.?\s*)?[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?)", re.I)
QUOTE = re.compile(r"[“\"]([^“”\"]{30,4000})[”\"]")


# --------------------------------------------------------------- il dizionario

_LEX = None


def lexicon(folder=LM_DIR):
    """{"pos", "neg", "unc"}: i tre insiemi di parole, in minuscolo."""
    global _LEX
    if _LEX is None or folder != LM_DIR:
        out = {}
        for k, fn in (("pos", "positive.txt"), ("neg", "negative.txt"), ("unc", "uncertainty.txt")):
            with open(os.path.join(folder, fn), encoding="utf-8") as fh:
                out[k] = {w.strip() for w in fh if w.strip()}
        if folder != LM_DIR:
            return out
        _LEX = out
    return _LEX


def words(text):
    return re.findall(r"[a-z]+", (text or "").lower())


def tone_counts(text, lex=None):
    """{"w", "p", "n", "u"}: parole, positive, negative, di incertezza.

    Una positiva con una negazione nelle tre parole prima non conta.
    """
    lex = lex or lexicon()
    ws = words(text)
    p = n = u = 0
    for i, w in enumerate(ws):
        if w in lex["pos"] and not NEGATIONS.intersection(ws[max(0, i - 3):i]):
            p += 1
        if w in lex["neg"]:
            n += 1
        if w in lex["unc"]:
            u += 1
    return {"w": len(ws), "p": p, "n": n, "u": u}


def net(c):
    """Tono netto per 100 parole, o None se le parole sono troppo poche."""
    if not c or (c.get("w") or 0) < MIN_WORDS:
        return None
    return 100.0 * (c["p"] - c["n"]) / c["w"]


# --------------------------------------------------------------- il testo

_BLOCK = re.compile(r"<\s*(/p|/div|br\s*/?|/tr|/li|/h[1-6]|/td|p\b[^>]*|div\b[^>]*)\s*>", re.I)


def paragraphs(doc):
    """HTML (o testo) di un allegato -> paragrafi di testo semplice."""
    doc = doc or ""
    if re.search(r"<\s*(p|div|br|span|font|table|html|body)\b", doc, re.I):
        # HTML: gli a capo del sorgente non contano, contano i paragrafi
        doc = re.sub(r"(?is)<(script|style|head)\b.*?</\1\s*>", " ", doc)
        doc = re.sub(r"\s+", " ", doc)
        doc = _BLOCK.sub("\n", doc)
        doc = re.sub(r"(?s)<[^>]+>", " ", doc)
        parts = doc.split("\n")
    else:
        # testo semplice (i depositi più vecchi): le righe vanno a capo a 80
        # caratteri, i paragrafi sono separati da una riga vuota
        parts = re.split(r"\n\s*\n", doc)
    out = []
    for p in (htmlmod.unescape(x).replace("\xa0", " ") for x in parts):
        p = re.sub(r"\s+", " ", p).strip()
        if p:
            out.append(p)
    return out


def quotes(paras):
    """Le citazioni dei dirigenti: [{"t": testo, "a": attribuzione}].

    Un paragrafo conta se fuori dalle virgolette nomina un ruolo e un verbo di
    attribuzione; oppure se continua la citazione del paragrafo prima (si apre
    con le virgolette, e fuori dalle virgolette c'è al più un verbo).
    """
    out, prev = [], False
    for p in paras:
        found = [m.group(1).strip() for m in QUOTE.finditer(p)]
        if not found:
            prev = False
            continue
        outside = QUOTE.sub(" ", p)
        outside = re.sub(r"\s+", " ", outside).strip(" ,.;:")
        own = bool(ROLE.search(outside) and VERB.search(outside))
        # continua la citazione di prima se fuori dalle virgolette non c'è niente,
        # o solo un nome e un verbo ("continued Doe", "she added"): "said an
        # analyst at the bank" è un'altra persona.
        cont = prev and p.lstrip()[:1] in "“\"" and (not outside or bool(CONT.fullmatch(outside)))
        if own or cont:
            for q in found:
                out.append({"t": q[:QUOTE_MAX], "a": outside[:140]})
            prev = True
        else:
            prev = False
        if len(out) >= QUOTES_PER_DOC:
            break
    return out[:QUOTES_PER_DOC]


def exhibit(index_html):
    """Dall'indice di un deposito (…-index.htm): il nome del primo allegato EX-99, o None."""
    best = None
    for row in re.findall(r"(?is)<tr\b.*?</tr>", index_html or ""):
        cells = [re.sub(r"(?s)<[^>]+>", " ", c) for c in re.findall(r"(?is)<td\b[^>]*>(.*?)</td>", row)]
        cells = [re.sub(r"\s+", " ", htmlmod.unescape(c)).strip() for c in cells]
        href = re.search(r'(?i)href="([^"]+)"', row)
        if not href or not cells:
            continue
        kind = next((c.upper() for c in cells if re.fullmatch(r"(?i)ex-?99(\.\d+)?", c)), None)
        name = href.group(1).rsplit("/", 1)[-1]
        if kind and name.lower().endswith((".htm", ".html", ".txt")):
            rank = 0 if kind in ("EX-99.1", "EX-99") else 1
            if best is None or rank < best[0]:
                best = (rank, name)
    return best[1] if best else None


def read_release(doc_html):
    """Allegato -> i conti del deposito: citazioni e parole, delle frasi e di tutto il comunicato."""
    paras = paragraphs(doc_html)
    qs = quotes(paras)
    qc = tone_counts(" ".join(q["t"] for q in qs))
    rc = tone_counts(" ".join(paras))
    return {"q": qs, "qc": qc, "rc": rc}


# --------------------------------------------------------------- la raccolta

def _read(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def pending_rows(cols, since):
    """Le righe 8-K con un item da comunicato, dal giorno `since`: [(data, accessione, cik, items)]."""
    out = []
    forms, dates = cols.get("form") or [], cols.get("filingDate") or []
    accs, items = cols.get("accessionNumber") or [], cols.get("items") or []
    ciks = cols.get("_cik") or [""] * len(forms)
    for i, f in enumerate(forms):
        if not str(f).startswith("8-K"):
            continue
        d = dates[i] if i < len(dates) else ""
        if not d or d < since:
            continue
        codes = [c.strip() for c in str(items[i] if i < len(items) else "").split(",") if c.strip()]
        if not any(c in ITEMS for c in codes):
            continue
        out.append((d, accs[i], str(ciks[i] if i < len(ciks) else ""), codes))
    return out


def doc_url(cik, acc, name):
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{name}"


def update(folder, companies, until, today=None, get=None, table=None, max_new=None):
    """Legge i comunicati nuovi, dal più recente, fino all'orario `until`.

    `companies`: {ticker: cik}. `get(url) -> testo` e `table(cik, since) ->
    colonne` si possono sostituire nelle prove. Ogni azienda ha il suo file
    `<cartella>/rel/<TICKER>.json`: {accessione: conti}, che si salva man mano.
    """
    from . import sources
    if get is None:
        # Un po' più piano del solito (circa 4 richieste al secondo invece di 6):
        # questo giro gira insieme a quello orario e a mercato.yml.
        def get(u):
            time.sleep(PAUSE)
            return sources._get(u).text
    table = table or (lambda c, s: sources.filings_table(c, s)[0])
    today = today or date.today().isoformat()
    since = (date.fromisoformat(today) - timedelta(days=int(365.25 * YEARS))).isoformat()
    log, todo, stores = [], [], {}
    for t, cik in sorted(companies.items()):
        if time.time() > until:
            log.append("tempo finito mentre leggevo gli elenchi dei depositi")
            break
        path = os.path.join(folder, "rel", f"{t}.json")
        stores[t] = (path, _read(path, {}) or {})
        try:
            rows = pending_rows(table(cik, since), since)
        except Exception as e:
            log.append(f"{t}: elenco dei depositi non letto ({e})")
            continue
        for d, acc, c2, codes in rows:
            have = stores[t][1].get(acc)
            if have and ("q" in have or have.get("none") or (have.get("fail") or 0) >= MAX_FAILS):
                continue
            todo.append((d, t, acc, c2 or cik, codes))
    todo.sort(reverse=True)              # prima i più recenti: il segnale di oggi arriva subito
    done = errs = 0
    dirty = set()
    for d, t, acc, cik, codes in todo:
        if time.time() > until or (max_new is not None and done >= max_new):
            break
        path, store = stores[t]
        base = doc_url(cik, acc, "")
        try:
            name = exhibit(get(base + f"{acc}-index.htm"))
            if not name:
                store[acc] = {"d": d, "i": codes, "none": "senza EX-99"}
            else:
                got = read_release(get(base + name))
                store[acc] = {"d": d, "i": codes, "doc": name, "cik": str(cik), **got}
            done += 1
        except Exception as e:
            errs += 1
            old = store.get(acc) or {}
            store[acc] = {"d": d, "i": codes, "fail": (old.get("fail") or 0) + 1, "err": str(e)[:160]}
        dirty.add(t)
        if (done + errs) % 50 == 0:
            for x in dirty:
                _write(*stores[x])
            dirty = set()
    for x in dirty:
        _write(*stores[x])
    left = len(todo) - done
    log.append(f"comunicati letti: {done}, errori: {errs}, ancora da leggere: {max(left, 0)}")
    st_path = os.path.join(folder, "stato.json")
    state = _read(st_path, {}) or {}
    state.update({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "pending": max(left, 0), "companies": len(stores)})
    _write(st_path, state)
    return log


# --------------------------------------------------------------- la lettura

def series(store):
    """{accessione: conti} -> i comunicati in ordine di data, con tono e scarto dal solito.

    Ogni elemento: {d, acc, doc, cik, earn, q, qn, qz, rn, rz}. `earn`: è un
    comunicato sui risultati (item 2.02). `qn` e `rn`: tono netto delle frasi
    dei dirigenti e di tutto il comunicato; `qz` e `rz`: di quante deviazioni
    standard il tono è sopra o sotto la media dei comunicati sui risultati
    precedenti dell'azienda. None se non è un comunicato sui risultati, o se
    prima ce ne sono meno di quattro.
    """
    rows = []
    for acc, v in (store or {}).items():
        if not isinstance(v, dict) or "q" not in v:
            continue
        rows.append({"d": v["d"], "acc": acc, "doc": v.get("doc"), "cik": v.get("cik"),
                     "earn": "2.02" in (v.get("i") or []),
                     "q": v.get("q") or [], "qn": net(v.get("qc")), "rn": net(v.get("rc"))})
    rows.sort(key=lambda r: (r["d"], r["acc"]))
    for k in ("q", "r"):
        prev = []
        for r in rows:
            x = r[k + "n"]
            r[k + "z"] = None
            if x is not None and r["earn"]:
                base = prev[-BASE_MAX:]
                if len(base) >= BASE_MIN:
                    sd = st.pstdev(base)
                    if sd > 0:
                        r[k + "z"] = round((x - st.mean(base)) / sd, 2)
                prev.append(x)
            if x is not None:
                r[k + "n"] = round(x, 2)
    return rows


def load(folder, ticker):
    return series(_read(os.path.join(folder, "rel", f"{ticker}.json"), {}) or {})


def as_of(rows, day, kind="q", window=WINDOW):
    """Lo scarto dal solito dell'ultimo comunicato misurabile già pubblico quel giorno (entro `window`)."""
    lo = (date.fromisoformat(day) - timedelta(days=window)).isoformat()
    for r in reversed(rows or []):
        if r["d"] > day:
            continue
        if r["d"] < lo:
            return None
        if r[kind + "z"] is not None:
            return r[kind + "z"]
    return None


def mood(z):
    if z is None:
        return None
    return "up" if z >= 1 else "down" if z <= -1 else "usual"


def card(rows, today, insider=None):
    """Il riassunto per la scheda dell'azienda, o None se non c'è nessun comunicato con frasi.

    `insider`: {"buyers", "sellers", "net_musd"} degli ultimi 90 giorni, per
    mettere vicino quello che i dirigenti dicono e quello che fanno.
    """
    with_q = [r for r in rows or [] if r["q"]]
    if not with_q:
        return None
    last = []
    for r in reversed(with_q[-CARD_N:]):
        q = r["q"][0]
        last.append({"d": r["d"], "n": r["qn"], "z": r["qz"], "rz": r["rz"], "earn": r["earn"],
                     "q": q["t"][:320] + ("…" if len(q["t"]) > 320 else ""), "a": q["a"],
                     "url": doc_url(r["cik"], r["acc"], r["doc"]) if r.get("cik") and r.get("doc") else None})
    lo = (date.fromisoformat(today) - timedelta(days=WINDOW)).isoformat()
    cur = next((r for r in reversed(with_q) if r["d"] >= lo and r["qz"] is not None), None)
    out = {"n": len(with_q), "from": with_q[0]["d"], "last": last,
           "now": {"d": cur["d"], "z": cur["qz"], "mood": mood(cur["qz"])} if cur else None}
    if cur and insider is not None:
        b, s = insider.get("buyers") or 0, insider.get("sellers") or 0
        acts = "buy" if b and (insider.get("net_musd") or 0) > 0 else "sell" if s else "none"
        m = mood(cur["qz"])
        clash = (m == "up" and acts == "sell") or (m == "down" and acts == "buy")
        agree = (m == "up" and acts == "buy") or (m == "down" and acts == "sell")
        out["say_do"] = {"mood": m, "acts": acts, "clash": clash, "agree": agree}
    return out


def companies_from_site(site="site"):
    """{ticker: cik} delle aziende del sito, dalle schede (site/data/<T>.json)."""
    out = {}
    idx = _read(os.path.join(site, "index.json"), {}) or {}
    for r in idx.get("companies") or []:
        d = _read(os.path.join(site, "data", f"{r['ticker']}.json"), {}) or {}
        cik = (d.get("company") or {}).get("cik")
        if cik:
            out[r["ticker"]] = cik
    return out


def status(folder=DIR):
    """Lo stato della raccolta per l'indice del sito."""
    st_ = _read(os.path.join(folder, "stato.json"), {}) or {}
    rel = os.path.join(folder, "rel")
    n_co = n_doc = n_q = 0
    first = None
    for fn in sorted(os.listdir(rel)) if os.path.isdir(rel) else []:
        store = _read(os.path.join(rel, fn), {}) or {}
        got = [v for v in store.values() if isinstance(v, dict) and "q" in v]
        if got:
            n_co += 1
        n_doc += len(got)
        n_q += sum(1 for v in got if v.get("q"))
        ds = [v["d"] for v in got if v.get("q")]
        if ds:
            first = min([first] + ds) if first else min(ds)
    if not n_doc and not st_:
        return None
    return {"companies": n_co, "releases": n_doc, "with_quotes": n_q, "from": first,
            "pending": st_.get("pending"), "updated": st_.get("updated")}


def main():
    from . import sources
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=50)
    ap.add_argument("--folder", default=DIR)
    a = ap.parse_args()
    start = time.time()
    cfg = _read("config.json", {}) or {}
    sources.ARCHIVE_DIR = os.path.join(a.folder, "submissions")
    cos = companies_from_site()
    # i CIK precedenti (ExxonMobil, BlackRock): la storia sta sotto di loro
    for t, olds in (cfg.get("cik_predecessors") or {}).items():
        if t in cos:
            sources.PREDECESSORS[str(cos[t]).zfill(10)] = [str(o).zfill(10) for o in olds]
    print(f"aziende: {len(cos)}")
    log = update(a.folder, cos, start + 60 * a.minutes)
    print("\n".join(log))
    print(json.dumps(status(a.folder), ensure_ascii=False))
    print(f"durata: {(time.time() - start) / 60:.1f} minuti")


if __name__ == "__main__":
    main()
