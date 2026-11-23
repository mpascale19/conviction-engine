"""Gli insider di tutto il mercato, non solo delle duecento aziende.

Il resto della pipeline scarica i Form 4 un'azienda alla volta. Per guardare
tutte le società americane (circa seimila con un Form 4 in un anno) quella
strada non regge: sarebbero più di mille documenti al giorno, e anni di
storia. La SEC però pubblica due cose che la rendono possibile.

1. **I data set trimestrali dei Form 3, 4 e 5** ("Insider Transactions Data
   Sets"): un file zip per trimestre di deposito, dal 2006, con tutte le
   operazioni già in tabella (circa 10 MB l'uno). Si scaricano una volta sola.
2. **L'indice giornaliero di EDGAR** (daily-index, master.AAAAMMGG.idx):
   l'elenco di tutto quello che è stato depositato in un giorno, pubblicato la
   sera stessa. Serve per il trimestre in corso, che nei data set arriva solo
   settimane dopo la fine del trimestre: per ogni Form 4 del giorno si legge
   il documento, uno alla volta, al ritmo che la SEC chiede (al massimo 10
   richieste al secondo; qui una ogni 0,15 secondi).

Il radar quindi è di **ieri sera**, non dell'ultima ora: l'indice di un giorno
esce dopo le 22 di New York. Le duecento aziende della pagina principale
restano aggiornate ogni ora.

Che cosa se ne tiene, nella cartella cache-mercato (cache di GitHub Actions,
non nel repository):

- `insider/<trimestre>.json`: per ogni mese, quanti insider (amministratori e
  dirigenti, una persona per azienda) hanno comprato sul mercato e quanti
  hanno venduto, che serve al termometro di tutto il mercato; e gli acquisti
  sul mercato dei trimestri recenti, che servono al radar;
- `giorni/<AAAA-MM-GG>.json`: lo stesso per un giorno di depositi letti
  dall'indice giornaliero, finché il data set del suo trimestre non esce;
- `spy.json`: le chiusure dell'S&P 500 (SPY) da trent'anni, per la prova del
  termometro su tutto il periodo dei data set;
- `13f/…`: i grandi fondi, vedi pipeline/fondi.py.

Lo lancia il workflow mercato.yml; il giro orario del sito (update.yml) legge
la cartella e ne ricava il radar (`radar`) e il termometro di mercato.

    python -m pipeline.mercato              # un giro, con il tetto di tempo
    python -m pipeline.mercato --minutes 5  # un giro corto
"""
import argparse
import csv
import io
import json
import os
import re
import tempfile
import time
import zipfile
from datetime import date, datetime, timedelta, timezone

DIR = "cache-mercato"
LIST_345 = ("https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets",
            "https://www.sec.gov/dera/data/form-345")
FIRST_YEAR = 2006         # il primo data set
KEEP_BUYS_DAYS = 200      # gli acquisti dei data set si tengono solo se così recenti (il radar ne guarda 90)
DAY_MINUTES = 30          # quanto di un giro può andare ai documenti del giorno per giorno
WINDOW = 90               # giorni del radar
CLUSTER_DAYS, CLUSTER_MIN = 30, 3
MIN_VALUE = 50_000        # sotto questo totale (amministratori e dirigenti) un'azienda non entra nel radar
MIN_PRICE = 1.0           # le penny stock fanno rumore: prezzo di acquisto almeno 1 dollaro
SENIOR = ("chief executive", "ceo", "chief financial", "cfo", "president")
MONTHS = {m: i + 1 for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                                          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}
TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,6}$")


