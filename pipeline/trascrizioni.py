"""Scarica da Alpha Vantage le trascrizioni delle call che mancano, e le salva in Word.

Non fa parte del giro orario: lo lancia un workflow a sé (trascrizioni.yml), una
volta al giorno o a mano. Il risultato è un pacchetto di file .docx che si
scarica dalla pagina del giro su GitHub e che resta lì tre giorni. Le
trascrizioni NON finiscono nel repository né sul sito: sono testi protetti.
Nel repository entrano solo i fatti estratti (input/extracted/), come sempre.

Perché Word: il passo successivo è a mano. Le trascrizioni si danno a Claude in
una conversazione, Claude ne estrae i fatti con lo schema di extract.py, e il
file JSON che ne esce si carica in input/extracted/. L'estrazione resta
gratuita (dentro l'abbonamento) e il programma non ha bisogno di nessuna chiave
di un modello.

Alpha Vantage, con la chiave gratuita, dà 25 richieste al giorno. Ogni giro ne
usa al massimo `--limit`, nell'ordine in cui servono di più:

1. le aziende con un cluster di acquisti, poi quelle con più compratori negli
   ultimi 90 giorni, poi le altre per punteggio;
2. per ogni azienda prima l'ultima call, poi la penultima (`--per-company`):
   tutte le aziende hanno la call più recente prima che qualcuna abbia la seconda.

Le date delle call sono quelle degli 8-K con item 2.02 (risultati), già nelle
schede del sito. Alpha Vantage vuole il trimestre FISCALE ("2027Q3" per la call
di Nvidia di novembre 2026): lo si ricava dal mese di chiusura dell'anno
fiscale, che la SEC scrive nel file dei depositi (`fiscalYearEnd`). Se con il
trimestre fiscale non arriva niente, si prova una volta quello di calendario.

Serve la variabile d'ambiente ALPHAVANTAGE_KEY (un segreto del repository).
"""
import argparse
import glob
import json
import os
import time
from datetime import date, timedelta

AV = "https://www.alphavantage.co/query"
PER_DAY = 25
OUT = "trascrizioni"
STATE = "cache-trascrizioni/scaricate.json"
EMPTY_TRIES = 2   # dopo due risposte vuote per la stessa call si smette di chiederla


class Limit(Exception):
    """Alpha Vantage dice che le richieste di oggi sono finite."""


# ------------------------------------------------------------------ trimestri
def fiscal_label(call_day, fy_end_month):
    """Il trimestre fiscale che una call commenta, nel formato di Alpha Vantage.

    La call commenta l'ultimo trimestre chiuso prima della call. I trimestri
    fiscali finiscono ogni tre mesi a partire dal mese di chiusura dell'anno
    (`fy_end_month`, da 1 a 12). L'anno fiscale prende il nome dall'anno in cui
    finisce: per Nvidia (chiusura a gennaio) il trimestre chiuso a ottobre 2026
    è il terzo dell'anno fiscale 2027.
    """
    d = date.fromisoformat(call_day) if isinstance(call_day, str) else call_day
    y, m = d.year, d.month - 1           # il trimestre è chiuso al più tardi a fine del mese prima
    if m == 0:
        y, m = y - 1, 12
    while (m - fy_end_month) % 3:
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    fy = y if m <= fy_end_month else y + 1
    q = ((m - fy_end_month - 1) % 12) // 3 + 1
    return f"{fy}Q{q}"


def calendar_label(call_day):
    """Lo stesso, con l'anno solare: la riserva se Alpha Vantage non usa quello fiscale."""
    return fiscal_label(call_day, 12)


# ------------------------------------------------------------------ che cosa serve
def wanted(index, cards, have, per_company=2, months=13, today=None):
    """[(ticker, data della call)] nell'ordine in cui servono.

    `index` è site/index.json, `cards` {ticker: scheda}, `have` l'insieme delle
    coppie (ticker, data) già scaricate o già estratte. Si guardano le call
    degli ultimi `months` mesi.
    """
    today = today or date.today()
    oldest = (today - timedelta(days=int(months * 30.44))).isoformat()

    def rank(r):
        return (0 if r.get("cluster") else 1, -(r.get("buyers") or 0), -(r.get("score") or 0), r["ticker"])

    order = [r["ticker"] for r in sorted(index.get("companies") or [], key=rank)]
    calls = {t: sorted({e[0] for e in ((cards.get(t) or {}).get("events_hist") or [])
                        if "2.02" in (e[1] or []) and e[0] >= oldest}, reverse=True)[:per_company]
             for t in order}
    out = []
    for k in range(per_company):          # prima l'ultima call di tutti, poi la penultima
        for t in order:
            if k < len(calls[t]) and (t, calls[t][k]) not in have:
                out.append((t, calls[t][k]))
    return out


def extracted_pairs(folder, cards, fy_end):
    """Le call già estratte in input/extracted, come coppie (ticker, data della call)."""
    names = {os.path.basename(p)[:-5] for p in glob.glob(os.path.join(folder, "*.json"))}
    out = set()
    for t, c in cards.items():
        for e in c.get("events_hist") or []:
            if "2.02" in (e[1] or []):
                labels = {calendar_label(e[0])}
                if t in fy_end:
                    labels.add(fiscal_label(e[0], fy_end[t]))
                if any(f"{t}_{lab}" in names for lab in labels):
                    out.add((t, e[0]))
    return out


