"""Gli avvisi automatici: un feed RSS degli acquisti degli insider.

Il sito si aggiorna ogni ora, ma bisogna aprirlo per sapere che cosa è
cambiato. Un feed RSS lo dice da solo: lo si aggiunge a un lettore di feed
(Feedly, Inoreader, NetNewsWire, Thunderbird, Outlook) o a un servizio che
manda una mail o un messaggio per ogni voce nuova, e ogni acquisto nuovo
arriva lì.

Che cosa entra:

- **ogni acquisto sul mercato** (codice P) degli ultimi DAYS giorni, una voce
  per insider e giorno: più righe dello stesso Form 4 si sommano;
- **ogni cluster**: quando almeno tre insider diversi della stessa azienda
  comprano entro trenta giorni, una voce in più.

Le vendite non entrano: sono molte di più e quasi sempre vogliono dire poco
(vedi la Guida). Due file, uno per lingua: site/feed.xml in italiano e
site/feed-en.xml in inglese.

Una voce, una volta uscita, non cambia più. Il feed nuovo rilegge quello del
giro prima e tiene com'erano le voci che c'erano già, con la loro data di
pubblicazione e il punteggio di quel momento; le voci nuove hanno la data del
giro che le ha trovate. Così un lettore di feed non vede la stessa voce due
volte, e la data dice quando l'operazione è diventata nota qui, non quando è
stata fatta (quella è nel testo). Al primo giro, senza un feed precedente, la
data di pubblicazione è quella dell'operazione.
"""
import os
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from xml.etree import ElementTree as ET

DAYS = 60                 # quanto indietro guarda il feed (giorno dell'operazione)
MAX_ITEMS = 100           # le voci più recenti; i lettori tengono da sé le vecchie
FILES = {"it": "feed.xml", "en": "feed-en.xml"}
ATOM = "http://www.w3.org/2005/Atom"
GUID_DAY = re.compile(r"-(\d{4}-\d{2}-\d{2})-")   # il giorno dentro l'identificativo di una voce
ET.register_namespace("atom", ATOM)

MONTHS_IT = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
             "agosto", "settembre", "ottobre", "novembre", "dicembre")
MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December")

CHANNEL = {
    "it": {"title": "Conviction Engine: acquisti degli insider",
           "desc": "Gli acquisti sul mercato degli insider di 200 aziende americane, dai Form 4 "
                   "depositati alla SEC, con il punteggio di convinzione del momento. "
                   "Strumento didattico, non una raccomandazione di investimento."},
    "en": {"title": "Conviction Engine: insider buying",
           "desc": "Open-market insider purchases at 200 US companies, from the Form 4 filings "
                   "at the SEC, with the conviction score at that moment. "
                   "A teaching tool, not investment advice."},
}


def _num(x, lang, dec=0):
    s = f"{x:,.{dec}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".") if lang == "it" else s


def money(v, lang):
    """45.000 $ / 1,2 milioni $ in italiano; $45,000 / $1.2m in inglese."""
    if v >= 1e6:
        return f"{_num(v / 1e6, lang, 1)} milioni $" if lang == "it" else f"${_num(v / 1e6, lang, 1)}m"
    return f"{_num(v, lang)} $" if lang == "it" else f"${_num(v, lang)}"


def day_text(d, lang):
    y, m, dd = int(d[:4]), int(d[5:7]), int(d[8:10])
    return f"{dd} {MONTHS_IT[m - 1]} {y}" if lang == "it" else f"{MONTHS_EN[m - 1]} {dd}, {y}"


def company(name):
    """Il nome come lo scrive la SEC, ripulito per una frase: senza lo stato
    d'origine ("/DE/") e senza il punto finale ("INC.."), che la frase aggiunge già."""
    return re.sub(r"\s*/[A-Z]{2}/?\s*$", "", name or "").rstrip(" .") or name


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _txt(v, lang):
    if isinstance(v, dict):
        return v.get(lang) or v.get("it") or ""
    return str(v or "")