# ----------------------------------------------------------------- formati
def sec_date(s):
    """"15-MAR-2010" (data set) o "2010-03-15" -> "2010-03-15"; None se illeggibile."""
    s = (s or "").strip()
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})", s)
    if m and m.group(2).upper() in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(2).upper()]:02d}-{int(m.group(1)):02d}"
    if re.match(r"^\d{8}$", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return None


def parse_listing(html, suffix):
    """Gli zip elencati in una pagina della SEC: [(nome, url)] in ordine di nome.

    `suffix`: "_form345.zip" o "_form13f.zip". Gli indirizzi cambiano cartella
    di tanto in tanto (dal 2026 alcuni stanno sotto "datastandardsinnovation"),
    quindi si leggono dalla pagina invece di costruirli.
    """
    out = {}
    for m in re.finditer(r'href="([^"]*?([0-9a-z\-]+' + re.escape(suffix) + r'))"', html, re.I):
        url, name = m.group(1), m.group(2).lower()
        if url.startswith("/"):
            url = "https://www.sec.gov" + url
        out[name] = url
    return sorted(out.items())


def quarter_of(name):
    """"2026q3_form345.zip" -> ("2026-07-01", "2026-09-30"); None se il nome non è di un trimestre."""
    m = re.match(r"^(\d{4})q([1-4])_", name)
    if not m:
        return None
    y, q = int(m.group(1)), int(m.group(2))
    start = date(y, 3 * q - 2, 1)
    end = (date(y + (q == 4), (3 * q) % 12 + 1, 1) - timedelta(days=1))
    return start.isoformat(), end.isoformat()


def ticker_of(s):
    t = (s or "").strip().upper()
    return t if TICKER.match(t) and t not in ("NONE", "NA", "N/A") else ""


def roles(rel, title):
    """(amministratore, dirigente, oltre il 10%) da una relazione del data set ("Director,Officer")."""
    r = (rel or "").lower().replace(" ", "")
    return ("director" in r, "officer" in r, "tenpercent" in r or "10%" in r)


# ----------------------------------------------------------------- data set
def _table(zf, stem):
    """Le righe di una tabella dello zip come dizionari (tabulazione, niente virgolette)."""
    name = next((n for n in zf.namelist() if os.path.basename(n).upper().startswith(stem)), None)
    if not name:
        raise ValueError(f"nello zip manca {stem}")
    with zf.open(name) as fh:
        text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline="")
        head = text.readline().rstrip("\r\n").split("\t")
        for line in text:
            v = line.rstrip("\r\n").split("\t")
            if len(v) < len(head):
                v += [""] * (len(head) - len(v))
            yield dict(zip(head, v))


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def read_form345(path, keep_from):
    """Uno zip trimestrale -> {"months": {mese: {"b", "s"}}, "buys": [...], "rows": n}.

    - si leggono solo i Form 4 originali (non le correzioni 4/A, che
      ripeterebbero le stesse operazioni);
    - un acquisto è il codice P, una vendita il codice S;
    - per il termometro contano amministratori e dirigenti, una volta per
      azienda e per mese (persona-mese): chi compra tre volte a marzo è un
      compratore solo;
    - gli acquisti si tengono, con i dettagli, solo dal giorno `keep_from`.
    """
    with zipfile.ZipFile(path) as zf:
        sub = {}
        for r in _table(zf, "SUBMISSION"):
            if (r.get("DOCUMENT_TYPE") or "").strip() != "4":
                continue
            sub[r["ACCESSION_NUMBER"]] = {
                "i": (r.get("ISSUERCIK") or "").lstrip("0"), "n": (r.get("ISSUERNAME") or "").strip(),
                "t": ticker_of(r.get("ISSUERTRADINGSYMBOL")), "f": sec_date(r.get("FILING_DATE"))}
        own = {}
        for r in _table(zf, "REPORTINGOWNER"):
            a = r["ACCESSION_NUMBER"]
            if a not in sub:
                continue
            d, o, ten = roles(r.get("RPTOWNER_RELATIONSHIP"), r.get("RPTOWNER_TITLE"))
            own.setdefault(a, []).append({"oc": (r.get("RPTOWNERCIK") or "").lstrip("0"),
                                          "o": (r.get("RPTOWNERNAME") or "").strip(),
                                          "r": (r.get("RPTOWNER_TITLE") or "").strip(),
                                          "dir": d, "off": o, "ten": ten})
        months, buys, n = {}, [], 0
        for r in _table(zf, "NONDERIV_TRANS"):
            a = r["ACCESSION_NUMBER"]
            s = sub.get(a)
            code = (r.get("TRANS_CODE") or "").strip().upper()
            if not s or code not in ("P", "S"):
                continue
            d = sec_date(r.get("TRANS_DATE"))
            if not d:
                continue
            ad = (r.get("TRANS_ACQUIRED_DISP_CD") or "").strip().upper()
            if (code == "P" and ad != "A") or (code == "S" and ad != "D"):
                continue
            n += 1
            owners = own.get(a) or []
            ins = [o for o in owners if o["dir"] or o["off"]]
            m = months.setdefault(d[:7], {"b": set(), "s": set()})
            for o in ins:
                m["b" if code == "P" else "s"].add(s["i"] + ":" + (o["oc"] or o["o"]))
            if code == "P" and d >= keep_from and owners:
                o = ins[0] if ins else owners[0]
                sh, px = _num(r.get("TRANS_SHARES")), _num(r.get("TRANS_PRICEPERSHARE"))
                buys.append(dict(s, d=d, a=a, o=o["o"], oc=o["oc"], r=o["r"], dir=o["dir"], off=o["off"],
                                 ten=o["ten"], sh=sh, p=px, v=round(sh * px, 2)))
    return {"months": {k: {"b": len(v["b"]), "s": len(v["s"])} for k, v in sorted(months.items())},
            "buys": buys, "rows": n}


