"""La scelta delle 200 small e mid cap: regola fissa, ripetibile.

Per ciascun indice (S&P MidCap 400, S&P SmallCap 600):
- si tengono i settori delle duecento grandi: tecnologia, finanza, sanità,
  industria, energia, consumi discrezionali, beni di prima necessità;
- si tolgono le aziende già seguite, i ticker con un punto (classi di azioni)
  e, fra due classi della stessa azienda, la seconda;
- i 100 posti si dividono fra i settori in proporzione a quante aziende ha
  ciascun settore (resti più grandi);
- dentro un settore l'ordine è quello dell'impronta SHA-1 di
  "conviction-engine|TICKER": un sorteggio che chiunque può rifare.

Sostituzioni (stessa regola: il ticker successivo nello stesso settore e
nello stesso ordine). Le liste di Wikipedia del 24/11/2026 avevano ancora
due aziende già sparite dalla borsa:
- QRVO -> BMI: Qorvo si è fusa in Skyworks (ottobre 2026);
- HLX -> KNTK: Helix si è fusa con Hornbeck Offshore (settembre 2026).
- SUPN -> INDV: Supernus si è fusa con Indivior ed è uscita dal Nasdaq
  (novembre 2026); il ticker successivo nell'ordine è proprio Indivior.
"""
import hashlib, json, sys

def load(p):
    out = {}
    for line in open(p):
        sec, t = line.split(":")
        out[sec.strip()] = t.split()
    return out

def pick(lists, skip, n=100):
    elig = {s: [t for t in ts if t not in skip and "." not in t] for s, ts in lists.items()}
    tot = sum(len(v) for v in elig.values())
    quota = {s: n * len(v) / tot for s, v in elig.items()}
    seats = {s: int(q) for s, q in quota.items()}
    for s in sorted(quota, key=lambda s: quota[s] - seats[s], reverse=True)[:n - sum(seats.values())]:
        seats[s] += 1
    key = lambda t: hashlib.sha1(("conviction-engine|" + t).encode()).hexdigest()
    return {s: sorted(sorted(v, key=key)[:seats[s]]) for s, v in elig.items()}

cfg = json.load(open(sys.argv[1]))
have = {t for g in cfg["universi"] for t in g["tickers"]}
mid = load(sys.argv[2]); small = load(sys.argv[3])
mid_all = {t for v in mid.values() for t in v}
dup = {"UA", "CENTA"}
pm = pick(mid, have)
ps = pick(small, have | mid_all | dup)
for name, p in (("mid", pm), ("small", ps)):
    print(name, {s: len(v) for s, v in p.items()}, sum(len(v) for v in p.values()))
    for s, v in p.items(): print("  ", s, " ".join(v))
json.dump({"mid": pm, "small": ps}, open(sys.argv[4], "w"), indent=1)