def events(results, today, days=DAYS):
    """Le voci possibili di oggi, senza lingua: [{guid, kind, t, name, date, ...}]."""
    cutoff = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    out = []
    for d in results:
        t, name = d["company"]["ticker"], d["company"]["name"]
        s = d.get("score") or {}
        sc = {"score": s.get("value"), "verdict": s.get("verdict") or ""}
        clu = d.get("cluster") or []
        in_cluster = {x[11:] for x in clu if isinstance(x, str)}
        by = {}
        for x in d.get("tx") or []:
            if x["kind"] != "BUY" or x["date"][:10] < cutoff or x["date"][:10] > today:
                continue
            k = (x["who"], x["date"][:10])
            g = by.setdefault(k, {"shares": 0.0, "value": 0.0, "role": x.get("role") or ""})
            g["shares"] += x.get("shares") or 0
            g["value"] += x.get("value") or 0
        for (who, day), g in by.items():
            out.append(dict(sc, guid=f"{t}-{day}-{slug(who)}-buy", kind="buy", t=t, name=name,
                            date=day, who=who, role=g["role"], shares=g["shares"], value=g["value"],
                            price=g["value"] / g["shares"] if g["shares"] else None,
                            cluster=who in in_cluster))
        if clu and isinstance(clu[0], str):
            first = clu[0][:10]
            people = []
            for x in clu:
                if x[11:] not in people:
                    people.append(x[11:])
            if first >= cutoff:
                out.append(dict(sc, guid=f"{t}-{first}-cluster", kind="cluster", t=t, name=name,
                                date=max(x[:10] for x in clu), first=first, people=people))
    return out


def texts(e, lang, cluster_days=30, with_score=True):
    """(titolo, descrizione) di una voce. `with_score`: se dire il punteggio del momento."""
    it = lang == "it"
    score = "" if not with_score else (f"Punteggio dell'azienda in quel momento: {e['score']}/100, {_txt(e['verdict'], 'it').lower()}."
             if it else
             f"Company score at that moment: {e['score']}/100, {_txt(e['verdict'], 'en').lower()}.") \
        if e.get("score") is not None else ""
    if e["kind"] == "cluster":
        n = len(e["people"])
        who = ", ".join(e["people"])
        if it:
            return (f"{e['t']}: {n} insider comprano entro {cluster_days} giorni",
                    f"{company(e['name'])}. Dal {day_text(e['first'], 'it')} hanno comprato azioni sul mercato: "
                    f"{who}. Più persone che comprano insieme sono il segnale che il punteggio "
                    f"chiama cluster. {score}")
        return (f"{e['t']}: {n} insiders buy within {cluster_days} days",
                f"{company(e['name'])}. Since {day_text(e['first'], 'en')} these people have bought shares "
                f"on the open market: {who}. Several people buying together is the signal the "
                f"score calls a cluster. {score}")
    role = f" ({e['role']})" if e.get("role") else ""
    price = (f" a {_num(e['price'], 'it', 2)} $ in media" if it else f" at ${_num(e['price'], 'en', 2)} on average") \
        if e.get("price") else ""
    if it:
        return (f"{e['t']}: {e['who']}{role} compra per {money(e['value'], 'it')}",
                f"{company(e['name'])}. Acquisto sul mercato del {day_text(e['date'], 'it')}: "
                f"{_num(e['shares'], 'it')} azioni{price}, {money(e['value'], 'it')} in tutto."
                + (" Fa parte di un cluster di acquisti." if e.get("cluster") else "")
                + (f" {score}" if score else ""))
    return (f"{e['t']}: {e['who']}{role} buys {money(e['value'], 'en')}",
            f"{company(e['name'])}. Open-market purchase on {day_text(e['date'], 'en')}: "
            f"{_num(e['shares'], 'en')} shares{price}, {money(e['value'], 'en')} in total."
            + (" Part of a cluster of purchases." if e.get("cluster") else "")
            + (f" {score}" if score else ""))


def link(site_url, t, lang):
    base = site_url.rstrip("/") + "/"
    return base + "?lang=" + lang + ("#" + t if t else "")