# ----------------------------------------------------------------- giorno per giorno
def parse_master(text):
    """master.AAAAMMGG.idx -> [(cik, data, percorso)] dei Form 4, una volta per documento.

    Il file elenca ogni deposito una volta per ogni soggetto coinvolto
    (l'azienda e chi dichiara): si tiene il primo.
    """
    out, seen = [], set()
    for line in text.splitlines():
        p = line.split("|")
        if len(p) != 5 or p[2].strip() != "4":
            continue
        path = p[4].strip()
        acc = os.path.basename(path).replace(".txt", "")
        if acc in seen:
            continue
        seen.add(acc)
        out.append((p[0].strip(), sec_date(p[3]), path))
    return out


def parse_filing(text):
    """Il testo completo di un deposito di Form 4 -> {issuer, rows} o None.

    Il documento XML sta fra <XML> e </XML>; le operazioni le legge lo stesso
    parser delle duecento aziende (sources.parse_form4).
    """
    from .sources import parse_form4
    m = re.search(r"<XML>\s*(.*?)\s*</XML>", text, re.S | re.I)
    if not m:
        return None
    xml = m.group(1)
    def tag(t):
        x = re.search(rf"<{t}>\s*([^<]*?)\s*</{t}>", xml)
        return x.group(1).strip() if x else ""
    try:
        rows = parse_form4(xml)
    except Exception:
        return None
    rel = re.search(r"<reportingOwnerRelationship>(.*?)</reportingOwnerRelationship>", xml, re.S)
    rel = rel.group(1) if rel else ""
    ten = bool(re.search(r"<isTenPercentOwner>\s*(1|true)\s*<", rel, re.I))
    return {"i": tag("issuerCik").lstrip("0"), "n": tag("issuerName"), "t": ticker_of(tag("issuerTradingSymbol")),
            "oc": tag("rptOwnerCik").lstrip("0"), "ten": ten, "rows": rows}


def day_summary(filings):
    """[(accessione, data di deposito, deposito letto)] -> il riassunto di un giorno."""
    b, s, buys = set(), set(), []
    for acc, fdate, f in filings:
        if not f:
            continue
        for t in f["rows"]:
            code, d = t.get("code"), (t.get("date") or "")[:10]
            if code not in ("P", "S") or not d:
                continue
            ins = t.get("is_director") or t.get("is_senior")
            key = f["i"] + ":" + (f["oc"] or t["who"])
            if ins:
                (b if code == "P" else s).add(d[:7] + "|" + key)
            if code == "P" and t.get("ad") == "A":
                buys.append({"i": f["i"], "n": f["n"], "t": f["t"], "f": fdate, "d": d, "a": acc,
                             "o": t["who"], "oc": f["oc"], "r": t.get("role") or "",
                             "dir": bool(t.get("is_director")), "off": bool(t.get("is_senior")),
                             "ten": bool(f.get("ten")) and not ins,
                             "sh": t.get("shares") or 0, "p": t.get("price") or 0,
                             "v": round((t.get("shares") or 0) * (t.get("price") or 0), 2)})
    return {"b": sorted(b), "s": sorted(s), "buys": buys}


