"""Che cosa comprano i grandi fondi: i Form 13F.

Ogni gestore che amministra più di 100 milioni di dollari in azioni americane
deposita ogni trimestre il Form 13F: l'elenco delle sue posizioni a fine
trimestre, entro 45 giorni. La SEC li pubblica in tabella ("Form 13F Data
Sets", dal 2013), un file zip ogni tre mesi di depositi (circa 90 MB l'uno).

Da quei file, per ognuna delle duecento aziende e per ogni trimestre:

- **detentori**: quanti gestori la possiedono (posizioni in azioni, non opzioni);
- **azioni**: quante ne possiedono in tutto;
- **ampiezza** (breadth): detentori diviso il numero di gestori che hanno
  depositato quel trimestre. Il numero dei gestori cresce ogni anno, quindi
  i detentori da soli salirebbero per tutte le aziende; l'ampiezza no.
- **variazione dell'ampiezza**: di quanti punti è cambiata rispetto al
  trimestre prima. È la misura di Chen, Hong e Stein ("Breadth of ownership
  and stock returns", Journal of Financial Economics, 2002): quando sempre
  meno fondi possiedono un titolo, quelli che vorrebbero venderlo allo
  scoperto ma non possono restano fuori, e il prezzo tende a scendere dopo;
  quando l'ampiezza cresce, il contrario.

Il dato è lento: arriva fino a 45 giorni dopo la fine del trimestre. La prova
all'indietro (pipeline/predict.py) lo usa solo dal 46° giorno.

**Come si trova l'azienda nel file.** Il 13F identifica un titolo con il CUSIP,
che la pipeline non ha. Il nome dell'emittente scritto dai gestori, ripulito
(maiuscole, niente "INC", "CORP", "/DE/", abbreviazioni sciolte), si confronta
con il nome dell'azienda alla SEC; fra i CUSIP che corrispondono vince quello
con più detentori, la classe principale. Poi si contano tutte le righe con quel
CUSIP, anche quelle in cui il nome è scritto in un altro modo.
L'indice del sito elenca le corrispondenze trovate, per un controllo a mano;
in config.json, "cusip", si può fissare quella giusta.

Le correzioni (13F-HR/A) non si leggono: si usa il deposito originale, e se un
gestore ne ha fatti due per lo stesso trimestre, il più recente. Di ogni file si
tiene solo il trimestre principale: i depositi in ritardo di trimestri vecchi
(o con date sbagliate, ce ne sono del 2001) farebbero trimestri con poche
decine di gestori, e un'ampiezza che non vuol dire niente.
"""
import io
import json
import os
import re
import tempfile
import time
import zipfile
from datetime import date, timedelta

from .mercato import _download, _listing, _read, _write, sec_date

LIST_13F = ("https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets",
            "https://www.sec.gov/dera/data/form-13f")
FIRST_YEAR = 2015            # dieci anni di trimestri, più uno per la prima variazione
FILES_PER_RUN = 10           # ogni file sono circa 90 MB: dieci per giro, un minuto l'uno
VERSION = 2                  # cambia quando cambia il modo di leggere i file: si rileggono tutti
DEADLINE = 45                # giorni dopo la fine del trimestre entro cui si deposita

STOP = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PLC",
        "THE", "NEW", "DEL", "DE", "LLC", "LP", "NV", "SA", "AG", "CL", "CLASS", "A", "B", "C",
        "COM", "COMMON", "SHS", "STK", "STOCK", "CAP", "ORD", "OF", "AND", "N", "V", "MD", "NY", "PA", "VA"}
ABBR = {"HLDGS": "HOLDINGS", "HLDG": "HOLDINGS", "HOLDING": "HOLDINGS", "GRP": "GROUP",
        "INTL": "INTERNATIONAL", "INTERNATL": "INTERNATIONAL", "TECHNOLOGIES": "TECH",
        "TECHNOLOGY": "TECH", "TECHNOLOGIE": "TECH", "SYS": "SYSTEMS", "FINL": "FINANCIAL",
        "SVCS": "SERVICES", "SVC": "SERVICES", "MGMT": "MANAGEMENT", "PHARMACEUTICALS": "PHARMA",
        "PHARMACEUTICAL": "PHARMA", "BANCORPORATION": "BANCORP", "LABS": "LABORATORIES",
        "MFG": "MANUFACTURING", "PPTYS": "PROPERTIES", "COMMUNICATIONS": "COMMUNICATION",
        "SOLUTIONS": "SOLUTION", "RES": "RESOURCES", "AMER": "AMERICA", "AMERN": "AMERICAN",
        "COS": "COMPANIES", "BK": "BANK", "INSTRS": "INSTRUMENTS", "INSTR": "INSTRUMENTS",
        "PETE": "PETROLEUM", "MATLS": "MATERIALS", "PAC": "PACIFIC", "WKS": "WORKS", "ELEC": "ELECTRIC",
        "MLS": "MILLS", "NATL": "NATIONAL", "LAB": "LABORATORIES", "HLTH": "HEALTH", "BANCSHS": "BANCSHARES",
        "COMMUNICATIONS": "COMMUNICATION", "SCIENCES": "SCIENCE", "STS": "STORES"}

