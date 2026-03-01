import requests
import schedule
import time
import json
from datetime import datetime, timezone
from collections import defaultdict

import os
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "TON_TOKEN")
TELEGRAM_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "8703014275"))

CAPITAL_PAR_TRADE = 50
SCAN_INTERVAL = 5
HAUSSE_SEUIL = 0.05
FADE_SEUIL_MIN = 0.85
FADE_SEUIL_MAX = 0.97
VOLUME_MIN = 30000

historique_prix = defaultdict(list)

def envoyer_alerte(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Erreur Telegram: {e}")

def recuperer_marches():
    try:
        url = "https://gamma-api.polymarket.com/markets"
        params = {"closed": "false", "limit": 100, "active": "true"}
        response = None
        for tentative in range(3):
            try:
                response = requests.get(url, params=params, timeout=30)
                break
            except Exception:
                print(f"  Tentative {tentative+1}/3...")
                time.sleep(5)
        if response is None:
            return []

        data = response.json()
        marches = []

        for m in data:
            try:
                outcome_prices = m.get("outcomePrices", None)
                if isinstance(outcome_prices, str):
                    outcome_prices = json.loads(outcome_prices)
                if not outcome_prices or len(outcome_prices) != 2:
                    continue

                prix_yes = float(outcome_prices[0])
                if abs(prix_yes - 0.5) < 0.02:
                    continue

                volume = float(m.get("volumeNum", 0) or m.get("volume", 0) or 0)
                if volume < VOLUME_MIN:
                    continue

                end_date = m.get("endDate", "")
                if end_date:
                    end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    jours = max(0, (end - now).days)
                else:
                    jours = 999

                if jours == 0:
                    continue

                marche = {
                    "id": m.get("id", ""),
                    "titre": m.get("question", "Sans titre"),
                    "prix_yes": prix_yes,
                    "prix_no": float(outcome_prices[1]),
                    "volume": volume,
                    "jours_restants": jours,
                    "slug": m.get("eventSlug") or m.get("slug", "")
                }
                marches.append(marche)

            except Exception:
                continue

        print(f"Marches recuperes : {len(marches)}")
        return marches

    except Exception as e:
        print(f"Erreur API: {e}")
        return []

def detecter_signaux(marches):
    signaux = []
    for m in marches:
        mid = m["id"]
        prix_actuel = m["prix_yes"]
        historique_prix[mid].append({"prix": prix_actuel, "time": datetime.now()})
        if len(historique_prix[mid]) > 10:
            historique_prix[mid].pop(0)
        if len(historique_prix[mid]) < 3:
            continue
        prix_ancien = historique_prix[mid][-3]["prix"]
        variation = prix_actuel - prix_ancien
        if variation >= HAUSSE_SEUIL and prix_actuel < 0.90:
            signaux.append({
                "type": "HAUSSE", "marche": m, "variation": variation,
                "entree": prix_actuel,
                "cible_sortie": min(prix_actuel + 0.04, 0.92),
                "profit_potentiel": round((0.90 - prix_actuel) * CAPITAL_PAR_TRADE / prix_actuel, 2),
                "emoji": "🚀"
            })
        elif FADE_SEUIL_MIN <= prix_actuel <= FADE_SEUIL_MAX and variation >= 0.03:
            signaux.append({
                "type": "FADE", "marche": m, "variation": variation,
                "entree_no": round(1 - prix_actuel, 2),
                "cible_sortie": max(prix_actuel - 0.05, 0.80),
                "profit_potentiel": round((prix_actuel - 0.82) * CAPITAL_PAR_TRADE / (1 - prix_actuel), 2),
                "emoji": "🔄"
            })
        elif variation <= -HAUSSE_SEUIL and prix_actuel > 0.55:
            signaux.append({
                "type": "REBOND", "marche": m, "variation": variation,
                "entree": prix_actuel,
                "cible_sortie": round(prix_actuel + 0.05, 2),
                "profit_potentiel": round((0.75 - prix_actuel) * CAPITAL_PAR_TRADE / prix_actuel, 2) if prix_actuel < 0.75 else 0,
                "emoji": "📉➡️📈"
            })
    return signaux

def envoyer_signal(signal):
    m = signal["marche"]
    stype = signal["type"]
    emoji = signal["emoji"]
    message = f"{emoji} <b>SIGNAL {stype}</b>\n"
    message += f"<b>{m['titre'][:55]}</b>\n\n"
    if stype == "HAUSSE":
        message += f"Prix YES : {m['prix_yes']*100:.1f}%\n"
        message += f"Variation : +{signal['variation']*100:.1f}%\n"
        message += f"Cible sortie : {signal['cible_sortie']*100:.1f}%\n"
        message += f"Action : <b>ACHETER YES</b>\n"
        message += f"Mise : ${CAPITAL_PAR_TRADE}\n"
        message += f"Profit potentiel : +${signal['profit_potentiel']}\n"
    elif stype == "FADE":
        message += f"Prix YES : {m['prix_yes']*100:.1f}%\n"
        message += f"Variation : +{signal['variation']*100:.1f}%\n"
        message += f"Cible retour : {signal['cible_sortie']*100:.1f}%\n"
        message += f"Action : <b>ACHETER NO a {signal['entree_no']*100:.1f}%</b>\n"
        message += f"Mise : ${CAPITAL_PAR_TRADE}\n"
        message += f"Profit potentiel : +${signal['profit_potentiel']}\n"
    elif stype == "REBOND":
        message += f"Prix YES : {m['prix_yes']*100:.1f}%\n"
        message += f"Baisse : {signal['variation']*100:.1f}%\n"
        message += f"Cible rebond : {signal['cible_sortie']*100:.1f}%\n"
        message += f"Action : <b>ACHETER YES</b>\n"
        message += f"Mise : ${CAPITAL_PAR_TRADE}\n"
    message += f"\nResolution : {m['jours_restants']} jours\n"
    message += f"Volume : ${m['volume']:,.0f}\n"
    message += f"polymarket.com/event/{m['slug']}\n"
    message += f"Sortir si prix baisse de 3% sous entree"
    envoyer_alerte(message)
    print(f"  Signal : {stype} sur {m['titre'][:40]}")

def scanner():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scan en cours...")
    marches = recuperer_marches()
    if not marches:
        print("Aucun marche recupere.")
        return
    print(f"Marches analyses : {len(marches)}")
    signaux = detecter_signaux(marches)
    print(f"Signaux detectes : {len(signaux)}")
    for signal in signaux:
        envoyer_signal(signal)
    if not signaux:
        print("Aucun signal - surveillance en cours...")

print("=" * 45)
print("  BOT DAY TRADING POLYMARKET")
print(f"  Capital par trade : ${CAPITAL_PAR_TRADE}")
print(f"  Scan toutes les {SCAN_INTERVAL} minutes")
print("=" * 45)
print("Signaux apres 3 scans (15 min)\n")

scanner()
schedule.every(SCAN_INTERVAL).minutes.do(scanner)

while True:
    schedule.run_pending()
    time.sleep(30)