# ----------------------------------------------------------------- cache
def _read(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def load(folder=DIR):
    """Tutto quello che serve al giro orario: {"months", "buys", "spy", "through", "pending", "quarters"}.

    `through`: l'ultimo giorno di depositi coperto senza buchi (data set più
    giorni letti), `pending`: i giorni feriali dopo l'ultimo data set non ancora
    letti per intero.
    """
    st = _read(os.path.join(folder, "stato.json"), {}) or {}
    months, buys = {}, []
    qdir = os.path.join(folder, "insider")
    last_q = None
    for fn in sorted(os.listdir(qdir)) if os.path.isdir(qdir) else []:
        q = _read(os.path.join(qdir, fn))
        if not q:
            continue
        for k, v in (q.get("months") or {}).items():
            m = months.setdefault(k, {"b": 0, "s": 0})
            m["b"] += v["b"]
            m["s"] += v["s"]
        buys += q.get("buys") or []
        last_q = max(last_q or "", q.get("to") or "")
    days = {}
    ddir = os.path.join(folder, "giorni")
    for fn in sorted(os.listdir(ddir)) if os.path.isdir(ddir) else []:
        d = _read(os.path.join(ddir, fn))
        if d and (not last_q or fn[:10] > last_q):
            days[fn[:10]] = d
    # i mesi dai giorni: persone distinte nel mese, sommate a quelle dei data set
    per = {}
    for d in days.values():
        for side in ("b", "s"):
            for x in d.get(side) or []:
                k, key = x.split("|", 1)
                per.setdefault(k, {"b": set(), "s": set()})[side].add(key)
        buys += d.get("buys") or []
    for k, v in per.items():
        m = months.setdefault(k, {"b": 0, "s": 0})
        m["b"] += len(v["b"])
        m["s"] += len(v["s"])
    through, pending, read = last_q, [], 0
    if last_q:
        day = date.fromisoformat(last_q) + timedelta(days=1)
        end = date.fromisoformat(st.get("today") or date.today().isoformat()) - timedelta(days=1)
        while day <= end:
            k = day.isoformat()
            if day.weekday() < 5:
                if (days.get(k) or {}).get("complete"):
                    read += 1
                    if not pending:
                        through = k
                elif k not in (st.get("no_index") or []):
                    pending.append(k)
            day += timedelta(days=1)
    spy = (_read(os.path.join(folder, "spy.json"), {}) or {}).get("px") or []
    return {"months": months, "buys": buys, "spy": spy, "through": through, "pending": pending,
            "read": read, "last_q": last_q,
            "quarters": st.get("quarters") or [], "updated": st.get("updated")}


# ----------------------------------------------------------------- radar
def _key(b):
    return b.get("t") or ("CIK" + b["i"])


def _cluster(rows):
    """Il primo gruppo di almeno CLUSTER_MIN persone diverse entro CLUSTER_DAYS giorni, o []."""
    rows = sorted(rows, key=lambda r: r["d"])
    for i, a in enumerate(rows):
        win = [r for r in rows[i:] if (date.fromisoformat(r["d"]) - date.fromisoformat(a["d"])).days <= CLUSTER_DAYS]
        if len({r["oc"] or r["o"] for r in win}) >= CLUSTER_MIN:
            return win
    return []


def radar(buys, today, universe=(), window=WINDOW, min_value=MIN_VALUE):
    """Le aziende di tutto il mercato con acquisti degli insider negli ultimi `window` giorni.

    Contano gli acquisti sul mercato di amministratori e dirigenti, a un prezzo
    di almeno MIN_PRICE dollari, in società con un simbolo di borsa (senza,
    sono fondi non quotati o società private: i loro amministratori che
    "comprano" sottoscrivono quote); un'azienda entra se insieme hanno comprato
    per almeno `min_value` dollari. Gli acquisti di chi possiede più del 10% senza
    essere amministratore o dirigente (spesso fondi) si mostrano a parte.
    """
    start = (date.fromisoformat(today) - timedelta(days=window)).isoformat()
    seen, by = set(), {}
    for b in buys:
        if not (start <= b["d"] <= today) or (b.get("p") or 0) < MIN_PRICE or (b.get("v") or 0) <= 0:
            continue
        if not b.get("t"):                # senza simbolo di borsa: fondi non quotati, società private
            continue
        k = (b["a"], b["d"], b["o"], b["sh"], b["p"])
        if k in seen:                     # lo stesso acquisto da due fonti
            continue
        seen.add(k)
        by.setdefault(_key(b), []).append(b)
    out = []
    for key, rows in by.items():
        ins = [r for r in rows if r["dir"] or r["off"]]
        big = [r for r in rows if not (r["dir"] or r["off"])]
        val = sum(r["v"] for r in ins)
        if val < min_value:
            continue
        people = {}
        for r in ins:
            p = people.setdefault(r["oc"] or r["o"], {"who": r["o"], "role": r["r"] or ("Director" if r["dir"] else "Officer"),
                                                       "v": 0.0, "last": r["d"]})
            p["v"] += r["v"]
            p["last"] = max(p["last"], r["d"])
        top = sorted(people.values(), key=lambda p: -p["v"])
        last = max(rows, key=lambda r: (r["d"], r["f"] or ""))
        out.append({
            "t": last.get("t") or "", "cik": last["i"], "name": last["n"],
            "buyers": len(people), "value": round(val), "n": len(ins),
            "first": min(r["d"] for r in ins), "last": max(r["d"] for r in ins),
            "filed": max(r["f"] or "" for r in ins) or None,
            "senior": any(any(s in (r["r"] or "").lower() for s in SENIOR) for r in ins),
            "cluster": bool(_cluster(ins)),
            "big_value": round(sum(r["v"] for r in big)),
            "avg_price": round(sum(r["v"] for r in ins) / max(1, sum(r["sh"] for r in ins)), 2),
            "who": [{"who": p["who"], "role": p["role"], "v": round(p["v"]), "last": p["last"]} for p in top[:5]],
            "ours": (last.get("t") or "") in set(universe),
        })
    out.sort(key=lambda c: (-c["cluster"], -c["buyers"], -c["value"]))
    return out


# ----------------------------------------------------------------- rete
def _download(url, path):
    """Uno zip della SEC su disco, a pezzi: non sta tutto in memoria."""
    import requests
    from .sources import HEAD
    with requests.get(url, headers=HEAD, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)


def _listing(urls, suffix):
    from .sources import _get
    err = None
    for u in urls:
        try:
            got = parse_listing(_get(u).text, suffix)
            if got:
                return got
            err = f"nessuno zip in {u}"
        except Exception as e:
            err = str(e)
    raise RuntimeError(f"elenco dei data set non trovato: {err}")


def update_quarters(folder, today, until):
    """Scarica i data set trimestrali che mancano, dal più recente. -> righe del registro."""
    log = []
    st_path = os.path.join(folder, "stato.json")
    st = _read(st_path, {}) or {}
    done = set(st.get("quarters") or [])
    files = [(n, u) for n, u in _listing(LIST_345, "_form345.zip")
             if quarter_of(n) and int(n[:4]) >= FIRST_YEAR]
    keep_from = (date.fromisoformat(today) - timedelta(days=KEEP_BUYS_DAYS)).isoformat()
    for name, url in sorted(files, reverse=True):
        if name in done:
            continue
        if time.time() > until:
            log.append("tempo finito: i data set che mancano al prossimo giro")
            break
        q0, q1 = quarter_of(name)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                p = os.path.join(tmp, name)
                _download(url, p)
                res = read_form345(p, keep_from)
            _write(os.path.join(folder, "insider", name.replace(".zip", ".json")),
                   dict(res, name=name, **{"from": q0, "to": q1}))
            done.add(name)
            log.append(f"{name}: {res['rows']} operazioni, {len(res['buys'])} acquisti recenti tenuti")
        except Exception as e:
            log.append(f"ERR {name}: {e}")
        st["quarters"] = sorted(done)
        _write(st_path, st)
    return log


def update_days(folder, today, until):
    """I giorni dopo l'ultimo data set, dal più recente, dall'indice giornaliero di EDGAR."""
    from .sources import _get
    log = []
    st_path = os.path.join(folder, "stato.json")
    st = _read(st_path, {}) or {}
    st["today"] = today
    last_q = max((quarter_of(q)[1] for q in st.get("quarters") or [] if quarter_of(q)), default=None)
    if not last_q:
        return ["nessun data set ancora: i giorni aspettano"]
    no_index = set(st.get("no_index") or [])
    day = date.fromisoformat(today) - timedelta(days=1)
    todo = []
    while day.isoformat() > last_q:
        if day.weekday() < 5 and day.isoformat() not in no_index:
            todo.append(day.isoformat())
        day -= timedelta(days=1)
    for k in todo:                        # dal più recente: il radar serve prima di tutto aggiornato
        path = os.path.join(folder, "giorni", k + ".json")
        cur = _read(path) or {}
        if cur.get("complete"):
            continue
        if time.time() > until:
            log.append("tempo finito: i giorni che mancano al prossimo giro")
            break
        y, m = int(k[:4]), int(k[5:7])
        url = (f"https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{(m - 1) // 3 + 1}/"
               f"master.{k.replace('-', '')}.idx")
        try:
            idx = parse_master(_get(url).text)
        except Exception as e:
            if "404" in str(e) or "fallita" in str(e):
                # un giorno senza indice: festivo, o non ancora pubblicato (se è ieri)
                if k < (date.fromisoformat(today) - timedelta(days=4)).isoformat():
                    no_index.add(k)
                log.append(f"{k}: nessun indice ({e})")
                continue
            raise
        got = dict(cur.get("got") or {})       # accessione -> deposito letto (o None)
        for cik, fdate, p in idx:
            acc = os.path.basename(p).replace(".txt", "")
            if acc in got:
                continue
            if time.time() > until:
                break
            try:
                got[acc] = [fdate, parse_filing(_get("https://www.sec.gov/Archives/" + p).text)]
            except Exception as e:
                got[acc] = [fdate, None]
                log.append(f"{k} {acc}: {e}")
        complete = all(os.path.basename(p).replace(".txt", "") in got for _, _, p in idx)
        summ = day_summary([(a, v[0], v[1]) for a, v in got.items()])
        # i documenti letti restano solo finché il giorno non è finito: poi basta il riassunto
        _write(path, dict(summ, complete=complete, docs=len(idx), got=None if complete else got))
        log.append(f"{k}: {len(got)} di {len(idx)} Form 4" + ("" if complete else " (continua al prossimo giro)"))
    st["no_index"] = sorted(no_index)
    _write(st_path, st)
    # i giorni ormai coperti da un data set non servono più
    ddir = os.path.join(folder, "giorni")
    for fn in os.listdir(ddir) if os.path.isdir(ddir) else []:
        if fn[:10] <= last_q:
            os.remove(os.path.join(ddir, fn))
    return log


def update_spy(folder):
    """Trent'anni di chiusure dello SPY, per la prova del termometro su tutto il mercato."""
    from .sources import WEB, _get, parse_prices_yahoo
    for host in ("query1", "query2"):
        try:
            px = parse_prices_yahoo(_get(f"https://{host}.finance.yahoo.com/v8/finance/chart/SPY"
                                         f"?range=30y&interval=1d", headers=WEB).json())
            if len(px) > 1000:
                _write(os.path.join(folder, "spy.json"), {"px": px, "src": "yahoo"})
                return f"SPY: {len(px)} sedute dal {px[0]['d']}"
        except Exception as e:
            err = e
    return f"ERR SPY: {err}"


def main():
    from . import fondi
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=50)
    ap.add_argument("--folder", default=DIR)
    ap.add_argument("--fondi", type=int, default=fondi.FILES_PER_RUN,
                    help="quanti data set 13F al massimo in questo giro")
    a = ap.parse_args()
    start = time.time()
    until = start + 60 * a.minutes
    today = date.today().isoformat()
    os.makedirs(a.folder, exist_ok=True)
    log = [update_spy(a.folder)]
    for step, fn in (("trimestri", lambda: update_quarters(a.folder, today, until)),
                     ("giorni", lambda: update_days(a.folder, today, min(until, time.time() + 60 * DAY_MINUTES))),
                     ("13F", lambda: fondi.update(os.path.join(a.folder, "13f"), until, a.fondi))):
        try:
            log += [f"[{step}] {x}" for x in fn()]
        except Exception as e:
            log.append(f"[{step}] ERR {e}")
    st_path = os.path.join(a.folder, "stato.json")
    st = _read(st_path, {}) or {}
    st["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write(st_path, st)
    print("\n".join(log))
    got = load(a.folder)
    print(f"\nmesi: {len(got['months'])}, acquisti tenuti: {len(got['buys'])}, "
          f"coperto fino al {got['through']}, giorni ancora da leggere: {len(got['pending'])}")
    print(f"durata: {(time.time() - start) / 60:.1f} minuti")


if __name__ == "__main__":
    main()