# Le aziende che in dieci anni hanno cambiato nome: nei 13F vecchi stanno sotto il
# nome di allora. Si aggiungono a quello di oggi; in config.json, "nomi_13f", se ne
# possono aggiungere altri.
ALIASES = {
    "XOM": ["EXXON MOBIL CORP"], "META": ["FACEBOOK INC"], "LLY": ["LILLY ELI & CO"],
    "GWW": ["GRAINGER W W INC"], "DHI": ["D R HORTON INC"], "CI": ["CIGNA CORP"],
    "ELV": ["ANTHEM INC"], "SLB": ["SCHLUMBERGER LTD"], "KLAC": ["KLA-TENCOR CORP"],
    "APA": ["APACHE CORP"], "WMT": ["WAL-MART STORES INC"], "TFC": ["BB&T CORP"],
    "BKNG": ["PRICELINE GROUP INC", "PRICELINE COM INC"], "OVV": ["ENCANA CORP"],
    "SSB": ["SOUTH STATE CORP"], "CPB": ["CAMPBELL SOUP CO"], "HCA": ["HCA HOLDINGS INC"],
    "RTX": ["RAYTHEON TECHNOLOGIES CORP", "UNITED TECHNOLOGIES CORP"], "CB": ["ACE LTD"],
    "ULTA": ["ULTA SALON COSMETCS & FRAG I"], "ONTO": ["NANOMETRICS INC"],
    "BNY": ["BANK OF NEW YORK MELLON CORP"],
}


def norm(name):
    """Un nome di emittente ripulito per il confronto: "Boeing Co/The" -> "BOEING"."""
    # lo stato d'origine: "/DE/", "/DE", " / MA" alla fine, "\\DE\\"; non "CULLEN/FROST"
    s = re.sub(r"\s*[/\\]\s*[A-Z]{2,3}\s*([/\\]|$)", " ", (name or "").upper().strip())
    s = re.sub(r"\s*[/\\]\s*[A-Z]{2,3}\s*([/\\]|$)", " ", s.strip())
    s = s.replace("&", " AND ").replace("'", "")
    toks = [ABBR.get(t, t) for t in re.split(r"[^A-Z0-9]+", s) if t]
    return " ".join(t for t in toks if t not in STOP)


def matcher(companies):
    """{ticker: nome alla SEC, o lista di nomi} -> funzione nome del 13F -> ticker o None.

    Uguale vince su "comincia con"; "uguale" vale anche senza spazi
    ("OREILLY" e "O REILLY", "EXXONMOBIL" e "EXXON MOBIL"); "comincia con" vale
    solo con almeno due parole, perché "AMERICAN" da solo è l'inizio di troppi nomi.
    """
    exact, pref = {}, []
    for t, names in companies.items():
        for n in ([names] if isinstance(names, str) else names):
            k = norm(n)
            if not k:
                continue
            exact.setdefault(k, t)
            exact.setdefault(k.replace(" ", ""), t)
            if len(k.split()) >= 2:
                pref.append((k, t))
    pref.sort(key=lambda x: -len(x[0]))
    cache = {}

    def match(name):
        if name in cache:
            return cache[name]
        k = norm(name)
        t = exact.get(k) or exact.get(k.replace(" ", ""))
        if t is None and len(k.split()) >= 2:
            t = next((tt for kk, tt in pref if k.startswith(kk + " ") or kk.startswith(k + " ")), None)
        cache[name] = t
        return t
    return match


def _rows(zf, stem):
    name = next((n for n in zf.namelist() if os.path.basename(n).upper().startswith(stem)), None)
    if not name:
        raise ValueError(f"nello zip manca {stem}")
    with zf.open(name) as fh:
        text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline="")
        head = text.readline().rstrip("\r\n").split("\t")
        idx = {h: i for i, h in enumerate(head)}
        for line in text:
            yield idx, line.rstrip("\r\n").split("\t")