# ------------------------------------------------------------------ Alpha Vantage
def parse_av(payload):
    """La risposta di Alpha Vantage -> [{"speaker", "title", "content"}], o [] se non c'è.

    Alza Limit se la risposta dice che la quota è finita, che serve un piano a
    pagamento o che la chiave non va: in tutti questi casi è inutile continuare oggi.
    """
    if not isinstance(payload, dict):
        return []
    note = " ".join(str(payload.get(k, "")) for k in ("Information", "Note", "Error Message"))
    low = note.lower()
    if any(w in low for w in ("rate limit", "requests per day", "premium", "frequency", "25 requests",
                              "apikey")):
        raise Limit(note.strip()[:300])
    rows = payload.get("transcript") or []
    out = []
    for r in rows:
        if isinstance(r, dict) and (r.get("content") or "").strip():
            out.append({"speaker": (r.get("speaker") or "").strip(),
                        "title": (r.get("title") or "").strip(),
                        "content": r["content"].strip()})
    return out


def fetch(ticker, label, key, get=None):
    if get is None:
        import requests

        def get(params):
            r = requests.get(AV, params=params, timeout=60)
            r.raise_for_status()
            return r.json()
    return parse_av(get({"function": "EARNINGS_CALL_TRANSCRIPT", "symbol": ticker,
                         "quarter": label, "apikey": key}))


# ------------------------------------------------------------------ Word
def to_docx(path, ticker, name, label, call_day, rows):
    from docx import Document
    from docx.shared import Pt
    doc = Document()
    doc.add_heading(f"{ticker} — {name}", level=1)
    doc.add_paragraph(f"Call del {call_day}, trimestre {label}. Fonte: Alpha Vantage. "
                      f"Uso personale per l'estrazione dei fatti: non ripubblicare.")
    for r in rows:
        p = doc.add_paragraph()
        who = p.add_run(r["speaker"] or "—")
        who.bold = True
        if r["title"]:
            p.add_run(f" ({r['title']})").italic = True
        body = doc.add_paragraph(r["content"])
        body.paragraph_format.space_after = Pt(8)
    doc.save(path)


# ------------------------------------------------------------------ giro
def _fy_end(cik, ua):
    """Il mese di chiusura dell'anno fiscale, dalla SEC (fiscalYearEnd, "MMDD")."""
    import requests
    r = requests.get(f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json",
                     headers={"User-Agent": ua}, timeout=30)
    r.raise_for_status()
    v = str(r.json().get("fiscalYearEnd") or "1231")
    return int(v[:2]) if v[:2].isdigit() and 1 <= int(v[:2]) <= 12 else 12


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=PER_DAY)
    ap.add_argument("--per-company", type=int, default=2)
    ap.add_argument("--site", default="site")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    key = os.environ.get("ALPHAVANTAGE_KEY")
    if not key:
        raise SystemExit("Manca ALPHAVANTAGE_KEY: va messa nei segreti del repository.")
    ua = os.environ.get("SEC_UA", "Conviction Engine (studente) nome@example.com")

    index = json.load(open(os.path.join(a.site, "index.json"), encoding="utf-8"))
    cards = {}
    for p in glob.glob(os.path.join(a.site, "data", "*.json")):
        c = json.load(open(p, encoding="utf-8"))
        cards[c["company"]["ticker"]] = c
    try:
        state = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        state = {"done": [], "fy_end": {}}
    fy_end = {k: int(v) for k, v in (state.get("fy_end") or {}).items()}
    have = {tuple(x) for x in state.get("done") or []} | extracted_pairs("input/extracted", cards, fy_end)
    # le call che Alpha Vantage non ha: dopo EMPTY_TRIES giorni a vuoto non si chiedono più
    empty = state.setdefault("empty", {})
    have |= {tuple(k.split("|")) for k, n in empty.items() if n >= EMPTY_TRIES}

    os.makedirs(a.out, exist_ok=True)
    log, used, got = [], 0, 0
    for t, day in wanted(index, cards, have, a.per_company):
        if used >= a.limit:
            break
        try:
            if t not in fy_end:
                fy_end[t] = _fy_end(cards[t]["company"]["cik"], ua)
                time.sleep(0.15)
            labels = [fiscal_label(day, fy_end[t])]
            if calendar_label(day) not in labels:
                labels.append(calendar_label(day))
            rows, lab = [], None
            for lab in labels:
                if used >= a.limit:
                    break
                used += 1
                rows = fetch(t, lab, key)
                time.sleep(1.5)
                if rows:
                    break
            if rows:
                name = (cards[t].get("company") or {}).get("name") or t
                to_docx(os.path.join(a.out, f"{t}_{lab}.docx"), t, name, lab, day, rows)
                state.setdefault("done", []).append([t, day])
                got += 1
                log.append(f"OK    {t} {lab} (call del {day}): {len(rows)} interventi")
            else:
                empty[f"{t}|{day}"] = empty.get(f"{t}|{day}", 0) + 1
                log.append(f"VUOTA {t} call del {day}: provati {', '.join(labels)} "
                           f"({empty[f'{t}|{day}']} di {EMPTY_TRIES} tentativi)")
        except Limit as e:
            log.append(f"STOP  Alpha Vantage: {e}")
            break
        except Exception as e:            # un'azienda che non va non ferma le altre
            log.append(f"ERR   {t} call del {day}: {str(e)[:200]}")

    state["fy_end"] = fy_end
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(state, open(STATE, "w", encoding="utf-8"))
    head = f"{date.today()}: {got} trascrizioni, {used} richieste ad Alpha Vantage su {a.limit}."
    with open(os.path.join(a.out, "riepilogo.txt"), "w", encoding="utf-8") as f:
        f.write(head + "\n\n" + "\n".join(log) + "\n")
    print(head)
    print("\n".join(log))


if __name__ == "__main__":
    main()