def _old_items(path):
    """{guid: elemento <item>} dal feed del giro prima, o None se non c'è."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    out = {}
    for it in root.iter("item"):
        g = it.findtext("guid")
        if g:
            out[g] = it
    return out


def _pub(dt):
    return format_datetime(dt.astimezone(timezone.utc), usegmt=True)


def _item(e, lang, site_url, pub, cluster_days, with_score=True):
    it = ET.Element("item")
    title, desc = texts(e, lang, cluster_days, with_score)
    ET.SubElement(it, "title").text = title
    ET.SubElement(it, "link").text = link(site_url, e["t"], lang)
    ET.SubElement(it, "description").text = desc
    ET.SubElement(it, "category").text = "cluster" if e["kind"] == "cluster" else (
        "acquisto" if lang == "it" else "purchase")
    g = ET.SubElement(it, "guid", isPermaLink="false")
    g.text = e["guid"]
    ET.SubElement(it, "pubDate").text = _pub(pub)
    return it


def _when(it):
    try:
        return parsedate_to_datetime(it.findtext("pubDate"))
    except (TypeError, ValueError):
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def build(results, lang, site_url, today, now, old=None, cluster_days=30):
    """-> (albero XML, quante voci nuove). `old`: {guid: <item>} del giro prima, o None."""
    items = []
    fresh = 0
    for e in events(results, today):
        if old is not None and e["guid"] in old:
            items.append(old[e["guid"]])          # una voce già uscita non cambia
            continue
        if old is None:
            # primo giro: la data dell'operazione, e niente punteggio, perché
            # quello di oggi non è quello del giorno dell'operazione
            pub = datetime.fromisoformat(e["date"]).replace(hour=12, tzinfo=timezone.utc)
            items.append(_item(e, lang, site_url, pub, cluster_days, with_score=False))
            continue
        fresh += 1
        items.append(_item(e, lang, site_url, now, cluster_days))
    # Le voci del giro prima che oggi mancano (un'azienda saltata per un errore,
    # un giro su un solo titolo) restano finché sono dentro la finestra: se
    # sparissero, al ritorno sembrerebbero nuove.
    if old:
        cutoff = (date.fromisoformat(today) - timedelta(days=DAYS)).isoformat()
        have = {it.findtext("guid") for it in items}
        for g, it in old.items():
            m = GUID_DAY.search(g)
            if g not in have and m and m.group(1) >= cutoff:
                items.append(it)
    items.sort(key=lambda it: (_when(it), it.findtext("guid")), reverse=True)
    items = items[:MAX_ITEMS]

    rss = ET.Element("rss", version="2.0")
    ch = ET.SubElement(rss, "channel")
    c = CHANNEL[lang]
    ET.SubElement(ch, "title").text = c["title"]
    ET.SubElement(ch, "link").text = link(site_url, "", lang)
    ET.SubElement(ch, "description").text = c["desc"]
    ET.SubElement(ch, "language").text = lang
    # la data dell'ultima voce, non quella del giro: un giro che non trova
    # niente di nuovo lascia il file identico
    last = _when(items[0]) if items else datetime.fromisoformat(today).replace(tzinfo=timezone.utc)
    ET.SubElement(ch, "lastBuildDate").text = _pub(last)
    ET.SubElement(ch, f"{{{ATOM}}}link", href=site_url.rstrip("/") + "/" + FILES[lang],
                  rel="self", type="application/rss+xml")
    ch.extend(items)
    return ET.ElementTree(rss), fresh


def write(results, outdir, site_url, cluster_days=30, today=None, now=None):
    """Scrive i due feed. -> {lingua: (voci, nuove)}."""
    now = now or datetime.now(timezone.utc)
    today = today or now.date().isoformat()
    out = {}
    for lang, fn in FILES.items():
        path = os.path.join(outdir, fn)
        tree, fresh = build(results, lang, site_url, today, now, _old_items(path), cluster_days)
        ET.indent(tree, space=" ")
        tree.write(path, encoding="utf-8", xml_declaration=True)
        out[lang] = (len(tree.getroot().find("channel").findall("item")), fresh)
    return out