def read_13f(path, companies, fixed=None):
    """Uno zip 13F -> {trimestre: {"N": gestori, "co": {ticker: {"h", "sh", "cusip", "name"}}}}.

    `companies`: {ticker: nome o nomi}; `fixed`: {ticker: CUSIP} scelti a mano.

    Un file contiene un trimestre principale (quello dei depositi puntuali) e
    qualche deposito in ritardo di trimestri vecchi, o con date sbagliate: si
    tiene solo il principale, quello con più gestori. Poi due passate sulla
    tabella delle posizioni:

    1. **dal nome al CUSIP**: le righe il cui emittente corrisponde a una delle
       nostre aziende dicono quale CUSIP le appartiene (vince quello con più
       detentori);
    2. **dal CUSIP ai detentori**: si contano tutte le righe con quel CUSIP,
       comunque il gestore abbia scritto il nome ("GENERAL MTRS CO",
       "ESTEE LAUDER COMPANIES-CL A"…). Bastano le prime otto cifre:
       la nona è solo di controllo, e qualcuno la sbaglia.
    """
    fixed = {t: c.upper()[:8] for t, c in (fixed or {}).items()}
    match = matcher(companies)
    with zipfile.ZipFile(path) as zf:
        latest = {}                           # (gestore, trimestre) -> (data di deposito, accessione)
        for idx, v in _rows(zf, "SUBMISSION"):
            g = lambda k: v[idx[k]].strip() if k in idx and idx[k] < len(v) else ""
            if g("SUBMISSIONTYPE").upper() != "13F-HR":
                continue
            per, fd = sec_date(g("PERIODOFREPORT")), sec_date(g("FILING_DATE")) or ""
            if not per:
                continue
            k = (g("CIK").lstrip("0"), per)
            if k not in latest or (fd, g("ACCESSION_NUMBER")) > latest[k]:
                latest[k] = (fd, g("ACCESSION_NUMBER"))
        n_by = {}
        for (cik, per) in latest:
            n_by[per] = n_by.get(per, 0) + 1
        if not n_by:
            return {}
        main = max(n_by, key=n_by.get)        # il trimestre principale del file
        acc = {a: k[0] for k, (_, a) in latest.items() if k[1] == main}

        def rows():
            pos = None
            for idx, v in _rows(zf, "INFOTABLE"):
                if pos is None:               # tre milioni di righe a file: niente ricerche per nome
                    pos = [idx.get(c, -1) for c in ("ACCESSION_NUMBER", "SSHPRNAMTTYPE", "PUTCALL",
                                                    "CUSIP", "NAMEOFISSUER", "SSHPRNAMT")]
                    ia, it, ip, ic, iname, ish = pos
                if len(v) <= max(pos):
                    v = v + [""] * (max(pos) + 1 - len(v))
                who = acc.get(v[ia])
                if not who or v[it].strip().upper() != "SH" or (ip >= 0 and v[ip].strip()):
                    continue
                yield who, v[ic].strip().upper(), v[iname].strip(), v[ish]

        # 1. dal nome al CUSIP
        cand = {}                             # (ticker, cusip a 8) -> gestori
        for who, cusip, nm, _ in rows():
            t = match(nm)
            if t is not None and t not in fixed and len(cusip) >= 8:
                cand.setdefault((t, cusip[:8]), set()).add(who)
        best = dict(fixed)
        size = {}
        for (t, c8), w in cand.items():
            if len(w) > size.get(t, 0):
                best[t], size[t] = c8, len(w)
        by8 = {c8: t for t, c8 in best.items()}
        # 2. dal CUSIP ai detentori
        agg = {}                              # ticker -> [gestori, azioni, nomi, cusip completi]
        for who, cusip, nm, sh in rows():
            t = by8.get(cusip[:8])
            if t is None:
                continue
            a = agg.get(t)
            if a is None:
                a = agg[t] = [set(), 0.0, {}, {}]
            a[0].add(who)
            try:
                a[1] += float(sh or 0)
            except ValueError:
                pass
            a[2][nm] = a[2].get(nm, 0) + 1
            a[3][cusip] = a[3].get(cusip, 0) + 1
    co = {t: {"h": len(w), "sh": round(sh), "cusip": max(cs, key=cs.get), "name": max(nms, key=nms.get)}
          for t, (w, sh, nms, cs) in agg.items()}
    return {main: {"N": n_by[main], "co": co}}


