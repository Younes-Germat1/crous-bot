"""
Crous Paris bot
- Checks trouverunlogement.lescrous.fr for student housing in Paris
- Keeps only offers at or below MAX_PRICE
- Sends a phone notification with ntfy for every NEW offer
- Remembers what it already sent in seen.json
"""

import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------- Settings (change these if you want) ----------
MAX_PRICE = float(os.environ.get("MAX_PRICE", "600"))
# Paris map area (west_north_east_south)
SEARCH_URL = os.environ.get(
    "SEARCH_URL",
    "https://trouverunlogement.lescrous.fr/tools/48/search"
    "?bounds=2.224122_48.902156_2.4697602_48.8155755",
)
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")  # set as a GitHub secret
# ----------------------------------------------------------

BASE = "https://trouverunlogement.lescrous.fr"
SEEN_FILE = Path(__file__).with_name("seen.json")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def parse_price(text):
    """'476,89 €' -> 476.89"""
    m = re.search(r"(\d[\d\s  ]*(?:[.,]\d+)?)", text or "")
    if not m:
        return None
    num = re.sub(r"[\s  ]", "", m.group(1)).replace(",", ".")
    try:
        return float(num)
    except ValueError:
        return None


def parse_page(html):
    """Return (offers, total_announced) from one search results page."""
    soup = BeautifulSoup(html, "html.parser")
    offers = []
    for card in soup.select("div.fr-card"):
        link = card.select_one("h3.fr-card__title a")
        if not link:
            continue
        href = link.get("href", "")
        badge = card.select_one(".fr-badge")
        details = [li.get_text(" ", strip=True) for li in card.select("li.fr-card__detail")]
        desc = card.select_one(".fr-card__desc")
        offers.append(
            {
                "id": href.rstrip("/").split("/")[-1],
                "name": link.get_text(strip=True),
                "address": desc.get_text(" ", strip=True) if desc else "",
                "details": details,
                "price_text": badge.get_text(strip=True) if badge else "",
                "price": parse_price(badge.get_text() if badge else ""),
                "url": BASE + href if href.startswith("/") else href,
            }
        )

    total = 0
    if not soup.find(string=re.compile("Aucun logement trouvé")):
        h2 = soup.find("h2", string=re.compile(r"logements? trouvés?"))
        if h2:
            m = re.search(r"(\d+)", h2.get_text())
            total = int(m.group(1)) if m else 0
    return offers, total


def fetch_all():
    """Fetch every results page for the search."""
    all_offers, total, page = [], 0, 1
    while page <= 20:
        sep = "&" if "?" in SEARCH_URL else "?"
        url = SEARCH_URL + (f"{sep}page={page}" if page > 1 else "")
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        offers, page_total = parse_page(r.text)
        total = max(total, page_total)
        if not offers:
            break
        new_ids = {o["id"] for o in all_offers}
        offers = [o for o in offers if o["id"] not in new_ids]
        if not offers:
            break
        all_offers.extend(offers)
        page += 1
    return all_offers, total


def notify(title, message, click_url=None, priority=5, tags="house"):
    """Send a phone notification through ntfy (JSON = accents and € work)."""
    payload = {
        "topic": NTFY_TOPIC,
        "title": title,
        "message": message,
        "priority": priority,  # 5 = max (alarm), 3 = normal
        "tags": [tags],
    }
    if click_url:
        payload["click"] = click_url
    if not NTFY_TOPIC:
        print("[no NTFY_TOPIC set] would send:", json.dumps(payload, ensure_ascii=False))
        return
    r = requests.post("https://ntfy.sh/", json=payload, timeout=30)
    print("ntfy:", r.status_code)


def main():
    seen = json.loads(SEEN_FILE.read_text()) if SEEN_FILE.exists() else {}

    try:
        offers, total = fetch_all()
    except Exception as e:  # site down or blocked
        print("Error while checking the site:", e)
        sys.exit(0)

    print(f"Site says {total} housing(s); parsed {len(offers)} card(s).")

    # Safety: the site shows results but the bot can't read them -> layout changed
    if total > 0 and not offers:
        if not seen.get("_layout_warning_sent"):
            notify(
                "Crous bot : mise à jour nécessaire",
                "Le site Crous a changé de présentation, le bot n'arrive plus à lire les annonces.",
                SEARCH_URL,
                priority=3,
                tags="warning",
            )
            seen["_layout_warning_sent"] = True
            SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2))
        return
    seen.pop("_layout_warning_sent", None)

    cheap = [o for o in offers if o["price"] is not None and o["price"] <= MAX_PRICE]
    new = [o for o in cheap if f"{o['id']}|{o['price_text']}" not in seen]

    for o in new:
        msg = (
            f"{o['name']}\n{o['address']}\n{o['price_text']} / mois\n"
            + " - ".join(o["details"][:3])
            + "\nTouche cette notification pour ouvrir le logement et réserver vite !"
        )
        notify(f"🏠 Nouveau logement Crous Paris – {o['price_text']}", msg, o["url"])
        seen[f"{o['id']}|{o['price_text']}"] = o["name"]
        print("NEW:", o["name"], o["price_text"], o["url"])

    if not new:
        print(f"Nothing new at or below {MAX_PRICE:.0f} EUR.")

    # Manual test run -> always send a "bot OK" notification
    if os.environ.get("TEST_NOTIFY") == "true":
        notify(
            "✅ Crous bot fonctionne",
            f"Paris : {total} logement(s) affiché(s), {len(cheap)} à {MAX_PRICE:.0f} € ou moins. "
            "Tu recevras une alerte dès qu'un nouveau apparaît.",
            SEARCH_URL,
            priority=3,
            tags="white_check_mark",
        )

    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