def update(folder, until, max_files=FILES_PER_RUN, companies=None, fixed=None):
    """Scarica i file 13F che mancano, dal più recente. -> righe del registro."""
    if companies is None:
        companies, fixed = _companies()
    st_path = os.path.join(folder, "stato.json")
    st = _read(st_path, {}) or {}
    done = set(st.get("files") or [])
    # la corrispondenza dei nomi è cambiata (aziende nuove, CUSIP fissati a mano): si rifà tutto
    sig = json.dumps([VERSION, sorted(companies.items()), sorted((fixed or {}).items())])
    if st.get("sig") != sig:
        done = set()
        st["sig"] = sig
    files = [(n, u) for n, u in _listing(LIST_13F, "_form13f.zip") if _year(n) >= FIRST_YEAR]
    log, n = [], 0
    for name, url in sorted(files, key=lambda x: _start(x[0]), reverse=True):
        if name in done:
            continue
        if n >= max_files or time.time() > until:
            log.append("i file 13F che mancano al prossimo giro")
            break
        try:
            with tempfile.TemporaryDirectory() as tmp:
                p = os.path.join(tmp, name)
                _download(url, p)
                res = read_13f(p, companies, fixed)
            _write(os.path.join(folder, name.replace(".zip", ".json")), res)
            done.add(name)
            n += 1
            log.append(f"{name}: trimestri {', '.join(sorted(res))}, "
                       f"{sum(len(v['co']) for v in res.values())} aziende trovate")
        except Exception as e:
            log.append(f"ERR {name}: {e}")
        st["files"] = sorted(done)
        _write(st_path, st)
    st["files"] = sorted(done)
    _write(st_path, st)
    return log


def _year(name):
    m = re.search(r"(\d{4})", name)
    return int(m.group(1)) if m else 0


def _start(name):
    """L'inizio del periodo di un file, per ordinarli: "2016q4…" o "01jun2026-31aug2026…"."""
    m = re.match(r"^(\d{4})q([1-4])", name)
    if m:
        return f"{m.group(1)}-{3 * int(m.group(2)) - 2:02d}"
    d = sec_date(re.sub(r"^(\d{2})([a-z]{3})(\d{4}).*", r"\1-\2-\3", name).upper())
    return (d or "0000-00")[:7]


def _companies(index="site/index.json", cfg="config.json"):
    """{ticker: nome} delle aziende seguite (dal sito) e i CUSIP fissati a mano (da config.json)."""
    idx = _read(index, {}) or {}
    c = _read(cfg, {}) or {}
    extra = {k: v for k, v in (c.get("nomi_13f") or {}).items() if not k.startswith("_")}
    cos = {}
    for r in idx.get("companies") or []:
        t = r["ticker"]
        cos[t] = [r["name"]] + ALIASES.get(t, []) + list(extra.get(t) or [])
    return cos, {k: v for k, v in (c.get("cusip") or {}).items() if not k.startswith("_")}


def load(folder):
    """{trimestre: {"N", "co"}}: per ogni trimestre il file che ne ha più depositi."""
    best = {}
    for fn in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if not fn.endswith(".json") or fn == "stato.json":
            continue
        for per, v in (_read(os.path.join(folder, fn), {}) or {}).items():
            if per not in best or v["N"] > best[per]["N"]:
                best[per] = v
    return dict(sorted(best.items()))


def series(periods, ticker):
    """La serie trimestrale di un'azienda: [{q, avail, h, sh, br, dbr, dh}].

    `br`: ampiezza in punti percentuali; `dbr`: la sua variazione sul
    trimestre prima (solo se il trimestre prima c'è); `dh`: la variazione dei
    detentori in percentuale. `avail`: il giorno in cui il dato è pubblico.
    """
    out, prev = [], None
    for per, v in sorted(periods.items()):
        c = v["co"].get(ticker)
        if not c or not v["N"]:
            prev = None
            continue
        br = c["h"] / v["N"] * 100
        row = {"q": per, "avail": (date.fromisoformat(per) + timedelta(days=DEADLINE + 1)).isoformat(),
               "h": c["h"], "sh": c["sh"], "N": v["N"], "br": round(br, 3), "dbr": None, "dh": None}
        if prev and 80 <= (date.fromisoformat(per) - date.fromisoformat(prev["q"])).days <= 100:
            row["dbr"] = round(br - prev["br"], 3)
            row["dh"] = round((c["h"] / prev["h"] - 1) * 100, 2) if prev["h"] else None
        out.append(row)
        prev = row
    return out


def as_of(rows, day):
    """L'ultima riga pubblica il giorno `day`, o None."""
    got = None
    for r in rows:
        if r["avail"] <= day:
            got = r
    return got


def mapping(periods):
    """Le corrispondenze dell'ultimo trimestre: {ticker: [cusip, nome nel 13F, detentori]}."""
    if not periods:
        return {}
    last = periods[max(periods)]
    return {t: [c["cusip"], c["name"], c["h"]] for t, c in sorted(last["co"].items())}
